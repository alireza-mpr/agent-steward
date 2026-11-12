"""Unit tests for least privilege per tool (NFR1, NFR8): each MCP tool documents the exact permission
it needs, and the guardian holds nothing beyond that documented set (docs/tool-permissions.md).

No network: a fake tenant answers the sidecar, Graph and the Identity API, and records every call
with the token it carried. Each tool runs for real against it, and so does the executor, so the test
sees the calls the code makes, not a list someone wrote. The documented set
(config/tool-permissions.json) is then compared with the grants: the policy file, the Bicep files
and the sidecar's settings.
"""

import ast
import asyncio
import io
import json
import re
import urllib.parse
import urllib.request
from pathlib import Path

import pytest
from mcp import Client

import executor
import server
from allow_list import load_allow_list

ROOT = Path(__file__).parents[3]
DOCUMENTED = json.loads((ROOT / "config" / "tool-permissions.json").read_text())
GUARDIAN_NAME = "agent-steward-guardian-01"
GRAPH, IDENTITY_API = "Microsoft Graph", "agent-steward-identity-api"

GUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
AGENT = "11111111-2222-3333-4444-555555555555"      # dormant, and not one of the guardian's agents
GUARDIAN = "99999999-2222-3333-4444-555555555555"
BLUEPRINT_OF = {AGENT: "66666666-7777-8888-9999-000000000000", GUARDIAN: "aaaaaaaa-7777-8888-9999-000000000000"}
GRAPH_PRINCIPAL = "33333333-4444-5555-6666-777777777777"   # Graph's service principal in the fake tenant
ROLE = "44444444-5555-6666-7777-888888888888"
REQUEST = "12345678-aaaa-bbbb-cccc-1234567890ab"
APPROVER = "dddddddd-1111-2222-3333-444444444444"

# The token the fake sidecar hands out for each downstream API (docker-compose.yml, DownstreamApis).
TOKEN_FOR = {(GRAPH, "application"): "app-only:graph",
             (IDENTITY_API, "application"): "app-only:identity-api",
             (GRAPH, "delegated"): "obo:graph-delegated"}

ARGUMENTS = {"scan_registry": {},
             "get_agent": {"app_id": AGENT},
             "submit_disable_agent_request": {"app_id": AGENT, "reason": "No sign-in for 30 days."}}


# The fake tenant

def agent_identity(app_id: str) -> dict:
    # For an agent identity, the object ID equals the app ID.
    return {"id": app_id, "appId": app_id, "displayName": f"agent-{app_id[:8]}",
            "agentIdentityBlueprintId": BLUEPRINT_OF[app_id], "accountEnabled": True, "sponsors": [],
            "description": ""}


def disable_request(status: str) -> dict:
    decided = {"decidedBy": {"id": APPROVER, "name": "An Approver"}} if status != "pending" else {}
    return {"id": REQUEST, "agentId": AGENT, "status": status, **decided}


def agent_identities(query: dict) -> dict:
    asked = re.search(r"appId eq '([^']+)'", query.get("$filter", ""))
    return {"value": [agent_identity(app_id) for app_id in ([asked.group(1)] if asked else [AGENT, GUARDIAN])]}


# (API, method, path) -> the answer. Paths use the same placeholders as config/tool-permissions.json.
ANSWERS = {
    (GRAPH, "GET", "/v1.0/servicePrincipals/microsoft.graph.agentIdentity"): agent_identities,
    (GRAPH, "GET", "/v1.0/servicePrincipals/{id}/appRoleAssignments"):
        {"value": [{"resourceId": GRAPH_PRINCIPAL, "appRoleId": ROLE, "resourceDisplayName": GRAPH}]},
    (GRAPH, "GET", "/beta/servicePrincipals/{id}/microsoft.graph.agentIdentity/inheritedAppRoleAssignments"): {"value": []},
    (GRAPH, "GET", "/v1.0/servicePrincipals/{id}"): {"appRoles": [{"id": ROLE, "value": "AgentIdentity.Read.All"}]},
    (GRAPH, "GET", "/v1.0/applications/microsoft.graph.agentIdentityBlueprint"): {"value": []},
    (GRAPH, "GET", "/beta/auditLogs/signIns"): {"value": []},   # no sign-in: every agent is dormant
    (GRAPH, "PATCH", "/beta/servicePrincipals/{id}/microsoft.graph.agentIdentity"): None,   # 204
    (GRAPH, "GET", "/beta/servicePrincipals/{id}/microsoft.graph.agentIdentity"): {"accountEnabled": False},
    (IDENTITY_API, "POST", "/findings"): {"accepted": 0},
    (IDENTITY_API, "GET", "/agents/{agent_id}/disableRequests"): {"value": []},
    (IDENTITY_API, "POST", "/agents/{agent_id}/disableRequests"): disable_request("pending"),
    (IDENTITY_API, "GET", "/agents/{agent_id}/disableRequests/{request_id}"): disable_request("approved"),
    (IDENTITY_API, "POST", "/agents/{agent_id}/disableRequests/{request_id}/execution"): disable_request("running"),
    (IDENTITY_API, "POST", "/agents/{agent_id}/disableRequests/{request_id}/outcome"): disable_request("succeeded"),
}


