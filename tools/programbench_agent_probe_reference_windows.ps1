[CmdletBinding()]
param(
    [Alias('args-json')]
    [string]$ArgsJson = '[]',

    [Alias('stdin-text')]
    [string]$StdinText = '',

    [Alias('stdin-file')]
    [string]$StdinFile,

    [Alias('env-json')]
    [string]$EnvJson = '{}',

    [Alias('files-json')]
    [string]$FilesJson = '{}',

    [Alias('http-json')]
    [string]$HttpJson = '{}',

    [double]$Timeout = 10.0
)

$ErrorActionPreference = 'Stop'
$root = $env:PROGRAMBENCH_REFERENCE_WORKSPACE_WINDOWS
if ([string]::IsNullOrWhiteSpace($root)) {
    throw 'PROGRAMBENCH_REFERENCE_WORKSPACE_WINDOWS is not set'
}
if ($root -notmatch '^\\\\wsl(?:\.localhost|\$)\\([^\\]+)\\(.*)$') {
    throw 'Windows reference probe must run from a WSL UNC workspace'
}
$distro = $Matches[1]
$linuxRoot = '/' + ($Matches[2] -replace '\\', '/')
$linuxScript = "$linuxRoot/agent_tools/probe_reference.py"
$arguments = @(
    '-d', $distro, '--', 'python3', $linuxScript,
    '--args-json', $ArgsJson,
    '--env-json', $EnvJson,
    '--files-json', $FilesJson,
    '--http-json', $HttpJson,
    '--timeout', [string]$Timeout
)

if ($StdinFile) {
    $resolved = if ([IO.Path]::IsPathRooted($StdinFile)) { $StdinFile } else { Join-Path $root $StdinFile }
    if ($resolved -notmatch '^\\\\wsl(?:\.localhost|\$)\\([^\\]+)\\(.*)$' -or $Matches[1] -ne $distro) {
        throw 'stdin file must be inside the same WSL workspace'
    }
    $linuxInput = '/' + ($Matches[2] -replace '\\', '/')
    $arguments += @('--stdin-file', $linuxInput)
}
else {
    if ($StdinText) {
        $arguments += @('--stdin-text', $StdinText)
    }
}

& wsl.exe @arguments
exit $LASTEXITCODE
