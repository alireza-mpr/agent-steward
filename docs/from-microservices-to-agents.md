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
- [9. Revocation Has to Reach the Running Agent](#9-revocation-has-to-reach-the-running-agent)
- [10. How to Approach Agent Identities](#10-how-to-approach-agent-identities)
- [11. The Short Version](#11-the-short-version)
- [12. The Guardian Today](#12-the-guardian-today)

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
8. **Revocation has to reach the running agent:** a disabled user's token stays valid until it expires. A person makes a few calls in that window; an agent acting for them can make hundreds. So revocation must stop the running task, not only new tokens.

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

## 9. Revocation Has to Reach the Running Agent

**In a microservice:** when a user is disabled, the identity provider stops issuing new tokens. A token already issued stays valid until it expires: 60 to 90 minutes by default in Entra ([Access tokens](https://learn.microsoft.com/en-us/entra/identity-platform/access-tokens#token-lifetime), verified 10 November 2026). With a person behind the app, that window is a small risk: they make a few calls in it, and they usually close the app.

**In an agent acting on the user's behalf (OBO):**

- **Speed:** the agent acts at machine speed. In those 60-90 minutes, it can make hundreds of calls for a user who no longer exists in the company.
- **Long tasks:** agents run long background tasks. So they hold tokens, and sometimes refresh tokens, for longer than a person would.
- **Nobody is watching:** a person might notice they've been locked out. An autonomous agent just keeps going.

**So:** disabling the identity is not enough. Revocation needs layers, from the identity provider down to the agent itself:

1. **Short-lived tokens:** keep the window small, and issue no new token or refresh after the disable.
2. **Push events at the resource:** the identity provider tells the resource that the user was disabled, and the resource rejects the old token at once.
3. **A live check for high-risk calls:** the resource asks whether the identity is still active, with no cache or a short one.
4. **The agent runtime:** the harness listens for the same events, or re-checks the delegation before each tool call, and stops the running task.

**In the industry:**

- **Token introspection ([RFC 7662](https://www.rfc-editor.org/rfc/rfc7662)):** the resource asks the identity provider whether a token is still active, on each call. It costs a round trip per call.
- **Push events:** OpenID Shared Signals carry revocation events, such as CAEP's session revoked. Microsoft's Continuous Access Evaluation (CAE) is "based on Open ID Continuous Access Evaluation Profile (CAEP)" ([CAE](https://learn.microsoft.com/en-us/entra/identity/conditional-access/concept-continuous-access-evaluation), verified 10 November 2026).

**Deviation, Entra:**

- **No introspection:** Entra's discovery document lists no `introspection_endpoint` ([OpenID configuration](https://login.microsoftonline.com/common/v2.0/.well-known/openid-configuration), checked 10 November 2026). APIs validate Entra tokens locally.
- **CAE is for Microsoft's resources:** "Microsoft Entra ID sends an event to CAE-enabled Microsoft resources" ([Secure applications with CAE](https://learn.microsoft.com/security/zero-trust/develop/secure-with-cae), verified 10 November 2026), such as Exchange Online, SharePoint Online, Teams and Microsoft Graph. For workload identities, only Microsoft Graph ([CAE for workload identities](https://learn.microsoft.com/entra/identity/conditional-access/concept-continuous-access-evaluation-workload)). Microsoft documents no way for your own API to receive CAE events. Even where CAE works, events can take up to 15 minutes to arrive.
- **So, for your own API:** the check is custom. Either the API asks Microsoft Graph whether the identity is still enabled on high-risk calls, or a deny list at the API is fed by events, for example Microsoft Graph change notifications on users (not checked).

The options for your own API, step by step: [Conditional Access and revocation](conditional-access-and-revocation.md#3-when-a-token-already-exists).

---

## 10. How to Approach Agent Identities

1. **Identity:** Give each agent its own identity, not a shared service account (and never a user account).
2. **Accountability:** Give each agent an owner and a sponsor.
3. **Least privilege:** Grant only what the agent's task needs, because the model may try anything it's allowed to do.
4. **Deterministic controls:** Put the checks around the model, in the harness, the MCP server and the API, never in the prompt.
5. **Human approval:** Require a person to approve irreversible actions, and record who approved.
6. **Audit and kill switch:** Log every action, and be ready to disable the identity, or every agent of a blueprint at once.

---

## 11. The Short Version

The whole page in about one minute, as I would say it to a microservices developer:

> "I'd tell them: keep everything you know. Tokens, client credentials, workload identity and Zero Trust all still apply. Then add one idea: a microservice does what its code says, but an agent does what a model decides, from text it reads. That changes three things. First, permissions are now the real blast radius, because anyone who can put text in front of the agent can try to steer it. Second, agents act for people, so the token needs both the user and the agent, and every agent needs a human sponsor. Third, agents are created by many teams, fast, so you need a registry and lifecycle, or you get sprawl. So we govern an agent like a person and secure it like a service, with every hard control outside the model."

---

## 12. The Guardian Today

How this project's guardian handles each difference, and where it falls short of the general practice:

| Difference | In the guardian | Delta |
|---|---|---|
| Behaviour from a model | Four read-only Graph permissions, and no write permission of its own: a disable runs with the approver's rights, through OBO ([delivery status, NFR1](delivery-status.md)) | The executor acts as the guardian's own agent identity, not a separate one ([A3](../THREATMODEL.md#accepted-risks)) |
| Data as instructions | A planted prompt injection in a fixture agent's description; the hard controls are in the MCP server and the executor ([THREATMODEL.md](../THREATMODEL.md), [agent eval](../tests/agent_eval/eval_prompt_injection.py)) | The eval passed 3 of 3 trials, which proves little on its own ([A5](../THREATMODEL.md#accepted-risks)) |
| Acts for people | Runs as itself, because the scan has no user ([why](agent-steward-architecture.md#why-the-guardian-runs-as-itself-not-on-behalf-of-a-user)); the approver's identity is recorded | OBO only for the approved disable; the other fixes are not built ([D5](agent-steward-architecture.md#architectural-shortcuts-where-the-guardian-differs)) |
| Tools at runtime | MCP allow-list, input schemas, endpoints never from the model; `submit_disable_agent_request` asks only for a dormant agent that is not its own, and the executor checks again before the write ([disable_checks.py](../src/mcp_server/disable_checks.py)) | The harness proves itself to the MCP server only by network location, so there is no role-to-tool mapping per agent ([D1](agent-steward-architecture.md#architectural-shortcuts-where-the-guardian-differs)) |
| Sprawl | The guardian is a registry hygiene agent: it flags agents with no sponsor, expiring credentials, too many permissions, or no sign-in in 30 days ([rules.py](../src/mcp_server/rules.py)) | Report and disable only; no access reviews |
| Agents call agents | One agent; the fixture agents run no code (ASI07 out of scope) | No delegation chain to track |
| Audit "why" | One audit line per decision at the Identity API, with the FR8 fields, and one line per write at the executor, naming the agent and the approver | One correlation ID across all parts is not built yet (NFR4) |
| Revocation | Disabling an agent identity is built, behind human approval (FR7), so Entra stops issuing it new tokens | A token already issued works until it expires; the deny list is design only ([DESIGN-NOTES §7](DESIGN-NOTES.md#7-revoking-live-tokens)) |

Sources: Key concepts and Authorization in Agent ID (Microsoft Learn) and RFC 8693, checked 9 November 2026; Access tokens, CAE, Secure applications with CAE (Microsoft Learn) and Entra's OpenID configuration, checked 10 November 2026; Copilot Studio and Agent 365 pages, checked 5 November 2026.
