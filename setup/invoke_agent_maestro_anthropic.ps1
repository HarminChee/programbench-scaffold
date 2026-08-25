[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$utf8 = New-Object Text.UTF8Encoding($false)
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
$securityManifest = Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Security\Microsoft.PowerShell.Security.psd1'
if (-not (Test-Path -LiteralPath $securityManifest)) {
    throw 'Microsoft.PowerShell.Security module manifest is unavailable'
}
# Import the module from this PowerShell host's own PSHOME.  Inherited
# PSModulePath entries may contain a PowerShell 7 module that Windows
# PowerShell 5.1 can discover but cannot load.
Import-Module -Name $securityManifest -Force -ErrorAction Stop
$secretPath = Join-Path $env:APPDATA 'AgentMaestro\api-key.dpapi'
$encrypted = (Get-Content -Raw -LiteralPath $secretPath).Trim()
$secure = ConvertTo-SecureString -String $encrypted
$credential = [pscredential]::new('AgentMaestro', $secure)
$plainText = $credential.GetNetworkCredential().Password
if ($plainText -notmatch '^[0-9a-f]{64}$') {
    throw 'Invalid Maestro key format'
}

$body = [Console]::In.ReadToEnd()
try {
    $response = Invoke-RestMethod `
        -Method Post `
        -Headers @{ 'x-api-key' = $plainText } `
        -ContentType 'application/json' `
        -Body $body `
        -Uri 'http://127.0.0.1:23333/api/anthropic/v1/messages'
    $response | ConvertTo-Json -Depth 100 -Compress
}
finally {
    $plainText = $null
    $body = $null
}
