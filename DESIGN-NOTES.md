# DESIGN-NOTES

Patterns that belong to a production version of this system but were deliberately kept out of code in the 14-day timebox. Each section: what it is, why it matters for agent security, how it would attach to this architecture, and what I have seen of this pattern before.

(To be written in Phase 7. Headings and prompts below.)

## 1. Token exchange (RFC 8693) - narrowing tokens

- What: exchange a broad token for a narrower, audience-specific downstream token before calling a second API.
- Why for agents: an agent should never forward its full-power token downstream; each hop gets the minimum.
- How it would attach here: guardian's token -> exchange -> scoped token for the Identity API only.
- Relation to OBO: both are exchanges; note the difference in actors and claims.

## 2. AIMS - AI Identity Management System

- What the emerging approach proposes; how it compares to Entra Agent ID's model.
- What problem it solves that today's model does not.

## 3. Agent 365 governance

- What the Agent 365 suite adds on top of Agent ID: inventory, governance features, licensing boundaries.
- Which parts of this project it would replace or absorb (the guardian overlaps with parts of it - say where).
- Licensing reality: which controls (CA for agents, ID Protection for agents, agent lifecycle workflows) sit behind Agent 365 / E7 plans.

## 4. Dynamic agent registration - the CDR parallel

- CDR solved third-party onboarding with: a central register of accredited recipients, a signed software statement (SSA), and Dynamic Client Registration (RFC 7591).
- The agent world is rebuilding the same machinery: agent registry <-> CDR Register; blueprint <-> software statement; programmatic identity creation from a blueprint <-> DCR.
- MCP's authorization story leans on OAuth DCR for clients registering with servers - the same RFC, nearly unchanged.
- What this project demonstrates (static, scripted registration from a blueprint) vs what production adds (runtime minting of agent instances, attestation, revocation at registry level).
- First-hand experience: I operated the receiving end of CDR's DCR at a major bank - onboarding, credential and certificate lifecycle, revocation and audit for 450 B2B client identities.

## 5. Workload identity federation for non-Microsoft agents

- What: an agent running outside Azure (AWS, local container) gets an Entra agent identity with no secret, by federating its platform-issued token.
- Why: kills long-lived cross-cloud secrets - the worst NHI anti-pattern.
- How it would attach here: the live worker, moved to AWS, authenticating via federation instead of a blueprint credential.
