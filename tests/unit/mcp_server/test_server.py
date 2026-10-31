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
from approval import ApprovalError, DisableRequest
from graph_reader import AgentDetails
from rules import Finding

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


def test_the_server_offers_exactly_the_three_tools():
    assert set(list_tools()) == {"scan_registry", "get_agent", "disable_agent"}


def test_scan_registry_is_not_marked_read_only_because_it_writes_the_audit_log():
    # docs/decisions.md, ADR-001: the tool sends the findings to the Identity API itself.
    tools = list_tools()
    assert tools["scan_registry"].annotations.read_only_hint is False
    assert tools["get_agent"].annotations.read_only_hint is True


def test_disable_agent_is_marked_destructive():
    assert list_tools()["disable_agent"].annotations.destructive_hint is True


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


# disable_agent (FR7). Graph and the approval service are replaced: nothing here can disable anything.

GUARDIAN = AgentDetails("99999999-2222-3333-4444-555555555555", "agent-steward-guardian-01",
                        "aaaaaaaa-7777-8888-9999-000000000000", "")
DORMANT = Finding(AGENT.app_id, AGENT.name, "dormant", "no sign-in in the last 30 days")


class FakeApprovals:
    """Answers each poll with the next status. The last one repeats."""

    def __init__(self, *statuses: str):
        self.statuses = list(statuses)
        self.created: list[tuple] = []

    def create(self, agent_id, reason, facts, correlation_id):
        self.created.append((agent_id, reason, facts))
        return DisableRequest("request-1", agent_id, "pending")

    def get(self, agent_id, request_id):
        status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        if status == "unreachable":
            raise OSError("connection refused")
        decided = ("approver-oid", "An Approver") if status in ("approved", "denied") else (None, None)
        return DisableRequest(request_id, agent_id, status, *decided)


@pytest.fixture
def disable_setup(monkeypatch):
    """A dormant agent that is not the guardian's. Records every real disable."""
    monkeypatch.setenv("GUARDIAN_AGENT_APP_ID", GUARDIAN.app_id)
    monkeypatch.setattr(server, "fetch_agent_details",
                        lambda client, app_id: GUARDIAN if app_id == GUARDIAN.app_id else AGENT)
    monkeypatch.setattr(server, "dormant_finding", lambda graph, agent, now: DORMANT)
    monkeypatch.setattr(server, "used_request_ids", set())
    async def no_wait(seconds):
        return None
    monkeypatch.setattr(server.asyncio, "sleep", no_wait)
    disabled = []
    def fake_disable(graph, app_id):
        disabled.append(app_id)
        return True
    monkeypatch.setattr(server, "disable_agent_identity", fake_disable)
    def use(approvals):
        monkeypatch.setattr(server, "approval_service", lambda: approvals)
        return approvals
    return disabled, use


def disable(app_id=AGENT.app_id):
    return call("disable_agent", {"app_id": app_id, "reason": "It is dormant."})


@pytest.mark.parametrize("app_id", ["not-a-guid", "x' or appId ne 'x", AGENT.app_id + "/../owners", ""])
def test_disable_agent_rejects_an_input_that_is_not_a_guid_before_calling_graph(app_id, monkeypatch):
    def must_not_run(*args):
        raise AssertionError("Graph was called with an unchecked input")
    monkeypatch.setattr(server, "fetch_agent_details", must_not_run)

    assert disable(app_id).is_error


def test_disable_agent_reports_an_unknown_agent_as_a_tool_error(disable_setup, monkeypatch):
    disabled, use = disable_setup
    approvals = use(FakeApprovals("approved"))
    monkeypatch.setattr(server, "fetch_agent_details", lambda client, app_id: None)

    assert disable().is_error
    assert approvals.created == [] and disabled == []


@pytest.mark.parametrize("target", [GUARDIAN, AgentDetails("bbbbbbbb-2222-3333-4444-555555555555",
                                                           "agent-steward-guardian-norole-01",
                                                           GUARDIAN.blueprint_app_id, "")])
def test_disable_agent_refuses_the_guardians_own_agents_and_asks_nobody(target, disable_setup, monkeypatch, capsys):
    disabled, use = disable_setup
    approvals = use(FakeApprovals("approved"))
    monkeypatch.setattr(server, "fetch_agent_details",
                        lambda client, app_id: GUARDIAN if app_id == GUARDIAN.app_id else target)

    outcome = disable(target.app_id)

    assert outcome.is_error and "guardian" in outcome.content[0].text
    assert approvals.created == [] and disabled == []
    assert {"event": "disable_refused", "agent": target.app_id, "reason": "protected"} in audit_lines(capsys)


