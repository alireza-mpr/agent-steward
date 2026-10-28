# Decision log

This log records the project's architecture decisions: what was decided, why, and what follows from it. The format is the common architecture decision record (ADR): context, decision, reasons, consequences. A record is not rewritten after it is accepted. If a decision changes, a new record replaces the old one and says so.

For the architecture these decisions belong to, see [Agent architecture](agent-architecture.md).

## Contents

- [ADR-001: The MCP server sends the findings to the Identity API, never the model](#adr-001-the-mcp-server-sends-the-findings-to-the-identity-api-never-the-model)

---

## ADR-001: The MCP server sends the findings to the Identity API, never the model

> **Decision: the `scan_registry` tool computes the findings and sends them to the Identity API itself, inside the MCP server. The findings never pass through the model on their way to the audit log.**

**Status:** Accepted, 28 October 2026. Not built yet: it is built together with the MCP server.

**Context.** The guardian's registry scan produces findings: orphan, expiring credential, over-permissioned and dormant agents. The findings must reach the Identity API, which writes one audit line per decision (FR8). There were two ways to get them there:

- **Option A: a separate `submit_findings` tool.** `scan_registry` returns the findings to the harness, the harness shows them to the model, and the model then calls `submit_findings` with the findings as its input.
- **Option B: `scan_registry` sends them itself.** The tool's code runs the rules and sends the findings to the Identity API in the same call. It then returns the findings and the audit's correlation ID to the harness, only so that the model can write its report.

**Decision.** Option B.

**Why:**

1. **The model is the part most exposed to attack.** A prompt injection, for example a planted instruction in an agent's description, can steer what the model asks for. In option A every finding passes through the model as a tool input, so a tricked model could drop a finding, change it, or invent one before it reaches the audit log.
2. **The harness is not trusted for evidence either.** The harness controls what the model sees and could change or fake a tool result ([Agent architecture](agent-architecture.md#5-blast-radius-what-happens-if-one-part-is-compromised)). With option B, neither the harness nor the model is on the path from the rules to the audit log.
3. **Evidence stays inside one trust boundary.** In option B the findings go straight from the code that computed them to the Identity API, inside the MCP server, which is also the only part that holds the agent's token. So the audit log is proof of what the rules found, and the model's report is only a summary of it.
4. **No write path that the model fills in.** In option A the tool would write whatever the model passes, signed with the agent's identity. A tool that writes audit data must not take that data from the model.

**Consequences:**

- The model cannot hide a finding from the audit log, and it cannot add a finding that the rules did not produce.
- The model's final report can still be wrong or incomplete. Anyone who needs the truth reads the audit log, using the correlation ID that `scan_registry` returns.
- `scan_registry` has a side effect: it writes to the Identity API. So it is not marked as a read-only tool.
- The same rule applies to any future tool that writes audit or evidence data: the tool's code produces the data, never the model.
