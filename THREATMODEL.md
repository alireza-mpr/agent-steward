# Threat Model

The guardian is one agent that reads an Entra tenant's agent registry, reports flaws, and can disable a dormant agent after a person approves. Its main attack path is text it reads from the directory, so the hard controls do not depend on the model behaving well. This page rates each [OWASP Top 10 for Agentic Applications (2026)](https://genai.owasp.org/2025/12/09/owasp-top-10-for-agentic-applications-the-benchmark-for-agentic-security-in-the-age-of-autonomous-ai/) risk and maps it to a [MAESTRO](https://cloudsecurityalliance.org/blog/2025/03/24/threat-modeling-openai-s-responses-api-with-the-maestro-framework) layer. The layer mapping is this project's own judgment.

## Contents

- [The risks](#the-risks)
- [Accepted risks](#accepted-risks)

## The risks

C numbers are in the [control register](docs/security-controls.md), with their evidence. A numbers are in [Accepted risks](#accepted-risks).

| Risk | Status | Why | Controls | Where | MAESTRO layer | Accepted |
|---|---|---|---|---|---|---|
| ASI01 Agent Goal Hijack | Mitigated | A planted attack failed in the [agent eval](tests/agent_eval/eval_prompt_injection.py), and the hard controls hold even if the model is fooled | C11, C13, C22-C25 | [server.py](src/mcp_server/server.py) | 1 Foundation Models, 3 Agent Frameworks | A5, A6 |
| ASI02 Tool Misuse & Exploitation | Mitigated | Three tools on an allow-list, checked inputs, and a person before the only write | C8, C9, C21-C25 | [tool-allow-list.json](config/tool-allow-list.json) | 3 Agent Frameworks | A3 |
| ASI03 Identity & Privilege Abuse | Mitigated | One agent identity, least privilege, and every token checked. An agent cannot approve | C1-C7, C26 | [token_validation.py](src/identity_api/token_validation.py) | 6 Security and Compliance | A1, A2, A7 |
| ASI04 Agentic Supply Chain Vulnerabilities | Mitigated | The sidecar image and Python packages are pinned. The history scan is still planned (NFR6) | C16, C18 | [docker-compose.yml](docker-compose.yml) | 4 Deployment and Infrastructure | - |
| ASI05 Unexpected Code Execution | Out of scope | No tool runs code, and the model's output is never executed. Containers run as non-root as a second layer | C19, C22 | [Dockerfile](src/mcp_server/Dockerfile) | 4 Deployment and Infrastructure | - |
| ASI06 Memory & Context Poisoning | Out of scope | No memory survives a run. Poisoned text within a run is ASI01 | C13 | [harness.py](src/guardian/harness.py) | 2 Data Operations | - |
| ASI07 Insecure Inter-Agent Communication | Out of scope | One agent. The fixture agents are registry entries and run no code | - | - | 7 Agent Ecosystem | - |
| ASI08 Cascading Failures | Mitigated | A limit on model calls, fail closed on errors, and one pending request per agent | C12, C23, C27 | [harness.py](src/guardian/harness.py) | 3 Agent Frameworks | A4 |
| ASI09 Human-Agent Trust Exploitation | Mitigated | The approver sees facts from Graph, the model's text is labelled untrusted, and only the full word approves. A person can still approve carelessly | C24, C27, C28 | [approve.py](src/approve_cli/approve.py) | 7 Agent Ecosystem | A3, A5 |
| ASI10 Rogue Agents | Mitigated | Rules flag bad agents, every decision is audited, and a disable path exists. Revocation is planned (FR11) | C14, C15, C24 | [rules.py](src/mcp_server/rules.py) | 5 Evaluation and Observability, 6 Security and Compliance | A7 |

## Accepted risks

Accepted for a 14-day lab. Each one has a known fix.

| # | Risk | Why it matters | The fix |
|---|---|---|---|
| A1 | The guardian's Graph token always carries its write role, because `.default` gives every role it holds | A stolen read token can also disable agents | A separate identity for writes |
| A2 | The harness proves itself to the MCP server only by network location ([D1](docs/agent-architecture.md#architectural-shortcuts-where-the-guardian-differs)) | Any code on that network can call the tools | A bearer token on that link |
| A3 | The MCP server holds the rules and does the write, and the Identity API trusts the facts it sends | A compromised MCP server could mislead the approver, and it holds the write role | A domain service with its own identity, which reads Graph itself |
| A4 | An approval on an abandoned request is accepted, and nothing follows. `disable_abandoned` records why | The approver believes an action happened | The domain service does the write |
| A5 | "Report only" on the default task is a prompt rule. The [eval](docs/testing-ai-agents.md) passed 3 of 3 runs, which still allows a failure rate of up to about 63% | The model could ask to disable an agent on a routine run, though a person must still approve | The harness hides the tool on that task, and more trials and attacks |
| A6 | Pending requests are in memory, and sign-in uses a paste step | A restart loses requests (nothing is disabled), and the step is clumsy | A database, and an approval web page |
| A7 | The blueprint's certificate and the model key are files, and audit logs stay local ([D3, D4, D6](docs/agent-architecture.md#architectural-shortcuts-where-the-guardian-differs)) | Someone on the host could take the keys or change the logs | Managed identity, federation, and a central log store |
