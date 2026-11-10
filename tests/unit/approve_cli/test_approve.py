"""Unit tests for the CLI (FR7, NFR8): what the person sees, and what counts as a
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


def refused(code, body):
    return urllib.error.HTTPError("http://api", code, "refused", {}, io.BytesIO(body))


def test_a_refusal_shows_the_apis_reason():
    body = b'{"detail": "permission: missing app role AgentDisable.Approve"}'

    assert approve.refusal_text(refused(403, body)) == "HTTP 403: permission: missing app role AgentDisable.Approve"


@pytest.mark.parametrize("body", [b"", b"not json", b"[]", b"{}"])
def test_a_refusal_with_no_usable_reason_shows_the_status(body):
    assert approve.refusal_text(refused(502, body)) == "HTTP 502"


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
LANDED = "http://localhost:18400/?code=the-code&state=abc&session_state=s"


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

    def get_accounts(self):
        return [{"home_account_id": "the-person"}] if self.result and "access_token" in self.result else []

    def acquire_token_silent(self, scopes, account):
        self.silent = (scopes, account)
        return getattr(self, "silent_result", None)


def use_msal(monkeypatch, app):
    monkeypatch.setitem(sys.modules, "msal", types.SimpleNamespace(PublicClientApplication=lambda client_id, authority: app))


def sign_in(read, lines=None):
    return approve.sign_in("tenant", "client", "api-app", read, (lines if lines is not None else []).append)


def test_sign_in_shows_the_address_and_redeems_what_the_person_pastes(monkeypatch):
    app = FakeMsalApp({"access_token": "token"})
    use_msal(monkeypatch, app)
    lines = []

    assert sign_in(typed(LANDED), lines).token == "token"
    assert app.started == {"scopes": ["api-app/.default"], "redirect_uri": "http://localhost:18400", "prompt": "select_account"}
    assert FLOW["auth_uri"] in lines[0]
    # The flow goes back to MSAL with the pasted values, so it checks the state and sends the PKCE verifier.
    assert app.redeemed == (FLOW, {"code": "the-code", "state": "abc", "session_state": "s"}, ["api-app/.default"])


def test_the_pasted_address_may_have_spaces_around_it(monkeypatch):
    use_msal(monkeypatch, FakeMsalApp({"access_token": "token"}))

    assert sign_in(typed(f"  {LANDED}\n")).token == "token"


@pytest.mark.parametrize("pasted", ["https://evil.example/?code=x&state=abc", "http://localhost.evil.example/?code=x",
                                    "https://localhost:18400/?code=x", "http://localhost/?code=x", "http://localhost:80/?code=x",
                                    "the-code", ""])
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
        sign_in(typed("http://localhost:18400/?error=access_denied&state=abc"))


def test_no_paste_gives_no_token(monkeypatch):
    use_msal(monkeypatch, FakeMsalApp({"access_token": "token"}))

    with pytest.raises(approve.SignInError):
        sign_in(typed())


def fake_jwt(claims: dict) -> str:
    """A token shaped like a JWT, with no real signature: only its payload matters here."""
    import base64
    import json
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"header.{payload}.signature"


def test_decode_claims_reads_the_payload():
    claims = {"scp": "AgentDisable.Decide", "roles": ["AgentDisable.Approve"], "idtyp": "user"}

    assert approve.decode_claims(fake_jwt(claims)) == claims


def test_show_claims_prints_the_claims_never_the_token_and_calls_no_api(monkeypatch, capsys):
    token = fake_jwt({"scp": "AgentDisable.Decide", "roles": ["AgentDisable.Approve"]})
    for name in ("TENANT_ID", "AGENT_DISABLE_CLI_APP_ID", "IDENTITY_API_APP_ID"):
        monkeypatch.setenv(name, "x")
    monkeypatch.setattr(approve, "sign_in", lambda *arguments: approve.Session(token, None, None))
    def no_api(*arguments):
        raise AssertionError("--show-claims must not call the Identity API")
    monkeypatch.setattr(approve, "IdentityApi", no_api)

    assert approve.main(["--show-claims"]) == 0
    shown = capsys.readouterr().out
    assert '"scp": "AgentDisable.Decide"' in shown
    assert token not in shown
    assert token.split(".")[1] not in shown


# The token for the guardian's blueprint (Tc), from the same sign-in

def test_token_for_uses_the_same_sign_in_without_asking_again(monkeypatch):
    app = FakeMsalApp({"access_token": "token"})
    app.silent_result = {"access_token": "tc"}
    use_msal(monkeypatch, app)
    session = sign_in(typed(LANDED))

    assert session.token_for("blueprint/access_as_user") == "tc"
    assert app.silent == (["blueprint/access_as_user"], {"home_account_id": "the-person"})


@pytest.mark.parametrize("silent_result", [None, {"error": "invalid_grant"}])
def test_no_token_for_the_blueprint_is_a_sign_in_error(silent_result, monkeypatch):
    app = FakeMsalApp({"access_token": "token"})
    app.silent_result = silent_result
    use_msal(monkeypatch, app)
    session = sign_in(typed(LANDED))

    with pytest.raises(approve.SignInError):
        session.token_for("blueprint/access_as_user")


def test_show_delegation_claims_prints_tc_claims_never_the_token_and_calls_no_api(monkeypatch, capsys):
    tc = fake_jwt({"aud": "blueprint", "scp": "access_as_user"})
    for name in ("TENANT_ID", "AGENT_DISABLE_CLI_APP_ID", "IDENTITY_API_APP_ID", "GUARDIAN_BLUEPRINT_APP_ID"):
        monkeypatch.setenv(name, "x")
    asked = []
    class FakeSession:
        token = "identity-api-token"
        def token_for(self, scope):
            asked.append(scope)
            return tc
    monkeypatch.setattr(approve, "sign_in", lambda *arguments: FakeSession())
    def no_api(*arguments):
        raise AssertionError("--show-delegation-claims must not call the Identity API")
    monkeypatch.setattr(approve, "IdentityApi", no_api)

    assert approve.main(["--show-delegation-claims"]) == 0
    shown = capsys.readouterr().out
    assert asked == ["x/access_as_user"]
    assert '"scp": "access_as_user"' in shown
    assert tc not in shown
    assert tc.split(".")[1] not in shown


def test_show_obo_claims_hands_tc_to_the_sidecar_and_prints_only_the_claims(monkeypatch, capsys):
    tc = fake_jwt({"aud": "blueprint", "scp": "access_as_user"})
    obo = fake_jwt({"aud": "graph", "scp": "AgentIdentity.EnableDisable.All"})
    for name in ("TENANT_ID", "AGENT_DISABLE_CLI_APP_ID", "IDENTITY_API_APP_ID", "GUARDIAN_BLUEPRINT_APP_ID"):
        monkeypatch.setenv(name, "x")
    monkeypatch.setenv("SIDECAR_URL", "http://127.0.0.1:5000")
    monkeypatch.setenv("GUARDIAN_AGENT_APP_ID", "guardian")
    class FakeSession:
        token = "identity-api-token"
        def token_for(self, scope):
            return tc
    monkeypatch.setattr(approve, "sign_in", lambda *arguments: FakeSession())
    sent = []
    class Response(io.BytesIO):
        def __enter__(self):
            return self
        def __exit__(self, *arguments):
            return False
    def fake_urlopen(request, timeout):
        sent.append(request)
        return Response(('{"authorizationHeader": "Bearer ' + obo + '"}').encode())
    monkeypatch.setattr(approve.urllib.request, "urlopen", fake_urlopen)

    assert approve.main(["--show-obo-claims"]) == 0
    assert sent[0].full_url == "http://127.0.0.1:5000/AuthorizationHeader/graph-delegated?AgentIdentity=guardian"
    assert sent[0].get_header("Authorization") == f"Bearer {tc}"
    shown = capsys.readouterr().out
    assert '"scp": "AgentIdentity.EnableDisable.All"' in shown
    for token in (tc, obo):
        assert token not in shown and token.split(".")[1] not in shown


# After an approval: the approver's delegation (FR4)

def test_after_an_approval_the_delegation_is_sent_and_its_answer_shown():
    api, lines, sent = FakeApi(REQUEST), [], []
    def delegate(request):
        sent.append(request.request_id)
        return "disabled"

    review(api, set(), typed("approve"), lines.append, delegate)

    assert sent == ["request-1"]
    assert lines[-1] == "Disabled, with your own rights."


def test_a_denial_sends_no_delegation():
    def delegate(request):
        raise AssertionError("a denial must not hand over the person's rights")

    review(FakeApi(REQUEST), set(), typed("deny"), lambda line: None, delegate)


def test_a_decision_refused_by_the_identity_api_sends_no_delegation():
    def delegate(request):
        raise AssertionError("no delegation for a decision that was not recorded")

    review(FakeApi(REQUEST, decide_error=conflict()), set(), typed("approve"), lambda line: None, delegate)


@pytest.mark.parametrize("result, shown", [("not_the_approver", "not the person who approved"),
                                           ("something\x1b[2Jodd", "Not disabled: something [2Jodd")])
def test_a_refused_delegation_is_shown_plainly(result, shown):
    lines = []

    review(FakeApi(REQUEST), set(), typed("approve"), lines.append, lambda request: result)

    assert shown in lines[-1]


@pytest.mark.parametrize("failure, shown", [(OSError("refused"), "could not be reached"),
                                            (approve.SignInError("no token for x: invalid_grant"), "no token")])
def test_a_delegation_that_cannot_be_made_is_shown_and_the_review_goes_on(failure, shown):
    lines = []
    def delegate(request):
        raise failure

    review(FakeApi(REQUEST), set(), typed("approve"), lines.append, delegate)

    assert shown in lines[-1]


def test_send_delegation_posts_tc_to_the_request_and_reads_the_result(monkeypatch):
    sent = []
    class Response(io.BytesIO):
        def __enter__(self):
            return self
        def __exit__(self, *arguments):
            return False
    def fake_urlopen(call, timeout):
        sent.append(call)
        return Response(b'{"result": "disabled"}')
    monkeypatch.setattr(approve.urllib.request, "urlopen", fake_urlopen)

    assert approve.send_delegation("http://executor-sidecar:8200", REQUEST, "the-tc") == "disabled"
    assert sent[0].full_url == "http://executor-sidecar:8200/agents/agent-1/disableRequests/request-1/delegation"
    assert sent[0].get_method() == "POST" and sent[0].get_header("Authorization") == "Bearer the-tc"


def test_send_delegation_reads_the_result_of_a_refusal(monkeypatch):
    def refuse(call, timeout):
        raise urllib.error.HTTPError(call.full_url, 403, "Forbidden", {}, io.BytesIO(b'{"result": "not_the_approver"}'))
    monkeypatch.setattr(approve.urllib.request, "urlopen", refuse)

    assert approve.send_delegation("http://executor-sidecar:8200", REQUEST, "the-tc") == "not_the_approver"
