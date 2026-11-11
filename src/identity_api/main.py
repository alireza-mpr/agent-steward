"""Identity API (FR3, FR5, FR7, FR8).

Receives the guardian's findings and accepts them only from an agent identity that holds
the app role Findings.Submit. It also serves the approval of a request to disable an agent:
the guardian's MCP server creates and reads requests, a person decides with the approve
CLI, and the guardian's executor claims an approved request before it writes. Every decision
becomes one JSON audit line on stdout.

Run: uvicorn main:app --host 0.0.0.0 --port 8000
"""

import json
import os
import sys
import time
from datetime import datetime, timezone
from collections.abc import Callable
from typing import Annotated, Literal, TypeVar

import jwt
from fastapi import Depends, FastAPI, HTTPException, Path, Request
from pydantic import BaseModel, ConfigDict, Field

from disable_requests import (AlreadyFinal, AlreadyPending, DisableRequest, DisableRequestStore, NotExecutable,
                              NotFound, NotRunning, Status)
from telemetry import setup_tracing, trace_fields
from token_validation import ApiSettings, Approver, Caller, TokenRejected, verify_agent, verify_approver

SETTINGS = ApiSettings(
    tenant_id=os.environ["TENANT_ID"],
    audience=os.environ["IDENTITY_API_APP_ID"],
    approver_client_id=os.environ["AGENT_DISABLE_CLI_APP_ID"],
)

# Entra's public signing keys for this tenant. The client caches them, and fetches them
# again when a token names a key it does not know.
SIGNING_KEYS = jwt.PyJWKClient(
    f"https://login.microsoftonline.com/{SETTINGS.tenant_id}/discovery/v2.0/keys"
)

app = FastAPI(title="agent-steward Identity API")
if setup_tracing():   # only when OTEL_SERVICE_NAME is set (src/shared/telemetry.py)
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    # A server span per request, as a child of the caller's span: it reads the traceparent header.
    FastAPIInstrumentor.instrument_app(app, excluded_urls="health", exclude_spans=["receive", "send"])


def audit(**fields) -> None:
    """Write one audit line (FR8), with the current trace ID and span ID (NFR4). Never pass a token in here."""
    record = {"timestamp": datetime.now(timezone.utc).isoformat(), **trace_fields(), **fields}
    print(json.dumps(record), file=sys.stdout, flush=True)


T = TypeVar("T")


def authenticate(request: Request, verify: Callable[[str, object], T]) -> T:
    """Run the token checks before the endpoint, and before the request body is validated.

    FastAPI reads the body first, so broken JSON gets a 422 before this runs. A body that
    is valid JSON but does not match the schema gets its 422 only after the token passes.
    """
    scheme, _, token = (request.headers.get("Authorization") or "").partition(" ")
    try:
        if scheme.lower() != "bearer" or not token:
            raise TokenRejected(401, "no bearer token")
        try:
            signing_key = SIGNING_KEYS.get_signing_key_from_jwt(token).key
        except jwt.PyJWTError as error:
            raise TokenRejected(401, f"signature: {error}") from error
        return verify(token, signing_key)
    except TokenRejected as rejection:
        audit(
            event="request_rejected",
            path=request.url.path,
            client_app_id=rejection.client,
            outcome="rejected",
            status=rejection.status,
            reason=rejection.reason,
        )
        headers = {"WWW-Authenticate": 'Bearer error="invalid_token"'} if rejection.status == 401 else None
        raise HTTPException(rejection.status, detail=rejection.reason, headers=headers) from None


def agent_with_role(role: str) -> Callable[[Request], Caller]:
    """A route's caller: an agent identity that holds this app role."""
    def dependency(request: Request) -> Caller:
        return authenticate(request, lambda token, key: verify_agent(token, key, SETTINGS, role, time.time()))
    return dependency


def approver(request: Request) -> Approver:
    """A route's caller: a person who may approve, using the CLI."""
    return authenticate(request, lambda token, key: verify_approver(token, key, SETTINGS, time.time()))


class Finding(BaseModel):
    agent_app_id: str
    agent_name: str
    check: Literal["orphan", "expiring_credential", "over_permissioned", "dormant"]
    detail: str
    date: str | None = None   # YYYY-MM-DD: when a credential ends, or when the agent last got a token


class FindingsReport(BaseModel):
    findings: list[Finding]


