"""Reads the tenant's agents from Microsoft Graph, in the shapes the rules expect (FR3).

Two layers:
- GraphClient and the fetch_* functions call Graph. They are tested live.
- The parse_* functions and permission_names turn Graph's JSON into the rules' shapes.
  They have no network access, so they are unit-tested with sample JSON (NFR8).

Every call uses the guardian's read-only Graph permissions (setup/bicep/guardian-permissions.bicep).
"""

import json
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import datetime

from rules import DORMANT_AFTER, Agent, Blueprint

GRAPH = "https://graph.microsoft.com"
AGENT_SIGN_IN = "agenticAppInstance"   # agent.agentType of an agent identity's own token request (T2)
NO_ROLE = "00000000-0000-0000-0000-000000000000"   # appRoleId of a plain assignment that grants no role


class GraphClient:
    def __init__(self, get_token: Callable[[], str]):
        self._headers = {"Authorization": f"Bearer {get_token()}"}

    def get(self, path: str, params: dict | None = None) -> dict:
        url = GRAPH + path + ("?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote) if params else "")
        with urllib.request.urlopen(urllib.request.Request(url, headers=self._headers), timeout=60) as response:
            return json.load(response)

    def get_all(self, path: str, params: dict | None = None) -> list[dict]:
        """Follow Graph's pages until the last one."""
        page = self.get(path, params)
        items = page["value"]
        while next_link := page.get("@odata.nextLink"):
            with urllib.request.urlopen(urllib.request.Request(next_link, headers=self._headers), timeout=60) as response:
                page = json.load(response)
            items += page["value"]
        return items


# Translation: Graph JSON -> the rules' shapes

def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def parse_blueprints(items: list[dict]) -> list[Blueprint]:
    return [Blueprint(item["appId"], item["displayName"],
                      tuple(parse_time(credential["endDateTime"])
                            for credential in item.get("passwordCredentials", []) + item.get("keyCredentials", [])))
            for item in items]


def parse_agents(items: list[dict], permissions: dict[str, frozenset[str]]) -> list[Agent]:
    """permissions holds each agent's permissions, by the agent's object ID."""
    return [Agent(item["appId"], item["displayName"], item["agentIdentityBlueprintId"],
                  len(item.get("sponsors", [])), permissions.get(item["id"], frozenset()))
            for item in items]


def permission_names(assignments: list[dict], role_values: dict[str, dict[str, str]]) -> frozenset[str]:
    """Turn app role assignments into "<API>/<role>" names.

    An assignment stores only the role's ID, so role_values maps each API's service principal
    ID to its roles, from ID to name. A role that cannot be named keeps its ID, so it is still
    flagged rather than hidden.
    """
    names = set()
    for assignment in assignments:
        if assignment["appRoleId"] == NO_ROLE:
            continue
        role = role_values.get(assignment["resourceId"], {}).get(assignment["appRoleId"], assignment["appRoleId"])
        names.add(f"{assignment['resourceDisplayName']}/{role}")
    return frozenset(names)


def parse_last_sign_ins(events: list[dict]) -> dict[str, datetime]:
    """Each agent identity's newest sign-in, by app ID. Its blueprint's sign-ins do not count."""
    last: dict[str, datetime] = {}
    for event in events:
        if (event.get("agent") or {}).get("agentType") != AGENT_SIGN_IN:
            continue
        when = parse_time(event["createdDateTime"])
        if event["appId"] not in last or when > last[event["appId"]]:
            last[event["appId"]] = when
    return last


# Fetching: these call Graph

def fetch_blueprints(client: GraphClient) -> list[Blueprint]:
    return parse_blueprints(client.get_all("/v1.0/applications/microsoft.graph.agentIdentityBlueprint",
                                           {"$select": "appId,displayName,passwordCredentials,keyCredentials"}))


def fetch_agents(client: GraphClient) -> list[Agent]:
    items = client.get_all("/v1.0/servicePrincipals/microsoft.graph.agentIdentity",
                           {"$select": "id,appId,displayName,agentIdentityBlueprintId",
                            "$expand": "sponsors($select=id)"})
    role_values: dict[str, dict[str, str]] = {}
    permissions = {}
    for item in items:
        # Direct grants, plus grants inherited from the blueprint. Inherited ones never show
        # on the agent itself, only in its tokens (Graph beta lists them).
        assignments = (client.get_all(f"/v1.0/servicePrincipals/{item['id']}/appRoleAssignments")
                       + client.get_all(f"/beta/servicePrincipals/{item['id']}/microsoft.graph.agentIdentity"
                                        "/inheritedAppRoleAssignments"))
        for resource_id in {assignment["resourceId"] for assignment in assignments} - role_values.keys():
            api = client.get(f"/v1.0/servicePrincipals/{resource_id}", {"$select": "appRoles"})
            role_values[resource_id] = {role["id"]: role["value"] for role in api["appRoles"]}
        permissions[item["id"]] = permission_names(assignments, role_values)
    return parse_agents(items, permissions)


def fetch_last_sign_ins(client: GraphClient, now: datetime) -> dict[str, datetime]:
    since = (now - DORMANT_AFTER).strftime("%Y-%m-%dT%H:%M:%SZ")
    events = client.get_all("/beta/auditLogs/signIns", {
        "$filter": f"signInEventTypes/any(t: t eq 'servicePrincipal') and createdDateTime ge {since}",
        "$top": "1000",
    })
    return parse_last_sign_ins(events)
