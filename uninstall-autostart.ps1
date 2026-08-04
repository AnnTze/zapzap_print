# ZapZap Photobooth — remove the Windows auto-start services (equivalent of uninstall-autostart.sh)
# Requires an ADMIN PowerShell and NSSM on PATH.
function Ok($m)   { Write-Host $m -ForegroundColor Green }
function Warn($m) { Write-Host $m -ForegroundColor Yellow }
function Err($m)  { Write-Host $m -ForegroundColor Red }

$admin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { Err "Run this in an Administrator PowerShell."; exit 1 }
if (-not (Get-Command nssm -ErrorAction SilentlyContinue)) { Err "NSSM not found on PATH."; exit 1 }

foreach ($svc in @("ZapZapPrintBot", "ZapZapMonitorBot", "ZapZapGalleryBot")) {
    if (Get-Service -Name $svc -ErrorAction SilentlyContinue) {
        nssm stop $svc | Out-Null
        nssm remove $svc confirm | Out-Null
        Ok "$svc removed."
    } else {
        Warn "$svc not installed."
    }
}
Ok "Auto-start removed."
