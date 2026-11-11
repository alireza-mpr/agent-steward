"""The CLI (FR7): a person approves or denies the guardian's requests to disable an agent.

    docker compose run --rm --service-ports approve

It signs the person in with the authorization code flow and PKCE, as the public client
agent-steward-agent-disable-cli, so the token is the person's own: its scp (AgentDisable.Decide) says
this app may act for them, and its roles (AgentDisable.Approve) say they may approve. The Identity API
checks both and records who decided. The token stays in memory: it is never written to a file or a log.

The CLI catches the sign-in redirect itself: a one-shot listener on port 18400 receives the browser's
redirect to http://localhost:18400, then stops. In compose, the listener runs on the container's
0.0.0.0, and Docker publishes it only on the host's 127.0.0.1:18400 (--service-ports). The code in the
redirect works once, and only with this run's PKCE verifier, so another program that got it could not
use it (RFC 7636). Without APPROVE_LISTEN_ADDRESS, for example in the obo-claims dev service, which
cannot publish a port, the person pastes the final address instead.

It lists the pending requests once, asks about each one, and exits.

One run of the CLI is one OpenTelemetry trace (NFR4): its root span, review disable_requests, holds the
list call and one span per decision. It is a trace of its own, because the approval happens later than
the guardian's run, in another process. Each decision's span links to the span that created the
request, in the guardian's run, so the two traces can be followed in both directions. The calls to the
Identity API and the executor carry the traceparent. MSAL's calls to Entra are not traced.

Everything shown here is data, never instructions. The reason comes from the model, so it is labelled
as untrusted. An agent's name comes from whoever registered it. Both have their control characters
removed, so they cannot move the cursor or rewrite what the terminal shows.

After an approval, the same sign-in gives a second token, for the guardian's blueprint (Tc), and the
CLI sends it to the guardian's executor. The executor exchanges it with OBO and disables the agent
with the approver's own rights (FR4): the agent proposes, and the approver decides. Tc never goes to
the MCP server, which runs the model's tool calls. A denial sends nothing. Tc is used for that one
call and is never written to a file or a log either.

The Identity API's side of the contract:
    GET  /disableRequests?status=pending                          -> {"value": [the pending requests]}
    POST /agents/{agentId}/disableRequests/{requestId}/decision   -> {"decision": "approve" or "deny"}
                                                                     409 if it was already decided
The executor's side:
    POST /agents/{agentId}/disableRequests/{requestId}/delegation -> {"result": "disabled" or why not}
                                                                     with Tc as the Bearer token
"""

import base64
import json
import os
import sys
import threading
import time
from contextlib import contextmanager
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from opentelemetry import trace

import span_names as names
from telemetry import link_to, setup_tracing

# The run's span and one span per decision. Without the OpenTelemetry SDK (src/shared/telemetry.py) they record nothing.
TRACER = trace.get_tracer(names.APPROVE_CLI_TRACER)

# Registered on the client app without a port (setup/bicep/agent-disable-approvers.bicep): Entra ignores
# the port of a localhost redirect URI. A port nothing listens on, so no local program gets the address.
REDIRECT_URI = "http://localhost:18400"


class SignInError(Exception):
    """The person did not get a token."""


@dataclass(frozen=True)
class PendingRequest:
    request_id: str
    agent_id: str
    reason: str                          # from the model: untrusted
    facts: dict = field(default_factory=dict)   # read from Graph by the MCP server
    expires_at: str = ""
    trace_id: str | None = None          # the guardian's span that created the request
    span_id: str | None = None


def parse_pending(body: dict) -> list[PendingRequest]:
    """The Identity API's list of pending requests. Anything unexpected raises ValueError."""
    if not isinstance(body, dict) or not isinstance(body.get("value"), list):
        raise ValueError("not a list of disable requests")
    requests = []
    for item in body["value"]:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not isinstance(item.get("agentId"), str):
            raise ValueError("a disable request needs id and agentId")
        facts = item.get("facts")
        requests.append(PendingRequest(item["id"], item["agentId"], str(item.get("reason") or ""),
                                       facts if isinstance(facts, dict) else {}, str(item.get("expiresAt") or ""),
                                       item.get("traceId"), item.get("spanId")))
    return requests


