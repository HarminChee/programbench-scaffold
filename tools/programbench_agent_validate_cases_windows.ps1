[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$workspace = $env:PROGRAMBENCH_AGENT_MIRROR_WINDOWS
if ([string]::IsNullOrWhiteSpace($workspace)) {
    throw 'PROGRAMBENCH_AGENT_MIRROR_WINDOWS is not set'
}
$path = Join-Path $workspace 'candidate_cases.json'
$payload = Get-Content -Raw -LiteralPath $path | ConvertFrom-Json
$rawCases = @($payload.cases)
$names = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
$signatures = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
$valid = 0
$invalid = 0
$duplicateNames = 0
$duplicateInputs = 0

foreach ($raw in $rawCases) {
    if ($null -eq $raw -or $raw -isnot [pscustomobject]) {
        $invalid += 1
        continue
    }
    $argsList = if ($null -eq $raw.args) { @() } else { @($raw.args) }
    if (@($argsList | Where-Object { $_ -isnot [string] -or $_.Contains([char]0) }).Count -gt 0) {
        $invalid += 1
        continue
    }
    $name = [regex]::Replace([string]$raw.name, '[^A-Za-z0-9_]+', '_')
    if ($name.Length -gt 120) { $name = $name.Substring(0, 120) }
    if ([string]::IsNullOrEmpty($name) -or -not $names.Add($name)) {
        $duplicateNames += 1
        continue
    }
    $stdin = if ($null -eq $raw.stdin) { '' } else { [string]$raw.stdin }
    if ($stdin.Length -gt 256000) { $stdin = $stdin.Substring(0, 256000) }
    $caseEnv = [ordered]@{}
    if ($raw.env -is [pscustomobject]) {
        foreach ($property in $raw.env.PSObject.Properties | Select-Object -First 32) {
            $key = [string]$property.Name
            $upper = $key.ToUpperInvariant()
            $text = [string]$property.Value
            $blockedName = $upper -in @('PATH', 'HOME', 'PWD', 'OLDPWD', 'SHELL', 'USER', 'LOGNAME', 'TMPDIR')
            $blockedMarker = $upper -match 'SECRET|TOKEN|PASSWORD|CREDENTIAL|API_KEY' -or $upper.StartsWith('LD_') -or $upper.StartsWith('DYLD_')
            if ($key -match '^[A-Za-z_][A-Za-z0-9_]{0,63}$' -and -not $blockedName -and -not $blockedMarker -and -not $text.Contains([char]0) -and $text.Length -le 4096) {
                $caseEnv[$key] = $text
            }
        }
    }
    $caseFiles = [ordered]@{}
    $fileBytes = 0
    if ($raw.files -is [pscustomobject]) {
        foreach ($property in $raw.files.PSObject.Properties | Select-Object -First 16) {
            $relative = ([string]$property.Name) -replace '\\', '/'
            $content = [string]$property.Value
            $size = [Text.Encoding]::UTF8.GetByteCount($content)
            if ($relative -notmatch '^/' -and $relative -notmatch '(^|/)\.\.(/|$)' -and $relative -notmatch '(^|/)\.(/|$)' -and $size -le 256000 -and ($fileBytes + $size) -le 1000000) {
                $caseFiles[$relative] = $content
                $fileBytes += $size
            }
        }
    }
    $caseHttp = [ordered]@{}
    if ($raw.http -is [pscustomobject]) {
        $httpPath = [string]$raw.http.path
        $httpStatus = if ($null -eq $raw.http.status) { 200 } else { [int]$raw.http.status }
        $httpBody = [string]$raw.http.body
        if ($httpPath.StartsWith('/') -and $httpPath -notmatch '[\r\n]' -and $httpStatus -ge 100 -and $httpStatus -le 599 -and [Text.Encoding]::UTF8.GetByteCount($httpBody) -le 256000) {
            $caseHttp.path = $httpPath
            $caseHttp.status = $httpStatus
            $caseHttp.body = $httpBody
            $caseHttp.headers = if ($raw.http.headers -is [pscustomobject]) { $raw.http.headers } else { [ordered]@{} }
        }
    }
    $signature = (@{
        args = @($argsList | Select-Object -First 100)
        stdin = $stdin
        env = $caseEnv
        files = $caseFiles
        http = $caseHttp
        stdout_mode = if ([string]$raw.stdout_mode -eq 'lines_unordered') { 'lines_unordered' } else { 'exact' }
    } | ConvertTo-Json -Depth 10 -Compress)
    if (-not $signatures.Add($signature)) {
        $duplicateInputs += 1
        continue
    }
    $valid += 1
}

[pscustomobject]@{
    raw_count = $rawCases.Count
    valid_unique_count = $valid
    invalid_count = $invalid
    duplicate_name_count = $duplicateNames
    duplicate_input_count = $duplicateInputs
} | ConvertTo-Json -Compress
