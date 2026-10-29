# JWT Bearer and Token Exchange

How a request to an OAuth token endpoint is built, and how to read any JWT in it. Sections 1 to 5 give the model. The appendix holds the details.

**Notation:** `urn:...:` is short for `urn:ietf:params:oauth:`. For example, `urn:...:grant-type:jwt-bearer` is `urn:ietf:params:oauth:grant-type:jwt-bearer`.

## Contents

- [1. The Big Picture](#1-the-big-picture)
- [2. Part 1: Client Authentication ("Who Is Asking?")](#2-part-1-client-authentication-who-is-asking)
- [3. Part 2: The Grant ("What Token, and for Whom?")](#3-part-2-the-grant-what-token-and-for-whom)
- [4. A Third Value: Token Exchange (RFC 8693)](#4-a-third-value-token-exchange-rfc-8693)
- [5. Two Cases From This Project](#5-two-cases-from-this-project)
  - [5.1 Anthropic Uses the Grant, Not a Client Assertion](#51-anthropic-uses-the-grant-not-a-client-assertion)
  - [5.2 OBO Is Not RFC 8693](#52-obo-is-not-rfc-8693)
- [Appendix](#appendix)
  - [A. Client Authentication in Detail](#a-client-authentication-in-detail)
  - [B. client_id in Detail](#b-client_id-in-detail)
  - [C. The Grant in Detail](#c-the-grant-in-detail)
  - [D. The Three Values on the Wire](#d-the-three-values-on-the-wire)
  - [E. The Three Roles: the Full Map](#e-the-three-roles-the-full-map)
  - [F. OBO in Detail](#f-obo-in-detail)
  - [Sources](#sources)

---

## 1. The Big Picture

A token request answers two questions:

| Part | Question | Where it is in the request |
|---|---|---|
| **Client authentication** | Who is asking? | `client_assertion` (or a secret, mutual TLS, or nothing) |
| **The grant** | What token, and for whom? | `grant_type`, and the credential that grant type needs (for example `assertion`) |

Three standard values put a JWT or a token into these parts:

| Value | Part |
|---|---|
| `urn:...:client-assertion-type:jwt-bearer` (RFC 7523) | Client authentication: the JWT proves who the client is |
| `urn:...:grant-type:jwt-bearer` (RFC 7523) | The grant: the JWT is the reason the server issues a token |
| `urn:...:grant-type:token-exchange` (RFC 8693) | The grant: the client trades a token it already has for a new one |

A JWT in either part has three roles: the **issuer** (`iss`) signs it, the **subject** (`sub`) is who it is about, and the **requester** (the client) sends it. The requester always does the asking: a JWT never asks by itself.

Both parts come in the same two worlds:

| | Client authentication | The grant |
|---|---|---|
| **World 1: X vouches for X** (`iss` = `sub`) | The client signs a JWT about itself | The client asks for a token for itself, with a JWT it signed |
| **World 2: X vouches for Y** (`iss` ≠ `sub`) | A trusted issuer vouches for the client | A trusted party vouches for someone else: the client itself (for a user), or an external issuer |

In World 1, the JWT says "I am X, and I vouch for myself", and the server only checks X's signature. In World 2, it says "I am issuer X, and I vouch for subject Y". The server also checks that X may speak for Y, so World 2 needs a setup made in advance, such as an admin approval or a federation trust.

Section 2 zooms into the first column, and section 3 into the second.

---

## 2. Part 1: Client Authentication ("Who Is Asking?")

**What it does:**
- ✅ **It proves:** "The client sending this request is client A, and it is not someone pretending to be A."
- ❌ **It does not decide:** which token comes back, or who the token is for. That is the grant's job. A client assertion can be attached to any grant.

**The methods:** a client assertion (a signed JWT, RFC 7523, or a SAML assertion, RFC 7522), a client secret, mutual TLS, or no proof at all (a public client). This document covers the client assertion. The others are in [appendix A](#a-client-authentication-in-detail).

**The general rule.** RFC 7523 says that for client authentication, `sub` MUST be the `client_id` ([section 3](https://www.rfc-editor.org/rfc/rfc7523.html#section-3)). If `client_id` is also sent, it MUST identify the same client as the assertion ([RFC 7521, section 4.2](https://www.rfc-editor.org/rfc/rfc7521.html#section-4.2)). OpenID Connect's `private_key_jwt` asks for more: `iss` and `sub` must both be the `client_id` ([OpenID Connect Core, section 9](https://openid.net/specs/openid-connect-core-1_0.html#ClientAuthentication)).

**Deviations.** Entra's workload identity federation sends a `sub` from another platform, such as a GitHub repository, and maps it to the app through the app's federated identity credential. Because that assertion does not name the app, Entra requires `client_id` in the request ([appendix B](#b-client_id-in-detail)).

**The two worlds:**
- **World 1, X vouches for X:** The client signs a JWT about itself (`iss` = `sub` = `client_id`). *Example:* an Entra certificate credential. In this lab, the sidecar signs one with the blueprint's certificate in request 1.
- **World 2, X vouches for Y:** A trusted issuer signs a JWT about the client. RFC 7523 allows it, with `sub` = `client_id`. OpenID Connect's `private_key_jwt` does not, because there `iss` must be the client.
  - *Deviation:* in Entra's federation, `sub` is the external identity, not the `client_id`. *Examples:* a GitHub Actions token, a managed identity token (token 2 in [authentication.md](authentication.md#1-how-the-harness-authenticates)), and, in this lab, T1 in request 2.

Under the general rule, a client cannot vouch for someone else in a client assertion: a proof about Alice does not prove that the request comes from client A. For a token for someone else, the client uses the grant (section 3).

More: the claim checks, what the proof is based on, and why it beats a secret, in [appendix A](#a-client-authentication-in-detail).

---

## 3. Part 2: The Grant ("What Token, and for Whom?")

**What it does:** It names the grant type (`grant_type`), which sets the rules, including who the token is for. It also carries the credential that the grant type needs. RFC 6749 calls that credential the *authorization grant* ([section 1.3](https://www.rfc-editor.org/rfc/rfc6749.html#section-1.3)).

| Grant type | The credential | Who the token is for |
|---|---|---|
| `authorization_code` | The code | The resource owner who gave approval |
| `urn:...:grant-type:jwt-bearer` | The JWT, in `assertion` | Typically the JWT's `sub` |
| `urn:...:grant-type:token-exchange` | The subject token | The subject token's subject (section 4) |
| `client_credentials` | None: the client's own credentials act as the grant ([RFC 6749, section 1.3.4](https://www.rfc-editor.org/rfc/rfc6749.html#section-1.3.4)) | The client. Both questions have the same answer. |

**The general rule (the JWT bearer grant).** RFC 7523 says the JWT MUST have a `sub`, which typically identifies who the token is for: the resource owner or an authorized delegate ([section 3](https://www.rfc-editor.org/rfc/rfc7523.html#section-3)). Client authentication is optional: the server decides by policy ([section 3.1](https://www.rfc-editor.org/rfc/rfc7523.html#section-3.1)).

**Deviations.** Google's service account JWT leaves `sub` out, and Google treats the service account itself as the subject. Entra's OBO puts the user's access token in `assertion`, although that token's audience is the middle tier and not the token endpoint, and it adds `requested_token_use=on_behalf_of` ([section 5.2](#52-obo-is-not-rfc-8693)).

**The two worlds:**
- **World 1, X vouches for X:** The client asks for a token for itself, with a JWT it signed. *Example:* a Google service account without impersonation (with the deviation above).
- **World 2, X vouches for Y, where the voucher is the client:** The client signs a JWT that names someone else. An admin must approve this in advance. *Examples:* Salesforce's JWT bearer flow (the app vouches for a user), and Google's domain-wide delegation (the service account vouches for a Workspace user).
- **World 2, X vouches for Y, where the voucher is an external issuer:** Another party signed the JWT, and the server trusts it. *Examples:* Anthropic's workload identity federation (Entra vouches for a managed identity, [section 5.1](#51-anthropic-uses-the-grant-not-a-client-assertion)), Entra's OBO (Entra vouches for a user), and a SAML or OIDC token from a trusted identity provider.

More: the grant's claim rules and the vendor details, in [appendix C](#c-the-grant-in-detail).

---

## 4. A Third Value: Token Exchange (RFC 8693)

Token exchange is a grant type too. The client sends a token it already has, the *subject token*, and gets a new token, usually for another audience or with less scope. It can also send an *actor token*, so the new token can record who acts for whom, in an `act` claim ([RFC 8693, section 4.1](https://www.rfc-editor.org/rfc/rfc8693.html#section-4.1)). *Use case:* a middle-tier API gets a token to call a downstream API as the user.

In this project, token exchange is written design only ([DESIGN-NOTES.md](../DESIGN-NOTES.md#1-token-exchange-rfc-8693---narrowing-tokens)). What each value sends and gets back: [appendix D](#d-the-three-values-on-the-wire).

---

## 5. Two Cases From This Project

### 5.1 Anthropic Uses the Grant, Not a Client Assertion

Anthropic's token 1 is a World 2 JWT: an external issuer, Entra ID, vouches for the managed identity. Anthropic takes it as the grant. That could look like a deviation, because Entra's own requests send a token like it as a client assertion. It is not: the grant is the value that fits.

| | The grant (`grant_type=urn:...:grant-type:jwt-bearer`) | A client assertion (`grant_type=client_credentials` with `client_assertion`) |
|---|---|---|
| What the JWT says | "I, Entra ID (`iss`), vouch for the managed identity (`sub`)." The client, the harness, asks: "Give me a token for `sub`." | "The client sending this request is `sub`." |
| The rule on `sub` ([RFC 7523, section 3](https://www.rfc-editor.org/rfc/rfc7523.html#section-3)) | It names the subject the token is for, such as a workload. The server decides which account that subject maps to. | It **MUST** be the client's `client_id`. |
| Does it fit Anthropic's case? | **Yes.** Entra's `sub` is the managed identity's object ID, and Anthropic's federation rule maps that subject to a service account. | **No.** Anthropic has no OAuth client registration and no `client_id`, and Entra's `sub` could not equal one anyway. |

Anthropic receives a JWT from a third party, Entra ID, and RFC 7523, section 2.1, was written for exactly that case. Entra, by contrast, bends the rule that Anthropic follows: its request 1 sends token 2 as a client assertion, although token 2's subject is the managed identity, not the blueprint. Entra accepts it because the blueprint's federated identity credential maps that issuer and subject to the blueprint ([Two values that look alike](authentication.md#1-how-the-harness-authenticates)).

> **Anthropic only: what this lab saw.** On 29 October 2026, three requests went to Anthropic's token endpoint, `https://api.anthropic.com/v1/oauth/token`, with fake values and no credentials. These findings are about Anthropic's endpoint, not about OAuth in general.
>
> | Request | Answer | What it shows |
> |---|---|---|
> | The grant, JSON body | 400: `federation_rule_id is not a well-formed fdrl_ tagged ID` | The request format is accepted, and the fake IDs are checked |
> | The grant, form body (`application/x-www-form-urlencoded`) | The same 400 | The form body that OAuth requires works too |
> | `client_credentials` with `client_assertion`, form body | 400: `Invalid request format` | Anthropic does not support the client assertion form |
>
> Where Anthropic goes its own way:
> - **The error body:** OAuth says an error is JSON with `error` as a code string, such as `"invalid_request"` ([RFC 6749, section 5.2](https://www.rfc-editor.org/rfc/rfc6749.html#section-5.2)). For these malformed requests, Anthropic answered in its own API error format instead: `{"type":"error","error":{"type":"invalid_request_error","message":…}}`. A real failed exchange, such as one with a bad JWT, was not tested and may answer differently.
> - **Extra parameters:** The request adds `federation_rule_id`, `organization_id`, `service_account_id` and `workspace_id`. That is a normal OAuth extension, because a token endpoint must ignore parameters it does not know ([RFC 6749, section 3.2](https://www.rfc-editor.org/rfc/rfc6749.html#section-3.2)). These four parameters are Anthropic's own, not standard OAuth parameters.

### 5.2 OBO Is Not RFC 8693

Microsoft built its own flow, OBO, which solves the same problem as token exchange, on top of the JWT bearer grant. So it is fair to say "Entra OBO is Microsoft's version of token exchange, built on the JWT bearer grant", but not to call it an RFC 8693 implementation.

- **Standard:** OBO reuses the JWT bearer grant's value with Microsoft's own rules, so it is neither plain RFC 7523 nor RFC 8693.
- **Parameters:** OBO sends `assertion` and `requested_token_use=on_behalf_of`. RFC 8693 uses `subject_token` and `actor_token`.
- **Actor:** RFC 8693 can record "A acts for B" in an `act` claim. OBO cannot.

The full comparison, the history, and Microsoft's confusing wording: [appendix F](#f-obo-in-detail).

---

## Appendix

### A. Client Authentication in Detail

#### The Methods

- **Client assertion:** a JWT ([RFC 7523](https://www.rfc-editor.org/rfc/rfc7523.html#section-2.2), `client_assertion_type=urn:...:client-assertion-type:jwt-bearer`) or a SAML assertion ([RFC 7522](https://www.rfc-editor.org/rfc/rfc7522.html#section-2.2), `urn:...:client-assertion-type:saml2-bearer`). OpenID Connect names two kinds of JWT: `private_key_jwt`, signed with the client's private key, and `client_secret_jwt`, signed with a shared secret ([OpenID Connect Core, section 9](https://openid.net/specs/openid-connect-core-1_0.html#ClientAuthentication)).
- **No authentication (public clients):** The client only identifies itself with `client_id`. It gives no proof, for example a mobile app or a single-page app that cannot keep a secret.
- **Client secret:** `client_secret_basic` sends the ID and the secret in an HTTP Basic `Authorization` header. `client_secret_post` sends them as `client_id` and `client_secret` in the request body, which RFC 6749 marks "NOT RECOMMENDED" ([section 2.3.1](https://www.rfc-editor.org/rfc/rfc6749.html#section-2.3.1)).
- **Mutual TLS:** A client certificate in the TLS handshake: `tls_client_auth` with a certificate from a PKI, or `self_signed_tls_client_auth` with a certificate the client registered ([RFC 8705](https://www.rfc-editor.org/rfc/rfc8705.html)).

#### What the Server Checks

[RFC 7523, section 3](https://www.rfc-editor.org/rfc/rfc7523.html#section-3) sets these rules for every client assertion JWT:

| Claim | The rule | The server's check | What it prevents |
|---|---|---|---|
| The signature | MUST be signed, or have a MAC applied, by the issuer | It is valid for a trusted key | A forged JWT |
| `iss` | MUST identify the issuer. It can be any issuer the server trusts, not only the client. | It is the client itself, or a trusted issuer | A JWT from an unknown party |
| `sub` | MUST be the `client_id` | It is the client's `client_id` | A proof that is about someone else |
| `aud` | MUST identify the authorization server. The token endpoint URL MAY be used. | It is this token endpoint | Replay of the JWT at another server |
| `exp` | MUST be present | It has not expired, and lifetimes are short | Replay later, for example after a leak |
| `nbf`, `iat` | MAY be present | It is not used before `nbf`. The server MAY reject an `iat` that is too far in the past. | Use too early, or of a very old JWT |
| `jti` | MAY be present. The server MAY use it to prevent replay. | It was not used before | Replay of the same JWT twice |

*Deviation:* Entra accepts a `sub` that is not the `client_id` when the app's federated identity credential maps that issuer and subject to the app.

#### The Two Worlds in Detail

**World 1: `iss` = `sub` = `client_id`.**
- OpenID Connect's `private_key_jwt` requires this ([OpenID Connect Core, section 9](https://openid.net/specs/openid-connect-core-1_0.html#ClientAuthentication)).
- Entra's certificate credentials use it: `iss` and `sub` are the app's client ID, and `aud` is the token endpoint URL ([certificate credentials](https://learn.microsoft.com/en-us/entra/identity-platform/certificate-credentials)).

**World 2: a third-party issuer, `sub` = `client_id`.** RFC 7523 allows it, because `iss` can be any issuer the server trusts ([RFC 7521, section 3](https://www.rfc-editor.org/rfc/rfc7521.html#section-3), Figure 1). OpenID Connect's `private_key_jwt` forbids it, because there `iss` must be the `client_id`.

**World 2 in Entra (deviation): `iss` and `sub` come from the external platform.**
- **GitHub Actions:** `iss` is `https://token.actions.githubusercontent.com`, and `sub` names the repository and its context, such as `repo:octo-org/octo-repo:environment:prod`.
- **Kubernetes:** `sub` is `system:serviceaccount:<namespace>:<service account name>`.
- **A managed identity:** token 2 in [authentication.md](authentication.md#1-how-the-harness-authenticates). Its `iss` is the tenant's Entra ID, and its `sub` is the managed identity's object ID.
- **The audience:** `aud` is usually `api://AzureADTokenExchange`. Microsoft recommends it and the portal fills it in. For a managed identity, it is required ([create a trust](https://learn.microsoft.com/en-us/entra/workload-id/workload-identity-federation-create-trust), [trust a managed identity](https://learn.microsoft.com/en-us/entra/workload-id/workload-identity-federation-config-app-trust-managed-identity)).
- **The mapping:** Entra maps the token to the app through the app's federated identity credential, whose issuer and subject must match the token's exactly. A server that applies RFC 7523 strictly would reject these tokens.
- **Entra's agent identity flow, in this lab:** T1 in request 2 is the client assertion. Entra issued it, and its `sub` is an FMI path that names the agent identity, not the agent's `client_id` ([What changes from T1 to TR](tokens.md#what-changes-from-t1-to-tr)).

So a client assertion is not always World 1.

#### What the Proof Is Based On

| | **World 1: the client signs it** (`private_key_jwt`) | **World 2: a trusted issuer signs it** (federation) |
|---|---|---|
| Who signs the JWT | The client, with its own private key | An identity provider, for example GitHub Actions, or Entra for a managed identity |
| What the server checks the signature with | The public key registered for that client in advance, for example the certificate uploaded to the app | The issuer's public keys, which the server fetches from the issuer's OIDC discovery document |
| What makes the server believe "this is client A" | Only client A holds the private key | An admin configured a trust in advance that maps that issuer and subject to client A. In Entra, that trust is the federated identity credential. |
| In this lab | The sidecar signs with the blueprint's certificate in request 1 | Token 2 (enterprise design), and T1 in request 2 |

#### Why Use It Instead of a Secret

- **The secret never travels.** A client secret goes over the wire on every request. With a client assertion, only a signed JWT goes over the wire, and the private key stays with the client. In federation, no long-lived secret exists at all.
- **A stolen JWT is worth little.** It works only at one token endpoint (`aud`), and only for a few minutes (`exp`).
- **Rotation is easier.** You replace a certificate or a trust, not a shared password that sits on both sides.

### B. client_id in Detail

**`client_id` names the client, and the client authentication proves it**, like a username and a password.

**The general rule, with a client assertion: optional.** The assertion's `sub` already identifies the client. If `client_id` is present, it MUST identify the same client that the assertion identifies ([RFC 7521, section 4.2](https://www.rfc-editor.org/rfc/rfc7521.html#section-4.2)).

**"Identify the same client", not "the same string".** The RFC's wording is the safe one, because the two values are not always the same string:

| Case | `client_id` | The assertion's `sub` | How they identify the same client |
|---|---|---|---|
| **Plain RFC 7523** (`private_key_jwt`, a certificate) | `A` | `A` | The same string: RFC 7523 says `sub` MUST be the `client_id` ([section 3](https://www.rfc-editor.org/rfc/rfc7523.html#section-3)). |
| **Entra's federated identity credential** (a GitHub Actions token) | The app's ID, `<app ID>` | `repo:octo-org/octo-repo:environment:prod` | Different strings, and neither is derived from the other. They identify the same client only because an admin added a federated identity credential to the app that names that issuer and that subject. |

**Deviation, Entra: required.** Entra marks `client_id` *Required* in every client credentials request: with a secret, with a certificate, and with a federated credential ([client credentials flow](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-client-creds-grant-flow)). With a certificate, it only repeats the assertion's `sub`. With a federated credential, Entra cannot do without it:
- The assertion does not identify the app: its `iss` and `sub` come from the external platform.
- Microsoft requires an issuer and subject pair to be unique only *on one app* ([create a trust](https://learn.microsoft.com/en-us/entra/workload-id/workload-identity-federation-create-trust)). So the same external identity can be trusted by several apps.
- So `client_id` tells Entra which app is meant, and so whose federated identity credentials to check.

**With other methods:** `client_secret_post` sends `client_id` in the body. `client_secret_basic` sends it as the user name in the `Authorization` header instead. A public client sends `client_id` in the body as its only identification: RFC 6749 makes it required "if the client is not authenticating with the authorization server" ([section 4.1.3](https://www.rfc-editor.org/rfc/rfc6749.html#section-4.1.3)).

### C. The Grant in Detail

#### The Grant JWT's Rules

[RFC 7523, section 3](https://www.rfc-editor.org/rfc/rfc7523.html#section-3):
- **The signature, `iss`, `aud`, `exp`, and the optional `nbf`, `iat` and `jti`:** The same rules as for a client assertion ([appendix A](#what-the-server-checks)).
- **`sub`:** MUST be present. It typically identifies the resource owner or an authorized delegate. In some cases, it is a pseudonymous identifier for an anonymous user. The server decides which account that subject maps to.
- **Client authentication:** "JWT authorization grants may be used with or without client authentication or identification", and whether to require it is a policy decision of the authorization server ([RFC 7523, section 3.1](https://www.rfc-editor.org/rfc/rfc7523.html#section-3.1); also [RFC 7521, section 4.1](https://www.rfc-editor.org/rfc/rfc7521.html#section-4.1)). Anthropic's token endpoint, for example, takes the grant with no client authentication at all.

#### The Examples in Detail

- **A Google service account without impersonation:** The service account signs the JWT as `iss` and leaves out `sub`. *Deviation:* RFC 7523 says `sub` MUST be present. Google treats the subject as the service account itself.
- **Salesforce's JWT bearer flow:** `iss` is the consumer key (the app's client ID), and `sub` is a user's username. An admin approves the users for the app first.
- **Google domain-wide delegation:** `iss` is the service account, and `sub` is a Google Workspace user's email. The Workspace admin must first let the service account act for users.
- **Anthropic's workload identity federation:** token 1 has `iss` = the tenant's Entra ID and `sub` = the managed identity. Anthropic's federation rule is the trust.
- **Entra's OBO:** The assertion is the user's access token, issued by Entra, and the user is the subject. *Deviation:* a Microsoft extension that adds `requested_token_use=on_behalf_of`, and the assertion's audience is the middle tier, not the token endpoint ([appendix F](#f-obo-in-detail)).
- **A federated identity provider:** A JWT, or a SAML assertion ([RFC 7522](https://www.rfc-editor.org/rfc/rfc7522.html#section-2.1), `urn:...:grant-type:saml2-bearer`), issued by an identity provider that the authorization server trusts.

### D. The Three Values on the Wire

| Value | What is sent | What comes back | Where these docs use it |
|---|---|---|---|
| `grant-type:jwt-bearer` | `grant_type=urn:...:grant-type:jwt-bearer`, the JWT in `assertion`, and usually a `scope`. Client authentication is optional in the RFC. | An access token for the requested scope | **Anthropic's workload identity federation:** a managed identity token goes in, and a short-lived Claude API token comes out ([step 2](authentication.md#1-how-the-harness-authenticates)). **Entra's OBO:** the user's token goes in, and a token for the downstream API that still carries the user comes out. |
| `client-assertion-type:jwt-bearer` | `client_assertion_type=urn:...:client-assertion-type:jwt-bearer`, the JWT in `client_assertion`, the `client_id`, and a separate `grant_type`, for example `client_credentials` | Whatever that grant issues. With `client_credentials`: an app-only access token. | **Entra request 1:** a managed identity token as the blueprint's credential goes in, and T1 comes out. **Entra request 2:** T1 as the agent identity's credential goes in, and TR comes out ([steps 3 and 4](authentication.md#1-how-the-harness-authenticates)). **A certificate credential:** a JWT signed with the certificate's private key goes in. |
| `grant-type:token-exchange` | `grant_type=urn:...:grant-type:token-exchange`, the token in `subject_token` with its type in `subject_token_type` (for example `urn:...:token-type:access_token`). Optional: `actor_token` and `actor_token_type`, `audience` or `resource`, `scope`, and `requested_token_type`. | The new token in `access_token`, and its type in `issued_token_type`. When the new token is a JWT and an actor took part, an `act` (actor) claim can name the actor. | **Written design only**, never code in this project ([DESIGN-NOTES.md](../DESIGN-NOTES.md#1-token-exchange-rfc-8693---narrowing-tokens)). Microsoft documents no RFC 8693 flow for this case ([note](agent-architecture.md#three-architectural-pillars)). |

**Both parts in one request.** The two RFC 7523 values can appear together, one for each part. Entra's OBO request does this when the middle-tier app proves itself with a certificate or a federated identity credential: the user's token goes in `assertion` (the grant), and the app's own JWT goes in `client_assertion` (client authentication). The agent OBO request works the same way, with T1 as the `client_assertion` ([Agent OAuth flows: On-behalf-of flow](https://learn.microsoft.com/entra/agent-id/agent-on-behalf-of-oauth-flow)).

### E. The Three Roles: the Full Map

The issuer, the subject and the requester can be the same party or different parties. The issuer is also the one asking only when it is the client: in World 1, and in World 2 when the client vouches.

| Example | Issuer | Subject | Requester | Which are the same |
|---|---|---|---|---|
| `private_key_jwt`, an Entra certificate credential (this lab: the blueprint's certificate in request 1) | The client | The client | The client | All three |
| A third-party client assertion | A trusted token service | The client | The client | Subject = requester |
| Entra workload identity federation: a GitHub Actions token | GitHub | The workflow, `repo:…` | The workflow, as the app | Subject = requester, through the federated identity credential |
| Entra workload identity federation: a managed identity token (token 2) | The tenant's Entra ID | The managed identity | The harness, as the blueprint | Subject = requester, through the federated identity credential |
| Entra's agent identity flow (this lab: T1 in request 2) | The tenant's Entra ID | The agent identity, named by an FMI path | The agent identity | Subject = requester |
| `authorization_code` (not a JWT) | The authorization server, which issued the code | The resource owner | The client | None |
| `client_credentials` (no credential) | — | The client | The client | Subject = requester |
| Token exchange (RFC 8693) | Whoever issued the subject token | The subject token's subject | The client, which can also be named as the actor | Usually none |
| A Google service account without impersonation | The service account | The service account (`sub` left out) | The service account | All three |
| Salesforce's JWT bearer flow | The app | A user | The app | Issuer = requester |
| Google domain-wide delegation | The service account | A Workspace user | The service account | Issuer = requester |
| Anthropic's workload identity federation (token 1) | The tenant's Entra ID | The managed identity | The harness, which runs as the managed identity | Subject = requester |
| Entra's OBO | Entra ID | The user | The middle-tier app | None |
| A federated identity provider | The identity provider | A workload or a user | The client | Depends on the setup |

One pairing has no legitimate example here: issuer = subject, but a different requester. That is what a stolen self-signed JWT looks like when someone else replays it. It is why the server checks `aud`, `exp` and `jti`.

**Reading any JWT in two steps:**
1. **Which parameter carries it?**
   - ✅ `assertion`: it is the grant, and the token is typically for `sub`.
   - ✅ `client_assertion`: it is client authentication, and under the general rule `sub` must be the `client_id`.
   - ❌ Any other `sub` in a client assertion: a server that follows RFC 7523 rejects it. *Deviation:* Entra's workload identity federation maps the external subject to an app.
2. **Is `iss` equal to `sub`?**
   - ✅ Yes: World 1, X vouches for X. The server only needs the key.
   - ✅ No: World 2, X vouches for Y. Then check who vouches (the client or an external issuer), and which setup lets it speak for `sub`.

### F. OBO in Detail

#### What Is the Same, What Is Different

**The same:**
- ✅ **Goal:** Both let a middle-tier API take a user's token and get a new token to call another API as that user.
- ✅ **Pattern:** Both trade an incoming token for a new token with a different audience.

**Different:**
- ❌ **Standard:** OBO reuses the JWT bearer grant's value with Microsoft's own rules, so it is neither plain RFC 7523 nor RFC 8693. Its assertion is a token issued to the middle tier, but RFC 7523 says a token endpoint must reject a JWT whose audience is not the token endpoint itself ([RFC 7523, section 3](https://www.rfc-editor.org/rfc/rfc7523.html#section-3); see also [Two values that look alike](authentication.md#1-how-the-harness-authenticates)).
- ❌ **Parameters:** OBO sends the incoming token in `assertion` and adds `requested_token_use=on_behalf_of` ([source](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-on-behalf-of-flow)). RFC 8693 uses `subject_token`, `subject_token_type`, `actor_token` and `requested_token_type`. So a client written for RFC 8693 does not work against Entra's OBO without changes.
- ❌ **Actor:** RFC 8693 can record "A acts for B", with `actor_token` in the request and the `act` claim in the new token. OBO has neither. Its new token names the user as the subject and the middle-tier app as the client (`azp`). For agents, Entra adds its own actor facts in `xms_act_fct` ([Agent tokens](tokens.md#what-kind-of-caller-vs-what-it-may-do)).

#### Why OBO Is Not RFC 8693: the History

Microsoft did not break away from a standard: when it built OBO, no token exchange standard existed.

1. **The need came first:** Azure AD customers built multi-tier apps, where a web API had to call another API as the signed-in user.
2. **Microsoft used the closest standard:** The JWT bearer grant (RFC 7523, published in 2015) already meant "here is a JWT, give me a token". It had no way to say "this is a delegation request", so Microsoft added `requested_token_use=on_behalf_of`. That extra parameter is allowed: OAuth 2.0 says a token endpoint must ignore parameters it does not know ([RFC 6749, section 3.2](https://www.rfc-editor.org/rfc/rfc6749.html#section-3.2)). But OBO also bends one RFC 7523 rule: its assertion's audience is the middle tier, not the token endpoint (see **Standard** above).
3. **The standard came later:** RFC 8693 was published in January 2020. By then, OBO had been in production for years.
4. **Microsoft kept OBO:** Microsoft has published no reason. The likely one is backward compatibility: many apps and Microsoft's own libraries depend on OBO, and a second flow for the same job would add little.

#### Microsoft's Wording

Microsoft's own wording adds to the confusion. It calls request 2 of the agent flow a "token exchange request", and T1's audience is `api://AzureADTokenExchange`. Neither is RFC 8693: request 2 uses `client_credentials`, or the JWT bearer grant for OBO, with T1 as a client assertion ([Clarifying Misconceptions](agent-id.md#clarifying-misconceptions)). So when a source lists "token exchange" as a JWT bearer use case, it means vendor flows like OBO, not the RFC 8693 standard.

### Sources

Checked 29 October 2026:

- [RFC 6749: The OAuth 2.0 Authorization Framework](https://www.rfc-editor.org/rfc/rfc6749.html), section 1.3 (the authorization grant is a credential), section 1.3.4 (client credentials as a grant), section 2.3.1 (client secrets: HTTP Basic, and the body form "NOT RECOMMENDED"), section 3.2 (unknown parameters are ignored), section 4.1.3 (`client_id` when the client does not authenticate) and section 5.2 (the error response)
- [RFC 7521: Assertion Framework for OAuth 2.0](https://www.rfc-editor.org/rfc/rfc7521.html), section 3 (an assertion created by a third party, and a self-issued assertion), section 4.1 (client authentication is optional for an assertion grant) and section 4.2 (with a client assertion, `client_id` is optional and must identify the same client)
- [RFC 7522: SAML 2.0 Profile for OAuth 2.0 Client Authentication and Authorization Grants](https://www.rfc-editor.org/rfc/rfc7522.html), sections 2.1 and 2.2 (the SAML grant and client assertion values)
- [RFC 7523: JWT Profile for OAuth 2.0 Client Authentication and Authorization Grants](https://www.rfc-editor.org/rfc/rfc7523.html), section 2.1 (the grant), section 2.2 (client authentication), section 3 (the claim rules) and section 3.1 (client authentication for the grant is a server policy)
- [RFC 8693: OAuth 2.0 Token Exchange](https://www.rfc-editor.org/rfc/rfc8693.html), section 2.1 (the request), section 2.2.1 (the response) and section 4.1 (the `act` claim)
- [RFC 8705: OAuth 2.0 Mutual-TLS Client Authentication](https://www.rfc-editor.org/rfc/rfc8705.html) (`tls_client_auth` and `self_signed_tls_client_auth`)
- [OpenID Connect Core 1.0](https://openid.net/specs/openid-connect-core-1_0.html#ClientAuthentication), section 9 (the client authentication methods; `private_key_jwt`: `iss` and `sub` are the `client_id`)
- [Microsoft identity platform and OAuth 2.0 On-Behalf-Of flow](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-on-behalf-of-flow)
- [OAuth 2.0 client credentials flow on the Microsoft identity platform](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-client-creds-grant-flow) (`client_id` is required, also with a certificate or a federated credential)
- [Microsoft identity platform certificate credentials](https://learn.microsoft.com/en-us/entra/identity-platform/certificate-credentials) (the assertion: `iss` and `sub` are the client ID, `aud` is the token endpoint)
- [Create a trust relationship between an app and an external identity provider](https://learn.microsoft.com/en-us/entra/workload-id/workload-identity-federation-create-trust) (issuer and subject must match exactly and be unique on the app; the recommended audience is `api://AzureADTokenExchange`)
- [Configure an application to trust a managed identity](https://learn.microsoft.com/en-us/entra/workload-id/workload-identity-federation-config-app-trust-managed-identity) (the managed identity token's audience)
- [Agent OAuth flows: On-behalf-of flow](https://learn.microsoft.com/entra/agent-id/agent-on-behalf-of-oauth-flow)
- [Agent autonomous app OAuth flow](https://learn.microsoft.com/entra/agent-id/agent-autonomous-app-oauth-flow)
- [Workload Identity Federation](https://platform.claude.com/docs/en/manage-claude/workload-identity-federation) and [Use WIF with Microsoft Entra ID](https://platform.claude.com/docs/en/manage-claude/wif-providers/azure) (Anthropic: the `jwt-bearer` grant, the federation rule and its parameters)
- [Using OAuth 2.0 for Server to Server Applications](https://developers.google.com/identity/protocols/oauth2/service-account) (Google: the service account JWT, with `sub` only for domain-wide delegation)
- [About security hardening with OpenID Connect](https://docs.github.com/en/actions/security-for-github-actions/security-hardening-your-deployments/about-security-hardening-with-openid-connect) (GitHub Actions: the token's `iss` and `sub`)
- [OAuth 2.0 JWT Bearer Flow for Server-to-Server Integration](https://help.salesforce.com/s/articleView?id=xcloud.remoteaccess_oauth_jwt_flow.htm&type=5) (Salesforce: `iss` is the app's client ID, `sub` the username). The page did not load for this check. The claim values match several integration guides.
