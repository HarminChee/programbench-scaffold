param(
    [Parameter(Mandatory = $true)][string]$BatchPrompt,
    [Parameter(Mandatory = $true)][string]$SourceContext,
    [Parameter(Mandatory = $true)][string]$OutputPath,
    [int]$MaxTokens = 12000,
    [string]$Model = 'claude-sonnet-5'
)

$ErrorActionPreference = 'Stop'
if (-not $env:AGENT_MAESTRO_API_KEY) { throw 'AGENT_MAESTRO_API_KEY is not present' }
$prompt = (Get-Content -Raw -LiteralPath $BatchPrompt) +
    "`n`n# Pinned source/docs/native-test context`n" +
    (Get-Content -Raw -LiteralPath $SourceContext)
$body = @{
    model = $Model
    max_tokens = $MaxTokens
    system = @'
You generate deterministic behavioral oracle candidates from pinned source,
documentation, native tests, and reference-binary probe design. Never use the
target ProgramBench official oracle tests. Each candidate must execute a real
stimulus and target observable behavior. Do not emit empty/no-op candidates or
exactly duplicate complete invocations. Input/state variations are allowed when
they exercise meaningful values or boundaries. Return only one JSON object with
a cases array and never invent expected output.
'@
    messages = @(@{ role = 'user'; content = $prompt })
} | ConvertTo-Json -Depth 14
$bytes = [Text.Encoding]::UTF8.GetBytes($body)
$response = Invoke-RestMethod -Method Post `
    -Uri 'http://127.0.0.1:23333/api/anthropic/v1/messages' `
    -Headers @{ 'x-api-key' = $env:AGENT_MAESTRO_API_KEY; 'Content-Type' = 'application/json; charset=utf-8' } `
    -Body $bytes
$text = (($response.content | Where-Object { $_.type -eq 'text' } | ForEach-Object { $_.text }) -join "`n")
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $OutputPath) | Out-Null
Set-Content -LiteralPath $OutputPath -Value $text -Encoding utf8
@{ model=$Model; actual_model=$response.model; status='completed'; output_path=$OutputPath } |
    ConvertTo-Json -Compress
