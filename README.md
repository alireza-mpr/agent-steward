# Agent Registry Guardian

An AI agent that governs other AI agents in a Microsoft Entra tenant.

The guardian has its own Entra agent identity (created from an agent identity blueprint). It autonomously finds badly governed agents - no owner, no sponsor, expiring credentials, excessive permissions, dormant - and reports them. Fixing a problem (disabling an agent, removing a permission, assigning a sponsor) is a privileged operation: it runs on behalf of a signed-in admin (OBO), behind a human approval gate, through an allow-listed MCP tool, and every action is attributed and audited.

The point of this project: demonstrate, end to end, how an autonomous agent can be **identified, authenticated, authorized, constrained, observed and revoked** using Microsoft Entra Agent ID - with Zero Trust and least privilege applied at every step.

## Architecture

```mermaid
flowchart LR
    Admin[Admin user] -- "signs in (OIDC)" --> Guardian
    Guardian["Guardian agent<br/>(Entra agent identity)"] -- "tool calls" --> MCP["MCP server<br/>allow-list + approval gate"]
    MCP -- "agent token<br/>(client credentials or OBO)" --> API["Identity API<br/>claim validation + audit"]
    Guardian -- "token requests via blueprint" --> Entra[("Microsoft Entra<br/>Agent ID")]
    Worker["Live worker agent<br/>(timer loop, no LLM)"] -- "client credentials" --> Entra
    Worker -- "read-only call every 5 min" --> API
    API --> Obs[("Audit log + OTel traces<br/>linked by correlation ID")]
    Entra --> Logs[("Entra sign-in logs")]
```

### Agents in this demo

| Agent | Role |
|---|---|
| **Guardian** | The only agent with a brain. Reads the registry autonomously; performs privileged fixes via OBO + approval. |
| **Live worker** | A tiny timer loop with its own agent identity. Calls one read-only endpoint every few minutes. Exists to produce real sign-in logs, to be blocked by Conditional Access, and to be killed live in the revocation demo. No LLM. |
| **Fixture agents (3-4)** | Registry entries with deliberate flaws: one without a sponsor, one with an expiring credential, one over-permissioned, one dormant. They run no code. The guardian finds them. |

## Functional requirements

- **FR1 - Agent identity.** The guardian runs under an Entra agent identity created from an agent identity blueprint. No shared user account, no plain service principal.
- **FR2 - Ownership.** Every agent identity has a named owner (technical) and sponsor (business), set in Entra.
- **FR3 - Autonomous mode.** For read-only registry checks (orphan agents, expiring credentials, over-permissioned agents, dormant agents), the guardian gets a token via its blueprint using client credentials and calls the Identity API.
- **FR4 - On-behalf-of mode.** An admin signs in. For privileged fixes (disable agent, remove permission, assign sponsor), the guardian calls the Identity API on behalf of that admin using the OBO flow. The API tells the two modes apart from token claims.
- **FR5 - Token validation.** The Identity API rejects any token not issued to a registered agent identity, and rejects agent tokens for scopes the agent was not granted. Negative tests included.
- **FR6 - Tool layer via MCP.** The guardian's tools are exposed through an MCP server. Only allow-listed tools can be called. A call to an unknown tool is refused and logged.
- **FR7 - Human approval.** Privileged tools require explicit human approval before execution. The approver's identity is recorded with the action.
- **FR8 - Audit.** Every action logs: agent object ID, mode (autonomous or OBO), delegating user if any, tool name, approver if any, correlation ID, timestamp, outcome.
- **FR9 - Conditional Access.** A policy applied to the blueprint blocks the agent under a defined condition, and the block appears in Entra sign-in logs. (If tenant licensing blocks CA for agents, this requirement is documented with the exact licensing constraint instead of demoed - see DEMO.md.)
- **FR10 - Secrets.** No long-lived secrets in code or config. Credentials via Key Vault or environment injection; tokens short-lived.
- **FR11 - Revocation.** Disabling an agent identity in Entra stops its calls within one token lifetime, with no code change. Demonstrated live on the worker's timer loop.
- **FR12 - Live telemetry source.** One real worker agent runs on a timer, continuously producing sign-in logs, fresh short-lived tokens, and traces, so lifecycle and observability demos use live data.

## Non-functional requirements