def path_pattern(path: str) -> re.Pattern:
    """A documented path as a pattern: each {placeholder} stands for one GUID."""
    return re.compile(GUID.join(re.escape(part) for part in re.split(r"\{\w+\}", path)))


def matches(api: str, method: str, path: str, keys) -> list:
    return [key for key in keys if key[0] == api and key[1] == method and path_pattern(key[2]).fullmatch(path)]


class FakeTenant:
    """Stands in for urllib.request.urlopen. Records each call to Graph and the Identity API as
    (API, method, path, token). The sidecar's calls are the token source, so they are not recorded."""

    def __init__(self):
        self.calls: list[tuple[str, str, str, str]] = []
        self.sidecars = {urllib.parse.urlsplit(module.SIDECAR_URL).netloc for module in (server, executor)}
        self.apis = {"graph.microsoft.com": GRAPH,
                     **{urllib.parse.urlsplit(module.API_URL).netloc: IDENTITY_API for module in (server, executor)}}

    def urlopen(self, request, timeout=None):
        request = request if isinstance(request, urllib.request.Request) else urllib.request.Request(request)
        url = urllib.parse.urlsplit(request.full_url)
        token = (request.get_header("Authorization") or "").partition(" ")[2]
        if url.netloc in self.sidecars:
            body = self.sidecar(url.path)
        else:
            api, method = self.apis[url.netloc], request.get_method()
            self.calls.append((api, method, url.path, token))
            [key] = matches(api, method, url.path, ANSWERS)
            body = ANSWERS[key]
            body = body(dict(urllib.parse.parse_qsl(url.query))) if callable(body) else body
        return io.BytesIO(b"" if body is None else json.dumps(body).encode())

    @staticmethod
    def sidecar(path: str) -> dict:
        if path == "/Validate":   # the approver's token (Tc), checked by the executor's sidecar
            return {"claims": {"oid": APPROVER, "scp": "access_as_user"}}
        api = path.rpartition("/")[2]
        kind = "obo" if path.startswith("/AuthorizationHeader/") else "app-only"
        return {"authorizationHeader": f"Bearer {kind}:{api}"}


@pytest.fixture
def tenant(monkeypatch):
    fake = FakeTenant()
    monkeypatch.setattr(urllib.request, "urlopen", fake.urlopen)
    monkeypatch.setenv("GUARDIAN_AGENT_APP_ID", GUARDIAN)
    monkeypatch.setattr(server, "POLICY_PATH", ROOT / "config" / "agent-permissions.json")
    registered = asyncio.run(server.mcp.registered_tool_names())
    monkeypatch.setattr(server.mcp, "allowed_tools",
                        load_allow_list(ROOT / "config" / "tool-allow-list.json", registered))
    return fake


def run_tool(name: str):
    async def run():
        async with Client(server.mcp) as client:
            return await client.call_tool(name, ARGUMENTS[name])
    return asyncio.run(run())


def check_calls(calls: list[tuple[str, str, str, str]], documented: list[dict]) -> None:
    """Every call is documented, with the token its permission type needs, and every documented
    call was made: no undocumented call, and no stale entry."""
    entries = {(entry["permission"].partition("/")[0], *entry["call"].split(" ")): entry for entry in documented}
    made = set()
    for api, method, path, token in calls:
        found = matches(api, method, path, entries)
        assert found, f"an undocumented call: {method} {path} at {api}"
        [key] = found
        assert token == TOKEN_FOR[(api, entries[key]["type"])], f"{method} {path} carried the wrong token"
        made.add(key)
    assert made == set(entries), f"documented but never made: {set(entries) - made}"


# The documented calls are the calls the code makes

def test_every_tool_in_the_code_has_a_documented_set_and_nothing_else_has_one():
    assert set(DOCUMENTED["tools"]) == asyncio.run(server.mcp.registered_tool_names())


@pytest.mark.parametrize("tool", sorted(DOCUMENTED["tools"]))
def test_each_tool_makes_exactly_its_documented_calls_each_with_an_app_only_token(tool, tenant):
    outcome = run_tool(tool)

    assert not outcome.is_error, outcome.content[0].text
    check_calls(tenant.calls, DOCUMENTED["tools"][tool])
    assert {entry["type"] for entry in DOCUMENTED["tools"][tool]} == {"application"}


