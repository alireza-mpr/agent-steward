"""End-to-end test of the guardian (FR3): the real guardian code, the real Graph, the real rules
and the running Identity API. Nothing is played: the test runs the same scan() as the guardian.

Runs inside docker compose (it needs the sidecar's loopback and http://api:8000):
    docker compose up -d sidecar api
    docker compose run --rm end-to-end-tests
"""

import pytest

pytestmark = pytest.mark.end_to_end

# One planned flaw per fixture (setup/agent-id/Deploy-Fixtures.ps1).
PLANNED_FINDINGS = {
    ("agent-steward-fixture-orphan-01", "orphan"),
    ("agent-steward-fixture-expiring-01", "expiring_credential"),
    ("agent-steward-fixture-overperm-01", "over_permissioned"),
    ("agent-steward-fixture-dormant-01", "dormant"),
}


def test_a_guardian_scan_finds_every_planned_flaw_and_reports_it():
    from main import scan   # imported here: the guardian's module needs the stack's settings

    result = scan()

    found = {(finding.agent_name, finding.check) for finding in result.findings}
    assert PLANNED_FINDINGS <= found
    assert not [finding for finding in result.findings if finding.agent_name.startswith("agent-steward-guardian")]
    assert result.answer == {"accepted": len(result.findings), "correlation_id": result.correlation_id}
