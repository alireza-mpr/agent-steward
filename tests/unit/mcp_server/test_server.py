"""Unit tests for the MCP server's tools (FR6, NFR8), through a real MCP client in memory.

No network: Graph and the Identity API are replaced where a test needs them.
"""

import asyncio

import pytest
from mcp import Client

import server
from graph_reader import AgentDetails

AGENT = AgentDetails("11111111-2222-3333-4444-555555555555", "agent-steward-fixture-orphan-01",
                     "66666666-7777-8888-9999-000000000000", "A fixture agent.")


def call(name: str, arguments: dict):
    async def run():
        async with Client(server.mcp) as client:
            return await client.call_tool(name, arguments)
    return asyncio.run(run())


def list_tools():
    async def run():
        async with Client(server.mcp) as client:
            return (await client.list_tools()).tools
    return {tool.name: tool for tool in asyncio.run(run())}


def test_the_server_offers_exactly_the_two_tools():
    assert set(list_tools()) == {"scan_registry", "get_agent"}


def test_scan_registry_is_not_marked_read_only_because_it_writes_the_audit_log():
    # docs/decisions.md, ADR-001: the tool sends the findings to the Identity API itself.
    tools = list_tools()
    assert tools["scan_registry"].annotations.read_only_hint is False
    assert tools["get_agent"].annotations.read_only_hint is True


@pytest.mark.parametrize("app_id", ["not-a-guid", "x' or appId ne 'x", AGENT.app_id + "/../owners", ""])
def test_get_agent_rejects_an_input_that_is_not_a_guid_before_calling_graph(app_id, monkeypatch):
    def must_not_run(*args):
        raise AssertionError("Graph was called with an unchecked input")
    monkeypatch.setattr(server, "fetch_agent_details", must_not_run)

    outcome = call("get_agent", {"app_id": app_id})

    assert outcome.is_error
    assert "GUID" in outcome.content[0].text


def test_get_agent_returns_the_agent_with_its_description(monkeypatch):
    monkeypatch.setattr(server, "fetch_agent_details", lambda client, app_id: AGENT)

    outcome = call("get_agent", {"app_id": AGENT.app_id})

    assert not outcome.is_error
    assert outcome.structured_content["description"] == "A fixture agent."


def test_get_agent_reports_an_unknown_agent_as_a_tool_error(monkeypatch):
    monkeypatch.setattr(server, "fetch_agent_details", lambda client, app_id: None)

    outcome = call("get_agent", {"app_id": AGENT.app_id})

    assert outcome.is_error
