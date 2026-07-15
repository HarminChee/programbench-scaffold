[CmdletBinding()]
param(
    [switch]$RevealToClipboard
)

$ErrorActionPreference = 'Stop'

$secretDir = Join-Path $env:APPDATA 'AgentMaestro'
$secretPath = Join-Path $secretDir 'api-key.dpapi'

if (-not (Test-Path -LiteralPath $secretDir)) {
    New-Item -ItemType Directory -Path $secretDir | Out-Null
}

if (-not (Test-Path -LiteralPath $secretPath)) {
    $bytes = New-Object byte[] 32
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $rng.GetBytes($bytes)
    } finally {
        $rng.Dispose()
    }

    $plainText = -join ($bytes | ForEach-Object { $_.ToString('x2') })
    $secureString = ConvertTo-SecureString -String $plainText -AsPlainText -Force
    $encrypted = ConvertFrom-SecureString -SecureString $secureString
    Set-Content -LiteralPath $secretPath -Value $encrypted -Encoding ascii
}

if ($RevealToClipboard) {
    $encrypted = (Get-Content -Raw -LiteralPath $secretPath).Trim()
    $secureString = ConvertTo-SecureString -String $encrypted
    $credential = New-Object System.Management.Automation.PSCredential('AgentMaestro', $secureString)
    $plainText = $credential.GetNetworkCredential().Password
    Set-Clipboard -Value $plainText
}

Write-Output "Agent Maestro key present: $(Test-Path -LiteralPath $secretPath)"
