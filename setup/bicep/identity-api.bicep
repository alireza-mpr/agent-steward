// The Identity API in Entra: its app registration, its service principal, and the guardian's
// roles (FR3, FR5, FR7). For the agent-disable approval (FR7) it defines the guardian's
// AgentDisable.Request role, the approvers' AgentDisable.Approve role and the AgentDisable.Decide
// scope. setup/bicep/agent-disable-approvers.bicep assigns and uses the last two.
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

// FR7, the agent-disable approval: one role per party, named Resource.Operation like Graph's
// permissions, so each privileged action can have its own approvers.

// What the guardian may do: ask to disable an agent, and read its request.
var agentDisableRequestRole = {
  id: guid('agent-steward-identity-api', 'AgentDisable.Request')
  value: 'AgentDisable.Request'
  displayName: 'Request an agent disable'
  description: 'Lets an agent ask a human to approve disabling another agent, and read the request.'
  allowedMemberTypes: ['Application']   // app-only: granted to agents, not users
  isEnabled: true
}

// What a person may do: approve or deny a request to disable an agent. Assigned to people
// (through the agent-disable approvers group), never to apps or agents. It reaches the API
// in the `roles` claim.
var agentDisableApproveRole = {
  id: guid('agent-steward-identity-api', 'AgentDisable.Approve')
  value: 'AgentDisable.Approve'
  displayName: 'Agent disable approver'
  description: 'Lets a person (a human) approve or deny a request to disable an agent.'
  allowedMemberTypes: ['User']   // users and groups, not apps or agents
  isEnabled: true
}

// What a client app may do for the signed-in person: read a request and send their decision.
// It reaches the API in the `scp` claim. The API needs both: the scope (the client may ask)
// and the role (the person may approve).
var agentDisableDecideScope = {
  id: guid('agent-steward-identity-api', 'AgentDisable.Decide')
  value: 'AgentDisable.Decide'
  type: 'Admin'   // only an admin can consent to it
  adminConsentDisplayName: 'Decide on agent disable requests'
  adminConsentDescription: 'Lets the app read a request to disable an agent and send the signed-in approver\'s decision.'
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
    oauth2PermissionScopes: [agentDisableDecideScope]
  }
  appRoles: [findingsSubmitRole, agentDisableRequestRole, agentDisableApproveRole]
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

// The MCP server's disable_agent tool creates and reads its requests as the guardian (FR7).
resource guardianCanRequestDisable 'Microsoft.Graph/appRoleAssignedTo@v1.0' = {
  principalId: guardianAgentId
  resourceId: identityApiPrincipal.id
  appRoleId: agentDisableRequestRole.id
}

output identityApiAppId string = identityApi.appId
