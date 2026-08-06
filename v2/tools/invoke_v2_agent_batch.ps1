param(
    [Parameter(Mandatory = $true)][string]$BatchPrompt,
    [Parameter(Mandatory = $true)][string]$SourceExcerpt,
    [Parameter(Mandatory = $true)][string]$OutputPath,
    [int]$MaxTokens = 6000,
    [string]$Model = 'claude-sonnet-5'
)

$ErrorActionPreference = 'Stop'
if (-not $env:AGENT_MAESTRO_API_KEY) { throw 'AGENT_MAESTRO_API_KEY is not present in this process' }
$prompt = (Get-Content -Raw -LiteralPath $BatchPrompt) + "`n`n# Source-only context`n" + (Get-Content -Raw -LiteralPath $SourceExcerpt)
$body = @{
    model = $Model
    max_tokens = $MaxTokens
    system = 'You generate deterministic behavioral CLI oracle candidates. Never use target ProgramBench official oracle tests. Return only one JSON object with a cases array.'
    messages = @(@{ role = 'user'; content = $prompt })
} | ConvertTo-Json -Depth 12
# PowerShell 5 can calculate an incorrect byte length when a JSON string
# contains non-ASCII source excerpts. Send explicit UTF-8 bytes so Maestro
# receives exactly the JSON validated locally.
$null = $body | ConvertFrom-Json
$bodyBytes = [Text.Encoding]::UTF8.GetBytes($body)
$response = Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:23333/api/anthropic/v1/messages' -Headers @{ 'x-api-key' = $env:AGENT_MAESTRO_API_KEY; 'Content-Type' = 'application/json; charset=utf-8' } -Body $bodyBytes
$text = (($response.content | Where-Object { $_.type -eq 'text' } | ForEach-Object { $_.text }) -join "`n")
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $OutputPath) | Out-Null
Set-Content -LiteralPath $OutputPath -Value $text -Encoding utf8
@{ model = $Model; status = 'completed'; output_path = $OutputPath } | ConvertTo-Json -Compress
