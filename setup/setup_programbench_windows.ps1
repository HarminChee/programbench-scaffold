[CmdletBinding()]
param(
    [string]$DistroName = "ProgramBench-Ubuntu-22.04",
    [string]$LinuxUser = "programbench",
    [string]$InstallDir = "$env:LOCALAPPDATA\ProgramBenchWSL",
    [string]$UbuntuTar = "$env:USERPROFILE\Documents\Codex\ProgramBenchDownloads\ubuntu-jammy-wsl-amd64-wsl.rootfs.tar.gz",
    [switch]$DownloadTargets,
    [switch]$RunSmokeExperiment
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$UbuntuUrl = "https://cloud-images.ubuntu.com/wsl/releases/jammy/current/ubuntu-jammy-wsl-amd64-wsl.rootfs.tar.gz"
$UbuntuSha256 = "de9f6149da07b90350a3ccd94b4858b82fef71f0ec2982acb93de583c4c87585"
$UserSetupScript = Join-Path $PSScriptRoot "programbench_wsl_user_setup.sh"
$BootstrapScript = Join-Path $PSScriptRoot "programbench_wsl_bootstrap.sh"

function Invoke-Wsl {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    & wsl.exe @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "wsl.exe failed with exit code ${LASTEXITCODE}: $($Arguments -join ' ')"
    }
}

function Get-DistroNames {
    $raw = & wsl.exe --list --quiet 2>$null
    if ($LASTEXITCODE -ne 0) {
        return @()
    }
    return @($raw | ForEach-Object { ($_ -replace [char]0, "").Trim() } | Where-Object { $_ })
}

function Convert-ToWslPath {
    param([Parameter(Mandatory = $true)][string]$WindowsPath)
    $fullPath = [System.IO.Path]::GetFullPath($WindowsPath)
    $root = [System.IO.Path]::GetPathRoot($fullPath)
    if (-not $root -or $root.Length -lt 2 -or $root[1] -ne ':') {
        throw "Expected a drive-qualified Windows path, got: $WindowsPath"
    }
    $drive = [char]::ToLowerInvariant($root[0])
    $relative = $fullPath.Substring($root.Length).Replace('\', '/')
    return "/mnt/$drive/$relative"
}

if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
    throw "WSL is not installed. Enable Windows Subsystem for Linux and Virtual Machine Platform first."
}
foreach ($requiredScript in @($UserSetupScript, $BootstrapScript)) {
    if (-not (Test-Path -LiteralPath $requiredScript)) {
        throw "Missing setup script: $requiredScript"
    }
}

if (-not (Test-Path -LiteralPath $UbuntuTar)) {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $UbuntuTar) | Out-Null
    Write-Host "Downloading the pinned Canonical Ubuntu 22.04 WSL image..."
    try {
        Start-BitsTransfer -Source $UbuntuUrl -Destination $UbuntuTar -ErrorAction Stop
    }
    catch {
        Invoke-WebRequest -UseBasicParsing -Uri $UbuntuUrl -OutFile $UbuntuTar
    }
}

$actualSha = (Get-FileHash -LiteralPath $UbuntuTar -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actualSha -ne $UbuntuSha256) {
    throw "Ubuntu image SHA-256 mismatch. Expected $UbuntuSha256, got $actualSha"
}
Write-Host "Ubuntu image checksum verified."

$wslConfig = Join-Path $env:USERPROFILE ".wslconfig"
if (-not (Test-Path -LiteralPath $wslConfig)) {
    $memoryGb = 12
    $processors = 8
    try {
        $computer = Get-CimInstance Win32_ComputerSystem
        $totalGb = [math]::Floor([double]$computer.TotalPhysicalMemory / 1GB)
        $memoryGb = [math]::Min(32, [math]::Max(8, [math]::Floor($totalGb * 0.75)))
        $processors = [math]::Min(12, [math]::Max(2, [int]$computer.NumberOfLogicalProcessors))
    }
    catch {
        Write-Warning "Could not inventory host RAM/CPU; using 12 GB and 8 processors for WSL."
    }
    @"
[wsl2]
memory=${memoryGb}GB
processors=$processors
swap=8GB
localhostForwarding=true
"@ | Set-Content -LiteralPath $wslConfig -Encoding ASCII
    Write-Host "Created $wslConfig with ${memoryGb}GB RAM and $processors processors."
}
else {
    Write-Host "Keeping existing $wslConfig unchanged."
}

$distros = Get-DistroNames
if ($distros -notcontains $DistroName) {
    New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
    Write-Host "Importing $DistroName into $InstallDir..."
    Invoke-Wsl -Arguments @("--import", $DistroName, $InstallDir, $UbuntuTar, "--version", "2")
}
else {
    Write-Host "$DistroName is already registered."
}

$userSetupWslPath = Convert-ToWslPath $UserSetupScript
if (-not $userSetupWslPath) {
    throw "Could not translate user-setup script path for WSL."
}
Invoke-Wsl -Arguments @("-d", $DistroName, "-u", "root", "--", "bash", $userSetupWslPath, $LinuxUser)

& wsl.exe --shutdown
Start-Sleep -Seconds 2

$bootstrapWslPath = Convert-ToWslPath $BootstrapScript
if (-not $bootstrapWslPath) {
    throw "Could not translate bootstrap script path for WSL."
}

$downloadFlag = if ($DownloadTargets) { "1" } else { "0" }
$smokeFlag = if ($RunSmokeExperiment) { "1" } else { "0" }
Invoke-Wsl -Arguments @(
    "-d", $DistroName, "-u", "root", "--",
    "env", "PROGRAMBENCH_USER=$LinuxUser", "PROGRAMBENCH_DOWNLOAD_TARGETS=$downloadFlag", "PROGRAMBENCH_RUN_SMOKE=$smokeFlag",
    "bash", $bootstrapWslPath
)

Invoke-Wsl -Arguments @("--set-default", $DistroName)
Write-Host ""
Write-Host "ProgramBench environment is ready."
Write-Host "Open it with: wsl -d $DistroName"
Write-Host "Official code: ~/research/programbench"
Write-Host "Scaffold branch: ~/research/programbench-scaffold"
Write-Host "Validation report: ~/research/setup-validation.txt"
