"""The approve command (FR7): a person approves or denies the guardian's requests to disable an agent.

    docker compose run --rm approve

It signs the person in with the device code flow, as the public client agent-steward-agent-disable-cli,
so the token is the person's own: its scp (AgentDisable.Decide) says this app may act for them, and its
roles (AgentDisable.Approve) say they may approve. The Identity API checks both and records who decided.
The token stays in memory: it is never written to a file or a log. Device code phishing is a known risk
of this flow, accepted for this lab (THREATMODEL.md).

Everything shown here is data, never instructions. The reason comes from the model, so it is labelled
as untrusted. An agent's name comes from whoever registered it. Both have their control characters
removed, so they cannot move the cursor or rewrite what the terminal shows.

The Identity API's side of the contract:
    GET  /disableRequests?status=pending                          -> {"value": [the pending requests]}
    POST /agents/{agentId}/disableRequests/{requestId}/decision   -> {"decision": "approve" or "deny"}
                                                                     409 if it was already decided
"""

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field

POLL_EVERY_SECONDS = 5


class SignInError(Exception):
    """The person did not get a token."""


@dataclass(frozen=True)
class PendingRequest:
    request_id: str
    agent_id: str
    reason: str                          # from the model: untrusted
    facts: dict = field(default_factory=dict)   # read from Graph by the MCP server
    expires_at: str = ""


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
                                       facts if isinstance(facts, dict) else {}, str(item.get("expiresAt") or "")))
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
    cannot. No more input (end of file) means skip: nothing is decided."""
    while True:
        try:
            answer = read("Type approve, deny or skip: ").strip().lower()
        except EOFError:
            return "skip"
        if answer in ("approve", "deny", "skip"):
            return answer
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


def review(api, skipped: set[str], read: Callable[[str], str], write: Callable[[str], None]) -> None:
    """Ask about every pending request, once each. A request the person skipped is not asked again."""
    for request in api.pending():
        if request.request_id in skipped:
            continue
        write(show(request))
        choice = ask(read, write)
        if choice == "skip":
            skipped.add(request.request_id)
            write("Skipped: nothing was decided.")
            continue
        try:
            api.decide(request.agent_id, request.request_id, choice)
            write(f"Sent: {choice}.")
        except urllib.error.HTTPError as error:
            if error.code != 409:
                raise
            write("Not sent: this request was already decided, or it expired.")


def sign_in(tenant_id: str, client_id: str, api_app_id: str, write: Callable[[str], None]) -> str:
    """The device code flow. MSAL's default token cache is in memory only."""
    import msal   # here, so the offline tests of the rest need no MSAL
    app = msal.PublicClientApplication(client_id, authority=f"https://login.microsoftonline.com/{tenant_id}")
    # .default: every scope an admin consented to for this client on this API, here AgentDisable.Decide.
    flow = app.initiate_device_flow(scopes=[f"{api_app_id}/.default"])
    if "user_code" not in flow:
        raise SignInError(f"the device code flow did not start: {flow.get('error', 'unknown error')}")
    write(flow["message"])
    result = app.acquire_token_by_device_flow(flow)
    if "access_token" not in result:
        raise SignInError(f"sign-in failed: {result.get('error', 'unknown error')}")
    return result["access_token"]


def main() -> int:
    try:
        token = sign_in(os.environ["TENANT_ID"], os.environ["AGENT_DISABLE_CLI_APP_ID"],
                        os.environ["IDENTITY_API_APP_ID"], print)
    except SignInError as error:
        print(f"Not signed in: {error}", file=sys.stderr)
        return 1
    api = IdentityApi(os.environ.get("API_URL", "http://api:8000"), token)
    skipped: set[str] = set()
    print("Waiting for requests. Press Ctrl+C to stop.", flush=True)
    try:
        while True:
            # The guardian asks one agent at a time, so the next request can arrive after this decision.
            review(api, skipped, input, print)
            time.sleep(POLL_EVERY_SECONDS)
    except KeyboardInterrupt:
        return 0
    except urllib.error.HTTPError as error:
        print(f"The Identity API refused the call: HTTP {error.code}. Are you in the approvers group?", file=sys.stderr)
        return 1
    except (OSError, ValueError) as error:
        print(f"The Identity API gave no usable answer: {type(error).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
