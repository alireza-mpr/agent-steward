# GLOSSARY - ubiquitous language

A living translation table between the identity world I come from (OAuth2, OIDC, FAPI, Australia's Consumer Data Right) and the world this project lives in (Microsoft Entra, Agent ID, MCP).

Two rules keep this table honest:

1. Every mapping is a **learning scaffold**, not an exact equivalence. The last column says where the analogy breaks.
2. A term is added the first time it appears in a build phase, not before. If a row has no "breaks" note yet, it is unverified.

## Core identity objects

| Known world (OAuth2/OIDC/CDR) | New world (Entra / Agent ID) | Where the analogy breaks |
|---|---|---|
| Authorization server / IdP | Microsoft Entra ID (the tenant's token issuer) | Entra is also the directory and policy engine, not only the token issuer. |
| One IdentityServer deployment: one issuer, its configuration store and its user store | Tenant: one organization's instance of Entra ID (directory and token issuer), with a permanent `<name>.onmicrosoft.com` domain and a tenant ID in every token (`tid`, `iss`) | Microsoft runs the server; the organization only configures it. Licenses belong to the tenant and decide which features it has. Microsoft 365 and Azure also use the tenant as their identity base. |
| OAuth client (the definition of an app) | App registration | Close. One registration can serve many tenants. |
| Client instance operating in an ecosystem | Service principal. The portal calls it an "enterprise application" (Entra ID > Enterprise apps). | CDR had the same split: one software product, with a separate DCR registration at each data holder. IdentityServer has no split: one `Client` entry is both the definition and the thing that gets access. In Entra, the service principal is a real directory account: it can hold roles, be blocked by Conditional Access and appear in sign-in logs. |
| One CDR software product, registered at many data holders | Multi-tenant application. Microsoft registered each of its apps once, in its own tenant. A tenant only holds the service principals: the local accounts that let those apps work there. A new tenant here had 0 applications and 187 service principals. Microsoft adds more just in time, the first time one of its apps is used in the tenant (audit log initiator: "Microsoft Azure AD Internal - Jit Provisioning"); here, the first Bicep deploy created the "Microsoft Graph Bicep Extension" service principal. In Graph, `servicePrincipalType` "Application" means "a service principal created from an application", even when that application lives in another tenant; `appOwnerOrganizationId` names that tenant. | In CDR, each data holder issued its own `client_id`. In Entra, the `appId` is the same in every tenant. |
| Non-human identity (my 450 B2B client identities) | Workload identity (umbrella: service principal, managed identity, agent identity) | Entra's umbrella is wider and has first-class sub-types with different lifecycles. |
| - (no equivalent) | Managed identity | No CDR analogue: the platform owns the credential, the app never sees one. |
| IdentityServer admin: whoever can change the configuration store, the user store or the code | Global Administrator (the highest built-in Entra admin role) | Plain IdentityServer has no admin roles: whoever can change the config store or the code has full power. Entra has many smaller built-in roles (for example Application Administrator, Conditional Access Administrator). Microsoft recommends Global Administrator only for setup and emergencies; this project uses it for tenant setup only. |
| ASP.NET Identity role: a named set of users, used for authorization | Security group: a directory object that holds members (users, devices, service principals) and is used to grant access in one place - app access, Conditional Access targeting, role assignments, licenses, agent sponsors. In Graph: `securityEnabled: true`, `mailEnabled: false`, `groupTypes: []`. The other main kind, the Microsoft 365 group (`groupTypes: ["Unified"]`), is for collaboration (mailbox, Teams), not access control. Membership is either assigned (members added by hand or by code; any member type) or dynamic (a rule on attributes, for example `user.department -eq "Finance"`; users or devices only, never service principals; needs Entra ID P1). A group cannot be both. | An Entra group is its own object with an ID, and its members can be non-humans. ASP.NET Identity has no rule-based membership; attribute rules live in app code (policy-based authorization), while Entra evaluates dynamic rules in the directory itself. Group membership is not in tokens by default. When the `groups` claim is turned on, it carries object IDs, not names, and more than 200 groups in a JWT trigger an overage claim instead of the list. |
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
| Audit under CDR rules | Entra sign-in logs + audit logs + my app audit trail; for Bicep deploys, also the Azure deployment history | Entra splits sign-in events from directory changes; correlation across the two plus app logs is on me (NFR4). Each audit entry says whether a user or an app initiated the change (`initiatedBy.user` vs `initiatedBy.app`). Seen here: the Bicep-created group was logged as initiated by the signed-in admin, because ARM called Graph on the admin's behalf; groups that Microsoft services created by themselves show only the app. |

## Tooling

| Known world | New world | Where the analogy breaks |
|---|---|---|
| An IdP's admin API (with IdentityServer: direct access to the configuration and user stores) | Microsoft Graph: one REST API (`https://graph.microsoft.com`) to read and change everything in a tenant - users, groups, applications, service principals, agents - plus Microsoft 365 data | Entra has no store the organization can touch: every change goes through the portal or through Graph. Graph covers all of Microsoft 365, not only identity. Graph is itself protected by Entra: each call needs a Graph token with delegated or application permissions. Graph PowerShell and the Graph Bicep extension are both built on it. |
| IdentityServer clients and API resources defined in code (`Config.cs`) or seeded into the configuration store | Bicep with the Microsoft Graph extension: Entra objects (applications, service principals, groups) declared in `.bicep` files and deployed to the tenant. Why: tenant setup as code, so the tenant can be rebuilt from the repo (NFR5). **Bicep files are deployed by Azure Resource Manager (ARM), Microsoft's deployment engine.** ARM normally deploys into an Azure subscription; a file with only Graph resources can also deploy at tenant scope, with no subscription. This is the only reason the project touches Azure: nothing in it runs in Azure. | Bicep is declarative: a file describes the end state, and each deployment makes the tenant match it, so running it again is safe. IdentityServer config is code that runs inside a server the team owns. Bicep covers only some Entra object types; types it lacks (possibly the new Agent ID types) need Graph PowerShell. Unverified for Agent ID types until Phase 1. |

## MCP corner

| Known world | New world | Where the analogy breaks |
|---|---|---|
| API gateway in front of resource APIs | MCP server in front of tools | The MCP server also *describes* tools to the model - a capability surface, not just a proxy. That is why allow-listing lives here. |
| An API endpoint + its scope | An MCP tool + its documented permission (NFR1) | Tools are model-callable; the caller is probabilistic. Hence the approval gate - no OAuth analogue. |

---

*Maintained as the build progresses. Rows without a verified "breaks" note are marked and revisited in the phase gap-close hour.*