def plain(value: object) -> str:
    """Text that is safe to print: control characters, such as terminal escape codes, become spaces."""
    return "".join(character if character.isprintable() else " " for character in str(value))


def show(request: PendingRequest) -> str:
    facts = request.facts
    return "\n".join([
        "",
        f"Disable agent:  {plain(facts.get('name', '(unknown)'))}",
        f"App ID:         {plain(request.agent_id)}",
        f"Blueprint:      {plain(facts.get('blueprintAppId', '(unknown)'))}",
        f"Flaw:           {plain(facts.get('flaw', '(unknown)'))}",
        f"Last sign-in:   {plain(facts.get('lastSignIn') or 'none in the last 30 days')}",
        f"Expires at:     {plain(request.expires_at or '(unknown)')}",
        f"Reason (UNTRUSTED, written by the model, not a fact): {plain(request.reason)}",
    ])


def ask(read: Callable[[str], str], write: Callable[[str], None]) -> str:
    """The person's choice: approve, deny or skip. Only the full word approves, so a stray key
    cannot. Enter alone, or no more input (end of file), means skip: nothing is decided."""
    while True:
        try:
            answer = read("Type approve or deny, or press Enter to skip: ").strip().lower()
        except EOFError:
            return "skip"
        if answer in ("approve", "deny", "skip"):
            return answer
        if answer == "":
            return "skip"
        write("Not understood: nothing was decided.")


class IdentityApi:
    def __init__(self, api_url: str, token: str):
        self._api_url = api_url
        self._token = token

    def _call(self, method: str, path: str, body: dict | None = None) -> dict:
        request = urllib.request.Request(self._api_url + path, method=method,
                                         headers={"Authorization": f"Bearer {self._token}",
                                                  "Content-Type": "application/json"},
                                         data=json.dumps(body).encode() if body is not None else None)
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)

    def pending(self) -> list[PendingRequest]:
        return parse_pending(self._call("GET", "/disableRequests?status=pending"))

    def decide(self, agent_id: str, request_id: str, decision: str) -> None:
        self._call("POST", f"/agents/{urllib.parse.quote(agent_id)}/disableRequests/"
                           f"{urllib.parse.quote(request_id)}/decision", {"decision": decision})


# What the executor's answer means for the person. Anything else is shown as it came, as plain text.
DELEGATION_RESULTS = {
    "disabled": "Disabled, with your own rights.",
    "not_the_approver": "Not disabled: the guardian refused, because you are not the person who approved.",
    "not_approved": "Not disabled: the request is not approved.",
    "already_executed": "Not disabled now: this request was already carried out.",
    "no_longer_allowed": "Not disabled: the agent is already disabled, no longer dormant, or belongs to the guardian.",
    "not_executable": "Not disabled: the approval can no longer be used.",
    "write_failed": "Not disabled: the write to Microsoft Graph failed. Check the executor's audit log.",
}


def send_delegation(executor_url: str, request: "PendingRequest", tc: str) -> str:
    """Hand Tc to the guardian's executor for one approved request. Returns its result word."""
    url = (f"{executor_url}/agents/{urllib.parse.quote(request.agent_id)}/disableRequests/"
           f"{urllib.parse.quote(request.request_id)}/delegation")
    call = urllib.request.Request(url, method="POST", data=b"", headers={"Authorization": f"Bearer {tc}"})
    try:
        with urllib.request.urlopen(call, timeout=120) as response:
            return str(json.load(response)["result"])
    except urllib.error.HTTPError as error:
        try:
            return str(json.load(error)["result"])
        except (OSError, ValueError, KeyError, TypeError):
            return f"HTTP {error.code}"


WAITING_TEXT = "Disabling the agent with your rights. Waiting for the guardian's executor..."
SPINNER_FRAMES = "|/-\\"


