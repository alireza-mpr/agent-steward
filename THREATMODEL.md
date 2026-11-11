# Threat Model

The guardian is one agent that reads an Entra tenant's agent registry, reports flaws, and can disable a dormant agent after a person approves. Its main attack path is text it reads from the directory, so the hard controls do not depend on the model behaving well. This page rates each [OWASP Top 10 for Agentic Applications (2026)](https://genai.owasp.org/2025/12/09/owasp-top-10-for-agentic-applications-the-benchmark-for-agentic-security-in-the-age-of-autonomous-ai/) risk and maps it to a [MAESTRO](https://cloudsecurityalliance.org/blog/2025/03/24/threat-modeling-openai-s-responses-api-with-the-maestro-framework) layer. The layer mapping is this project's own judgment.

## Contents

- [The risks](#the-risks)
- [Blast Radius: What Happens If One Part Is Compromised?](#blast-radius-what-happens-if-one-part-is-compromised)
- [Accepted risks](#accepted-risks)

## The risks

C numbers are in the [control register](docs/security-controls.md), with their evidence. A numbers are in [Accepted risks](#accepted-risks).

| Risk | Status | Why | Controls | Where | MAESTRO layer | Accepted |
|---|---|---|---|---|---|---|
| ASI01 Agent Goal Hijack | Mitigated | A planted attack failed in the [agent eval](tests/agent_eval/eval_prompt_injection.py), and the hard controls hold even if the model is fooled | C24, C11 | [server.py](src/mcp_server/server.py) | 1 Foundation Models, 3 Agent Frameworks | A5 |
| ASI02 Tool Misuse & Exploitation | Mitigated | Enforcing allow-list in MCP server, human approval on disable | C24, C22 | [tool-allow-list.json](config/tool-allow-list.json) | 3 Agent Frameworks | A3 |
| ASI03 Identity & Privilege Abuse | Mitigated | Human in the loop, token checks, least privilege, one agent identity, and no standing write permission: a disable uses the approver's rights | C26, C7, C6, C1, C30, C31 | [token_validation.py](src/identity_api/token_validation.py) | 6 Security and Compliance | A2, A7, A8 |
| ASI04 Agentic Supply Chain Vulnerabilities | Accepted | Pinned versions, but no dependency scanning | C18 | [docker-compose.yml](docker-compose.yml) | 4 Deployment and Infrastructure | - |
| ASI05 Unexpected Code Execution | Out of scope | No tool runs code | C22 | [tool-allow-list.json](config/tool-allow-list.json) | 4 Deployment and Infrastructure | - |
| ASI06 Memory & Context Poisoning | Mitigated | No memory survives a run, so a planted instruction cannot come back in a later run. Poisoned text within a run is ASI01 | C29 | [harness.py](src/guardian/harness.py) | 2 Data Operations | - |
| ASI07 Insecure Inter-Agent Communication | Out of scope | One agent. The fixture agents are registry entries and run no code | - | - | 7 Agent Ecosystem | - |
| ASI08 Cascading Failures | Mitigated | A limit on model calls, fail closed on errors | C12, C23 | [harness.py](src/guardian/harness.py) | 3 Agent Frameworks | A6 |
| ASI09 Human-Agent Trust Exploitation | Accepted | Facts from Graph and the model's text labelled untrusted, but a person can still approve carelessly | C28, C27 | [approve.py](src/approve_cli/approve.py) | 7 Agent Ecosystem | A3, A4, A5 |
| ASI10 Rogue Agents | Accepted | Registry rules flag bad agents, audit lines, a disable path, but no detection of an active agent misbehaving | C14, C15, C24 | [rules.py](src/mcp_server/rules.py) | 5 Evaluation and Observability, 6 Security and Compliance | A7 |

## Blast Radius: What Happens If One Part Is Compromised?

We separate the agent into distinct trust boundaries specifically to contain the blast radius when something goes wrong. Here is how the Guardian's architecture holds up if individual components fail:

* **Scenario 1: The Model Is Tricked (Prompt Injection)**
  * *Impact:* Minimal (this is the primary failure mode the architecture is built to handle). The model can only *ask* the harness to invoke tools, and its strongest tool only asks a human. The MCP server's checks (the tool allow-list, its input checks, its own check that an agent is dormant and not one of the guardian's own), the human approval gate, the executor's second check and the harness's turn limit all remain strictly enforced, and the model never sees an Entra token.
* **Scenario 2: The Harness is Compromised**
  * *Impact:* Moderate. Because the harness mediates all communication with the model, it can manipulate the tool list, alter or fabricate tool results, invoke any allow-listed tool, and burn through the local Anthropic API key. However, **it still cannot obtain an Entra token**, and it cannot bypass the tool allow-list or approval gates because those checks are enforced inside the MCP server. 
  * *Crucial Audit Rule:* Because a compromised harness can lie to the model, **the LLM's final report is never proof of what actually happened—only the Identity API's audit log is.**
* **Scenario 3: The MCP Server is Compromised**
  * *Impact:* Moderate. Due to shortcut **D2** (where the sidecar lets its caller request tokens for any child of the blueprint), a compromised MCP server can obtain app-only TR access tokens for *any* agent identity under that blueprint. But it cannot disable anything: the guardian holds no app-only write role, the write code is in the executor, and the approver's token never reaches the MCP server. It writes the facts the approver sees, so it can mislead the approver ([A3](#accepted-risks)), but the executor checks the agent against Graph again before the write, so even a misled approval cannot disable an active agent or one of the guardian's own.
* **Scenario 4: The Executor is Compromised**
  * *Impact:* High. It receives each approver's token (Tc) after an approval. It could skip its own checks and exchange Tc to disable any agent the approver is allowed to disable, while Tc is valid ([A8](#accepted-risks)). It shares the second sidecar's namespace, so it can also get the guardian's app-only tokens. So the executor is now the most trusted component, and its code is kept to one route and one write.
* **Scenario 5: A Sidecar is Compromised**
  * *Impact:* Critical. The blueprint's private certificate is exposed (**D3**), and it is in both sidecars, immediately compromising every agent identity governed by that blueprint *(see the blast radius note in [Agent ID](docs/agent-id.md#why-use-two-requests-instead-of-one))*.
* **Scenario 6: The Identity API is Compromised**
  * *Impact:* Low to moderate. It stores the disable requests and the decisions, so it can report "approved" for a request that nobody approved. That alone disables nothing: the executor writes only with an approver's own token (Tc), sent by the approver's CLI after their own approval, and Tc's `oid` must be the recorded approver. The Identity API also keeps the audit log, so it could change or drop lines, because the logs stay on this machine ([D6](docs/agent-steward-architecture.md#architectural-shortcuts-where-the-guardian-differs)). It holds no credential and never gets a token, so it cannot call Graph itself.
* **Scenario 7: An Approver's Account is Compromised**
  * *Impact:* Low to moderate. The attacker can sign in through the CLI as the approver, and approve or deny pending requests. They cannot create a request, so they can approve only the disables the MCP server already asked for: dormant agents that are not the guardian's own, while the request is pending (up to 24 hours), and the executor checks the agent again. Denying everything blocks the clean-up but disables nothing. The account's own rights in Entra are a wider risk than this system: it can disable agents in the admin center anyway.
  * *The fix:* protect the account itself, for example with a Conditional Access policy that requires phishing-resistant MFA for the approvers group. This is not set up in the lab tenant.

## Accepted risks

Accepted for a 14-day lab. Each one has a known fix.

| # | Risk | OWASP risk | Why it matters | The fix |
|---|---|---|---|---|
| A1 | **Closed on 10 November 2026.** The guardian's Graph token carried its write role, because `.default` gives every role it holds | ASI03 Identity & Privilege Abuse | A stolen read token could also disable agents | Done: the app-only write role is removed, and a disable uses the approver's rights through OBO, in the executor |
| A2 | The harness proves itself to the MCP server only by network location ([D1](docs/agent-steward-architecture.md#architectural-shortcuts-where-the-guardian-differs)) | ASI03 Identity & Privilege Abuse | Any code on that network can call the tools | A bearer token on that link |
| A3 | The approver sees the facts that the MCP server sent, and the executor runs as the guardian's agent identity, not its own | ASI02 Tool Misuse & Exploitation, ASI09 Human-Agent Trust Exploitation | A compromised MCP server could mislead the approver. The executor's second check limits the damage to dormant agents that are not the guardian's own | The Identity API reads the facts from Graph itself, and the executor gets its own identity |
| A4 | An approval whose token never reaches the executor stays `approved`, and after 10 minutes it can no longer be executed | ASI09 Human-Agent Trust Exploitation | The CLI tells the approver nothing was disabled, but the request still reads `approved`, not `running` | An expired status for approvals that were never carried out |
| A5 | "Ask only for dormant agents" is a prompt rule. The [eval](docs/testing-ai-agents.md) passed 3 of 3 runs, which still allows a failure rate of up to about 63% | ASI01 Agent Goal Hijack, ASI09 Human-Agent Trust Exploitation | A fooled model could ask to disable the wrong agent; the MCP server refuses any agent that is not dormant or is the guardian's own, and a person must still approve | More trials and attacks |
| A6 | Pending requests are in memory | ASI08 Cascading Failures | A restart loses requests (nothing is disabled) | A database, and an approval web page |
| A7 | The blueprint's certificate (in both sidecars) and the model key are files, and audit logs stay local ([D3, D4, D6](docs/agent-steward-architecture.md#architectural-shortcuts-where-the-guardian-differs)) | ASI03 Identity & Privilege Abuse, ASI10 Rogue Agents | Someone on the host could take the keys or change the logs | Managed identity, federation, and a central log store |
| A8 | The approver's token (Tc) is valid for its whole lifetime, not for one action. The executor binds it to one request, but only by its own checks | ASI03 Identity & Privilege Abuse | A compromised executor could use one Tc for other disables the approver is allowed, while Tc is valid | A token bound to one action, such as Rich Authorization Requests (RFC 9396), if the identity provider supports it (not checked for Entra) |
