# ZapZap Photobooth — Windows manual start (equivalent of run.sh)
# For development / one-off events. For always-on production use install-autostart.ps1.
$ErrorActionPreference = "Stop"

function Ok($m)   { Write-Host $m -ForegroundColor Green }
function Warn($m) { Write-Host $m -ForegroundColor Yellow }
function Err($m)  { Write-Host $m -ForegroundColor Red }

$ProjectDir = $PSScriptRoot
Set-Location $ProjectDir
$venvPy = Join-Path $ProjectDir ".venv\Scripts\python.exe"

if (-not (Test-Path ".env"))   { Err ".env not found. Run .\setup.ps1 first."; exit 1 }
if (-not (Test-Path $venvPy))  { Err "Virtual environment not found. Run .\setup.ps1 first."; exit 1 }

New-Item -ItemType Directory -Force -Path "logs" | Out-Null

# Read existing PIDs (name=PID per line) if present.
$pids = @{}
if (Test-Path ".pids") {
    Get-Content ".pids" | ForEach-Object {
        if ($_ -match "^(.*?)=(\d+)\s*$") { $pids[$Matches[1]] = [int]$Matches[2] }
    }
}

function Start-Bot($label, $name, $script, $logfile) {
    if ($pids.ContainsKey($name)) {
        $existing = Get-Process -Id $pids[$name] -ErrorAction SilentlyContinue
        if ($existing) { Write-Host "$label already running (PID $($pids[$name]))"; return $pids[$name] }
    }
    # Start hidden, redirect stdout+stderr to the log file.
    $p = Start-Process -FilePath $venvPy -ArgumentList $script `
        -WorkingDirectory $ProjectDir -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $logfile -RedirectStandardError "$logfile.err"
    Ok "$label started (PID $($p.Id)), logging to $logfile"
    return $p.Id
}

$pPrint   = Start-Bot "Print bot"   "print_bot"   "bot.py"     "logs\bot.log"
$pMonitor = Start-Bot "Monitor bot" "monitor_bot" "monitor.py" "logs\monitor.log"
$pGallery = Start-Bot "Gallery bot" "gallery_bot" "gallery.py" "logs\gallery.log"

@("print_bot=$pPrint", "monitor_bot=$pMonitor", "gallery_bot=$pGallery") |
    Set-Content ".pids"

Write-Host "`nAll bots running. Use .\status.ps1 to check health."
Write-Host "To stop: .\stop.ps1"