@contextmanager
def spinner(out=sys.stdout, every_seconds: float = 0.1):
    """A spinner on the current line while the block runs, only in a real terminal. The executor's
    checks, the OBO exchange and the confirming read take about 20 seconds in this lab."""
    if not out.isatty():
        yield
        return
    stop = threading.Event()
    def spin():
        frame = 0
        while not stop.wait(every_seconds):
            out.write(f"\r{SPINNER_FRAMES[frame % len(SPINNER_FRAMES)]} ")
            out.flush()
            frame += 1
    thread = threading.Thread(target=spin, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join()
        out.write("\r  \r")   # clears the spinner, so the result starts at the line's beginning
        out.flush()


def review(api, skipped: set[str], read: Callable[[str], str], write: Callable[[str], None],
           delegate: Callable[["PendingRequest"], str] | None = None) -> int:
    """Ask about every pending request, once each, and return how many were shown. A request the
    person skipped is not asked again. After an approval, delegate hands the person's rights to the
    guardian for that one request and returns what the guardian answered. The whole review is one
    span, the root of this run's trace."""
    with TRACER.start_as_current_span(names.REVIEW_SPAN) as span:
        shown = review_each(api, skipped, read, write, delegate)
        span.set_attribute(names.REQUESTS_SHOWN, shown)
    return shown


def review_each(api, skipped: set[str], read: Callable[[str], str], write: Callable[[str], None],
                delegate: Callable[["PendingRequest"], str] | None) -> int:
    """review's loop, inside the run's span."""
    shown = 0
    for request in api.pending():
        if request.request_id in skipped:
            continue
        shown += 1
        write(show(request))
        choice = ask(read, write)
        if choice == "skip":
            skipped.add(request.request_id)
            write("Skipped: nothing was decided.")
            continue
        # The span starts after the answer, so it times the calls, not the person's thinking.
        with TRACER.start_as_current_span(names.DECIDE_SPAN, links=link_to(request.trace_id, request.span_id),
                                          attributes={names.REQUEST_ID: request.request_id, names.DECISION: choice}):
            decide_and_delegate(api, request, choice, write, delegate)
    return shown


def decide_and_delegate(api, request: "PendingRequest", choice: str, write: Callable[[str], None],
                        delegate: Callable[["PendingRequest"], str] | None) -> None:
    """Send the decision. After an approval, hand the person's rights to the guardian (delegate)."""
    try:
        api.decide(request.agent_id, request.request_id, choice)
        write(f"Sent: {choice}.")
    except urllib.error.HTTPError as error:
        if error.code != 409:
            raise
        write("Not sent: this request was already decided, or it expired.")
        return
    if choice == "approve" and delegate is not None:
        write(WAITING_TEXT)
        try:
            with spinner():
                result = delegate(request)
        except SignInError as error:
            write(f"Not disabled: {plain(error)}")
            return
        except OSError:
            write("Not disabled: the guardian's executor could not be reached.")
            return
        write(DELEGATION_RESULTS.get(result, f"Not disabled: {plain(result)}"))


LISTEN_PORT = 18400          # the port in REDIRECT_URI
WAIT_FOR_SIGN_IN_SECONDS = 300
SIGNED_IN_PAGE = b"Signed in. You can close this tab and go back to the terminal."
OLD_SIGN_IN_PAGE = b"This page is from an earlier sign-in. You can close this tab."


def wait_for_redirect(address: str, expected_state: str, port: int = LISTEN_PORT,
                      timeout_seconds: float = WAIT_FOR_SIGN_IN_SECONDS,
                      ready: Callable[[], None] = lambda: None) -> str | None:
    """A one-shot listener for the browser's redirect. Returns the redirect as a full URL, or None
    if none arrived in time. Only the redirect for this sign-in counts: its state must match. A
    browser tab left from an earlier sign-in can send its old redirect again as soon as the port
    answers; it gets a 400 and the wait goes on, as do requests without code or error, such as
    /favicon.ico (404). Nothing is logged: the redirect holds the sign-in code."""
    import http.server
    received: dict[str, str] = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            values = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(self.path).query))
            if not values.keys() & {"code", "error"}:
                self.send_error(404)
                return
            if values.get("state") != expected_state:
                self.send_response(400)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(OLD_SIGN_IN_PAGE)))
                self.end_headers()
                self.wfile.write(OLD_SIGN_IN_PAGE)
                return
            received["path"] = self.path
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(SIGNED_IN_PAGE)))
            self.end_headers()
            self.wfile.write(SIGNED_IN_PAGE)

        def log_message(self, *arguments):   # the default logs the path, which holds the code
            pass

    deadline = time.monotonic() + timeout_seconds
    with http.server.HTTPServer((address, port), Handler) as server:
        ready()
        while "path" not in received:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            server.timeout = remaining
            server.handle_request()   # returns after one request, or when the timeout passes
    return REDIRECT_URI + received["path"]


