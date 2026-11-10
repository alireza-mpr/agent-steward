#Requires -Version 7.0
#Requires -Modules Microsoft.Graph.Authentication

<#
.SYNOPSIS
    Prepares the guardian's blueprint for OBO on the approved disable (FR4): the agent
    proposes, and the approver decides and lends their own rights.

.DESCRIPTION
    Safe to run again. Two settings on the guardian's blueprint, and one removal:

      1. The blueprint exposes a delegated scope, access_as_user, with v2 access tokens. The
         approve CLI asks for <blueprint app ID>/access_as_user, so the approver's token (Tc) has
         the blueprint's app ID as its audience, as OBO for agents requires (Learn: "Agent OAuth
         flows: On-behalf-of flow"). No App ID URI is needed: the client names the scope by app
         ID, as it already does for the Identity API.
      2. Nothing from Microsoft Graph is inheritable (noScopes). The guardian's delegated
         permission is granted directly on its own agent identity instead
         (setup/bicep/approver-delegation.bicep), so no other agent identity of this blueprint
         gets it. Learn's advice for privileged permissions: don't make them inheritable, so
         each one needs an explicit admin decision ("Inheritable permissions", scenario 3).

      3. The guardian's app-only AgentIdentity.EnableDisable.All is removed (locked decision 7: no
         shortcut around OBO). The guardian then holds no write permission of its own: a disable
         runs only with an approver's rights. Bicep cannot do this step, because it never deletes
         an assignment that is taken out of a file.

    The admin consents (oauth2PermissionGrants) are in setup/bicep/approver-delegation.bicep,
    because the Graph Bicep extension supports them. It has no Agent ID types (docs/infra.md),
    so the two blueprint settings are here.

    Run it as the blueprint's owner (owners may modify their blueprint), or as an
    Agent ID Administrator.

.PARAMETER TenantId
    The tenant to deploy to, for example <your-domain>.onmicrosoft.com.

.EXAMPLE
    ./setup/agent-id/Deploy-ApproverDelegation.ps1 -TenantId <your-domain>.onmicrosoft.com
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string] $TenantId
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot/AgentIdHelpers.ps1"

# AgentIdentityBlueprint.ReadWrite.All: the least privileged permissions for updating a blueprint
# cover only credentials and branding, not the api property (Graph API docs, "Update
# agentIdentityBlueprint"). It also covers the inheritable permissions.
# AppRoleAssignment.ReadWrite.All: the least privileged permission to delete an app role assignment
# (Graph API docs, "Delete appRoleAssignedTo").
$DelegationScopes = @('Application.Read.All', 'AgentIdentityBlueprint.ReadWrite.All', 'AppRoleAssignment.ReadWrite.All')

$BlueprintName       = 'agent-steward-guardian'
$ScopeValue          = 'access_as_user'
$MicrosoftGraphAppId = '00000003-0000-0000-c000-000000000000'   # a public constant
$GuardianName        = 'agent-steward-guardian-01'
$RemovedAppRole      = 'AgentIdentity.EnableDisable.All'        # decision 7: only the delegated one stays

Connect-AgentIdTenant -TenantId $TenantId -Scopes $DelegationScopes | Out-Null

$blueprint = Find-GraphObject -CollectionUri "$GraphV1/applications" -Filter "displayName eq '$BlueprintName'" |
    Where-Object { $_['@odata.type'] -eq '#microsoft.graph.agentIdentityBlueprint' } |
    Select-Object -First 1
if (-not $blueprint) { throw "Blueprint '$BlueprintName' not found. Run Deploy-AgentIdentities.ps1 first." }
$blueprintUri = "$GraphV1/applications/$($blueprint.id)/microsoft.graph.agentIdentityBlueprint"

# 1. The access_as_user scope, with v2 tokens and no App ID URI.
$appIdUri = "api://$($blueprint.appId)"
$uris     = @($blueprint.identifierUris | Where-Object { $_ })
$api      = $blueprint.api
$scopes   = @(if ($api -and $api.oauth2PermissionScopes) { $api.oauth2PermissionScopes })
$hasScope = @($scopes | Where-Object { $_.value -eq $ScopeValue }).Count -gt 0

