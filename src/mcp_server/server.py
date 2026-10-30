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
from graph_reader import AgentDetails, GraphClient, fetch_agent_details, fetch_agents, fetch_blueprints, fetch_last_sign_ins
from rules import Finding, load_permission_policy, run_all_rules

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
