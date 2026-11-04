# Agent Steward Architecture: The Guardian's Design and Decisions

This document shows how this project's **Guardian** agent is built today: its parts, its main flows, and every shortcut it takes from the enterprise design, with the risk each one accepts. For the general design that the Guardian is compared with, see [Agent Architecture: The Enterprise Reference Design](agent-architecture.md). The decisions behind this design, with their reasons, are in the [decision log](decisions.md).

> **Current Implementation Status: Partly Built**
> * **Built:** The Entra identity, the sidecar (Microsoft Entra ID Auth SDK), the Identity API, the MCP server with three tools (`scan_registry` runs the Guardian's four registry rules, `get_agent` reads one agent, `disable_agent` disables a dormant agent after a human approves) and its tool allow-list, and the harness's agent loop with Claude. The Guardian's container cannot reach the sidecar, and an MCP integration test proves it.
> * **Not built yet:** The correlation-ID scaffold across all components.
> 
> *(See [Delivery status](delivery-status.md) for live progress tracking).*

## Contents

* [1. The Guardian's Design Today](#1-the-guardians-design-today)
  * [Why the Guardian Uses a Model](#why-the-guardian-uses-a-model)
  * [Component Inventory: Who Holds What?](#component-inventory-who-holds-what)
  * [The Main Flow at a Glance](#the-main-flow-at-a-glance)
  * [The Human Approval Flow: `disable_agent`](#the-human-approval-flow-disable_agent)
  * [Architectural Shortcuts (Where the Guardian Differs)](#architectural-shortcuts-where-the-guardian-differs)
* [2. Blast Radius: What Happens If One Part Is Compromised?](#2-blast-radius-what-happens-if-one-part-is-compromised)

---

## 1. The Guardian's Design Today

While our **Guardian** agent shares the same four-part conceptual anatomy, it runs locally on a single machine via Docker Compose. To fit our project's timebox, it takes six deliberate architectural shortcuts (labeled **D1** through **D6** in the diagram and detailed in [Architectural Shortcuts](#architectural-shortcuts-where-the-guardian-differs), at the end of this section). The decisions behind this design, with their reasons, are recorded in the [decision log](decisions.md).

```mermaid
flowchart LR
    MODEL["<b>Model</b><br/>Claude (Anthropic)"]
    subgraph COMPOSE["Docker Compose, one machine"]
        HARNESS["<b>Harness</b><br/>agent loop · D4"]
        subgraph NS["sidecar's network namespace"]
            MCP["<b>MCP server</b><br/>allow-list, approval gate · D2"]
            SIDECAR["<b>Sidecar</b><br/>certificate · D3"]
        end
        API["<b>Identity API</b><br/>approvals, audit log · D6"]
    end
    ENTRA["<b>Entra ID</b><br/>autonomous only · D5"]
    GRAPH["<b>Microsoft Graph</b>"]

    HARNESS <-->|"prompts, tool calls"| MODEL
    HARNESS -->|"MCP, no token · D1"| MCP
    MCP -->|"loopback: get TR"| SIDECAR
    SIDECAR -->|"T1, then TR"| ENTRA
    MCP -->|"TR"| GRAPH
    MCP -->|"TR"| API
```

### Why the Guardian Uses a Model

Every safety decision is deterministic, on purpose. The MCP server's rules decide which agents are flagged, the allow-list decides which tools exist, the MCP server checks each disable again (the agent must be dormant and not the guardian's own), and a human approves each disable. The operator's `--disable-dormant` option only changes the model's task, so it is not a control.

The model is used where rules fail: scanning unstructured data, such as an agent's description, for malicious text. A rule could search for known words, but an attacker can reword the text easily. In the NFR3 agent eval, it flagged the planted prompt injection in a description on its own ([eval_prompt_injection.py](../tests/agent_eval/eval_prompt_injection.py)).

So if the model is wrong, the system stays safe, just less useful.

### Component Inventory: Who Holds What?

To verify our security posture, here is exactly what each container holds and what it can reach over the network:

| Component | Agent Part | Credentials Held | Network Reach |
|---|---|---|---|
| **Claude** | Model | *Nothing* | *Nothing* (only responds to incoming requests from the `guardian`). |
| **`guardian`** | Harness | Anthropic API key | Can reach Claude and `mcp-server`. **Cannot reach `sidecar`** (meaning it can never obtain an Entra token). |
| **`mcp-server`** | Tools | *No stored secrets* (requests TR tokens on-demand from `sidecar`). | Can reach `sidecar`, Microsoft Graph, and `api`. |
| **`sidecar`** | Token acquisition | The blueprint's certificate file. | Can reach Entra ID. Listens strictly on loopback ([details](infra.md#local-run-the-sidecar)). |
| **`api`** | Resource API | *No secrets* (validates tokens using Entra's public signing keys). | *Nothing* (only responds to incoming API calls). |

*(Transport Note: The communication between the harness and the MCP server uses **MCP over Streamable HTTP**, the standard transport for standalone networked MCP services. The client sends each MCP message as an HTTP POST to a single `/mcp` endpoint, and the server either replies with a standard JSON payload or streams the response back over the same connection).*

### The Main Flow at a Glance

One run of the guardian on the `--disable-dormant` task, from the scan to the disable. Sign-in and tokens are left out: they are in [Authentication](authentication.md) and in the next section.

```mermaid
sequenceDiagram
    actor O as Operator
    participant H as Harness
    participant L as Model
    participant M as MCP server
    participant G as Microsoft Graph
    participant A as Identity API
    participant C as CLI
    actor P as Approver

    O->>H: start a run (--disable-dormant)
    H->>M: tools/list
    M-->>H: the tools on the allow-list
    H->>L: task and tool list
    L-->>H: call scan_registry
    H->>M: scan_registry
    M->>G: read agents, blueprints, sign-ins
    G-->>M: the data
    M->>M: run the four rules
    M->>A: send the findings (audit log)
    M-->>H: the findings
    H->>L: the findings

    L-->>H: tool calls: disable_agent(agent 1),<br/>disable_agent(agent 2), ...

    loop For each disable_agent call, one at a time
        H->>M: disable_agent
        M->>G: read the agent and its sign-ins
        M->>M: check: dormant, not the guardian's own
        M->>A: create a disable request (facts and reason)
        A-->>M: request pending

        par The approver decides
            P->>C: start the CLI
            C->>A: get pending requests
            A-->>C: the request
            C->>P: show facts, and the reason as untrusted
            P->>C: approve or deny
            C->>A: send the decision
        and The MCP server waits
            loop every 5 seconds, at most 4 minutes
                M->>A: get the request's status
                A-->>M: pending, approved, denied or expired
            end
        end

        alt Approved
            M->>G: disable the agent
            M->>G: read it back to confirm
            M-->>H: disabled, and who approved
        else Denied or expired
            M-->>H: not disabled
        end
    end

    H->>L: all the results
    L-->>H: the final report
```

* **The MCP server does all the Graph work:** reading, the rules and the disable.
* **The Identity API only stores things:** findings, requests and decisions.
* **The approver and the MCP server never talk directly.** The Identity API connects them.
* **The model chooses, the harness loops.** The model can ask for several disables in one answer. The harness runs the calls one at a time, and each waits for its own approval.

### The Human Approval Flow: `disable_agent`

`disable_agent` disables one dormant agent identity, and only after a human approves (FR7). The MCP server does the checks and the write. The model only asks. The Identity API records who decided.

> **Run live on 31 October 2026:** one approval disabled a fixture, and a read confirmed it. Four denials left their fixtures enabled.

```mermaid
sequenceDiagram
    actor O as Operator
    participant H as Harness and model
    participant M as MCP server
    participant G as Microsoft Graph
    participant A as Identity API
    participant C as approve command
    participant E as Entra ID
    actor P as Approver

    O->>H: main.py --disable-dormant
    H->>M: tools/call disable_agent(app_id, reason)
    M->>M: check the allow-list and that app_id is a GUID
    M->>G: read the agent and its sign-ins (sidecar's token)
    G-->>M: the facts

    alt The agent is unknown, is the guardian's own, or is not dormant
        M-->>H: tool error: refused, and nobody is asked
    else The agent can be disabled
        M->>A: POST /agents/{agentId}/disableRequests<br/>(facts from Graph, the model's reason)
        A-->>M: 201, request pending<br/>(409 if one is already waiting: tool error, not disabled)
        Note over M,A: From here the MCP server polls (loop below) while the approver decides

        P->>C: docker compose run --rm approve
        C->>E: authorization code flow with PKCE
        P->>E: sign in, then paste the final address into the command
        E-->>C: the person's token (scp, roles)
        C->>A: GET /disableRequests?status=pending
        A-->>C: pending requests
        C->>P: show the facts, and the reason labelled untrusted
        P->>C: approve or deny
        C->>A: POST .../{requestId}/decision
        A->>A: check the token (scp, roles, the approve command as client),<br/>store who decided

        loop Every 5 seconds, for at most 4 minutes
            M->>A: GET /agents/{agentId}/disableRequests/{requestId}
            A-->>M: status, and who decided
        end

        alt Approved, matches the request, not used before
            M->>G: PATCH accountEnabled = false
            G-->>M: done
            M->>G: read accountEnabled until it shows false, or give up
            G-->>M: the current value
            M-->>H: result: approver's name, request ID, confirmed
        else Denied, no decision in time, mismatch, reused, or service unreachable
            M-->>H: tool error: not disabled (fail closed)
        end
    end
```

**Where the guardian differs from [the general practice](agent-architecture.md#human-approval-who-approves-and-how):**

| General practice | The guardian | Why |
|---|---|---|
| A domain service does the write, with its own identity | The MCP server writes, as the guardian, which holds `AgentIdentity.EnableDisable.All` | Timebox |
| A domain service checks the rules and reads the facts itself | The MCP server does both, and the Identity API trusts the facts it sends | Timebox |
| A notification delivers the request | The approver runs the `approve` command, which lists what is pending | No web page in the timebox |
| A local listener or a web page catches the sign-in redirect | The person pastes the final address into the terminal | The command runs in a container |
| A durable store | In memory. A restart loses pending requests, and nothing is disabled (fail closed) | Lab |
| An approval is refused once nobody is waiting for it | The Identity API still accepts it, and nothing follows. The MCP server writes `disable_abandoned`, so the record explains why | Known limit |

The next version would use URL-mode elicitation and an approval web page, on the same identity design.

#### The Approver's Sign-In and Decision, Step by Step

The `approve` command ([approve.py](../src/approve_cli/approve.py)) is the client application `agent-steward-agent-disable-cli`. It gets a delegated token for the Identity API with the authorization code flow and PKCE ([ADR-002](decisions.md#adr-002-the-approver-signs-in-with-the-authorization-code-flow-not-the-device-code-flow)). No OBO happens: the command calls the Identity API with this token directly.

```mermaid
sequenceDiagram
    autonumber
    actor P as Approver (and browser)
    participant C as approve command<br/>(agent-steward-agent-disable-cli)
    participant E as Entra ID
    participant A as Identity API

    P->>C: docker compose run --rm approve
    C->>C: MSAL makes code_verifier, code_challenge and state
    C->>P: print the sign-in address: /authorize?<br/>client_id=#lt;CLI app ID#gt;, response_type=code,<br/>redirect_uri=http://localhost:18400,<br/>scope=#lt;Identity API app ID#gt;/.default openid profile offline_access,<br/>code_challenge, code_challenge_method=S256, state,<br/>prompt=select_account
    P->>E: open the address, sign in (password, MFA)
    E->>E: the CLI's grant: AgentDisable.Decide, for all users (admin consent)<br/>the person's roles on the Identity API
    E-->>P: redirect to http://localhost:18400/?code=...#amp;state=...<br/>(the page fails to load, as expected)
    P->>C: paste the address
    C->>E: POST /token: grant_type=authorization_code,<br/>client_id, code, code_verifier, redirect_uri<br/>(MSAL first checks that state matches)
    E->>E: check code_verifier against code_challenge
    E-->>C: the approver's access token<br/>(aud: the Identity API, azp: the CLI, oid: the person, idtyp: user,<br/>scp: AgentDisable.Decide, roles: AgentDisable.Approve)

    loop Every 5 seconds, until Ctrl+C
        C->>A: GET /disableRequests?status=pending + Bearer token
        A->>A: check_approver: signature, issuer, aud, exp,<br/>idtyp is user, not an agent, azp is the CLI,<br/>scp has AgentDisable.Decide, roles has AgentDisable.Approve
        A-->>C: pending requests
    end

    C->>P: show the facts, and the model's reason labelled untrusted
    P->>C: approve (the full word) or deny
    C->>A: POST /agents/{agentId}/disableRequests/{requestId}/decision + Bearer token
    A->>A: the same checks, then decide once, under a lock,<br/>and record the approver's oid and name
    A-->>C: the decision (409 if already decided or expired)
```

The token answers two questions: `scp` says the CLI may send decisions for this person, and `roles` says this person may approve. `azp` is a third check, in case the scope is ever granted to another app by mistake.

### Architectural Shortcuts (Where the Guardian Differs)

| ID | Enterprise Reference Design | The Guardian Today | Why We Took the Shortcut | Risk Accepted |
|---|---|---|---|---|
| **D1** | The harness sends a bearer token to the MCP server, which validates it. | **No token:** The MCP server relies on Docker network isolation, trusting that only the Guardian container can reach it. Because it does not know who calls it, its tool allow-list is one list for every caller, not a role-to-tool mapping per agent. | Project timebox. | Any code that manages to reach the MCP server's network port can invoke allow-listed tools. |
| **D2** | The MCP server has its own Entra identity and serves multiple agents. | **Shared Identity:** The MCP server serves *only* the Guardian and fetches tokens using the Guardian's identity (it acts as the Guardian itself). | Keeping one identity and one sidecar is simpler, and ensures no Entra token ever reaches the harness. | The MCP server cannot be shared with other agents. If compromised, it can request tokens for *any* agent identity under the blueprint (since the sidecar lets callers specify which agent identity they want). |
| **D3** | The blueprint authenticates via an Azure Managed Identity (zero keys on disk). | **Certificate File:** A certificate file is mounted exclusively into the sidecar container ([details](infra.md#the-blueprints-credential)). | The demo runs locally in Docker, not in Azure. | If the certificate file is stolen from disk, every agent identity tied to the blueprint is exposed. |
| **D4** | No static model key: the harness authenticates to the model provider with workload identity federation (or, failing that, reads a key from a cloud vault). | **API Key File:** An Anthropic service account API key in `secrets/anthropic.env` (the folder is excluded via `.gitignore`), given only to the guardian container. | Local demo environment, with no managed identity to federate. | The Anthropic API key sits unencrypted on the host disk. |
| **D5** | Supports Delegated mode with OBO when a human user is present. | **Autonomous Mode Only:** OBO exists strictly as written documentation. | Project timebox. | No human user ever appears in the token chain. |
| **D6** | Audit logs and distributed traces ship to a central SIEM/sink. | **Local Logs:** Audit lines and traces are written locally. One trace ID across the Guardian, the MCP server and the Identity API is planned (FR8). | Project timebox. | All audit logs reside on the same single machine. |

For the credential options behind D2, D3 and D4, and how to move from the Guardian's shortcuts to the enterprise design, see [Credentials and Tokens](authentication.md#3-credentials-and-tokens-how-a-workload-gets-them).

---

## 2. Blast Radius: What Happens If One Part Is Compromised?

We separate the agent into distinct trust boundaries specifically to contain the blast radius when something goes wrong. Here is how the Guardian's architecture holds up if individual components fail:

* **Scenario 1: The Model Is Tricked (Prompt Injection)**
  * *Impact:* Minimal (this is the primary failure mode the architecture is built to handle). The model can only *ask* the harness to invoke tools. The MCP server's checks (the tool allow-list, its input checks, its own check that an agent is dormant and not one of the guardian's own, and the human approval gate) and the harness's turn limit all remain strictly enforced, and the model never sees an Entra token.
* **Scenario 2: The Harness is Compromised**
  * *Impact:* Moderate. Because the harness mediates all communication with the model, it can manipulate the tool list, alter or fabricate tool results, invoke any allow-listed tool, and burn through the local Anthropic API key. However, **it still cannot obtain an Entra token**, and it cannot bypass the tool allow-list or approval gates because those checks are enforced inside the MCP server. 
  * *Crucial Audit Rule:* Because a compromised harness can lie to the model, **the LLM's final report is never proof of what actually happened—only the Identity API's audit log is.**
* **Scenario 3: The MCP Server is Compromised**
  * *Impact:* High. Due to shortcut **D2** (where the sidecar lets its caller request tokens for any child of the blueprint), a compromised MCP server can obtain TR access tokens for *any* agent identity under that blueprint. It also does the write itself: its Graph token always carries the write role ([A1](../THREATMODEL.md#accepted-risks)), so it can disable any agent without asking anyone. And it writes the facts the approver sees, so it can mislead the approver ([A3](../THREATMODEL.md#accepted-risks)). Consequently, in the Guardian's current design, the MCP server is our most trusted component and its codebase must be kept as small and auditable as possible.
* **Scenario 4: The Sidecar is Compromised**
  * *Impact:* Critical. The blueprint's private certificate is exposed (**D3**), immediately compromising every agent identity governed by that blueprint *(see the blast radius note in [Agent ID](agent-id.md#why-use-two-requests-instead-of-one))*.
* **Scenario 5: The Identity API is Compromised**
  * *Impact:* Moderate. It stores the disable requests and the decisions, so it can report "approved" for a request that nobody approved, and the MCP server then disables the agent. The damage has a limit: the MCP server waits only on requests it created itself, after its own checks, so only dormant agents that are not the guardian's own can be disabled this way. The Identity API also keeps the audit log, so it could change or drop lines, because the logs stay on this machine ([D6](#architectural-shortcuts-where-the-guardian-differs)). It holds no credential and never gets a token, so it cannot call Graph itself.
* **Scenario 6: An Approver's Account is Compromised**
  * *Impact:* Low to moderate. The attacker can sign in through the `approve` command as the approver, and approve or deny pending requests. They cannot create a request, so they can approve only the disables the MCP server already asked for: dormant agents that are not the guardian's own, while the request is pending (up to 4 minutes). Denying everything blocks the clean-up but disables nothing.
  * *The fix:* protect the account itself, for example with a Conditional Access policy that requires phishing-resistant MFA for the approvers group. This is not set up in the lab tenant.

