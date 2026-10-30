"""End-to-end test of the guardian's tools (FR3, FR6): the real MCP server with its tool allow-list,
the real Graph, the real rules and the running Identity API. No model is involved: the test calls
the tool the model would ask for, from the guardian's own network.

Runs inside docker compose, on the guardian's network (outside the sidecar's namespace):
    docker compose up -d sidecar api mcp-server
    docker compose run --rm end-to-end-tests
"""

import asyncio
import os
import socket

import pytest
from mcp import Client, MCPError

pytestmark = pytest.mark.end_to_end

MCP_URL = os.environ.get("MCP_URL", "http://sidecar:8100/mcp")

# One planned flaw per fixture (setup/agent-id/Deploy-Fixtures.ps1).
PLANNED_FINDINGS = {
    ("agent-steward-fixture-orphan-01", "orphan"),
    ("agent-steward-fixture-expiring-01", "expiring_credential"),
    ("agent-steward-fixture-overperm-01", "over_permissioned"),
    ("agent-steward-fixture-dormant-01", "dormant"),
}


def call_tool(name: str, arguments: dict):
    async def run():
        async with Client(MCP_URL) as client:
            try:
                return await client.call_tool(name, arguments)
            except MCPError as error:
                # Raised again outside the client, which would wrap it in an exception group.
                return error
    outcome = asyncio.run(run())
    if isinstance(outcome, MCPError):
        raise outcome
    return outcome


def list_tools() -> set[str]:
    async def run():
        async with Client(MCP_URL) as client:
            return (await client.list_tools()).tools
    return {tool.name for tool in asyncio.run(run())}


def test_scan_registry_finds_every_planned_flaw_and_reports_it():
    outcome = call_tool("scan_registry", {})

    assert not outcome.is_error
    findings = outcome.structured_content["findings"]
    found = {(finding["agent_name"], finding["check"]) for finding in findings}
    assert PLANNED_FINDINGS <= found
    assert not [finding for finding in findings if finding["agent_name"].startswith("agent-steward-guardian")]
    assert outcome.structured_content["accepted"] == len(findings)


def test_get_agent_rejects_an_input_that_is_not_a_guid():
    outcome = call_tool("get_agent", {"app_id": "x' or appId ne 'x"})

    assert outcome.is_error


def test_tools_list_returns_only_the_tools_on_the_allow_list():
    # config/tool-allow-list.json, as the running server loaded it at start.
    assert list_tools() == {"scan_registry", "get_agent"}


def test_a_tool_off_the_allow_list_is_refused_with_the_specs_unknown_tool_error():
    # The MCP server writes a "refused" audit line for it: docker compose logs mcp-server
    with pytest.raises(MCPError) as refusal:
        call_tool("disable_agent", {})

    assert refusal.value.code == -32602   # a protocol error (JSON-RPC invalid params), not an isError result
    assert refusal.value.message == "Unknown tool: disable_agent"


def test_the_guardians_network_cannot_reach_the_token_sidecar():
    # The sidecar listens on 127.0.0.1 inside its own namespace, so from here the port is closed.
    with pytest.raises(OSError):
        socket.create_connection(("sidecar", 5000), timeout=5).close()
