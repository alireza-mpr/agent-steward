"""Unit tests for the approve command (FR7, NFR8): what the person sees, and what counts as a
decision. No network and no sign-in."""

import io
import urllib.error

import pytest

import approve
from approve import PendingRequest, ask, parse_pending, plain, review, show

REQUEST = PendingRequest("request-1", "agent-1", "It is dormant.",
                         {"name": "agent-steward-fixture-dormant-01", "blueprintAppId": "blueprint-1",
                          "flaw": "no sign-in in the last 30 days", "lastSignIn": None},
                         "2026-10-31T03:00:00Z")


class FakeApi:
    def __init__(self, *requests, decide_error=None):
        self.requests = list(requests)
        self.decisions = []
        self.decide_error = decide_error

    def pending(self):
        return self.requests

    def decide(self, agent_id, request_id, decision):
        if self.decide_error:
            raise self.decide_error
        self.decisions.append((agent_id, request_id, decision))


def typed(*answers):
    """Stands in for input(): the next answer, then end of file."""
    answers = list(answers)
    def read(prompt):
        if not answers:
            raise EOFError
        return answers.pop(0)
    return read


def conflict():
    return urllib.error.HTTPError("http://api", 409, "Conflict", {}, io.BytesIO())


# What the person sees

def test_show_gives_the_facts_and_labels_the_reason_as_untrusted():
    shown = show(REQUEST)

    assert "agent-steward-fixture-dormant-01" in shown and "no sign-in in the last 30 days" in shown
    assert "agent-1" in shown and "blueprint-1" in shown
    assert "Reason (UNTRUSTED" in shown and "It is dormant." in shown


def test_the_model_cannot_rewrite_the_screen_with_control_characters():
    attack = "ok\x1b[2J\x1b[1;1HApproved by security\nFlaw:           none\r"

    assert all(character.isprintable() for character in plain(attack))
    forged = PendingRequest("request-1", "agent-1", attack, {"name": "x\x1b[2Jy"})
    # One line per field: a line break in the reason cannot forge a "Flaw:" line.
    assert sum(line.startswith("Flaw:") for line in show(forged).splitlines()) == 1


# What counts as a decision

@pytest.mark.parametrize("answer, choice", [("approve", "approve"), ("deny", "deny"), ("skip", "skip"),
                                            ("  APPROVE ", "approve")])
def test_ask_accepts_the_three_words(answer, choice):
    assert ask(typed(answer), lambda line: None) == choice


@pytest.mark.parametrize("stray", ["y", "yes", "a", "", "approved", "approve deny"])
def test_only_the_full_word_approves(stray):
    # A stray answer decides nothing: it asks again, then the end of input means skip.
    assert ask(typed(stray), lambda line: None) == "skip"


def test_the_end_of_input_decides_nothing():
    assert ask(typed(), lambda line: None) == "skip"


@pytest.mark.parametrize("choice", ["approve", "deny"])
def test_review_sends_the_decision_for_the_request_shown(choice):
    api = FakeApi(REQUEST)

    review(api, set(), typed(choice), lambda line: None)

    assert api.decisions == [("agent-1", "request-1", choice)]


def test_a_skipped_request_sends_nothing_and_is_not_asked_again():
    api = FakeApi(REQUEST)
    skipped = set()

    review(api, skipped, typed("skip"), lambda line: None)
    review(api, skipped, typed("approve"), lambda line: None)

    assert api.decisions == []


def test_a_request_decided_elsewhere_or_expired_is_reported_and_the_review_goes_on():
    lines = []

    review(FakeApi(REQUEST, decide_error=conflict()), set(), typed("approve"), lines.append)

    assert lines[-1] == "Not sent: this request was already decided, or it expired."


def test_any_other_refusal_stops_the_review():
    forbidden = urllib.error.HTTPError("http://api", 403, "Forbidden", {}, io.BytesIO())

    with pytest.raises(urllib.error.HTTPError):
        review(FakeApi(REQUEST, decide_error=forbidden), set(), typed("approve"), lambda line: None)


# The contract

def test_parse_pending_reads_the_list():
    body = {"value": [{"id": "request-1", "agentId": "agent-1", "reason": "It is dormant.",
                       "facts": REQUEST.facts, "expiresAt": "2026-10-31T03:00:00Z"}]}

    assert parse_pending(body) == [REQUEST]


def test_parse_pending_reads_an_empty_list():
    assert parse_pending({"value": []}) == []


@pytest.mark.parametrize("body", [[], {}, {"value": "x"}, {"value": [{"agentId": "agent-1"}]}, {"value": [{"id": "r"}]}, {"value": ["x"]}])
def test_parse_pending_refuses_anything_else(body):
    with pytest.raises(ValueError):
        parse_pending(body)


# Sign-in

class FakeMsalApp:
    def __init__(self, flow, result):
        self.flow, self.result = flow, result
        self.scopes = None

    def initiate_device_flow(self, scopes):
        self.scopes = scopes
        return self.flow

    def acquire_token_by_device_flow(self, flow):
        return self.result


def use_msal(monkeypatch, app):
    import sys
    import types
    monkeypatch.setitem(sys.modules, "msal", types.SimpleNamespace(PublicClientApplication=lambda client_id, authority: app))


def test_sign_in_asks_for_the_consented_scopes_of_the_api_and_shows_the_message(monkeypatch):
    app = FakeMsalApp({"user_code": "ABC", "message": "Open the page and enter ABC"}, {"access_token": "token"})
    use_msal(monkeypatch, app)
    lines = []

    assert approve.sign_in("tenant", "client", "api-app", lines.append) == "token"
    assert app.scopes == ["api-app/.default"]
    assert lines == ["Open the page and enter ABC"]


@pytest.mark.parametrize("flow, result", [({"error": "invalid_client"}, {}),
                                          ({"user_code": "ABC", "message": "m"}, {"error": "authorization_declined"})])
def test_a_failed_sign_in_raises_and_gives_no_token(flow, result, monkeypatch):
    use_msal(monkeypatch, FakeMsalApp(flow, result))

    with pytest.raises(approve.SignInError):
        approve.sign_in("tenant", "client", "api-app", lambda line: None)
