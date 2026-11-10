// OBO on the approved disable (FR4): the agent proposes, and the approver decides and lends
// their own rights. Two admin consents, for all users. Run
// setup/agent-id/Deploy-ApproverDelegation.ps1 first: it creates the blueprint's
// access_as_user scope that the second consent names.
//
// Deploy (signed in with `az login`, lab subscription selected):
//   az deployment group create --resource-group rg-agent-steward \
//     --template-file setup/bicep/approver-delegation.bicep \
//     --parameters guardianAgentId=<GUARDIAN_AGENT_APP_ID> blueprintPrincipalId=<BLUEPRINT_PRINCIPAL_ID>

extension microsoftGraphV1

@description('Object ID of the guardian\'s agent identity. For an agent identity it equals its app ID.')
param guardianAgentId string

@description('Object ID of the guardian blueprint\'s principal (printed by Deploy-ApproverDelegation.ps1).')
param blueprintPrincipalId string

// Microsoft Graph's appId is a public constant, the same in every tenant.
resource graph 'Microsoft.Graph/servicePrincipals@v1.0' existing = {
  appId: '00000003-0000-0000-c000-000000000000'
}

resource agentDisableCli 'Microsoft.Graph/applications@v1.0' existing = {
  uniqueName: 'agent-steward-agent-disable-cli'
}

resource agentDisableCliPrincipal 'Microsoft.Graph/servicePrincipals@v1.0' existing = {
  appId: agentDisableCli.appId
}

// 1. The guardian may disable agent identities on behalf of a signed-in user. Granted on the
// guardian's own agent identity, not inherited from the blueprint, so no other agent of the
// blueprint gets it. A delegated permission never exceeds the user's own rights: Graph also
// checks that the approver may disable agents (Agent ID Administrator, Cloud Application
// Administrator, or the agent's owner). Not on Microsoft's list of permissions blocked for agents.
resource guardianDisablesForTheApprover 'Microsoft.Graph/oauth2PermissionGrants@v1.0' = {
  clientId: guardianAgentId
  consentType: 'AllPrincipals'
  resourceId: graph.id
  scope: 'AgentIdentity.EnableDisable.All'
}

// 2. The approve CLI may ask for a token to the guardian's blueprint (Tc), which the guardian then
// exchanges with OBO. Consent alone approves nothing: the MCP server only uses Tc for a request
// that this same person approved at the Identity API.
resource cliGetsTokensForTheGuardian 'Microsoft.Graph/oauth2PermissionGrants@v1.0' = {
  clientId: agentDisableCliPrincipal.id
  consentType: 'AllPrincipals'
  resourceId: blueprintPrincipalId
  scope: 'access_as_user'
}
