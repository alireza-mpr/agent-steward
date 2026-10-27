# Microsoft Entra Reference

This guide serves as your roadmap to Microsoft Entra for this project. It covers essential links, how to navigate the portal without getting lost, a breakdown of the licensing model, and the exact state of this project's tenant. 

*(For details on Bicep, ARM, and how we deploy tenant objects as code, please see [infra.md](infra.md)).*

**Note on accuracy:** These details were checked against Microsoft Learn on **22 October 2026**. Because Microsoft's licensing changes frequently, always verify with official sources before making purchasing decisions.

---

## 1. Essential Links

Keep these handy. They are the primary tools and references you'll need.

### Portals
| Portal | URL | What we use it for |
| :--- | :--- | :--- |
| **Entra Admin Center** | [entra.microsoft.com](https://entra.microsoft.com/) | Managing identity: users, apps, agents, Conditional Access, and sign-in logs. |
| **Microsoft 365 Admin Center** | [admin.microsoft.com](https://admin.microsoft.com/) | Managing licenses, billing, and trials. |
| **Azure Portal** | [portal.azure.com](https://portal.azure.com/) | Managing Azure subscriptions and resources (like Key Vault). |

### Tools
| Tool | URL | What we use it for |
| :--- | :--- | :--- |
| **Graph Explorer** | [developer.microsoft.com/graph/graph-explorer](https://developer.microsoft.com/graph/graph-explorer) | Testing Microsoft Graph API calls directly in the browser. |
| **jwt.ms** | [jwt.ms](https://jwt.ms/) | Decoding Entra tokens (Microsoft's official token decoder). |

### Official Documentation
* [Microsoft Entra](https://learn.microsoft.com/en-us/entra/)
* [Microsoft Entra Agent ID](https://learn.microsoft.com/en-us/entra/agent-id/)
* [Microsoft Graph](https://learn.microsoft.com/en-us/graph/) & [Graph PowerShell](https://learn.microsoft.com/en-us/powershell/microsoftgraph/)
* [Bicep Templates for Graph](https://learn.microsoft.com/en-us/graph/templates/bicep/overview-bicep-templates-for-graph)
* [Microsoft Entra Licensing Fundamentals](https://learn.microsoft.com/en-us/entra/fundamentals/licensing)

---

## 2. Finding Things in the Portal (Without Getting Lost)

Entra's naming conventions can be tricky. Here is how to make sense of the core objects.

### Applications vs. Service Principals

Understanding the difference between these two is the single most important concept in Entra. 

* **The Application (The Template):** Found under **App registrations**. This is the global definition or "wish list" of your app. It lives only in its home tenant. It declares what the app needs (permissions on other APIs) and what it offers (roles/scopes). *Crucially, declaring something here grants nothing.*
* **The Service Principal (The Reality):** Found under **Enterprise apps**. This is the local representation of the application in *your* specific tenant. It is where your tenant ticks the boxes: which requested permissions are actually granted, who holds the roles, whether the app is enabled, Conditional Access targets, and sign-in logs.

**A helpful analogy:** Think of the Application as an ID badge definition, and the Service Principal as the physical security system in a specific building that decides which doors that badge can actually open. Neither object contains actual code.

| Feature | Application (App registrations) | Service Principal (Enterprise apps) |
| :--- | :--- | :--- |
| **Quantity** | One (in its home tenant) | At most one per tenant |
| **Permissions** | Requests them | Holds the actual grants in this tenant |
| **APIs** | Defines scopes and app roles offered | Records who was actually granted them |
| **Other Data** | Name, credentials, redirect URIs | Enabled/disabled status, owners, logs, CA policies |

*(Tip: In the portal, "Enterprise application" is just Microsoft's UI name for a Service Principal. By default, the portal hides Microsoft's own apps. To see them, change the Application Type filter from "Enterprise Applications" to "All Applications" or "Microsoft Applications").*

### Client, API, or Both?

Unlike IdentityServer (which uses strict `Client` or `ApiResource` configs), Entra uses one unified model. The object type doesn't dictate its role; its configuration and behavior do.

* **As an API (Resource):** It defines what callers can be granted. Tokens are issued *for* it. Its identifier goes into the `aud` claim (e.g., `agent-steward-identity-api`).
* **As a Client:** It asks Entra for tokens to call APIs. It usually holds a credential, but public clients (like Microsoft Graph Command Line Tools) or Agent Identities (which have no credential of their own) are also clients.
* **As Both:** A middle-tier service acts as an API when receiving a token from a frontend, and a client when requesting a new token to call a downstream API (the On-Behalf-Of flow).

No matter the role, every participant has a Service Principal in the tenant to handle grants and logs. 

---

## 3. Where to Look: A Cheat Sheet

### When viewing an Application (App registrations)
* **App roles (Incoming):** The application permissions it *offers* as an API. They are defined solely here.
* **Expose an API (Incoming):** The delegated scopes it offers, plus its Application ID URI.
* **API permissions (Outgoing):** The permissions it *requests* on other APIs (its wish list). You will also see "Other permissions granted for [tenant]" here, which shows grants made directly to the service principal that weren't on the wish list.

### When viewing a Service Principal (Enterprise apps)
* **Properties:** Shows the Object ID (unique to this tenant) and the Application ID (the `appId`, which is globally the same).
* **Security > Permissions (Outgoing):** The permissions this app was *actually granted* on other APIs. Shows both Admin consent (org-wide) and User consent (single users).
* **Manage > Users and groups (Incoming):** Shows who holds this app's roles (users, groups, or other service principals). 

**Real-World Examples in our Tenant:**
* In our tenant, looking at `agent-steward-identity-api`, you will see the agent identity `agent-steward-guardian-01` listed because it holds the `Findings.Submit` role.
* Microsoft Graph lists `agent-steward-guardian-01` four times (once for each Graph permission).
* *Note on Microsoft Graph:* In the portal, its Service Principal is named **GraphAggregatorService**. You can identify it by its universal Application ID: `00000003-0000-0000-c000-000000000000`.

**Who can hold roles?**
Assignments can be user-to-app or app-to-app. Agent identities are Service Principals, so they can hold roles. The role's `allowedMemberTypes` decides what is permitted (e.g., `Application` for service principals). Keep in mind: Service principals cannot grant roles to themselves unless they have `AppRoleAssignment.ReadWrite.All` (which is intentionally blocked for agent identities).

---

## 4. Finding Agent ID Objects

Agent ID objects hide in different places. **You will not find them in the standard "Enterprise apps" menu.** (Even searching `agent-steward` under All Applications returns 0 results).

| Object | Where to find it in the Entra Admin Center |
| :--- | :--- |
| **Blueprint Principal** | **Entra ID > Agents > Agent blueprints**. Each row is a principal showing its Name, Object ID, and Status. |
| **Blueprint (Application)** | On the same page, look for the **Blueprint Application ID** column (add it if hidden). Click a row, and the **Developer settings** (Credentials/Manifest) belong to the blueprint. |
| **Agent Identities (Child)**| Click a blueprint row, then go to **Linked agent identities**. |
| **Agent Identities (All)** | **Entra ID > Agents > Agent identities**. |

**Logs and Management:** 
On the blueprint management page, logs are split. "Agent blueprint principal activity" holds both audit and sign-in logs. "Agent blueprint activity" holds only audit logs. Hitting **Disable** on the overview page disables the principal, completely blocking all of its linked agent identities from signing in.

---

## 5. Demystifying Entra Licensing

Microsoft sells cloud products in **bundles**, priced per user, per month. A license only does something once it is **assigned** to a specific user. Keep in mind that having a license (which grants features) and having an Admin Role (which grants management permissions) are entirely separate things. 

### The Hierarchy
1. **Tenant:** Your organization's instance (e.g., `name.onmicrosoft.com`).
2. **Bundle:** The package you buy (e.g., Microsoft 365 E5).
3. **Entra ID Edition:** The feature tier of Entra inside your bundle (Free, P1, P2).
4. **Add-on:** Extras bought on top (e.g., Microsoft Agent 365).
5. **Assignment:** Giving the license to a user.

### Who actually needs a license?
* **Human Users:** Yes.
* **Service Principals & Managed Identities:** No. 
* **Agent Identities:** No. Agent ID architecture is free for all Entra customers. (However, the *Agent 365* features are licensed per human user). 

### Entra ID Editions Overview
* **Free:** Basic directory, SSO, app registrations, MFA via Security Defaults. Logs are kept for **7 days**.
* **P1:** Adds Conditional Access (CA) and custom admin roles. Logs are kept for **30 days**.
* **P2:** Adds Risk-based Conditional Access (Identity Protection), PIM, and access reviews. Logs kept for **30 days**.

### Bundles (Ranked by Entra ID Edition)
*Lowest to Highest Entra features:*
1. **Office 365 E5** (Entra Free) - Office apps, Exchange, Teams, basic security.
2. **Microsoft 365 Business Premium / M365 E3** (Entra P1) - Adds Intune, Windows Enterprise.
3. **Microsoft 365 E5** (Entra P2) - Adds Mobility + Security E5.
4. **Microsoft 365 E7** (Entra P2) - Includes Entra Suite and Microsoft Agent 365.

---

## 6. Agent ID Licensing & Trials

### What you need for Agent ID features:
* **Basic Blueprints & Agent Identities:** Free on any Entra tenant.
* **Conditional Access for Agents:** Requires M365 E7 **OR** (Agent 365 + Entra P1/M365 E3).
* **Risk-Based Conditional Access / ID Protection:** Requires Entra P2 (Microsoft states Agent 365 will soon be required for ID Protection as well).

> **CRITICAL LIMITATION:** Conditional Access **does not apply** while Entra "Security Defaults" are turned on. New tenants have this on by default.

### How Trials Work
Trials give you 25 licenses for one month. By default, they auto-renew into paid subscriptions. To prevent this, go to **Microsoft 365 admin center > Billing > Your products** and turn off recurring billing. The trial will stay active until its end date.

---

## 7. This Project's Tenant State

Here is exactly what is running in our current environment (as of late October 2026):

* **Active Bundles:** Office 365 E5, Microsoft 365 E5, and Microsoft Agent 365 (All are currently active **trials**).
* **Entra ID Edition:** **P2** (derived from the M365 E5 trial, confirmed on the overview page).
* **Assignments:** M365 E5 and Agent 365 are both assigned to the setup admin.
* **Billing:** Recurring billing is successfully turned **OFF** for all three trials.
* **Trial Expirations:** 
  * Microsoft 365 trials end on **21 November 2026**. 
  * Agent 365 trial ends on **22 November 2026** (Started Oct 22, runs one month). 
  * *Note: All screenshots for DEMO.md must be captured before these dates.*

### Project Requirements Mapping

| Requirement | What it needs | Do we have it? |
| :--- | :--- | :--- |
| **FR1-FR5, FR11:** Agent identities, token flows, disable | Agent ID (Any tenant) | ✅ Yes |
| **FR9:** Conditional Access for agents | Agent 365 + Entra P1 (or higher) | ✅ Yes (Agent 365 Trial + P2). *Note: Security Defaults must be turned off before Phase 5 demo.* |
| **NFR4:** Sign-in logs for correlation demo | Logs kept > 7 days | ✅ Yes (P2 gives us 30 days) |
| **Optional:** Risk-based Conditional Access for agents | Agent 365 + Entra P2 | ✅ Yes |

---

## 8. Sources

*Links checked on 22 October 2026.*

* [Microsoft Entra licensing](https://learn.microsoft.com/en-us/entra/fundamentals/licensing)
* [Conditional Access for agents](https://learn.microsoft.com/en-us/entra/identity/conditional-access/agent-id)
* [Microsoft Entra data retention](https://learn.microsoft.com/en-us/entra/identity/monitoring-health/reference-reports-data-retention)
* [What is Microsoft Entra Agent ID?](https://learn.microsoft.com/en-us/entra/agent-id/what-is-microsoft-entra-agent-id)
* [Try or buy a Microsoft 365 subscription](https://learn.microsoft.com/en-us/microsoft-365/commerce/try-or-buy-microsoft-365)
* Microsoft 365 admin center, Marketplace page for Microsoft 365 E5.