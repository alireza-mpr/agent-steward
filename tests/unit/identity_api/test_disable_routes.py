"""Unit tests for the Identity API's routes (FR7, FR8, NFR8): real tokens signed with a throwaway
key go through the real checks, so these also test that each route names the right check. The
person's tokens are built from the docs: no real one exists until the first live sign-in."""

import importlib.util
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from disable_requests import EXPIRES_AFTER, DisableRequestStore
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

TENANT = "00000000-0000-0000-0000-0000000000aa"
API = "00000000-0000-0000-0000-0000000000a1"
GUARDIAN = "00000000-0000-0000-0000-0000000000a2"
OTHER_AGENT = "00000000-0000-0000-0000-0000000000a3"
CLI = "00000000-0000-0000-0000-0000000000c1"
PERSON = "00000000-0000-0000-0000-0000000000d1"
TARGET = "00000000-0000-0000-0000-0000000000e1"
BODY = {"reason": "It is dormant.", "facts": {"name": "agent-steward-fixture-dormant-01", "blueprintAppId": "b",
                                              "flaw": "no sign-in in the last 30 days", "lastSignIn": None}}

# Loaded by path: the guardian also has a main.py on the test path.
with mock.patch.dict(os.environ, {"TENANT_ID": TENANT, "IDENTITY_API_APP_ID": API, "AGENT_DISABLE_CLI_APP_ID": CLI}):
    spec = importlib.util.spec_from_file_location("identity_api_main",
                                                  Path(__file__).parents[3] / "src" / "identity_api" / "main.py")
    api = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(api)

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def token(**changes) -> str:
    now = int(time.time())
    claims = {"iss": f"https://login.microsoftonline.com/{TENANT}/v2.0", "tid": TENANT, "aud": API, "iat": now - 60,
              "nbf": now - 60, "exp": now + 3600, **changes}
    return jwt.encode({name: value for name, value in claims.items() if value is not None}, KEY, algorithm="RS256")


def guardian(**changes) -> dict:
    return bearer(token(**{"azp": GUARDIAN, "oid": GUARDIAN, "idtyp": "app", "xms_act_fct": "11 9 3",
                           "roles": ["AgentDisable.Request"], "xms_par_app_azp": "blueprint", **changes}))


def person(**changes) -> dict:
    return bearer(token(**{"azp": CLI, "oid": PERSON, "name": "An Approver", "idtyp": "user", "xms_act_fct": "3 9",
                           "xms_sub_fct": "1 2", "scp": "AgentDisable.Decide", "roles": ["AgentDisable.Approve"],
                           **changes}))


def bearer(value: str) -> dict:
    return {"Authorization": f"Bearer {value}"}


class Clock:
    def __init__(self):
        self.time = api.datetime.now(api.timezone.utc)

    def __call__(self):
        return self.time


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def client(monkeypatch, clock):
    monkeypatch.setattr(api, "SIGNING_KEYS", SimpleNamespace(
        get_signing_key_from_jwt=lambda value: SimpleNamespace(key=KEY.public_key())))
    monkeypatch.setattr(api, "STORE", DisableRequestStore(clock, api.audit_expired))
    return TestClient(api.app)


def create(client, agent=TARGET, headers=None):
    return client.post(f"/agents/{agent}/disableRequests", json=BODY,
                       headers=headers or guardian())


def decide(client, request_id, decision="approve", headers=None, agent=TARGET):
    return client.post(f"/agents/{agent}/disableRequests/{request_id}/decision", json={"decision": decision},
                       headers=headers or person())


def audit_lines(capsys, event) -> list[dict]:
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    return [line for line in lines if line.get("event") == event]


# The whole path

