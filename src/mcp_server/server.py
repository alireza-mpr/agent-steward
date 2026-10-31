"""The guardian's MCP server: the agent's hands (FR6).

It exposes the guardian's registry checks as MCP tools over Streamable HTTP. It is the only part
of the agent that can get the guardian's tokens: it shares the token sidecar's network namespace,
and the guardian's harness does not (docs/agent-architecture.md). The harness reaches it at
http://sidecar:8100/mcp and never sees a token.
"""

import asyncio
import json
import os
import re
import sys
import urllib.parse
import urllib.request
import uuid
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
from approval import SERVICE_ERRORS, ApprovalError, DisableRequest, IdentityApiApprovals, wait_for_decision
from graph_reader import (AgentDetails, GraphClient, disable_agent_identity, fetch_agent_details, fetch_agents,
                          fetch_blueprints, fetch_last_sign_ins)
from rules import Agent, Finding, find_dormant, load_permission_policy, run_all_rules

SIDECAR_URL = os.environ.get("SIDECAR_URL", "http://127.0.0.1:5000")
API_URL = os.environ.get("API_URL", "http://api:8000")
POLICY_PATH = Path(os.environ.get("POLICY_PATH", "/config/agent-permissions.json"))
ALLOW_LIST_PATH = Path(os.environ.get("ALLOW_LIST_PATH", "/config/tool-allow-list.json"))
MCP_PORT = int(os.environ.get("MCP_PORT", "8100"))
# The Host headers the server accepts (DNS rebinding protection): the name the harness uses.
MCP_ALLOWED_HOSTS = os.environ.get("MCP_ALLOWED_HOSTS", "sidecar:8100").split(",")

GUID = re.compile(r"^[0-9a-fA-F]{8}-([0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$")


def token_for(api: str):
    """A function that asks the sidecar for a token for one downstream API, as the guardian."""
    def get_token() -> str:
        query = urllib.parse.urlencode({"AgentIdentity": os.environ["GUARDIAN_AGENT_APP_ID"]})
        url = f"{SIDECAR_URL}/AuthorizationHeaderUnauthenticated/{api}?{query}"
        with urllib.request.urlopen(url, timeout=60) as response:
            return json.load(response)["authorizationHeader"].partition(" ")[2]
    return get_token


def submit(findings: list[Finding], correlation_id: str) -> dict:
    """Send the findings to the Identity API, which writes one audit line per decision (FR8)."""
    request = urllib.request.Request(
        f"{API_URL}/findings",
        data=json.dumps({"findings": [asdict(finding) for finding in findings]}).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {token_for('identity-api')()}",
                 "X-Correlation-ID": correlation_id},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


@dataclass(frozen=True)
class ScanResult:
    findings: list[Finding]
    accepted: int          # how many findings the Identity API accepted
    correlation_id: str    # the Identity API writes the same ID into its audit line


def scan() -> ScanResult:
    """Read the tenant, apply the rules, and report the findings to the Identity API."""
    now = datetime.now(timezone.utc)
    correlation_id = str(uuid.uuid4())
    graph = GraphClient(token_for("graph"))
    findings = run_all_rules(fetch_agents(graph), fetch_blueprints(graph),
                             load_permission_policy(POLICY_PATH), fetch_last_sign_ins(graph, now), now)
    answer = submit(findings, correlation_id)
    return ScanResult(findings, answer["accepted"], correlation_id)


# Sent in every answer's _meta (serverInfo), so logs and clients can tell which build answered.
# Self-declared, like an HTTP Server header: useful for debugging, never for access decisions.
SERVER_NAME = "agent-steward-mcp-server"
SERVER_VERSION = "1.0.0"


def audit(**fields) -> None:
    """Write one audit line on stdout, like the Identity API's (FR8). Never pass a token in here."""
    record = {"timestamp": datetime.now(timezone.utc).isoformat(), **fields}
    print(json.dumps(record), file=sys.stdout, flush=True)


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
    days (dormant). Reports the findings to the Identity API's audit log, then returns them with
    the audit's correlation ID."""
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
class DisableResult:
    app_id: str
    name: str
    approved_by: str | None     # the approver's display name
    request_id: str
    confirmed: bool             # whether a read already shows the agent as disabled


