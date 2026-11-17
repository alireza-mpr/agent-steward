# Agent Steward

An AI agent that governs other AI agents in a Microsoft Entra tenant.

It finds risky agents on its own, in four categories:

- 😴 dormant (no sign-in for 30 days),
- 👻 orphan (no sponsor),
- ⏳ expiring credentials,
- 🔑 excessive permissions.

In short:
- [🤖] The agent writes a report, and submits a request to disable each risky agent, **_autonomously_**.
- [🧑‍💼⚖️] Then a person with the approver role signs in, reviews the pending disable requests, and approves or denies each one.
- [🤖(🧑‍💼)] After an approval, the agent runs the disable itself, with the approver's delegated rights, not its own: **_on behalf of the approver_**.

In summary: **the agent proposes, the approver decides, and the agent executes on behalf of the approver**.

![How Agent Steward works: the agent scans Entra and proposes, the approver decides, and the agent disables with the approver's rights](docs/images/overview.svg)

## Contents

- [What It Demonstrates](#what-it-demonstrates)
- [How It Works](#how-it-works)
  - [The Agents](#the-agents)
- [Functional and Non-functional requirements](#functional-and-non-functional-requirements)
  - [Functional](#functional)
  - [Non-functional](#non-functional)
  - [Written Design Only](#written-design-only)
- [Security Controls at a Glance](#security-controls-at-a-glance)
- [Tech Stack](#tech-stack)
- [Read More](#read-more)
- [Repository Layout](#repository-layout)

## What It Demonstrates

The point of this project: demonstrate, end to end, how an agent can be **identified, authenticated, authorized, constrained, delegated, observed and revoked** using Microsoft Entra Agent ID - with Zero Trust and least privilege applied at every step:

- **Identified:** the guardian is an agent identity created from a blueprint, with a named owner and sponsor. It uses no shared user account and no plain service principal.
- **Authenticated:** it never holds a credential. A token sidecar holds the blueprint's certificate and gets each token for it.
- **Authorized:** it holds four read-only Graph permissions and two roles at the Identity API. Its one write permission is delegated, so it works only with an approver's token.
- **Constrained:** the model can call only allow-listed tools, every tool input is checked, and every disable needs a person's approval.
- **Delegated:** after an approval, the disable runs with the approver's own rights. The executor exchanges the approver's token with the On-Behalf-Of flow (OBO), so the guardian needs no write permission of its own.
- **Observed:** every service sends OpenTelemetry traces, and every decision writes an audit line that carries the trace ID.
- **Revoked:** written design only. Disabling an agent stops new tokens, and the options for tokens already issued are in [DESIGN-NOTES.md](docs/DESIGN-NOTES.md#7-revoking-live-tokens).

## How It Works

The guardian works in two phases. In the **propose phase** it scans on its own, as itself. In the **approve phase** a person decides, and the agent runs the fix with that person's rights.

**The propose phase:**

1. **Start:** someone starts the guardian. In production it would run on a schedule.
2. **Scan:** Claude calls the MCP tool `scan_registry`. The MCP server reads every agent identity from Microsoft Graph with the guardian's app-only, read-only token, and applies four rules: orphan, expiring credential, over-permissioned and dormant.
3. **Audit:** the MCP server sends the findings to the Identity API's audit log itself, so they never pass through the model.
4. **Read:** Claude may read one agent with `get_agent`. An agent's description is treated as data, never as instructions.
5. **Propose:** for a dormant agent, Claude calls `submit_disable_agent_request`. The MCP server checks the agent again and creates a pending request at the Identity API. Nothing is disabled yet.

**The approve phase:**

1. **Decide:** an approver signs in with the approve CLI and approves, denies or skips each pending request.
2. **Lend rights:** right after an approval, the CLI sends the approver's token to the executor.
3. **Check and claim:** the executor checks that this person approved this request, checks the agent again, and claims the request at the Identity API.
4. **Disable:** the executor exchanges the approver's token with OBO and disables the agent with the approver's own rights. Its audit line names both the guardian and the approver.

```mermaid
flowchart LR
    subgraph Propose["Propose phase: autonomous, as the guardian"]
        Harness["Harness + Claude<br/>(holds no token)"] -- "tool calls (MCP)" --> MCP["MCP server<br/>allow-list, checks, requests"]
        MCP -- "loopback" --> Sidecar["Token sidecar<br/>holds the certificate"]
    end
    subgraph Approve["Approve phase: a person, then OBO"]
        Approver["Approver<br/>(approve CLI)"] -- "token, after an approval" --> Exec["Executor<br/>the one write"]
    end
    Sidecar -- "app-only tokens" --> Entra[("Entra ID")]
    Exec -- "OBO exchange" --> Entra
    MCP -- "reads" --> Graph[("Microsoft Graph")]
    MCP -- "findings, disable requests" --> API["Identity API<br/>token checks, approvals, audit"]
    Approver -- "decision" --> API
    Exec -- "claims the request" --> API
    Exec -- "disable, with the<br/>approver's rights" --> Graph
    Propose -. "traces, audit lines" .-> Tel["Telemetry<br/>Jaeger, Loki, Grafana"]
    Approve -.-> Tel
    API -.-> Tel
```

### The Agents

- **The guardian:** the agent that governs, and the only one that uses a model.
- **Five fixture agents:** the agents it governs. They are registry entries with planted flaws: one each with no sponsor, with an expiring credential, over-permissioned, dormant, and with a prompt injection in its description. They run no code.

## Functional and Non-functional requirements

The project is built to demonstrate the below requirements:

Legend: 
- ✅: done
- ▶️: in progress
- 📄: design only
- ✂️: cut

### Functional

- [✅] **FR1 - Agent identity.** The guardian runs under an Entra agent identity created from an agent identity blueprint. No shared user account, no plain service principal.
- [✅] **FR2 - Ownership.** Every agent identity has a named owner (technical) and sponsor (business), set in Entra.
- [✅] **FR3 - Autonomous mode.** For read-only registry checks (orphan agents, expiring credentials, over-permissioned agents, dormant agents), the guardian gets a token via its blueprint using client credentials and calls the Identity API.
- [✅] **FR4 - On-behalf-of mode.** For a privileged fix, disabling an agent, the agent proposes and the approver decides. The approver's token is exchanged with OBO, so the fix runs with the approver's rights, and the guardian holds no standing write permission. The API tells the two modes apart from token claims.
  - Cut: removing a permission and assigning a sponsor. One fix already proves the whole OBO chain, and each extra fix would only repeat it.
- [✅] **FR5 - Token validation.** The Identity API rejects any token not issued to a registered agent identity, and rejects agent tokens for scopes the agent was not granted. Negative tests included.
- [✅] **FR6 - Tool layer via MCP.** The guardian's tools are exposed through an MCP server. Only allow-listed tools can be called. A call to an unknown tool is refused and logged.
- [✅] **FR7 - Human approval.** Privileged tools require explicit human approval before execution. The approver's identity is recorded with the action.
- [✅] **FR8 - Audit.** Every action logs: agent object ID, parent blueprint app ID, mode (autonomous or OBO), delegating user if any, tool name, approver if any, trace ID, timestamp, outcome.
- [✂️] **FR9 - Conditional Access.** A policy applied to the blueprint blocks the agent under a defined condition, and the block appears in Entra sign-in logs.
  - Cut: a policy applies only when the tenant's security defaults are off, and turning them off also removes the tenant's built-in MFA ([why](docs/conditional-access-and-revocation.md)).
- [✅] **FR10 - Secrets.** No long-lived secrets in code or config. Credentials via Key Vault or environment injection; tokens short-lived.
  - The blueprint's certificate is a file injected only into the two token sidecars, not Key Vault (shortcut D3).
- [📄] **FR11 - Revocation.** Disabling an agent identity in Entra stops its calls within one token lifetime, with no code change. The ways to stop a token already issued are written design ([DESIGN-NOTES.md](docs/DESIGN-NOTES.md#7-revoking-live-tokens)).
- [✂️] **FR12 - Live telemetry source.** One real worker agent runs on a timer, so the lifecycle and observability features use live data.
  - Cut: the worker was only a live target for the revocation and Conditional Access features, and neither is built.

### Non-functional

- [✅] **NFR1 - Least privilege.** Each tool documents the exact permission it needs. No agent holds anything beyond its documented set ([tool permissions](docs/tool-permissions.md)).
  - Per tool in the code, not in the token: a token carries all the guardian's roles on its API ([why](docs/tool-permissions.md#where-the-guardian-differs)).
- [▶️] **NFR2 - Attribution.** Any log line traces to exactly one agent identity and, in OBO mode, exactly one human.
- [✅] **NFR3 - Untrusted content boundary.** Tool outputs and directory data are data, never instructions. Tested with a prompt injection planted in a fixture agent's description: it is treated as text, not executed.
- [▶️] **NFR4 - Observability.** OpenTelemetry trace per guardian request. Three records are linked: the trace ID links the application trace to the audit line, and the token's ID (`uti`) links the audit line to the Entra sign-in log. (Per-step token count and cost estimation: optional, first to cut.)
  - Not yet: the link to the Entra sign-in log. The trace ID never reaches Entra, so this link needs the token's ID in each audit line.
- [▶️] **NFR5 - Reproducibility.** One `docker compose up` runs the app locally. Tenant setup is documented step by step and scripted as code: Bicep (Microsoft Graph Bicep extension) where the object type supports it, Microsoft Graph PowerShell for the rest.
- [✅] **NFR6 - Threat model.** Every OWASP Agentic Top 10 risk is marked mitigated, accepted or out of scope, with a reason, mapped to a MAESTRO layer, and pointing at the code or config that implements the control ([THREATMODEL.md](THREATMODEL.md)).
- [▶️] **NFR7 - Tests.** Offline tests for all policy logic (allow-list, approval gate, token claim checks). One live test per auth flow.
- [✂️] **NFR8 - Durable proof.** A walkthrough of every control with screenshots from the live tenant.
  - Cut: the project keeps no demo page and no screenshots. The code, the tests and the docs are the record.

Note:
The evidence for each one is in [Delivery status](docs/delivery-status.md), and the shortcuts the guardian takes from the enterprise design (D1 to D6) are in [the architecture](docs/agent-steward-architecture.md#architectural-shortcuts-where-the-guardian-differs).


### Written Design Only

Workload identity federation for non-Microsoft agents, RFC 8693 token exchange (the standard grant; the project's token exchange is Entra's OBO, which is built: [why OBO is not RFC 8693](docs/jwt-bearer-and-token-exchange.md#52-obo-is-not-rfc-8693)), AIMS, Agent 365 governance and dynamic agent registration are written designs in [DESIGN-NOTES.md](docs/DESIGN-NOTES.md), not code. Each note says what a production system would add, and why it was left out.

## Security Controls at a Glance

Every control in place, with the threat it stops and its evidence, is in the [control register](docs/security-controls.md). The main ones are:

- **No credential in the agent:** the blueprint's certificate is only in the two token sidecars, and the harness that talks to the model cannot reach them (C2 to C4).
- **No standing write permission:** the guardian's one write permission is delegated. So a disable runs only with an approver's token, through OBO, and never beyond that person's own rights (C6, C30, C31).
- **A person approves every disable:** the request tool only asks. The approval is checked as a person's, bound to one request, and final (C24, C26, C27).
- **Least privilege per tool:** each MCP tool's calls and permissions are documented, and a test checks them against the code and the grants (C6).
- **Zero Trust at the API:** seven checks on every token, tested with forged and real Entra tokens (C7).
- **The MCP server checks every call:** only allow-listed tools are listed or run, inputs are validated, endpoints never come from the model, and findings reach the audit log without passing through the model (C8 to C11, C22).
- **Limits on the model:** a hard limit on model calls, a system prompt that treats tool results as data, tool inputs that must match their schema, and a run that stops when the MCP server rejects a tool call (C12, C13, C21, C23).

## Tech Stack

Every technology and tool the project uses, by category. Versions are the pinned ones in the repo.

**Identity and access (Microsoft Entra)**

- **Microsoft Entra Agent ID:** an agent identity blueprint, with the guardian's agent identity created from it, and the fixture agents the guardian scans.
- **Blueprint certificate:** the blueprint's only credential. It is mounted into the token sidecars as a Docker secret, and no other container can read it.
- **On-Behalf-Of flow for agents (OBO):** the executor exchanges the approver's token for one that runs the disable with the approver's rights.
- **App roles:** three roles on the Identity API's application: submit findings, request a disable, and approve a disable.
- **Approver group:** a security group whose direct members get the approve role.
- **Entra logs:** the sign-in log shows when each agent last signed in. The audit log and the sign-in log together show who disabled an agent.

**Microsoft Graph**

- **Graph v1.0:** reads agent identities, blueprints, sponsors and app role assignments.
- **Graph beta:** the sign-in logs for the dormant check, agent details that v1.0 does not return, and the disable (a PATCH on the agent identity).

**Token source**

- **Microsoft Entra SDK auth sidecar (1.1.2):** Microsoft's container that holds the certificate and gets each token. There are two: one for the guardian's services, and one for the executor.
- **MSAL Python (1.39.0):** the approve CLI signs the approver in with the authorization code flow and PKCE, and catches the redirect on a one-shot localhost listener.
- **PyJWT (2.15.1) and cryptography (50.0.2):** the Identity API checks each token's RS256 signature against Entra's keys, then checks its claims.

**AI model and agent**

- **Claude (Opus 5.5):** the guardian's model, through the Anthropic API.
- **Anthropic Python SDK (2.3.0):** native tool use, adaptive thinking and the effort setting.
- **Harness:** our own agent loop in Python, with a hard limit on rounds and a token count per run.
- **Rich (15.0.0):** prints the run's rounds and its report in the terminal.

**Model Context Protocol (MCP)**

- **Official MCP Python SDK (2.3.0):** the MCP server and the harness's MCP client.
- **Streamable HTTP transport:** the server accepts only allow-listed host names.
- **MCP Inspector (2.12.0):** used during development to call the tools by hand ([docker-compose.inspect.yml](docker-compose.inspect.yml)).

**Services**

- **Python 3.13:** every service, the approve CLI and the tests.
- **FastAPI (0.143.0) and Uvicorn (0.54.0):** the Identity API and the executor.
- **Policy as JSON:** the tool allow-list, each tool's permissions, what each agent may hold, and the harness's settings ([config/](config/)).

**Observability**

- **OpenTelemetry SDK (1.45.2):** traces in every service, sent over OTLP/HTTP.
- **OpenTelemetry instrumentation (0.66b2):** automatic spans for FastAPI requests and for outgoing HTTP calls (urllib).
- **Jaeger (2.23.0):** stores and shows the traces.
- **Loki (3.7.9):** stores the audit lines, received over OTLP.
- **Grafana (13.3.0):** shows the traces and the audit lines, with a provisioned dashboard of agent disables.
- **One telemetry container:** Jaeger, Loki and Grafana are copied from their official images into one container ([observability/](observability/)).

**Tenant setup as code**

- **Bicep with the Microsoft Graph Bicep extension (v1.0, 1.0.0):** the applications, app roles, permission grants and the approver group.
- **PowerShell 7 with Microsoft Graph PowerShell:** the Agent ID objects that Bicep does not support yet: the blueprint, its certificate, the agent identities and the fixtures ([setup/](setup/)).

**Containers and local run**

- **Docker Compose:** the whole stack starts with one command ([how](docs/infra.md#run-it)). Tests and dev tools run under the `tools` profile, so they never start by default.
- **Base images:** `python:3.13-slim` for our services, and a distroless Azure Linux image for the sidecar.
- **Docker secrets:** the certificate reaches the sidecars as a file under `/run/secrets`, never as an environment variable.

**Testing**

- **pytest (9.1.1):** offline unit tests for the policy logic: the allow-list, the approval gate and the token checks.
- **httpx (0.28.1):** drives the FastAPI services in tests.
- **Integration tests:** against the running stack, with real Entra tokens.
- **Agent eval:** runs the guardian against planted prompt injections, and checks that it does not follow them.

**Security frameworks**

- **OWASP Top 10 for Agentic Applications (2026):** the threat model's list of risks ([THREATMODEL.md](THREATMODEL.md)).
- **MAESTRO:** the layers each risk is mapped to.

**Development tools**

- **Token claim tools:** small scripts that print a token's claims, never the token itself ([tools/](tools/)).
- **Claude Code:** an AI pair programmer for the code and the docs.

## Read More

- **Start here:** [Agent Steward architecture](docs/agent-steward-architecture.md): how the guardian is built, its decisions, and every shortcut it takes. It is compared with the [enterprise design](docs/agent-architecture.md).
- **Security:** the [threat model](THREATMODEL.md), the [control register](docs/security-controls.md), the [permission each tool needs](docs/tool-permissions.md), the [decision log](docs/decisions.md), and a runbook: [who disabled this agent?](docs/who-disabled-this-agent.md).
- **How it is built:** [the harness](docs/harness.md), [MCP](docs/mcp.md), [infrastructure and local run](docs/infra.md), [traces and span names](docs/tracing.md), and [testing an agent's behaviour](docs/testing-ai-agents.md).
- **Reference:** [Agent ID](docs/agent-id.md), [tokens](docs/tokens.md), [authentication](docs/authentication.md), [JWT bearer and token exchange](docs/jwt-bearer-and-token-exchange.md), [delegation and the trusted subsystem](docs/delegation-and-trusted-subsystem.md), [from microservices to agents](docs/from-microservices-to-agents.md), and the [glossary](docs/GLOSSARY.md). All documents are in [docs/](docs/).
- **Status:** [Delivery status](docs/delivery-status.md): every requirement, its status and its evidence.

## Repository Layout

```
config/           Policy: what each agent may hold, the tool allow-list, each tool's permissions, the harness's settings
setup/            Tenant setup as code: Bicep and Microsoft Graph PowerShell
src/guardian/     The harness: the agent loop between Claude and the MCP server
src/mcp_server/   The MCP server: the guardian's tools, the four rules, the Graph reader; it never writes
src/identity_api/ The Identity API: token checks, disable requests and approvals, audit
src/executor/     The executor: the one write, disabling an agent with the approver's rights (OBO)
src/approve_cli/  The approve CLI: a person approves or denies a disable request
src/shared/       Code every service uses: telemetry and audit lines
observability/    The telemetry container: Jaeger, Loki and Grafana, with a dashboard
tests/            Unit tests (offline), integration tests (the running stack, real Entra tokens), an agent eval
tools/            Dev tools that print token claims, never tokens
docs/             Architecture, security, reference and delivery status
THREATMODEL.md    OWASP Agentic Top 10, mapped to MAESTRO layers
```
