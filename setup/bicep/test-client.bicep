// A plain test client, not an agent, for the live negative test (FR5). It holds
// Findings.Submit on purpose: then the permission check (6) would pass, so only the
// agent check (5) can reject it. Test only: delete it when Phase 2 is done.
//
// Deploy (signed in with `az login`, lab subscription selected):
//   az deployment group create --resource-group rg-agent-steward \
//     --template-file setup/bicep/test-client.bicep \
//     --parameters identityApiAppId=<IDENTITY_API_APP_ID> \
//                  ownerId=$(az ad signed-in-user show --query id -o tsv)
//
// Its secret is not created here (Bicep cannot create secrets): see docs/infra.md.

extension microsoftGraphV1

@description('App ID of the Identity API (output of identity-api.bicep).')
param identityApiAppId string

@description('Object ID of the person accountable for the test client (its owner).')
param ownerId string

resource testClient 'Microsoft.Graph/applications@v1.0' = {
  uniqueName: 'agent-steward-test-client'
  displayName: 'agent-steward-test-client'
  signInAudience: 'AzureADMyOrg'
  owners: {
    relationships: [ownerId]
  }
}

resource testClientPrincipal 'Microsoft.Graph/servicePrincipals@v1.0' = {
  appId: testClient.appId
}

resource identityApi 'Microsoft.Graph/servicePrincipals@v1.0' existing = {
  appId: identityApiAppId
}

resource testClientCanSubmit 'Microsoft.Graph/appRoleAssignedTo@v1.0' = {
  principalId: testClientPrincipal.id
  resourceId: identityApi.id
  appRoleId: filter(identityApi.appRoles, role => role.value == 'Findings.Submit')[0].id
}

output testClientAppId string = testClient.appId