def test_disable_agent_refuses_everything_when_the_guardian_cannot_be_read(disable_setup, monkeypatch):
    disabled, use = disable_setup
    approvals = use(FakeApprovals("approved"))
    monkeypatch.setattr(server, "fetch_agent_details",
                        lambda client, app_id: None if app_id == GUARDIAN.app_id else AGENT)

    assert disable().is_error
    assert approvals.created == [] and disabled == []


def test_disable_agent_refuses_an_agent_that_is_not_dormant_and_asks_nobody(disable_setup, monkeypatch, capsys):
    disabled, use = disable_setup
    approvals = use(FakeApprovals("approved"))
    monkeypatch.setattr(server, "dormant_finding", lambda graph, agent, now: None)

    outcome = disable()

    assert outcome.is_error and "30 days" in outcome.content[0].text
    assert approvals.created == [] and disabled == []
    assert {"event": "disable_refused", "agent": AGENT.app_id, "reason": "not dormant"} in audit_lines(capsys)


def test_disable_agent_disables_only_after_an_approval_and_records_the_approver(disable_setup, capsys):
    disabled, use = disable_setup
    use(FakeApprovals("pending", "pending", "approved"))

    outcome = disable()

    assert not outcome.is_error
    assert disabled == [AGENT.app_id]
    assert outcome.structured_content["approved_by"] == "An Approver"
    lines = audit_lines(capsys)
    [decision] = [line for line in lines if line["event"] == "disable_decision"]
    assert (decision["decision"], decision["approver_id"], decision["approver_name"]) == ("approved", "approver-oid", "An Approver")
    # The order is the control: the decision is on record before the write.
    events = [line["event"] for line in lines]
    assert events.index("disable_requested") < events.index("disable_decision") < events.index("agent_disabled")


@pytest.mark.parametrize("status, text", [("denied", "denied"), ("expired", "in time"), ("unreachable", "reached")])
def test_disable_agent_disables_nothing_without_an_approval(status, text, disable_setup):
    disabled, use = disable_setup
    use(FakeApprovals("pending", status))

    outcome = disable()

    assert outcome.is_error and text in outcome.content[0].text
    assert disabled == []


def test_disable_agent_fails_closed_when_the_request_cannot_be_created(disable_setup, capsys):
    disabled, use = disable_setup
    class Down(FakeApprovals):
        def create(self, *args):
            raise OSError("connection refused")
    use(Down("approved"))

    outcome = disable()

    assert outcome.is_error and "reached" in outcome.content[0].text
    assert disabled == []
    assert {"event": "disable_refused", "agent": AGENT.app_id, "reason": "unavailable"} in audit_lines(capsys)


def test_the_write_is_on_record_with_the_approver(disable_setup, capsys):
    _, use = disable_setup
    use(FakeApprovals("approved"))

    disable()

    [line] = [line for line in audit_lines(capsys) if line["event"] == "agent_disabled"]
    assert (line["approver_id"], line["approver_name"], line["confirmed"]) == ("approver-oid", "An Approver", True)


def test_a_second_request_for_an_agent_gets_its_own_message(disable_setup, capsys):
    disabled, use = disable_setup
    class Waiting(FakeApprovals):
        def create(self, *args):
            raise ApprovalError("already_waiting")
    use(Waiting("approved"))

    outcome = disable()

    assert outcome.is_error and "already waiting" in outcome.content[0].text
    assert disabled == []
    assert {"event": "disable_refused", "agent": AGENT.app_id, "reason": "already_waiting"} in audit_lines(capsys)


def test_disable_agent_reports_and_audits_a_failed_write_after_an_approval(disable_setup, monkeypatch, capsys):
    _, use = disable_setup
    use(FakeApprovals("approved"))
    def write_fails(graph, app_id):
        raise OSError("HTTP Error 403: Forbidden")
    monkeypatch.setattr(server, "disable_agent_identity", write_fails)

    outcome = disable()

    assert outcome.is_error and "write to Microsoft Graph failed" in outcome.content[0].text
    assert "403" not in outcome.content[0].text   # no internal detail for the model (CWE-209)
    events = [line["event"] for line in audit_lines(capsys)]
    assert "disable_failed" in events and "agent_disabled" not in events


def test_disable_agent_gives_the_approver_facts_from_graph_and_cuts_the_models_reason(disable_setup):
    _, use = disable_setup
    approvals = use(FakeApprovals("denied"))

    call("disable_agent", {"app_id": AGENT.app_id, "reason": "x" * 5000})

    [(agent_id, reason, facts)] = approvals.created
    assert agent_id == AGENT.app_id and len(reason) == server.MAX_REASON
    assert facts["name"] == AGENT.name and facts["flaw"] == DORMANT.detail
