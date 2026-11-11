"""Unit tests for the executor (FR4, FR7, NFR8): the approver decides and lends their own rights.

No network: the sidecar, Graph and the Identity API are replaced. Every refusal must leave the
agent enabled, and only the OBO token may ever write.
"""

import asyncio
import json
import time
import urllib.error

import pytest
from fastapi.testclient import TestClient

import disable_checks
import executor
from approval import ApprovalError, DisableRequest
from graph_reader import AgentDetails
from rules import Finding

AGENT = AgentDetails("11111111-2222-3333-4444-555555555555", "agent-steward-fixture-orphan-01",
                     "66666666-7777-8888-9999-000000000000", "A fixture agent.")
GUARDIAN = AgentDetails("99999999-2222-3333-4444-555555555555", "agent-steward-guardian-01",
                        "aaaaaaaa-7777-8888-9999-000000000000", "")
DORMANT = Finding(AGENT.app_id, AGENT.name, "dormant", "no sign-in in the last 30 days")
REQUEST_ID = "12345678-aaaa-bbbb-cccc-1234567890ab"
APPROVER = "dddddddd-1111-2222-3333-444444444444"


def audit_lines(capsys) -> list[dict]:
    """The audit lines written so far, without their timestamps."""
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    return [{key: value for key, value in line.items() if key != "timestamp"} for line in lines]


class FakeApprovals:
    """The Identity API's requests, in memory. get answers with the request in its current state."""

    def __init__(self, status="approved", decided_by=APPROVER, execute_error=None, outcome_error=None):
        self.status, self.decided_by = status, decided_by
        self.execute_error, self.outcome_error = execute_error, outcome_error
        self.executed: list[tuple] = []
        self.outcomes: list[str] = []

    def request(self, agent_id):
        decided = (self.decided_by, "An Approver") if self.status not in ("pending", "expired") else (None, None)
        return DisableRequest(REQUEST_ID, agent_id, self.status, *decided, correlation_id="correlation-1",
                              expires_at="2026-11-11T06:00:00+00:00")

    def get(self, agent_id, request_id):
        if self.status == "unreachable":
            raise OSError("connection refused")
        return self.request(agent_id)

    def execute(self, agent_id, request_id, delegating_user):
        if self.execute_error:
            raise self.execute_error
        self.executed.append((agent_id, request_id, delegating_user))
        self.status = "running"
        return self.request(agent_id)

    def record_outcome(self, agent_id, request_id, outcome):
        if self.outcome_error:
            raise self.outcome_error
        self.outcomes.append(str(outcome))
        self.status = str(outcome)
        return self.request(agent_id)


@pytest.fixture
def setup(monkeypatch):
    """A dormant agent that is not the guardian's. Records every real disable, and fails the test if
    any token but the OBO token writes. The sidecar is replaced: validate_user_token returns
    claims["value"], and the OBO exchange returns "obo-token"."""
    monkeypatch.setenv("GUARDIAN_AGENT_APP_ID", GUARDIAN.app_id)
    monkeypatch.setattr(disable_checks, "fetch_agent_details",
                        lambda client, app_id: GUARDIAN if app_id == GUARDIAN.app_id else AGENT)
    monkeypatch.setattr(disable_checks, "dormant_finding", lambda graph, agent, now: DORMANT)
    claims = {"value": {"oid": APPROVER, "scp": "access_as_user"}}
    monkeypatch.setattr(executor, "validate_user_token", lambda tc: claims["value"])
    monkeypatch.setattr(executor, "obo_token", lambda tc: "obo-token")
    monkeypatch.setattr(executor, "token_for", lambda api: (lambda: f"app-only-{api}"))
    disabled = []
    def fake_disable(reader, writer, app_id):
        # Only the OBO token may write: the guardian's app-only token never does (decision 7). The
        # reads use the guardian's app-only token, because the OBO token cannot read agent identities.
        assert writer._get_token() == "obo-token"
        assert reader._get_token() == "app-only-graph"
        disabled.append(app_id)
        return True
    monkeypatch.setattr(executor, "disable_agent_identity", fake_disable)
    def use(approvals):
        monkeypatch.setattr(executor, "approval_service", lambda: approvals)
        return approvals
    return disabled, use, claims


def delegate(agent_id=AGENT.app_id, request_id=REQUEST_ID, tc="tc"):
    return asyncio.run(executor.accept_delegation(agent_id, request_id, tc))


