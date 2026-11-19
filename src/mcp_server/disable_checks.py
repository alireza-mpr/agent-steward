"""The checks before an agent is disabled (FR7): the same rule in two places.

The MCP server runs them before anyone is asked, and the executor runs them again right before
the write, because time has passed since the request. Both call this one module, so the two can
never drift apart. Plain Python with no MCP SDK and no web framework, so it has offline tests (NFR8).
"""

import asyncio
import re
from collections.abc import Callable
from datetime import datetime, timezone

from graph_reader import AgentDetails, GraphClient, fetch_agent_details, fetch_last_sign_ins
from rules import Agent, Finding, find_dormant, find_poisoned_descriptions

GUID = re.compile(r"^[0-9a-fA-F]{8}-([0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$")


def is_protected(graph: GraphClient, agent: AgentDetails, guardian_app_id: str) -> bool:
    """The guardian's own agents: the guardian itself and the others of its blueprint. If the
    guardian cannot be read, every agent counts as protected (fail closed)."""
    guardian = fetch_agent_details(graph, guardian_app_id)
    return guardian is None or agent.blueprint_app_id == guardian.blueprint_app_id


def plain_agent(agent: AgentDetails) -> Agent:
    return Agent(agent.app_id, agent.name, agent.blueprint_app_id, sponsor_count=0, enabled=agent.enabled,
                 description=agent.description)


def dormant_finding(graph: GraphClient, agent: AgentDetails, now: datetime) -> Finding | None:
    """The same rule as scan_registry, so a disable does not depend on what the model says."""
    return next(iter(find_dormant([plain_agent(agent)], fetch_last_sign_ins(graph, now), now)), None)


def poisoned_finding(agent: AgentDetails) -> Finding | None:
    """The same rule as scan_registry, on the description just read from Graph."""
    return next(iter(find_poisoned_descriptions([plain_agent(agent)])), None)


async def check_can_be_disabled(graph: GraphClient, app_id: str, guardian_app_id: str,
                                audit: Callable[..., None]) -> tuple[AgentDetails | None, list[Finding], str | None]:
    """Returns the agent, its reasons to be disabled (a dormant finding, a poisoned description
    finding, or both), and the refusal text, which is None when the agent can be disabled. Writes a
    disable_refused line for the guardian's own agents, agents that are already disabled, and
    agents with no reason to be disabled."""
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
    dormant = await asyncio.to_thread(dormant_finding, graph, agent, datetime.now(timezone.utc))
    reasons = [finding for finding in (dormant, poisoned_finding(agent)) if finding]
    if not reasons:
        audit(event="disable_refused", agent=app_id, reason="not dormant, not poisoned")
        return agent, [], ("Refused: this agent signed in within the last 30 days, "
                           "and its description shows no sign of a prompt injection.")
    return agent, reasons, None
