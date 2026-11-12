"""The guardian's executor: the only service that disables an agent (FR4, FR7).

The agent proposes, and the approver decides and lends their own rights. The MCP server only
creates the request. After an approval, the approve CLI sends the approver's token (Tc) here. The
executor checks Tc, binds it to the approval, checks the agent again, claims the request at the
Identity API, exchanges Tc with OBO, writes, and records the outcome at the Identity API (succeeded
or failed), all within that one HTTP call.

It runs as the guardian's agent identity, through its own token sidecar (docker-compose.yml).
Only that sidecar has the OBO settings, and only this service ever receives Tc. So the MCP
server, which runs the model's tool calls, has no way to write, and a bug in it cannot reach this
code. The harness cannot reach the executor either: the executor is on the approval network only.

Run: uvicorn executor:app --host 0.0.0.0 --port 8200
"""

import asyncio
import json
import os
import urllib.error
import urllib.parse
import urllib.request

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from approval import CLAIMED, SERVICE_ERRORS, ApprovalError, DisableRequest, IdentityApiApprovals, Status
from disable_checks import GUID, check_can_be_disabled
from graph_reader import GraphClient
from graph_writer import GraphWriter, disable_agent_identity
from telemetry import audit, setup_tracing

SIDECAR_URL = os.environ.get("SIDECAR_URL", "http://127.0.0.1:5000")
API_URL = os.environ.get("API_URL", "http://identity-api:8000")

# One route and nothing else: no interactive docs and no OpenAPI page.
app = FastAPI(title="agent-steward executor", docs_url=None, redoc_url=None, openapi_url=None)
if setup_tracing():   # only when OTEL_SERVICE_NAME is set (src/shared/telemetry.py)
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.urllib import URLLibInstrumentor
    # A server span for the CLI's call, as a child of the CLI's span, and a client span for each call
    # to the sidecar, Graph and the Identity API. Neither records a header, so Tc never goes into a span.
    FastAPIInstrumentor.instrument_app(app, exclude_spans=["receive", "send"])
    URLLibInstrumentor().instrument()




def audit_request(event: str, request: DisableRequest, **fields) -> None:
    audit(event=event, agent=request.agent_id, request_id=request.request_id, **fields)


def guardian_app_id() -> str:
    return os.environ["GUARDIAN_AGENT_APP_ID"]


def token_for(api: str):
    """A function that asks this executor's sidecar for an app-only token for one downstream API,
    as the guardian: Graph to read the agent again, and the Identity API to claim the request."""
    def get_token() -> str:
        query = urllib.parse.urlencode({"AgentIdentity": guardian_app_id()})
        url = f"{SIDECAR_URL}/AuthorizationHeaderUnauthenticated/{api}?{query}"
        with urllib.request.urlopen(url, timeout=60) as response:
            return json.load(response)["authorizationHeader"].partition(" ")[2]
    return get_token