def test_the_whole_path_from_request_to_decision(client):
    created = create(client)
    assert created.status_code == 201
    request_id = created.json()["id"]
    assert created.json()["status"] == "pending" and created.json()["decidedBy"] is None

    listed = client.get("/disableRequests?status=pending", headers=person())
    assert [item["id"] for item in listed.json()["value"]] == [request_id]
    assert listed.json()["value"][0]["facts"]["name"] == "agent-steward-fixture-dormant-01"

    assert decide(client, request_id).status_code == 200

    read = client.get(f"/agents/{TARGET}/disableRequests/{request_id}", headers=guardian())
    assert read.json()["status"] == "approved"
    assert read.json()["decidedBy"] == {"id": PERSON, "name": "An Approver"}
    assert client.get("/disableRequests?status=pending", headers=person()).json() == {"value": []}


def test_a_denial_is_recorded_too(client):
    request_id = create(client).json()["id"]

    assert decide(client, request_id, "deny").json()["status"] == "denied"


# Who may call what

@pytest.mark.parametrize("call", [
    lambda client, headers: client.post(f"/agents/{TARGET}/disableRequests", json=BODY, headers=headers),
    lambda client, headers: client.get(f"/agents/{TARGET}/disableRequests/some-id", headers=headers),
    lambda client, headers: client.get("/disableRequests?status=pending", headers=headers),
    lambda client, headers: client.post(f"/agents/{TARGET}/disableRequests/some-id/decision",
                                        json={"decision": "approve"}, headers=headers),
])
def test_every_route_needs_a_token(client, call):
    assert call(client, {}).status_code == 401


def test_a_person_cannot_create_or_read_a_request(client):
    assert create(client, headers=person()).status_code == 403
    request_id = create(client).json()["id"]
    assert client.get(f"/agents/{TARGET}/disableRequests/{request_id}", headers=person()).status_code == 403


def test_the_agent_that_asks_cannot_approve_or_list(client):
    request_id = create(client).json()["id"]

    assert decide(client, request_id, headers=guardian()).status_code == 403
    assert client.get("/disableRequests?status=pending", headers=guardian()).status_code == 403
    # Not even with the approver's role and scope on its own token.
    forged = guardian(roles=["AgentDisable.Approve"], scp="AgentDisable.Decide", azp=CLI)
    assert decide(client, request_id, headers=forged).status_code == 403
    assert client.get(f"/agents/{TARGET}/disableRequests/{request_id}", headers=guardian()).json()["status"] == "pending"


@pytest.mark.parametrize("changes", [{"roles": []}, {"scp": None}, {"azp": GUARDIAN}])
def test_a_person_without_the_role_or_scope_or_the_right_app_cannot_decide(client, changes):
    request_id = create(client).json()["id"]

    assert decide(client, request_id, headers=person(**changes)).status_code == 403


def test_an_agent_without_the_request_role_cannot_create(client):
    assert create(client, headers=guardian(roles=["Findings.Submit"])).status_code == 403


def test_findings_still_need_their_own_role(client):
    report = {"findings": []}
    assert client.post("/findings", json=report, headers=guardian()).status_code == 403
    assert client.post("/findings", json=report, headers=guardian(roles=["Findings.Submit"])).status_code == 202


def test_a_refused_decision_is_audited_as_a_rejected_request(client, capsys):
    request_id = create(client).json()["id"]
    capsys.readouterr()

    decide(client, request_id, headers=guardian())

    [line] = audit_lines(capsys, "request_rejected")
    assert (line["status"], line["reason"]) == (403, "approver: idtyp claim is not user")


# The rules inside the routes

def test_a_second_pending_request_for_one_agent_gets_409_and_is_audited(client, capsys):
    create(client)
    assert create(client).status_code == 409

    [line] = audit_lines(capsys, "disable_request_refused")
    assert (line["target_agent"], line["reason"]) == (TARGET, "already pending")


