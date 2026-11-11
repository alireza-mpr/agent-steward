"""Unit tests for the MCP server's tools and its tool allow-list (FR6, NFR8), through a real MCP
client in memory.

No network: Graph and the Identity API are replaced where a test needs them.
"""

import asyncio
import json
from dataclasses import replace
from pathlib import Path

import pytest
from mcp import Client, MCPError
from mcp.types import INVALID_PARAMS

import disable_checks
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
    assert set(list_tools()) == {"scan_registry", "get_agent", "submit_disable_agent_request"}


def test_scan_registry_is_not_marked_read_only_because_it_writes_the_audit_log():
    # docs/decisions.md, ADR-001: the tool sends the findings to the Identity API itself.
    tools = list_tools()
    assert tools["scan_registry"].annotations.read_only_hint is False
    assert tools["get_agent"].annotations.read_only_hint is True


def test_the_request_tool_only_asks_so_it_is_not_marked_destructive():
    # It creates a request and returns. The write needs an approver's own token (decision 7).
    annotations = list_tools()["submit_disable_agent_request"].annotations
    assert annotations.read_only_hint is False and annotations.destructive_hint is False


@pytest.mark.parametrize("app_id", ["not-a-guid", "x' or appId ne 'x", AGENT.app_id + "/../owners", ""])
def test_get_agent_rejects_an_input_that_is_not_a_guid_before_calling_graph(app_id, monkeypatch):
    def must_not_run(*args):
        raise AssertionError("Graph was called with an unchecked input")
    set_agents(monkeypatch, must_not_run)

    outcome = call("get_agent", {"app_id": app_id})

    assert outcome.is_error
    assert "GUID" in outcome.content[0].text


def test_get_agent_returns_the_agent_with_its_description(monkeypatch):
    set_agents(monkeypatch, lambda client, app_id: AGENT)

    outcome = call("get_agent", {"app_id": AGENT.app_id})

    assert not outcome.is_error
    assert outcome.structured_content["description"] == "A fixture agent."


def test_get_agent_reports_an_unknown_agent_as_a_tool_error(monkeypatch):
    set_agents(monkeypatch, lambda client, app_id: None)

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
    set_agents(monkeypatch, must_not_run)

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
    set_agents(monkeypatch, lambda client, app_id: AGENT)

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


# submit_disable_agent_request (FR7): the agent proposes. Graph and the approval service are
# replaced: nothing here can disable anything.

GUARDIAN = AgentDetails("99999999-2222-3333-4444-555555555555", "agent-steward-guardian-01",
                        "aaaaaaaa-7777-8888-9999-000000000000", "")
DORMANT = Finding(AGENT.app_id, AGENT.name, "dormant", "no sign-in in the last 30 days")
REQUEST_ID = "12345678-aaaa-bbbb-cccc-1234567890ab"
APPROVER = "dddddddd-1111-2222-3333-444444444444"


class FakeApprovals:
    """The Identity API's requests, in memory. get answers with the request in its current state."""

    def __init__(self, status="pending", decided_by=APPROVER, create_error=None, execute_error=None):
        self.status, self.decided_by = status, decided_by
        self.create_error, self.execute_error = create_error, execute_error
        self.created: list[tuple] = []
        self.executed: list[tuple] = []

    def request(self, agent_id):
        decided = (self.decided_by, "An Approver") if self.status not in ("pending", "expired") else (None, None)
        return DisableRequest(REQUEST_ID, agent_id, self.status, *decided, correlation_id="correlation-1",
                              expires_at="2026-11-11T06:00:00+00:00")

    def create(self, agent_id, reason, facts, correlation_id):
        if self.create_error:
            raise self.create_error
        self.created.append((agent_id, reason, facts))
        return self.request(agent_id)

    def get(self, agent_id, request_id):
        if self.status == "unreachable":
            raise OSError("connection refused")
        return self.request(agent_id)

    def of_agent(self, agent_id):
        return [self.request(agent_id)]

    def execute(self, agent_id, request_id, delegating_user):
        if self.execute_error:
            raise self.execute_error
        self.executed.append((agent_id, request_id, delegating_user))
        self.status = "running"
        return self.request(agent_id)


def set_agents(monkeypatch, read):
    """Graph's answer for one agent, for both readers: get_agent's and the shared disable checks'."""
    monkeypatch.setattr(server, "fetch_agent_details", read)
    monkeypatch.setattr(disable_checks, "fetch_agent_details", read)


@pytest.fixture
def disable_setup(monkeypatch):
    """A dormant agent that is not the guardian's. Returns use(approvals), which puts in a fake
    approval service. The MCP server holds no write code, so nothing here can disable anything."""
    monkeypatch.setenv("GUARDIAN_AGENT_APP_ID", GUARDIAN.app_id)
    set_agents(monkeypatch, lambda client, app_id: GUARDIAN if app_id == GUARDIAN.app_id else AGENT)
    monkeypatch.setattr(disable_checks, "dormant_finding", lambda graph, agent, now: DORMANT)
    def use(approvals):
        monkeypatch.setattr(server, "approval_service", lambda: approvals)
        return approvals
    return use


def submit(app_id=AGENT.app_id, reason="It is dormant."):
    return call("submit_disable_agent_request", {"app_id": app_id, "reason": reason})


@pytest.mark.parametrize("app_id", ["not-a-guid", "x' or appId ne 'x", AGENT.app_id + "/../owners", ""])
def test_the_request_tool_rejects_an_input_that_is_not_a_guid_before_calling_graph(app_id, monkeypatch):
    def must_not_run(*args):
        raise AssertionError("Graph was called with an unchecked input")
    set_agents(monkeypatch, must_not_run)

    assert submit(app_id).is_error


