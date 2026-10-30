"""Unit tests for the MCP server's tools and its tool allow-list (FR6, NFR8), through a real MCP
client in memory.

No network: Graph and the Identity API are replaced where a test needs them.
"""

import asyncio
import json
from pathlib import Path

import pytest
from mcp import Client, MCPError
from mcp.types import INVALID_PARAMS

import server
from allow_list import load_allow_list
from graph_reader import AgentDetails

AGENT = AgentDetails("11111111-2222-3333-4444-555555555555", "agent-steward-fixture-orphan-01",
                     "66666666-7777-8888-9999-000000000000", "A fixture agent.")
REAL_ALLOW_LIST = Path(__file__).parents[3] / "config" / "tool-allow-list.json"


@pytest.fixture(autouse=True)
def real_allow_list(monkeypatch):
    """Every test starts with the real allow-list, loaded the way the server loads it at start."""
    registered = asyncio.run(server.mcp.registered_tool_names())
    monkeypatch.setattr(server.mcp, "allowed_tools", load_allow_list(REAL_ALLOW_LIST, registered))


def audit_lines(capsys) -> list[dict]:
    """The audit lines written so far, without their timestamps."""
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    return [{key: value for key, value in line.items() if key != "timestamp"} for line in lines]


def call(name: str, arguments: dict):
    async def run():
        async with Client(server.mcp) as client:
            try:
                return await client.call_tool(name, arguments)
            except MCPError as error:
                # Raised again outside the client, which would wrap it in an exception group.
                return error
    outcome = asyncio.run(run())
    if isinstance(outcome, MCPError):
        raise outcome
    return outcome


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


# The allow-list. get_agent stands in for a tool that is in the code but not on the list.

def test_tools_list_shows_only_the_tools_on_the_allow_list(monkeypatch):
    monkeypatch.setattr(server.mcp, "allowed_tools", frozenset({"scan_registry"}))

    assert set(list_tools()) == {"scan_registry"}


def test_a_tool_off_the_allow_list_is_refused_before_it_runs_and_the_refusal_is_logged(monkeypatch, capsys):
    monkeypatch.setattr(server.mcp, "allowed_tools", frozenset({"scan_registry"}))
    def must_not_run(*args):
        raise AssertionError("a tool off the allow-list ran")
    monkeypatch.setattr(server, "fetch_agent_details", must_not_run)

    with pytest.raises(MCPError) as refusal:
        call("get_agent", {"app_id": AGENT.app_id})

    # A protocol error, as the MCP spec answers an unknown tool, not a result with isError.
    assert refusal.value.code == INVALID_PARAMS
    assert audit_lines(capsys) == [{"event": "tool_call", "tool": "get_agent", "decision": "refused",
                                    "reason": "not on the allow-list"}]


def test_a_blocked_tool_and_a_made_up_name_get_the_same_answer(monkeypatch):
    # So a caller cannot find out which tools exist.
    monkeypatch.setattr(server.mcp, "allowed_tools", frozenset({"scan_registry"}))
    answers = {}
    for name in ("get_agent", "no_such_tool"):
        with pytest.raises(MCPError) as refusal:
            call(name, {"app_id": AGENT.app_id})
        answers[name] = (refusal.value.code, refusal.value.message.replace(name, "<name>"))

    assert answers["get_agent"] == answers["no_such_tool"] == (INVALID_PARAMS, "Unknown tool: <name>")


def test_an_allowed_tool_runs_and_is_logged_without_its_arguments(monkeypatch, capsys):
    monkeypatch.setattr(server, "fetch_agent_details", lambda client, app_id: AGENT)

    outcome = call("get_agent", {"app_id": AGENT.app_id})

    assert not outcome.is_error
    assert audit_lines(capsys) == [{"event": "tool_call", "tool": "get_agent", "decision": "allowed"}]


def test_a_long_tool_name_is_shortened_in_the_audit_line(capsys):
    with pytest.raises(MCPError):
        call("x" * 5000 + "\nforged line", {})

    [line] = audit_lines(capsys)
    assert line["tool"] == "x" * 128


def test_before_the_allow_list_is_loaded_the_server_offers_and_runs_nothing(monkeypatch):
    monkeypatch.setattr(server.mcp, "allowed_tools", server.AllowListedServer.allowed_tools)

    assert list_tools() == {}
    with pytest.raises(MCPError):
        call("scan_registry", {})
