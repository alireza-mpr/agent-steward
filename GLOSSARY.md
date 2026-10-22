# GLOSSARY - ubiquitous language

A living translation table between the identity world I come from (OAuth2, OIDC, FAPI, Australia's Consumer Data Right) and the world this project lives in (Microsoft Entra, Agent ID, MCP).

Two rules keep this table honest:

1. Every mapping is a **learning scaffold**, not an exact equivalence. The last column says where the analogy breaks.
2. A term is added the first time it appears in a build phase, not before. If a row has no "breaks" note yet, it is unverified.

## Core identity objects

| Known world (OAuth2/OIDC/CDR) | New world (Entra / Agent ID) | Where the analogy breaks |
|---|---|---|
| Authorization server / IdP | Microsoft Entra ID (the tenant's token issuer) | Entra is also the directory and policy engine, not only the token issuer. |
| OAuth client (the definition of an app) | App registration | Close. One registration can serve many tenants. |
| Client instance operating in an ecosystem | Service principal / enterprise application | CDR had no split between "definition" and "per-tenant instance"; Entra does, and it matters. |
| Non-human identity (my 450 B2B client identities) | Workload identity (umbrella: service principal, managed identity, agent identity) | Entra's umbrella is wider and has first-class sub-types with different lifecycles. |
| - (no equivalent) | Managed identity | No CDR analogue: the platform owns the credential, the app never sees one. |
| Accredited software product + its SSA | Agent identity blueprint | The blueprint is a live template that mints identities; an SSA was a one-shot signed statement. |
| CDR Register (who is accredited, who is accountable) | Agent registry (Agent ID blade; Agent 365 registry) | The CDR Register was ecosystem-wide and regulator-run; the agent registry is per-tenant and self-governed - which is exactly why owner/sponsor fields exist. |
| Dynamic Client Registration (RFC 7591) | Programmatic agent identity creation from a blueprint; also literal DCR in MCP client onboarding | Blueprint creation is Graph-API-driven, not the RFC 7591 protocol; MCP, however, uses the actual RFC. |

## Flows and delegation

| Known world | New world | Where the analogy breaks |
|---|---|---|
| Client credentials grant | Autonomous agent mode: token via blueprint, no human in the token | Same grant underneath; the difference is agent-specific claims and the blueprint as credential source. |
| Authorization code flow + consent | Admin sign-in (delegated access) | Consent in Entra is split: user consent vs admin consent, per permission. CDR consent was consumer-arrangement based. |
| My CDR chain: data recipient -> OB IdP -> customer IdP -> user | Agent chain: agent -> Entra (OBO) -> downstream API -> acting human | Same shape: federation + delegation, each hop re-proven by signed tokens. Breaks: the agent is a new actor type that can initiate on its own. |
| Token exchange for delegation | On-behalf-of (OBO) flow | OBO is Entra's proprietary shape of the idea; RFC 8693 is the standard shape. Both appear in this project (OBO in code, 8693 in DESIGN-NOTES.md). |
| Scopes | Delegated permissions vs application permissions | One word in OAuth2 became two permission systems in Entra, keyed to whether a human is in the token. |
| Resource server validating tokens | The Identity API (claim validation middleware) | Same job. New claims to check: agent-issued markers, mode, delegating user. |

## Controls and lifecycle

| Known world | New world | Where the analogy breaks |
|---|---|---|
| CDR arrangement revocation; my client offboarding | Disabling an agent identity | Disable acts at the identity plane and bites within one token lifetime; CDR revocation was consent-plane. |
| FAPI hardening (PAR, PKCE, JARM, mTLS-bound tokens) | Conditional Access on the blueprint; token protection features | Different layer: FAPI hardens the protocol messages; CA is policy at sign-in time. Not interchangeable. |
| Client allow-listing at the gateway | MCP tool allow-list | Same instinct, different object: CDR allow-listed *who may call*; MCP allow-lists *what may be called*. |
| Audit under CDR rules | Entra sign-in logs + audit logs + my app audit trail | Entra splits sign-in events from directory changes; correlation across the two plus app logs is on me (NFR4). |

## MCP corner

| Known world | New world | Where the analogy breaks |
|---|---|---|
| API gateway in front of resource APIs | MCP server in front of tools | The MCP server also *describes* tools to the model - a capability surface, not just a proxy. That is why allow-listing lives here. |
| An API endpoint + its scope | An MCP tool + its documented permission (NFR1) | Tools are model-callable; the caller is probabilistic. Hence the approval gate - no OAuth analogue. |

---

*Maintained as the build progresses. Rows without a verified "breaks" note are marked and revisited in the phase gap-close hour.*
