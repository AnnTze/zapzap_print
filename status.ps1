# ZapZap Photobooth — Windows health check (equivalent of status.sh)
function Ok($m)   { Write-Host $m -ForegroundColor Green }
function Warn($m) { Write-Host $m -ForegroundColor Yellow }
function Err($m)  { Write-Host $m -ForegroundColor Red }

$ProjectDir = $PSScriptRoot
Set-Location $ProjectDir
$venvPy = Join-Path $ProjectDir ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) { $venvPy = "python" }

# Map bot -> service name (install-autostart.ps1) and log file.
$bots = @(
    @{ name = "print_bot";   svc = "ZapZapPrintBot";   log = "logs\bot.log" },
    @{ name = "monitor_bot"; svc = "ZapZapMonitorBot"; log = "logs\monitor.log" },
    @{ name = "gallery_bot"; svc = "ZapZapGalleryBot"; log = "logs\gallery.log" }
)

# Read manual PIDs if present.
$pids = @{}
if (Test-Path ".pids") {
    Get-Content ".pids" | ForEach-Object {
        if ($_ -match "^(.*?)=(\d+)\s*$") { $pids[$Matches[1]] = [int]$Matches[2] }
    }
}

Write-Host "=== Bot Status ==="
foreach ($b in $bots) {
    $running = $false; $detail = ""
    # Prefer manual PID; fall back to the Windows service.
    if ($pids.ContainsKey($b.name) -and (Get-Process -Id $pids[$b.name] -ErrorAction SilentlyContinue)) {
        $running = $true; $detail = "PID $($pids[$b.name])"
    } else {
        $svc = Get-Service -Name $b.svc -ErrorAction SilentlyContinue
        if ($svc -and $svc.Status -eq "Running") { $running = $true; $detail = "service $($b.svc)" }
    }
    if ($running) {
        Ok "$($b.name) RUNNING ($detail)"
        if (Test-Path $b.log) {
            Write-Host "  Last 3 log lines:"
            Get-Content $b.log -Tail 3 | ForEach-Object { Write-Host "    $_" }
        }
    } else {
        Err "$($b.name) STOPPED"
    }
    Write-Host ""
}

Write-Host "=== Printer Status ==="
$printerStatus = & $venvPy -m printing status 2>$null
if ($LASTEXITCODE -eq 0) { Ok $printerStatus }
elseif ($printerStatus) { Err $printerStatus }
else { Err "Printer NOT DETECTED" }
Write-Host ""

Write-Host "=== Queue ==="
& $venvPy -m printing queue 2>$null
Write-Host ""

Write-Host "=== Log Sizes ==="
foreach ($f in @("print_log.jsonl", "gallery_log.jsonl")) {
    if (Test-Path $f) {
        $size = (Get-Item $f).Length
        Write-Host ("{0}  {1:N0} bytes" -f $f, $size)
    } else {
        Write-Host "$f not yet created"
    }
}
