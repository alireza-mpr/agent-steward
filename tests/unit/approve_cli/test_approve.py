"""Unit tests for the approve command (FR7, NFR8): what the person sees, and what counts as a
decision. No network and no sign-in."""

import io
import sys
import types
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

FLOW = {"auth_uri": "https://login.example/authorize?state=abc", "state": "abc"}
LANDED = "http://localhost/?code=the-code&state=abc&session_state=s"


class FakeMsalApp:
    def __init__(self, result=None, error=None):
        self.result, self.error = result, error
        self.started, self.redeemed = None, None

    def initiate_auth_code_flow(self, **arguments):
        self.started = arguments
        return FLOW

    def acquire_token_by_auth_code_flow(self, flow, response, scopes):
        self.redeemed = (flow, response, scopes)
        if self.error:
            raise self.error
        return self.result


def use_msal(monkeypatch, app):
    monkeypatch.setitem(sys.modules, "msal", types.SimpleNamespace(PublicClientApplication=lambda client_id, authority: app))


def sign_in(read, lines=None):
    return approve.sign_in("tenant", "client", "api-app", read, (lines if lines is not None else []).append)


def test_sign_in_shows_the_address_and_redeems_what_the_person_pastes(monkeypatch):
    app = FakeMsalApp({"access_token": "token"})
    use_msal(monkeypatch, app)
    lines = []

    assert sign_in(typed(LANDED), lines) == "token"
    assert app.started == {"scopes": ["api-app/.default"], "redirect_uri": "http://localhost", "prompt": "select_account"}
    assert FLOW["auth_uri"] in lines[0]
    # The flow goes back to MSAL with the pasted values, so it checks the state and sends the PKCE verifier.
    assert app.redeemed == (FLOW, {"code": "the-code", "state": "abc", "session_state": "s"}, ["api-app/.default"])


def test_the_pasted_address_may_have_spaces_around_it(monkeypatch):
    use_msal(monkeypatch, FakeMsalApp({"access_token": "token"}))

    assert sign_in(typed(f"  {LANDED}\n")) == "token"


@pytest.mark.parametrize("pasted", ["https://evil.example/?code=x&state=abc", "http://localhost.evil.example/?code=x",
                                    "https://localhost/?code=x", "the-code", ""])
def test_an_address_that_is_not_the_redirect_is_refused_before_msal_sees_it(pasted, monkeypatch):
    app = FakeMsalApp({"access_token": "token"})
    use_msal(monkeypatch, app)

    with pytest.raises(approve.SignInError):
        sign_in(typed(pasted))
    assert app.redeemed is None


def test_an_address_from_another_sign_in_is_refused(monkeypatch):
    use_msal(monkeypatch, FakeMsalApp(error=ValueError("state mismatch")))

    with pytest.raises(approve.SignInError, match="does not belong"):
        sign_in(typed(LANDED))


def test_a_refused_sign_in_gives_no_token(monkeypatch):
    use_msal(monkeypatch, FakeMsalApp({"error": "access_denied"}))

    with pytest.raises(approve.SignInError, match="access_denied"):
        sign_in(typed("http://localhost/?error=access_denied&state=abc"))


def test_no_paste_gives_no_token(monkeypatch):
    use_msal(monkeypatch, FakeMsalApp({"access_token": "token"}))

    with pytest.raises(approve.SignInError):
        sign_in(typed())
