"""Unit tests for the approval gate's waiting and its checks on the answer (FR7, NFR8). No network,
and the clock is fake, so the 4-minute limit takes no time."""

import asyncio
import io
import urllib.error
from datetime import datetime, timedelta, timezone

import pytest

from approval import (POLL_EVERY, WAIT_FOR_APPROVAL, ApprovalError, DisableRequest, IdentityApiApprovals, parse_request,
                      wait_for_decision)

CREATED = DisableRequest("request-1", "agent-1", "pending")
APPROVED = DisableRequest("request-1", "agent-1", "approved", "approver-oid", "An Approver")


class FakeClock:
    def __init__(self):
        self.time = datetime(2026, 10, 31, tzinfo=timezone.utc)

    def now(self):
        return self.time

    async def sleep(self, seconds):
        self.time += timedelta(seconds=seconds)


def wait(answers, used=None, clock=None):
    """Run the wait against scripted answers. The last answer repeats. An exception is raised."""
    clock = clock or FakeClock()
    answers = list(answers)
    async def poll():
        answer = answers.pop(0) if len(answers) > 1 else answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer
    return asyncio.run(wait_for_decision(poll, CREATED, set() if used is None else used, clock.now, clock.sleep))


def reason_of(answers, **kwargs):
    with pytest.raises(ApprovalError) as error:
        wait(answers, **kwargs)
    return error.value.reason


def test_an_approval_returns_the_request_with_its_approver():
    assert wait([CREATED, CREATED, APPROVED]) == APPROVED


def test_a_denial_stops_the_wait():
    assert reason_of([CREATED, DisableRequest("request-1", "agent-1", "denied", "approver-oid", "An Approver")]) == "denied"


def test_a_request_the_service_expired_stops_the_wait():
    assert reason_of([DisableRequest("request-1", "agent-1", "expired")]) == "expired"


def test_no_answer_in_time_fails_closed_at_four_minutes():
    clock = FakeClock()
    start = clock.time

    assert reason_of([CREATED], clock=clock) == "expired"
    assert WAIT_FOR_APPROVAL <= clock.time - start <= WAIT_FOR_APPROVAL + POLL_EVERY


@pytest.mark.parametrize("answer", [
    DisableRequest("request-2", "agent-1", "approved", "approver-oid", "An Approver"),   # another request
    DisableRequest("request-1", "agent-2", "approved", "approver-oid", "An Approver"),   # another agent
])
def test_an_approval_for_something_else_is_refused(answer):
    assert reason_of([answer]) == "mismatch"


def test_an_approval_works_only_once():
    used = set()
    assert wait([APPROVED], used=used) == APPROVED
    assert reason_of([APPROVED], used=used) == "reused"


@pytest.mark.parametrize("failure", [OSError("connection refused"), TimeoutError(), ValueError("not a disable request")])
def test_a_failed_poll_fails_closed(failure):
    assert reason_of([CREATED, failure]) == "unavailable"


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
        service.create("agent-1", "reason", {}, "correlation-1")
    assert error.value.reason == "already_waiting"


def test_any_other_refusal_on_create_stays_a_service_error(monkeypatch):
    service = creating_answers(monkeypatch, 403)

    with pytest.raises(OSError):
        service.create("agent-1", "reason", {}, "correlation-1")
