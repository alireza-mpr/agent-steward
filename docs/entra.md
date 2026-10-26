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
  - [Client, API, or both](#client-api-or-both)
  - [What to look at in an application](#what-to-look-at-in-an-application)
  - [What to look at in a service principal](#what-to-look-at-in-a-service-principal)
  - [Agent ID objects](#agent-id-objects)
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
| Service principals (the app's local representation in this tenant) | Entra ID > Enterprise apps | **"Enterprise application" is the portal's name for a service principal.** The default filter (Application type = Enterprise Applications) hides Microsoft's apps. Change it to Microsoft Applications or All Applications to see them. |

**What each one holds.** The application declares; the service principal holds this tenant's decisions. Neither contains code: they are identity records, like a badge and the list of doors it opens, not the person who carries it.

| | Application | Service principal |
|---|---|---|
| Permissions | Requests them | Holds the ones granted in this tenant |
| If it is an API | Defines the permissions it offers (scopes, app roles) | Records who was granted them |
| Other | Name, credentials, redirect URIs | Enabled or disabled, owners, who may use it, Conditional Access target, sign-in logs |
| How many | One, in its home tenant | At most one per tenant |

### Client, API, or both

**In one line:** in Entra, the object type does not say which role an app plays. Its configuration, and its part in each token request, does.

IdentityServer splits the roles into separate config types: a `Client` calls APIs, and an `ApiResource` or `ApiScope` describes an API. Entra uses one model for both. Every app is an application with a service principal in each tenant, and the same application can play either role, or both:

- **As an API (resource):** It defines what callers can be granted: app roles (application permissions) and scopes (delegated permissions). Tokens are issued *for* it, and in this role it only receives and validates them. Its identifier goes into `aud`: its Application ID URI if it has one, or its client ID in v2 tokens. Examples: Microsoft Graph, and this project's `agent-steward-identity-api`, which has no URI, so its `aud` is its client ID.
- **As a client:** It asks Entra for tokens and calls APIs with them. It usually lists the permissions it wants under "API permissions" and holds a credential, but not always. A public client like Microsoft Graph Command Line Tools holds no secret and uses PKCE. An agent identity has no app registration and no credential of its own. Examples: Microsoft Graph Command Line Tools, and the guardian's agent identity.
- **As both:** A middle-tier service is an API when it receives a token from a front end, and a client when it asks for a new token to call a downstream API (the on-behalf-of flow).

Every participant has a service principal in the tenant, whichever way the tokens flow. It is the object that grants and sign-in logs attach to, and for some app types Conditional Access policies too. So "Enterprise app" names the object, not the role: Microsoft Graph Command Line Tools (a client) and Microsoft Graph (an API) both appear there.

### What to look at in an application

| Page (App registrations > the app) | Shows | Direction |
|---|---|---|
| App roles | The application permissions it offers, as an API. They are defined and edited only here. Enterprise apps cannot change them. | Incoming |
| Expose an API | The delegated scopes it offers, and its Application ID URI if it has one | Incoming |
| API permissions | The permissions it requests on other APIs, as a client. It is only a wish list until someone consents. | Outgoing |

### What to look at in a service principal

| Tab (Enterprise apps > the app) | Shows |
|---|---|
| Properties | The Object ID (this service principal, unique to this tenant) and the Application ID (the `appId`, the same in every tenant) |
| Security > Permissions | The permissions this app **was granted** on other APIs, as a client (outgoing). **Admin consent:** grants for the whole organization, either application permissions or delegated permissions for all users. **User consent:** delegated permissions that single users granted for themselves. |
| Manage > Users and groups | Who holds this app's roles, as an API (incoming): users, groups, and also service principals. It shows one row per role assignment, so a principal with four roles appears four times. Seen in this tenant on 26 October 2026: `agent-steward-identity-api` lists the agent identity `agent-steward-guardian-01`, which holds `Findings.Submit`, and Microsoft Graph lists it four times, once per Graph permission ([screenshot](screenshots/phase2-guardian-graph-permissions.png)). In Graph: `GET /servicePrincipals/{id}/appRoleAssignedTo`. |
| Sign-in logs | When the app signed in to this tenant |

**Microsoft Graph's name in the portal.** Enterprise apps shows Microsoft Graph's service principal as **GraphAggregatorService**, while Graph itself calls it "Microsoft Graph". Its Application ID tells you which one it is: `00000003-0000-0000-c000-000000000000`, the same in every tenant.

**Who can hold an app's roles.** An app role assignment links one principal to one role of an API's service principal. The principal can be a user, a group, or another service principal, including an agent identity. So assignments run user-to-app and app-to-app alike. Each role sets which kinds it accepts (`allowedMemberTypes`): `Application` for service principals, and `User` for users and groups. This project's `Findings.Submit` accepts only service principals.

Service principals do not grant roles by themselves. An admin creates the assignment: in the portal, through admin consent, or through Graph (like this project's Bicep). Or an app that holds `AppRoleAssignment.ReadWrite.All` creates it. That permission is blocked for agent identities, so an agent can never grant roles.

Example: the "Microsoft Graph Command Line Tools" service principal is created when a user first consents in `Connect-MgGraph`. Its Permissions tab shows, under **User consent**, the scopes that the user consented to for themselves. An admin who ticks "Consent on behalf of your organization" creates admin consent instead.

### Agent ID objects

The Agent blueprints page lists **blueprint principals**, not blueprints. Opening one row shows a single management page for both the principal and its blueprint.

| You want | Entra admin center page |
|---|---|
| Blueprint principal (the blueprint's service principal in this tenant) | Entra ID > Agents > Agent blueprints. Each row is a principal: its Name, Object ID and Status. |
| Blueprint (the application) | Same list: the **Blueprint Application ID** column holds its `appId` (add the column with Choose columns if it is hidden). Open a row: **Developer settings > Credentials** and **Developer settings > Manifest** belong to the blueprint. |
| Agent identities of one blueprint | Open a row > **Linked agent identities** |
| All agent identities | Entra ID > Agents > Agent identities |

**Enterprise apps does not list them.** Blueprint principals and agent identities are service principals, but only the Agents pages and Graph show them. Seen in this tenant on 23 October 2026: Enterprise apps > All applications, search `agent-steward`, 0 results. Microsoft Learn documents an Application type value "Agent ID (Preview)" that shows agent identities, but this tenant's filter offers only Enterprise Applications, Microsoft Applications, Managed Identities and All Applications.

The logs on the management page are split by object. "Agent blueprint principal activity" has audit logs and sign-in logs. "Agent blueprint activity" has audit logs only. **Disable** on the overview page disables the principal, and then none of its agent identities can sign in.

Source: [Manage agent identity blueprints in the Microsoft Entra admin center](https://learn.microsoft.com/en-us/entra/agent-id/manage-agent-blueprint) (Microsoft Learn, checked 23 October 2026).

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
