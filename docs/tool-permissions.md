# Tool Permissions

This page lists every call each MCP tool makes as the guardian, and the one permission each call needs (NFR1). The executor is listed too: it is not a tool, but it acts as the guardian. The same list is in [config/tool-permissions.json](../config/tool-permissions.json), and a unit test checks it against the calls the code makes and against the guardian's grants, so the page cannot drift from the code.

## Contents

- [The Practice: Least Privilege per Tool](#the-practice-least-privilege-per-tool)
- [How Entra Expresses a Permission](#how-entra-expresses-a-permission)
- [The Tools](#the-tools)
  - [scan_registry](#scan_registry)
  - [get_agent](#get_agent)
  - [submit_disable_agent_request](#submit_disable_agent_request)
- [The Executor](#the-executor)
- [How It Is Checked](#how-it-is-checked)
- [Where the Guardian Differs](#where-the-guardian-differs)

## The Practice: Least Privilege per Tool

**The rule:** each tool gets only the permissions its own calls need. Because a model decides which tool to call, and with which input, a tool's permissions are what a fooled model can do with it. So the permissions are the real limit, not the model's good behaviour ([From microservices to agents](from-microservices-to-agents.md)).

The sources:

- **OWASP:** [LLM06:2025 Excessive Agency](https://genai.owasp.org/llmrisk/llm062025-excessive-agency/) names excessive permissions as one of its three root causes, next to excessive functionality and excessive autonomy. The [threat model](../THREATMODEL.md) maps the guardian's least privilege to ASI03, Identity and Privilege Abuse, in the [OWASP Top 10 for Agentic Applications](https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/).
- **MCP:** the spec's security best practices have a section on [Scope Minimization](https://modelcontextprotocol.io/specification/2025-11-25/basic/security_best_practices#scope-minimization): "Poor scope design increases token compromise impact, elevates user friction, and obscures audit trails." That section is about the token an MCP client sends to the server. The same reason holds for the tokens the server itself uses downstream (*my reading*).
- **Microsoft Graph:** every Graph API page lists the least privileged permission for the call, and says: "Choose the permission or permissions marked as least privileged for this API. Use a higher privileged permission or permissions only if your app requires it." Each permission below is the least privileged one on the call's page.

## How Entra Expresses a Permission

Entra has two kinds of permission, and the guardian uses both.

- **Application permission:** an app role assigned to the agent identity. It arrives in the `roles` claim of an app-only token, with no user involved. Every read and every request below uses one.
- **Delegated permission:** a scope that an admin granted to the client, for users. It arrives in the `scp` claim, and it works only with a user's token, never beyond that user's own rights. The guardian holds one: `AgentIdentity.EnableDisable.All`, for the approved disable, through OBO ([architecture](agent-steward-architecture.md#the-two-phases-propose-then-approve)).

The Identity API's roles are its own. Each of its routes checks one role, in [main.py](../src/identity_api/main.py).

## The Tools

The MCP server runs three tools ([server.py](../src/mcp_server/server.py)). For each, the calls it makes, the permission each call needs, and where that permission is stated. All of them use the guardian's app-only tokens.

### scan_registry

It reads every agent identity in the tenant, applies the four checks, sends the findings to the audit log, and adds the latest disable request of each dormant agent.

| Call | API | Permission | Why | Source |
|---|---|---|---|---|
| `GET /v1.0/servicePrincipals/microsoft.graph.agentIdentity` | Graph | `AgentIdentity.Read.All` | The agent identities, with their sponsors (the orphan check) | [List agentIdentity objects](https://learn.microsoft.com/en-us/graph/api/agentidentity-list?view=graph-rest-1.0) |
| `GET /v1.0/servicePrincipals/{id}/appRoleAssignments` | Graph | `Application.Read.All` | Each agent's own app role assignments (the over-permissioned check) | [List appRoleAssignments granted to a service principal](https://learn.microsoft.com/en-us/graph/api/serviceprincipal-list-approleassignments?view=graph-rest-1.0) |
| `GET /beta/servicePrincipals/{id}/microsoft.graph.agentIdentity/inheritedAppRoleAssignments` | Graph | `Application.Read.All` | The assignments each agent inherits from its blueprint | [List inherited app role assignments](https://learn.microsoft.com/en-us/graph/api/agentidentity-list-inheritedapproleassignments?view=graph-rest-beta) |
| `GET /v1.0/servicePrincipals/{id}` | Graph | `Application.Read.All` | The role names of each API an agent holds roles on | [Get servicePrincipal](https://learn.microsoft.com/en-us/graph/api/serviceprincipal-get?view=graph-rest-1.0) |
| `GET /v1.0/applications/microsoft.graph.agentIdentityBlueprint` | Graph | `AgentIdentityBlueprint.Read.All` | The blueprints and their credentials (the expiring check) | [List agentIdentityBlueprint objects](https://learn.microsoft.com/en-us/graph/api/agentidentityblueprint-list?view=graph-rest-1.0) |
| `GET /beta/auditLogs/signIns` | Graph | `AuditLog.Read.All` | The sign-ins of the last 30 days (the dormant check) | [List signIns](https://learn.microsoft.com/en-us/graph/api/signin-list?view=graph-rest-beta) |
| `POST /findings` | Identity API | `Findings.Submit` | The findings, into the audit log | [main.py](../src/identity_api/main.py) |
| `GET /agents/{agent_id}/disableRequests` | Identity API | `AgentDisable.Request` | The latest disable request for each dormant agent | [main.py](../src/identity_api/main.py) |

**So it needs:** all six of the guardian's application permissions. It is the tool that does most of the work.

### get_agent

It reads one agent identity by its app ID, with the free-text description that the guardian treats as data (NFR3).

| Call | API | Permission | Why | Source |
|---|---|---|---|---|
| `GET /v1.0/servicePrincipals/microsoft.graph.agentIdentity` (filtered by `appId`) | Graph | `AgentIdentity.Read.All` | One agent identity | [List agentIdentity objects](https://learn.microsoft.com/en-us/graph/api/agentidentity-list?view=graph-rest-1.0) |

**So it needs:** one permission.

### submit_disable_agent_request

It checks that the agent may be disabled, then asks an approver. It writes nothing in Graph.

| Call | API | Permission | Why | Source |
|---|---|---|---|---|
| `GET /v1.0/servicePrincipals/microsoft.graph.agentIdentity` (filtered by `appId`) | Graph | `AgentIdentity.Read.All` | The agent and the guardian: the agent is not the guardian's own, and not disabled yet | [List agentIdentity objects](https://learn.microsoft.com/en-us/graph/api/agentidentity-list?view=graph-rest-1.0) |
| `GET /beta/auditLogs/signIns` | Graph | `AuditLog.Read.All` | Is the agent dormant? | [List signIns](https://learn.microsoft.com/en-us/graph/api/signin-list?view=graph-rest-beta) |
| `POST /agents/{agent_id}/disableRequests` | Identity API | `AgentDisable.Request` | The disable request, for an approver | [main.py](../src/identity_api/main.py) |

**So it needs:** three permissions.

## The Executor

The executor runs the approved disable ([executor.py](../src/executor/executor.py)). It runs the same checks as the request tool again, claims the request, writes, and records the outcome. Every call uses the guardian's app-only tokens, except the write, which uses the OBO token.

| Call | API | Permission | Type | Why | Source |
|---|---|---|---|---|---|
| `GET /agents/{agent_id}/disableRequests/{request_id}` | Identity API | `AgentDisable.Request` | Application | The request: approved, and by this approver? | [main.py](../src/identity_api/main.py) |
| `GET /v1.0/servicePrincipals/microsoft.graph.agentIdentity` (filtered by `appId`) | Graph | `AgentIdentity.Read.All` | Application | The same checks as the request tool, and the agent's object ID for the write | [List agentIdentity objects](https://learn.microsoft.com/en-us/graph/api/agentidentity-list?view=graph-rest-1.0) |
| `GET /beta/auditLogs/signIns` | Graph | `AuditLog.Read.All` | Application | Is the agent still dormant? | [List signIns](https://learn.microsoft.com/en-us/graph/api/signin-list?view=graph-rest-beta) |
| `POST /agents/{agent_id}/disableRequests/{request_id}/execution` | Identity API | `AgentDisable.Request` | Application | Claim the request, right before the write | [main.py](../src/identity_api/main.py) |
| `PATCH /beta/servicePrincipals/{id}/microsoft.graph.agentIdentity` | Graph | `AgentIdentity.EnableDisable.All` | Delegated | The disable, with the approver's rights | [Update agentIdentity](https://learn.microsoft.com/en-us/graph/api/agentidentity-update?view=graph-rest-beta) |
| `GET /beta/servicePrincipals/{id}/microsoft.graph.agentIdentity` | Graph | `AgentIdentity.Read.All` | Application | Read `accountEnabled` back, until Graph shows the change | [Get agentIdentity](https://learn.microsoft.com/en-us/graph/api/agentidentity-get?view=graph-rest-1.0) |
| `POST /agents/{agent_id}/disableRequests/{request_id}/outcome` | Identity API | `AgentDisable.Request` | Application | What the write did | [main.py](../src/identity_api/main.py) |

**The write needs the approver's rights too:** a delegated permission never goes beyond the user's own rights. So Graph also checks the approver. The Update agentIdentity page names Agent ID Administrator as the least privileged built-in role for a person who does not own the agent's blueprint. An owner of the blueprint needs no role.

**Why the reads do not use the OBO token:** `AgentIdentity.EnableDisable.All` allows the write but not a read, so the lookup and the read back use the guardian's own read permission ([graph_writer.py](../src/executor/graph_writer.py)).

**The harness holds nothing:** it gets no Entra token at all, only the model's API key ([C4](security-controls.md#identity-and-credentials)).

## How It Is Checked

A unit test, [test_tool_permissions.py](../tests/unit/mcp_server/test_tool_permissions.py), runs offline and checks three things. The live check in the tenant comes on top.

- **The calls are the calls the code makes:** a fake tenant answers the sidecar, Graph and the Identity API, and records every call with the token it carried. Each tool runs for real against it, and so does the executor. Every call must be in the file, with the right token for its permission type, and every call in the file must be made. So a new Graph call in the code fails the test until someone documents its permission (*each tool makes exactly its documented calls, each with an app-only token*; *the executor makes exactly its documented calls and writes only with the OBO token*).
- **The roles are the roles the Identity API demands:** the test reads the API's routes. Every route an agent may call must be in the file, with the role that route checks (*each Identity API call needs the documented role at the API*).
- **The grants hold nothing more:** the application permissions of all the calls together must equal the guardian's entry in [agent-permissions.json](../config/agent-permissions.json) and the grants in [guardian-permissions.bicep](../setup/bicep/guardian-permissions.bicep) and [identity-api.bicep](../setup/bicep/identity-api.bicep). The delegated permission must equal the grant in [approver-delegation.bicep](../setup/bicep/approver-delegation.bicep) and the scope the executor's sidecar asks for. And only those three Bicep files may grant the guardian anything.
- **The tenant, live:** the files say what should be granted, but someone can still grant more by hand in the portal. The guardian's own scan flags any agent that holds more than its policy allows, the guardian included, from its direct and its inherited assignments ([C14](security-controls.md#governance-and-audit)). Read in the tenant on 10 November 2026: the guardian's Graph app roles are the four read roles only.

## Where the Guardian Differs

**One token per API, not per tool:** the MCP server asks for `<API>/.default`, so a token carries every role the guardian holds on that API ([The `.default` scope](agent-id.md#app-only-tokens-and-the-default-scope)). Because of this, `get_agent` needs one Graph permission, but its Graph token carries all four. The executor's Identity API token carries `Findings.Submit`, which it never uses.

**So per-tool least privilege is enforced by the code, not by the token:** each tool makes only its documented calls, and the test proves it. If the MCP server itself were taken over, though, the attacker would hold all six application permissions: everything the scan can read, plus findings and disable requests. They would still hold no write, because the only write permission is delegated and needs an approver's token.

**The usual fix is a separate identity:** Entra cannot narrow an app-only token per request, so different access needs a different identity ([agent-id.md](agent-id.md#app-only-tokens-and-the-default-scope)). The first step is in Future work: one identity per workload, so the MCP server and the executor stop sharing the guardian's (D2, [DESIGN-NOTES.md](DESIGN-NOTES.md#future-work)). One identity per set of tool permissions would go further. When a user is present, MCP's own answer is a small first scope and step-up per privileged tool ([Scope Minimization](https://modelcontextprotocol.io/specification/2025-11-25/basic/security_best_practices#scope-minimization)).

**One permission is smaller than Graph's docs ask for:** the scan reads each agent's sponsors with `AgentIdentity.Read.All`. Graph's docs listed only `AgentIdentity.ReadWrite.All` for sponsors, but the read-only permission reads them too (tested on 26 October 2026, [guardian-permissions.bicep](../setup/bicep/guardian-permissions.bicep)). So the guardian holds the smaller one.
