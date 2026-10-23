# Microsoft Entra reference

Links, the licensing model, and what this project's tenant has. For Bicep, ARM and how tenant objects are deployed as code, see [infra.md](infra.md).

Checked against Microsoft Learn on 22 October 2026. Licensing changes often, so check the sources before relying on it.

## Contents

- [Links](#links)
  - [Portals](#portals)
  - [Tools](#tools)
  - [Documentation](#documentation)
- [Finding things in the portal](#finding-things-in-the-portal)
  - [Applications vs service principals](#applications-vs-service-principals)
  - [What to look at in a service principal](#what-to-look-at-in-a-service-principal)
- [What a license does](#what-a-license-does)
- [The model](#the-model)
  - [Reading a plan name](#reading-a-plan-name)
  - [Licenses and admin roles are separate](#licenses-and-admin-roles-are-separate)
  - [Which identities use a license](#which-identities-use-a-license)
- [Entra ID editions](#entra-id-editions)
- [Bundles, ordered by Entra ID edition](#bundles-ordered-by-entra-id-edition)
- [Agent ID licensing](#agent-id-licensing)
- [Trials](#trials)
- [This project's tenant](#this-projects-tenant)
- [Sources](#sources)

## Links

### Portals

| Portal | Link | Used for |
|---|---|---|
| Microsoft Entra admin center | https://entra.microsoft.com/ | Identity: users, apps, agents, Conditional Access, sign-in logs |
| Microsoft 365 admin center | https://admin.microsoft.com/ | Licenses, billing, trials |
| Azure portal | https://portal.azure.com/ | Azure subscriptions and resources, such as Key Vault |

### Tools

| Tool | Link | Used for |
|---|---|---|
| Graph Explorer | https://developer.microsoft.com/graph/graph-explorer | Trying Microsoft Graph calls in the browser |
| jwt.ms | https://jwt.ms/ | Decoding Entra tokens (Microsoft's token decoder) |

### Documentation

| Topic | Link |
|---|---|
| Microsoft Entra | https://learn.microsoft.com/en-us/entra/ |
| Microsoft Entra Agent ID | https://learn.microsoft.com/en-us/entra/agent-id/ |
| Microsoft Graph | https://learn.microsoft.com/en-us/graph/ |
| Microsoft Graph PowerShell | https://learn.microsoft.com/en-us/powershell/microsoftgraph/ |
| Bicep templates for Microsoft Graph | https://learn.microsoft.com/en-us/graph/templates/bicep/overview-bicep-templates-for-graph |
| Microsoft Entra licensing | https://learn.microsoft.com/en-us/entra/fundamentals/licensing |

## Finding things in the portal

### Applications vs service principals

| You want | Entra admin center page | Notes |
|---|---|---|
| Applications (the global definition of an app) | Entra ID > App registrations | Lists only apps registered in this tenant. Microsoft's own apps never appear here; they are registered in Microsoft's tenant. |
| Service principals (the app's local account in this tenant) | Entra ID > Enterprise apps | **"Enterprise application" is the portal's name for a service principal.** The default filter (Application type = Enterprise Applications) hides Microsoft's apps. Change it to Microsoft Applications or All Applications to see them. |

**What each one holds.** The application declares; the service principal holds this tenant's decisions. Neither contains code: they are identity records, like a badge and the list of doors it opens, not the person who carries it.

| | Application | Service principal |
|---|---|---|
| Permissions | Requests them | Holds the ones granted in this tenant |
| If it is an API | Defines the permissions it offers (scopes, app roles) | Records who was granted them |
| Other | Name, credentials, redirect URIs | Enabled or disabled, owners, who may use it, Conditional Access target, sign-in logs |
| How many | One, in its home tenant | At most one per tenant |

### What to look at in a service principal

| Tab | Shows |
|---|---|
| Properties | The Object ID (this service principal, unique to this tenant) and the Application ID (the `appId`, the same in every tenant) |
| Permissions | Which permissions were consented, by an admin or by users |
| Sign-in logs | When the app signed in to this tenant |

Example: the "Microsoft Graph Command Line Tools" service principal is created when a user first consents in `Connect-MgGraph`. Its Permissions tab shows the scopes that the user consented to.

## What a license does

- Microsoft sells its cloud products as **bundles**, priced per user per month.
- The bundles a tenant has decide which features the tenant can use.
- A license helps a user only after it is **assigned** to that user.

## The model

| Layer | What it is | Examples |
|---|---|---|
| Tenant | One organization's instance of Entra ID. Licenses belong to a tenant. | `<name>.onmicrosoft.com` |
| Bundle | A package of products, sold per user per month | Office 365 E5, Microsoft 365 E5 |
| Entra ID edition | The level of Entra ID features inside a bundle | Free, P1, P2 |
| Add-on | A product bought on top of a bundle | Microsoft Agent 365 |
| Assignment | One license given to one user | The admin account gets Microsoft 365 E5 |

### Reading a plan name

| Part | Meaning |
|---|---|
| E | Enterprise: no user limit |
| Business (Basic, Standard, Premium) | Small organizations: up to 300 users |
| F / A / G | Frontline workers / education / government |
| Number (E3, E5, E7) | The tier. A higher number includes more. |
| "Office 365" vs "Microsoft 365" | Office 365 is apps and collaboration only. Microsoft 365 adds Entra ID, Intune and Windows. |

### Licenses and admin roles are separate

| | License | Admin role |
|---|---|---|
| Answers | Which features and services can this user use? | What can this user manage in the tenant? |
| Example | Microsoft 365 E5 | Global Administrator |
| Where it is given | Microsoft 365 admin center | Entra admin center > Roles & admins |

A user can have a license and no admin role, or an admin role and no license.

### Which identities use a license

| Identity | Uses a Microsoft 365 E5 license? |
|---|---|
| Human user, admin or not | Yes, when the user needs the bundle's features |
| Application / service principal | No |
| Managed identity | No. Managed identities have no licensing requirements. |
| Agent identity | No. Agent ID is available to all Entra customers. Agent 365 is licensed separately, per user. |
| Agent's user account (optional; not used in this project) | Only when it needs Microsoft 365 services, such as a mailbox |

Some advanced features for service principals, such as Conditional Access for workload identities, need Microsoft Entra Workload ID, licensed per workload identity. This project does not need it.

## Entra ID editions

"P" stands for Premium.

| Edition | Adds | Features this project cares about |
|---|---|---|
| Free | The basics | Directory, single sign-on, app registrations, built-in admin roles, MFA through Security Defaults. Audit and sign-in logs kept 7 days. |
| P1 | Policy | Conditional Access. Audit and sign-in logs kept 30 days. Custom admin roles. |
| P2 | Risk and privileged access, plus everything in P1 | Identity Protection (risk-based Conditional Access), Privileged Identity Management, access reviews. |

## Bundles, ordered by Entra ID edition

**Office 365 E5 < Microsoft 365 E3 / Business Premium < Microsoft 365 E5 < Microsoft 365 E7**

| Bundle | Entra ID edition | Also includes |
|---|---|---|
| Office 365 E5 | Free | Office apps, Exchange, SharePoint, Teams, and security and compliance for them |
| Microsoft 365 E3 | P1 | Office apps and services, Intune, Windows Enterprise |
| Microsoft 365 Business Premium | P1 | Business apps and security, up to 300 users |
| Microsoft 365 E5 | P2 | Office 365 E5, Enterprise Mobility + Security E5, Windows Enterprise E5 |
| Microsoft 365 E7 | P2 | Microsoft Entra Suite and Microsoft Agent 365 |

This order is about Entra ID only. For Office and compliance features, Office 365 E5 is above Microsoft 365 E3.

## Agent ID licensing

| Capability | What is needed |
|---|---|
| Agent ID: blueprints and agent identities | Any Entra tenant. "Agent ID is available for all Microsoft Entra customers." |
| Conditional Access for agents | Microsoft 365 E7, **or** Microsoft Agent 365 plus at least Entra ID P1 or Microsoft 365 E3 |
| Risk-based Conditional Access (for example, agent risk) | Also Entra ID P2 |
| ID Protection for agents | Entra ID P2. Microsoft says it will soon also require Agent 365. |
| Microsoft Agent 365 | Included in Microsoft 365 E7. An add-on for Microsoft 365 E5, A5 and Business Premium. Licensed per user. |

One limit that no license removes: **Conditional Access does not apply while Security Defaults are on.** New tenants have Security Defaults on.

## Trials

- A trial gives 25 licenses for one month.
- By default, a trial turns into a paid subscription when it ends. To stop this, turn off recurring billing in the Microsoft 365 admin center under **Billing > Your products**.
- After recurring billing is off, the trial keeps working until its end date. Then it expires.

## This project's tenant

| Item | Value |
|---|---|
| Bundles | Office 365 E5, Microsoft 365 E5 and Microsoft Agent 365 (all trials) |
| Entra ID edition | P2, from Microsoft 365 E5 (confirmed on the Entra admin center Overview page) |
| Licenses assigned | Microsoft 365 E5 and Agent 365, both to the setup admin |
| Recurring billing | Off for all three trials |
| Trials end | 21 November 2026 for the Microsoft 365 trials. The Agent 365 trial started on 22 October 2026 and runs one month. Screenshots for DEMO.md must be captured before the trials end. |

| Requirement | Needs | In this tenant |
|---|---|---|
| FR1-FR5, FR11: agent identities, token flows, disable | Agent ID (any tenant) | Available |
| FR9: Conditional Access for agents | Agent 365 plus Entra ID P1, or Microsoft 365 E7 | Available: Agent 365 (trial) plus Entra ID P2. Security Defaults must be turned off before the demo (Phase 5). |
| NFR4: sign-in logs for the correlation demo | Logs kept long enough | 30 days (P2) |
| Optional: risk-based Conditional Access for agents | Agent 365 plus Entra ID P2 | Available |

## Sources

Checked 22 October 2026.

- [Microsoft Entra licensing](https://learn.microsoft.com/en-us/entra/fundamentals/licensing) - editions, bundles, Agent ID, Conditional Access for agents
- [Conditional Access for agents](https://learn.microsoft.com/en-us/entra/identity/conditional-access/agent-id) - licensing, Security Defaults limit
- [Microsoft Entra data retention](https://learn.microsoft.com/en-us/entra/identity/monitoring-health/reference-reports-data-retention) - log retention per edition
- [What is Microsoft Entra Agent ID?](https://learn.microsoft.com/en-us/entra/agent-id/what-is-microsoft-entra-agent-id) - Agent ID availability, Agent 365
- [Try or buy a Microsoft 365 subscription](https://learn.microsoft.com/en-us/microsoft-365/commerce/try-or-buy-microsoft-365) - trials and recurring billing
- Microsoft 365 admin center, Marketplace page for Microsoft 365 E5 - bundle contents
