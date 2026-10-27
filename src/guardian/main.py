"""The guardian's run (FR3): read the tenant's agents, apply the four rules, report the findings.

One run, then exit. docker compose up runs it once, and docker compose run --rm guardian runs
it again. It gets every token from the sidecar, as its agent identity, and never sees the
blueprint's key.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from graph_reader import GraphClient, fetch_agents, fetch_blueprints, fetch_last_sign_ins
from rules import Finding, load_permission_policy, run_all_rules

SIDECAR_URL = os.environ.get("SIDECAR_URL", "http://127.0.0.1:5000")
API_URL = os.environ.get("API_URL", "http://api:8000")
AGENT_APP_ID = os.environ["GUARDIAN_AGENT_APP_ID"]
POLICY_PATH = Path(os.environ.get("POLICY_PATH", "/config/agent-permissions.json"))


def wait_until_ready(url: str, timeout_seconds: int = 60) -> None:
    """docker compose up starts everything at once, so the guardian can be faster than its neighbours."""
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            with urllib.request.urlopen(url, timeout=5):
                return
        except OSError:
            if time.monotonic() > deadline:
                raise SystemExit(f"Not ready after {timeout_seconds} s: {url}")
            time.sleep(2)


def token_for(api: str):
    """A function that asks the sidecar for a token for one downstream API, as the guardian."""
    def get_token() -> str:
        query = urllib.parse.urlencode({"AgentIdentity": AGENT_APP_ID})
        url = f"{SIDECAR_URL}/AuthorizationHeaderUnauthenticated/{api}?{query}"
        with urllib.request.urlopen(url, timeout=60) as response:
            return json.load(response)["authorizationHeader"].partition(" ")[2]
    return get_token


def submit(findings: list[Finding], correlation_id: str) -> dict:
    request = urllib.request.Request(
        f"{API_URL}/findings",
        data=json.dumps({"findings": [asdict(finding) for finding in findings]}).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {token_for('identity-api')()}",
                 "X-Correlation-ID": correlation_id},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def main() -> int:
    wait_until_ready(f"{SIDECAR_URL}/healthz")
    wait_until_ready(f"{API_URL}/health")
    now = datetime.now(timezone.utc)
    correlation_id = str(uuid.uuid4())   # the Identity API writes the same ID into its audit line

    try:
        graph = GraphClient(token_for("graph"))
        findings = run_all_rules(fetch_agents(graph), fetch_blueprints(graph),
                                 load_permission_policy(POLICY_PATH), fetch_last_sign_ins(graph, now), now)
        answer = submit(findings, correlation_id)
    except urllib.error.HTTPError as error:
        print(f"Guardian run {correlation_id} failed: {error.code} from {error.url}", file=sys.stderr)
        return 1

    print(f"Guardian run {correlation_id}: {len(findings)} findings, "
          f"{answer['accepted']} accepted by the Identity API")
    for finding in sorted(findings, key=lambda finding: (finding.agent_name, finding.check)):
        print(f"  {finding.agent_name:<36} {finding.check:<20} {finding.detail}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
