"""Unit tests for the guardian's governance rules (FR3, NFR8).

The sample data mirrors this lab's agents and fixtures, with made-up IDs.
"""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from rules import (Agent, Blueprint, find_dormant, find_expiring_credentials, find_orphans,
                   find_over_permissioned, load_permission_policy, run_all_rules)

NOW = datetime(2026, 10, 27, tzinfo=timezone.utc)
POLICY = load_permission_policy(Path(__file__).parents[3] / "config" / "agent-permissions.json")

GUARDIAN_BLUEPRINT = Blueprint("bp-guardian", "agent-steward-guardian", (NOW + timedelta(days=44),))
FIXTURE_BLUEPRINT = Blueprint("bp-fixture", "agent-steward-fixture")     # no credential at all
EXPIRING_BLUEPRINT = Blueprint("bp-expiring", "agent-steward-fixture-expiring", (NOW + timedelta(days=6),))
BLUEPRINTS = [GUARDIAN_BLUEPRINT, FIXTURE_BLUEPRINT, EXPIRING_BLUEPRINT]

GUARDIAN = Agent("a-guardian", "agent-steward-guardian-01", "bp-guardian", 1, POLICY["agent-steward-guardian-01"])
NOROLE = Agent("a-norole", "agent-steward-guardian-norole-01", "bp-guardian", 1)
DORMANT = Agent("a-dormant", "agent-steward-fixture-dormant-01", "bp-fixture", 1)
OVERPERM = Agent("a-overperm", "agent-steward-fixture-overperm-01", "bp-fixture", 1,
                 frozenset({"Microsoft Graph/User.Read.All"}))
ORPHAN = Agent("a-orphan", "agent-steward-fixture-orphan-01", "bp-fixture", 0)
EXPIRING = Agent("a-expiring", "agent-steward-fixture-expiring-01", "bp-expiring", 1)
AGENTS = [GUARDIAN, NOROLE, DORMANT, OVERPERM, ORPHAN, EXPIRING]

# Only the guardian's agents get tokens. The fixtures' blueprints have no usable credential.
LAST_SIGN_INS = {"a-guardian": NOW - timedelta(hours=1), "a-norole": NOW - timedelta(days=1)}


def names(findings) -> list[str]:
    return sorted(finding.agent_name for finding in findings)


# Orphan

def test_orphan_rule_flags_only_the_agent_without_a_sponsor():
    assert names(find_orphans(AGENTS)) == ["agent-steward-fixture-orphan-01"]


# Expiring credential

def test_expiring_rule_flags_the_agents_of_a_blueprint_whose_last_credential_ends_soon():
    findings = find_expiring_credentials(AGENTS, BLUEPRINTS, NOW)
    assert names(findings) == ["agent-steward-fixture-expiring-01"]
    assert (findings[0].date, findings[0].detail) == (
        "2026-11-02", "blueprint agent-steward-fixture-expiring: its last credential ends within 14 days")


def test_a_rotation_is_not_flagged():
    # The old credential ends in 5 days, but a new one is already in place.
    rotating = replace(EXPIRING_BLUEPRINT, credential_end_times=(NOW + timedelta(days=5), NOW + timedelta(days=300)))
    assert find_expiring_credentials([EXPIRING], [rotating], NOW) == []


def test_an_expired_credential_is_flagged():
    expired = replace(EXPIRING_BLUEPRINT, credential_end_times=(NOW - timedelta(days=1),))
    [finding] = find_expiring_credentials([EXPIRING], [expired], NOW)
    assert (finding.date, finding.detail) == ("2026-10-26", "blueprint agent-steward-fixture-expiring: its last credential has expired")


def test_a_blueprint_without_credentials_is_not_flagged_as_expiring():
    assert find_expiring_credentials([DORMANT, OVERPERM, ORPHAN], [FIXTURE_BLUEPRINT], NOW) == []


# Over-permissioned

def test_over_permissioned_rule_flags_a_permission_outside_the_policy():
    findings = find_over_permissioned(AGENTS, POLICY)
    assert names(findings) == ["agent-steward-fixture-overperm-01"]
    assert findings[0].detail == "holds permissions it does not need: Microsoft Graph/User.Read.All"


def test_an_agent_holding_exactly_its_allowed_permissions_is_not_flagged():
    assert find_over_permissioned([GUARDIAN], POLICY) == []


def test_the_same_role_name_on_another_api_is_a_finding():
    # A role name means nothing without its API.
    guardian = replace(GUARDIAN, permissions=GUARDIAN.permissions | {"some-other-api/Findings.Submit"})
    [finding] = find_over_permissioned([guardian], POLICY)
    assert finding.detail.endswith("some-other-api/Findings.Submit")


# Dormant

def test_dormant_rule_flags_every_agent_without_a_recent_sign_in():
    # All four fixtures: their blueprints give them no way to get a token.
    assert names(find_dormant(AGENTS, LAST_SIGN_INS, NOW)) == [
        "agent-steward-fixture-dormant-01", "agent-steward-fixture-expiring-01",
        "agent-steward-fixture-orphan-01", "agent-steward-fixture-overperm-01",
    ]
    # No sign-in at all in the log, so there is no date to show.
    assert {finding.date for finding in find_dormant(AGENTS, LAST_SIGN_INS, NOW)} == {None}


def test_a_sign_in_older_than_30_days_is_dormant():
    [finding] = find_dormant([GUARDIAN], {"a-guardian": NOW - timedelta(days=31)}, NOW)
    assert (finding.date, finding.detail) == ("2026-09-26", "last sign-in more than 30 days ago")


# All rules

def test_every_fixture_is_flagged_for_its_planned_flaw_and_the_guardian_is_clean():
    findings = run_all_rules(AGENTS, BLUEPRINTS, POLICY, LAST_SIGN_INS, NOW)
    planned = {(finding.agent_name, finding.check) for finding in findings}
    assert {("agent-steward-fixture-orphan-01", "orphan"),
            ("agent-steward-fixture-expiring-01", "expiring_credential"),
            ("agent-steward-fixture-overperm-01", "over_permissioned"),
            ("agent-steward-fixture-dormant-01", "dormant")} <= planned
    assert not [finding for finding in findings if finding.agent_name.startswith("agent-steward-guardian")]
