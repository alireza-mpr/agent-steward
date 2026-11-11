# Decision log

This log records the project's architecture decisions: what was decided, why, and what follows from it. The format is the common architecture decision record (ADR): context, decision, reasons, consequences. A record is not rewritten after it is accepted. If a decision changes, a new record replaces the old one and says so.

For the architecture these decisions belong to, see [Agent Steward architecture](agent-steward-architecture.md).

## Contents

- [ADR-001: The MCP server sends the findings to the Identity API, never the model](#adr-001-the-mcp-server-sends-the-findings-to-the-identity-api-never-the-model)
- [ADR-002: The approver signs in with the authorization code flow, not the device code flow](#adr-002-the-approver-signs-in-with-the-authorization-code-flow-not-the-device-code-flow)
- [ADR-003: The approved disable runs in a separate executor, with its own sidecar](#adr-003-the-approved-disable-runs-in-a-separate-executor-with-its-own-sidecar)

---

## ADR-001: The MCP server sends the findings to the Identity API, never the model

> **Decision: the `scan_registry` tool computes the findings and sends them to the Identity API itself, inside the MCP server. The findings never pass through the model on their way to the audit log.**

**Status:** Accepted, 28 October 2026. Built with the MCP server, and run live on 28 October 2026 ([C11](security-controls.md#the-mcp-server)).

**Context.** The guardian's registry scan produces findings: orphan, expiring credential, over-permissioned and dormant agents. The findings must reach the Identity API, which writes one audit line per decision (FR8). There were two ways to get them there:

- **Option A: a separate `submit_findings` tool.** `scan_registry` returns the findings to the harness, the harness shows them to the model, and the model then calls `submit_findings` with the findings as its input.
- **Option B: `scan_registry` sends them itself.** The tool's code runs the rules and sends the findings to the Identity API in the same call. It then returns the findings and the audit's correlation ID to the harness, only so that the model can write its report.

**Decision.** Option B.

**Why:**

1. **The model is the part most exposed to attack.** A prompt injection, for example a planted instruction in an agent's description, can steer what the model asks for. In option A every finding passes through the model as a tool input, so a tricked model could drop a finding, change it, or invent one before it reaches the audit log.
2. **The harness is not trusted for evidence either.** The harness controls what the model sees and could change or fake a tool result ([Threat model](../THREATMODEL.md#blast-radius-what-happens-if-one-part-is-compromised)). With option B, neither the harness nor the model is on the path from the rules to the audit log.
3. **Evidence stays inside one trust boundary.** In option B the findings go straight from the code that computed them to the Identity API, inside the MCP server, which is also the only part that holds the agent's token. So the audit log is proof of what the rules found, and the model's report is only a summary of it.
4. **No write path that the model fills in.** In option A the tool would write whatever the model passes, signed with the agent's identity. A tool that writes audit data must not take that data from the model.

**Consequences:**

- The model cannot hide a finding from the audit log, and it cannot add a finding that the rules did not produce.
- The model's final report can still be wrong or incomplete. Anyone who needs the truth reads the audit log, using the run's trace ID, which the harness logs and every audit line of the run carries (since 11 November 2026; until then, a correlation ID that `scan_registry` returned).
- `scan_registry` has a side effect: it writes to the Identity API. So it is not marked as a read-only tool.
- The same rule applies to any future tool that writes audit or evidence data: the tool's code produces the data, never the model.

---

## ADR-002: The approver signs in with the authorization code flow, not the device code flow

> **Decision: the `approve` command signs the person in with the authorization code flow and PKCE. The tenant's security defaults stay on.**

**Status:** Accepted, 31 October 2026. Built and run live.

**Context.** The `approve` command first used the device code flow, because it runs in a container with no browser. The first live sign-in failed with `AADSTS530035` (`BlockedBySecurityDefaults`). Security defaults block the device code flow ([Learn](https://learn.microsoft.com/en-us/entra/fundamentals/security-defaults)). There were two ways forward:

- **Option A: authorization code with PKCE.** Add a `localhost` redirect URI to the client app, and change the sign-in code.
- **Option B: turn off security defaults.** Keep the device code flow, and write a Conditional Access policy to require MFA and limit the flow.

**Decision.** Option A.

**Why:**

1. **It removes a risk instead of accepting one.** The device code flow is a known phishing route, and a person who approves privileged actions is a good target for it.
2. **It leaves the tenant's protection on.** Option B weakens the whole tenant so that one command can sign in.
3. **It is the recommended flow** ([RFC 9700](https://www.rfc-editor.org/rfc/rfc9700)). PKCE ties the code to the run that asked for it, so another program that sees the address cannot use the code.

**Consequences:**

- **A listener catches the redirect** (since 11 November 2026; before, the person pasted the final address into the terminal). The command runs in a container, so the one-shot listener runs on the container's `0.0.0.0:18400`, and Docker publishes it only on the host's `127.0.0.1:18400` (`docker compose run --service-ports`). If another local program held the port and caught the redirect, the code would still be useless to it without this run's PKCE verifier ([RFC 7636](https://www.rfc-editor.org/rfc/rfc7636)).
- **A fixed local port.** The command uses `http://localhost:18400`, because the author's machine had a web server on port 80. Entra ignores the port when it matches a `localhost` redirect URI ([Learn](https://learn.microsoft.com/en-us/entra/identity-platform/reply-url)), so the registered `http://localhost` covers it.
- **Public client flows are off** on the client app, because nothing needs them now.

---

## ADR-003: The approved disable runs in a separate executor, with its own sidecar

> **Decision: after an approval, the approver's CLI sends the approver's token (Tc) to the executor, a separate service. The executor exchanges Tc with OBO and disables the agent with the approver's rights. The MCP server only asks, and holds no write code.**

**Status:** Accepted, 10 November 2026. Built, unit-tested, and run live end to end on 10 November 2026.

**Context.** The disable moved to OBO (FR4), so the guardian holds no app-only write permission. For OBO, Tc must reach a service that can act as the guardian's agent identity, because only the token's audience may exchange it. The MCP server was the only such service, but it also runs the model's tool calls, so it handles untrusted input. There were three options:

- **Option A: a separate executor, as the same guardian identity, with its own sidecar.**
- **Option B: a separate executor with its own blueprint and agent identity.**
- **Option C: keep the delegation route in the MCP server.**

**Decision.** Option A.

**Why:**

1. **The write is away from the model's tools.** Only the executor holds the write code and receives Tc, and only its sidecar has the OBO settings. A bug or an injection path in the MCP server cannot reach the write.
2. **The harness cannot reach it.** The executor is on the approval network, with the CLI and the Identity API only.
3. **The tenant setup stays on one agent.** The blueprint's `access_as_user` scope, the CLI's grant and the delegated Graph grant all target the guardian. With Option B they would move to a new identity, and the guardian would act no more: the executor would. The guardian's own-agent protection covers only its own blueprint, so the guardian could also propose disabling its own executor.

**Consequences:**

- **Two copies of the certificate.** The blueprint's certificate is mounted into both sidecars ([D3](agent-steward-architecture.md#architectural-shortcuts-where-the-guardian-differs)).
- **The executor is now the most trusted part.** It receives each approver's token, which is valid for its whole lifetime, not for one action ([A8](../THREATMODEL.md#accepted-risks)). So its code is kept to one route and one write.
- **The checks are shared.** The executor runs the MCP server's checks again before the write, from one module ([disable_checks.py](../src/mcp_server/disable_checks.py)), so they cannot drift apart.
- **CIBA would remove the CLI-to-executor hop,** because the authorization server would send the token straight to the agent. Entra's OpenID metadata has no `backchannel_authentication_endpoint` (checked 10 November 2026), so it is not an option.