def test_the_approvers_delegation_disables_the_agent_with_their_rights_once(setup, capsys):
    disabled, use, _ = setup
    approvals = use(FakeApprovals("approved"))

    assert delegate() == (200, "disabled")
    assert disabled == [AGENT.app_id]
    assert approvals.executed == [(AGENT.app_id, REQUEST_ID, APPROVER)]
    assert approvals.outcomes == ["succeeded"]   # the request now says what happened
    [line] = [line for line in audit_lines(capsys) if line["event"] == "agent_disabled"]
    assert (line["mode"], line["delegating_user"], line["approver_id"]) == ("obo", APPROVER, APPROVER)
    assert line["agent_app_id"] == GUARDIAN.app_id   # NFR2: the line names the user and the agent
    assert line["correlation_id"] == "correlation-1"
    # Used once: the request has been executed.
    assert delegate() == (409, "already_executed")
    assert disabled == [AGENT.app_id]


@pytest.mark.parametrize("claims, answer", [
    ({"oid": "someone-else", "scp": "access_as_user"}, (403, "not_the_approver")),
    ({"scp": "access_as_user"}, (401, "token_rejected")),
    ({"oid": APPROVER, "scp": "User.Read"}, (401, "token_rejected")),
    ({"oid": APPROVER}, (401, "token_rejected")),   # app-only tokens carry no scp
])
def test_a_token_that_is_not_the_approvers_delegated_token_disables_nothing(claims, answer, setup, capsys):
    disabled, use, token_claims = setup
    approvals = use(FakeApprovals("approved"))
    token_claims["value"] = claims

    assert delegate() == answer
    assert disabled == [] and approvals.executed == []
    assert "delegation_refused" in [line["event"] for line in audit_lines(capsys)]


def test_no_token_disables_nothing(setup):
    disabled, use, _ = setup
    use(FakeApprovals("approved"))

    assert delegate(tc=None) == (401, "no_token")
    assert disabled == []


def test_a_token_the_sidecar_rejects_disables_nothing(setup, monkeypatch):
    disabled, use, _ = setup
    use(FakeApprovals("approved"))
    def rejected(tc):
        raise OSError("HTTP Error 401: Unauthorized")
    monkeypatch.setattr(executor, "validate_user_token", rejected)

    assert delegate() == (401, "token_rejected")
    assert disabled == []


@pytest.mark.parametrize("status, answer", [("pending", (403, "not_approved")), ("denied", (403, "not_approved")),
                                            ("expired", (403, "not_approved")), ("running", (409, "already_executed")),
                                            ("succeeded", (409, "already_executed")),
                                            ("failed", (409, "already_executed")),
                                            ("unreachable", (503, "unavailable"))])
def test_a_request_that_is_not_approved_disables_nothing(status, answer, setup):
    disabled, use, _ = setup
    approvals = use(FakeApprovals(status))

    assert delegate() == answer
    assert disabled == [] and approvals.executed == []


@pytest.mark.parametrize("agent_id, request_id", [("not-a-guid", REQUEST_ID), (AGENT.app_id, "../decision")])
def test_a_delegation_with_ids_that_are_not_guids_is_refused_before_any_call(agent_id, request_id, setup):
    disabled, use, _ = setup
    class Untouchable(FakeApprovals):
        def get(self, *args):
            raise AssertionError("the Identity API was called with an unchecked ID")
    use(Untouchable("approved"))

    assert delegate(agent_id, request_id) == (404, "no_such_request")
    assert disabled == []


def test_an_agent_that_is_no_longer_dormant_is_not_disabled_after_the_approval(setup, monkeypatch):
    disabled, use, _ = setup
    approvals = use(FakeApprovals("approved"))
    monkeypatch.setattr(disable_checks, "dormant_finding", lambda graph, agent, now: None)

    assert delegate() == (409, "no_longer_allowed")
    assert disabled == [] and approvals.executed == []


def test_the_guardians_own_agent_is_not_disabled_even_after_an_approval(setup, monkeypatch):
    disabled, use, _ = setup
    approvals = use(FakeApprovals("approved"))
    sibling = AgentDetails(AGENT.app_id, "agent-steward-guardian-norole-01", GUARDIAN.blueprint_app_id, "")
    monkeypatch.setattr(disable_checks, "fetch_agent_details",
                        lambda client, app_id: GUARDIAN if app_id == GUARDIAN.app_id else sibling)

    assert delegate() == (409, "no_longer_allowed")
    assert disabled == [] and approvals.executed == []


def test_when_the_identity_api_refuses_the_claim_nothing_is_disabled(setup):
    disabled, use, _ = setup
    use(FakeApprovals("approved", execute_error=ApprovalError("not_executable")))

    assert delegate() == (409, "not_executable")
    assert disabled == []


