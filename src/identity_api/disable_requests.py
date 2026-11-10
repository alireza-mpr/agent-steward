"""The Identity API's store of requests to disable an agent (FR7, FR4).

The agent proposes, and the approver decides. A request starts pending, and leaves pending only
once: approved, denied or expired. An approved request is executed at most once, and only soon
after the approval: the executor claims it here before it writes, with the approver's rights
(OBO). Plain Python with no FastAPI, and the clock is passed in, so the rules have offline tests (NFR8).

In memory only. If the API restarts, every request is gone: a pending one can no longer be
approved, an approved one can no longer be executed, and nothing is disabled (fail closed). The
audit lines are the lasting record.
"""

import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

# The guardian does not wait for the decision: it asks, and a later run sees the request's status.
# So a pending request waits for an approver for a day, about one run of the guardian.
EXPIRES_AFTER = timedelta(hours=24)
# The approve CLI hands the approver's token to the executor right after the decision. An approved
# request that is not executed within this time can no longer be.
EXECUTE_WITHIN = timedelta(minutes=10)


class AlreadyPending(Exception):
    """This agent already has a pending request."""


class NotFound(Exception):
    """No such request for this agent."""


class AlreadyFinal(Exception):
    """The request was already decided, or it expired."""


class NotExecutable(Exception):
    """The request cannot be executed: not approved, already executed, too late, or the delegating
    user is not the person who approved it. reason says which."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class DisableRequest:
    id: str
    agent_id: str
    requested_by: str       # the agent that asked: only it can read the request
    reason: str             # from the model: untrusted
    facts: dict             # read from Graph by the MCP server
    correlation_id: str     # links the audit lines of this request
    expires_at: datetime
    created_at: datetime | None = None
    status: str = "pending"             # pending, approved, denied, expired or executed
    decided_by_id: str | None = None    # the approver's oid, from their token
    decided_by_name: str | None = None
    decided_at: datetime | None = None
    executed_at: datetime | None = None


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
            now = self._now()
            request = DisableRequest(str(uuid.uuid4()), agent_id, requested_by, reason, facts, correlation_id,
                                     now + EXPIRES_AFTER, created_at=now)
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

    def of_agent(self, agent_id: str, requested_by: str) -> list[DisableRequest]:
        """The requests one requester made for one agent, the newest first."""
        with self._lock:
            found = [request for request in self._current()
                     if request.agent_id == agent_id and request.requested_by == requested_by]
            return sorted(found, key=lambda request: request.created_at, reverse=True)

    def execute(self, agent_id: str, request_id: str, delegating_user: str) -> DisableRequest:
        """Claim an approved request for its one write. Only the approver may lend their rights for
        it, and only within EXECUTE_WITHIN of the approval. Raises NotFound or NotExecutable."""
        with self._lock:
            request = self._find(agent_id, request_id)
            if request.status == "executed":
                raise NotExecutable("already executed")
            if request.status != "approved":
                raise NotExecutable("not approved")
            if request.decided_by_id != delegating_user:
                raise NotExecutable("not the approver")
            if self._now() > request.decided_at + EXECUTE_WITHIN:
                raise NotExecutable("too late")
            executed = replace(request, status="executed", executed_at=self._now())
            self._requests[request_id] = executed
            return executed

    def decide(self, agent_id: str, request_id: str, status: str, decided_by_id: str, decided_by_name: str) -> DisableRequest:
        """Record a decision, once. status is approved or denied."""
        with self._lock:
            request = self._find(agent_id, request_id)
            if request.status != "pending":
                raise AlreadyFinal
            decided = replace(request, status=status, decided_by_id=decided_by_id, decided_by_name=decided_by_name,
                              decided_at=self._now())
            self._requests[request_id] = decided
            return decided