def test_a_second_decision_gets_409_and_the_first_stands(client, capsys):
    request_id = create(client).json()["id"]
    decide(client, request_id, "deny")

    assert decide(client, request_id, "approve").status_code == 409
    assert client.get(f"/agents/{TARGET}/disableRequests/{request_id}", headers=guardian()).json()["status"] == "denied"
    [line] = audit_lines(capsys, "disable_decision_refused")
    assert (line["approver_id"], line["decision"]) == (PERSON, "approved")


def test_a_late_decision_gets_409_and_the_expiry_is_audited(client, clock, capsys):
    request_id = create(client).json()["id"]
    clock.time += EXPIRES_AFTER

    assert decide(client, request_id).status_code == 409
    assert client.get(f"/agents/{TARGET}/disableRequests/{request_id}", headers=guardian()).json()["status"] == "expired"
    [line] = audit_lines(capsys, "disable_request_expired")
    assert line["request_id"] == request_id


def test_a_request_is_not_reachable_through_another_agents_path(client):
    request_id = create(client).json()["id"]
    other = "00000000-0000-0000-0000-0000000000e2"

    assert client.get(f"/agents/{other}/disableRequests/{request_id}", headers=guardian()).status_code == 404
    assert decide(client, request_id, agent=other).status_code == 404


def test_only_the_agent_that_asked_can_read_its_request(client):
    request_id = create(client).json()["id"]

    outcome = client.get(f"/agents/{TARGET}/disableRequests/{request_id}", headers=guardian(azp=OTHER_AGENT))

    unknown = client.get(f"/agents/{TARGET}/disableRequests/no-such-id", headers=guardian(azp=OTHER_AGENT))
    assert outcome.status_code == unknown.status_code == 404
    assert outcome.json() == unknown.json()   # it cannot tell that the request exists


def test_an_unknown_request_gets_404(client):
    assert decide(client, "no-such-id").status_code == 404


def test_the_list_accepts_only_pending(client):
    assert client.get("/disableRequests?status=approved", headers=person()).status_code == 400


# The input

@pytest.mark.parametrize("agent", ["not-a-guid", "x'%20or%20'1"])
def test_create_needs_a_guid(client, agent):
    assert create(client, agent=agent).status_code == 422


@pytest.mark.parametrize("change", [
    {"reason": "x" * 501},
    {"extra": "field"},
    {"facts": {**BODY["facts"], "extra": "field"}},
    {"facts": {**BODY["facts"], "name": "x" * 257}},
    {"facts": {"name": "only a name"}},
])
def test_create_refuses_a_body_out_of_its_limits(client, change):
    outcome = client.post(f"/agents/{TARGET}/disableRequests", json={**BODY, **change}, headers=guardian())

    assert outcome.status_code == 422


@pytest.mark.parametrize("body", [{"decision": "APPROVE"}, {"decision": "yes"}, {},
                                  {"decision": "approve", "approver": "someone else"}])
def test_a_decision_is_approve_or_deny_and_the_body_cannot_name_the_approver(client, body):
    request_id = create(client).json()["id"]

    outcome = client.post(f"/agents/{TARGET}/disableRequests/{request_id}/decision", json=body, headers=person())

    assert outcome.status_code == 422
    assert client.get(f"/agents/{TARGET}/disableRequests/{request_id}", headers=guardian()).json()["status"] == "pending"


# The audit lines

def test_the_created_line_holds_what_the_approver_will_see(client, capsys):
    create(client)

    [line] = audit_lines(capsys, "disable_request_created")
    assert line["request_id"] and line["target_agent"] == TARGET
    assert line["agent_app_id"] == GUARDIAN and line["blueprint_app_id"] == "blueprint"
    assert line["untrusted_reason"] == "It is dormant."
    assert line["facts"]["flaw"] == "no sign-in in the last 30 days"


def test_the_decided_line_names_the_approver_and_the_request(client, capsys):
    request_id = create(client).json()["id"]

    client.post(f"/agents/{TARGET}/disableRequests/{request_id}/decision", json={"decision": "approve"},
                headers=person())

    [line] = audit_lines(capsys, "disable_request_decided")
    assert (line["request_id"], line["decision"]) == (request_id, "approved")
    assert (line["approver_id"], line["approver_name"], line["client_app_id"]) == (PERSON, "An Approver", CLI)


