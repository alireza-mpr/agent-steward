# Infrastructure as code

How this project creates tenant objects as code: Bicep, Azure Resource Manager (ARM) and Microsoft Graph.

Checked on 23 October 2026.

## Why

Tenant setup must be reproducible (NFR5): the tenant can be rebuilt from the repo. Bicep is used for every Entra object type it supports. Graph PowerShell covers the types Bicep does not support.

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
