"""Integration tests for the Identity API (FR5, NFR8): real tokens from Entra, against the running API.

They need the sidecar's loopback (for agent tokens) and http://api:8000, so they run inside
docker compose:
    docker compose up -d sidecar api
    docker compose run --rm integration-tests
"""

import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request

import pytest

pytestmark = pytest.mark.integration


def env(name: str) -> str:
    return os.environ[name]


def sidecar_token(api: str, agent_app_id: str) -> str:
    """A token for one agent identity of the guardian's blueprint, from the sidecar."""
    query = urllib.parse.urlencode({"AgentIdentity": agent_app_id})
    url = f"{env('SIDECAR_URL')}/AuthorizationHeaderUnauthenticated/{api}?{query}"
    with urllib.request.urlopen(url, timeout=60) as response:
        return json.load(response)["authorizationHeader"].partition(" ")[2]


def plain_app_token() -> str:
    """A token for the plain test client, straight from Entra with its secret (no sidecar)."""
    body = urllib.parse.urlencode({
        "client_id": env("TEST_CLIENT_APP_ID"),
        "client_secret": env("TEST_CLIENT_SECRET"),
        "scope": f"{env('IDENTITY_API_APP_ID')}/.default",
        "grant_type": "client_credentials",
    }).encode()
    url = f"https://login.microsoftonline.com/{env('TENANT_ID')}/oauth2/v2.0/token"
    with urllib.request.urlopen(urllib.request.Request(url, data=body), timeout=60) as response:
        return json.load(response)["access_token"]


def claims(token: str) -> dict:
    """Read a token's claims without checking it. The API does the checking."""
    payload = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


def submit_findings(token: str | None, correlation_id: str) -> tuple[int, dict]:
    headers = {"Content-Type": "application/json", "X-Correlation-ID": correlation_id}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    report = {"findings": [{"agent_app_id": "integration-test", "agent_name": "integration-test",
                            "check": "orphan", "detail": "integration test, not a real finding"}]}
    request = urllib.request.Request(f"{env('API_URL')}/findings", data=json.dumps(report).encode(),
                                     headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, json.load(error)


def test_guardian_is_accepted():
    status, body = submit_findings(sidecar_token("identity-api", env("GUARDIAN_AGENT_APP_ID")), "integration-guardian")
    assert (status, body["accepted"]) == (202, 1)


def test_agent_without_the_role_is_rejected():
    token = sidecar_token("identity-api", env("GUARDIAN_NOROLE_AGENT_APP_ID"))
    assert "11" in claims(token)["xms_act_fct"].split()             # a real agent...
    assert "Findings.Submit" not in claims(token).get("roles", [])  # ...without the role
    status, body = submit_findings(token, "integration-norole")
    assert (status, body["detail"]) == (403, "permission: missing app role Findings.Submit")


def test_plain_app_is_rejected_even_with_the_role():
    token = plain_app_token()
    assert "Findings.Submit" in claims(token).get("roles", [])           # check 6 alone would pass...
    assert "11" not in str(claims(token).get("xms_act_fct", "")).split()  # ...but it is not an agent
    status, body = submit_findings(token, "integration-plain-app")
    assert (status, body["detail"]) == (403, "agent: caller is not an agent identity")


def test_token_for_another_api_is_rejected():
    status, _ = submit_findings(sidecar_token("graph", env("GUARDIAN_AGENT_APP_ID")), "integration-graph-token")
    assert status == 401


def test_no_token_is_rejected():
    status, body = submit_findings(None, "integration-no-token")
    assert (status, body["detail"]) == (401, "no bearer token")
