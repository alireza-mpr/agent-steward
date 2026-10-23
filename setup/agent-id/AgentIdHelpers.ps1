# Shared helpers for the Agent ID setup scripts. Dot-source it: . "$PSScriptRoot/AgentIdHelpers.ps1"
# Every Set-* function is safe to run again: it looks the object up and creates it only if missing.

$GraphV1      = 'https://graph.microsoft.com/v1.0'
$GraphBeta    = 'https://graph.microsoft.com/beta'
$ODataHeaders = @{ 'OData-Version' = '4.0' }   # required when a body uses @odata.type

# Delegated permissions every setup script needs (least privileged per the Graph API docs).
$BaseScopes = @(
    'User.ReadWrite.All'                    # create stand-in users
    'AgentIdentityBlueprint.Create'         # create blueprints
    'Application.Read.All'                  # find existing blueprints and read their sponsors
    'AgentIdentityBlueprintPrincipal.Create'
    'AgentIdentity.Create.All'              # create agent identities (as the blueprint owner)
)

# The business sponsor of the project's agents (FR2). A lab stand-in, not a real person.
$StandInSponsor = @{ Nickname = 'agent.sponsor'; DisplayName = 'Agent Sponsor (Admin stand-in)' }

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

# Returns a list. Callers wrap the call in @(...) because PowerShell unrolls
# arrays returned from functions: a one-item list would come back as the item.
function Find-GraphObject {
    param(
        [Parameter(Mandatory)] [string] $CollectionUri,
        [Parameter(Mandatory)] [string] $Filter,
        [string] $Select
    )
    $uri = "$CollectionUri`?`$filter=$([uri]::EscapeDataString($Filter))"
    if ($Select) { $uri += "&`$select=$Select" }
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

function Connect-AgentIdTenant {
    param(
        [Parameter(Mandatory)] [string] $TenantId,
        [Parameter(Mandatory)] [string[]] $Scopes
    )
    Connect-MgGraph -TenantId $TenantId -Scopes $Scopes -NoWelcome
    $admin = Invoke-Graph -Method GET -Uri "$GraphV1/me"
    Write-Host "Signed in as $($admin.displayName)"
    $admin
}

function Set-StandInUser {
    param(
        [Parameter(Mandatory)] [string] $Nickname,
        [Parameter(Mandatory)] [string] $DisplayName,
        [Parameter(Mandatory)] [string] $Domain
    )
    # upn: User Principal Name
    $upn = "$Nickname@$Domain"
    $existing = @(Find-GraphObject -CollectionUri "$GraphV1/users" -Filter "userPrincipalName eq '$upn'")
    if ($existing) {
        $user = $existing[0]
        if ($user.displayName -ne $DisplayName) {
            Invoke-Graph -Method PATCH -Uri "$GraphV1/users/$($user.id)" -Body @{ displayName = $DisplayName } | Out-Null
            Write-Step 'updated' "user renamed: $($user.displayName) -> $DisplayName"
        }
        else {
            Write-Step 'exists' "user $DisplayName"
        }
        return $user
    }

    # Random and never shown or stored: stand-in users never sign in.
    # An admin can reset the password in the portal if that ever changes.
    $bytes = [byte[]]::new(24)
    [System.Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
    $user = Invoke-Graph -Method POST -Uri "$GraphV1/users" -Body @{
        accountEnabled    = $true
        displayName       = $DisplayName
        mailNickname      = $Nickname
        userPrincipalName = $upn
        passwordProfile   = @{
            forceChangePasswordNextSignIn = $true
            password                      = "$([Convert]::ToBase64String($bytes))Aa1!"
        }
    }
    Write-Step 'created' "user $DisplayName"
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
        Write-Warning "Blueprint '$Name' exists without the expected sponsor. Delete it and run again to recreate it."
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

# Returns the agent identity, existing or new.
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
        return $existing[0]
    }
    # The Learn how-to (August 2026) shows this call on the beta endpoint.
    $identity = Invoke-WithRetry {
        Invoke-Graph -Method POST -Uri "$GraphBeta/serviceprincipals/Microsoft.Graph.AgentIdentity" -Body @{
            '@odata.type'            = '#Microsoft.Graph.AgentIdentity'
            displayName              = $Name
            agentIdentityBlueprintId = $BlueprintAppId
            'sponsors@odata.bind'    = @("$GraphV1/users/$SponsorId")
            'owners@odata.bind'      = @("$GraphV1/users/$OwnerId")
        }
    }
    Write-Step 'created' "agent identity $Name"
    $identity
}
