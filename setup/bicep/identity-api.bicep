// The Identity API in Entra: its app registration with one app role, its service
// principal, and that role assigned to the guardian's agent identity (FR3, FR5).
//
// Deploy (signed in with `az login`, lab subscription selected):
//   az deployment group create --resource-group rg-agent-steward \
//     --template-file setup/bicep/identity-api.bicep \
//     --parameters guardianAgentId=<GUARDIAN_AGENT_APP_ID> \
//                  ownerId=$(az ad signed-in-user show --query id -o tsv)

extension microsoftGraphV1

@description('Object ID of the guardian\'s agent identity. For an agent identity it equals its app ID.')
param guardianAgentId string

@description('Object ID of the person accountable for the API\'s app registration (its owner).')
param ownerId string

var findingsSubmitRole = {
  id: guid('agent-steward-identity-api', 'Findings.Submit')   // stable across runs
  value: 'Findings.Submit'
  displayName: 'Submit governance findings'
  description: 'Lets an agent post its registry findings to the Identity API.'
  allowedMemberTypes: ['Application']   // app-only: granted to agents, not users
  isEnabled: true
}

resource identityApi 'Microsoft.Graph/applications@v1.0' = {
  uniqueName: 'agent-steward-identity-api'
  displayName: 'agent-steward-identity-api'
  signInAudience: 'AzureADMyOrg'
  owners: {
    relationships: [ownerId]
  }
  // v2 tokens: aud is this app's client ID, and the client is in azp. Callers ask
  // for <appId>/.default, so no identifier URI is needed.
  api: {
    requestedAccessTokenVersion: 2
  }
  appRoles: [findingsSubmitRole]
  // Tokens for this API carry the agent claims only when it asks for them
  // (docs/tokens.md). Without them, the API's agent check rejects every agent.
  optionalClaims: {
    accessToken: [
      { name: 'idtyp' }             // app or user: tells the autonomous and OBO modes apart
      { name: 'xms_act_fct' }       // actor facets: contains 11 when the client is an agent identity
      { name: 'xms_sub_fct' }       // subject facets: 13 marks an agent's user account
      { name: 'xms_par_app_azp' }   // the agent's blueprint, for the audit log
    ]
  }
}

resource identityApiPrincipal 'Microsoft.Graph/servicePrincipals@v1.0' = {
  appId: identityApi.appId
}

// The API also checks this role itself (docs/tokens.md, check 6), so a token
// without it is rejected even though Entra still issues one.
resource guardianCanSubmit 'Microsoft.Graph/appRoleAssignedTo@v1.0' = {
  principalId: guardianAgentId
  resourceId: identityApiPrincipal.id
  appRoleId: findingsSubmitRole.id
}

output identityApiAppId string = identityApi.appId
