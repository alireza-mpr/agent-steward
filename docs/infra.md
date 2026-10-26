# Infrastructure as code

How this project creates tenant objects as code: Bicep, Azure Resource Manager (ARM) and Microsoft Graph.

## Contents

- [Why](#why)
- [The layers](#the-layers)
  - [Three ways to create the same object](#three-ways-to-create-the-same-object)
  - [What ARM adds over plain Graph calls](#what-arm-adds-over-plain-graph-calls)
- [Identity and permissions of a deploy](#identity-and-permissions-of-a-deploy)
  - [Two separate role systems](#two-separate-role-systems)
  - [Who is the actor](#who-is-the-actor)
- [Where deployments go](#where-deployments-go)
- [Files](#files)
- [Run it](#run-it)
- [Where to check a deploy](#where-to-check-a-deploy)
- [Agent ID objects](#agent-id-objects)
  - [Steps to create a blueprint and its agents](#steps-to-create-a-blueprint-and-its-agents)
  - [The blueprint's credential](#the-blueprints-credential)
  - [Run the Agent ID scripts](#run-the-agent-id-scripts)
- [Cost](#cost)
- [Known issue: Bicep on Apple Silicon](#known-issue-bicep-on-apple-silicon)
- [Not in scope](#not-in-scope)

## Why

Tenant setup must be reproducible (NFR5): the tenant can be rebuilt from the repo. Bicep is used for every Entra object type it supports. Graph PowerShell covers the types Bicep does not support. The Agent ID object types (blueprint, blueprint principal, agent identity) are not in the Graph Bicep extension (`v1.0` and `beta`, version 1.0.0, checked 23 October 2026), so they are created with Graph PowerShell.

## The layers

```
setup/bicep/*.bicep  ->  ARM JSON  ->  Azure Resource Manager  ->  Microsoft Graph  ->  Entra ID
 (what we write)       (compiled)    (deployment engine,         (REST API)           (where the object
                                      + Graph extension)                               is stored)
```

| Layer | What it is | Its job |
|---|---|---|
| Entra ID | The directory: users, groups, apps, agents | Stores the objects |
| Microsoft Graph | The REST API in front of Entra | The door: every program that changes Entra goes through Graph |
| Azure Resource Manager (ARM) | Azure's deployment engine | Reads a template of what should exist, works out the order, calls the right API for each resource, and records each deployment |
| Graph Bicep extension | A plug-in for Bicep and ARM | Adds the `Microsoft.Graph/*` resource types. For these types, ARM calls Graph instead of an Azure service. |
| Bicep | A language that compiles to ARM JSON | A readable way to write the template |

**Bicep files are deployed by Azure Resource Manager.** This is the only reason the project uses Azure: nothing in the project runs in Azure.

### Three ways to create the same object

| Tool | Style | Path to Entra |
|---|---|---|
| Portal | Manual clicks | Portal -> Entra |
| Graph PowerShell (`New-MgGroup`) | Imperative: "create this now" | PowerShell -> Graph -> Entra |
| Bicep | Declarative: "this should exist" | Bicep -> ARM -> Graph -> Entra |

### What ARM adds over plain Graph calls

- **Repeatable:** deploying the same file twice gives the same result. For groups, the client-chosen `uniqueName` key lets ARM find the existing object and update it instead of creating a copy.
- **Ordered:** dependent objects are created in the right order.
- **Recorded:** every deployment is kept with its template, inputs, outputs and result.
- **One file for both worlds:** a template can hold Azure resources (such as a Key Vault) and Entra objects together.

## Identity and permissions of a deploy

### Two separate role systems

Microsoft Learn: "Microsoft Entra role assignments do not grant access to Azure resources, and Azure role assignments do not grant access to Microsoft Entra ID." Entra roles do not leak into Azure RBAC, and Azure roles do not leak into Entra.

| Step of a deploy | Checked by | In this lab |
|---|---|---|
| Start a deployment in the resource group | Azure RBAC | Owner of the lab subscription |
| Create or change the Entra object | The deploying user's Entra role and Graph permissions | Global Administrator |

### Who is the actor

ARM calls Graph on the deployer's behalf, with the deployer's identity, so Graph records the deployer as the actor. The Entra audit log shows `initiatedBy.user` = the signed-in admin, with no app. This is the same idea as the on-behalf-of flow: the downstream API sees the human, not the service in the middle.

The first deploy also made Entra create a service principal for Microsoft's "Microsoft Graph Bicep Extension" app in the tenant, just in time (audit log initiator: "Microsoft Azure AD Internal - Jit Provisioning").

## Where deployments go

- **Lab subscription** in the project tenant, resource group `rg-agent-steward` (Australia East). The resource group only records the deployments. Entra objects do not live in it, and deleting it does not delete them.
- **Alternative without a subscription (not used):** tenant-scope deployment (`targetScope = 'tenant'`, `az deployment tenant create`). It needs a deployment role at the root scope `/`. Custom roles cannot be assigned at `/`, so only broad built-in roles fit.

## Files

| File | Purpose |
|---|---|
| [`setup/bicep/bicepconfig.json`](../setup/bicep/bicepconfig.json) | Loads the Graph extension (`v1.0` types, version `1.0.0`, pinned) under the alias `microsoftGraphV1` |
| [`setup/bicep/phase0-smoke.bicep`](../setup/bicep/phase0-smoke.bicep) | Phase 0 smoke test: one security group. Proves the toolchain works end to end. Safe to delete. |
| [`setup/agent-id/AgentIdHelpers.ps1`](../setup/agent-id/AgentIdHelpers.ps1) | Shared helpers: Graph calls, permission scopes, and one safe-to-rerun `Set-*` function per object type |
| [`setup/agent-id/Deploy-AgentIdentities.ps1`](../setup/agent-id/Deploy-AgentIdentities.ps1) | Creates the project's blueprints, blueprint principals and agent identities, plus a stand-in sponsor user |
| [`setup/agent-id/Deploy-Fixtures.ps1`](../setup/agent-id/Deploy-Fixtures.ps1) | Creates the fixture agents with deliberate governance flaws (FR3) |
| [`setup/agent-id/Deploy-BlueprintCertificate.ps1`](../setup/agent-id/Deploy-BlueprintCertificate.ps1) | Gives the guardian's blueprint a certificate credential: creates the key pair locally and uploads only the public certificate (FR10) |

## Run it

```sh
az login --tenant <your-domain>.onmicrosoft.com
az group create --name rg-agent-steward --location australiaeast
az deployment group create --resource-group rg-agent-steward --template-file setup/bicep/phase0-smoke.bicep
```

Check the result through Graph:

```sh
az rest --method get --url "https://graph.microsoft.com/v1.0/groups(uniqueName='agentStewardPhase0Smoke')"
```

Clean up (the group must be deleted through Graph, not through the resource group):

```sh
az rest --method delete --url "https://graph.microsoft.com/v1.0/groups(uniqueName='agentStewardPhase0Smoke')"
```

## Where to check a deploy

| Record | Where |
|---|---|
| The Entra object | Entra admin center > Entra ID > Groups |
| The directory change and its actor | Entra admin center > Entra ID > Monitoring & health > Audit logs |
| The deployment | Azure portal > Resource groups > `rg-agent-steward` > Deployments |

## Agent ID objects

Bicep cannot create Agent ID objects (see [Why](#why)), so the scripts in `setup/agent-id/` create them with Graph PowerShell. Every script is safe to run again: it looks each object up by name and creates it only if it is missing.

An agent needs three kinds of object, created in this order:

```
1. Blueprint             ->  2. Blueprint principal          ->  3. Agent identities
   (an application)          (its service principal here)        (service principals, many)
```

Creating the blueprint does not create its principal. That is a second call, the same as for a normal application. The portal wizard is the only exception: it creates both.

### Steps to create a blueprint and its agents

| # | Step | Graph call | Delegated permission | When needed | In this project |
|---|---|---|---|---|---|
| 1 | Create the blueprint. A sponsor is required; an owner is optional. | `POST /v1.0/applications` with `@odata.type` = `Microsoft.Graph.AgentIdentityBlueprint` and the header `OData-Version: 4.0` | `AgentIdentityBlueprint.Create` | Always | `Set-Blueprint` |
| 2 | Add a credential. Production: a managed identity as a federated identity credential. Local development: a certificate or a secret. | `POST /v1.0/applications/{id}/federatedIdentityCredentials`, or `.../addPassword`, or for a first certificate `PATCH /v1.0/applications/{id}/microsoft.graph.agentIdentityBlueprint` with `keyCredentials` | `AgentIdentityBlueprint.AddRemoveCreds.All` | Before the blueprint can get tokens | `Deploy-BlueprintCertificate.ps1`, for the guardian; `Set-ExpiringSecret`, for the expiring fixture |
| 3 | Set an identifier URI and a scope | `PATCH /v1.0/applications/{id}` with `identifierUris` and `api.oauth2PermissionScopes` | `AgentIdentityBlueprint.UpdateAuthProperties.All` | Only when users or other agents call the agent (on-behalf-of flow) | Not used |
| 4 | Create the blueprint principal | `POST /v1.0/servicePrincipals/microsoft.graph.agentIdentityBlueprintPrincipal` with the blueprint's `appId` | `AgentIdentityBlueprintPrincipal.Create` | Always | `Set-BlueprintPrincipal` |
| 5 | Create an agent identity | `POST /beta/servicePrincipals/microsoft.graph.agentIdentity` with `agentIdentityBlueprintId` = the blueprint's `appId` | `AgentIdentity.Create.All` | Once per agent | `Set-AgentIdentity` |

**Who may run them.** Agent ID Developer or Agent ID Administrator, or any user when the tenant setting "Users can create agent blueprints" is Yes. Only Agent ID Administrator may add a secret or a certificate. The owner of a blueprint can create its agent identities without an Agent ID role. This lab runs the scripts as Global Administrator, for tenant setup only.

**Portal alternative.** Entra ID > Agents > Agent blueprints > New agent blueprint (Preview). The wizard does steps 1 and 4 only. To see the result in the portal, see [Agent ID objects in the portal](entra.md#agent-id-objects).

**Not in scope.** The Learn how-to ends with one more step: register the blueprint in the Agent 365 registry. Agent 365 governance is a written design only in this project ([DESIGN-NOTES.md](../DESIGN-NOTES.md#3-agent-365-governance)).

Source: [Create an agent identity blueprint](https://learn.microsoft.com/en-us/entra/agent-id/create-blueprint) (Microsoft Learn, checked 23 October 2026).

### The blueprint's credential

The blueprint's credential proves the blueprint when it asks Entra for T1 (see [Who it is vs how it proves it](agent-id.md#who-it-is-vs-how-it-proves-it)). The blueprint is only a record in the directory, so it cannot send that request itself. The program that sends it holds the credential: a hosting platform, a sidecar, or the agent's own code.

**Two layers.** In OAuth, a confidential client must present a client credential as proof that it is the client it claims to be. Two layers are involved:

- **The credential type:** What is registered on the client, here the blueprint. It is a secret, a certificate, or a federated identity credential.
- **The client authentication method:** How the proof is sent in the token request. Entra supports two: `client_secret` and `client_assertion`.

**Which type.**

- **Managed identity, as a federated identity credential:** Microsoft's recommendation for production. Nobody creates or rotates anything, because Azure manages the hidden credential. It works only when the code runs on an Azure service. Sent as a `client_assertion`: the managed identity's token.
- **Certificate (`keyCredentials`):** For local development and testing, or where a managed identity cannot work. Microsoft's [autonomous flow page](https://learn.microsoft.com/en-us/entra/agent-id/agent-autonomous-app-oauth-flow) prefers it over a secret. Sent as a `client_assertion`: a JWT signed with the private key.
- **Secret (`passwordCredentials`):** For local development and testing only. Entra generates the value and shows it only once. Sent as `client_secret`: the secret itself.

This project runs locally, not in Azure. So the guardian's blueprint has a certificate, created by [`Deploy-BlueprintCertificate.ps1`](../setup/agent-id/Deploy-BlueprintCertificate.ps1). The expiring fixture has a secret on purpose.

**How a certificate is set up.** It is not created on the fly. A person or a setup script creates it once, ahead of time:

1. **Make the key pair:** Create a private key and a certificate that holds the matching public key, for example with `openssl`.
2. **Upload the public part:** Add the certificate (the public key only) to the blueprint's `keyCredentials` through Graph. This needs the Agent ID Administrator role and the `AgentIdentityBlueprint.AddRemoveCreds.All` permission.
3. **Hand over the private key:** Give the private key only to the program that sends the T1 request, through environment injection or a mounted file (FR10). It never goes into the repo or to Entra.
4. **Use it many times:** For each T1 request, that program signs a fresh, short-lived JWT (the `client_assertion`) with the private key. So the certificate stays the same, while every proof and every token is new.

**Where each half lives.**

- **Private key:** One key, shared by all agents of the blueprint. It is kept in one store, for example Azure Key Vault, a cloud KMS, or (in this lab) a mounted file. Every program that sends T1 requests reads the same copy from that store. With a sidecar, only the sidecar reads it. A store like Key Vault or a KMS can also keep the key inside and sign for the program, so the key is never copied out.
- **Public key:** The matching certificate, uploaded once to the blueprint in Entra.

The key never says which agent is asking. Each program names its agent with that agent identity's `appId`, which is not a secret.

Of the two types sent as a `client_assertion`, only a certificate has a private key to share. With a managed identity, nothing is copied.

**Lifetime and rotation.**

- **Lifetime:** Chosen when the certificate is created (its end date). A tenant policy can set a maximum. A short-lived lab like this one needs only 30 to 60 days.
- **Rotation:** A blueprint can hold more than one certificate. So add the new one, switch the program to it, then remove the old one, with no downtime.

Source: [Create an agent identity blueprint](https://learn.microsoft.com/en-us/entra/agent-id/create-blueprint), section "Configure credentials for the agent identity blueprint" (Microsoft Learn, checked 26 October 2026).

### Run the Agent ID scripts

The scripts need PowerShell 7 and the `Microsoft.Graph.Authentication` module. Each one signs in with `Connect-MgGraph` and asks only for the delegated permissions it needs.

```sh
pwsh ./setup/agent-id/Deploy-AgentIdentities.ps1 -TenantId <your-domain>.onmicrosoft.com
pwsh ./setup/agent-id/Deploy-Fixtures.ps1 -TenantId <your-domain>.onmicrosoft.com
pwsh ./setup/agent-id/Deploy-BlueprintCertificate.ps1 -TenantId <your-domain>.onmicrosoft.com
```

`Deploy-BlueprintCertificate.ps1` also needs OpenSSL on the PATH. It writes three files to `secrets/`, which git ignores:

- **`agent-steward-guardian.pfx`:** The private key and the certificate, protected by a random password. Only the sidecar mounts it.
- **`agent-steward-guardian.crt`:** The public certificate, the same half that Entra holds. Not a secret.
- **`agent-steward-guardian.sidecar.env`:** The PFX password, as the sidecar's environment variable.

## Cost

| Item | Cost |
|---|---|
| Subscription, resource group, ARM deployments, Bicep | Free. You pay only for Azure resources a deployment creates. |
| Entra objects created by Bicep | Covered by Entra licenses, not by Azure billing |
| Key Vault, if used for FR10 | A$0.0427 per 10,000 operations (Standard, Australia East, October 2026). No monthly fee. |

A A$5 monthly budget alert on the lab subscription warns about any unexpected spend.

## Known issue: Bicep on Apple Silicon

On macOS 26.5.1, the Apple Silicon (arm64) builds of the Bicep CLI (0.48.1 and 0.47.16) crash with `AccessViolationException` in .NET TLS code when they download the Graph extension. Offline compiles work. Fix: install the Intel build, which runs under Rosetta:

```sh
az bicep install --target-platform osx-x64
```

`az bicep upgrade` may switch back to the arm64 build, so pass the target platform again when upgrading.

## Not in scope

Deploying `setup/` from a CI/CD pipeline is recorded in [DESIGN-NOTES.md](../DESIGN-NOTES.md), "Future work".
