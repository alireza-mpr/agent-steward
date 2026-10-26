#Requires -Version 7.0
#Requires -Modules Microsoft.Graph.Authentication

<#
.SYNOPSIS
    Gives a blueprint a certificate credential: creates the key pair on this machine
    and uploads only the public certificate to the blueprint.

.DESCRIPTION
    Safe to run again. Writes three files to secrets/ (ignored by git):
      <blueprint>.pfx          private key + certificate, password-protected. Only the sidecar mounts it.
      <blueprint>.crt          the public certificate: the half that Entra holds. Not a secret.
      <blueprint>.sidecar.env  the PFX password, as the sidecar's environment variable.

    The private key never goes to Entra or the repo (FR10).

    This script adds the first certificate only. A later rotation needs the addKey
    action with a proof signed by the current key (see docs/infra.md).

    Needs OpenSSL on the PATH. On macOS: brew install openssl.

.PARAMETER TenantId
    The tenant to deploy to, for example <your-domain>.onmicrosoft.com.

.PARAMETER BlueprintName
    The blueprint that gets the certificate. It must already exist (Deploy-AgentIdentities.ps1).

.PARAMETER LifetimeDays
    Lifetime of the certificate. The lab needs only a few weeks.

.EXAMPLE
    ./setup/agent-id/Deploy-BlueprintCertificate.ps1 -TenantId <your-domain>.onmicrosoft.com
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string] $TenantId,

    [string] $BlueprintName = 'agent-steward-guardian',

    [ValidateRange(1, 90)]
    [int] $LifetimeDays = 45
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot/AgentIdHelpers.ps1"

$CertScopes = @(
    'User.Read'                                   # read the signed-in admin
    'Application.Read.All'                        # find the blueprint and read its key credentials
    'AgentIdentityBlueprint.AddRemoveCreds.All'   # add the certificate (least privileged)
)

$SecretsDir = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../secrets'))
$PfxPath    = Join-Path $SecretsDir "$BlueprintName.pfx"
$CrtPath    = Join-Path $SecretsDir "$BlueprintName.crt"
$EnvPath    = Join-Path $SecretsDir "$BlueprintName.sidecar.env"

function Read-CertificateBase64 {
    param([Parameter(Mandatory)] [string] $Path)
    $pem = Get-Content -Raw -Path $Path
    ($pem -replace '-----(BEGIN|END) CERTIFICATE-----', '') -replace '\s', ''
}

# SHA-1 of the DER bytes, in upper-case hex: the thumbprint Entra shows.
function Get-Thumbprint {
    param([Parameter(Mandatory)] [string] $Base64)
    $hash = [System.Security.Cryptography.SHA1]::Create().ComputeHash([Convert]::FromBase64String($Base64))
    -join ($hash | ForEach-Object { $_.ToString('X2') })
}

# This script names each key "<blueprint> <thumbprint>". Graph also stores the
# thumbprint in customKeyIdentifier (base64), so check both.
function Test-KeyHasThumbprint {
    param(
        [Parameter(Mandatory)] $KeyCredential,
        [Parameter(Mandatory)] [string] $Thumbprint
    )
    if ("$($KeyCredential.displayName)" -like "*$Thumbprint*") { return $true }
    if ($KeyCredential.customKeyIdentifier) {
        try {
            $bytes = [Convert]::FromBase64String($KeyCredential.customKeyIdentifier)
            return (-join ($bytes | ForEach-Object { $_.ToString('X2') })) -eq $Thumbprint
        }
        catch { return $false }
    }
    $false
}