@app.post("/findings", status_code=202)
def submit_findings(report: FindingsReport, request: Request,
                    caller: Caller = Depends(agent_with_role("Findings.Submit"))) -> dict:
    audit(
        event="findings_submitted",
        path=request.url.path,
        tool="submit_findings",
        agent_app_id=caller.agent_app_id,
        agent_object_id=caller.agent_object_id,
        blueprint_app_id=caller.blueprint_app_id,
        mode=caller.mode,
        delegating_user=None,   # autonomous mode: no user (a human) in the token
        approver=None,          # read-only findings need no approval (FR7 covers fixes)
        outcome="accepted",
        findings=len(report.findings),
    )
    for finding in report.findings:
        audit(event="finding", **finding.model_dump())
    return {"accepted": len(report.findings)}


# The approval of a request to disable an agent (FR7, FR4): the agent proposes, and the approver
# decides. The agent creates, reads and executes its own requests, and records the outcome; a person
# lists and decides.

GUID_PATTERN = r"^[0-9a-fA-F]{8}-([0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$"
AgentId = Annotated[str, Path(pattern=GUID_PATTERN)]
can_request = agent_with_role("AgentDisable.Request")


def audit_expired(request: DisableRequest) -> None:
    audit(event="disable_request_expired", request_id=request.id,
          target_agent=request.agent_id)


STORE = DisableRequestStore(lambda: datetime.now(timezone.utc), audit_expired)


class DisableFacts(BaseModel):
    """What the MCP server read from Graph. The API does not read Graph itself (THREATMODEL.md)."""

    model_config = ConfigDict(extra="forbid")
    name: str = Field(max_length=256)
    blueprintAppId: str = Field(max_length=64)
    flaw: str = Field(max_length=500)
    lastSignIn: str | None = Field(default=None, max_length=32)


class NewDisableRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(max_length=500)   # from the model: untrusted
    facts: DisableFacts


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["approve", "deny"]   # the approver's identity comes only from the token


class Outcome(BaseModel):
    model_config = ConfigDict(extra="forbid")
    outcome: Literal[Status.SUCCEEDED, Status.FAILED]   # what the executor's write did


