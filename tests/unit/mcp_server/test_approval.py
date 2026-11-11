"""Unit tests for the approval gate's client (FR7, FR4, NFR8): its reading of the Identity API's
answers, and its errors. No network."""

import io
import json
import urllib.error

import pytest

from approval import ApprovalError, DisableRequest, IdentityApiApprovals, Status, parse_request

CREATED = DisableRequest("request-1", "agent-1", "pending")
APPROVED = DisableRequest("request-1", "agent-1", "approved", "approver-oid", "An Approver")


def test_parse_request_reads_the_contract():
    body = {"id": "request-1", "agentId": "agent-1", "status": "approved",
            "decidedBy": {"id": "approver-oid", "name": "An Approver"}}

    assert parse_request(body) == APPROVED


def test_parse_request_reads_a_pending_request_with_no_approver():
    assert parse_request({"id": "request-1", "agentId": "agent-1", "status": "pending", "decidedBy": None}) == CREATED


@pytest.mark.parametrize("body", [
    [],
    {"id": "request-1", "agentId": "agent-1"},                                                # no status
    {"id": "request-1", "agentId": "agent-1", "status": "APPROVED"},                          # unknown status
    {"agentId": "agent-1", "status": "approved"},                                             # no ID
    {"id": "request-1", "status": "approved"},                                                # no agent
    {"id": "request-1", "agentId": "agent-1", "status": "approved", "decidedBy": "someone"},  # wrong shape
])
def test_parse_request_refuses_anything_else(body):
    with pytest.raises(ValueError):
        parse_request(body)


# Creating a request

def creating_answers(monkeypatch, code):
    def fail(request, timeout):
        raise urllib.error.HTTPError(request.full_url, code, "refused", {}, io.BytesIO())
    monkeypatch.setattr("approval.urllib.request.urlopen", fail)
    return IdentityApiApprovals("http://api", lambda: "token")


def test_a_409_on_create_means_a_request_is_already_waiting(monkeypatch):
    service = creating_answers(monkeypatch, 409)

    with pytest.raises(ApprovalError) as error:
        service.create("agent-1", "reason", {})
    assert error.value.reason == "already_waiting"


def test_any_other_refusal_on_create_stays_a_service_error(monkeypatch):
    service = creating_answers(monkeypatch, 403)

    with pytest.raises(OSError):
        service.create("agent-1", "reason", {})


# Listing and executing

class Answer(io.BytesIO):
    def __enter__(self):
        return self
    def __exit__(self, *arguments):
        return False


def answering(monkeypatch, body):
    sent = []
    def reply(request, timeout):
        sent.append(request)
        return Answer(json.dumps(body).encode())
    monkeypatch.setattr("approval.urllib.request.urlopen", reply)
    return IdentityApiApprovals("http://api", lambda: "token"), sent


def test_of_agent_reads_the_list_newest_first(monkeypatch):
    service, sent = answering(monkeypatch, {"value": [
        {"id": "request-2", "agentId": "agent-1", "status": "pending", "createdAt": "2026-11-10T06:00:00+00:00"},
        {"id": "request-1", "agentId": "agent-1", "status": "denied", "decidedBy": {"id": "a", "name": "An Approver"}}]})

    found = service.of_agent("agent-1")

    assert [(request.request_id, request.status) for request in found] == [("request-2", "pending"), ("request-1", "denied")]
    assert sent[0].full_url == "http://api/agents/agent-1/disableRequests" and sent[0].get_method() == "GET"


def test_of_agent_refuses_an_answer_that_is_not_a_list(monkeypatch):
    service, _ = answering(monkeypatch, {"value": "nope"})

    with pytest.raises(ValueError):
        service.of_agent("agent-1")


def test_execute_sends_the_delegating_user(monkeypatch):
    service, sent = answering(monkeypatch, {"id": "request-1", "agentId": "agent-1", "status": "running",
                                            "decidedBy": {"id": "approver-oid", "name": "An Approver"}})

    assert service.execute("agent-1", "request-1", "approver-oid").status == Status.RUNNING
    assert sent[0].full_url == "http://api/agents/agent-1/disableRequests/request-1/execution"
    assert json.loads(sent[0].data) == {"delegatingUser": "approver-oid"}


def test_a_409_on_execute_means_the_request_cannot_be_executed(monkeypatch):
    service = creating_answers(monkeypatch, 409)

    with pytest.raises(ApprovalError) as error:
        service.execute("agent-1", "request-1", "approver-oid")
    assert error.value.reason == "not_executable"


@pytest.mark.parametrize("outcome", [Status.SUCCEEDED, Status.FAILED])
def test_record_outcome_sends_the_outcome(outcome, monkeypatch):
    service, sent = answering(monkeypatch, {"id": "request-1", "agentId": "agent-1", "status": str(outcome)})

    assert service.record_outcome("agent-1", "request-1", outcome).status == outcome
    assert sent[0].full_url == "http://api/agents/agent-1/disableRequests/request-1/outcome"
    assert sent[0].get_method() == "POST" and json.loads(sent[0].data) == {"outcome": str(outcome)}


def test_a_409_on_record_outcome_means_the_request_is_not_running(monkeypatch):
    service = creating_answers(monkeypatch, 409)

    with pytest.raises(ApprovalError) as error:
        service.record_outcome("agent-1", "request-1", Status.SUCCEEDED)
    assert error.value.reason == "not_running"


def test_the_old_executed_status_is_not_a_disable_request():
    with pytest.raises(ValueError):
        parse_request({"id": "request-1", "agentId": "agent-1", "status": "executed"})
