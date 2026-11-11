"""Unit tests for the CLI (FR7, NFR8): what the person sees, and what counts as a
decision. No network and no sign-in."""

import io
import time
import sys
import types
import urllib.error
import urllib.request

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

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


@pytest.mark.parametrize("stray", ["y", "yes", "a", "approved", "approve deny"])
def test_only_the_full_word_approves(stray):
    # A stray answer decides nothing: it asks again, then the end of input means skip.
    assert ask(typed(stray), lambda line: None) == "skip"


def test_the_end_of_input_decides_nothing():
    assert ask(typed(), lambda line: None) == "skip"


@pytest.mark.parametrize("enter", ["", "   "])
def test_enter_alone_skips_at_once(enter):
    lines = []

    assert ask(typed(enter, "approve"), lines.append) == "skip"   # never reaches the second answer
    assert lines == []


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


# While the guardian's executor works: a waiting line, and a spinner only in a terminal

def test_after_an_approval_the_waiting_line_comes_before_the_result():
    lines = []

    review(FakeApi(REQUEST), set(), typed("approve"), lines.append, lambda request: "disabled")

    assert lines[-2:] == [approve.WAITING_TEXT, "Disabled, with your own rights."]


def test_a_denial_shows_no_waiting_line():
    lines = []

    review(FakeApi(REQUEST), set(), typed("deny"), lines.append, lambda request: "disabled")

    assert approve.WAITING_TEXT not in lines


class FakeTerminal(io.StringIO):
    def __init__(self, tty):
        super().__init__()
        self.tty = tty

    def isatty(self):
        return self.tty


def test_the_spinner_draws_in_a_terminal_and_clears_its_line():
    out = FakeTerminal(tty=True)

    with approve.spinner(out, every_seconds=0.01):
        time.sleep(0.05)

    assert out.getvalue().startswith("\r") and out.getvalue().endswith("\r  \r")


def test_the_spinner_writes_nothing_when_the_output_is_not_a_terminal():
    out = FakeTerminal(tty=False)

    with approve.spinner(out, every_seconds=0.01):
        time.sleep(0.03)

    assert out.getvalue() == ""


# One pass: list the pending requests once, ask about each, exit

def run_main(monkeypatch, api, *answers):
    for name in ("TENANT_ID", "AGENT_DISABLE_CLI_APP_ID", "IDENTITY_API_APP_ID", "GUARDIAN_BLUEPRINT_APP_ID"):
        monkeypatch.setenv(name, "x")
    monkeypatch.setattr(approve, "sign_in", lambda *arguments: approve.Session("token", None, None))
    monkeypatch.setattr(approve, "IdentityApi", lambda url, token: api)
    monkeypatch.setattr("builtins.input", typed(*answers))
    return approve.main([])


class CountingApi(FakeApi):
    def __init__(self, *requests):
        super().__init__(*requests)
        self.listed = 0

    def pending(self):
        self.listed += 1
        return super().pending()


def test_the_cli_lists_once_asks_about_each_and_exits(monkeypatch, capsys):
    api = CountingApi(REQUEST)

    assert run_main(monkeypatch, api, "skip") == 0
    assert api.listed == 1
    assert capsys.readouterr().out.rstrip().endswith("No more pending requests.")


def test_with_nothing_pending_the_cli_says_so_and_exits(monkeypatch, capsys):
    api = CountingApi()

    assert run_main(monkeypatch, api) == 0
    assert api.listed == 1
    assert "No pending requests." in capsys.readouterr().out


# The sign-in redirect, caught by a one-shot listener

def free_port():
    import socket
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def listen_in_background(port, timeout_seconds=5):
    import threading
    result, ready = {}, threading.Event()
    def run():
        result["url"] = approve.wait_for_redirect("127.0.0.1", "abc", port, timeout_seconds, ready.set)
    thread = threading.Thread(target=run)
    thread.start()
    ready.wait(2)
    return thread, result


def get(port, path):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, b""


