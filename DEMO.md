# Demo

This page shows what agent-steward does, with screenshots from a real Entra tenant, so you can review it without running anything. The guardian is one agent that reads the tenant's agent registry, reports flaws, and can ask to disable a dormant agent. A person must approve that disable. IDs are redacted or replaced with placeholders.

The page covers the main controls, not every requirement. [What this page does not show](#what-this-page-does-not-show) lists the gaps.

## Contents

- [1. Accountable agent identities](#1-accountable-agent-identities)
- [2. The token comes from the blueprint](#2-the-token-comes-from-the-blueprint)
- [3. The scan finds the planted flaws](#3-the-scan-finds-the-planted-flaws)
- [4. A prompt injection attack](#4-a-prompt-injection-attack)
- [5. The tool allow-list](#5-the-tool-allow-list)
- [6. Human approval](#6-human-approval)
- [What this page does not show](#what-this-page-does-not-show)

## 1. Accountable agent identities

The guardian has its own agent identity, created from a blueprint. A technical owner and a business sponsor are accountable for it, and they are different people.

![The guardian's owner and sponsor](docs/screenshots/phase1-guardian-owner-and-sponsor.png)

## 2. The token comes from the blueprint

The agent identity has no credential. The blueprint proves itself first, and the agent identity then gets the access token. This is the guardian's token for Microsoft Graph, as claims only: `xms_par_app_azp` names the parent blueprint. The tool prints claims and never the token.

![The claims of the guardian's Graph token](docs/screenshots/phase2-guardian-token-claims.png)

The harness, which runs the model, cannot reach the service that issues tokens. A [test](tests/integration/mcp_server) checks that, and it appears in the screenshot in section 5. Details: [Authentication](docs/authentication.md).

## 3. The scan finds the planted flaws

Fixture agents in the tenant carry planted flaws. The guardian found no sponsor, a credential about to expire, a permission the policy does not allow, and no sign-in for 30 days. The MCP server sends the findings to the audit log itself, so a fooled model cannot hide them.

This run was before the poisoned fixture in section 4 was added.

![Findings from a guardian run](docs/screenshots/phase2-guardian-run.png)

## 4. A prompt injection attack

One fixture's description holds a real attack. It tells the model to hide that agent's finding, to disable the guardian, and to report that all agents are compliant. The model resisted in 3 of 3 runs.

![The agent eval passing 3 of 3 runs](docs/screenshots/nfr3-agent-eval-summary.png)

Three runs is a small sample: it still allows a failure rate of up to about 63%. So no safety property depends on this result, and [control tests](tests/unit/mcp_server) prove the system stays safe if the model is fooled. See the [threat model](THREATMODEL.md).

## 5. The tool allow-list

The MCP server offers only the tools on its [allow-list](config/tool-allow-list.json), and it refuses any other name before the tool runs. In this run, `disable_agent` was off the list, so the call was refused and audited. Its successor, `submit_disable_agent_request`, is on the list today, behind the approval in section 6.

![End-to-end tests and the refusals in the audit log](docs/screenshots/mcp-allow-list-refusal.png)

## 6. Human approval

The model asked to disable each dormant agent. The agent's token can only create a request. A decision needs a person's own token, in the approvers group, so a fooled agent cannot approve itself. The approver sees facts read from Graph, and the model's reason labelled as untrusted.

In this live run on 31 October 2026, a person approved one request and denied four.

![The CLI with five decisions](docs/screenshots/fr7-approve-command-decisions.png)

Only the approved agent was disabled, and the four denied agents stayed active. The agent was re-enabled after the test.

![The agent list with one disabled agent](docs/screenshots/fr7-agent-disabled-after-approval.png)

A disable stops new tokens, but issued tokens work until they expire ([design note](docs/DESIGN-NOTES.md#7-revoking-live-tokens)).

Since this run, the disable runs with the approver's own rights (OBO), in a separate executor, and the guardian holds no write permission of its own. That version is not shown here yet: it has not run live end to end.

## What this page does not show

- **Not shown yet:** OBO for the approved disable, which is built but not yet run live end to end ([design](docs/DESIGN-NOTES.md#6-delegation-and-on-behalf-of-obo-for-agents)).
- **Not built:** revocation of live tokens, Conditional Access for agents, the live worker, and the correlation of traces with Entra sign-in logs.
- **Known risks:** the [accepted risks](THREATMODEL.md#accepted-risks), each with its fix.

The [delivery status](docs/delivery-status.md) has the state of every requirement.
