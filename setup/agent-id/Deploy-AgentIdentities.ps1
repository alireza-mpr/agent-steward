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

# Delegated permissions this script needs (least privileged per the Graph API docs).
$RequiredScopes = @(
    'User.ReadWrite.All'                    # create the stand-in sponsor user
    'AgentIdentityBlueprint.Create'         # create blueprints
    'Application.Read.All'                  # find existing blueprints and read their sponsors
    'AgentIdentityBlueprintPrincipal.Create'
    'AgentIdentity.Create.All'              # create agent identities (as the blueprint owner)
)

# Kind -> instances. More agents (worker, fixtures) are added here in later steps.
$Agents = @(
    @{ Blueprint = 'agent-steward-guardian'; Identities = @('agent-steward-guardian-01') }
)

$SponsorNickname    = 'agent.sponsor'
$SponsorDisplayName = 'Agent Sponsor (Admin stand-in)'

$GraphV1      = 'https://graph.microsoft.com/v1.0'
$GraphBeta    = 'https://graph.microsoft.com/beta'
$ODataHeaders = @{ 'OData-Version' = '4.0' }   # required when a body uses @odata.type

function Invoke-Graph {
    param(
        [Parameter(Mandatory)] [string] $Method,
        [Parameter(Mandatory)] [string] $Uri,
        [hashtable] $Body
    )
    $request = @{ Method = $Method; Uri = $Uri; Headers = $ODataHeaders }
    if ($Body) {
        $request.Body        = $Body | ConvertTo-Json -Depth 5
        $request.ContentType = 'application/json'
    }
    Invoke-MgGraphRequest @request
}

function Find-GraphObject {
    param(
        [Parameter(Mandatory)] [string] $CollectionUri,
        [Parameter(Mandatory)] [string] $Filter
    )
    $uri = "$CollectionUri`?`$filter=$([uri]::EscapeDataString($Filter))"
    @((Invoke-Graph -Method GET -Uri $uri).value)
}

# New directory objects can take a few seconds to become visible to the next call.
function Invoke-WithRetry {
    param([Parameter(Mandatory)] [scriptblock] $Action)
    for ($attempt = 1; ; $attempt++) {
        try { return & $Action }
        catch {
            if ($attempt -ge 5) { throw }
            $delay = 5 * $attempt
            Write-Host "  waiting $delay s for the directory to catch up (attempt $attempt)..."
            Start-Sleep -Seconds $delay
        }
    }
}

function Write-Step {
    param([string] $Status, [string] $What)
    Write-Host ("  [{0}] {1}" -f $Status, $What)
}