# The model reads these. They say what happened, and no internal detail (CWE-209).
NOT_APPROVED = {
    "already_waiting": "Not disabled: a request for this agent is already waiting.",
    "denied": "Not disabled: the approver denied the request.",
    "expired": "Not disabled: no approver decided in time.",
    "mismatch": "Not disabled: the approval service's answer did not match the request.",
    "reused": "Not disabled: this approval was already used.",
    "unavailable": "Not disabled: the approval service could not be reached.",
}
MAX_REASON = 500                    # the model's reason is shown to the approver, labelled as untrusted
used_request_ids: set[str] = set()  # each approval works once


def approval_service() -> IdentityApiApprovals:
    return IdentityApiApprovals(API_URL, token_for("identity-api"))


def is_protected(graph: GraphClient, agent: AgentDetails) -> bool:
    """The guardian's own agents: the guardian itself and the others of its blueprint. If the
    guardian cannot be read, every agent counts as protected (fail closed)."""
    guardian = fetch_agent_details(graph, os.environ["GUARDIAN_AGENT_APP_ID"])
    return guardian is None or agent.blueprint_app_id == guardian.blueprint_app_id


def dormant_finding(graph: GraphClient, agent: AgentDetails, now: datetime) -> Finding | None:
    """The same rule as scan_registry, so the tool does not depend on what the model says."""
    plain = Agent(agent.app_id, agent.name, agent.blueprint_app_id, sponsor_count=0)
    return next(iter(find_dormant([plain], fetch_last_sign_ins(graph, now), now)), None)


def audit_request(event: str, request: DisableRequest, correlation_id: str, **fields) -> None:
    audit(event=event, agent=request.agent_id, request_id=request.request_id, correlation_id=correlation_id, **fields)


# The steps of disable_agent. A step reports what happened and raises nothing for an expected
# outcome, so every decision to stop is in one place: disable_agent itself.

async def check_can_be_disabled(graph: GraphClient, app_id: str) -> tuple[AgentDetails | None, Finding | None, str | None]:
    """The server's own checks, before anyone is asked. Returns the agent, its dormant finding,
    and the refusal text, which is None when the agent can be disabled."""
    # MCP servers must validate every input. app_id goes into a Graph filter, so only a GUID passes.
    if not GUID.fullmatch(app_id):
        return None, None, "app_id must be an agent identity's app ID (a GUID)"
    agent = await asyncio.to_thread(fetch_agent_details, graph, app_id)
    if agent is None:
        return None, None, f"No agent identity has the app ID {app_id}"
    if await asyncio.to_thread(is_protected, graph, agent):
        audit(event="disable_refused", agent=app_id, reason="protected")
        return agent, None, "Refused: this agent belongs to the guardian."
    dormant = await asyncio.to_thread(dormant_finding, graph, agent, datetime.now(timezone.utc))
    if dormant is None:
        audit(event="disable_refused", agent=app_id, reason="not dormant")
        return agent, None, "Refused: this agent signed in within the last 30 days."
    return agent, dormant, None


async def request_approval(service: IdentityApiApprovals, agent: AgentDetails, dormant: Finding, reason: str,
                           correlation_id: str) -> tuple[DisableRequest | None, str | None]:
    """Create the disable request. Returns the request and None, or None and why there is none. The
    facts come from Graph, so the approver can trust them. The reason comes from the model."""
    facts = {"name": agent.name, "blueprintAppId": agent.blueprint_app_id, "flaw": dormant.detail,
             "lastSignIn": dormant.date}
    try:
        request = await asyncio.to_thread(service.create, agent.app_id, reason[:MAX_REASON], facts, correlation_id)
    except ApprovalError as error:
        audit(event="disable_refused", agent=agent.app_id, reason=error.reason)
        return None, error.reason
    except SERVICE_ERRORS:
        audit(event="disable_refused", agent=agent.app_id, reason="unavailable")
        return None, "unavailable"
    audit_request("disable_requested", request, correlation_id)
    return request, None


