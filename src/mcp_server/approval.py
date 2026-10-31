"""The approval gate's client side (FR7): ask a human to approve disabling an agent, and wait.

The disable_agent tool (server.py) creates a disable request at the Identity API, then polls it
until an approver decides or the time runs out. The waiting and the checks on the answer are
plain Python with no MCP SDK, and the clock and the sleep are passed in, so they have offline
tests (NFR8).

Every path that is not a clear approval ends in ApprovalError, so nothing is disabled: a denial,
no answer in time, an answer that does not match the request, an approval used before, or an
approval service that cannot be reached (fail closed).

The Identity API's side of the contract:
    POST /agents/{agentId}/disableRequests                 -> 201, the new request (pending)
    GET  /agents/{agentId}/disableRequests/{requestId}     -> the request and its status
The approver's list and decision go to the Identity API from the approve command (src/approve_cli),
never through here. This server's role, AgentDisable.Request, opens only the two calls above.
"""

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

WAIT_FOR_APPROVAL = timedelta(minutes=4)
POLL_EVERY = timedelta(seconds=5)
STATUSES = frozenset({"pending", "approved", "denied", "expired"})


@dataclass(frozen=True)
class DisableRequest:
    request_id: str
    agent_id: str                       # the app ID of the agent to disable
    status: str                         # pending, approved, denied or expired
    decided_by_id: str | None = None    # the approver's object ID (oid), once someone decided
    decided_by_name: str | None = None


class ApprovalError(Exception):
    """The request was not approved, so nothing may be disabled. reason is one of: already_waiting,
    denied, expired, mismatch, reused, unavailable."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class ApprovalService(Protocol):
    def create(self, agent_id: str, reason: str, facts: dict, correlation_id: str) -> DisableRequest: ...
    def get(self, agent_id: str, request_id: str) -> DisableRequest: ...


def parse_request(body: dict) -> DisableRequest:
    """The Identity API's JSON for one request. Anything unexpected raises ValueError."""
    if not isinstance(body, dict) or body.get("status") not in STATUSES:
        raise ValueError("not a disable request")
    request_id, agent_id = body.get("id"), body.get("agentId")
    if not isinstance(request_id, str) or not isinstance(agent_id, str):
        raise ValueError("a disable request needs id and agentId")
    decided_by = body.get("decidedBy") or {}
    if not isinstance(decided_by, dict):
        raise ValueError("decidedBy must be an object")
    return DisableRequest(request_id, agent_id, body["status"], decided_by.get("id"), decided_by.get("name"))


class IdentityApiApprovals:
    """The approval service at the Identity API. The MCP server calls it as the guardian, whose
    token carries the AgentDisable.Request role."""

    def __init__(self, api_url: str, get_token: Callable[[], str]):
        self._api_url = api_url
        self._get_token = get_token

    def _call(self, method: str, path: str, correlation_id: str = "", body: dict | None = None) -> DisableRequest:
        headers = {"Authorization": f"Bearer {self._get_token()}", "Content-Type": "application/json"}
        if correlation_id:
            headers["X-Correlation-ID"] = correlation_id
        request = urllib.request.Request(self._api_url + path, method=method, headers=headers,
                                         data=json.dumps(body).encode() if body is not None else None)
        with urllib.request.urlopen(request, timeout=30) as response:
            return parse_request(json.load(response))

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


# What a failed call to the approval service raises: urllib's errors and timeouts are OSErrors,
# and an answer that is not a disable request is a ValueError.
SERVICE_ERRORS = (OSError, ValueError)


async def wait_for_decision(poll: Callable[[], Awaitable[DisableRequest]], created: DisableRequest,
                            used_request_ids: set[str], now: Callable[[], datetime],
                            sleep: Callable[[float], Awaitable[None]]) -> DisableRequest:
    """Poll the request until an approver decides, for at most WAIT_FOR_APPROVAL. Returns the
    approved request, or raises ApprovalError. A failed poll stops the wait: nothing is retried
    here, and the next run can ask again.
    """
    deadline = now() + WAIT_FOR_APPROVAL
    while True:
        try:
            answer = await poll()
        except SERVICE_ERRORS:
            raise ApprovalError("unavailable") from None
        # The answer must be about this request, for this agent: the approval is bound to them.
        if answer.request_id != created.request_id or answer.agent_id != created.agent_id:
            raise ApprovalError("mismatch")
        if answer.status == "approved":
            if answer.request_id in used_request_ids:
                raise ApprovalError("reused")
            used_request_ids.add(answer.request_id)
            return answer
        if answer.status in ("denied", "expired"):
            raise ApprovalError(answer.status)
        if now() >= deadline:
            raise ApprovalError("expired")
        await sleep(POLL_EVERY.total_seconds())
