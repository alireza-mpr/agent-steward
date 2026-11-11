# Investigating Agent Activity: Who, for Whom, and Why

An agent did something in the tenant: it deleted a site, changed a group, or read a mailbox. This page shows how to find which agent did it, whether it acted for a person or on its own, and why. Each claim is marked as verified (with its source) or not checked.

## Contents

- [1. The High-Level View](#1-the-high-level-view)
- [2. Entra Sign-in Logs: When a Token Was Issued](#2-entra-sign-in-logs-when-a-token-was-issued)
- [3. Microsoft Graph Activity Logs: Every Graph Call](#3-microsoft-graph-activity-logs-every-graph-call)
- [4. Workload Audit Logs: What Happened Inside the Service](#4-workload-audit-logs-what-happened-inside-the-service)
- [5. The Agent's Own Trace: Why It Acted](#5-the-agents-own-trace-why-it-acted)
- [6. Gaps and Limits](#6-gaps-and-limits)
- [7. A Worked Example: The Missing SharePoint Site](#7-a-worked-example-the-missing-sharepoint-site)
- [8. The Guardian Today](#8-the-guardian-today)

---

## 1. The High-Level View

**Three questions.** Every investigation answers the same three:

1. **Who acted?** The agent identity.
2. **For whom?** A signed-in person (delegated), or nobody (autonomous).
3. **Why?** The task, the input and the reasoning that led to the action.

**The rule: the token itself is never logged.** Logs copy identifiers out of the token instead. Microsoft calls them **linkable identifiers**: Entra "embeds specific identifiers in all access tokens that enable the correlation of activities back to a single root authentication event" ([Linkable identifiers](https://learn.microsoft.com/en-us/entra/identity/authentication/how-to-authentication-track-linkable-identifiers), verified). The two that matter most:

- **`uti`:** a unique ID for one token. It traces every action made with that token.
- **`sid`:** a session ID, created when a person signs in interactively. Every token from that session carries it, so it traces a whole session. An autonomous agent has no interactive sign-in.

**The chain of four records.** Each one answers part of the questions, and the linkable identifiers join them:

| Record | Answers | Join key |
|---|---|---|
| Workload audit log (for example SharePoint, in Microsoft Purview) | What happened, and with which token | `UniqueTokenId`, `AADSessionId` |
| Entra sign-in log | Which app (the agent), which user, when the token was issued | `Unique Token Identifier`, `Session ID` |
| Microsoft Graph activity log | Every Graph call made with the token | `SignInActivityId` (the `uti`), `SessionId` |
| The agent's own trace | Why: the task, what it read, its tool calls | A correlation ID that the agent's platform writes |

The first three are Microsoft's. The fourth is yours to build: no Microsoft log records why an agent acted.

---

## 2. Entra Sign-in Logs: When a Token Was Issued

Each time Entra issues a token, it writes a sign-in event. The event names the application, the user (if any), the resource and the time. It does not store the token.

**Where:** Microsoft Entra admin center > **Entra ID** > **Monitoring & health** > **Sign-in logs** (needs at least the Reports Reader role). The same logs, filtered to one app, are under the app's enterprise application > **Sign-in logs**.

**The tabs:**

- **User sign-ins (interactive):** the person gave a credential in this event, such as a password or an MFA response.
- **User sign-ins (non-interactive):** a client signed in for the person with something it already held, such as a refresh token or the browser's existing session. The person typed nothing.
- **Service principal sign-ins:** an app signed in as itself. Autonomous agents appear here.
- **Managed identity sign-ins.**

**Agent fields.** Each event has **Is Agent**, **Agent type** and **Agent subject type**, and the portal can filter on them. In Graph (beta), the property is `agent/agentType`, for example ([Agent ID logs](https://learn.microsoft.com/en-us/entra/agent-id/sign-in-audit-logs-agents), found in search results, not tested in this tenant):

```http
GET https://graph.microsoft.com/beta/auditLogs/signIns?$filter=signInEventTypes/any(t: t eq 'servicePrincipal') and agent/agentType eq 'AgentIdentity'
```

**Where each claim appears** (verified, [Linkable identifiers](https://learn.microsoft.com/en-us/entra/identity/authentication/how-to-authentication-track-linkable-identifiers)):

| Token claim | Sign-in log field |
|---|---|
| `oid` | User ID |
| `tid` | Resource Tenant ID |
| `sid` | Session ID |
| `deviceid` | Device ID |
| `uti` | Unique Token Identifier |
| `iat` | Date |

**Searching by one token.** Open an event to see its **Unique Token Identifier**. To search for one value across all sign-ins, send the sign-in logs to Log Analytics and query it (not checked whether the portal can filter on this field):

```kusto
union SigninLogs, AADNonInteractiveUserSignInLogs, AADServicePrincipalSignInLogs
| where UniqueTokenIdentifier == "<the token's uti>"
```

**An example from this project: why the CLI's sign-in was "non-interactive".** On 3 November 2026 the CLI signed a person in, and the event appeared under **User sign-ins (non-interactive)**, with "Additional details: MFA requirement satisfied by claim in the token". Most likely, the person was already signed in to the Entra admin center in the same browser (not checked). The CLI's `prompt=select_account` only asked them to pick an account, and Entra reused the existing session, which already had MFA. The same event showed **Is Agent: No** and **Agent type: Not Agentic**, as expected for a person using a normal client.

---

## 3. Microsoft Graph Activity Logs: Every Graph Call

This log records every HTTP request that Microsoft Graph receives for the tenant: "an audit trail of all HTTP requests that the Microsoft Graph service receives and processes for a tenant" ([Graph activity logs](https://learn.microsoft.com/en-us/graph/microsoft-graph-activity-logs-overview), verified). It is the record that links an action to the token that made it.

**Useful columns:** `AppId`, `ServicePrincipalId`, `UserId`, `Roles`, `Scopes`, `RequestMethod`, `RequestUri`, `ResponseStatusCode`, `SessionId`, `SignInActivityId` (the token's `uti`), `TokenIssuedAt`.

**How to turn it on.** It is off by default. It needs:

- a Microsoft Entra ID P1 or P2 licence,
- a diagnostic setting (set by at least a Security Administrator) that sends the logs to a Log Analytics workspace, a storage account or Event Hubs.

**Joining a call to its sign-in.** Microsoft's own query, from the same page:

```kusto
MicrosoftGraphActivityLogs
| where TimeGenerated > ago(7d)
| join kind=leftouter (union SigninLogs, AADNonInteractiveUserSignInLogs, AADServicePrincipalSignInLogs, AADManagedIdentitySignInLogs, ADFSSignInLogs
    | where TimeGenerated > ago(7d))
    on $left.SignInActivityId == $right.UniqueTokenIdentifier
```

**Delay:** events usually arrive within 30 minutes, and sometimes take up to 2 hours.

---

## 4. Workload Audit Logs: What Happened Inside the Service

A service that does not go through Graph keeps its own audit log. For Microsoft 365 services, that is the unified audit log, searched in **Microsoft Purview** (Audit Standard or Premium). SharePoint Online, Exchange Online and Teams record the linkable identifiers there ([Linkable identifiers](https://learn.microsoft.com/en-us/entra/identity/authentication/how-to-authentication-track-linkable-identifiers), verified).

**In SharePoint Online audit records:**

| Token claim | SharePoint audit field |
|---|---|
| `oid` | `UserObjectId` |
| `tid` | `OrganizationId` |
| `sid` | `AADSessionId`, in the `AppAccessContext` object |
| `deviceid` | `DeviceId` |
| `uti` | `UniqueTokenId`, in the `AppAccessContext` object |
| `iat` | `IssuedAtTime`, in the `AppAccessContext` object |

Exchange Online and Teams use the same `AppAccessContext` fields.

**Where:** the [Microsoft Purview portal](https://purview.microsoft.com/) > Audit. Search a time range and a workload, then filter by user, by `UniqueTokenId` or by `AADSessionId`, or export the results.

**Entra's own audit log.** Directory changes, such as an app or a role assignment being changed, are in Entra's audit log. For supported events it records a directory user session identifier (DUSI) that links the change to the person's session.

---

## 5. The Agent's Own Trace: Why It Acted

The three Microsoft logs answer who and for whom. None of them answers why. Only the agent's platform knows:

- **the task** it was given, and by whom,
- **what it read**: a document or a description it read could contain a planted instruction,
- **the tool calls** it asked for, in order,
- **the approvals**: who approved a privileged action, if anyone.

So an agent platform should write a trace for every run, and give the run a **correlation ID** that also goes into each audit line the agent's tools write. Then the investigation can go from the agent's audit line to its trace, and from the token's `uti` to the Microsoft logs.

---

## 6. Gaps and Limits

- **No sign-in entry for some tokens.** "Activity logs from Microsoft applications might not all have matching sign-in log entries" ([Graph activity logs](https://learn.microsoft.com/en-us/graph/microsoft-graph-activity-logs-overview), verified), so a `uti` from a workload log can have no match.
- **Some workload records lack the identifiers.** Exchange Online leaves them out of some aggregated entries and of background processes. Entra's audit log has a DUSI only for events with a person's session.
- **Delays.** Graph activity logs can take up to 2 hours.
- **Licences and setup.** Graph activity logs need P1 or P2 and a diagnostic setting, set up before the incident. A log that was off on Monday cannot be read on Tuesday.
- **No `sid` for autonomous agents.** `sid` comes from a person's interactive sign-in, so an agent acting on its own is traced by `uti` and its app ID, not by session.

---

## 7. A Worked Example: The Missing SharePoint Site

On Monday morning, an important SharePoint site is gone.

1. **Contain.** Restore the site from the recycle bin. If nobody knows yet why the agent acted, disable the agent identity until they do.
2. **Find the record.** In Microsoft Purview Audit, search SharePoint records for the deletion. Note `UniqueTokenId` and `AADSessionId` from `AppAccessContext`.
3. **Find the token's sign-in.** Search the Entra sign-in logs for that Unique Token Identifier. The event names the application. If **Is Agent** is Yes, the application is the agent identity.
4. **Decide: for a person, or on its own.** A user in the event, and `Scopes` in the Graph activity log, mean delegated: the agent acted for that person. A service principal sign-in with `Roles` and no user means autonomous.
5. **See everything else the token did.** If the agent went through Graph, filter the Graph activity log by `SignInActivityId`. For a person's session, filter by `AADSessionId` or `SessionId`.
6. **Find why.** Use the time and the agent identity to find the run in the agent's own trace: its task, what it read, its tool calls and any approval.
7. **Fix the cause.** Narrow the agent's permissions, and put destructive actions such as deleting a site behind a human approval.

---

## 8. The Guardian Today

**What the guardian records:**

- **Identity API audit lines.** Each line names the agent identity (`agent_app_id`, `agent_object_id`), its blueprint, the mode (autonomous), the delegating user (none), the approver (for a disable), and the trace ID and span ID of the call ([main.py](../src/identity_api/main.py)).
- **The trace ID per run.** The harness starts the run's trace and logs its ID, and every audit line of the run carries it, in the MCP server and the Identity API. So the model's report can be checked against the audit log, and the trace in Jaeger shows each step ([telemetry.py](../src/shared/telemetry.py)).
- **The executor's audit line for a disable.** `agent_disabled` names the target agent, the agent identity that acted (`agent_app_id`), the request, the approver, `mode: obo` and the delegating user, whose rights the write used.
- **One request ID per disable request,** on every line from request to decision to write, across the MCP server, the Identity API and the executor. The approval is its own trace, with a span link to the span that created the request.

**Verified:** a token's `uti` equals the **Unique Token Identifier** of the sign-in event that issued it. Tested with real guardian tokens on 27 October 2026.

**The delta from the design above:**

- **The `uti` is not in the audit lines yet,** so the Identity API's lines cannot be joined to Entra's sign-in logs directly. The fix is to write the caller token's `uti` into each line.
- **The logs stay on one machine** ([D6](agent-steward-architecture.md#architectural-shortcuts-where-the-guardian-differs)). The Identity API writes to stdout, which Docker keeps until the containers are removed.
- **Graph activity logs:** not checked whether they are turned on in the lab tenant, so the guardian's Graph calls (its reads and its disable) have not been checked against them.
- **The run trace is the harness's log,** not a durable store.
