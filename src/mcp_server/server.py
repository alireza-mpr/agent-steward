"""The guardian's MCP server: the agent's hands (FR6).

It exposes the guardian's registry checks as MCP tools over Streamable HTTP. It gets the guardian's
app-only tokens from the token sidecar, whose network namespace it shares, and the guardian's harness
does not (docs/agent-steward-architecture.md). The harness reaches it at http://sidecar:8100/mcp and
never sees a token.

It can read Graph and ask for a disable, never write: the disable happens in the executor
(src/executor), with the approver's own rights (FR4). This server never receives the approver's
token, and it holds no write code.
"""

import asyncio
import json
import os
import sys
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mcp import MCPError
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import INVALID_PARAMS, ToolAnnotations

from allow_list import AllowListError, load_allow_list
from approval import SERVICE_ERRORS, ApprovalError, DisableRequest, IdentityApiApprovals
from disable_checks import GUID, check_can_be_disabled
from graph_reader import AgentDetails, GraphClient, fetch_agent_details, fetch_agents, fetch_blueprints, fetch_last_sign_ins
from rules import Finding, load_permission_policy, run_all_rules
from telemetry import audit, setup_tracing

SIDECAR_URL = os.environ.get("SIDECAR_URL", "http://127.0.0.1:5000")
API_URL = os.environ.get("API_URL", "http://identity-api:8000")
POLICY_PATH = Path(os.environ.get("POLICY_PATH", "/config/agent-permissions.json"))
ALLOW_LIST_PATH = Path(os.environ.get("ALLOW_LIST_PATH", "/config/tool-allow-list.json"))
MCP_PORT = int(os.environ.get("MCP_PORT", "8100"))
# The Host headers the server accepts (DNS rebinding protection): the name the harness uses.
MCP_ALLOWED_HOSTS = os.environ.get("MCP_ALLOWED_HOSTS", "sidecar:8100").split(",")


def token_for(api: str):
    """A function that asks the sidecar for a token for one downstream API, as the guardian."""
    def get_token() -> str:
        query = urllib.parse.urlencode({"AgentIdentity": os.environ["GUARDIAN_AGENT_APP_ID"]})
        url = f"{SIDECAR_URL}/AuthorizationHeaderUnauthenticated/{api}?{query}"
        with urllib.request.urlopen(url, timeout=60) as response:
            return json.load(response)["authorizationHeader"].partition(" ")[2]
    return get_token


def submit(findings: list[Finding]) -> dict:
    """Send the findings to the Identity API, which writes one audit line per decision (FR8)."""
    request = urllib.request.Request(
        f"{API_URL}/findings",
        data=json.dumps({"findings": [asdict(finding) for finding in findings]}).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {token_for('identity-api')()}"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


@dataclass(frozen=True)
class RequestStatus:
    """The latest disable request for a dormant agent, so the model can see what is already asked."""
    agent_app_id: str
    request_id: str
    status: str                 # pending, approved, denied, expired, running, succeeded or failed (approval.Status)
    created_at: str | None
    decided_by: str | None      # the approver's display name, once someone decided
    decided_at: str | None


@dataclass(frozen=True)
class ScanResult:
    findings: list[Finding]
    accepted: int          # how many findings the Identity API accepted
    disable_requests: list[RequestStatus]   # the latest request for each dormant agent that has one


def latest_requests(findings: list[Finding]) -> list[RequestStatus]:
    """The latest disable request this server made for each dormant agent, from the Identity API."""
    service = approval_service()
    latest = []
    for agent_app_id in sorted({finding.agent_app_id for finding in findings if finding.check == "dormant"}):
        requests = service.of_agent(agent_app_id)
        if requests:
            newest = requests[0]
            latest.append(RequestStatus(agent_app_id, newest.request_id, newest.status, newest.created_at,
                                        newest.decided_by_name, newest.decided_at))
    return latest


def scan() -> ScanResult:
    """Read the tenant, apply the rules, report the findings to the Identity API, and add the
    status of the latest disable request for each dormant agent."""
    now = datetime.now(timezone.utc)
    graph = GraphClient(token_for("graph"))
    findings = run_all_rules(fetch_agents(graph), fetch_blueprints(graph),
                             load_permission_policy(POLICY_PATH), fetch_last_sign_ins(graph, now), now)
    answer = submit(findings)
    return ScanResult(findings, answer["accepted"], latest_requests(findings))


# Sent in every answer's _meta (serverInfo), so logs and clients can tell which build answered.
# Self-declared, like an HTTP Server header: useful for debugging, never for access decisions.
SERVER_NAME = "agent-steward-mcp-server"
SERVER_VERSION = "1.0.0"




class AllowListedServer(MCPServer):
    """The SDK's server with the tool allow-list in front of tools/list and tools/call (FR6).

    The list is the same for every caller, because the server does not know who calls it (D1).
    It starts empty, so the server offers and runs nothing until the list is loaded.
    """

    allowed_tools: frozenset[str] = frozenset()

    async def registered_tool_names(self) -> set[str]:
        """Every tool in the code, on the allow-list or not."""
        return {tool.name for tool in await super().list_tools()}

    async def list_tools(self):
        return [tool for tool in await super().list_tools() if tool.name in self.allowed_tools]

    async def call_tool(self, name: str, arguments: dict[str, Any], context=None):
        if name not in self.allowed_tools:
            # The name comes from the caller: shortened, and json.dumps escapes line breaks.
            audit(event="tool_call", tool=name[:128], decision="refused", reason="not on the allow-list")
            # The MCP spec answers an unknown tool with a protocol error. A tool that exists but
            # is not allowed gets the same answer, so a caller cannot find out which tools exist.
            raise MCPError(INVALID_PARAMS, f"Unknown tool: {name}")
        # Only the tool name: the argument values are the caller's data.
        audit(event="tool_call", tool=name, decision="allowed")
        return await super().call_tool(name, arguments, context)


mcp = AllowListedServer(SERVER_NAME, version=SERVER_VERSION)


# Not read-only: it writes the findings to the Identity API's audit log itself, so they never
# pass through the model on their way there (docs/decisions.md, ADR-001).
@mcp.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False))
def scan_registry() -> ScanResult:
    """Check every agent identity in the tenant for four flaws: no sponsor (orphan), a blueprint
    credential that expires soon, more permissions than its policy allows, and no sign-in for 30
    days (dormant). Reports the findings to the Identity API's audit log, then returns them."""
    return scan()