class Execution(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # The oid of the person whose rights the write uses (OBO). The executor took it from that
    # person's token, which it validated. It must be the approver's.
    delegatingUser: str = Field(pattern=r"^[0-9a-fA-F]{8}-([0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$")


def iso(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment else None


def as_json(request: DisableRequest) -> dict:
    decided_by = {"id": request.decided_by_id, "name": request.decided_by_name} if request.decided_by_id else None
    return {"id": request.id, "agentId": request.agent_id, "status": request.status, "reason": request.reason,
            "facts": request.facts, "traceId": request.trace_id, "spanId": request.span_id,
            "createdAt": iso(request.created_at), "expiresAt": iso(request.expires_at),
            "decidedBy": decided_by, "decidedAt": iso(request.decided_at), "executedAt": iso(request.executed_at),
            "finishedAt": iso(request.finished_at)}


@app.post("/agents/{agent_id}/disableRequests", status_code=201)
def create_disable_request(agent_id: AgentId, body: NewDisableRequest, request: Request,
                           caller: Caller = Depends(can_request)) -> dict:
    # The span that creates the request, so the approval's trace can link to it later.
    origin = trace_fields()
    try:
        created = STORE.create(agent_id, caller.agent_app_id, body.reason, body.facts.model_dump(),
                               origin.get("trace_id"), origin.get("span_id"))
    except AlreadyPending:
        audit(event="disable_request_refused", target_agent=agent_id,
              agent_app_id=caller.agent_app_id, reason="already pending")
        raise HTTPException(409, detail="a request for this agent is already pending") from None
    audit(event="disable_request_created", request_id=created.id, target_agent=agent_id,
          agent_app_id=caller.agent_app_id, agent_object_id=caller.agent_object_id,
          blueprint_app_id=caller.blueprint_app_id, expires_at=created.expires_at.isoformat(),
          untrusted_reason=created.reason, facts=created.facts)
    return as_json(created)


@app.get("/agents/{agent_id}/disableRequests/{request_id}")
def read_disable_request(agent_id: AgentId, request_id: str, caller: Caller = Depends(can_request)) -> dict:
    try:
        found = STORE.get(agent_id, request_id)
    except NotFound:
        raise HTTPException(404, detail="no such request") from None
    # Only the agent that asked. Another agent gets the same answer as for an unknown request.
    if found.requested_by != caller.agent_app_id:
        raise HTTPException(404, detail="no such request")
    return as_json(found)


@app.get("/agents/{agent_id}/disableRequests")
def list_agent_disable_requests(agent_id: AgentId, caller: Caller = Depends(can_request)) -> dict:
    """The caller's own requests for one agent, the newest first: the guardian's scan shows their status."""
    return {"value": [as_json(found) for found in STORE.of_agent(agent_id, caller.agent_app_id)]}


@app.post("/agents/{agent_id}/disableRequests/{request_id}/execution")
def execute_disable_request(agent_id: AgentId, request_id: str, body: Execution, request: Request,
                            caller: Caller = Depends(can_request)) -> dict:
    """The executor claims an approved request right before its one write, with the approver's
    rights. Only the agent that asked, once, soon after the approval, and only for the approver."""
    try:
        found = STORE.get(agent_id, request_id)
    except NotFound:
        raise HTTPException(404, detail="no such request") from None
    if found.requested_by != caller.agent_app_id:
        raise HTTPException(404, detail="no such request")
    try:
        executed = STORE.execute(agent_id, request_id, body.delegatingUser)
    except NotExecutable as refusal:
        audit(event="disable_execution_refused", request_id=request_id,
              target_agent=agent_id, agent_app_id=caller.agent_app_id, delegating_user=body.delegatingUser,
              reason=refusal.reason)
        raise HTTPException(409, detail=f"this request cannot be executed: {refusal.reason}") from None
    audit(event="disable_request_executed", request_id=request_id,
          target_agent=agent_id, agent_app_id=caller.agent_app_id, mode="obo", delegating_user=body.delegatingUser,
          approver_id=executed.decided_by_id, approver_name=executed.decided_by_name)
    return as_json(executed)


@app.post("/agents/{agent_id}/disableRequests/{request_id}/outcome")
def record_disable_outcome(agent_id: AgentId, request_id: str, body: Outcome, caller: Caller = Depends(can_request)) -> dict:
    """The executor records what its write did, once, for a running request: succeeded or failed.
    Only the agent that asked. A failed request lets the agent ask again, with a new request."""
    try:
        found = STORE.get(agent_id, request_id)
    except NotFound:
        raise HTTPException(404, detail="no such request") from None
    if found.requested_by != caller.agent_app_id:
        raise HTTPException(404, detail="no such request")
    try:
        finished = STORE.record_outcome(agent_id, request_id, body.outcome)
    except NotRunning:
        audit(event="disable_outcome_refused", request_id=request_id,
              target_agent=agent_id, agent_app_id=caller.agent_app_id, status=found.status)
        raise HTTPException(409, detail="this request is not running") from None
    audit(event="disable_request_finished", request_id=request_id,
          target_agent=agent_id, agent_app_id=caller.agent_app_id, outcome=finished.status,
          approver_id=finished.decided_by_id, approver_name=finished.decided_by_name)
    return as_json(finished)


@app.get("/disableRequests")
def list_disable_requests(status: str, person: Approver = Depends(approver)) -> dict:
    if status != Status.PENDING:
        raise HTTPException(400, detail="status must be pending")
    return {"value": [as_json(request) for request in STORE.pending()]}


@app.post("/agents/{agent_id}/disableRequests/{request_id}/decision")
def decide_disable_request(agent_id: AgentId, request_id: str, body: Decision, person: Approver = Depends(approver)) -> dict:
    status = {"approve": Status.APPROVED, "deny": Status.DENIED}[body.decision]
    try:
        current = STORE.get(agent_id, request_id)
        decided = STORE.decide(agent_id, request_id, status, person.object_id, person.name)
    except NotFound:
        raise HTTPException(404, detail="no such request") from None
    except AlreadyFinal:
        audit(event="disable_decision_refused", request_id=request_id,
              target_agent=agent_id, decision=status, approver_id=person.object_id, approver_name=person.name,
              reason="already decided or expired")
        raise HTTPException(409, detail="this request was already decided, or it expired") from None
    audit(event="disable_request_decided", request_id=request_id,
          target_agent=agent_id, decision=status, approver_id=person.object_id, approver_name=person.name,
          client_app_id=person.client_app_id)
    return as_json(decided)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
