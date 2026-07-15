[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Model,

    [Parameter(Mandatory = $true)]
    [int]$MaxTurns,

    [Parameter(Mandatory = $true)]
    [string]$Tools,

    [Parameter(Mandatory = $true)]
    [string]$AllowedTools,

    [Parameter(Mandatory = $true)]
    [string]$JsonSchemaBase64,

    [Parameter(Mandatory = $true)]
    [string]$WorkingDirectory
)

$ErrorActionPreference = 'Stop'
$utf8 = New-Object Text.UTF8Encoding($false)
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
$secretPath = Join-Path $env:APPDATA 'AgentMaestro\api-key.dpapi'
if (-not (Test-Path -LiteralPath $secretPath)) {
    throw "Agent Maestro DPAPI key was not found: $secretPath"
}

$encrypted = (Get-Content -Raw -LiteralPath $secretPath).Trim()
$secure = ConvertTo-SecureString -String $encrypted
$credential = [pscredential]::new('AgentMaestro', $secure)
$plainText = $credential.GetNetworkCredential().Password
if ($plainText -notmatch '^[0-9a-f]{64}$') {
    throw 'Invalid Maestro key format'
}

$claude = Join-Path $env:USERPROFILE '.local\bin\claude.exe'
if (-not (Test-Path -LiteralPath $claude)) {
    throw "Claude Code was not found: $claude"
}

$prompt = [Console]::In.ReadToEnd()
$jsonSchema = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($JsonSchemaBase64))
$escapedJsonSchema = $jsonSchema.Replace('"', '\"')
$mirrorRoot = Join-Path $env:LOCALAPPDATA 'ProgramBenchAgent'
$mirror = Join-Path $mirrorRoot ("agent-{0}-{1}" -f $PID, [guid]::NewGuid().ToString('N'))
$resolvedMirrorRoot = [IO.Path]::GetFullPath($mirrorRoot).TrimEnd('\') + '\'
$resolvedMirror = [IO.Path]::GetFullPath($mirror)
if (-not $resolvedMirror.StartsWith($resolvedMirrorRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Refusing to create an agent mirror outside the intended root'
}

New-Item -ItemType Directory -Path $mirror -Force | Out-Null
$null = & robocopy.exe $WorkingDirectory $mirror /E /XD (Join-Path $WorkingDirectory 'reference') (Join-Path $WorkingDirectory '.git') /NFL /NDL /NJH /NJS /NC /NS
if ($LASTEXITCODE -ge 8) {
    throw "Failed to create Windows agent mirror (robocopy exit $LASTEXITCODE)"
}
try {
    $env:AGENT_MAESTRO_API_KEY = $plainText
    $env:ANTHROPIC_BASE_URL = 'http://127.0.0.1:23333/api/anthropic'
    $env:ANTHROPIC_API_KEY = $plainText
    $env:ANTHROPIC_AUTH_TOKEN = $plainText
    $env:CLAUDE_CODE_AUTO_COMPACT_WINDOW = '936000'
    $env:CLAUDE_AUTOCOMPACT_PCT_OVERRIDE = '85'
    $env:CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC = '1'
    $env:CLAUDE_CODE_ATTRIBUTION_HEADER = '0'
    $env:CLAUDE_CODE_USE_POWERSHELL_TOOL = '1'
    $env:CLAUDE_CODE_GIT_BASH_PATH = 'C:\Program Files\Git\bin\bash.exe'
    $env:PROGRAMBENCH_REFERENCE_WORKSPACE_WINDOWS = $WorkingDirectory
    $env:PROGRAMBENCH_AGENT_MIRROR_WINDOWS = $mirror

    Push-Location -LiteralPath $mirror
    try {
        $prompt | & $claude -p `
            --output-format json `
            --permission-mode dontAsk `
            --no-session-persistence `
            --max-turns $MaxTurns `
            --model $Model `
            --tools $Tools `
            --allowedTools $AllowedTools `
            --json-schema $escapedJsonSchema
        $exitCode = $LASTEXITCODE
    }
    finally {
        Pop-Location
    }

    $candidate = Join-Path $mirror 'candidate_cases.json'
    if (Test-Path -LiteralPath $candidate) {
        if ($WorkingDirectory -notmatch '^\\\\wsl(?:\.localhost|\$)\\([^\\]+)\\(.*)$') {
            throw 'Original agent workspace is not a WSL UNC path'
        }
        $distro = $Matches[1]
        $linuxRoot = '/' + ($Matches[2] -replace '\\', '/')
        if ($candidate -notmatch '^([A-Za-z]):\\(.*)$') {
            throw 'Windows mirror candidate path is not drive-qualified'
        }
        $drive = $Matches[1].ToLowerInvariant()
        $linuxCandidate = "/mnt/$drive/" + ($Matches[2] -replace '\\', '/')
        & wsl.exe -d $distro -- cp -- $linuxCandidate "$linuxRoot/candidate_cases.json"
        if ($LASTEXITCODE -ne 0) {
            throw 'Failed to sync candidate_cases.json back to the Linux workspace'
        }
    }
}
finally {
    $plainText = $null
    $prompt = $null
    $jsonSchema = $null
    $escapedJsonSchema = $null
    $env:AGENT_MAESTRO_API_KEY = $null
    $env:ANTHROPIC_API_KEY = $null
    $env:ANTHROPIC_AUTH_TOKEN = $null
    $env:ANTHROPIC_BASE_URL = $null
    $env:PROGRAMBENCH_REFERENCE_WORKSPACE_WINDOWS = $null
    $env:PROGRAMBENCH_AGENT_MIRROR_WINDOWS = $null
    if (Test-Path -LiteralPath $resolvedMirror) {
        if (-not $resolvedMirror.StartsWith($resolvedMirrorRoot, [StringComparison]::OrdinalIgnoreCase)) {
            throw 'Refusing to remove an agent mirror outside the intended root'
        }
        Remove-Item -LiteralPath $resolvedMirror -Recurse -Force
    }
}

exit $exitCode