function Set-SponsorUser {
    param([Parameter(Mandatory)] [string] $Domain)
    # upn: User Principal Name
    $upn = "$SponsorNickname@$Domain"
    # @() keeps a one-item result a list: PowerShell unrolls arrays returned from functions.
    $existing = @(Find-GraphObject -CollectionUri "$GraphV1/users" -Filter "userPrincipalName eq '$upn'")
    if ($existing) {
        $user = $existing[0]
        if ($user.displayName -ne $SponsorDisplayName) {
            Invoke-Graph -Method PATCH -Uri "$GraphV1/users/$($user.id)" -Body @{ displayName = $SponsorDisplayName } | Out-Null
            Write-Step 'updated' "sponsor user renamed: $($user.displayName) -> $SponsorDisplayName"
        }
        else {
            Write-Step 'exists' "sponsor user $SponsorDisplayName"
        }
        return $user
    }

    # Random and never shown or stored: the stand-in sponsor never signs in.
    # An admin can reset it in the portal if that ever changes.
    $bytes = [byte[]]::new(24)
    [System.Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
    $user = Invoke-Graph -Method POST -Uri "$GraphV1/users" -Body @{
        accountEnabled    = $true
        displayName       = $SponsorDisplayName
        mailNickname      = $SponsorNickname
        userPrincipalName = $upn
        passwordProfile   = @{
            forceChangePasswordNextSignIn = $true
            password                      = "$([Convert]::ToBase64String($bytes))Aa1!"
        }
    }
    Write-Step 'created' "sponsor user $SponsorDisplayName"
    $user
}

function Set-Blueprint {
    param(
        [Parameter(Mandatory)] [string] $Name,
        [Parameter(Mandatory)] [string] $OwnerId,
        [Parameter(Mandatory)] [string] $SponsorId
    )
    $blueprint = Find-GraphObject -CollectionUri "$GraphV1/applications" -Filter "displayName eq '$Name'" |
        Where-Object { $_['@odata.type'] -eq '#microsoft.graph.agentIdentityBlueprint' } |
        Select-Object -First 1

    if (-not $blueprint) {
        $blueprint = Invoke-Graph -Method POST -Uri "$GraphV1/applications" -Body @{
            '@odata.type'         = 'Microsoft.Graph.AgentIdentityBlueprint'
            displayName           = $Name
            'sponsors@odata.bind' = @("$GraphV1/users/$SponsorId")
            'owners@odata.bind'   = @("$GraphV1/users/$OwnerId")
        }
        Write-Step 'created' "blueprint $Name"
        return $blueprint
    }

    # Sponsors are set only at creation. Changing them later returned 403 even with the
    # permission the API docs list (Application.ReadWrite.All), so here we only check.
    Write-Step 'exists' "blueprint $Name"
    $sponsorsUri = "$GraphV1/applications/$($blueprint.id)/microsoft.graph.agentIdentityBlueprint/sponsors"
    $sponsorIds  = @((Invoke-Graph -Method GET -Uri $sponsorsUri).value | ForEach-Object { $_.id })
    if ($sponsorIds -notcontains $SponsorId) {
        Write-Warning "Blueprint '$Name' exists without '$SponsorDisplayName' as sponsor. Delete it and run again to recreate it."
    }
    $blueprint
}

function Set-BlueprintPrincipal {
    param(
        [Parameter(Mandatory)] [string] $Name,
        [Parameter(Mandatory)] [string] $AppId
    )
    $principalsUri = "$GraphV1/servicePrincipals/microsoft.graph.agentIdentityBlueprintPrincipal"
    if (Find-GraphObject -CollectionUri $principalsUri -Filter "appId eq '$AppId'") {
        Write-Step 'exists' "blueprint principal $Name"
        return
    }
    Invoke-WithRetry { Invoke-Graph -Method POST -Uri $principalsUri -Body @{ appId = $AppId } } | Out-Null
    Write-Step 'created' "blueprint principal $Name"
}

function Set-AgentIdentity {
    param(
        [Parameter(Mandatory)] [string] $Name,
        [Parameter(Mandatory)] [string] $BlueprintAppId,
        [Parameter(Mandatory)] [string] $OwnerId,
        [Parameter(Mandatory)] [string] $SponsorId
    )
    $existing = @(Find-GraphObject -CollectionUri "$GraphV1/servicePrincipals/microsoft.graph.agentIdentity" -Filter "displayName eq '$Name'")
    if ($existing) {
        if ($existing[0].agentIdentityBlueprintId -ne $BlueprintAppId) {
            throw "Agent identity '$Name' exists but belongs to a different blueprint."
        }
        Write-Step 'exists' "agent identity $Name"
        return
    }
    # The Learn how-to (August 2026) shows this call on the beta endpoint.
    Invoke-WithRetry {
        Invoke-Graph -Method POST -Uri "$GraphBeta/serviceprincipals/Microsoft.Graph.AgentIdentity" -Body @{
            '@odata.type'            = '#Microsoft.Graph.AgentIdentity'
            displayName              = $Name
            agentIdentityBlueprintId = $BlueprintAppId
            'sponsors@odata.bind'    = @("$GraphV1/users/$SponsorId")
            'owners@odata.bind'      = @("$GraphV1/users/$OwnerId")
        }
    } | Out-Null
    Write-Step 'created' "agent identity $Name"
}

# --- main ---

Connect-MgGraph -TenantId $TenantId -Scopes $RequiredScopes -NoWelcome
$admin  = Invoke-Graph -Method GET -Uri "$GraphV1/me"
$domain = $admin.userPrincipalName.Split('@')[1]
Write-Host "Deploying Agent ID objects as $($admin.displayName)"

$sponsor = Set-SponsorUser -Domain $domain

foreach ($agent in $Agents) {
    $blueprint = Set-Blueprint -Name $agent.Blueprint -OwnerId $admin.id -SponsorId $sponsor.id
    Set-BlueprintPrincipal -Name $agent.Blueprint -AppId $blueprint.appId
    foreach ($identity in $agent.Identities) {
        Set-AgentIdentity -Name $identity -BlueprintAppId $blueprint.appId -OwnerId $admin.id -SponsorId $sponsor.id
    }
}

Write-Host 'Done.'