def parse_redirect(pasted: str) -> dict[str, str]:
    """The query values of the URL the browser landed on, for MSAL to check."""
    url = urllib.parse.urlsplit(pasted.strip())
    if f"{url.scheme}://{url.netloc}" != REDIRECT_URI:
        raise SignInError(f"that is not the {REDIRECT_URI} address the browser landed on")
    return {name: value for name, value in urllib.parse.parse_qsl(url.query)}


@dataclass
class Session:
    """The person's sign-in: a token for the Identity API, and more tokens on request."""
    token: str            # for the Identity API
    app: Any              # MSAL's app, which holds the sign-in in its in-memory cache
    account: dict | None

    def token_for(self, scope: str) -> str:
        """A token for another API, from the same sign-in: MSAL redeems the refresh token, so the
        person does not sign in again. Raises SignInError if Entra issues none."""
        result = self.app.acquire_token_silent([scope], account=self.account) if self.account else None
        if not result or "access_token" not in result:
            error = (result or {}).get("error", "no cached sign-in to use")
            raise SignInError(f"no token for {scope}: {error}")
        return result["access_token"]


def sign_in(tenant_id: str, client_id: str, api_app_id: str, read: Callable[[str], str],
            write: Callable[[str], None], listen_address: str | None = None) -> Session:
    """The authorization code flow with PKCE: MSAL makes the verifier and checks the state. Its
    default token cache is in memory only. With listen_address, a one-shot listener catches the
    redirect; without it, the person pastes the address the browser landed on."""
    import msal   # here, so the offline tests of the rest need no MSAL
    app = msal.PublicClientApplication(client_id, authority=f"https://login.microsoftonline.com/{tenant_id}")
    # .default: every scope an admin consented to for this client on this API, here AgentDisable.Decide.
    scopes = [f"{api_app_id}/.default"]
    flow = app.initiate_auth_code_flow(scopes=scopes, redirect_uri=REDIRECT_URI, prompt="select_account")
    write("1. Open this address in your browser and sign in:\n\n" + flow["auth_uri"] + "\n")
    if listen_address:
        write("2. After you sign in, the browser says \"Signed in\". Then come back here.\n")
        pasted = wait_for_redirect(listen_address, flow["state"])
        if pasted is None:
            raise SignInError(f"no sign-in arrived within {WAIT_FOR_SIGN_IN_SECONDS // 60} minutes")
    else:
        write(f"2. The browser then fails to load a {REDIRECT_URI} page. That is expected. Copy its whole address.\n")
        try:
            pasted = read("3. Paste the address here: ")
        except EOFError:
            raise SignInError("no address was pasted") from None
    try:
        result = app.acquire_token_by_auth_code_flow(flow, parse_redirect(pasted), scopes=scopes)
    except ValueError:   # MSAL's check of the state failed: not the answer to this sign-in
        raise SignInError("that address does not belong to this sign-in") from None
    if "access_token" not in result:
        raise SignInError(f"sign-in failed: {result.get('error', 'unknown error')}")
    accounts = app.get_accounts()
    return Session(result["access_token"], app, accounts[0] if accounts else None)


def refusal_text(error: urllib.error.HTTPError) -> str:
    """The status and the API's own reason, such as a missing role, shown as plain text."""
    try:
        return f"HTTP {error.code}: {plain(json.load(error)['detail'])}"
    except (OSError, ValueError, KeyError, TypeError):
        return f"HTTP {error.code}"