@mcp.tool(annotations=ToolAnnotations(read_only_hint=True))
def get_agent(app_id: str) -> AgentDetails:
    """Read one agent identity by its app ID: its name, its blueprint and its description.
    The description is free text written by whoever registered the agent."""
    # MCP servers must validate every input. app_id goes into a Graph filter, so only a GUID passes.
    if not GUID.fullmatch(app_id):
        raise ToolError("app_id must be an agent identity's app ID (a GUID)")
    details = fetch_agent_details(GraphClient(token_for("graph")), app_id)
    if details is None:
        raise ToolError(f"No agent identity has the app ID {app_id}")
    return details


@dataclass(frozen=True)
class SubmittedRequest:
    app_id: str
    name: str
    request_id: str
    status: str                 # pending: nothing is disabled yet; an approver decides later
    expires_at: str | None      # when the request stops waiting for an approver


# The model reads these. They say what happened, and no internal detail (CWE-209).
NOT_REQUESTED = {
    "already_waiting": "Not requested: a request for this agent is already pending.",
    "unavailable": "Not requested: the approval service could not be reached.",
}
MAX_REASON = 500                    # the model's reason is shown to the approver, labelled as untrusted


def approval_service() -> IdentityApiApprovals:
    return IdentityApiApprovals(API_URL, token_for("identity-api"))


def audit_request(event: str, request: DisableRequest, **fields) -> None:
    audit(event=event, agent=request.agent_id, request_id=request.request_id, **fields)


async def request_approval(service: IdentityApiApprovals, agent: AgentDetails, dormant: Finding,
                           reason: str) -> tuple[DisableRequest | None, str | None]:
    """Create the disable request. Returns the request and None, or None and why there is none. The
    facts come from Graph, so the approver can trust them. The reason comes from the model."""
    facts = {"name": agent.name, "blueprintAppId": agent.blueprint_app_id, "flaw": dormant.detail,
             "lastSignIn": dormant.date}
    try:
        request = await asyncio.to_thread(service.create, agent.app_id, reason[:MAX_REASON], facts)
    except ApprovalError as error:
        audit(event="disable_refused", agent=agent.app_id, reason=error.reason)
        return None, error.reason
    except SERVICE_ERRORS:
        audit(event="disable_refused", agent=agent.app_id, reason="unavailable")
        return None, "unavailable"
    audit_request("disable_requested", request)
    return request, None


# Only the guardian's own tool for disabling: it asks, and returns at once. The write is not here:
# it happens in the executor, with the approver's rights (decision 7: no shortcut around OBO).
@mcp.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False))
async def submit_disable_agent_request(app_id: str, reason: str) -> SubmittedRequest:
    """Ask a human to approve disabling one dormant agent identity. Returns at once with the request,
    which is pending: nothing is disabled now. If the approver approves, the agent is disabled with
    the approver's own rights. A later scan_registry shows the request's status. Refused for an
    agent that is not dormant, for the guardian's own agents, and while a request for the agent is
    already pending. reason: one short sentence for the approver."""
    graph = GraphClient(token_for("graph"))   # app-only, read-only: the checks before anyone is asked

    agent, dormant, refusal = await check_can_be_disabled(graph, app_id, os.environ["GUARDIAN_AGENT_APP_ID"], audit)
    if refusal:
        raise ToolError(refusal)

    request, not_created = await request_approval(approval_service(), agent, dormant, reason)
    if not_created:
        raise ToolError(NOT_REQUESTED[not_created])
    return SubmittedRequest(app_id, agent.name, request.request_id, request.status, request.expires_at)


if __name__ == "__main__":
    try:
        mcp.allowed_tools = load_allow_list(ALLOW_LIST_PATH, asyncio.run(mcp.registered_tool_names()))
    except AllowListError as error:
        sys.exit(f"The MCP server did not start: {error}")
    audit(event="allow_list_loaded", allowed_tools=sorted(mcp.allowed_tools))
    if setup_tracing():   # only when OTEL_SERVICE_NAME is set (src/shared/telemetry.py)
        from opentelemetry.instrumentation.urllib import URLLibInstrumentor
        # A client span for each call to the sidecar, Graph and the Identity API, and the traceparent
        # header on each. It records the method, the URL and the status, never a header or a body.
        URLLibInstrumentor().instrument()
    # 0.0.0.0, because the harness calls from another container. The port is reachable on the
    # compose network, not from the host (docs/agent-steward-architecture.md, D1).
    mcp.run(transport="streamable-http", host="0.0.0.0", port=MCP_PORT,
            transport_security=TransportSecuritySettings(allowed_hosts=MCP_ALLOWED_HOSTS))