def test_a_failed_obo_exchange_disables_nothing_and_is_on_record(setup, monkeypatch, capsys):
    disabled, use, _ = setup
    approvals = use(FakeApprovals("approved"))
    def exchange_fails(tc):
        raise OSError("HTTP Error 500: Internal Server Error")
    monkeypatch.setattr(executor, "obo_token", exchange_fails)

    assert delegate() == (502, "write_failed")
    assert disabled == []
    assert approvals.outcomes == ["failed"]   # not left as if it was carried out
    [line] = [line for line in audit_lines(capsys) if line["event"] == "disable_failed"]
    assert line["stage"] == "obo exchange"


def test_a_failed_write_is_reported_and_on_record(setup, monkeypatch, capsys):
    _, use, _ = setup
    approvals = use(FakeApprovals("approved"))
    def write_fails(reader, writer, app_id):
        raise urllib.error.HTTPError("https://graph.microsoft.com/beta/x", 403, "Forbidden", {}, None)
    monkeypatch.setattr(executor, "disable_agent_identity", write_fails)

    assert delegate() == (502, "write_failed")
    assert approvals.outcomes == ["failed"]
    lines = audit_lines(capsys)
    assert "agent_disabled" not in [line["event"] for line in lines]
    [line] = [line for line in lines if line["event"] == "disable_failed"]
    assert line["http_status"] == 403   # so a refused permission is visible without guessing


def test_a_write_that_is_abandoned_is_on_record_with_the_approver(monkeypatch, capsys):
    monkeypatch.setenv("GUARDIAN_AGENT_APP_ID", GUARDIAN.app_id)
    def slow_write(reader, writer, app_id):
        time.sleep(0.2)
        return True
    monkeypatch.setattr(executor, "disable_agent_identity", slow_write)
    approved = DisableRequest(REQUEST_ID, AGENT.app_id, "approved", APPROVER, "An Approver")
    async def run():
        task = asyncio.create_task(executor.write_disable(None, None, approved, "correlation-1"))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(run())

    [line] = [line for line in audit_lines(capsys) if line["event"] == "disable_abandoned"]
    assert (line["stage"], line["approver_id"]) == ("writing", APPROVER)


def test_the_delegation_route_reads_the_bearer_token_and_answers_without_detail(monkeypatch):
    seen = []
    async def fake_accept(agent_id, request_id, tc):
        seen.append((agent_id, request_id, tc))
        return 403, "not_the_approver"
    monkeypatch.setattr(executor, "accept_delegation", fake_accept)
    client = TestClient(executor.app)
    path = f"/agents/{AGENT.app_id}/disableRequests/{REQUEST_ID}/delegation"

    answer = client.post(path, headers={"Authorization": "Bearer the-tc"})
    client.post(path, headers={"Authorization": "Basic the-tc"})
    client.post(path)

    assert answer.status_code == 403 and answer.json() == {"result": "not_the_approver"}
    assert seen == [(AGENT.app_id, REQUEST_ID, "the-tc"), (AGENT.app_id, REQUEST_ID, None),
                    (AGENT.app_id, REQUEST_ID, None)]


def test_the_executor_offers_only_the_delegation_route():
    routes = {(route.path, method) for route in executor.app.routes for method in getattr(route, "methods", ())}
    assert routes == {("/agents/{agent_id}/disableRequests/{request_id}/delegation", "POST")}


def test_a_write_that_a_read_does_not_show_yet_still_succeeded(setup, monkeypatch):
    # Graph accepted the PATCH; the read-back is only slow (eventual consistency).
    _, use, _ = setup
    approvals = use(FakeApprovals("approved"))
    monkeypatch.setattr(executor, "disable_agent_identity", lambda reader, writer, app_id: False)

    assert delegate() == (200, "disabled")
    assert approvals.outcomes == ["succeeded"]


@pytest.mark.parametrize("error", [OSError("connection refused"), ApprovalError("not_running")])
def test_when_the_outcome_cannot_be_recorded_the_answer_is_unchanged_and_on_record(error, setup, capsys):
    disabled, use, _ = setup
    use(FakeApprovals("approved", outcome_error=error))

    assert delegate() == (200, "disabled")
    assert disabled == [AGENT.app_id]
    [line] = [line for line in audit_lines(capsys) if line["event"] == "outcome_not_recorded"]
    assert (line["outcome"], line["correlation_id"]) == ("succeeded", "correlation-1")
