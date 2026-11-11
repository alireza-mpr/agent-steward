"""The approval gate's client side (FR7, FR4): the agent proposes, and the approver decides.

The submit_disable_agent_request tool (server.py) creates a disable request at the Identity API and
returns at once: it does not wait. A later scan reads the request's status. The write happens only
when the approver hands over their own token to the executor (src/executor), which claims the
request here right before it writes with the approver's rights (OBO). Both use this client, as the
guardian. Plain Python with no MCP SDK, so it has offline tests (NFR8).

The Identity API's side of the contract:
    POST /agents/{agentId}/disableRequests                          -> 201, the new request (pending)
                                                                       409 if one is already pending
    GET  /agents/{agentId}/disableRequests                          -> {"value": this server's requests
                                                                       for the agent, newest first}
    GET  /agents/{agentId}/disableRequests/{requestId}              -> the request and its status
    POST /agents/{agentId}/disableRequests/{requestId}/execution    -> the request, now running
                                                                       409 if it cannot be executed
    POST /agents/{agentId}/disableRequests/{requestId}/outcome      -> the request, now succeeded or failed
                                                                       409 if it is not running
The approver's list and decision go to the Identity API from the approve CLI (src/approve_cli),
never through here. The guardian's role, AgentDisable.Request, opens only the five calls above: the
MCP server creates and lists requests, and the executor reads one, claims it (execution) and
records what the write did (outcome).
"""

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class Status(StrEnum):
    """A request's status, as the Identity API sends it (src/identity_api/disable_requests.py).

    pending -> approved, denied or expired (the approver decides, or nobody does within a day)
    approved -> running (the executor claimed it, right before its write)
    running -> succeeded or failed (the executor recorded what the write did)
    The last three are Microsoft Graph's longRunningOperationStatus values."""
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    EXPIRED = "expired"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


# A claimed request: the executor has used it, so it can never be executed again.
CLAIMED = frozenset({Status.RUNNING, Status.SUCCEEDED, Status.FAILED})


@dataclass(frozen=True)
class DisableRequest:
    request_id: str
    agent_id: str                       # the app ID of the agent to disable
    status: Status
    decided_by_id: str | None = None    # the approver's object ID (oid), once someone decided
    decided_by_name: str | None = None
    created_at: str | None = None       # ISO 8601, as the Identity API sends them
    decided_at: str | None = None
    correlation_id: str | None = None   # links the request's audit lines at the Identity API and here
    expires_at: str | None = None       # when a pending request stops waiting for an approver


class ApprovalError(Exception):
    """The request could not be made or used. reason is one of: already_waiting (create),
    not_executable (execute), not_running (record_outcome)."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class ApprovalService(Protocol):
    def create(self, agent_id: str, reason: str, facts: dict, correlation_id: str) -> DisableRequest: ...
    def get(self, agent_id: str, request_id: str) -> DisableRequest: ...
    def of_agent(self, agent_id: str) -> list[DisableRequest]: ...
    def execute(self, agent_id: str, request_id: str, delegating_user: str) -> DisableRequest: ...
    def record_outcome(self, agent_id: str, request_id: str, outcome: Status) -> DisableRequest: ...


def parse_request(body: dict) -> DisableRequest:
    """The Identity API's JSON for one request. Anything unexpected raises ValueError."""
    if not isinstance(body, dict) or body.get("status") not in set(Status):
        raise ValueError("not a disable request")
    request_id, agent_id = body.get("id"), body.get("agentId")
    if not isinstance(request_id, str) or not isinstance(agent_id, str):
        raise ValueError("a disable request needs id and agentId")
    decided_by = body.get("decidedBy") or {}
    if not isinstance(decided_by, dict):
        raise ValueError("decidedBy must be an object")
    return DisableRequest(request_id, agent_id, Status(body["status"]), decided_by.get("id"), decided_by.get("name"),
                          body.get("createdAt"), body.get("decidedAt"), body.get("correlationId"),
                          body.get("expiresAt"))


class IdentityApiApprovals:
    """The approval service at the Identity API. The MCP server and the executor call it as the
    guardian, whose token carries the AgentDisable.Request role."""

    def __init__(self, api_url: str, get_token: Callable[[], str]):
        self._api_url = api_url
        self._get_token = get_token

    def _send(self, method: str, path: str, correlation_id: str = "", body: dict | None = None):
        headers = {"Authorization": f"Bearer {self._get_token()}", "Content-Type": "application/json"}
        if correlation_id:
            headers["X-Correlation-ID"] = correlation_id
        request = urllib.request.Request(self._api_url + path, method=method, headers=headers,
                                         data=json.dumps(body).encode() if body is not None else None)
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)

    def _call(self, method: str, path: str, correlation_id: str = "", body: dict | None = None) -> DisableRequest:
        return parse_request(self._send(method, path, correlation_id, body))

    def create(self, agent_id: str, reason: str, facts: dict, correlation_id: str) -> DisableRequest:
        try:
            return self._call("POST", f"/agents/{urllib.parse.quote(agent_id)}/disableRequests", correlation_id,
                              {"reason": reason, "facts": facts})
        except urllib.error.HTTPError as error:
            if error.code == 409:
                raise ApprovalError("already_waiting") from None
            raise

    def get(self, agent_id: str, request_id: str) -> DisableRequest:
        return self._call("GET", f"/agents/{urllib.parse.quote(agent_id)}/disableRequests/"
                                 f"{urllib.parse.quote(request_id)}")

    def of_agent(self, agent_id: str) -> list[DisableRequest]:
        body = self._send("GET", f"/agents/{urllib.parse.quote(agent_id)}/disableRequests")
        if not isinstance(body, dict) or not isinstance(body.get("value"), list):
            raise ValueError("not a list of disable requests")
        return [parse_request(item) for item in body["value"]]

    def execute(self, agent_id: str, request_id: str, delegating_user: str) -> DisableRequest:
        try:
            return self._call("POST", f"/agents/{urllib.parse.quote(agent_id)}/disableRequests/"
                                      f"{urllib.parse.quote(request_id)}/execution",
                              body={"delegatingUser": delegating_user})
        except urllib.error.HTTPError as error:
            if error.code == 409:
                raise ApprovalError("not_executable") from None
            raise

    def record_outcome(self, agent_id: str, request_id: str, outcome: Status) -> DisableRequest:
        try:
            return self._call("POST", f"/agents/{urllib.parse.quote(agent_id)}/disableRequests/"
                                      f"{urllib.parse.quote(request_id)}/outcome",
                              body={"outcome": str(outcome)})
        except urllib.error.HTTPError as error:
            if error.code == 409:
                raise ApprovalError("not_running") from None
            raise


# What a failed call to the approval service raises: urllib's errors and timeouts are OSErrors,
# and an answer that is not a disable request is a ValueError.
SERVICE_ERRORS = (OSError, ValueError)
