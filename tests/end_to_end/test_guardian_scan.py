"""End-to-end test of the guardian's tools (FR3, FR6): the real MCP server, the real Graph, the real
rules and the running Identity API. No model is involved: the test calls the tool the model would
ask for, from the guardian's own network.

Runs inside docker compose, on the guardian's network (outside the sidecar's namespace):
    docker compose up -d sidecar api mcp-server
    docker compose run --rm end-to-end-tests
"""

import asyncio
import os
import socket

import pytest
from mcp import Client

pytestmark = pytest.mark.end_to_end

MCP_URL = os.environ.get("MCP_URL", "http://sidecar:8100/mcp")

# One planned flaw per fixture (setup/agent-id/Deploy-Fixtures.ps1).
PLANNED_FINDINGS = {
    ("agent-steward-fixture-orphan-01", "orphan"),
    ("agent-steward-fixture-expiring-01", "expiring_credential"),
    ("agent-steward-fixture-overperm-01", "over_permissioned"),
    ("agent-steward-fixture-dormant-01", "dormant"),
}


async def call_tool(name: str, arguments: dict):
    async with Client(MCP_URL) as client:
        return await client.call_tool(name, arguments)


def test_scan_registry_finds_every_planned_flaw_and_reports_it():
    outcome = asyncio.run(call_tool("scan_registry", {}))

    assert not outcome.is_error
    findings = outcome.structured_content["findings"]
    found = {(finding["agent_name"], finding["check"]) for finding in findings}
    assert PLANNED_FINDINGS <= found
    assert not [finding for finding in findings if finding["agent_name"].startswith("agent-steward-guardian")]
    assert outcome.structured_content["accepted"] == len(findings)


def test_get_agent_rejects_an_input_that_is_not_a_guid():
    outcome = asyncio.run(call_tool("get_agent", {"app_id": "x' or appId ne 'x"}))

    assert outcome.is_error


def test_the_guardians_network_cannot_reach_the_token_sidecar():
    # The sidecar listens on 127.0.0.1 inside its own namespace, so from here the port is closed.
    with pytest.raises(OSError):
        socket.create_connection(("sidecar", 5000), timeout=5).close()
