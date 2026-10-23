#Requires -Version 7.0
#Requires -Modules Microsoft.Graph.Authentication

<#
.SYNOPSIS
    Creates the fixture agents: registry entries with deliberate governance flaws
    for the guardian to find (FR3). They run no code.

.DESCRIPTION
    Safe to run again. Fixtures:
      agent-steward-fixture-dormant-01    never signs in
      agent-steward-fixture-overperm-01   holds a Graph permission its (empty) job does not need
      agent-steward-fixture-orphan-01     its only sponsor "left the company" (user deleted)
      agent-steward-fixture-expiring-01   its blueprint's only secret expires soon

    The expiring fixture has its own blueprint: credentials live on the blueprint,
    so sharing one would give every fixture the expiring secret.

.PARAMETER TenantId
    The tenant to deploy to, for example <your-domain>.onmicrosoft.com.

.PARAMETER SecretLifetimeDays
    Lifetime of the expiring fixture's secret. Short on purpose.

.EXAMPLE
    ./setup/agent-id/Deploy-Fixtures.ps1 -TenantId <your-domain>.onmicrosoft.com
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string] $TenantId,

    [ValidateRange(1, 30)]
    [int] $SecretLifetimeDays = 10
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot/AgentIdHelpers.ps1"

$FixtureScopes = $BaseScopes + @(
    'AgentIdentityBlueprint.AddRemoveCreds.All'   # add the expiring secret
    'AppRoleAssignment.ReadWrite.All'             # grant the excess permission
)

# Microsoft Graph's appId: a public constant, the same in every tenant.
$MicrosoftGraphAppId = '00000003-0000-0000-c000-000000000000'

function Grant-GraphAppRole {
    param(
        [Parameter(Mandatory)] [hashtable] $Identity,
        [Parameter(Mandatory)] [string] $Permission
    )
    $graph = @(Find-GraphObject -CollectionUri "$GraphV1/servicePrincipals" -Filter "appId eq '$MicrosoftGraphAppId'" -Select 'id,appRoles')[0]
    $role  = $graph.appRoles | Where-Object { $_.value -eq $Permission } | Select-Object -First 1
    if (-not $role) { throw "Microsoft Graph has no app role named '$Permission'." }

    $assignmentsUri = "$GraphV1/servicePrincipals/$($Identity.id)/appRoleAssignments"
    $existing = @((Invoke-Graph -Method GET -Uri $assignmentsUri).value |
        Where-Object { $_.resourceId -eq $graph.id -and $_.appRoleId -eq $role.id })
    if ($existing) {
        Write-Step 'exists' "$($Identity.displayName) holds Graph $Permission"
        return
    }
    Invoke-WithRetry {
        Invoke-Graph -Method POST -Uri $assignmentsUri -Body @{
            principalId = $Identity.id
            resourceId  = $graph.id
            appRoleId   = $role.id
        }
    } | Out-Null
    Write-Step 'created' "$($Identity.displayName) granted Graph $Permission"
}

function Set-OrphanFixture {
    param(
        [Parameter(Mandatory)] [string] $Name,
        [Parameter(Mandatory)] [string] $BlueprintAppId,
        [Parameter(Mandatory)] [string] $OwnerId,
        [Parameter(Mandatory)] [string] $Domain
    )
    if (@(Find-GraphObject -CollectionUri "$GraphV1/servicePrincipals/microsoft.graph.agentIdentity" -Filter "displayName eq '$Name'")) {
        Write-Step 'exists' "agent identity $Name"
        return
    }
    # A sponsor is required at creation, so give it a throwaway sponsor, then delete
    # that user: the realistic "sponsor left the company" case.
    $departed = Set-StandInUser -Nickname 'departed.sponsor' -DisplayName 'Departed Sponsor (lab stand-in)' -Domain $Domain
    Set-AgentIdentity -Name $Name -BlueprintAppId $BlueprintAppId -OwnerId $OwnerId -SponsorId $departed.id | Out-Null
    Invoke-Graph -Method DELETE -Uri "$GraphV1/users/$($departed.id)" | Out-Null
    Write-Step 'updated' "sponsor of $Name left the company (user deleted)"
}

function Set-ExpiringSecret {
    param(
        [Parameter(Mandatory)] [hashtable] $Blueprint,
        [Parameter(Mandatory)] [int] $Days
    )
    $current = Invoke-Graph -Method GET -Uri "$GraphV1/applications/$($Blueprint.id)?`$select=passwordCredentials"
    if (@($current.passwordCredentials).Count -gt 0) {
        Write-Step 'exists' "secret on blueprint $($Blueprint.displayName)"
        return
    }
    $endDateTime = (Get-Date).ToUniversalTime().AddDays($Days).ToString('o')
    # The response holds the secret text. Out-Null discards it, so it is never shown or stored.
    Invoke-WithRetry {
        Invoke-Graph -Method POST -Uri "$GraphV1/applications/$($Blueprint.id)/microsoft.graph.agentIdentityBlueprint/addPassword" -Body @{
            passwordCredential = @{ displayName = 'fixture: expires soon'; endDateTime = $endDateTime }
        }
    } | Out-Null
    Write-Step 'created' "secret on blueprint $($Blueprint.displayName), expires in $Days days"
}

# --- main ---

$admin   = Connect-AgentIdTenant -TenantId $TenantId -Scopes $FixtureScopes
$domain  = $admin.userPrincipalName.Split('@')[1]
$sponsor = Set-StandInUser @StandInSponsor -Domain $domain

# Shared blueprint for the dormant, over-permissioned and orphan fixtures.
$fixture = Set-Blueprint -Name 'agent-steward-fixture' -OwnerId $admin.id -SponsorId $sponsor.id
Set-BlueprintPrincipal -Name 'agent-steward-fixture' -AppId $fixture.appId

Set-AgentIdentity -Name 'agent-steward-fixture-dormant-01' -BlueprintAppId $fixture.appId -OwnerId $admin.id -SponsorId $sponsor.id | Out-Null

$overperm = Set-AgentIdentity -Name 'agent-steward-fixture-overperm-01' -BlueprintAppId $fixture.appId -OwnerId $admin.id -SponsorId $sponsor.id
Grant-GraphAppRole -Identity $overperm -Permission 'User.Read.All'

Set-OrphanFixture -Name 'agent-steward-fixture-orphan-01' -BlueprintAppId $fixture.appId -OwnerId $admin.id -Domain $domain

# The expiring fixture gets its own blueprint, because the secret lives on the blueprint.
$expiring = Set-Blueprint -Name 'agent-steward-fixture-expiring' -OwnerId $admin.id -SponsorId $sponsor.id
Set-BlueprintPrincipal -Name 'agent-steward-fixture-expiring' -AppId $expiring.appId
Set-AgentIdentity -Name 'agent-steward-fixture-expiring-01' -BlueprintAppId $expiring.appId -OwnerId $admin.id -SponsorId $sponsor.id | Out-Null
Set-ExpiringSecret -Blueprint $expiring -Days $SecretLifetimeDays

Write-Host 'Done.'
