# From Microservices to Agents: What Changes for Non-Human Identities

An engineer who knows microservices already knows most of the identity machinery that agents use. What changes is that a model, not code, decides what the workload does at runtime, from text it reads. This page lists what stays the same, what changes, and how to approach agent identities. Each claim is marked as verified (with its source) or not checked.

## Contents

- [1. The High-Level View](#1-the-high-level-view)
- [2. Behaviour Comes from a Model, Not Code](#2-behaviour-comes-from-a-model-not-code)
- [3. Data Can Become Instructions](#3-data-can-become-instructions)
- [4. The Agent Acts for People](#4-the-agent-acts-for-people)
- [5. The Agent Picks Its Tools at Runtime](#5-the-agent-picks-its-tools-at-runtime)
- [6. Many People Create Agents, Fast](#6-many-people-create-agents-fast)
- [7. Agents Call Agents](#7-agents-call-agents)
- [8. Audit Needs "Why"](#8-audit-needs-why)
- [9. How to Approach Agent Identities](#9-how-to-approach-agent-identities)
- [10. The Short Version](#10-the-short-version)
- [11. The Guardian Today](#11-the-guardian-today)

---

## 1. The High-Level View

**The question.** Imagine explaining agents to a strong microservices developer. What's different about agent identities compared with service accounts, and how should we handle them?

**The short answer.** Everything you know still applies: OAuth, tokens, service identities, Zero Trust. What changes is that the code no longer decides what the service does. A model decides at runtime, from text it reads. So the identity's permissions become its real blast radius, and we have to govern an agent like a person and secure it like a service. ("Govern it like a person, secure it like a service" is a summary phrase of this project, not an industry term.)

**What stays the same.**

- **The protocols:** OAuth 2.0, client credentials, short-lived tokens, workload identity, mTLS.
- **The principles:** least privilege, Zero Trust, audit.

A microservices developer already knows most of the machinery. So say that first. It makes the differences easier to accept.

**The key differences.** Each one has its own section below.

1. **Behaviour comes from a model, not code:** a microservice does what its code says, so you can review the code and know what it will do. An agent decides at runtime. So you can't review its behaviour, and the permissions you grant are what it could actually do. Least privilege stops being good practice and becomes the main control.
2. **Data can become instructions:** for a microservice, a Kafka message is just data. For an agent, any text it reads, such as an email, a document or a tool result, can steer it. That's prompt injection. An attacker can misuse the identity without stealing a credential.
3. **It acts for people:** a service account acts for itself. An agent often acts for a user, so the token must carry both the user and the agent (OBO). And someone must answer for the agent: an owner and a sponsor.
4. **It picks its tools at runtime:** a microservice calls fixed endpoints. An agent chooses which tool to call, and with which arguments. So authorization moves to every tool call: an allow-list, input checks and runtime policies.
5. **Many people create agents, fast:** service accounts come from platform teams through change control. Agents are created by many teams, including with low-code tools such as Copilot Studio. So you get sprawl. You need a registry, an owner and sponsor for every agent, expiry and clean-up.
6. **Agents call agents:** delegation chains form, so you need to track who acted for whom across hops.
7. **Audit needs "why":** for a microservice, "what happened" is enough. For an agent, you also need for whom and why: the user, the task, and the approver.

Microsoft says the same in its Learn "Key concepts" page: "Traditional service principals were designed for static, deterministic workloads. Microsoft Entra Agent ID exists because service principals lack the governance infrastructure AI agents need. There's no enforced sponsorship, no agent-aware audit entries, and no blueprint-managed lifecycle." ([Key concepts](https://learn.microsoft.com/en-us/entra/agent-id/key-concepts), verified 9 November 2026). That's why Entra Agent ID exists.

---

## 2. Behaviour Comes from a Model, Not Code

**In a microservice:** the code fixes what the service does. A reviewer can read the code and know which calls it will make. So its permissions can be wider than it needs, and the code still limits what happens.

**In an agent:** a model decides at runtime which actions to take. Nobody can read the behaviour in advance, and the same input can lead to different actions. So everything the identity is allowed to do is something the agent might do.

**So:** the permissions are the real blast radius. Least privilege stops being good practice and becomes the main control.

**In Entra:** Entra limits agents by default. It blocks agent identities from being granted many high-privilege roles and Graph permissions, such as Global Administrator and `Application.ReadWrite.All`, because "agents operate under a principle of limited scope" ([Authorization in Agent ID](https://learn.microsoft.com/en-us/entra/agent-id/authorization-agent-id), verified). A plain service principal has no such limit.

---

## 3. Data Can Become Instructions

**In a microservice:** a Kafka message or an API response is data. Fixed code parses it, and the data cannot change what the code does.

**In an agent:** any text the agent reads, such as an email, a document, a tool result or another agent's description, reaches the model. Text that looks like an instruction can steer it. This is prompt injection. An attacker can misuse the agent's identity without stealing any credential.

**So:** treat everything the agent reads as untrusted, and put every hard control outside the model, where text cannot change it: in the harness, the tool layer and the API.

**In the industry:** OWASP lists this as ASI01 Agent Goal Hijack in the [Top 10 for Agentic Applications](https://genai.owasp.org/2025/12/09/owasp-top-10-for-agentic-applications-the-benchmark-for-agentic-security-in-the-age-of-autonomous-ai/), and as LLM01 Prompt Injection in its LLM Top 10.

---

## 4. The Agent Acts for People

**In a microservice:** a service account acts as itself, with client credentials. If a user is involved, the service usually checks the user itself and then acts with its own power.

**In an agent:** an agent often acts for a user ("read my mail", "book my meeting"). Then two parties are responsible for each action: the user who asked, and the agent that acted. And someone must answer for the agent itself.

**So:**

- **Delegation:** the token must carry both the user and the agent, so the API can check both. In OAuth, that's on-behalf-of (OBO) or token exchange.
- **Accountability:** every agent needs a technical owner and a business sponsor.

**In Entra:**

- **OBO:** interactive agents get "delegated permissions that allow them to act on behalf of users using the on-behalf-of (OBO) authentication flow" ([Key concepts](https://learn.microsoft.com/en-us/entra/agent-id/key-concepts#interactive-agents), verified). With delegated permissions, the agent "can't exceed that user's access" ([Authorization in Agent ID](https://learn.microsoft.com/en-us/entra/agent-id/authorization-agent-id#delegated-microsoft-graph-permissions), verified). The new token names the user in `sub`/`oid` and the agent in `azp` ([tokens.md](tokens.md#who-the-token-is-for-aud-and-who-it-is-about-sub)).
- **Sponsors:** "Sponsors provide business accountability for agents, making lifecycle decisions without technical administrative access" ([Key concepts](https://learn.microsoft.com/en-us/entra/agent-id/key-concepts#agent-owners-sponsors-and-managers), verified).
- **Deviation:** Entra's OBO is not RFC 8693 token exchange. It has no `act` claim ([details](jwt-bearer-and-token-exchange.md#52-obo-is-not-rfc-8693)).

---

## 5. The Agent Picks Its Tools at Runtime

**In a microservice:** the endpoints it calls are fixed in its code. A scope per API is enough, because the code decides which call is made with which arguments.

**In an agent:** the model chooses which tool to call, and with which arguments. A tool the agent may call can be called with any arguments the model writes.

**So:** authorization moves to every tool call:

- **Allow-list:** only approved tools are offered and run.
- **Input checks:** every argument is validated against the tool's schema.
- **Runtime policy:** the tool layer checks the request in context, for example which object, how much, and for whom.
- **No token passthrough:** the tool layer uses its own token for downstream APIs, never the caller's.

**In the industry:** the Model Context Protocol (MCP) is the common way to give agents tools. The MCP spec forbids token passthrough ([details](agent-architecture.md)). Not checked again for this page.

---

## 6. Many People Create Agents, Fast

**In a microservice:** service accounts are usually created by platform teams, through change control, and there are few of them.

**In an agent:** many teams create agents, often with low-code tools, and in large numbers. Agents are tried, copied and abandoned. So agents with no owner, old credentials and too many permissions pile up. This is agent sprawl.

**So:**

- **A registry:** a list of every agent, its owner and sponsor, and what it may do.
- **A lifecycle:** expiry, review and clean-up of agents that are no longer used.

**In the Microsoft stack:**

- **Copilot Studio** creates an agent identity automatically when a maker creates an agent ([Agent identity integration](https://learn.microsoft.com/en-us/microsoft-agent-365/builder/identity), verified 5 November 2026).
- **Agent 365** adds a registry of agents in the Microsoft 365 admin center ([Agent 365 overview](https://learn.microsoft.com/en-us/microsoft-agent-365/overview), verified 5 November 2026).

---

## 7. Agents Call Agents

**In a microservice:** services call services, and each call carries the calling service's own token. Distributed tracing links the calls.

**In an agent:** agents hand work to other agents, sometimes for a user. So a chain forms (user, agent A, agent B, the API), and the last service needs to know who acted for whom.

**So:** each hop exchanges its token for a new one, with the next hop as the audience. Never pass a token through. Each exchange is a fresh check by the identity provider, and the scope can only stay the same or shrink.

**The standard:** RFC 8693 records the chain in nested `act` claims. "The outermost act claim represents the current actor while nested act claims represent prior actors." For access decisions, the API uses only the top-level claims and the current actor. Prior actors "are informational only", a history for audit ([RFC 8693 §4.1](https://www.rfc-editor.org/rfc/rfc8693.html#section-4.1), verified).

**Deviation, Entra:** OBO carries no `act` claim. The new token names the user and only the latest middle tier (`azp`), so earlier hops must come from logs and correlation IDs ([Investigating agent activity](investigating-agent-activity.md)). Microsoft documents no chain where a middle tier exchanges a token that an agent identity obtained ([agent-architecture.md](agent-architecture.md), not tested).

---

## 8. Audit Needs "Why"

**In a microservice:** request logs and traces answer "what happened". Because code decided the action, the code explains why.

**In an agent:** the code doesn't explain the action. The model chose it, from a task and from the text it read. So an investigation needs three answers: which agent acted, for whom, and why.

**So:** every action is logged with the agent, the user (if any), the approver (if any) and a correlation ID. The agent's own trace (its task, inputs and tool calls) explains why. The model's own report of what it did is not proof, because the model can be wrong or fooled.

**In Entra:** the sign-in logs, the Microsoft Graph activity logs and the workload audit logs each answer part of this. They are joined by linkable identifiers such as `uti` ([Investigating agent activity](investigating-agent-activity.md#1-the-high-level-view)).

---

## 9. How to Approach Agent Identities

1. **Identity:** Give each agent its own identity, not a shared service account (and never a user account).
2. **Accountability:** Give each agent an owner and a sponsor.
3. **Least privilege:** Grant only what the agent's task needs, because the model may try anything it's allowed to do.
4. **Deterministic controls:** Put the checks around the model, in the harness, the MCP server and the API, never in the prompt.
5. **Human approval:** Require a person to approve irreversible actions, and record who approved.
6. **Audit and kill switch:** Log every action, and be ready to disable the identity, or every agent of a blueprint at once.

---

## 10. The Short Version

The whole page in about one minute, as I would say it to a microservices developer:

> "I'd tell them: keep everything you know. Tokens, client credentials, workload identity and Zero Trust all still apply. Then add one idea: a microservice does what its code says, but an agent does what a model decides, from text it reads. That changes three things. First, permissions are now the real blast radius, because anyone who can put text in front of the agent can try to steer it. Second, agents act for people, so the token needs both the user and the agent, and every agent needs a human sponsor. Third, agents are created by many teams, fast, so you need a registry and lifecycle, or you get sprawl. So we govern an agent like a person and secure it like a service, with every hard control outside the model."

---

## 11. The Guardian Today

How this project's guardian handles each difference, and where it falls short of the general practice:

| Difference | In the guardian | Delta |
|---|---|---|
| Behaviour from a model | Four read-only Graph permissions, and one write permission behind human approval ([delivery status, NFR1](delivery-status.md)) | Its Graph token always carries the write role, because `.default` gives every role ([A1](../THREATMODEL.md#accepted-risks)) |
| Data as instructions | A planted prompt injection in a fixture agent's description; the hard controls are in the MCP server ([THREATMODEL.md](../THREATMODEL.md), [agent eval](../tests/agent_eval/eval_prompt_injection.py)) | The eval passed 3 of 3 trials, which proves little on its own ([A5](../THREATMODEL.md#accepted-risks)) |
| Acts for people | Runs as itself, because the scan has no user ([why](agent-steward-architecture.md#why-the-guardian-runs-as-itself-not-on-behalf-of-a-user)); the approver's identity is recorded | No OBO for admin-started fixes ([D5](agent-steward-architecture.md#architectural-shortcuts-where-the-guardian-differs)) |
| Tools at runtime | MCP allow-list, input schemas, endpoints never from the model; `disable_agent` runs only on a dormant agent that is not its own ([server.py](../src/mcp_server/server.py)) | The harness proves itself to the MCP server only by network location, so there is no role-to-tool mapping per agent ([D1](agent-steward-architecture.md#architectural-shortcuts-where-the-guardian-differs)) |
| Sprawl | The guardian is a registry hygiene agent: it flags agents with no sponsor, expiring credentials, too many permissions, or no sign-in in 30 days ([rules.py](../src/mcp_server/rules.py)) | Report and disable only; no access reviews |
| Agents call agents | One agent; the fixture agents run no code (ASI07 out of scope) | No delegation chain to track |
| Audit "why" | One audit line per decision at the Identity API, with the FR8 fields | One correlation ID across all parts is not built yet (NFR4) |

Sources: Key concepts and Authorization in Agent ID (Microsoft Learn) and RFC 8693, checked 9 November 2026; Copilot Studio and Agent 365 pages, checked 5 November 2026.
