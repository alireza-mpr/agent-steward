# JWT Bearer and Token Exchange

Three standard values decide what a JWT or a token does in a request to an OAuth token endpoint. Two come from RFC 7523: one makes the JWT the *grant*, the other makes it the *client's proof of identity*. The third comes from RFC 8693: it trades a token the client already has for a new one. They look alike, but they do different jobs. This document explains all three, shows what each request sends and gets back, and ends with Microsoft's On-Behalf-Of (OBO) flow, which acts like token exchange but is not RFC 8693.

## Contents

- [1. The Three Values](#1-the-three-values)
- [2. What Is Sent and What Comes Back](#2-what-is-sent-and-what-comes-back)
- [3. OBO Is Not RFC 8693](#3-obo-is-not-rfc-8693)
  - [What Is the Same, What Is Different](#what-is-the-same-what-is-different)
  - [Why OBO Is Not RFC 8693: the History](#why-obo-is-not-rfc-8693-the-history)
  - [Microsoft's Wording](#microsofts-wording)
- [Sources](#sources)

---

## 1. The Three Values

- **`urn:ietf:params:oauth:grant-type:jwt-bearer` (the JWT is the grant):** It is a value of `grant_type`. The JWT itself is the reason the server issues a token: it says who the token is for, and the server trusts its issuer. *Use case:* a workload swaps a token from one issuer for an access token from another, with no secret, for example in workload identity federation. Microsoft's OBO flow also uses it ([section 3](#3-obo-is-not-rfc-8693)).
- **`urn:ietf:params:oauth:client-assertion-type:jwt-bearer` (the JWT proves who the client is):** It is a value of `client_assertion_type`. The JWT replaces a client secret. The grant is something else, for example `client_credentials`. *Use case:* a confidential client signs in with a certificate or a federated identity credential instead of a secret, in any grant.
- **`urn:ietf:params:oauth:grant-type:token-exchange` (trade a token for a new one):** It is a value of `grant_type`. The client sends a token it already has, the *subject token*, and gets a new token, usually for another audience or with less scope. It can also send an *actor token*, so the new token records who acts for whom. *Use case:* a middle-tier API gets a token to call a downstream API as the user, with that API as the audience.

---

## 2. What Is Sent and What Comes Back

| Value | What is sent | What comes back | Where these docs use it |
|---|---|---|---|
| `grant-type:jwt-bearer` | `grant_type=urn:ietf:params:oauth:grant-type:jwt-bearer`, the JWT in `assertion`, and usually a `scope`. Client authentication is optional in the RFC. | An access token for the requested scope | **Anthropic's workload identity federation:** a managed identity token goes in, and a short-lived Claude API token comes out ([step 2](authentication.md#1-how-the-harness-authenticates)). **Entra's OBO:** the user's token goes in, and a token for the downstream API that still carries the user comes out. |
| `client-assertion-type:jwt-bearer` | `client_assertion_type=urn:ietf:params:oauth:client-assertion-type:jwt-bearer`, the JWT in `client_assertion`, the `client_id`, and a separate `grant_type`, for example `client_credentials` | Whatever that grant issues. With `client_credentials`: an app-only access token. | **Entra request 1:** a managed identity token as the blueprint's credential goes in, and T1 comes out. **Entra request 2:** T1 as the agent identity's credential goes in, and TR comes out ([steps 3 and 4](authentication.md#1-how-the-harness-authenticates)). **A certificate credential:** a JWT signed with the certificate's private key goes in. |
| `grant-type:token-exchange` | `grant_type=urn:ietf:params:oauth:grant-type:token-exchange`, the token in `subject_token` with its type in `subject_token_type` (for example `urn:ietf:params:oauth:token-type:access_token`). Optional: `actor_token` and `actor_token_type`, `audience` or `resource`, `scope`, and `requested_token_type`. | The new token in `access_token`, and its type in `issued_token_type`. When the new token is a JWT and an actor took part, an `act` (actor) claim can name the actor. | **Written design only**, never code in this project ([DESIGN-NOTES.md](../DESIGN-NOTES.md#1-token-exchange-rfc-8693---narrowing-tokens)). Microsoft documents no RFC 8693 flow for this case ([note](agent-architecture.md#three-architectural-pillars)). |

**Both in one request.** The two RFC 7523 values can appear together. Entra's OBO request does this when the middle-tier app proves itself with a certificate or a federated identity credential: the user's token goes in `assertion` (the grant), and the app's own JWT goes in `client_assertion` (the client's proof). The agent OBO request works the same way, with T1 as the `client_assertion` ([Agent OAuth flows: On-behalf-of flow](https://learn.microsoft.com/entra/agent-id/agent-on-behalf-of-oauth-flow)).

---

## 3. OBO Is Not RFC 8693

**In one line:** Microsoft built its own flow, OBO, which solves the same problem as Token Exchange, on top of the JWT bearer grant. It is not an implementation of RFC 8693. So it is fair to say "Entra OBO is Microsoft's version of token exchange, built on the JWT bearer grant", but not to call it an RFC 8693 implementation.

### What Is the Same, What Is Different

**The same:**
- ✅ **Goal:** Both let a middle-tier API take a user's token and get a new token to call another API as that user.
- ✅ **Pattern:** Both trade an incoming token for a new token with a different audience.

**Different:**
- ❌ **Standard:** OBO reuses the JWT bearer grant's value with Microsoft's own rules, so it is neither plain RFC 7523 nor RFC 8693. Its assertion is a token issued to the middle tier, but RFC 7523 says a token endpoint must reject a JWT whose audience is not the token endpoint itself ([RFC 7523, section 3](https://www.rfc-editor.org/rfc/rfc7523.html#section-3); see also [Two values that look alike](authentication.md#1-how-the-harness-authenticates)).
- ❌ **Parameters:** OBO sends the incoming token in `assertion` and adds `requested_token_use=on_behalf_of` ([source](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-on-behalf-of-flow)). RFC 8693 uses `subject_token`, `subject_token_type`, `actor_token` and `requested_token_type`. So a client written for RFC 8693 does not work against Entra's OBO without changes.
- ❌ **Actor:** RFC 8693 can record "A acts for B", with `actor_token` in the request and the `act` claim in the new token. OBO has neither. Its new token names the user as the subject and the middle-tier app as the client (`azp`). For agents, Entra adds its own actor facts in `xms_act_fct` ([Agent tokens](tokens.md#what-kind-of-caller-vs-what-it-may-do)).

### Why OBO Is Not RFC 8693: the History

Microsoft did not break away from a standard: when it built OBO, no token exchange standard existed.

1. **The need came first:** Azure AD customers built multi-tier apps, where a web API had to call another API as the signed-in user.
2. **Microsoft used the closest standard:** The JWT bearer grant (RFC 7523, published in 2015) already meant "here is a JWT, give me a token". It had no way to say "this is a delegation request", so Microsoft added `requested_token_use=on_behalf_of`. That extra parameter is allowed: OAuth 2.0 says a token endpoint must ignore parameters it does not know ([RFC 6749, section 3.2](https://www.rfc-editor.org/rfc/rfc6749.html#section-3.2)). But OBO also bends one RFC 7523 rule: its assertion's audience is the middle tier, not the token endpoint (see **Standard** above).
3. **The standard came later:** RFC 8693 was published in January 2020. By then, OBO had been in production for years.
4. **Microsoft kept OBO:** Microsoft has published no reason. The likely one is backward compatibility: many apps and Microsoft's own libraries depend on OBO, and a second flow for the same job would add little.

### Microsoft's Wording

Microsoft's own wording adds to the confusion. It calls request 2 of the agent flow a "token exchange request", and T1's audience is `api://AzureADTokenExchange`. Neither is RFC 8693: request 2 uses `client_credentials`, or the JWT bearer grant for OBO, with T1 as a client assertion ([Clarifying Misconceptions](agent-id.md#clarifying-misconceptions)). So when a source lists "token exchange" as a JWT bearer use case, it means vendor flows like OBO, not the RFC 8693 standard.

---

## Sources

Checked 29 October 2026:

- [RFC 6749: The OAuth 2.0 Authorization Framework](https://www.rfc-editor.org/rfc/rfc6749.html), section 3.2 (unknown parameters are ignored)
- [RFC 7521: Assertion Framework for OAuth 2.0](https://www.rfc-editor.org/rfc/rfc7521.html), section 4.1 (client authentication is optional for an assertion grant)
- [RFC 7523: JWT Profile for OAuth 2.0 Client Authentication and Authorization Grants](https://www.rfc-editor.org/rfc/rfc7523.html), section 2.1 (the grant), section 2.2 (client authentication) and section 3 (the audience rule)
- [RFC 8693: OAuth 2.0 Token Exchange](https://www.rfc-editor.org/rfc/rfc8693.html), section 2.1 (the request), section 2.2.1 (the response) and section 4.1 (the `act` claim)
- [Microsoft identity platform and OAuth 2.0 On-Behalf-Of flow](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-on-behalf-of-flow)
- [Agent OAuth flows: On-behalf-of flow](https://learn.microsoft.com/entra/agent-id/agent-on-behalf-of-oauth-flow)
- [Agent autonomous app OAuth flow](https://learn.microsoft.com/entra/agent-id/agent-autonomous-app-oauth-flow)
