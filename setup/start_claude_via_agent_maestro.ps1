[CmdletBinding()]
param(
    [ValidateSet('sonnet', 'opus')]
    [string]$Model = 'sonnet',

    [string]$Prompt,

    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ClaudeArguments
)

$ErrorActionPreference = 'Stop'

$secretPath = Join-Path $env:APPDATA 'AgentMaestro\api-key.dpapi'
if (-not (Test-Path -LiteralPath $secretPath)) {
    throw "Agent Maestro DPAPI key was not found: $secretPath"
}

$encrypted = (Get-Content -Raw -LiteralPath $secretPath).Trim()
$secureString = ConvertTo-SecureString -String $encrypted
$credential = New-Object System.Management.Automation.PSCredential('AgentMaestro', $secureString)
$plainText = $credential.GetNetworkCredential().Password

$modelId = if ($Model -eq 'opus') {
    'claude-opus-4.8[1m]'
} else {
    'claude-sonnet-5[1m]'
}

$env:AGENT_MAESTRO_API_KEY = $plainText
$env:ANTHROPIC_BASE_URL = 'http://127.0.0.1:23333/api/anthropic'
$env:ANTHROPIC_API_KEY = $plainText
$env:ANTHROPIC_AUTH_TOKEN = $plainText
$env:ANTHROPIC_MODEL = $modelId
$env:CLAUDE_CODE_AUTO_COMPACT_WINDOW = '936000'
$env:CLAUDE_AUTOCOMPACT_PCT_OVERRIDE = '85'
$env:CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC = '1'
$env:CLAUDE_CODE_ATTRIBUTION_HEADER = '0'
$env:CLAUDE_CODE_USE_POWERSHELL_TOOL = '1'
$env:CLAUDE_CODE_GIT_BASH_PATH = 'C:\Program Files\Git\bin\bash.exe'

$claude = Join-Path $env:USERPROFILE '.local\bin\claude.exe'
if (-not (Test-Path -LiteralPath $claude)) {
    throw "Claude Code was not found: $claude"
}

if ($Prompt) {
    & $claude -p `
        --model $modelId `
        --permission-mode plan `
        --no-session-persistence `
        --output-format json `
        $Prompt
} elseif (-not $ClaudeArguments) {
    & $claude --model $modelId
} else {
    & $claude --model $modelId @ClaudeArguments
}

exit $LASTEXITCODE