def test_the_executor_makes_exactly_its_documented_calls_and_writes_only_with_the_obo_token(tenant):
    assert asyncio.run(executor.accept_delegation(AGENT, REQUEST, "tc")) == (200, "disabled")

    check_calls(tenant.calls, DOCUMENTED["executor"])
    [write] = [entry for entry in DOCUMENTED["executor"] if entry["type"] == "delegated"]
    assert write["call"].startswith("PATCH ")


def test_each_identity_api_call_needs_the_documented_role_at_the_api():
    # Read from the API's own routes (src/identity_api/main.py): the role each route demands of an agent.
    tree = ast.parse((ROOT / "src" / "identity_api" / "main.py").read_text())

    def role_of(node) -> str | None:
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "agent_with_role":
            return node.args[0].value
        return None

    named = {node.targets[0].id: role_of(node.value) for node in tree.body
             if isinstance(node, ast.Assign) and role_of(node.value)}
    routes = {}
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if (isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute)
                    and getattr(decorator.func.value, "id", None) == "app"):
                for default in node.args.defaults:
                    if isinstance(default, ast.Call) and getattr(default.func, "id", None) == "Depends":
                        role = role_of(default.args[0]) or named.get(getattr(default.args[0], "id", None))
                        if role:
                            routes[f"{decorator.func.attr.upper()} {decorator.args[0].value}"] = f"{IDENTITY_API}/{role}"

    documented = {entry["call"]: entry["permission"]
                  for entries in [*DOCUMENTED["tools"].values(), DOCUMENTED["executor"]]
                  for entry in entries if entry["permission"].startswith(IDENTITY_API + "/")}
    # Every route that an agent may call is one the guardian calls, with the role the route demands.
    assert documented == routes


# The documented set is what the guardian is granted

def documented_permissions(permission_type: str) -> set[str]:
    return {entry["permission"]
            for entries in [*DOCUMENTED["tools"].values(), DOCUMENTED["executor"]]
            for entry in entries if entry["type"] == permission_type}


def test_the_policy_file_allows_the_guardian_exactly_the_documented_application_permissions():
    policy = json.loads((ROOT / "config" / "agent-permissions.json").read_text())["agents"]

    assert set(policy[GUARDIAN_NAME]) == documented_permissions("application")


def test_the_bicep_files_grant_the_guardian_exactly_the_documented_application_permissions():
    bicep = ROOT / "setup" / "bicep"
    # The Graph application permissions: the graphPermissions list.
    listed = re.search(r"var graphPermissions = \[(.*?)\n\]", (bicep / "guardian-permissions.bicep").read_text(), re.S)
    graph = {f"{GRAPH}/{name}" for name in re.findall(r"^\s*'([^']+)'", listed.group(1), re.M)}
    # The Identity API roles: each app role assignment to the guardian, by the role's variable.
    text = (bicep / "identity-api.bicep").read_text()
    role_values = dict(re.findall(r"var (\w+) = \{[^}]*?value: '([^']+)'", text, re.S))
    assigned = [re.search(r"appRoleId: (\w+)\.id", block).group(1)
                for block in re.findall(r"'Microsoft\.Graph/appRoleAssignedTo@[^']+' = \{([^}]*)\}", text)
                if "principalId: guardianAgentId" in block]
    identity_api = {f"{IDENTITY_API}/{role_values[variable]}" for variable in assigned}

    assert graph | identity_api == documented_permissions("application")


def test_the_one_delegated_permission_is_the_one_granted_to_the_guardian_and_asked_for_by_its_sidecar():
    grants = re.findall(r"'Microsoft\.Graph/oauth2PermissionGrants@[^']+' = \{([^}]*)\}",
                        (ROOT / "setup" / "bicep" / "approver-delegation.bicep").read_text())
    scopes = [re.search(r"scope: '([^']+)'", block).group(1) for block in grants if "clientId: guardianAgentId" in block]
    granted = {f"{GRAPH}/{scope}" for scope in scopes}
    asked = re.findall(r"DownstreamApis__graph-delegated__Scopes__\d+: https://graph\.microsoft\.com/(\S+)",
                       (ROOT / "docker-compose.yml").read_text())

    assert documented_permissions("delegated") == granted == {f"{GRAPH}/{scope}" for scope in asked}


def test_only_the_three_known_bicep_files_grant_the_guardian_anything():
    # A grant added in a new file would bypass the two tests above.
    granting = {path.name for path in (ROOT / "setup" / "bicep").glob("*.bicep")
                if re.search(r"(principalId|clientId): guardianAgentId", path.read_text())}

    assert granting == {"guardian-permissions.bicep", "identity-api.bicep", "approver-delegation.bicep"}
