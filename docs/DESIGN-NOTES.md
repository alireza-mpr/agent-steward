# DESIGN-NOTES

Patterns that belong to a production version of this system but were deliberately kept out of code in the timebox. None of them is built, except section 6: OBO is now built for the approved disable. Each section says what the pattern is, why it matters for agents, and how it would attach to this architecture.

## Contents

- [1. Token exchange (RFC 8693) - narrowing tokens](#1-token-exchange-rfc-8693---narrowing-tokens)
- [2. AIMS - AI Identity Management System](#2-aims---ai-identity-management-system)
- [3. Agent 365 governance](#3-agent-365-governance)
- [4. Dynamic agent registration - the CDR parallel](#4-dynamic-agent-registration---the-cdr-parallel)
- [5. Workload identity federation for non-Microsoft agents](#5-workload-identity-federation-for-non-microsoft-agents)
- [6. Delegation and On-Behalf-Of (OBO) for agents](#6-delegation-and-on-behalf-of-obo-for-agents)
- [7. Revoking live tokens](#7-revoking-live-tokens)
- [Future work](#future-work)

## 1. Token exchange (RFC 8693) - narrowing tokens

- **What:** a client trades a token it holds for a new one, usually for another audience or with less scope. It can also name an actor, and the new token then records who acts for whom in an `act` claim.
- **Why for agents:** an agent should never forward its full-power token downstream. Each hop gets the minimum, and the audit can see the whole chain.
- **How it would attach here:** the guardian has no incoming token to exchange, because it acts as itself and the sidecar gets a new token for each API. Exchange would matter on a chain of services. It could also have addressed [A1](../THREATMODEL.md#accepted-risks), because a read-only token could be narrowed from a broader one. A1 was closed another way, with OBO for the write. Entra's app-only tokens cannot do that today: `.default` gives every role the agent holds.
- **Relation to OBO:** both solve the same problem, but Entra's OBO is not RFC 8693 ([comparison](jwt-bearer-and-token-exchange.md#52-obo-is-not-rfc-8693)).

## 2. AIMS - AI Identity Management System

- **What:** an [IETF Internet-Draft](https://datatracker.ietf.org/doc/html/draft-ietf-wimse-aims) in the WIMSE working group (`draft-ietf-wimse-aims-00`, September 2026, intended as Informational). It is a model of the functions an agent's identity needs, and it composes existing standards (WIMSE, SPIFFE, OAuth 2.0) instead of defining new protocols. It may still change.
- **What problem it solves:** Entra Agent ID is one vendor's product. AIMS gives a vendor-neutral way to describe the same functions, so identity can work across platforms.

The mapping below is this project's own reading of the draft:

| AIMS function | This project | Gap |
|---|---|---|
| Identifier | Each agent identity's `appId` | Not a WIMSE or SPIFFE identifier |
| Credentials and provisioning | The blueprint's credential, and short-lived tokens | The certificate and the model key are files ([A7](../THREATMODEL.md#accepted-risks)). The draft treats static API keys as an antipattern. |
| Authentication | Entra's two token requests | The harness to MCP server hop has no token ([A2](../THREATMODEL.md#accepted-risks)) |
| Authorization | OAuth, with roles checked at each API, and OBO for the approved disable | Delegated mode for that one fix only ([section 6](#6-delegation-and-on-behalf-of-obo-for-agents)) |
| Observability and remediation | Audit lines, and a disable path with human approval | No detection of an active agent misbehaving |
| Policy and compliance | The registry rules and the permission policy file | Local files, not a central policy service |

Two of the draft's rules match choices already made here. A tool must not forward a token it received, and an agent must not treat local UI confirmation alone as authorization. In this project, the approval is a decision checked by the Identity API, from a person's own token, and the executor writes only with that person's own token.

## 3. Agent 365 governance

- **What it adds:** Microsoft says [Agent 365](https://learn.microsoft.com/en-us/microsoft-agent-365/overview) is the registry and control plane for agents, and Entra provides the identity through Agent ID ([announcement](https://www.microsoft.com/en-us/security/blog/2026/05/01/microsoft-agent-365-now-generally-available-expands-capabilities-and-integrations/), generally available 1 May 2026).
- **Overlap with the guardian:** I expect a real deployment to take the inventory from Agent 365, not from its own scan. The custom rules, the prompt injection boundary and the approval gate would stay until the product covers them. This project did not test Agent 365 in a tenant, so that is a judgment, not a finding.
- **Licensing reality:** viewing the inventory needs an admin role. Controls such as Conditional Access for agents need a license ([notes](entra.md#6-agent-id-licensing--trials)). Microsoft's pages differ on the prerequisites, so check the current service description before relying on this.

## 4. Dynamic agent registration - the CDR parallel

- CDR solved third-party onboarding with a central register of accredited recipients, a signed software statement (SSA), and Dynamic Client Registration (RFC 7591).
- The agent world is rebuilding similar machinery: agent registry and CDR Register, blueprint and software statement, identity creation from a blueprint and DCR. The match is loose: identity creation is a Graph call, not RFC 7591, and the registry is per tenant ([glossary](GLOSSARY.md)).
- The MCP spec (2026-07-28) now prefers Client ID Metadata Documents, and keeps RFC 7591 only as an optional, deprecated path for older servers.
- This project shows static, scripted registration from a blueprint. Production would add runtime creation of agent instances, attestation, and revocation at registry level.
- First-hand experience: I operated the receiving end of CDR's DCR at a major bank - onboarding, credential and certificate lifecycle, revocation and audit for 450 B2B client identities.

## 5. Workload identity federation for non-Microsoft agents

- **What:** an agent running outside Azure (AWS, a local container) gets an Entra agent identity with no secret, by federating a token its platform issues.
- **Why:** it removes long-lived cross-cloud secrets.
- **How it would attach here:** the blueprint's certificate would be replaced by a federated identity credential that trusts the platform's token. That closes the credential half of [A7](../THREATMODEL.md#accepted-risks).

## 6. Delegation and On-Behalf-Of (OBO) for agents

This is the general design for FR4. The guardian's scan runs autonomously, and the approved disable now uses OBO, in the executor ([D5](agent-steward-architecture.md#architectural-shortcuts-where-the-guardian-differs)). What was built differs from the general design in places, listed below.

- **The problem:** a user signs in and asks the agent to act for them. The user's token, Tc, is issued for the blueprint. Graph would refuse it, and forwarding it would be token passthrough. So the agent asks Entra for a new token for the downstream API, and the user stays in that token.
- **The flow:** request 1 does not change: the blueprint gets T1. Request 2 changes ([Microsoft's flow](https://learn.microsoft.com/entra/agent-id/agent-on-behalf-of-oauth-flow), [details](authentication.md#part-a-a-workload-proves-itself-to-entra-id)):

| Request 2 | Autonomous (built) | OBO (built for the approved disable) |
|---|---|---|
| Grant | `client_credentials` | `urn:ietf:params:oauth:grant-type:jwt-bearer` |
| Proof of the agent | T1, as `client_assertion` | T1, as `client_assertion` |
| The user's token | None | Tc, as `assertion` |
| Permissions in TR | `roles`, from app roles an admin assigned | `scp`, from delegated permissions an admin consented to, limited by what the user may do |
| Conditional Access | Policies for agents | The user's policies |

- **What was built, against this design:**
  1. **Who signs in, and when:** not before the task. The agent proposes, then the approver signs in to the CLI and decides. The model never sees the token.
  2. **Where Tc goes:** to the executor, not to the harness. So the harness to MCP server hop still carries no token, and [A2](../THREATMODEL.md#accepted-risks) stays open.
  3. **The scope and the consents:** built. The blueprint exposes `access_as_user`, the CLI is granted it, and the delegated Graph permission is granted on the guardian's agent identity directly.
  4. **Telling the modes apart:** the executor checks Tc itself (`scp`, and the approver's `oid`). The Identity API sees only the guardian's app-only tokens, so it has no OBO mode to detect.
  5. **The audit line:** the executor's `agent_disabled` line has `mode: obo`, the agent identity that acted (`agent_app_id`), the delegating user and the approver.
- **The trade-off:** the scan runs with no person present, so it stays autonomous. A write could use OBO, so the guardian would not need an app-only write role, and that would address [A1](../THREATMODEL.md#accepted-risks). This is now built for the approved disable, in the executor ([the flow](agent-steward-architecture.md#the-two-phases-propose-then-approve)). The cost is that a write then needs a user who holds the rights, and a signed-in user is not by itself an approval of one action.
- **What exists today:** the CLI uses a person's delegated token, with `scp` and `roles`, and the Identity API checks it: that alone is delegated access, not OBO. After an approval, the executor exchanges the person's second token, Tc, with OBO, so the disable is OBO.

## 7. Revoking live tokens

- **The gap:** disabling an agent identity stops new tokens, but issued tokens work until they expire. A Graph token observed in this project lived 65 minutes.
- **The options, from cheapest:**
  1. Short lifetimes, where the platform allows them.
  2. The API checks the agent's state on each call or with a short cache, which costs a call or some delay.
  3. A kill list at the API, fed by the disable event.
  4. Continuous Access Evaluation: not an option for the Identity API. Its events reach only Microsoft's resources; for workload identities, only Microsoft Graph.
- **More detail:** each option, with its steps, cases and sources, is in [Conditional Access and revocation](conditional-access-and-revocation.md#3-when-a-token-already-exists).
- **Status:** FR11 is not built. The disable itself is built and was run live ([FR7](delivery-status.md)).

## Future work

Ideas that came up during the build but are outside of the current scope. One line each, with the reason it was left out. None of these are built as of the writing of this document.

- **CI/CD deployment of `setup/`** with GitHub Actions and OIDC federation: no stored secrets, a dedicated deploy identity, Contributor on one resource group, Graph application permissions, and a GitHub environment approval gate. Left out: federation is design-only in this project (see section 5).
- **The harness signs in to the MCP server (closes D1):** the harness gets a token as the guardian's agent identity, with the MCP server as its audience. The MCP server validates it (MCP authorization, with RFC 9728 protected resource metadata), and the tool allow-list becomes a role-to-tool mapping per caller. An MCP gateway in front of shared MCP servers is the option when many agents use them. This is the standard design ([pillar 2](agent-architecture.md#four-architectural-pillars)), planned as the first step after the finalisation. Left out for now: the finalisation scope keeps D1 open, and the timebox.
- **One identity per workload (closes D2):** only the harness signs in as the guardian's agent identity. The MCP server gets its own identity and calls the APIs with its own tokens, with no token passthrough. The executor becomes a domain service with its own identity: the OBO grant for the disable moves to it, so the approver stays the subject and the executor is the actor, and the blueprint's certificate leaves the executor's sidecar. The cost: after the MCP server, the guardian shows in the logs only through the request and the audit lines, not in the tokens. Planned together with the harness sign-in, after the finalisation. Left out for now: the timebox, and the OBO run of 10 November 2026 used today's design.
- **Revocation before a token expires:** today the Identity API accepts a disabled agent's token until it expires (60 to 90 minutes by default in Entra). The fix: for high-risk calls, check the identity's status live, with token introspection (RFC 7662) or a deny list, and clear any cache on push events (OpenID Shared Signals: CAEP and RISC, or Entra's Continuous Access Evaluation where the resource supports it). Left out: revocation (FR11) is not built yet, and the timebox.
- **One identity per set of tool permissions:** a tool that needs one Graph role runs as an agent identity that holds only that role, so its token carries nothing more. Today a token carries all the guardian's roles on its API ([Tool permissions](tool-permissions.md#where-the-guardian-differs)). Left out: one identity per workload (D2, above) comes first, and the roles the tools share are all reads or requests, with no write.
- **Security evals as a gate:** the guardian's detection rate and false positives on the fixtures, more prompt injection variants, and a baseline score that a change cannot fall below. Left out: cut on 12 November 2026 to finish the project. The prompt injection eval of NFR3 ([eval_prompt_injection.py](../tests/agent_eval/eval_prompt_injection.py)) stays.
