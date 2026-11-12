# Security controls

This is the project's control register: every security control that is in place, the threat it stops, where it is enforced, and the evidence that it works. Controls that are planned or exist only as written design are listed separately at the end, so nothing here claims more than the code does.

The [threat model](../THREATMODEL.md) works from the other side: it starts from each risk and points to the controls below by their IDs. For the architecture these controls protect, see [Agent Steward architecture](agent-steward-architecture.md).

## Contents

- [In place](#in-place)
  - [Identity and credentials](#identity-and-credentials)
  - [The Identity API](#the-identity-api)
  - [The MCP server](#the-mcp-server)
  - [The executor](#the-executor)
  - [The harness](#the-harness)
  - [Governance and audit](#governance-and-audit)
  - [Repository and supply chain](#repository-and-supply-chain)
- [Planned](#planned)
- [Written design only](#written-design-only)

---

## In place

In the Evidence column, a test is named in plain words, so the tables fit the page: for example, *the loop stops at the round limit from the settings* is the test `test_the_loop_stops_at_the_round_limit_from_the_settings`.

### Identity and credentials

| ID | Control | The threat it stops | Where it is enforced | Evidence |
|---|---|---|---|---|
| C1 | Every agent is an agent identity created from a blueprint, with a named owner and sponsor | Unaccountable agents, and agents that share a user account | Entra ID | [Owner and sponsor](screenshots/phase1-guardian-owner-and-sponsor.png), [setup script](../setup/agent-id/Deploy-AgentIdentities.ps1) |
| C2 | The agent has no credential of its own. The blueprint's certificate is mounted only into the two sidecars: the MCP server's and the executor's | A credential stolen from the agent's code, or from the container the model's requests arrive in | Entra ID, and the `secrets:` entry of the sidecar in Compose | [docker-compose.yml](../docker-compose.yml), [The blueprint's credential](infra.md#the-blueprints-credential) |
| C3 | Each sidecar answers only on loopback, and only one service shares its network namespace: the MCP server, or the executor | Other code on the network getting the agent's tokens | The sidecar (version 1.1 refuses non-loopback callers) and Compose (`network_mode`) | [Local run: the sidecar](infra.md#local-run-the-sidecar) |
| C4 | The model never holds a token: the harness runs outside the sidecar's network namespace and holds only the model key | A prompt-injected model, or a compromised harness, getting an Entra token | Compose: the guardian is on its own network | MCP integration test *the guardian's network cannot reach the token sidecar* ([test_mcp_tools.py](../tests/integration/mcp_server/test_mcp_tools.py), [screenshot](screenshots/mcp-end-to-end-tests.png)) |
| C5 | Each token is for one API: the agent asks for `<app ID>/.default`, and the token carries only its roles on that API | A token for one API being used on another | Entra ID when it issues the token, and each API's audience check (C7) | [The `.default` scope](agent-id.md#app-only-tokens-and-the-default-scope): the guardian's Identity API token carried only `Findings.Submit` |
| C6 | Least privilege: the guardian holds four read-only Graph roles and two Identity API roles, and no Graph write role. Its one write permission is delegated (`AgentIdentity.EnableDisable.All`), so it works only with an approver's token, through OBO, and never beyond that person's own rights. A reviewed policy file lists what each agent may hold. Each MCP tool's calls, and the permission each call needs, are documented ([Tool permissions](tool-permissions.md)) | An agent holding more access than its job needs, and a stolen app-only token that can write | Entra role assignments and the delegated grant, and the over-permissioned rule (C14). Per tool, only in the code: a token carries all the guardian's roles on its API ([why](tool-permissions.md#where-the-guardian-differs)) | [guardian-permissions.bicep](../setup/bicep/guardian-permissions.bicep), [approver-delegation.bicep](../setup/bicep/approver-delegation.bicep), [agent-permissions.json](../config/agent-permissions.json). Read in the tenant on 10 November 2026: the guardian's Graph app roles are the four read roles only. Unit tests *each tool makes exactly its documented calls, each with an app-only token* and *the bicep files grant the guardian exactly the documented application permissions* ([test_tool_permissions.py](../tests/unit/mcp_server/test_tool_permissions.py)) |

### The Identity API

| ID | Control | The threat it stops | Where it is enforced | Evidence |
|---|---|---|---|---|
| C7 | Seven checks on every token, in a fixed order: signature, issuer and tenant, audience, lifetime, agent identity (`xms_act_fct` contains 11), app role, then a log line | Forged, foreign or expired tokens, callers that are not agent identities, and agents without the role | The Identity API ([token_validation.py](../src/identity_api/token_validation.py)) | [What an API must check](tokens.md#what-an-api-must-check), [unit tests](../tests/unit/identity_api/test_token_validation.py), [integration tests with real Entra tokens](../tests/integration/identity_api/test_with_real_tokens.py), [screenshot](screenshots/phase2-integration-tests.png) |
| C26 | The approver check: only a person (`idtyp` is `user`, no agent facets), through the CLI (`azp`), with the scope `AgentDisable.Decide` and the role `AgentDisable.Approve`. The approver's identity comes only from the token | An agent approving its own request, and a person or app with no right to approve | The Identity API ([token_validation.py](../src/identity_api/token_validation.py)) | [Tests](../tests/unit/identity_api/test_token_validation.py), and a real sign-in on 31 October 2026 |
| C27 | A decision is bound and final: a request is reachable only through its own agent's path, leaves `pending` once under a lock, and expires after 24 hours. A second pending request for one agent is refused. An approved request is executed at most once, within 10 minutes of the approval, and only for the person who approved it | A replayed or double decision, an approval used for another agent, and approval fatigue | The Identity API ([disable_requests.py](../src/identity_api/disable_requests.py)) | [Tests](../tests/unit/identity_api/test_disable_requests.py), [route tests](../tests/unit/identity_api/test_disable_routes.py) |
| C28 | What the approver sees cannot be forged: the CLI removes control characters from the model's reason and the agent's name, labels the reason as untrusted, and only the full word `approve` approves | A model or a registered name rewriting the terminal, and an approval by a stray key | The CLI ([approve.py](../src/approve_cli/approve.py)) | [Tests](../tests/unit/approve_cli/test_approve.py) |

### The MCP server

| ID | Control | The threat it stops | Where it is enforced | Evidence |
|---|---|---|---|---|
| C8 | Every tool input is validated before use: `get_agent` accepts only a GUID before it builds a Graph filter | A model, tricked or not, injecting its own query into a Graph request | The MCP server ([server.py](../src/mcp_server/server.py)) | [Unit tests](../tests/unit/mcp_server/test_server.py) and the MCP integration test *`get_agent` rejects an input that is not a GUID* ([test_mcp_tools.py](../tests/integration/mcp_server/test_mcp_tools.py)) |
| C9 | API endpoints come from the MCP server's configuration, never from a tool input | The model sending data to a server of its choice (data exfiltration, request forgery) | The MCP server's tool code | [How a tool reaches its API](mcp.md#3-how-a-tool-reaches-its-api) |
| C10 | The MCP server answers only to its own names (the `Host` header), as protection against DNS rebinding | A web page in a local browser that reaches the MCP server through a domain pointed at `127.0.0.1` | The MCP SDK's transport security settings (`MCP_ALLOWED_HOSTS`) | Checked on 28 October 2026: a request with `Host: evil.example:8100` got `421 Misdirected Request` |
| C11 | Evidence never passes through the model: `scan_registry` sends its findings to the audit log itself | A tricked model or a compromised harness hiding, changing or inventing findings | The MCP server | [ADR-001](decisions.md#adr-001-the-mcp-server-sends-the-findings-to-the-identity-api-never-the-model). In the first live run (28 October 2026), the model's report of 7 findings matched the 7 finding lines in the audit log under the same correlation ID ([a traced run](screenshots/mcp-guardian-chat-trace.png)). Control test *the poisoned agent's finding is audited before any model reads its description* ([test_mcp_tools.py](../tests/integration/mcp_server/test_mcp_tools.py)) |
| C22 | A tool allow-list: the server reads [config/tool-allow-list.json](../config/tool-allow-list.json) at start. `tools/list` shows only the tools on it, and `tools/call` refuses any other name before the tool runs, with the MCP spec's protocol error `-32602` ("Unknown tool") and a "refused" audit line. A blocked tool and a tool that does not exist get the same answer. A missing or wrong file, or a name that is not a tool in the code, stops the server | A model, tricked or not, calling a tool it should not have, and a new tool going live without review | The MCP server ([server.py](../src/mcp_server/server.py), [allow_list.py](../src/mcp_server/allow_list.py)). One list for every caller, because the server does not know who calls it (D1) | Unit tests ([test_allow_list.py](../tests/unit/mcp_server/test_allow_list.py), [test_server.py](../tests/unit/mcp_server/test_server.py)), the MCP integration tests *tools/list returns only the tools on the allow-list* and *a tool off the allow-list is refused with the spec's unknown tool error* ([test_mcp_tools.py](../tests/integration/mcp_server/test_mcp_tools.py)), and the live refusal with its audit line ([screenshot](screenshots/mcp-allow-list-refusal.png)) |
| C24 | A human approval gate: `submit_disable_agent_request` only asks, and nothing is disabled without a clear approval and the approver's own token. A denial, an expired request, an approval not used within 10 minutes, a reused approval or an unreachable service all end in no action. The request, the decision and the write are audited, and the write names the approver and the delegating user | A privileged action without a human decision, and an action nobody can trace to a person | The MCP server asks ([server.py](../src/mcp_server/server.py)), the Identity API decides once ([disable_requests.py](../src/identity_api/disable_requests.py)), and the executor writes ([executor.py](../src/executor/executor.py)) | [MCP server tests](../tests/unit/mcp_server/test_server.py), [executor tests](../tests/unit/executor/test_executor.py). Live run on 31 October 2026, with the earlier design (one tool that waited, and the guardian's own write role): [the decisions](screenshots/fr7-approve-command-decisions.png), [the tenant](screenshots/fr7-agent-disabled-after-approval.png). The current design ran live end to end on 10 November 2026: one approval disabled a fixture with the approver's rights, and the executor's line says `mode: obo` and `confirmed: true` |
| C25 | The checks run twice: `submit_disable_agent_request` refuses the guardian's own agents and any agent that is not dormant, before anyone is asked, and the executor runs the same checks again right before the write. Both use one module, so the two cannot drift apart. Nothing depends on what the model says | A tricked model disabling the guardian, or an active agent, and an agent that became active again after the request | The MCP server and the executor ([disable_checks.py](../src/mcp_server/disable_checks.py)) | [MCP server tests](../tests/unit/mcp_server/test_server.py), [executor tests](../tests/unit/executor/test_executor.py) |

### The executor

| ID | Control | The threat it stops | Where it is enforced | Evidence |
|---|---|---|---|---|
| C30 | The write is kept away from the model's tools: only the executor holds the write code and receives the approver's token. It has its own sidecar, the only one with the OBO settings, and it is on the approval network, which the harness cannot reach. The MCP server holds no write code at all | A bug or an injection path in the MCP server reaching the write, and a compromised harness or MCP server disabling an agent | Compose ([docker-compose.yml](../docker-compose.yml)) and the executor ([executor.py](../src/executor/executor.py), [graph_writer.py](../src/executor/graph_writer.py)) | Unit tests *the MCP server holds no write and takes no approver token* ([test_server.py](../tests/unit/mcp_server/test_server.py)) and *the executor offers only the delegation route* ([test_executor.py](../tests/unit/executor/test_executor.py)) |
| C31 | The approver's token is bound to their own approval: the executor accepts Tc only with the blueprint as audience and `access_as_user` as scope, and only if its `oid` is the person who approved that request. The Identity API checks the same person again when the request is claimed | Someone else's token, an app-only token, or one approver's token used for another person's approval | The executor and the Identity API ([executor.py](../src/executor/executor.py), [disable_requests.py](../src/identity_api/disable_requests.py)) | Unit tests *a token that is not the approver's delegated token disables nothing* ([test_executor.py](../tests/unit/executor/test_executor.py)) and the claim tests ([test_disable_requests.py](../tests/unit/identity_api/test_disable_requests.py)). Live on 10 November 2026: Tc had the blueprint as `aud` and `access_as_user` as `scp`, and the OBO token had the approver as `oid` |

### The harness

| ID | Control | The threat it stops | Where it is enforced | Evidence |
|---|---|---|---|---|
| C12 | A hard limit on model calls per run, even if the model never finishes. The limit is in the settings file (10 today), which may set at most 25. A missing or wrong setting stops the run before the first model call: there is no default | Runaway loops, excessive agency, and runaway cost | The harness ([harness.py](../src/guardian/harness.py)), with its settings in [config/guardian.json](../config/guardian.json), mounted read-only | Unit test *the loop stops at the round limit from the settings*, and the settings-file tests ([test_harness.py](../tests/unit/guardian/test_harness.py)) |
| C13 | The system prompt says that tool results and agent descriptions are data, never instructions. **A weak control:** a prompt can be overridden, so no other control depends on it | Prompt injection, as one layer of defense in depth | The harness's system prompt | [harness.py](../src/guardian/harness.py). Agent eval (NFR3): against a prompt injection planted in a fixture's description, the guardian resisted it and flagged it in 3 of 3 runs ([eval_prompt_injection.py](../tests/agent_eval/eval_prompt_injection.py), [screenshot](screenshots/nfr3-agent-eval-summary.png)). A pass shows that this agent resisted this attack in these runs, not that it cannot be fooled |
| C21 | Strict tool inputs: Anthropic's API makes every tool input the model writes match the tool's schema. It checks the shape only (a string, not a GUID), so the MCP server still checks every input (C8) | Malformed tool input from a confused or tricked model | Anthropic's API, turned on by the harness from [config/guardian.json](../config/guardian.json) (`strict_tool_inputs`) | Unit test *strict tool inputs mark every tool strict and close its schema* ([test_harness.py](../tests/unit/guardian/test_harness.py)) |
| C23 | Fail closed on a rejected tool call: when the MCP server answers `tools/call` with a protocol error, the run stops with an error instead of sending it back to the model | A tricked model probing for tools it was never shown | The harness ([harness.py](../src/guardian/harness.py)) | Unit test *a tool call the MCP server rejects stops the run and never goes back to the model* ([test_harness.py](../tests/unit/guardian/test_harness.py)) |
| C29 | No memory across runs: the conversation is a list inside one run, and it is gone when the run ends. Every run starts again from the system prompt and the operator's task, so nothing the model read in one run reaches the next. If a later version adds memory, this risk comes back, and every memory write would need its own checks | Memory poisoning (OWASP ASI06): an instruction planted in an agent description is saved and comes back in every later run. Today it can affect one run only, and the controls on that run still hold | The harness: the message list is created new in each `run_agent` call ([harness.py:323](../src/guardian/harness.py#L323)), and the guardian has no store, file or memory tool | The code only: no test checks it yet |

### Governance and audit

| ID | Control | The threat it stops | Where it is enforced | Evidence |
|---|---|---|---|---|
| C14 | Four registry rules flag orphan agents (no sponsor), expiring blueprint credentials, over-permissioned agents and dormant agents. Only *successful* sign-ins count as activity | Unaccountable, unused or over-privileged agents, and outages from expired credentials | The MCP server's `scan_registry` tool ([rules.py](../src/mcp_server/rules.py)) | [Unit tests](../tests/unit/mcp_server/test_rules.py), including *a failed sign-in is not activity* in [test_graph_reader.py](../tests/unit/mcp_server/test_graph_reader.py), the MCP integration test against the real tenant, and a [guardian run](screenshots/phase2-guardian-run.png) |
| C15 | One audit line for each decision and each finding, with the agent, its blueprint, the mode and the trace ID. Each disable also gets one line at the executor, naming the agent that acted, the approver and the delegating user | Actions that cannot be traced to one agent, or to the person whose rights they used | The Identity API, and the executor for the write | The live run's 8 audit lines (one decision, 7 findings) under one correlation ID |

### Repository and supply chain

| ID | Control | The threat it stops | Where it is enforced | Evidence |
|---|---|---|---|---|
| C16 | Secrets stay out of git: `secrets/` and `.env` are ignored, and before each commit the staged changes are checked against every value in them | Credentials or tenant data leaking into this public repository | Git, and the commit routine | [.gitignore](../.gitignore) |
| C17 | The model key is given only to containers built from the guardian's image (the guardian and its agent eval), never to the MCP server, the executor, the sidecars or the integration tests | A wider exposure of the model key | Compose (`env_file` on those two services only) | [docker-compose.yml](../docker-compose.yml) |
| C18 | Both sidecars use one image, pinned by digest, and every Python package is pinned by exact version | A changed upstream image or package slipping in unnoticed | Compose and the `requirements.txt` files | [docker-compose.yml](../docker-compose.yml) |
| C19 | Every container runs as a non-root user | More damage after code runs inside a container | The Dockerfiles (`USER nobody`), and Microsoft's sidecar image (seen running as user 1654) | [src/mcp_server/Dockerfile](../src/mcp_server/Dockerfile), [src/executor/Dockerfile](../src/executor/Dockerfile), [src/guardian/Dockerfile](../src/guardian/Dockerfile), [src/identity_api/Dockerfile](../src/identity_api/Dockerfile), [src/approve_cli/Dockerfile](../src/approve_cli/Dockerfile) |
| C20 | A debug port is opt-in only: the MCP Inspector override publishes the MCP port on `127.0.0.1` and only when that file is named | A debug port left open by default | [docker-compose.inspect.yml](../docker-compose.inspect.yml) | The file is never loaded automatically, because it is not named `docker-compose.override.yml` |

---

## Planned

These controls are part of the plan but not built yet. Each moves to [In place](#in-place) with its evidence when it ships.

| Control | The threat it stops |
|---|---|
| One trace ID across the harness, the MCP server, the Identity API and the executor (FR8, NFR4) | Actions that cannot be followed across components |
| Revocation: disabling the agent identity stops its next run, and the gap for tokens already issued is shown (FR11) | An agent that keeps working after it should have been stopped |
| A gitleaks scan of the whole history (NFR6) | A secret that slipped into an earlier commit |

## Written design only

These controls belong to the enterprise design and are described in [Agent architecture](agent-steward-architecture.md#architectural-shortcuts-where-the-guardian-differs). This project does not build them.

| Control | Shortcut it would close |
|---|---|
| OAuth between the harness and the MCP server: the MCP server checks a bearer token | D1: network location only |
| An identity of the MCP server's own, used for downstream calls | D2: the MCP server acts as the guardian |
| A managed identity as the blueprint's credential, so no key exists | D3: a certificate file |
| Workload identity federation for the model provider, so no key exists | D4: an API key in a file |
| Delegated mode with OBO for the other fixes (remove a permission, assign a sponsor) | D5: OBO only for the approved disable |
| A central sink for audit logs and traces | D6: logs stay local |
| Conditional Access for agent identities | Not demonstrated: it needs an Agent 365 licence ([glossary](GLOSSARY.md#core-identity-objects)) |
