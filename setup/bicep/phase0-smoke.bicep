// Phase 0 smoke test: proves the Microsoft Graph Bicep extension can create
// an Entra object in this tenant. Safe to delete.
//
// Deploy (signed in with `az login`, lab subscription selected):
//   az group create --name rg-agent-steward --location australiaeast
//   az deployment group create --resource-group rg-agent-steward --template-file phase0-smoke.bicep

extension microsoftGraphV1

resource smokeGroup 'Microsoft.Graph/groups@v1.0' = {
  uniqueName: 'agentStewardPhase0Smoke'
  displayName: 'agent-steward-phase0-smoke'
  description: 'Phase 0 smoke test for the Graph Bicep extension. Safe to delete.'
  mailEnabled: false
  mailNickname: 'agent-steward-phase0-smoke'
  securityEnabled: true
}

output groupObjectId string = smokeGroup.id
