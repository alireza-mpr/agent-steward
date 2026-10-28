"""The guardian's four governance rules (FR3).

Each rule takes plain data that the guardian read from Microsoft Graph, and returns findings in
the shape the Identity API accepts. There is no network access here, so the rules can be tested
offline (NFR8).
"""

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

EXPIRING_WITHIN = timedelta(days=14)
DORMANT_AFTER = timedelta(days=30)   # the sign-in log keeps 30 days, so this is the longest we can see


@dataclass(frozen=True)
class Agent:
    app_id: str
    name: str
    blueprint_app_id: str                   # agentIdentityBlueprintId
    sponsor_count: int
    permissions: frozenset[str] = frozenset()   # "<API>/<role>", for example "Microsoft Graph/User.Read.All"


@dataclass(frozen=True)
class Blueprint:
    app_id: str
    name: str
    credential_end_times: tuple[datetime, ...] = ()   # one per secret or certificate


@dataclass(frozen=True)
class Finding:
    agent_app_id: str
    agent_name: str
    check: str      # orphan, expiring_credential, over_permissioned or dormant
    detail: str
    date: str | None = None   # YYYY-MM-DD: when the credential ends, or when the agent last got a token


def load_permission_policy(path: Path) -> dict[str, frozenset[str]]:
    """The permissions each agent may hold, by display name. An agent not listed may hold nothing."""
    agents = json.loads(path.read_text())["agents"]
    return {name: frozenset(permissions) for name, permissions in agents.items()}


def find_orphans(agents: list[Agent]) -> list[Finding]:
    return [Finding(agent.app_id, agent.name, "orphan", "no sponsor: nobody is accountable for this agent")
            for agent in agents if agent.sponsor_count == 0]


def find_expiring_credentials(agents: list[Agent], blueprints: list[Blueprint], now: datetime) -> list[Finding]:
    """Flag the agents of a blueprint whose last credential ends within 14 days, or has ended.

    Agents have no credential of their own: they get their tokens through their blueprint's.
    So only the blueprint's last credential matters. An older one that expires during a
    rotation is fine. A blueprint with no credential at all is not flagged here: nothing can
    expire, and its agents cannot get tokens at all, which the dormant rule catches.
    """
    by_app_id = {blueprint.app_id: blueprint for blueprint in blueprints}
    findings = []
    for agent in agents:
        blueprint = by_app_id.get(agent.blueprint_app_id)
        if not blueprint or not blueprint.credential_end_times:
            continue
        last_end = max(blueprint.credential_end_times)
        if last_end <= now:
            detail = f"blueprint {blueprint.name}: its last credential has expired"
        elif last_end - now <= EXPIRING_WITHIN:
            detail = f"blueprint {blueprint.name}: its last credential ends within 14 days"
        else:
            continue
        findings.append(Finding(agent.app_id, agent.name, "expiring_credential", detail, f"{last_end:%Y-%m-%d}"))
    return findings


def find_over_permissioned(agents: list[Agent], policy: dict[str, frozenset[str]]) -> list[Finding]:
    """Flag every permission an agent holds that the policy does not list for it.

    A permission is "<API>/<role>": a role name means nothing without its API.
    """
    findings = []
    for agent in agents:
        extra = sorted(agent.permissions - policy.get(agent.name, frozenset()))
        if extra:
            findings.append(Finding(agent.app_id, agent.name, "over_permissioned",
                                    f"holds permissions it does not need: {', '.join(extra)}"))
    return findings


def find_dormant(agents: list[Agent], last_sign_ins: dict[str, datetime], now: datetime) -> list[Finding]:
    """Flag agents that got no token in the last 30 days.

    last_sign_ins holds each agent identity's own last sign-in (its T2 requests), not its
    blueprint's: one blueprint signs in for all its agents.
    """
    findings = []
    for agent in agents:
        last = last_sign_ins.get(agent.app_id)
        if last is None:
            detail = "no sign-in in the last 30 days"
        elif now - last > DORMANT_AFTER:
            detail = "last sign-in more than 30 days ago"
        else:
            continue
        findings.append(Finding(agent.app_id, agent.name, "dormant", detail, f"{last:%Y-%m-%d}" if last else None))
    return findings


def run_all_rules(agents: list[Agent], blueprints: list[Blueprint], policy: dict[str, frozenset[str]],
                  last_sign_ins: dict[str, datetime], now: datetime) -> list[Finding]:
    return (find_orphans(agents)
            + find_expiring_credentials(agents, blueprints, now)
            + find_over_permissioned(agents, policy)
            + find_dormant(agents, last_sign_ins, now))