async def wait_for_approval(service: IdentityApiApprovals, request: DisableRequest,
                            correlation_id: str) -> tuple[DisableRequest | None, str | None]:
    """Wait for the human. Returns the approved request and None, or None and why it was not approved."""
    try:
        approved = await wait_for_decision(lambda: asyncio.to_thread(service.get, request.agent_id, request.request_id),
                                           request, used_request_ids, lambda: datetime.now(timezone.utc),
                                           asyncio.sleep)
    except ApprovalError as error:
        audit_request("disable_decision", request, correlation_id, decision=error.reason)
        return None, error.reason
    except asyncio.CancelledError:
        # The caller left, for example the run was stopped. Nobody will act on this request now, even
        # if a person still approves it at the Identity API, so the record must say why nothing follows.
        audit_request("disable_abandoned", request, correlation_id, stage="waiting for approval")
        raise
    audit_request("disable_decision", request, correlation_id, decision="approved",
                  approver_id=approved.decided_by_id, approver_name=approved.decided_by_name)
    return approved, None


async def write_disable(graph: GraphClient, approved: DisableRequest, correlation_id: str) -> bool | None:
    """The write. Returns whether a read already shows it, or None when the write failed. Its audit
    lines name the approver, so the action stands on its own (FR7)."""
    approver = {"approver_id": approved.decided_by_id, "approver_name": approved.decided_by_name}
    try:
        confirmed = await asyncio.to_thread(disable_agent_identity, graph, approved.agent_id)
    except SERVICE_ERRORS:
        audit_request("disable_failed", approved, correlation_id, **approver)
        return None
    except asyncio.CancelledError:
        # The write runs in its own thread and may still finish, so the outcome is unknown: check the tenant.
        audit_request("disable_abandoned", approved, correlation_id, stage="writing", **approver)
        raise
    audit_request("agent_disabled", approved, correlation_id, confirmed=confirmed, **approver)
    return confirmed


@mcp.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True))
async def disable_agent(app_id: str, reason: str) -> DisableResult:
    """Ask a human to approve disabling one dormant agent identity, and wait up to 4 minutes for
    the decision. The agent is disabled only if the human approves. Refused for an agent that is
    not dormant, and for the guardian's own agents. reason: one short sentence for the approver."""
    graph = GraphClient(token_for("graph"))
    service = approval_service()
    correlation_id = str(uuid.uuid4())

    agent, dormant, refusal = await check_can_be_disabled(graph, app_id)
    if refusal:
        raise ToolError(refusal)

    request, not_created = await request_approval(service, agent, dormant, reason, correlation_id)
    if not_created:
        raise ToolError(NOT_APPROVED[not_created])

    approved, not_approved = await wait_for_approval(service, request, correlation_id)
    if not_approved:
        raise ToolError(NOT_APPROVED[not_approved])

    confirmed = await write_disable(graph, approved, correlation_id)
    if confirmed is None:
        raise ToolError("Not disabled: the approver approved, but the write to Microsoft Graph failed.")

    return DisableResult(app_id, agent.name, approved.decided_by_name, request.request_id, confirmed)


if __name__ == "__main__":
    try:
        mcp.allowed_tools = load_allow_list(ALLOW_LIST_PATH, asyncio.run(mcp.registered_tool_names()))
    except AllowListError as error:
        sys.exit(f"The MCP server did not start: {error}")
    audit(event="allow_list_loaded", allowed_tools=sorted(mcp.allowed_tools))
    # 0.0.0.0, because the harness calls from another container. The port is reachable on the
    # compose network, not from the host (docs/agent-architecture.md, D1).
    mcp.run(transport="streamable-http", host="0.0.0.0", port=MCP_PORT,
            transport_security=TransportSecuritySettings(allowed_hosts=MCP_ALLOWED_HOSTS))