def test_the_listener_returns_the_redirect_and_tells_the_browser_it_is_done():
    port = free_port()
    thread, result = listen_in_background(port)

    status, body = get(port, "/?code=the-code&state=abc&session_state=s")
    thread.join(5)

    assert (status, body) == (200, approve.SIGNED_IN_PAGE)
    assert result["url"] == "http://localhost:18400/?code=the-code&state=abc&session_state=s"
    assert approve.parse_redirect(result["url"]) == {"code": "the-code", "state": "abc", "session_state": "s"}


def test_the_listener_ignores_requests_without_a_code_and_keeps_waiting():
    port = free_port()
    thread, result = listen_in_background(port)

    assert get(port, "/favicon.ico")[0] == 404
    get(port, "/?error=access_denied&state=abc")
    thread.join(5)

    assert result["url"].endswith("/?error=access_denied&state=abc")


def test_the_listener_gives_up_after_its_timeout():
    port = free_port()
    thread, result = listen_in_background(port, timeout_seconds=0.2)
    thread.join(5)

    assert result["url"] is None


def test_with_a_listener_the_sign_in_needs_no_paste(monkeypatch):
    app = FakeMsalApp({"access_token": "token"})
    use_msal(monkeypatch, app)
    monkeypatch.setattr(approve, "wait_for_redirect", lambda address, state: LANDED if state == "abc" else None)
    def no_paste(prompt):
        raise AssertionError("nothing should be pasted when the CLI listens")

    session = approve.sign_in("tenant", "client", "api-app", no_paste, [].append, listen_address="0.0.0.0")

    assert session.token == "token"
    assert app.redeemed == (FLOW, {"code": "the-code", "state": "abc", "session_state": "s"}, ["api-app/.default"])


def test_a_sign_in_that_never_arrives_is_a_sign_in_error(monkeypatch):
    use_msal(monkeypatch, FakeMsalApp({"access_token": "token"}))
    monkeypatch.setattr(approve, "wait_for_redirect", lambda address, state: None)

    with pytest.raises(approve.SignInError, match="no sign-in arrived"):
        approve.sign_in("tenant", "client", "api-app", typed(), [].append, listen_address="0.0.0.0")


def test_an_old_redirect_from_an_earlier_sign_in_is_ignored_and_the_wait_goes_on():
    port = free_port()
    thread, result = listen_in_background(port)

    status, body = get(port, "/?code=old-code&state=from-an-earlier-run")
    get(port, "/?code=the-code&state=abc")
    thread.join(5)

    assert (status, body) == (400, b"")   # urllib's HTTPError hides the body; the status is what matters
    assert result["url"] == "http://localhost:18400/?code=the-code&state=abc"


# Tracing (NFR4): one trace per run; each decision is a span, linked to the guardian's span that asked

@pytest.fixture
def spans(monkeypatch):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(approve, "TRACER", provider.get_tracer("test"))
    return exporter


def test_a_decision_is_one_span_linked_to_the_span_that_created_the_request(spans):
    asked = PendingRequest("request-1", "agent-1", "It is dormant.", trace_id="0af7651916cd43dd8448eb211c80319c",
                           span_id="b7ad6b7169203331")

    review(FakeApi(asked), set(), typed("approve"), lambda line: None, lambda request: "disabled")

    span, run = spans.get_finished_spans()   # in the order they ended: the decision, then the run
    assert (run.name, run.parent) == ("review disable_requests", None)   # the root of this run's trace
    assert run.attributes["guardian.disable_requests.shown"] == 1
    assert span.name == "decide disable_request" and span.parent.span_id == run.context.span_id
    assert span.attributes["guardian.disable_request.id"] == "request-1"
    assert span.attributes["guardian.decision"] == "approve"
    [link] = span.links
    assert (f"{link.context.trace_id:032x}", f"{link.context.span_id:016x}") == (asked.trace_id, asked.span_id)


def test_a_skip_makes_no_decision_span(spans):
    review(FakeApi(REQUEST), set(), typed(""), lambda line: None)

    [run] = spans.get_finished_spans()
    assert run.name == "review disable_requests"


def test_parse_pending_reads_the_trace_of_the_request():
    [request] = parse_pending({"value": [{"id": "r", "agentId": "a", "traceId": "t", "spanId": "s"}]})

    assert (request.trace_id, request.span_id) == ("t", "s")
