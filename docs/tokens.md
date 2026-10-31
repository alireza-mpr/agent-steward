# Agent tokens: claims and validation

This doc explains why an API must be registered before any token exists for it, what is inside an agent's resource access token (TR, Microsoft's name for it), and what an API must check before it accepts one. For how the agent gets the token, see [How an agent signs in](agent-id.md#3-getting-a-token-the-agent-autonomous-app-flow). Every claim below was seen in a real token from this lab, and each one says whether Microsoft documents it.

## Contents

- [Why the API must be registered first](#why-the-api-must-be-registered-first)
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
| The `approve` command's token (a person, through `agent-steward-agent-disable-cli`) | `AgentDisable.Decide` | `AgentDisable.Approve` | A person signs in through a client app, so both apply. |

The Identity API needs both claims in the approver's token (FR7, planned):

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
