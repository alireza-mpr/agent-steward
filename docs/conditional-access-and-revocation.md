# Conditional Access and revocation

Access can be stopped at two moments: when a token is being issued, and when a token already exists. Entra controls the first moment for every API that accepts Entra tokens, with Conditional Access. For the second, Entra helps only Microsoft's own resources, through Continuous Access Evaluation (CAE), so your own API has to do it itself.

Tags used here: **verified** (with a link to the source), **my design**, **not checked**. Sources checked on 11 November 2026 unless a date is given.

## Contents

- [1. The high-level view](#1-the-high-level-view)
- [2. When a token is being issued: Conditional Access](#2-when-a-token-is-being-issued-conditional-access)
- [3. When a token already exists](#3-when-a-token-already-exists)
  - [3.1 Continuous Access Evaluation](#31-continuous-access-evaluation)
  - [3.2 Short token lifetime](#32-short-token-lifetime)
  - [3.3 Live status check at the API](#33-live-status-check-at-the-api)
  - [3.4 Deny list fed by the disable event](#34-deny-list-fed-by-the-disable-event)
  - [3.5 How to choose](#35-how-to-choose)
- [4. agent-steward](#4-agent-steward)
- [Sources](#sources)

## 1. The high-level view

**The two moments:**

1. **When a token is being issued:** The issuer decides whether to issue a token at all. So it can refuse a request it does not like. Conditional Access works here.
2. **When a token already exists:** The issuer is no longer involved. The resource validates the token on its own and accepts it until it expires. So only the resource can refuse it.

**The general rule:** A JWT access token is self-contained. The resource validates it locally: the signature, the issuer, the audience, and that "the current time MUST be before the time represented by the `exp` claim" ([RFC 9068, section 4](https://www.rfc-editor.org/rfc/rfc9068.html#section-4)). Because of this, the resource needs no call to the issuer, and the issuer cannot recall a token it has issued. [RFC 7009, section 3](https://www.rfc-editor.org/rfc/rfc7009.html#section-3) names two ways out: short-lived access tokens, or "some (currently non-standardized) backend interaction between the authorization server and the resource server" when immediate revocation is needed.

**The parts:**

- **Moment 1:** Conditional Access ([section 2](#2-when-a-token-is-being-issued-conditional-access)).
- **Moment 2:** CAE, a short token lifetime, a live status check, and a deny list ([section 3](#3-when-a-token-already-exists)).

## 2. When a token is being issued: Conditional Access

**What it is:** A policy that Entra checks before it issues or refreshes a token. If a policy blocks, no token is issued.

**The problem it solves:** A valid credential alone is not enough. Conditional Access also checks the context: who asks, for which resource, from which IP range, and at what risk level. So a stolen blueprint secret used from an unknown network gets no token.

**How it works:**

1. **Token request:** A user or an agent asks Entra for a token for one resource (the audience).
2. **Policy match:** Entra finds the policies that match the token's subject and the resource.
3. **Evaluation:** Entra checks each matching policy's conditions and controls.
4. **Decision:** If a policy blocks, Entra issues no token. Otherwise it issues the token.
5. **Use:** The client calls the resource. The resource validates the token only. It never sees the policy.

Steps 2 to 4 run again on every refresh. Between refreshes, nothing is evaluated: Entra "evaluates policies when Microsoft Entra ID issues or refreshes an access token" (verified, [Conditional Access for agents](https://learn.microsoft.com/en-us/entra/identity/conditional-access/agent-id)).

**For agents:** The policy targets the token's subject, so the target depends on the access pattern (verified, [Conditional Access for agents](https://learn.microsoft.com/en-us/entra/identity/conditional-access/agent-id)):

- **App-only:** The agent identity is the subject. A policy can target the agent identity, its blueprint, or agents with a custom security attribute. A policy on the blueprint covers all its agent identities, including ones created later.
- **On-behalf-of (OBO):** The user is the subject. So the user's policies apply, and a policy that targets the agent identity does not.
- **Agent's user account:** The agent's user account is the subject. It needs a policy that targets agent users.
- **Controls:** For workload identities, Block access is the only grant control (verified, [Conditional Access for workload identities](https://learn.microsoft.com/en-us/entra/identity/conditional-access/workload-identity)). The Learn training module says the same for agent identities, but the Conditional Access for agents page does not confirm it (not checked).

**Your own APIs:**

- ✅ **Covered:** The check runs at Entra when the token is issued, so it protects any resource Entra issues tokens for. Learn names an "MCP server, or Open API service" as examples. The API must be registered in Entra.
- ❌ **Not covered:** An API that accepts an API key instead of an Entra token. Entra never sees that call.

**Deviations and limits** (verified, [Conditional Access for agents](https://learn.microsoft.com/en-us/entra/identity/conditional-access/agent-id), unless marked):

- **Security defaults:** While they are on, Conditional Access does not apply.
- **A blueprint creating agents:** A blueprint's token for Microsoft Graph, to create an agent identity or an agent's user account, is not covered.
- **The token exchange endpoint:** The intermediate exchange at `AAD Token Exchange Endpoint: Public` is not covered.
- **Groups:** A policy assigned to a group does not apply to the service principals in it (verified, [Conditional Access for workload identities](https://learn.microsoft.com/en-us/entra/identity/conditional-access/workload-identity)).
- **Licensing:** For agents, Microsoft 365 E7, or Agent 365 with at least Entra ID P1 or Microsoft 365 E3. For other workload identities, Workload Identities Premium.

## 3. When a token already exists

Unlike moment 1, Entra plays no part here by default: the resource accepts the token until `exp`. Entra's default access token lifetime is a random value between 60 and 90 minutes (verified, [Configurable token lifetimes](https://learn.microsoft.com/en-us/entra/identity-platform/configurable-token-lifetimes)). So after a disable, an existing token can keep working for up to 90 minutes. The four options below shrink that gap.

### 3.1 Continuous Access Evaluation

**What it is:** A Microsoft feature that lets a resource reject a token before it expires, soon after a critical event in Entra. It is "based on Open ID Continuous Access Evaluation Profile (CAEP)" (verified, [CAE](https://learn.microsoft.com/en-us/entra/identity/conditional-access/concept-continuous-access-evaluation)).

**How it works:**

1. **Opt-in:** The client tells Entra that it can handle CAE, with the `cp1` client capability.
2. **Token:** Entra issues a long-lived token. Because it can now be revoked, it lives up to 28 hours for users, and up to 24 hours for workload identities.
3. **Critical event:** For example, an admin disables the user or the service principal. Entra sends the event to the resource.
4. **Rejection:** The client calls the resource. The resource sees the event and answers 401 with a claims challenge (`error="insufficient_claims"`).
5. **Re-evaluation:** The client asks Entra for a new token with the challenge. Entra evaluates again and refuses.

Microsoft's goal is near real time, but up to 15 minutes of delay is possible.

**What it covers for users** (verified, [CAE](https://learn.microsoft.com/en-us/entra/identity/conditional-access/concept-continuous-access-evaluation)):

- **Critical events:** user deleted or disabled, password changed or reset, MFA enabled for the user, all refresh tokens revoked by an admin, and high user risk.
- **Resources:** Exchange Online, SharePoint Online and Teams for critical events. Microsoft Graph joins them for Conditional Access policy sync (IP location).

**What it covers for workload identities** (verified, [CAE for workload identities](https://learn.microsoft.com/en-us/entra/identity/conditional-access/concept-continuous-access-evaluation-workload)):

- **Revocation events:** service principal disabled, service principal deleted, and high service principal risk.
- **Resources:** Microsoft Graph only.
- **Identities:** single-tenant service principals registered in the tenant. No managed identities, no multitenant apps.

**Cases:**

- ✅ **A disabled user, on Exchange, SharePoint or Teams:** loses access within minutes.
- ✅ **A disabled single-tenant service principal, on Microsoft Graph:** its long-lived token is rejected.
- ❌ **A disabled agent identity:** An agent identity is a kind of service principal, but the workload identity page does not name agent identities (not checked).
- ❌ **Your own API:** CAE events go to "CAE-enabled Microsoft resources" ([Secure applications with CAE](https://learn.microsoft.com/security/zero-trust/develop/secure-with-cae), verified 10 November 2026). Microsoft documents no way for a custom API to receive them.

### 3.2 Short token lifetime

**What it is:** A token lifetime policy that makes access tokens for one API expire sooner.

**How it works:**

1. **Policy:** An admin creates a `tokenLifetimePolicy` with `AccessTokenLifetime`, for example `00:10:00`.
2. **Assignment:** The admin assigns it to the API's application. The policy on "the application that is being accessed" takes effect.
3. **Effect:** New tokens for that API expire after 10 minutes. So after a disable, the gap is at most 10 minutes.

**The policy's data** (verified, [Configurable token lifetimes](https://learn.microsoft.com/en-us/entra/identity-platform/configurable-token-lifetimes)):

- **Range:** minimum 10 minutes, maximum one day.
- **Priority:** An organization-level policy takes precedence over an application-level one.
- **Tooling:** Microsoft Graph or Graph PowerShell only. There is no portal UI.
- **Not supported:** managed identities.

**Cases:**

- ✅ **No code change:** setup only.
- ❌ **The gap remains:** up to 10 minutes.
- ❌ **More token requests:** every client gets a new token more often.
- ❌ **Agent tokens:** whether Entra applies the policy to agent identity tokens (not checked).

### 3.3 Live status check at the API

**What it is:** The API asks whether the caller is still enabled, instead of trusting the token alone.

**How it works:**

1. **Validate:** The API validates the token as usual.
2. **Look up:** It reads the caller's status. In Entra, that is `accountEnabled` on the agent identity in Microsoft Graph, found by the token's `oid`.
3. **Cache:** It keeps the answer for a short time, for example 60 seconds.
4. **Decide:** A disabled caller gets 401 `invalid_token`.

**The standard form:** OAuth token introspection ([RFC 7662](https://www.rfc-editor.org/rfc/rfc7662)). The resource sends the token to the authorization server's introspection endpoint, and the answer says whether the token is `active`.

**Cases:**

- ✅ **Every disable is seen:** whoever made it, a tool or a person in the portal.
- ✅ **Gap = cache time.**
- ❌ **The identity provider is on the hot path:** more latency, and Graph throttling.
- ❌ **An outage decision:** When Graph is down, the API must fail closed (refuse) or fail open (accept). For an API that writes, fail closed.
- ❌ **An extra permission:** The API needs Graph read access to agent identities, for example `AgentIdentity.Read.All`.

**Deviation, Entra:** Entra has no introspection endpoint: its discovery document lists no `introspection_endpoint` ([OpenID configuration](https://login.microsoftonline.com/common/v2.0/.well-known/openid-configuration), checked 10 November 2026). So in Entra, the API builds the lookup itself, with a Graph read.

### 3.4 Deny list fed by the disable event

**What it is:** The component that disables an identity tells the API right away. Then the API rejects that identity's tokens.

**How it works:**

1. **Disable:** A component disables the identity in Entra.
2. **Notify:** It sends an event to the API: identity X was disabled at time T.
3. **Store:** The API adds X to its deny list. It keeps the entry at least as long as the longest token lifetime, because Entra issues X no new token after T.
4. **Reject:** Every call with a token for X gets 401.

**The standard form:** The OpenID Shared Signals Framework (SSF), in which a transmitter pushes security events to receivers. Its RISC profile defines `account-disabled`: "the account identified by the subject has been disabled" (verified, [RISC 1.0](https://openid.net/specs/openid-risc-1_0-final.html)). Its CAEP profile defines `session-revoked`.

**Cases:**

- ✅ **A gap of seconds.**
- ✅ **No identity provider call per request.**
- ❌ **Only known disables:** A disable made elsewhere, for example by an admin in the portal, never reaches the list. So it is usually paired with the live status check ([3.3](#33-live-status-check-at-the-api)) as a backstop.
- ❌ **State to keep:** The list must survive a restart. Otherwise the API forgets a disable and accepts the old token again.

**Deviation, Entra:** Whether Entra can act as an SSF transmitter to a custom API (not checked). Microsoft Graph change notifications are another possible feed (not checked for agent identities).

### 3.5 How to choose

- **A low-risk, read-only API:** the short token lifetime ([3.2](#32-short-token-lifetime)) is enough.
- **An API that writes, or holds sensitive data:** the live status check ([3.3](#33-live-status-check-at-the-api)), with a short cache.
- **The disable runs through your own code:** the deny list ([3.4](#34-deny-list-fed-by-the-disable-event)) for speed, plus the live status check as the backstop.
- **Microsoft's resources:** CAE ([3.1](#31-continuous-access-evaluation)) already does it, where it covers the identity type. Nothing to build.

## 4. agent-steward

**Moment 1, Conditional Access (FR9, cut):** The tenant has the license (an Agent 365 trial with Entra ID P2). But security defaults are still on, because the approver's sign-in relies on them ([ADR-002](decisions.md#adr-002-the-approver-signs-in-with-the-authorization-code-flow-not-the-device-code-flow)). While they are on, no Conditional Access policy applies. See [entra.md](entra.md).

**Moment 2, revocation (FR11, documented-only):** The executor disables an agent with the approver's own rights (FR4, FR7), so Entra stops issuing that agent new tokens. The Identity API validates tokens locally ([tokens.md](tokens.md)) and does no status check. So an existing token keeps working until it expires. A Graph token observed in this project lived 65 minutes.

**The delta, if FR11 were built (my design):**

- **The deny list ([3.4](#34-deny-list-fed-by-the-disable-event)):** The executor already knows when it disabled an agent. It would tell the Identity API, which would reject that agent's tokens.
- **The live status check as the backstop ([3.3](#33-live-status-check-at-the-api)):** For disables made in the portal, the Identity API would read `accountEnabled` on its write routes, with a short cache, and fail closed.
- **CAE is not an option:** The Identity API is a custom API, and CAE events reach only Microsoft's resources.

The options as a design are in [DESIGN-NOTES section 7](DESIGN-NOTES.md#7-revoking-live-tokens). Why revocation matters more for agents is in [From microservices to agents, section 9](from-microservices-to-agents.md#9-revocation-has-to-reach-the-running-agent).

## Sources

- [RFC 9068: JWT Profile for OAuth 2.0 Access Tokens](https://www.rfc-editor.org/rfc/rfc9068.html), section 4
- [RFC 7009: OAuth 2.0 Token Revocation](https://www.rfc-editor.org/rfc/rfc7009.html), section 3
- [RFC 7662: OAuth 2.0 Token Introspection](https://www.rfc-editor.org/rfc/rfc7662)
- [OpenID RISC Profile 1.0](https://openid.net/specs/openid-risc-1_0-final.html)
- [Conditional Access for agents](https://learn.microsoft.com/en-us/entra/identity/conditional-access/agent-id)
- [Conditional Access for workload identities](https://learn.microsoft.com/en-us/entra/identity/conditional-access/workload-identity)
- [Continuous access evaluation](https://learn.microsoft.com/en-us/entra/identity/conditional-access/concept-continuous-access-evaluation)
- [Continuous access evaluation for workload identities](https://learn.microsoft.com/en-us/entra/identity/conditional-access/concept-continuous-access-evaluation-workload)
- [Secure applications with CAE](https://learn.microsoft.com/security/zero-trust/develop/secure-with-cae) (checked 10 November 2026)
- [Configurable token lifetimes](https://learn.microsoft.com/en-us/entra/identity-platform/configurable-token-lifetimes)
- [Entra's OpenID configuration](https://login.microsoftonline.com/common/v2.0/.well-known/openid-configuration) (checked 10 November 2026)
