"""Unit tests for the store of disable requests (FR7, NFR8). The clock is fake, so expiry takes no time."""

import threading
from datetime import datetime, timedelta, timezone

import pytest

from disable_requests import EXPIRES_AFTER, AlreadyFinal, AlreadyPending, DisableRequestStore, NotFound


class Clock:
    def __init__(self):
        self.time = datetime(2026, 10, 31, tzinfo=timezone.utc)

    def __call__(self):
        return self.time


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def store(clock):
    return DisableRequestStore(clock)


def create(store, agent="agent-1"):
    return store.create(agent, "guardian", "It is dormant.", {"name": agent}, "correlation-1")


def test_a_new_request_is_pending_with_its_own_id_and_expiry(store, clock):
    first, second = create(store, "agent-1"), create(store, "agent-2")

    assert first.status == "pending" and first.id != second.id
    assert first.expires_at == clock.time + EXPIRES_AFTER
    assert first.decided_by_id is None


def test_a_second_pending_request_for_one_agent_is_refused(store):
    create(store)

    with pytest.raises(AlreadyPending):
        create(store)


@pytest.mark.parametrize("status", ["approved", "denied"])
def test_a_new_request_is_allowed_once_the_old_one_is_decided(store, status):
    old = create(store)
    store.decide("agent-1", old.id, status, "oid", "name")

    assert create(store).status == "pending"


def test_a_new_request_is_allowed_once_the_old_one_expired(store, clock):
    create(store)
    clock.time += EXPIRES_AFTER

    assert create(store).status == "pending"


@pytest.mark.parametrize("status", ["approved", "denied"])
def test_a_decision_records_who_decided(store, status):
    request = create(store)

    decided = store.decide("agent-1", request.id, status, "approver-oid", "An Approver")

    assert (decided.status, decided.decided_by_id, decided.decided_by_name) == (status, "approver-oid", "An Approver")
    assert store.get("agent-1", request.id) == decided


def test_a_decision_works_only_once(store):
    request = create(store)
    store.decide("agent-1", request.id, "denied", "first", "First")

    with pytest.raises(AlreadyFinal):
        store.decide("agent-1", request.id, "approved", "second", "Second")
    assert store.get("agent-1", request.id).decided_by_id == "first"


def test_a_request_expires_on_the_read_after_the_limit(store, clock):
    request = create(store)
    clock.time += EXPIRES_AFTER - timedelta(seconds=1)
    assert store.get("agent-1", request.id).status == "pending"

    clock.time += timedelta(seconds=1)
    assert store.get("agent-1", request.id).status == "expired"


def test_a_late_decision_is_refused(store, clock):
    request = create(store)
    clock.time += EXPIRES_AFTER

    with pytest.raises(AlreadyFinal):
        store.decide("agent-1", request.id, "approved", "oid", "name")


def test_expiry_is_reported_once(clock):
    expired = []
    store = DisableRequestStore(clock, expired.append)
    request = create(store)
    clock.time += EXPIRES_AFTER

    store.get("agent-1", request.id)
    store.get("agent-1", request.id)
    store.pending()

    assert [item.id for item in expired] == [request.id]


def test_pending_lists_only_what_is_waiting(store, clock):
    decided, waiting, late = create(store, "a"), create(store, "b"), create(store, "c")
    store.decide("a", decided.id, "approved", "oid", "name")
    assert {request.id for request in store.pending()} == {waiting.id, late.id}

    clock.time += EXPIRES_AFTER
    assert store.pending() == []


def test_a_request_is_reachable_only_through_its_own_agent(store):
    request = create(store, "agent-1")

    with pytest.raises(NotFound):
        store.get("agent-2", request.id)
    with pytest.raises(NotFound):
        store.decide("agent-2", request.id, "approved", "oid", "name")
    assert store.get("agent-1", request.id).status == "pending"


def test_an_unknown_request_is_not_found(store):
    with pytest.raises(NotFound):
        store.get("agent-1", "no-such-id")


def test_when_decisions_race_only_one_wins(store):
    request = create(store)
    outcomes = []
    start = threading.Barrier(16)
    def decide(number):
        start.wait()
        try:
            store.decide("agent-1", request.id, "approved", str(number), "name")
            outcomes.append("won")
        except AlreadyFinal:
            outcomes.append("refused")
    threads = [threading.Thread(target=decide, args=(number,)) for number in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert outcomes.count("won") == 1 and outcomes.count("refused") == 15