def test_a_request_returns_pending_at_once_and_disables_nothing(disable_setup, capsys):
    use = disable_setup
    approvals = use(FakeApprovals("pending"))

    outcome = submit()

    assert not outcome.is_error
    assert (outcome.structured_content["status"], outcome.structured_content["request_id"]) == ("pending", REQUEST_ID)
    assert approvals.executed == []
    assert "disable_requested" in [line["event"] for line in audit_lines(capsys)]


def test_the_request_tool_reports_an_unknown_agent_as_a_tool_error(disable_setup, monkeypatch):
    use = disable_setup
    approvals = use(FakeApprovals())
    set_agents(monkeypatch, lambda client, app_id: None)

    assert submit().is_error
    assert approvals.created == []


@pytest.mark.parametrize("target", [GUARDIAN, AgentDetails("bbbbbbbb-2222-3333-4444-555555555555",
                                                           "agent-steward-guardian-norole-01",
                                                           GUARDIAN.blueprint_app_id, "")])
def test_the_request_tool_refuses_the_guardians_own_agents_and_asks_nobody(target, disable_setup, monkeypatch, capsys):
    use = disable_setup
    approvals = use(FakeApprovals())
    set_agents(monkeypatch, lambda client, app_id: GUARDIAN if app_id == GUARDIAN.app_id else target)

    outcome = submit(target.app_id)

    assert outcome.is_error and "guardian" in outcome.content[0].text
    assert approvals.created == []
    assert {"event": "disable_refused", "agent": target.app_id, "reason": "protected"} in audit_lines(capsys)


def test_the_request_tool_refuses_everything_when_the_guardian_cannot_be_read(disable_setup, monkeypatch):
    use = disable_setup
    approvals = use(FakeApprovals())
    set_agents(monkeypatch, lambda client, app_id: None if app_id == GUARDIAN.app_id else AGENT)

    assert submit().is_error
    assert approvals.created == []


def test_the_request_tool_refuses_an_agent_that_is_already_disabled_and_asks_nobody(disable_setup, monkeypatch, capsys):
    use = disable_setup
    approvals = use(FakeApprovals())
    disabled = replace(AGENT, enabled=False)
    set_agents(monkeypatch, lambda client, app_id: GUARDIAN if app_id == GUARDIAN.app_id else disabled)

    outcome = submit()

    assert outcome.is_error and "already disabled" in outcome.content[0].text
    assert approvals.created == []
    assert {"event": "disable_refused", "agent": AGENT.app_id, "reason": "already disabled"} in audit_lines(capsys)


def test_the_request_tool_refuses_an_agent_that_is_not_dormant_and_asks_nobody(disable_setup, monkeypatch, capsys):
    use = disable_setup
    approvals = use(FakeApprovals())
    monkeypatch.setattr(disable_checks, "dormant_finding", lambda graph, agent, now: None)

    outcome = submit()

    assert outcome.is_error and "30 days" in outcome.content[0].text
    assert approvals.created == []
    assert {"event": "disable_refused", "agent": AGENT.app_id, "reason": "not dormant"} in audit_lines(capsys)


def test_a_second_request_while_one_is_pending_is_refused_by_the_identity_api(disable_setup, capsys):
    use = disable_setup
    use(FakeApprovals(create_error=ApprovalError("already_waiting")))

    outcome = submit()

    assert outcome.is_error and "already pending" in outcome.content[0].text
    assert {"event": "disable_refused", "agent": AGENT.app_id, "reason": "already_waiting"} in audit_lines(capsys)


def test_the_request_tool_fails_closed_when_the_request_cannot_be_created(disable_setup, capsys):
    use = disable_setup
    use(FakeApprovals(create_error=OSError("connection refused")))

    outcome = submit()

    assert outcome.is_error and "reached" in outcome.content[0].text
    assert {"event": "disable_refused", "agent": AGENT.app_id, "reason": "unavailable"} in audit_lines(capsys)


def test_the_approver_gets_facts_from_graph_and_the_models_reason_is_cut(disable_setup):
    use = disable_setup
    approvals = use(FakeApprovals())

    submit(reason="x" * 5000)

    [(agent_id, reason, facts)] = approvals.created
    assert agent_id == AGENT.app_id and len(reason) == server.MAX_REASON
    assert facts["name"] == AGENT.name and facts["flaw"] == DORMANT.detail


# The scan shows each dormant agent's latest request, so the model can see what is already asked.

def test_the_scan_adds_the_latest_request_of_each_dormant_agent(monkeypatch):
    approvals = FakeApprovals("pending")
    monkeypatch.setattr(server, "approval_service", lambda: approvals)
    findings = [DORMANT, Finding("eeeeeeee-2222-3333-4444-555555555555", "x", "orphan", "no sponsor")]

    [latest] = server.latest_requests(findings)

    assert (latest.agent_app_id, latest.request_id, latest.status) == (AGENT.app_id, REQUEST_ID, "pending")


def test_a_dormant_agent_with_no_request_has_no_entry(monkeypatch):
    class Empty(FakeApprovals):
        def of_agent(self, agent_id):
            return []
    monkeypatch.setattr(server, "approval_service", lambda: Empty())

    assert server.latest_requests([DORMANT]) == []


# The write is not here (FR4): the MCP server has no write code and no door for the approver's token.

def test_the_mcp_server_holds_no_write_and_takes_no_approver_token():
    for name in ("accept_delegation", "write_disable", "obo_token", "validate_user_token", "disable_agent_identity"):
        assert not hasattr(server, name), name
    import graph_reader
    assert not hasattr(graph_reader, "disable_agent_identity")
    assert not hasattr(graph_reader.GraphClient, "patch")