function New-CertificateFiles {
    if (-not (Get-Command openssl -ErrorAction SilentlyContinue)) {
        throw 'OpenSSL is not on the PATH. On macOS: brew install openssl.'
    }
    New-Item -ItemType Directory -Force -Path $SecretsDir | Out-Null

    # The unencrypted private key exists only in this folder, and only until the PFX is built.
    $work = Join-Path $SecretsDir ".tmp-$([guid]::NewGuid().ToString('N'))"
    New-Item -ItemType Directory -Path $work | Out-Null
    if (-not $IsWindows) { chmod 700 $work }
    try {
        $keyPath = Join-Path $work 'key.pem'
        openssl req -x509 -newkey rsa:2048 -sha256 -nodes -quiet -days $LifetimeDays `
            -subj "/CN=$BlueprintName" -keyout $keyPath -out $CrtPath
        if ($LASTEXITCODE) { throw 'OpenSSL could not create the key pair.' }

        $bytes = [byte[]]::new(32)
        [System.Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
        $password = -join ($bytes | ForEach-Object { $_.ToString('x2') })

        # Passed through the environment, so the password never shows in a process list.
        $env:PFX_PASSWORD = $password
        try {
            openssl pkcs12 -export -inkey $keyPath -in $CrtPath -out $PfxPath `
                -keypbe AES-256-CBC -certpbe AES-256-CBC -macalg SHA256 -passout env:PFX_PASSWORD
            if ($LASTEXITCODE) { throw 'OpenSSL could not build the PFX file.' }
        }
        finally {
            Remove-Item Env:PFX_PASSWORD -ErrorAction SilentlyContinue
        }

        Set-Content -Path $EnvPath -Value "AzureAd__ClientCredentials__0__CertificatePassword=$password"
        if (-not $IsWindows) { chmod 600 $PfxPath $EnvPath }
    }
    finally {
        Remove-Item -Recurse -Force -Path $work
    }
    Write-Step 'created' "key pair in $SecretsDir"
}

function Add-BlueprintCertificate {
    param(
        [Parameter(Mandatory)] $Blueprint,
        [Parameter(Mandatory)] [string] $Base64,
        [Parameter(Mandatory)] [string] $Thumbprint
    )
    # PATCH replaces the whole keyCredentials list. The caller has checked that the list
    # is empty, so nothing is lost. (addKey cannot add a first key: it needs a proof
    # signed by an existing one.)
    Invoke-Graph -Method PATCH -Uri "$GraphV1/applications/$($Blueprint.id)/microsoft.graph.agentIdentityBlueprint" -Body @{
        keyCredentials = @(@{
            type        = 'AsymmetricX509Cert'
            usage       = 'Verify'
            key         = $Base64
            displayName = "$BlueprintName $Thumbprint"
        })
    } | Out-Null
    Write-Step 'created' "certificate on blueprint $BlueprintName"
}

Connect-AgentIdTenant -TenantId $TenantId -Scopes $CertScopes | Out-Null

$blueprint = Find-GraphObject -CollectionUri "$GraphV1/applications" -Filter "displayName eq '$BlueprintName'" |
    Where-Object { $_['@odata.type'] -eq '#microsoft.graph.agentIdentityBlueprint' } |
    Select-Object -First 1
if (-not $blueprint) {
    throw "Blueprint '$BlueprintName' not found. Run Deploy-AgentIdentities.ps1 first."
}

$keysUri = "$GraphV1/applications/$($blueprint.id)?`$select=keyCredentials"
$keys    = @((Invoke-Graph -Method GET -Uri $keysUri).keyCredentials | Where-Object { $_ })

if ((Test-Path $PfxPath) -and (Test-Path $CrtPath) -and (Test-Path $EnvPath)) {
    $base64     = Read-CertificateBase64 -Path $CrtPath
    $thumbprint = Get-Thumbprint -Base64 $base64
    if ($keys | Where-Object { Test-KeyHasThumbprint -KeyCredential $_ -Thumbprint $thumbprint }) {
        Write-Step 'exists' "certificate $thumbprint on blueprint $BlueprintName"
    }
    elseif ($keys) {
        throw "Blueprint '$BlueprintName' has a different certificate than the one in $SecretsDir. This script does not rotate certificates."
    }
    else {
        # A run that stopped after making the files but before the upload.
        Add-BlueprintCertificate -Blueprint $blueprint -Base64 $base64 -Thumbprint $thumbprint
    }
}
else {
    if ($keys) {
        throw "Blueprint '$BlueprintName' already has a certificate, but its private key is not in $SecretsDir. Remove that key credential from the blueprint first, then run again."
    }
    New-CertificateFiles
    $base64     = Read-CertificateBase64 -Path $CrtPath
    $thumbprint = Get-Thumbprint -Base64 $base64
    Add-BlueprintCertificate -Blueprint $blueprint -Base64 $base64 -Thumbprint $thumbprint
}

$key = Invoke-WithRetry {
    $match = @((Invoke-Graph -Method GET -Uri $keysUri).keyCredentials |
        Where-Object { $_ -and (Test-KeyHasThumbprint -KeyCredential $_ -Thumbprint $thumbprint) })
    if (-not $match) { throw 'The certificate does not show on the blueprint yet.' }
    $match[0]
}

Write-Host "Thumbprint: $thumbprint"
Write-Host "Expires:    $($key.endDateTime)"
Write-Host 'Done.'