def test_no_audit_line_holds_a_token(client, capsys):
    headers = guardian()
    request_id = create(client, headers=headers).json()["id"]
    decide(client, request_id)
    decide(client, request_id, headers=guardian())

    assert headers["Authorization"].split()[1] not in capsys.readouterr().out


# Execution (FR4) and the requester's list

def approve_one(client):
    request_id = create(client).json()["id"]
    decide(client, request_id)
    return request_id


def execute(client, request_id, user=PERSON, headers=None, agent=TARGET):
    return client.post(f"/agents/{agent}/disableRequests/{request_id}/execution", json={"delegatingUser": user},
                       headers=headers or guardian())


def test_the_agent_that_asked_executes_an_approved_request_once(client, capsys):
    request_id = approve_one(client)

    first, second = execute(client, request_id), execute(client, request_id)

    assert first.status_code == 200 and first.json()["status"] == "running"
    assert second.status_code == 409
    [line] = audit_lines(capsys, "disable_request_executed")
    assert (line["mode"], line["delegating_user"], line["approver_id"]) == ("obo", PERSON, PERSON)


def test_execution_for_someone_other_than_the_approver_is_refused_and_audited(client, capsys):
    request_id = approve_one(client)

    answer = execute(client, request_id, user="00000000-0000-0000-0000-0000000000d2")

    assert answer.status_code == 409 and "not the approver" in answer.json()["detail"]
    [line] = audit_lines(capsys, "disable_execution_refused")
    assert line["reason"] == "not the approver"


def test_a_pending_request_cannot_be_executed(client):
    request_id = create(client).json()["id"]

    assert execute(client, request_id).status_code == 409


def test_a_person_or_another_agent_cannot_execute(client):
    request_id = approve_one(client)

    assert execute(client, request_id, headers=person()).status_code in (401, 403)
    assert execute(client, request_id, headers=guardian(azp=OTHER_AGENT, oid=OTHER_AGENT)).status_code == 404


def test_execution_needs_a_delegating_user_that_is_an_object_id(client):
    request_id = approve_one(client)

    assert execute(client, request_id, user="not-an-oid").status_code == 422


def test_the_requester_lists_its_own_requests_for_one_agent(client):
    request_id = approve_one(client)

    mine = client.get(f"/agents/{TARGET}/disableRequests", headers=guardian())
    theirs = client.get(f"/agents/{TARGET}/disableRequests", headers=guardian(azp=OTHER_AGENT, oid=OTHER_AGENT))

    assert [(item["id"], item["status"]) for item in mine.json()["value"]] == [(request_id, "approved")]
    assert mine.json()["value"][0]["decidedAt"] is not None
    assert theirs.json()["value"] == []
    assert client.get(f"/agents/{TARGET}/disableRequests", headers=person()).status_code in (401, 403)


# The outcome: the executor records what its write did.

def record(client, request_id, outcome="succeeded", headers=None, agent=TARGET):
    return client.post(f"/agents/{agent}/disableRequests/{request_id}/outcome", json={"outcome": outcome},
                       headers=headers or guardian())


@pytest.mark.parametrize("outcome", ["succeeded", "failed"])
def test_the_agent_that_asked_records_the_outcome_once_and_it_is_audited(outcome, client, capsys):
    request_id = approve_one(client)
    execute(client, request_id)

    first, second = record(client, request_id, outcome), record(client, request_id, outcome)

    assert first.status_code == 200 and first.json()["status"] == outcome and first.json()["finishedAt"]
    assert second.status_code == 409
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    [line] = [line for line in lines if line["event"] == "disable_request_finished"]
    assert (line["outcome"], line["request_id"], line["approver_id"]) == (outcome, request_id, PERSON)
    [refused] = [line for line in lines if line["event"] == "disable_outcome_refused"]
    assert refused["status"] == outcome