def decode_claims(token: str) -> dict:
    """A JWT's payload, without checking the signature. For display only: the Identity API checks it."""
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))


def obo_token(sidecar_url: str, agent_id: str, tc: str) -> str:
    """Dev check for the OBO design (FR4): hand Tc to the sidecar and get back the token it exchanges
    it for, for Graph's delegated disable permission, as the guardian's agent identity. Works only
    from inside the sidecar's network namespace (the obo-claims service)."""
    query = urllib.parse.urlencode({"AgentIdentity": agent_id})
    request = urllib.request.Request(f"{sidecar_url}/AuthorizationHeader/graph-delegated?{query}",
                                     headers={"Authorization": f"Bearer {tc}"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)["authorizationHeader"].partition(" ")[2]


def main(argv: list[str] | None = None) -> int:
    # Dev options. Each prints one token's claims after sign-in, then stops. They never print the
    # token itself, so the output cannot be replayed. The claims hold real tenant and object IDs:
    # redact them before they go anywhere.
    #   --show-claims             the token for the Identity API
    #   --show-delegation-claims  the token for the guardian's blueprint (Tc), which the guardian
    #                             will exchange with OBO for the approved disable (FR4)
    #   --show-obo-claims         the token the sidecar exchanges Tc for: Graph, delegated, as the
    #                             guardian. Only in the obo-claims service, which shares the
    #                             executor's sidecar's loopback.
    options = sys.argv[1:] if argv is None else argv
    if setup_tracing():   # only when OTEL_SERVICE_NAME is set (src/shared/telemetry.py)
        from opentelemetry.instrumentation.urllib import URLLibInstrumentor
        # A client span, and the traceparent header, for each call to the Identity API and the
        # executor. It never records a header, so neither token goes into a span.
        URLLibInstrumentor().instrument()
    try:
        session = sign_in(os.environ["TENANT_ID"], os.environ["AGENT_DISABLE_CLI_APP_ID"],
                          os.environ["IDENTITY_API_APP_ID"], input, print,
                          os.environ.get("APPROVE_LISTEN_ADDRESS") or None)
        if "--show-delegation-claims" in options or "--show-obo-claims" in options:
            # A v2 token: the scope is named by the blueprint's app ID, and aud is that app ID.
            tc = session.token_for(f"{os.environ['GUARDIAN_BLUEPRINT_APP_ID']}/access_as_user")
            if "--show-obo-claims" in options:
                tc = obo_token(os.environ["SIDECAR_URL"], os.environ["GUARDIAN_AGENT_APP_ID"], tc)
            print(json.dumps(decode_claims(tc), indent=2, sort_keys=True))
            return 0
    except SignInError as error:
        print(f"Not signed in: {error}", file=sys.stderr)
        return 1
    except urllib.error.HTTPError as error:
        print(f"The sidecar refused the exchange: HTTP {error.code}: "
              f"{plain(error.read().decode(errors='replace'))}", file=sys.stderr)
        return 1
    if "--show-claims" in options:
        print(json.dumps(decode_claims(session.token), indent=2, sort_keys=True))
        return 0
    api = IdentityApi(os.environ.get("API_URL", "http://identity-api:8000"), session.token)
    blueprint_scope = f"{os.environ['GUARDIAN_BLUEPRINT_APP_ID']}/access_as_user"
    executor_url = os.environ.get("EXECUTOR_URL", "http://executor-sidecar:8200")
    def delegate(request: PendingRequest) -> str:
        return send_delegation(executor_url, request, session.token_for(blueprint_scope))
    # One pass: the propose phase has created every request before the approver opens the CLI, so
    # the CLI lists them once, asks about each, and exits. A skipped request stays pending.
    try:
        shown = review(api, set(), input, print, delegate)
    except KeyboardInterrupt:
        return 0
    except urllib.error.HTTPError as error:
        print(f"The Identity API refused the call: {refusal_text(error)}", file=sys.stderr)
        return 1
    except (OSError, ValueError) as error:
        print(f"The Identity API gave no usable answer: {type(error).__name__}", file=sys.stderr)
        return 1
    print("No more pending requests." if shown else "No pending requests.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
