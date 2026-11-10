// The guardian's Microsoft Graph application permissions (FR3, NFR1): four read-only ones, for the
// autonomous scan. One app role assignment per permission, on Microsoft Graph's service principal.
// The guardian holds no write permission of its own (locked decision 7: no shortcut around OBO):
// a disable runs only with an approver's rights, through the delegated grant in
// approver-delegation.bicep. An earlier version assigned the app-only AgentIdentity.EnableDisable.All
// here; Bicep does not delete it when it leaves this file, so Deploy-ApproverDelegation.ps1 removes it.
//
// Deploy (signed in with `az login`, lab subscription selected):
//   az deployment group create --resource-group rg-agent-steward \
//     --template-file setup/bicep/guardian-permissions.bicep \
//     --parameters guardianAgentId=<GUARDIAN_AGENT_APP_ID>

extension microsoftGraphV1

@description('Object ID of the guardian\'s agent identity. For an agent identity it equals its app ID.')
param guardianAgentId string

// The least privileged application permission for each check. None is on Microsoft's
// list of permissions blocked for agents.
var graphPermissions = [
  // List the agent identities, and their sponsors for the orphan check. Graph's docs
  // list only AgentIdentity.ReadWrite.All for sponsors, but this read-only permission
  // reads them too (tested 26 October 2026).
  'AgentIdentity.Read.All'
  'AgentIdentityBlueprint.Read.All'   // blueprints and their credentials: the expiring check
  'Application.Read.All'              // app role assignments: the over-permissioned check
  'AuditLog.Read.All'                 // the sign-in log: the dormant check
]

// Microsoft Graph's appId is a public constant, the same in every tenant.
resource graph 'Microsoft.Graph/servicePrincipals@v1.0' existing = {
  appId: '00000003-0000-0000-c000-000000000000'
}

resource graphGrants 'Microsoft.Graph/appRoleAssignedTo@v1.0' = [for permission in graphPermissions: {
  principalId: guardianAgentId
  resourceId: graph.id
  appRoleId: filter(graph.appRoles, role => role.value == permission)[0].id
}]
