"""The Identity API's store of requests to disable an agent (FR7).

A request starts pending, and leaves pending only once: approved, denied or expired. Plain Python
with no FastAPI, and the clock is passed in, so the rules have offline tests (NFR8).

In memory only. If the API restarts, the pending requests are gone, the MCP server's next poll
gets a 404, and nothing is disabled (fail closed). The audit lines are the lasting record.
"""

import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

EXPIRES_AFTER = timedelta(minutes=4)   # starts before the MCP server's own 4 minutes, so its last poll sees a final status


class AlreadyPending(Exception):
    """This agent already has a pending request."""


class NotFound(Exception):
    """No such request for this agent."""


class AlreadyFinal(Exception):
    """The request was already decided, or it expired."""


@dataclass(frozen=True)
class DisableRequest:
    id: str
    agent_id: str
    requested_by: str       # the agent that asked: only it can read the request
    reason: str             # from the model: untrusted
    facts: dict             # read from Graph by the MCP server
    correlation_id: str     # links the audit lines of this request
    expires_at: datetime
    status: str = "pending"             # pending, approved, denied or expired
    decided_by_id: str | None = None    # the approver's oid, from their token
    decided_by_name: str | None = None


class DisableRequestStore:
    def __init__(self, now: Callable[[], datetime], on_expired: Callable[[DisableRequest], None] = lambda request: None):
        self._now = now
        self._on_expired = on_expired
        self._requests: dict[str, DisableRequest] = {}
        # FastAPI runs routes in a thread pool, so two decisions can arrive at once. Under the
        # lock, only one of them can see the request as pending.
        self._lock = threading.Lock()

    def _expire_if_due(self, request: DisableRequest) -> DisableRequest:
        """Expiry is checked on every read and decision, so no background timer is needed."""
        if request.status != "pending" or self._now() < request.expires_at:
            return request
        expired = replace(request, status="expired")
        self._requests[request.id] = expired
        self._on_expired(expired)
        return expired

    def _current(self) -> list[DisableRequest]:
        return [self._expire_if_due(request) for request in list(self._requests.values())]

    def create(self, agent_id: str, requested_by: str, reason: str, facts: dict, correlation_id: str) -> DisableRequest:
        with self._lock:
            if any(request.agent_id == agent_id and request.status == "pending" for request in self._current()):
                raise AlreadyPending
            request = DisableRequest(str(uuid.uuid4()), agent_id, requested_by, reason, facts, correlation_id,
                                     self._now() + EXPIRES_AFTER)
            self._requests[request.id] = request
            return request

    def _find(self, agent_id: str, request_id: str) -> DisableRequest:
        """The request, only through its own agent's path: an approval stays bound to its agent."""
        request = self._requests.get(request_id)
        if request is None or request.agent_id != agent_id:
            raise NotFound
        return self._expire_if_due(request)

    def get(self, agent_id: str, request_id: str) -> DisableRequest:
        with self._lock:
            return self._find(agent_id, request_id)

    def pending(self) -> list[DisableRequest]:
        with self._lock:
            return [request for request in self._current() if request.status == "pending"]

    def decide(self, agent_id: str, request_id: str, status: str, decided_by_id: str, decided_by_name: str) -> DisableRequest:
        """Record a decision, once. status is approved or denied."""
        with self._lock:
            request = self._find(agent_id, request_id)
            if request.status != "pending":
                raise AlreadyFinal
            decided = replace(request, status=status, decided_by_id=decided_by_id, decided_by_name=decided_by_name)
            self._requests[request_id] = decided
            return decided