- **NFR1 - Least privilege.** Each tool documents the exact permission it needs. No agent holds anything beyond its documented set.
- **NFR2 - Attribution.** Any log line traces to exactly one agent identity and, in OBO mode, exactly one human.
- **NFR3 - Untrusted content boundary.** Tool outputs and directory data are data, never instructions. Demonstrated with a prompt injection test: a malicious instruction planted in a fixture agent's description field is treated as text, not executed.
- **NFR4 - Observability.** OpenTelemetry trace per guardian request. One correlation ID links three records: the application trace, the audit line, and the Entra sign-in log. (Per-step token count and cost estimation: optional, first to cut.)
- **NFR5 - Reproducibility.** One `docker compose up` runs the app locally. Tenant setup is documented step by step and scripted as code: Bicep (Microsoft Graph Bicep extension) where the object type supports it, Microsoft Graph PowerShell for the rest.
- **NFR6 - Repo hygiene.** No secrets, tenant IDs or real data in the repo. History scanned with gitleaks.
- **NFR7 - Threat model.** Every OWASP Agentic Top 10 risk is marked mitigated, accepted or out of scope, with a reason, mapped to a MAESTRO layer, and pointing at the code or config that implements the control. See [THREATMODEL.md](THREATMODEL.md).
- **NFR8 - Tests.** Offline tests for all policy logic (allow-list, approval gate, token claim checks). One live test per auth flow.
- **NFR9 - Durable proof.** [DEMO.md](DEMO.md) walks through every control with screenshots captured while the tenant was live, so the whole story is reviewable without running anything.

## Deliberately out of code scope

Workload identity federation for non-Microsoft agents, token exchange (RFC 8693), AIMS, Agent 365 governance, and dynamic agent registration are covered as written designs in [DESIGN-NOTES.md](DESIGN-NOTES.md), not as code. A 14-day timebox forces choices; the notes explain what a production system would add and why.

## Security controls at a glance

| Control | Principle | Where to see it |
|---|---|---|
| Blueprint-based agent identity with owner + sponsor | Accountability, NHI lifecycle | Entra admin centre, `setup/` scripts |
| Client credentials via blueprint | No credentials of the agent's own | Autonomous flow, token claims |
| OBO flow | Secure delegation, dual attribution | Privileged flow, API logs |
| Claim validation + negative tests | Zero Trust at the API | `tests/`, API middleware |
| MCP allow-list | Constrained tool access | MCP server, refused-tool log |
| Approval gate | Human-in-the-loop for privilege | Privileged flow |
| Audit + correlation ID | Observable, attributable, auditable | Audit log, OTel trace, sign-in log |
| Conditional Access on blueprint | Policy-constrained agents | Entra sign-in logs |
| Short-lived tokens, Key Vault/env | Credential hygiene | Repo (absence of secrets), gitleaks |
| Disable = kill switch | Revocation without code change | Live worker failing |
| Injection test | Untrusted content boundary | NFR3 test |

## Repository layout

```
setup/          Tenant setup as code: Bicep + Graph PowerShell (blueprint, identities, fixtures)
src/api/        Identity API (Python / FastAPI): token validation, audit
src/guardian/   Guardian agent (Python)
src/mcp/        MCP server (official Python SDK): tools, allow-list, approval gate
src/worker/     Live worker timer loop (PowerShell)
tests/          Offline policy tests + live auth-flow tests
docs/           Reference (entra.md, infra.md), screenshots, diagrams
GLOSSARY.md     Ubiquitous language: OAuth2/OIDC/CDR terms mapped to Entra Agent ID and MCP
THREATMODEL.md  OWASP Agentic Top 10 x MAESTRO
DEMO.md         Screenshot walkthrough
DESIGN-NOTES.md Written designs for out-of-scope patterns
```

## Delivery status

The spec above is the backlog. This table tracks delivery: every requirement, its status, and the evidence that proves it. Status values: `not started` / `in progress` / `done` / `documented-only` (demonstrated on paper where tenant licensing or the timebox prevented a live demo - the evidence says why).

| Req | Status | Evidence |
|---|---|---|
| FR1 - Agent identity via blueprint | not started | - |
| FR2 - Owner + sponsor | not started | - |
| FR3 - Autonomous mode | not started | - |
| FR4 - OBO mode | not started | - |
| FR5 - Token validation | not started | - |
| FR6 - MCP allow-list | not started | - |
| FR7 - Human approval | not started | - |
| FR8 - Audit | not started | - |
| FR9 - Conditional Access | not started | - |
| FR10 - Secrets | not started | - |
| FR11 - Revocation | not started | - |
| FR12 - Live worker | not started | - |
| NFR1 - Least privilege | not started | - |
| NFR2 - Attribution | not started | - |
| NFR3 - Untrusted content boundary | not started | - |
| NFR4 - Observability | not started | - |
| NFR5 - Reproducibility | not started | - |
| NFR6 - Repo hygiene | not started | - |
| NFR7 - Threat model | not started | - |
| NFR8 - Tests | not started | - |
| NFR9 - Durable proof | not started | - |

Evidence links point to DEMO.md sections, screenshots in `docs/screenshots/`, test files, or Entra log captures.
