# Agent tokens: claims and validation

This doc explains what is inside an agent's access token (T2), and what an API must check before it accepts one. For how the agent gets the token, see [How an agent signs in](agent-id.md#how-an-agent-signs-in). Every claim below was seen in a real token from this lab, and each one says whether Microsoft documents it.

## Contents

- [See a real token](#see-a-real-token)
- [The autonomous token, claim by claim](#the-autonomous-token-claim-by-claim)
  - [Who issued it, for which API, and for how long](#who-issued-it-for-which-api-and-for-how-long)
  - [Who the client is](#who-the-client-is)
  - [The Agent ID claims](#the-agent-id-claims)
  - [What the token allows](#what-the-token-allows)
  - [Internal claims](#internal-claims)
- [What an API must check](#what-an-api-must-check)
  - [A real negative case: the blueprint's own token](#a-real-negative-case-the-blueprints-own-token)
- [Sources](#sources)

## See a real token

**In one line:** the `token-claims` tool asks the token sidecar for the guardian's token and prints the token's decoded claims.

```sh
docker compose run --rm token-claims graph
```

The tool prints the claims, never the token itself, so its output cannot be replayed. The claims hold real tenant and object IDs, so redact them in any screenshot ([example](screenshots/phase2-guardian-token-claims.png)). For the setup, see [Local run: the token sidecar](infra.md#local-run-the-token-sidecar).

Two runs a few minutes apart showed the same `iat` and `exp`. So the sidecar caches the token and reuses it while it is valid. It does not ask Entra on every call.

## The autonomous token, claim by claim

**In one line:** this is T2, an app-only access token for Microsoft Graph, issued to the guardian's agent identity `agent-steward-guardian-01`.

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

Learn lists the `xms_*` agent claims as optional claims, so check that tokens for your own API carry them. If they are missing, check 5 rejects the token, so the API fails safe.

This is the check list for the project's Identity API (FR5).

### A real negative case: the blueprint's own token

When the sidecar's agent identity override is off, the sidecar ignores `AgentIdentity` and returns the blueprint's own token instead (see [Local run: the token sidecar](infra.md#local-run-the-token-sidecar)). That token looked almost the same, but:

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
