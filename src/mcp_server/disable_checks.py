"""The checks before an agent is disabled (FR7): the same rules as the scan, in two places.

The MCP server runs them before anyone is asked, and the executor runs them again right before
the write, because time has passed since the request. Both call this one module, so the two can
never drift apart. Plain Python with no MCP SDK and no web framework, so it has offline tests (NFR8).
"""

import asyncio
import os
import re
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from graph_reader import AgentDetails, GraphClient, fetch_agent_details, fetch_agents, fetch_blueprints, fetch_last_sign_ins
from rules import Agent, Finding, load_permission_policy, run_all_rules

GUID = re.compile(r"^[0-9a-fA-F]{8}-([0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$")
POLICY_PATH = Path(os.environ.get("POLICY_PATH", "/config/agent-permissions.json"))


def find_all(graph: GraphClient, now: datetime, app_id: str | None = None) -> tuple[list[Agent], list[Finding]]:
    """Read the tenant, or only the agent with app_id, and apply every rule. scan_registry and the
    two checks before a disable all call this one function, so they cannot drift apart."""
    agents = fetch_agents(graph, app_id)
    findings = run_all_rules(agents, fetch_blueprints(graph), load_permission_policy(POLICY_PATH),
                             fetch_last_sign_ins(graph, now), now)
    return agents, findings


def findings_for(graph: GraphClient, app_id: str, now: datetime) -> list[Finding]:
    """The agent's findings right now: the same rules as scan_registry, so a disable does not
    depend on what the model says."""
    return find_all(graph, now, app_id)[1]


def is_protected(graph: GraphClient, agent: AgentDetails, guardian_app_id: str) -> bool:
    """The guardian's own agents: the guardian itself and the others of its blueprint. If the
    guardian cannot be read, every agent counts as protected (fail closed)."""
    guardian = fetch_agent_details(graph, guardian_app_id)
    return guardian is None or agent.blueprint_app_id == guardian.blueprint_app_id


async def check_can_be_disabled(graph: GraphClient, app_id: str, guardian_app_id: str,
                                audit: Callable[..., None]) -> tuple[AgentDetails | None, list[Finding], str | None]:
    """Returns the agent, its findings (each one a reason to disable it), and the refusal text,
    which is None when the agent can be disabled. Writes a disable_refused line for the guardian's
    own agents, agents that are already disabled, and agents with no finding."""
    # app_id goes into a Graph filter, so only a GUID passes.
    if not GUID.fullmatch(app_id):
        return None, [], "app_id must be an agent identity's app ID (a GUID)"
    agent = await asyncio.to_thread(fetch_agent_details, graph, app_id)
    if agent is None:
        return None, [], f"No agent identity has the app ID {app_id}"
    if await asyncio.to_thread(is_protected, graph, agent, guardian_app_id):
        audit(event="disable_refused", agent=app_id, reason="protected")
        return agent, [], "Refused: this agent belongs to the guardian."
    if not agent.enabled:
        audit(event="disable_refused", agent=app_id, reason="already disabled")
        return agent, [], "Refused: this agent is already disabled."
    findings = await asyncio.to_thread(findings_for, graph, app_id, datetime.now(timezone.utc))
    if not findings:
        audit(event="disable_refused", agent=app_id, reason="no finding")
        return agent, [], "Refused: no rule flags this agent, so there is nothing to fix."
    return agent, findings, None
