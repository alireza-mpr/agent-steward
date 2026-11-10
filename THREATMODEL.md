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
| ASI03 Identity & Privilege Abuse | Mitigated | Human in the loop, token checks, least privilege, one agent identity | C26, C7, C6, C1 | [token_validation.py](src/identity_api/token_validation.py) | 6 Security and Compliance | A1, A2, A7 |
| ASI04 Agentic Supply Chain Vulnerabilities | Accepted | Pinned versions, but no dependency scanning | C18 | [docker-compose.yml](docker-compose.yml) | 4 Deployment and Infrastructure | - |
| ASI05 Unexpected Code Execution | Out of scope | No tool runs code | C22 | [tool-allow-list.json](config/tool-allow-list.json) | 4 Deployment and Infrastructure | - |
| ASI06 Memory & Context Poisoning | Out of scope | No memory survives a run, so a planted instruction cannot come back in a later run. Poisoned text within a run is ASI01 | C29 | [harness.py](src/guardian/harness.py) | 2 Data Operations | - |
| ASI07 Insecure Inter-Agent Communication | Out of scope | One agent. The fixture agents are registry entries and run no code | - | - | 7 Agent Ecosystem | - |
| ASI08 Cascading Failures | Mitigated | A limit on model calls, fail closed on errors | C12, C23 | [harness.py](src/guardian/harness.py) | 3 Agent Frameworks | A6 |
| ASI09 Human-Agent Trust Exploitation | Accepted | Facts from Graph and the model's text labelled untrusted, but a person can still approve carelessly | C28, C27 | [approve.py](src/approve_cli/approve.py) | 7 Agent Ecosystem | A3, A4, A5 |
| ASI10 Rogue Agents | Accepted | Registry rules flag bad agents, audit lines, a disable path, but no detection of an active agent misbehaving | C14, C15, C24 | [rules.py](src/mcp_server/rules.py) | 5 Evaluation and Observability, 6 Security and Compliance | A7 |

## Blast Radius: What Happens If One Part Is Compromised?

We separate the agent into distinct trust boundaries specifically to contain the blast radius when something goes wrong. Here is how the Guardian's architecture holds up if individual components fail:

* **Scenario 1: The Model Is Tricked (Prompt Injection)**
  * *Impact:* Minimal (this is the primary failure mode the architecture is built to handle). The model can only *ask* the harness to invoke tools. The MCP server's checks (the tool allow-list, its input checks, its own check that an agent is dormant and not one of the guardian's own, and the human approval gate) and the harness's turn limit all remain strictly enforced, and the model never sees an Entra token.
* **Scenario 2: The Harness is Compromised**
  * *Impact:* Moderate. Because the harness mediates all communication with the model, it can manipulate the tool list, alter or fabricate tool results, invoke any allow-listed tool, and burn through the local Anthropic API key. However, **it still cannot obtain an Entra token**, and it cannot bypass the tool allow-list or approval gates because those checks are enforced inside the MCP server. 
  * *Crucial Audit Rule:* Because a compromised harness can lie to the model, **the LLM's final report is never proof of what actually happened—only the Identity API's audit log is.**
* **Scenario 3: The MCP Server is Compromised**
  * *Impact:* High. Due to shortcut **D2** (where the sidecar lets its caller request tokens for any child of the blueprint), a compromised MCP server can obtain TR access tokens for *any* agent identity under that blueprint. It also does the write itself: its Graph token always carries the write role ([A1](#accepted-risks)), so it can disable any agent without asking anyone. And it writes the facts the approver sees, so it can mislead the approver ([A3](#accepted-risks)). Consequently, in the Guardian's current design, the MCP server is our most trusted component and its codebase must be kept as small and auditable as possible.
* **Scenario 4: The Sidecar is Compromised**
  * *Impact:* Critical. The blueprint's private certificate is exposed (**D3**), immediately compromising every agent identity governed by that blueprint *(see the blast radius note in [Agent ID](docs/agent-id.md#why-use-two-requests-instead-of-one))*.
* **Scenario 5: The Identity API is Compromised**
  * *Impact:* Moderate. It stores the disable requests and the decisions, so it can report "approved" for a request that nobody approved, and the MCP server then disables the agent. The damage has a limit: the MCP server waits only on requests it created itself, after its own checks, so only dormant agents that are not the guardian's own can be disabled this way. The Identity API also keeps the audit log, so it could change or drop lines, because the logs stay on this machine ([D6](docs/agent-steward-architecture.md#architectural-shortcuts-where-the-guardian-differs)). It holds no credential and never gets a token, so it cannot call Graph itself.
* **Scenario 6: An Approver's Account is Compromised**
  * *Impact:* Low to moderate. The attacker can sign in through the CLI as the approver, and approve or deny pending requests. They cannot create a request, so they can approve only the disables the MCP server already asked for: dormant agents that are not the guardian's own, while the request is pending (up to 4 minutes). Denying everything blocks the clean-up but disables nothing.
  * *The fix:* protect the account itself, for example with a Conditional Access policy that requires phishing-resistant MFA for the approvers group. This is not set up in the lab tenant.

## Accepted risks

Accepted for a 14-day lab. Each one has a known fix.

| # | Risk | OWASP risk | Why it matters | The fix |
|---|---|---|---|---|
| A1 | The guardian's Graph token always carries its write role, because `.default` gives every role it holds | ASI03 Identity & Privilege Abuse | A stolen read token can also disable agents | A separate identity for writes |
| A2 | The harness proves itself to the MCP server only by network location ([D1](docs/agent-steward-architecture.md#architectural-shortcuts-where-the-guardian-differs)) | ASI03 Identity & Privilege Abuse | Any code on that network can call the tools | A bearer token on that link |
| A3 | The MCP server holds the rules and does the write, and the Identity API trusts the facts it sends | ASI02 Tool Misuse & Exploitation, ASI09 Human-Agent Trust Exploitation | A compromised MCP server could mislead the approver, and it holds the write role | A domain service with its own identity, which reads Graph itself |
| A4 | An approval on an abandoned request is accepted, and nothing follows. `disable_abandoned` records why | ASI09 Human-Agent Trust Exploitation | The approver believes an action happened | The domain service does the write |
| A5 | "Report only" on the default task is a prompt rule. The [eval](docs/testing-ai-agents.md) passed 3 of 3 runs, which still allows a failure rate of up to about 63% | ASI01 Agent Goal Hijack, ASI09 Human-Agent Trust Exploitation | The model could ask to disable an agent on a routine run, though a person must still approve | The harness hides the tool on that task, and more trials and attacks |
| A6 | Pending requests are in memory, and sign-in uses a paste step | ASI08 Cascading Failures | A restart loses requests (nothing is disabled), and the step is clumsy | A database, and an approval web page |
| A7 | The blueprint's certificate and the model key are files, and audit logs stay local ([D3, D4, D6](docs/agent-steward-architecture.md#architectural-shortcuts-where-the-guardian-differs)) | ASI03 Identity & Privilege Abuse, ASI10 Rogue Agents | Someone on the host could take the keys or change the logs | Managed identity, federation, and a central log store |