def validate_user_token(tc: str) -> dict:
    """The sidecar checks the approver's token (Tc): signature, issuer, audience (the guardian's
    blueprint) and the access_as_user scope (AzureAd__Audience and AzureAd__Scopes in
    docker-compose.yml). Returns its claims. A refusal raises urllib's HTTPError, an OSError."""
    request = urllib.request.Request(f"{SIDECAR_URL}/Validate", headers={"Authorization": f"Bearer {tc}"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)["claims"]


def obo_token(tc: str) -> str:
    """Tc exchanged with OBO (FR4): a Graph token for the delegated AgentIdentity.EnableDisable.All,
    as the guardian's agent identity, acting for the approver. The sidecar makes both requests."""
    query = urllib.parse.urlencode({"AgentIdentity": guardian_app_id()})
    request = urllib.request.Request(f"{SIDECAR_URL}/AuthorizationHeader/graph-delegated?{query}",
                                     headers={"Authorization": f"Bearer {tc}"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)["authorizationHeader"].partition(" ")[2]


def approval_service() -> IdentityApiApprovals:
    return IdentityApiApprovals(API_URL, token_for("identity-api"))


async def record_outcome(service: IdentityApiApprovals, claimed: DisableRequest, outcome: Status) -> None:
    """Tell the Identity API what the write did, so the scan, the model and the audit can tell a
    disabled agent from a failed write. If this call fails, the request stays running: the audit
    line here is then the record, and a later scan sees the agent's real state."""
    try:
        await asyncio.to_thread(service.record_outcome, claimed.agent_id, claimed.request_id, outcome)
    except (ApprovalError, *SERVICE_ERRORS) as error:
        audit_request("outcome_not_recorded", claimed, outcome=str(outcome),
                      reason=getattr(error, "reason", type(error).__name__))


async def write_disable(reader: GraphClient, writer: GraphWriter, approved: DisableRequest,
                        delegating_user: str | None = None) -> bool | None:
    """The write: reader reads with the guardian's app-only token, writer writes with the approver's
    OBO token (graph_writer.py says why). Returns whether a read already shows it, or None when the
    write failed. Its audit lines name the approver, so the action stands on its own (FR7), the
    delegating user, whose rights the write used, and the agent identity that acted (FR4, FR8, NFR2)."""
    approver = {"approver_id": approved.decided_by_id, "approver_name": approved.decided_by_name,
                "mode": "obo", "delegating_user": delegating_user, "agent_app_id": guardian_app_id()}
    try:
        confirmed = await asyncio.to_thread(disable_agent_identity, reader, writer, approved.agent_id)
    except SERVICE_ERRORS as error:
        # The HTTP status, when Graph answered (403: a missing permission or role). Never the body.
        audit_request("disable_failed", approved, http_status=getattr(error, "code", None),
                      **approver)
        return None
    except asyncio.CancelledError:
        # The write runs in its own thread and may still finish, so the outcome is unknown: check the tenant.
        audit_request("disable_abandoned", approved, stage="writing", **approver)
        raise
    audit_request("agent_disabled", approved, confirmed=confirmed, **approver)
    return confirmed


async def accept_delegation(agent_id: str, request_id: str, tc: str | None) -> tuple[int, str]:
    """The approver's delegation for one approved request. Returns the HTTP status and a short
    result word. Tc and the OBO token are never stored. Every refusal leaves the agent enabled."""
    def refuse(status: int, reason: str) -> tuple[int, str]:
        audit(event="delegation_refused", agent=agent_id[:64], request_id=request_id[:64], reason=reason)
        return status, reason.replace(" ", "_")

    # Both go into Identity API paths: only well-formed IDs pass.
    if not GUID.fullmatch(agent_id) or not GUID.fullmatch(request_id):
        return refuse(404, "no such request")
    if not tc:
        return refuse(401, "no token")
    try:
        claims = await asyncio.to_thread(validate_user_token, tc)
    except SERVICE_ERRORS:
        return refuse(401, "token rejected")
    oid = claims.get("oid") if isinstance(claims, dict) else None
    # The sidecar already requires the scope. Checked again here: a person's delegated token has scp.
    if not isinstance(oid, str) or "access_as_user" not in str(claims.get("scp", "")).split():
        return refuse(401, "token rejected")

    service = approval_service()
    try:
        found = await asyncio.to_thread(service.get, agent_id, request_id)
    except urllib.error.HTTPError as error:
        return refuse(404, "no such request") if error.code == 404 else refuse(503, "unavailable")
    except SERVICE_ERRORS:
        return refuse(503, "unavailable")
    if found.status in CLAIMED:
        return refuse(409, "already executed")
    if found.status != Status.APPROVED:
        return refuse(403, "not approved")
    # The binding: only the person who approved this request may lend their rights for it.
    if found.decided_by_id != oid:
        return refuse(403, "not the approver")

    # Time has passed since the request: the MCP server's checks again, right before the write.
    _, _, refusal = await check_can_be_disabled(GraphClient(token_for("graph")), agent_id, guardian_app_id(), audit)
    if refusal:
        return refuse(409, "no longer allowed")

    # The claim: once, soon after the approval, only for the approver. The Identity API checks all
    # three again, under its own lock, so a second delegation for this request cannot write.
    try:
        claimed = await asyncio.to_thread(service.execute, agent_id, request_id, oid)
    except ApprovalError:
        return refuse(409, "not executable")
    except SERVICE_ERRORS:
        return refuse(503, "unavailable")

    # From here the request is running. Every path below records its outcome, except a cancelled
    # write, whose outcome is unknown: it stays running (disable_abandoned).
    try:
        token = await asyncio.to_thread(obo_token, tc)
    except SERVICE_ERRORS:
        audit_request("disable_failed", claimed, stage="obo exchange", delegating_user=oid)
        await record_outcome(service, claimed, Status.FAILED)
        return 502, "write_failed"
    confirmed = await write_disable(GraphClient(token_for("graph")), GraphWriter(lambda: token), claimed,
                                    delegating_user=oid)
    if confirmed is None:
        await record_outcome(service, claimed, Status.FAILED)
        return 502, "write_failed"
    # Graph accepted the PATCH. A read that does not show it yet (confirmed False) is only lag.
    await record_outcome(service, claimed, Status.SUCCEEDED)
    return 200, "disabled"


@app.post("/agents/{agent_id}/disableRequests/{request_id}/delegation")
async def delegation(agent_id: str, request_id: str, request: Request) -> JSONResponse:
    """The approve CLI sends the approver's token (Tc) here, right after an approval. It answers
    with a result word only: no token and no detail."""
    scheme, _, tc = request.headers.get("authorization", "").partition(" ")
    status, result = await accept_delegation(agent_id, request_id, tc.strip() if scheme.lower() == "bearer" else None)
    return JSONResponse({"result": result}, status_code=status)