if ($hasScope -and $api.requestedAccessTokenVersion -eq 2 -and $uris -notcontains $appIdUri) {
    Write-Step 'exists' "scope $ScopeValue"
}
else {
    # Keep any scope that already exists: Entra refuses to drop an enabled scope in one step.
    if (-not $hasScope) {
        $scopes += @{
            id                      = [guid]::NewGuid().ToString()
            value                   = $ScopeValue
            type                    = 'Admin'   # only an admin can consent; the consent is in Bicep
            isEnabled               = $true
            adminConsentDisplayName = 'Act for the approver on an approved fix'
            adminConsentDescription = 'Lets the agent-steward guardian disable an agent with the signed-in approver''s own rights, after the approver approves that exact action.'
        }
    }
    Invoke-Graph -Method PATCH -Uri $blueprintUri -Body @{
        identifierUris = @($uris | Where-Object { $_ -ne $appIdUri })
        api            = @{
            requestedAccessTokenVersion = 2   # v2: aud is the blueprint's app ID, as OBO for agents requires
            oauth2PermissionScopes      = $scopes
        }
    } | Out-Null
    Write-Step 'updated' "scope $ScopeValue"
}

# 2. Nothing from Microsoft Graph is inheritable.
$noScopes = @{ '@odata.type' = '#microsoft.graph.noScopes' }
$existing = @((Invoke-Graph -Method GET -Uri "$blueprintUri/inheritablePermissions").value |
    Where-Object { $_.resourceAppId -eq $MicrosoftGraphAppId })

if (-not $existing) {
    Invoke-Graph -Method POST -Uri "$blueprintUri/inheritablePermissions" -Body @{
        resourceAppId     = $MicrosoftGraphAppId
        inheritableScopes = $noScopes
    } | Out-Null
    Write-Step 'created' 'no inheritable Graph scopes'
}
elseif ($existing[0].inheritableScopes.kind -eq 'none') {
    Write-Step 'exists' 'no inheritable Graph scopes'
}
else {
    Invoke-Graph -Method PATCH -Uri "$blueprintUri/inheritablePermissions/$MicrosoftGraphAppId" -Body @{
        inheritableScopes = $noScopes
    } | Out-Null
    Write-Step 'updated' 'no inheritable Graph scopes'
}

# 3. The guardian's app-only write permission is removed.
$guardian = @(Find-GraphObject -CollectionUri "$GraphV1/servicePrincipals/microsoft.graph.agentIdentity" -Filter "displayName eq '$GuardianName'")
if (-not $guardian) { throw "Agent identity '$GuardianName' not found. Run Deploy-AgentIdentities.ps1 first." }
$graph  = @(Find-GraphObject -CollectionUri "$GraphV1/servicePrincipals" -Filter "appId eq '$MicrosoftGraphAppId'")[0]
$roleId = @($graph.appRoles | Where-Object { $_.value -eq $RemovedAppRole })[0].id
$assignments = @((Invoke-Graph -Method GET -Uri "$GraphV1/servicePrincipals/$($guardian[0].id)/appRoleAssignments").value |
    Where-Object { $_.resourceId -eq $graph.id -and $_.appRoleId -eq $roleId })
if (-not $assignments) {
    Write-Step 'exists' "no app-only $RemovedAppRole on $GuardianName"
}
foreach ($assignment in $assignments) {
    # Through the resource, as the Graph API docs recommend.
    Invoke-Graph -Method DELETE -Uri "$GraphV1/servicePrincipals/$($graph.id)/appRoleAssignedTo/$($assignment.id)" | Out-Null
    Write-Step 'removed' "app-only $RemovedAppRole from $GuardianName"
}

# The Bicep file needs the blueprint principal's object ID.
$principal = @(Find-GraphObject -CollectionUri "$GraphV1/servicePrincipals/microsoft.graph.agentIdentityBlueprintPrincipal" -Filter "appId eq '$($blueprint.appId)'" -Select 'id')
if (-not $principal) { throw "Blueprint principal for '$BlueprintName' not found. Run Deploy-AgentIdentities.ps1 first." }
Write-Host "Done. Next: setup/bicep/approver-delegation.bicep with blueprintPrincipalId=$($principal[0].id)"
