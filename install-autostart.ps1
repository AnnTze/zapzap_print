# ZapZap Photobooth — Windows auto-start via NSSM services (equivalent of install-autostart.sh)
# Installs the three bots as Windows services that start at boot and restart on crash.
# Requires: NSSM on PATH (choco install nssm, or https://nssm.cc/) and an ADMIN PowerShell.
$ErrorActionPreference = "Stop"

function Ok($m)   { Write-Host $m -ForegroundColor Green }
function Warn($m) { Write-Host $m -ForegroundColor Yellow }
function Err($m)  { Write-Host $m -ForegroundColor Red }

$ProjectDir = $PSScriptRoot
Set-Location $ProjectDir
$venvPy = Join-Path $ProjectDir ".venv\Scripts\python.exe"

# --- Preconditions ---
$admin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { Err "Run this in an Administrator PowerShell (services require admin)."; exit 1 }
if (-not (Get-Command nssm -ErrorAction SilentlyContinue)) {
    Err "NSSM not found on PATH. Install it first:  choco install nssm"
    Write-Host "or download from https://nssm.cc/ and add nssm.exe to PATH."
    exit 1
}
if (-not (Test-Path $venvPy)) { Err "Virtual environment not found. Run .\setup.ps1 first."; exit 1 }

# --- Stop any manually-started bots to avoid duplicate getUpdates conflicts ---
if (Test-Path ".pids") {
    Write-Host "Stopping manually-started bots first..."
    & (Join-Path $ProjectDir "stop.ps1")
    Write-Host ""
}

New-Item -ItemType Directory -Force -Path "logs" | Out-Null

$services = @(
    @{ svc = "ZapZapPrintBot";   script = "bot.py";     log = "logs\bot.log" },
    @{ svc = "ZapZapMonitorBot"; script = "monitor.py"; log = "logs\monitor.log" },
    @{ svc = "ZapZapGalleryBot"; script = "gallery.py"; log = "logs\gallery.log" }
)

foreach ($s in $services) {
    $svc = $s.svc
    $script = Join-Path $ProjectDir $s.script
    $log = Join-Path $ProjectDir $s.log

    # Clean reinstall if it already exists.
    if (Get-Service -Name $svc -ErrorAction SilentlyContinue) {
        nssm stop $svc | Out-Null
        nssm remove $svc confirm | Out-Null
    }

    nssm install $svc $venvPy $script | Out-Null
    nssm set $svc AppDirectory $ProjectDir | Out-Null
    nssm set $svc AppStdout $log | Out-Null
    nssm set $svc AppStderr $log | Out-Null
    nssm set $svc Start SERVICE_AUTO_START | Out-Null
    # Restart on crash, after a short delay.
    nssm set $svc AppExit Default Restart | Out-Null
    nssm set $svc AppRestartDelay 3000 | Out-Null
    nssm start $svc | Out-Null
    Ok "$svc installed and started."
}

Write-Host ""
Ok "Auto-start installed. All three bots will start at boot and restart on crash."
Write-Host "Verify:   Get-Service ZapZap*   and   .\status.ps1"
Write-Host "Do NOT also use .\run.ps1 while services are installed (duplicate instances)."
Write-Host "To remove:  .\uninstall-autostart.ps1"
