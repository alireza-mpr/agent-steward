#Requires -Version 7.0
#Requires -Modules Microsoft.Graph.Authentication

<#
.SYNOPSIS
    Creates the project's Agent ID objects: a stand-in sponsor user, blueprints,
    blueprint principals and agent identities.

.DESCRIPTION
    Safe to run again: every object is looked up by name and created only if missing.
    The Graph Bicep extension has no Agent ID types (see docs/infra.md), so this
    script uses Microsoft Graph PowerShell.

    Owner (technical) = the signed-in admin. Sponsor (business) = a stand-in user,
    so the two roles are held by different people (FR2).

    The deliberately flawed fixture agents are in Deploy-Fixtures.ps1.

.PARAMETER TenantId
    The tenant to deploy to, for example <your-domain>.onmicrosoft.com.

.EXAMPLE
    ./setup/agent-id/Deploy-AgentIdentities.ps1 -TenantId <your-domain>.onmicrosoft.com
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string] $TenantId
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot/AgentIdHelpers.ps1"

# Kind -> instances. The worker is added here in Phase 5.
# agent-steward-guardian-norole-01 is a test agent for the live negative test (FR5): it
# holds no app role on the Identity API, so the API must reject it at the permission check.
# It sits under the guardian's blueprint because the sidecar holds only that blueprint's key.
$Agents = @(
    @{ Blueprint = 'agent-steward-guardian'; Identities = @('agent-steward-guardian-01', 'agent-steward-guardian-norole-01') }
)

$admin   = Connect-AgentIdTenant -TenantId $TenantId -Scopes $BaseScopes
$domain  = $admin.userPrincipalName.Split('@')[1]
$sponsor = Set-StandInUser @StandInSponsor -Domain $domain

foreach ($agent in $Agents) {
    $blueprint = Set-Blueprint -Name $agent.Blueprint -OwnerId $admin.id -SponsorId $sponsor.id
    Set-BlueprintPrincipal -Name $agent.Blueprint -AppId $blueprint.appId
    foreach ($identity in $agent.Identities) {
        Set-AgentIdentity -Name $identity -BlueprintAppId $blueprint.appId -OwnerId $admin.id -SponsorId $sponsor.id | Out-Null
    }
}

Write-Host 'Done.'