def test_an_approved_request_that_was_not_claimed_has_no_outcome(client):
    request_id = approve_one(client)

    assert record(client, request_id).status_code == 409


def test_an_outcome_must_be_succeeded_or_failed_on_the_wire(client):
    request_id = approve_one(client)
    execute(client, request_id)

    assert record(client, request_id, "executed").status_code == 422
    assert record(client, request_id, "approved").status_code == 422


def test_a_person_or_another_agent_cannot_record_an_outcome(client):
    request_id = approve_one(client)
    execute(client, request_id)

    assert record(client, request_id, headers=person()).status_code in (401, 403)
    assert record(client, request_id, headers=guardian(azp=OTHER_AGENT, oid=OTHER_AGENT)).status_code == 404


def test_the_scan_sees_a_failed_request_and_the_agent_can_ask_again(client):
    request_id = approve_one(client)
    execute(client, request_id)
    record(client, request_id, "failed")

    again = create(client)
    listed = client.get(f"/agents/{TARGET}/disableRequests", headers=guardian()).json()["value"]

    assert again.status_code == 201
    assert sorted(item["status"] for item in listed) == ["failed", "pending"]


# Tracing (NFR4): the trace ID replaces the old correlation ID

RUN_TRACE = "0af7651916cd43dd8448eb211c80319c"   # the guardian's run, as the MCP server passes it on
TRACEPARENT = {"traceparent": f"00-{RUN_TRACE}-b7ad6b7169203331-01"}


@pytest.fixture
def traced(client):
    """The app with the FastAPI instrumentation, as in compose, and its spans kept in memory."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    FastAPIInstrumentor.instrument_app(api.app, tracer_provider=provider, exclude_spans=["receive", "send"])
    api.app.middleware_stack = None   # earlier tests built it already: build it again, with the instrumentation
    yield TestClient(api.app), exporter
    FastAPIInstrumentor.uninstrument_app(api.app)
    api.app.middleware_stack = None


def test_the_callers_traceparent_becomes_the_audit_lines_trace_id(traced, capsys):
    client, spans = traced

    created = create(client, headers={**guardian(), **TRACEPARENT}).json()

    [line] = audit_lines(capsys, "disable_request_created")
    assert line["trace_id"] == RUN_TRACE   # the same ID as the MCP server's lines and the run's spans
    # The request keeps the span that created it, so the approval's trace can link to it.
    [server_span] = [span for span in spans.get_finished_spans() if span.kind.name == "SERVER"]
    assert (created["traceId"], created["spanId"]) == (RUN_TRACE, line["span_id"])
    assert line["span_id"] == f"{server_span.context.span_id:016x}"


def test_the_decision_is_in_the_approvals_own_trace_and_the_request_id_ties_the_two(traced, capsys):
    client, _ = traced
    request_id = create(client, headers={**guardian(), **TRACEPARENT}).json()["id"]
    approval_trace = "4bf92f3577b34da6a3ce929d0e0e4736"

    client.post(f"/agents/{TARGET}/disableRequests/{request_id}/decision", json={"decision": "approve"},
                headers={**person(), "traceparent": f"00-{approval_trace}-00f067aa0ba902b7-01"})

    created, decided = audit_lines_of(capsys, "disable_request_created", "disable_request_decided")
    assert (created["trace_id"], decided["trace_id"]) == (RUN_TRACE, approval_trace)
    assert created["request_id"] == decided["request_id"] == request_id


def test_without_the_sdk_no_line_carries_a_trace_id(client, capsys):
    create(client)

    [line] = audit_lines(capsys, "disable_request_created")
    assert "trace_id" not in line


def audit_lines_of(capsys, *events) -> list[dict]:
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    return [next(line for line in lines if line.get("event") == event) for event in events]
