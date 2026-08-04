# ZapZap Photobooth - Windows manual stop (equivalent of stop.sh)
function Ok($m)   { Write-Host $m -ForegroundColor Green }
function Warn($m) { Write-Host $m -ForegroundColor Yellow }

$ProjectDir = $PSScriptRoot
Set-Location $ProjectDir

if (-not (Test-Path ".pids")) {
    Warn "No .pids file found - bots may not be running (or are running as services)."
    exit 0
}

Get-Content ".pids" | ForEach-Object {
    if ($_ -match "^(.*?)=(\d+)\s*$") {
        $name = $Matches[1]; $procId = [int]$Matches[2]
        $p = Get-Process -Id $procId -ErrorAction SilentlyContinue
        if ($p) {
            # Ask to close, then force after 5s if still alive.
            $p.CloseMainWindow() | Out-Null
            Stop-Process -Id $procId -ErrorAction SilentlyContinue
            for ($i = 0; $i -lt 5; $i++) {
                if (-not (Get-Process -Id $procId -ErrorAction SilentlyContinue)) { break }
                Start-Sleep -Seconds 1
            }
            if (Get-Process -Id $procId -ErrorAction SilentlyContinue) {
                Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
            }
            Ok "$name stopped (PID $procId)"
        } else {
            Warn "$name was not running (PID $procId)"
        }
    }
}

Remove-Item ".pids" -ErrorAction SilentlyContinue
Ok "All bots stopped."
