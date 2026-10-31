// The people who may approve disabling an agent (FR7): a security group that holds the Identity
// API's AgentDisable.Approve role, and the public client app that the `approve` command signs in
// with (device code flow, no secret). Deploy identity-api.bicep first: it defines the role and
// the scope that this file uses.
//
// Deploy (signed in with `az login`, lab subscription selected):
//   me=$(az ad signed-in-user show --query id -o tsv)
//   az deployment group create --resource-group rg-agent-steward \
//     --template-file setup/bicep/agent-disable-approvers.bicep \
//     --parameters ownerId=$me approverIds="['$me']"

extension microsoftGraphV1

@description('Object ID of the person accountable for the group and the app (their owner).')
param ownerId string

@description('Object IDs of the people who may approve disabling an agent: the group\'s direct members.')
param approverIds array

resource identityApi 'Microsoft.Graph/applications@v1.0' existing = {
  uniqueName: 'agent-steward-identity-api'
}

resource identityApiPrincipal 'Microsoft.Graph/servicePrincipals@v1.0' existing = {
  appId: identityApi.appId
}

// The role goes to the group, so adding or removing an approver changes only the membership.
// Only direct members get the role: nested groups do not count. Assigning an app role to a
// group needs Entra ID P1 or P2. In an enterprise, the approvers are not the people who own or
// operate the guardian, so no one person can both steer it and approve its actions.
resource agentDisableApprovers 'Microsoft.Graph/groups@v1.0' = {
  uniqueName: 'agent-steward-agent-disable-approvers'
  displayName: 'agent-steward-agent-disable-approvers'
  description: 'People who may approve or deny a request to disable an agent (FR7).'
  mailEnabled: false
  mailNickname: 'agent-steward-agent-disable-approvers'
  securityEnabled: true
  owners: {
    relationships: [ownerId]
  }
  members: {
    relationships: approverIds
  }
}

resource approversHoldTheApproveRole 'Microsoft.Graph/appRoleAssignedTo@v1.0' = {
  principalId: agentDisableApprovers.id
  resourceId: identityApiPrincipal.id
  appRoleId: filter(identityApi.appRoles, role => role.value == 'AgentDisable.Approve')[0].id
}

// The `approve` command's app. It runs on the approver's machine and cannot keep a secret, so it
// is a public client with no credential. "Allow public client flows" turns on the device code
// flow. Its own registration gives approvals their own name in the sign-in logs, and their own
// Conditional Access target.
resource agentDisableCli 'Microsoft.Graph/applications@v1.0' = {
  uniqueName: 'agent-steward-agent-disable-cli'
  displayName: 'agent-steward-agent-disable-cli'
  signInAudience: 'AzureADMyOrg'
  isFallbackPublicClient: true
  owners: {
    relationships: [ownerId]
  }
  requiredResourceAccess: [
    {
      resourceAppId: identityApi.appId
      resourceAccess: [
        {
          id: filter(identityApi.api.oauth2PermissionScopes, scope => scope.value == 'AgentDisable.Decide')[0].id
          type: 'Scope'
        }
      ]
    }
  ]
}

resource agentDisableCliPrincipal 'Microsoft.Graph/servicePrincipals@v1.0' = {
  appId: agentDisableCli.appId
}

// Admin consent for all users, so nobody sees a consent prompt. Consent alone approves nothing:
// the Identity API also requires the AgentDisable.Approve role, which only the group's members hold.
resource agentDisableCliConsent 'Microsoft.Graph/oauth2PermissionGrants@v1.0' = {
  clientId: agentDisableCliPrincipal.id
  consentType: 'AllPrincipals'
  resourceId: identityApiPrincipal.id
  scope: 'AgentDisable.Decide'
}

output agentDisableCliAppId string = agentDisableCli.appId
