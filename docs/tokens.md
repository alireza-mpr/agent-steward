# Agent tokens: claims and validation

This doc explains why an API must be registered before any token exists for it, what is inside an agent's resource access token (TR, Microsoft's name for it), and what an API must check before it accepts one. For how the agent gets the token, see [How an agent signs in](agent-id.md#3-getting-a-token-the-agent-autonomous-app-flow). Every claim below was seen in a real token from this lab, and each one says whether Microsoft documents it.

## Contents

- [Why the API must be registered first](#why-the-api-must-be-registered-first)
- [Who the token is for (`aud`) and who it is about (`sub`)](#who-the-token-is-for-aud-and-who-it-is-about-sub)
  - [The standard: `aud`](#the-standard-aud)
  - [The standard: `sub`](#the-standard-sub)
  - [`sub` in the refresh token grant](#sub-in-the-refresh-token-grant)
  - [Where Entra differs](#where-entra-differs)
  - [Deviation: one refresh token, many APIs](#deviation-one-refresh-token-many-apis)
  - [`aud` and `sub` in the guardian](#aud-and-sub-in-the-guardian)
- [See a real token](#see-a-real-token)
- [TR, claim by claim](#tr-claim-by-claim)
  - [Who issued it, for which API, and for how long](#who-issued-it-for-which-api-and-for-how-long)
  - [Who the client is](#who-the-client-is)
  - [The Agent ID claims](#the-agent-id-claims)
  - [What the token allows](#what-the-token-allows)
  - [Internal claims](#internal-claims)
- [T1 and TR side by side](#t1-and-tr-side-by-side)
  - [What changes from T1 to TR](#what-changes-from-t1-to-tr)
  - [The claims, masked](#the-claims-masked)
  - [Where Entra checks the parent blueprint](#where-entra-checks-the-parent-blueprint)
  - [How a token names the API](#how-a-token-names-the-api)
  - [What TR does not show: the blueprint's credential](#what-tr-does-not-show-the-blueprints-credential)
- [The approver's token: a delegated token](#the-approvers-token-a-delegated-token)
  - [The claims, with placeholders](#the-claims-with-placeholders)
  - [What differs from TR](#what-differs-from-tr)
- [The OBO token: the agent acting for the approver](#the-obo-token-the-agent-acting-for-the-approver)
  - [The OBO claims, masked](#the-obo-claims-masked)
  - [Who is who in the token](#who-is-who-in-the-token)
  - [From Tc to the OBO token](#from-tc-to-the-obo-token)
  - [Compared with RFC 8693 delegation](#compared-with-rfc-8693-delegation)
  - [Other claims](#other-claims)
- [Scopes and app roles](#scopes-and-app-roles)
  - [The two side by side](#the-two-side-by-side)
  - [The standard](#the-standard)
  - [Where vendors differ](#where-vendors-differ)
  - [In the guardian](#in-the-guardian)
- [What an API must check](#what-an-api-must-check)
  - [What kind of caller vs what it may do](#what-kind-of-caller-vs-what-it-may-do)
  - [A real negative case: the blueprint's own token](#a-real-negative-case-the-blueprints-own-token)
- [Sources](#sources)

## Why the API must be registered first

**In one line:** an authorization server can only issue an access token for an API it already knows, so every API is registered before any token is issued for it.

In OAuth, the authorization server, such as IdentityServer or Microsoft Entra ID, is the central registry of APIs. In IdentityServer, an API's registration is an `ApiResource` or `ApiScope` config entry. In Entra, it is the API's app registration and service principal, like this project's [identity-api.bicep](../setup/bicep/identity-api.bicep). The registration describes a separate API that accepts the server's tokens. It is not an endpoint of the server itself.

The server needs the registration for four reasons:

1. **Scope mapping:** A client asks for a scope, not for an API. So the server must map the requested scope to the API that owns it. In IdentityServer, each `ApiResource` lists its `ApiScope`s, so a request for a scope leads to the API resources that include it. In Entra, the scope names its API: the guardian asks for `<IDENTITY_API_APP_ID>/.default`, which leads to our API's registration, and `.default` means "every app role granted to me on this API".
2. **Audience:** From that mapping, the server writes the API's identifier into `aud`. The API accepts only tokens with its own `aud`, so a token for one API cannot be replayed at another. Without a registration, there is no identifier to write.
3. **Permissions:** The API defines what can be granted on it: scopes (delegated) or app roles (application). The server can only grant, and write into a token, permissions that exist. Here, that is the app role `Findings.Submit`.
4. **Token shape:** The registration tells the server how to build tokens for this API. In Entra, that includes the token version (`requestedAccessTokenVersion`) and the optional claims. In this lab, the agent claims appeared only after the API asked for them. In IdentityServer, the `ApiResource` lists the user claims to include and the allowed signing algorithms.

Token lifetime is not one of these reasons in IdentityServer. There it is a client setting (`AccessTokenLifetime`), not an API setting.

## Who the token is for (`aud`) and who it is about (`sub`)

**In one line:** an access token's `aud` is the API that receives it, never the client, and its `sub` is the user when a user takes part in the grant, or the client when no user does. The API is never the `sub`.

### The standard: `aud`

- **The API, not the client:** `aud` names the resource server that will receive and check the token ([RFC 9068, section 2.2](https://www.rfc-editor.org/rfc/rfc9068#section-2.2)).
- **The client chooses it:** the client names the API in its token request. The standard parameter is `resource` ([RFC 8707](https://www.rfc-editor.org/rfc/rfc8707)), and the server can also derive the API from the requested scope ([RFC 9068, section 3](https://www.rfc-editor.org/rfc/rfc9068#section-3)).
- **Only the API reads it:** the client sends the token as it is. With a bearer token, whoever holds it can use it, with no extra proof ([RFC 6750, section 1.2](https://www.rfc-editor.org/rfc/rfc6750#section-1.2)). So the client keeps it in memory, sends it only over TLS, and never logs it.

### The standard: `sub`

RFC 9068 ([section 2.2](https://www.rfc-editor.org/rfc/rfc9068#section-2.2)) says: when a resource owner is involved, "such as the authorization code grant", `sub` is the resource owner. When no resource owner is involved, "such as the client credentials grant", `sub` identifies the client.

- **The user is the `sub`:** in the authorization code grant (with PKCE), the device code grant ([RFC 8628](https://www.rfc-editor.org/rfc/rfc8628)), the refresh token grant, token exchange and OBO, and the JWT bearer grant with an assertion that names the user ([RFC 7523](https://www.rfc-editor.org/rfc/rfc7523)). The password (ROPC) and implicit grants also give a user `sub`, but they are deprecated.
- **The client is the `sub`:** in the client credentials grant, because no user is involved.
- **The API is never the `sub`:** the API is always in `aud`.
- **Token exchange keeps the user:** the `sub` stays the user from the incoming token. The middle tier is the actor, in the `act` claim, not the subject ([RFC 8693, section 4.1](https://www.rfc-editor.org/rfc/rfc8693#section-4.1)).

### `sub` in the refresh token grant

1. **The user signs in once:** with the authorization code grant, the client gets an access token and a refresh token, both tied to that user and that client.
2. **The access token expires:** after 60 to 90 minutes in Entra.
3. **The client sends the refresh token:** `grant_type=refresh_token`. The user does not need to be there.
4. **The server issues a new access token:** for the same user, with the same or narrower scopes ([RFC 6749, section 6](https://www.rfc-editor.org/rfc/rfc6749#section-6)).

So a refresh cannot change the subject. And the client is never the `sub` here: the client credentials grant normally gives no refresh token, because "a refresh token SHOULD NOT be included" ([RFC 6749, section 4.4.3](https://www.rfc-editor.org/rfc/rfc6749#section-4.4.3)). The client can always ask again with its own credential.

### Where Entra differs

- **`aud` comes from the scope:** the v2 endpoint has no `resource` parameter. It reads the API from the scope prefix, the part before the last `/`. So `scope=<IDENTITY_API_APP_ID>/.default` gives `aud` = the Identity API.
- **The client must not parse the token:** "Although client applications can receive and use access tokens, they should treat them as opaque strings. The client application shouldn't attempt to validate access tokens." Tokens for Microsoft APIs, such as Graph, may not even be a JWT the client can decode ([Access tokens](https://learn.microsoft.com/en-us/entra/identity-platform/access-tokens)).
- **`sub` is pairwise:** each app sees a different `sub` for the same user, and `sub` is read within its tenant. Use `oid` for a stable user ID across the tenant.
- **App-only tokens:** `sub` equals `oid`, the service principal's object ID (see [Who the client is](#who-the-client-is)).
- **The agent's own user account:** the agent is the subject. `sub` names that agent user, and `xms_sub_fct` contains `13`.
- **TR in OBO with an agent:** `sub` and `oid` stay the user, `azp` names the agent identity, and `xms_act_fct` contains `11` ([the OBO flow](agent-architecture.md#the-delegated-flow-obo-step-by-step)).
- **The refresh token is bound to the user and the client, not to one API:** so one refresh token can get access tokens with a different `aud`, for any API the client has permission for. This breaks the standard, and the guardian depends on it: see [Deviation: one refresh token, many APIs](#deviation-one-refresh-token-many-apis). Its `sub` is still the user. Entra encrypts it, and "only the Microsoft identity platform can read" it. It lives 90 days, or 24 hours for single-page apps ([Refresh tokens](https://learn.microsoft.com/en-us/entra/identity-platform/refresh-tokens)).
- **A refresh is a non-interactive sign-in:** the user is not there, so the sign-in log records it under **User sign-ins (non-interactive)** ([Investigating agent activity](investigating-agent-activity.md)).
- **A refresh can be refused:** for example after an admin revokes all refresh tokens for the user. The client must then sign the user in again ([Refresh tokens](https://learn.microsoft.com/en-us/entra/identity-platform/refresh-tokens)).

### Deviation: one refresh token, many APIs

> **⚠️ Entra breaks the OAuth standard here, on purpose, and the guardian relies on it.** A standard refresh token can only renew or narrow the access it was issued with. An Entra refresh token can get an access token for a *different API*.

**The standard.** When a client refreshes, "the requested scope MUST NOT include any scope not originally granted by the resource owner" ([RFC 6749, section 6](https://www.rfc-editor.org/rfc/rfc6749#section-6), checked 10 November 2026). So a refresh can renew the first token, or narrow it. It cannot get a token for another API.

**Entra.** "Refresh tokens are also used to acquire extra access tokens for other resources. Refresh tokens are bound to a combination of user and client, but aren't tied to a resource or tenant. A client can use a refresh token to acquire access tokens across any combination of resource and tenant where it has permission to do so." And: "Refresh tokens replace themselves with a fresh token upon every use." ([Refresh tokens](https://learn.microsoft.com/en-us/entra/identity-platform/refresh-tokens), Microsoft Learn, checked 10 November 2026.)

**Where the guardian uses it: the approve CLI gets Tc this way.**

1. **The sign-in (authorization code grant with PKCE):** the CLI asks for `<IDENTITY_API_APP_ID>/.default openid profile offline_access`. Entra returns three tokens: an access token for the Identity API, an ID token, and a refresh token (from `offline_access`).
2. **The list and the decision:** the CLI calls the Identity API with that access token.
3. **After "approve" (the refresh token grant):** the CLI sends the refresh token with a scope for a different API, `<GUARDIAN_BLUEPRINT_APP_ID>/access_as_user`. Entra returns Tc, an access token whose `aud` is the guardian's blueprint, plus a new refresh token. The person sees no second prompt.
4. **Tc goes to the executor,** which exchanges it with OBO for a Graph token.

```text
CLI ──(authorization code + PKCE)──────────────────────────→ Entra
CLI ←── access token (aud: Identity API) + ID token + refresh token
CLI ──(access token for the Identity API)──────────────────→ Identity API   (list, decide)
CLI ──(refresh_token grant, scope: <blueprint>/access_as_user)→ Entra        ⚠️ a different API
CLI ←── Tc (aud: the blueprint, scp: access_as_user) + a new refresh token
CLI ──(Tc)─────────────────────────────────────────────────→ executor       (then OBO, for Graph)
```

**The proof:**

- **MSAL's code:** in MSAL for Python 1.39.0, the version in the approve CLI's image, `acquire_token_silent` finds a cached refresh token that belongs to this client and calls `obtain_token_by_refresh_token` with the new scope (read from its source on 10 November 2026). The CLI calls it in `Session.token_for` ([approve.py](../src/approve_cli/approve.py)).
- **Live:** on 10 November 2026, one sign-in gave Tc with `aud` = the guardian's blueprint and `scp: access_as_user`, with no second prompt.

**Why Entra allows it here:** "where it has permission to do so". The approve CLI holds an admin consent for `access_as_user` on the guardian's blueprint ([approver-delegation.bicep](../setup/bicep/approver-delegation.bicep)). Without that consent, the refresh would be refused.

**The security consequence:** a stolen refresh token is worth more than in the standard. It can get access tokens for *every* API the client has consent for, for up to 90 days. So the CLI keeps all its tokens in MSAL's in-memory cache only, never writes one to a file or a log, and loses them when the command exits.

### `aud` and `sub` in the guardian

- **The approver's token (the CLI):** `aud` is the Identity API, and `sub` is the approver ([the claims](#the-approvers-token-a-delegated-token)).
- **The guardian's autonomous token (TR):** `aud` is the Identity API or Graph. It is app-only, so `sub` is the agent identity, not a user ([the claims](#tr-claim-by-claim)).
- **The approver's second token (Tc):** `aud` is the guardian's blueprint, and the person is the user (`oid`). The CLI gets it after an approval with the refresh token grant ([the deviation](#deviation-one-refresh-token-many-apis)) and sends it to the executor (seen live on 10 November 2026: `scp` was `access_as_user`, `azp` the CLI, version 2.0).
- **The OBO token for the disable:** `aud` is Graph. The user is still the approver (`oid`), and the client is the guardian's agent identity (`appid`), with `xms_par_app_azp` naming the blueprint (seen live on 10 November 2026, version 1.0; [the claims](#the-obo-token-the-agent-acting-for-the-approver), 12 November 2026).

## See a real token

**In one line:** the `token-claims` tool asks the sidecar for the guardian's token and prints the token's decoded claims.

```sh
docker compose run --rm token-claims graph
```

The tool prints the claims, never the token itself, so its output cannot be replayed. The claims hold real tenant and object IDs, so redact them in any screenshot ([example](screenshots/phase2-guardian-token-claims.png)). For the setup, see [Local run: the sidecar](infra.md#local-run-the-sidecar).

Two runs a few minutes apart showed the same `iat` and `exp`. So the sidecar caches the token and reuses it while it is valid. It does not ask Entra on every call.

## TR, claim by claim

**In one line:** this is TR, an app-only access token for Microsoft Graph, issued to the guardian's agent identity `agent-steward-guardian-01`.

Each claim is marked **documented** (Microsoft Learn describes it) or **observed** (Learn does not describe it, and the note says what this lab saw).

### Who issued it, for which API, and for how long

- **`iss` and `idp` (documented):** `https://sts.windows.net/<TENANT_ID>/`. The tenant's Entra issued the token (`iss`), and the same tenant authenticated the subject (`idp`). This URL form means a v1.0 token.
- **`tid` (documented):** `<TENANT_ID>`, the tenant the token is valid in.
- **`tenant_region_scope` (observed):** `OC`, the tenant's region (Oceania).
- **`aud` (documented):** `https://graph.microsoft.com`. Only Microsoft Graph should accept this token.
- **`ver` (documented):** `1.0`, the token format. Because this is a v1.0 token, the client is in `appid`. A v2.0 token uses `azp` instead.
- **`iat`, `nbf`, `exp` (documented):** Issued at, not valid before, and expiry, as Unix times. This token lived 65 minutes.

### Who the client is

- **`appid` (documented):** `<AGENT_APP_ID>`, the agent identity's appId. So the client is the agent identity, not the blueprint.
- **`app_displayname` (observed):** `agent-steward-guardian-01`, the client's name.
- **`oid` (documented):** The principal's object ID. For an agent identity it equals the appId, so here it is `<AGENT_APP_ID>` again.
- **`sub` (documented):** The subject. In this app-only token it equals `oid`. In a token with a user, `sub` is pairwise: each app sees a different value for the same user.
- **`idtyp` (documented):** `app`, so no user is in the token.
- **`appidacr` (documented):** `2`. Learn says `2` means a client certificate and `1` means a client secret. Here the client sent T1 as a signed assertion, and the value was also `2`.

### The Agent ID claims

- **`xms_act_fct` (documented):** The actor facets, `3 11 9`. They contain `11`, which means AgentIdentity, so the actor (the client) is an agent identity.
- **`xms_sub_fct` (documented):** The subject facets, `11 9 3`. They also contain `11`, because in the autonomous flow the agent is both the actor and the subject. The order changes between tokens: another token here had `9 3 11`. Learn documents only `11` (AgentIdentity) and `13` (an agent's user account), so ignore the other values.
- **`xms_par_app_azp` (documented):** `<BLUEPRINT_APP_ID>`, the parent of the client, which is the agent's blueprint. Log it, but never authorize on it, because all agents of the blueprint share it.
- **`xms_idrel` (documented):** `7 22`, how the subject relates to the tenant. `7` means service principal. The second value changed between tokens (`8`, `12`, `22`) and is not in Learn's list.
- **`xms_tnt_fct` (documented):** `3 12`, facts about the tenant. Learn documents the claim, but not these values.

### What the token allows

- **`roles` (documented):** Missing. When this token was issued, no Graph application permissions were granted to the agent identity, so Graph would refuse every call with it.
- **`scp` (documented):** Missing, which is normal for app-only tokens. Delegated scopes appear only when a user is in the token.
- **`wids` (documented):** Directory role template IDs assigned to the principal. This token had one ID that is not on Microsoft's list of built-in roles, although no role was assigned. Do not authorize on it.

### Internal claims

Learn calls some claims "opaque": they are internal to Entra, and resources should not use them. The undocumented `xms_*` claims below get the same treatment.

- **`aio` and `rh` (documented as opaque):** Entra's internal data for token reuse and revalidation.
- **`uti` (documented):** A unique ID for this token, like `jti` in the JWT spec.
- **`acrs` (documented):** Normally lists the Conditional Access authentication contexts the caller satisfied (`c1`, `c2`, and so on). This token had `pfdr`, which Learn does not describe.
- **`xms_aud_guid` (observed):** `00000003-0000-0000-c000-000000000000`, Microsoft Graph's appId. So it is the audience as a GUID.
- **`xms_tcdt` (observed):** A Unix time that matches when this lab tenant was created, so very likely the tenant creation time.
- **`xms_pftexp` (observed):** Exactly 24 hours after `exp`.
- **`xms_ftd`, `xms_rd` and `xms_sess_fct` (observed):** No documented meaning.

## T1 and TR side by side

**In one line:** T1, the exchange token, is the blueprint's token for one agent, usable only as that agent's proof, and TR, the resource access token, is the agent's own token for an API.

The sidecar keeps T1 to itself. To see it, the dev tool [token_exchange.py](../tools/token_exchange.py) makes both requests the same way the sidecar does, and prints both tokens' claims (never the tokens themselves):

```sh
.venv/bin/python tools/token_exchange.py --mask
```

It is a dev-only exception: it reads the guardian blueprint's private key from `secrets/`, which in normal runs only the sidecar holds. `--mask` keeps the first and last 4 characters of IDs and opaque values ([screenshot](screenshots/phase2-t1-t2-claims.png)).

### What changes from T1 to TR

- **The audience (`aud`):** T1's is the token exchange resource that request 1 asked for (`api://AzureADTokenExchange`), so T1 cannot call any API. TR's is the Identity API.
- **The client (`azp`):** T1's is the blueprint. TR's is the agent identity.
- **The principal (`oid`):** T1's is the blueprint principal, the blueprint's service principal in this tenant. TR's is the agent identity.
- **The subject (`sub`):** T1's is an FMI path, `/eid1/c/pub/t/<tenant>/a/<blueprint>/<agent>`. The tenant ID and the blueprint's appId are written as 22 characters of base64url, and the agent's appId as a plain GUID. So T1 names exactly one agent of one blueprint. TR's `sub` is the agent identity.
- **The actor facets (`xms_act_fct`):** T1's are `9 3`, with no `11`, because the actor is the blueprint. TR's contain `11`, because the actor is an agent identity.
- **The permissions (`roles`):** T1 has none. TR has `Findings.Submit`.
- **The parent (`xms_par_app_azp`):** Only TR has it. It names the blueprint.
- **The client authentication (`azpacr`):** `2` in both. In request 1 the blueprint signed with its certificate. In request 2 the agent sent T1 as its assertion. So TR's `2` describes request 2 only (see [What TR does not show](#what-tr-does-not-show-the-blueprints-credential)).
- **`xms_ficinfo`:** Only T1 has it. It is not documented, and its name suggests federated credential information, which fits T1's use as a federated assertion.

### The claims, masked

T1, from request 1 (client: the blueprint):

Reference:
```
b445…63b4: Blueprint's application id
0927…28e5: Blueprint's principal id
1927…9d25: Agent identity's object ID, and also its app ID (the two are equal for an agent identity)
cc7d…c57a: Identity API's application id
fb60…9936: the token exchange resource (api://AzureADTokenExchange)
```

```jsonc
{
  "aio": "ASQA…0eg=",
  "aud": "fb60…9936",
  "azp": "b445…63b4", // Blueprint's application - who requested the token.
  "azpacr": "2",
  "exp": 1793071205,
  "iat": 1793067305,
  "idtyp": "app",
  "iss": "https://login.microsoftonline.com/6dfa…8171/v2.0",
  "nbf": 1793067305,
  "oid": "0927…28e5", // Blueprint's principal - who's authenticated.
  "rh": "1.AU…CAA.",
  "sub": "/eid1/c/pub/t/Qj76…qBcQ/a/uulF…pjtA/1927…9d25", // The FMI path, which names the tenant, the blueprint and the agent (agent = agent id's principal) - the entity this token represents.
  "tid": "6dfa…8171",
  "uti": "Slcw…fXAA",
  "ver": "2.0",
  "xms_act_fct": "9 3",
  "xms_aud_guid": "fb60…9936",
  "xms_ficinfo": "CAAQABgAIAAoAjAA",
  "xms_ftd": "O_7K…c21z",
  "xms_idrel": "7 26",
  "xms_sess_fct": "21 14",
  "xms_sub_fct": "9 3",
  "xms_tnt_fct": "3 4"
}
```

TR, from request 2 (client: the agent identity, for the Identity API):

```jsonc
{
  "aio": "ASQA…gt0=",
  "aud": "cc7d…c57a", // Identity API's application.
  "azp": "1927…9d25", // The agent identity's app ID (its client ID) - who requested this. For an agent identity it equals its object ID.
  "azpacr": "2",
  "exp": 1793071205,
  "iat": 1793067305,
  "idtyp": "app",
  "iss": "https://login.microsoftonline.com/6dfa…8171/v2.0",
  "nbf": 1793067305,
  "oid": "1927…9d25", // Agent id's principal id
  "rh": "1.AU…CAA.",
  "roles": ["Findings.Submit"],
  "sub": "1927…9d25", // the entity this token represents.
  "tid": "6dfa…8171",
  "uti": "tbBt…sqAA",
  "ver": "2.0",
  "xms_act_fct": "3 11 9",
  "xms_ftd": "vDZi…c21z",
  "xms_par_app_azp": "b445…63b4",
  "xms_sub_fct": "11 3 9"
}
```

Seen in this lab on 27 October 2026. The screenshot comes from a later run, so the values that change with every token differ: the times, `aio`, `uti`, `xms_ftd`, and the order of the facet numbers.

### Where Entra checks the parent blueprint

**In one line:** Entra checks that the agent belongs to the blueprint only at request 2, when the agent presents T1.

Tested in this lab on 27 October 2026, with the guardian's blueprint and an agent of another blueprint (the dormant fixture):

1. **Request 1 was issued:** The guardian's blueprint asked for T1 with `fmi_path` naming the foreign agent, and Entra issued it. T1's `sub` even named that agent.
2. **Request 2 was refused:** The foreign agent presented that T1, and Entra refused with `AADSTS7002134`: "The presented credential was issued for agent blueprint '…' and agent identity '…', but agent identity '…' has a different blueprint as parent."

**The lesson:** T1 alone proves nothing about the agent, because Entra will issue a T1 naming any agent. The parent–child link is enforced only when the agent presents T1. So a leaked blueprint key exposes only that blueprint's own agents.

### How a token names the API

**In one line:** the token names the API by its app ID. The service principal's object ID only appears in the directory's own records: the sign-in log (`resourceServicePrincipalId`) and the role assignments (`resourceId`).

Checked in this lab on 27 October 2026, for the Identity API and the sign-in event behind TR:

| Where | What identifies the Identity API |
|---|---|
| The token (TR) | `aud` = its app ID (the Application ID in the portal) |
| Entra's sign-in log | `resourceId` = its app ID, and `resourceServicePrincipalId` = its service principal's object ID |
| Graph role assignments | `resourceId` = its service principal's object ID |

The client asks for the API by its app ID (`<appId>/.default`), and the app ID is the same in every tenant, while the object ID belongs to one tenant only. Note the asymmetry: the token names its subject, the caller, by object ID (`oid`), but its audience, the API, by app ID (`aud`).

### What TR does not show: the blueprint's credential

**In one line:** TR does not say whether the blueprint proved itself with a secret or a certificate, so an API cannot require "certificates only". Only Entra's sign-in log shows it.

Tested in this lab on 27 October 2026. A 1-day secret was added to the guardian's blueprint for the test and removed after it.

| | The blueprint used its certificate | The blueprint used a secret |
|---|---|---|
| Request 1 | T1 issued | T1 issued |
| T1 `azpacr` | `2` | `1` |
| TR `azpacr` | `2` | `2` |
| Sign-in log, request 1 (`clientCredentialType`) | `clientAssertion` | `clientSecret` |
| Sign-in log, request 2 (`clientCredentialType`) | `federatedIdentityCredential` | `federatedIdentityCredential` |

TR's `azpacr` describes request 2 only. There the client is the agent identity, and its proof is always T1, a signed assertion. Entra records that proof as a federated identity credential. So a rule like "blueprints use certificates, never secrets" must be enforced on the blueprint itself, by the credentials it holds ([how](infra.md#the-blueprints-credential)), or watched in the sign-in log. The Identity API cannot enforce it.

## The approver's token: a delegated token

**In one line:** this is the token the CLI gets after a person signs in through `agent-steward-agent-disable-cli`. It is a delegated access token for the Identity API: the client acts for a signed-in person. No OBO is involved.

It was captured on 3 November 2026 with `docker compose run --rm approve python approve.py --show-claims` ([how](infra.md#the-cli)), which prints the claims and never the token. The IDs and personal details are replaced with placeholders.

### The claims, with placeholders

```json
{
  "aio": "<opaque>",
  "aud": "<IDENTITY_API_APP_ID>",
  "azp": "<AGENT_DISABLE_CLI_APP_ID>",
  "azpacr": "0",
  "exp": 1793682788,
  "iat": 1793677091,
  "idtyp": "user",
  "iss": "https://login.microsoftonline.com/<TENANT_ID>/v2.0",
  "name": "<APPROVER_NAME>",
  "nbf": 1793677091,
  "oid": "<APPROVER_OBJECT_ID>",
  "preferred_username": "<APPROVER_UPN>",
  "rh": "<opaque>",
  "roles": ["AgentDisable.Approve"],
  "scp": "AgentDisable.Decide",
  "sid": "<SESSION_ID>",
  "sub": "<PAIRWISE_SUBJECT>",
  "tid": "<TENANT_ID>",
  "uti": "<opaque>",
  "ver": "2.0",
  "xms_act_fct": "3 9",
  "xms_ftd": "<opaque>",
  "xms_sub_fct": "6 3"
}
```

### What differs from TR

| Claim | TR (the guardian, app-only) | The approver's token (delegated) |
|---|---|---|
| `idtyp` | `app` | `user`. Entra sends it on user tokens only because the Identity API asks for it with `include_user_token`. |
| `sub` and `oid` | The agent identity | The person. `sub` is pairwise: another app would see a different value. Use `oid`. |
| `name`, `preferred_username`, `sid` | Missing | The person's name, sign-in name, and session |
| `azp` (or `appid`) | The agent identity | The CLI: the client that acts for the person |
| `azpacr` (or `appidacr`) | `2`: the client proved itself with a signed assertion | `0`: a public client, with no credential |
| `scp` | Missing | `AgentDisable.Decide`: what the CLI may do for the person |
| `roles` | The agent's app roles | `AgentDisable.Approve`: what the **person** may do on the Identity API |
| `xms_act_fct` | Contains `11`: the client is an agent | `3 9`, no `11`: the client is not an agent |
| `xms_sub_fct` | Contains `11`: the subject is an agent | `6 3`, no `11` or `13`: a human, not an agent's user account |
| `xms_par_app_azp` | The blueprint | Missing: no agent is involved |

This is the token `check_approver` accepts ([token_validation.py](../src/identity_api/token_validation.py)): `idtyp` is `user`, `xms_act_fct` has no `11` (the client is not an agent), `xms_sub_fct` has no `13` (the person is not an agent's user account), `azp` is the CLI, `scp` has `AgentDisable.Decide`, and `roles` has `AgentDisable.Approve`. It lived 95 minutes. Learn documents only `11` and `13` among the facet values, so the other values are not read.

## The OBO token: the agent acting for the approver

**In one line:** this is the Graph token the executor gets when it exchanges Tc with OBO. The approver is the subject, and the guardian's agent identity is the client that acts for them. It is Entra's version of a delegation token ([Delegation](delegation-and-trusted-subsystem.md#2-delegation)).

It was captured on 12 November 2026 from Loki, with `HTTP_BODY_LOG=1` ([how](infra.md#request-and-response-bodies-debugging-only)): the token was in the sidecar's answer to the executor. Its payload was decoded locally, and the token itself was not kept. It lived 68 minutes. The approver's details are replaced with placeholders. The guardian's IDs are masked as in [TR](#the-claims-masked), so the same values can be matched.

### The OBO claims, masked

```jsonc
{
  "aud": "https://graph.microsoft.com",
  "iss": "https://sts.windows.net/6dfa…8171/", // The v1.0 form, so this is a v1.0 token.
  "iat": 1794474622,
  "nbf": 1794474622,
  "exp": 1794478707,
  "acct": 0,
  "acr": "1",
  "acrs": ["p1", "pfdr"],
  "aio": "AZQA…B67p",
  "amr": ["pwd", "mfa"], // How the approver signed in.
  "app_displayname": "agent-steward-guardian-01",
  "appid": "1927…9d25", // The agent identity - the client acting for the approver. TR's azp has the same value.
  "appidacr": "2",
  "family_name": "<APPROVER_FAMILY_NAME>",
  "given_name": "<APPROVER_GIVEN_NAME>",
  "idtyp": "user",
  "ipaddr": "<APPROVER_IP_ADDRESS>",
  "name": "<APPROVER_NAME>",
  "oid": "<APPROVER_OBJECT_ID>", // The approver - the subject.
  "platf": "5",
  "puid": "<opaque>",
  "rh": "1.AU…CAA.",
  "scp": "AgentIdentity.EnableDisable.All profile openid",
  "sid": "<SESSION_ID>",
  "sub": "<PAIRWISE_SUBJECT>",
  "tenant_region_scope": "OC",
  "tid": "6dfa…8171",
  "unique_name": "<APPROVER_UPN>",
  "upn": "<APPROVER_UPN>",
  "uti": "MHac…0oAA",
  "ver": "1.0",
  "wids": ["62e9…5e10", "b79f…5509"],
  "xms_act_fct": "3 9 11", // Contains 11: the actor is an agent identity.
  "xms_aud_guid": "00000003-0000-0000-c000-000000000000",
  "xms_ftd": "44MU…c21z",
  "xms_idrel": "26 1",
  "xms_par_app_azp": "b445…63b4", // The blueprint, the agent's parent.
  "xms_pftexp": 1794565107,
  "xms_sess_fct": "21 16",
  "xms_st": { "sub": "<opaque>" },
  "xms_sub_fct": "3 12", // No 11 or 13: the subject is a person, not an agent.
  "xms_tcdt": 1792649921,
  "xms_tnt_fct": "2 3"
}
```

### Who is who in the token

- **The subject, the approver (documented):** `oid` is "the verified identity of the user or service principal", and `idtyp` is `user`. `sub` is pairwise, so use `oid` ([Access token claims reference][claims-ref]).
- **The actor, the agent identity (documented):** `appid` is "The application ID of the client using the token. The application can act as itself or on behalf of a user." ([Access token claims reference][claims-ref]) Here it acts for the approver. `xms_act_fct` contains `11`, so the actor is an agent identity ([Token claims reference for agent IDs][agent-claims]).
- **The parent (documented):** `xms_par_app_azp` names the guardian's blueprint ([Token claims reference for agent IDs][agent-claims]).
- **The rights, the approver's (documented):** `scp` holds the delegated `AgentIdentity.EnableDisable.All`, and there is no `roles` claim. So Graph checks the approver's own rights, through the scope. `wids` lists the approver's directory roles ([Access token claims reference][claims-ref]): `62e9…5e10` is Global Administrator ([Microsoft Entra built-in roles][builtin-roles]). The other ID was not identified. A Global Administrator as approver is a lab simplification.
- **The approver's sign-in (documented):** `amr` is `pwd` and `mfa`. So the OBO token still says how the person signed in, after the exchange ([Access token claims reference][claims-ref]).

### From Tc to the OBO token

Tc was seen live on 10 November 2026 ([`aud` and `sub` in the guardian](#aud-and-sub-in-the-guardian)). Only these claims of Tc were recorded:

- **The audience (`aud`):** Tc's is the guardian's blueprint. The OBO token's is Graph.
- **The client (`azp`, then `appid`):** Tc's is the approve CLI. The OBO token's is the agent identity. **The CLI is gone:** no claim names it.
- **The scope (`scp`):** Tc's is `access_as_user`. The OBO token's is `AgentIdentity.EnableDisable.All`.
- **The version (`ver`):** Tc's is `2.0`. The OBO token's is `1.0`, so the client is in `appid`, not `azp`.
- **The person (`oid`):** the same approver in both.

### Compared with RFC 8693 delegation

The RFC's delegation token is in [The Data: the act Claim](delegation-and-trusted-subsystem.md#the-data-the-act-claim).

- **The same idea:** the token is about the user, and it names the party that acts for the user ([RFC 8693, section 1.1][rfc8693-1.1]).
- **The actor is in another claim:** the RFC uses `act`. Entra uses `appid` (or `azp` in v2.0), and the Agent ID claims `xms_act_fct` and `xms_par_app_azp`.
- **No chain:** the RFC nests earlier actors inside `act` ([RFC 8693, section 4.1][rfc8693-4.1]). The OBO token names only the current client: the CLI, Tc's client, is not in it.

### Other claims

The internal claims (`aio`, `rh`, `uti`, `acrs` and the `xms_*` claims with no documented meaning) are read as in TR ([Internal claims](#internal-claims)). Three claims here are not in Learn's access token claims reference: `acct`, `platf` and `xms_st`, an object with a `sub` that differs from this token's `sub` (checked on 12 November 2026).

## Scopes and app roles

**In one line:** a role says what a caller (a person or an app) may do, and a scope says what a client app may do for the signed-in person. When a person uses an app, the API checks both, so the action is allowed only when both allow it.

### The two side by side

| | Role (app role) | Scope (delegated permission) |
|---|---|---|
| Question it answers | May this **caller** do it? | May this **client app** do it **for this person**? |
| Who gets it | A user, a group or a service principal (an agent too) | A client application |
| How it is given | An admin assigns it | Someone consents to it: the user, or an admin for all users |
| Claim in the token | `roles` | `scp` |
| When it is in the token | App-only tokens and user tokens | User (delegated) tokens only |

A picture to remember it: you may drive your car, so that is your role. A valet key lets the valet drive it but not open the boot, so that is a scope. The valet can do only what both allow.

**Two rules for reading a token:**

- **`roles`:** what the token's subject may do on the token's `aud`. With no user, that's the client. With a user, that's the user.
- **`scp`:** what the client may do for the signed-in user. It only appears when a user is involved.

| Same client | `roles` holds | `scp` holds |
|---|---|---|
| Autonomous token | The client's app roles | Nothing |
| OBO token | The signed-in user's roles, if any | The client's delegated permissions |

**When two tokens have the same claims:**

- **Same end user, same resource API → same `roles`**, whatever the client. The roles belong to the user, on that API.
- **Same client, same resource API → same `scp`** for every user, when the grant's `consentType` is `AllPrincipals` (admin consent). With `Principal` (user consent), each grant covers one user, so `scp` can differ between users.

**So, in a user token:** `scp` answers "may this client act for this user?", and `roles` answers "may this user do it?". The API needs both to say yes. An example from this project: `AgentDisable.Decide` is granted to the CLI with `AllPrincipals`, so every user who signs in to the CLI gets it in `scp`. That alone would let anyone decide. The `AgentDisable.Approve` role limits it to approvers.

**A scope is granted, a role is assigned:**

- **A scope** is granted to the client app, for all users (`AllPrincipals`) or for one user (`Principal`). It cannot be granted for a group.
- **A role** is assigned to a user, a group or a service principal. That is why the approvers group can hold `AgentDisable.Approve`.
- **So, to allow only some people:** use a role, or set "Assignment required" on the client's enterprise application and assign the group (assigning a group needs Entra ID P1 or P2), so only its members can sign in to the client. This project uses the role only. "Assignment required" would be a second layer.

**Roles cannot be narrowed, scopes can:**

- **`roles`, app-only:** the client asks for `.default`, so the token carries every app role the client holds on that API. For this reason, the guardian's Graph token carried its write role until that role was removed on 10 November 2026 ([A1](../THREATMODEL.md#accepted-risks)). A disable now uses a delegated token, through OBO.
- **`roles`, delegated:** the token carries every role the user holds on that API. The client cannot ask for fewer.
- **`scp`:** a client can ask for one named scope instead of `.default`, and gets only that scope.
- **The ways around it:** separate identities (for example one to read and one to write), or separate APIs, so a token for one API carries no roles for the other.

Standard OAuth lets a client name the scope it wants in any grant ([RFC 6749 §3.3](https://www.rfc-editor.org/rfc/rfc6749#section-3.3)), so it can ask for less. Entra's app roles are the exception.

### The standard

- **Scope is OAuth's word, and it is general.** It is the extent of the access the client asks for, with or without a user ([RFC 6749 §3.3](https://www.rfc-editor.org/rfc/rfc6749#section-3.3)). Every grant can carry a `scope` parameter, also the client credentials grant, which has no user ([§4.4.2](https://www.rfc-editor.org/rfc/rfc6749#section-4.4.2)). So by the standard, an app-only token can hold scopes.
- **Roles are not in core OAuth.** The JWT access token profile puts the granted scopes in a `scope` claim, one string with the values separated by spaces. It only allows `roles`, `groups` and `entitlements` claims, borrowed from SCIM ([RFC 9068 §2.2.3](https://www.rfc-editor.org/rfc/rfc9068#section-2.2.3)).

### Where vendors differ

- **Entra splits permissions by whether a user is in the token** ([source](https://learn.microsoft.com/en-us/entra/identity-platform/permissions-consent-overview)):
  - With a user, the app gets **delegated permissions** (scopes) in `scp`, and it can never reach more than the user could.
  - With no user, the app gets **application permissions**, which are app roles, in `roles`. Only the client credentials grant gives them.
  - So an app-only token **never** has `scp`. A user token can have both: `scp` for the client, and `roles` for the roles the user holds on this API.
  - The claim is named `scp`, not the standard `scope`.
  - The same app-role object serves as a user's role and as an app's permission.
  - On-Behalf-Of uses only delegated scopes, never app roles ([source](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-on-behalf-of-flow)).
- **The `.default` trap in Entra:** an app-only client still sends `scope=<appId>/.default`, but what comes back is roles. In the client credentials grant, `scope` is only the name of the request parameter. `.default` means "every permission an admin granted to me on this API".
- **Okta follows the standard more closely:** a client credentials token carries the granted custom scopes in `scp` ([source](https://developer.okta.com/docs/guides/implement-grant-type/clientcreds/main/)).
- **Auth0 too:** a machine-to-machine app gets the scopes of its client grant in the token's `scope` claim ([source](https://auth0.com/docs/get-started/applications/application-access-to-apis-client-grants)).

So the short rule is:

| Platform | Scope means |
|---|---|
| OAuth standard, Okta, Auth0 | What the client may do, with or without a user |
| Entra | What the client may do **for a signed-in user**. App-only access uses app roles. |

### In the guardian

| Token | `scp` | `roles` | Why |
|---|---|---|---|
| The guardian's TR for the Identity API (app only) | none | `Findings.Submit`, `AgentDisable.Request` | No person is signed in, so only roles apply. |
| The CLI's token (a person, through `agent-steward-agent-disable-cli`) | `AgentDisable.Decide` | `AgentDisable.Approve` | A person signs in through a client app, so both apply. |
| Tc, the CLI's second token, for the guardian's blueprint | `access_as_user` | none | The executor checks the scope and that the person is the approver. |
| The OBO token for Graph (the guardian, for the approver) | `AgentIdentity.EnableDisable.All` (seen with `profile openid`) | none | A delegated permission, limited by what the approver may do. |

The Identity API needs both claims in the approver's token (FR7):

- **The scope is for the client.** Entra issues a token for the API only to an app that got consent for the scope. So another app the approver signs in to cannot approve in their name.
- **The role is for the person.** The client app holds the scope, but a person outside the group `agent-steward-agent-disable-approvers` gets no `AgentDisable.Approve`, so the API refuses them.

The roles and the scope are defined in [identity-api.bicep](../setup/bicep/identity-api.bicep). The group, the client app and its consent are in [agent-disable-approvers.bicep](../setup/bicep/agent-disable-approvers.bicep).

## What an API must check

**In one line:** an API decides from the token alone whether to accept a call, using a fixed list of checks, and the first failed check rejects the call.

1. **Signature:** Check the signature with Entra's public signing keys, from the tenant's OpenID metadata. The token header's `kid` names the key.
2. **Issuer and tenant:** `iss` and `tid` name the expected tenant.
3. **Audience:** `aud` names this API. A Graph token like the one above must fail here.
4. **Lifetime:** The current time is between `nbf` and `exp`.
5. **Agent:** `xms_act_fct` contains `11`. Split the value on spaces and look for `11`. Never compare the whole string.
6. **Permission:** `roles` contains the app role the operation needs. App-only tokens carry no `scp`.
7. **Log:** Record the agent (`appid` or `azp`, and `oid`) and its blueprint (`xms_par_app_azp`).

To tell the modes apart, `idtyp` is `app` in the autonomous flow and `user` when a user is in the token. `xms_sub_fct` containing `13` marks an agent's user account instead of a human.

By default, Entra sends `idtyp` only on app-only tokens. A user token carries it only when the API's optional claim asks for it with `include_user_token` ([Learn](https://learn.microsoft.com/en-us/entra/identity-platform/optional-claims-reference)). In this lab, the first real approver token had no `idtyp`, so the approver check refused it with a 403. The fix was the setting in [identity-api.bicep](../setup/bicep/identity-api.bicep), not a looser check.

Learn lists the `xms_*` agent claims as optional claims, so check that tokens for your own API carry them. If they are missing, check 5 rejects the token, so the API fails safe. In this lab, the guardian's first token for the Identity API had none of them, and no `idtyp` either. They appeared only after the API's app registration asked for them as optional claims (`idtyp`, `xms_act_fct`, `xms_sub_fct` and `xms_par_app_azp`, in [identity-api.bicep](../setup/bicep/identity-api.bicep)).

This is the check list for the project's Identity API (FR5). The code is [token_validation.py](../src/identity_api/token_validation.py), its unit tests are in [tests/unit/identity_api](../tests/unit/identity_api/test_token_validation.py), and its integration tests with real Entra tokens are in [tests/integration/identity_api](../tests/integration/identity_api/test_with_real_tokens.py).

### What kind of caller vs what it may do

**In one line:** check 5 decides what kind of caller this is, and check 6 decides what it may do. They are two separate decisions.

- **What kind of caller (check 5):** Entra stamps facts about the caller into the token. `xms_act_fct` containing `11` means the client (the actor) is an agent identity. An agent identity can get a token only through its blueprint, with T1 and then TR, so the value also implies that sign-in path. The Identity API enforces this itself: it accepts agents only. A plain app or a blueprint carries no `11` (for example `3 9`), so it is rejected, even if it holds the right role.
- **What it may do (check 6):** The authorization is role-based. `roles` must contain `Findings.Submit`. Which agents hold that role is decided in Entra, as an app role assignment, not in a list of agent IDs inside the API. So it is governed in one place, visible in the portal, and revoked without a deploy.
- **What is not an authorization decision:** The agent's own ID (`azp`, `oid`) and its blueprint (`xms_par_app_azp`) are only logged. Never authorize on the blueprint: every agent of that blueprint would pass.

Why check 6 alone is not enough: an app role can be assigned to any service principal, including a plain app. So without check 5, a role given to a normal app by mistake would let that app in.

**Anchor:** FAPI, which CDR builds on, makes the API check the `cnf` claim: the access token is bound to the client's mTLS certificate, so only the holder of that certificate may use it. Like check 5, it is a fact about the client that the authorization server stamps into the token, and the API checks it separately from the scopes, which say what the client may do. Unlike check 5, it does not say what kind of client this is, and it says nothing about the grant: a token from the authorization code grant and one from the refresh token grant carry the same binding.

### A real negative case: the blueprint's own token

When the sidecar's agent identity override is off, the sidecar ignores `AgentIdentity` and returns the blueprint's own token instead (see [Local run: the sidecar](infra.md#local-run-the-sidecar)). That token looked almost the same, but:

- **`app_displayname`:** `agent-steward-guardian`, the blueprint.
- **`appid`:** The blueprint's appId.
- **`roles`:** `AgentIdentity.CreateAsManager`, a blueprint permission.
- **`xms_act_fct` and `xms_sub_fct`:** `3 9`, with no `11`.
- **`xms_par_app_azp`:** Missing.

So check 5 rejects it: its actor facets do not contain `11`.

## Sources

- [Access token claims reference](https://learn.microsoft.com/en-us/entra/identity-platform/access-token-claims-reference) (Microsoft Learn, checked 26 October 2026).
- [Token claims reference for agent IDs](https://learn.microsoft.com/en-us/entra/agent-id/agent-token-claims) (Microsoft Learn, updated August 2026, checked 26 October 2026).
- Real tokens from this lab's guardian agent identity, 26 October 2026.
- Scopes and app roles: [RFC 6749](https://www.rfc-editor.org/rfc/rfc6749), [RFC 9068](https://www.rfc-editor.org/rfc/rfc9068), [Overview of permissions and consent](https://learn.microsoft.com/en-us/entra/identity-platform/permissions-consent-overview) (Microsoft Learn), [Okta client credentials](https://developer.okta.com/docs/guides/implement-grant-type/clientcreds/main/) and [Auth0 client grants](https://auth0.com/docs/get-started/applications/application-access-to-apis-client-grants), checked 30 October 2026.
- `aud` and `sub`: [RFC 9068](https://www.rfc-editor.org/rfc/rfc9068), [RFC 6749](https://www.rfc-editor.org/rfc/rfc6749), [RFC 6750](https://www.rfc-editor.org/rfc/rfc6750), [RFC 8693](https://www.rfc-editor.org/rfc/rfc8693), [Access tokens](https://learn.microsoft.com/en-us/entra/identity-platform/access-tokens) and [Refresh tokens](https://learn.microsoft.com/en-us/entra/identity-platform/refresh-tokens) (Microsoft Learn), checked 4 November 2026.
- The OBO token: [Access token claims reference](https://learn.microsoft.com/en-us/entra/identity-platform/access-token-claims-reference) (last updated 24 July 2026), [Microsoft Entra built-in roles](https://learn.microsoft.com/en-us/entra/identity/role-based-access-control/permissions-reference) (last updated 13 October 2026) and [RFC 8693](https://www.rfc-editor.org/rfc/rfc8693), checked 12 November 2026. The token was seen in this lab on 12 November 2026.

[claims-ref]: https://learn.microsoft.com/en-us/entra/identity-platform/access-token-claims-reference#payload-claims
[agent-claims]: https://learn.microsoft.com/en-us/entra/agent-id/agent-token-claims
[builtin-roles]: https://learn.microsoft.com/en-us/entra/identity/role-based-access-control/permissions-reference#all-roles
[rfc8693-1.1]: https://www.rfc-editor.org/rfc/rfc8693.html#section-1.1
[rfc8693-4.1]: https://www.rfc-editor.org/rfc/rfc8693.html#section-4.1
