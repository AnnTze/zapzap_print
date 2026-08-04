# ZapZap Photobooth — Windows setup (equivalent of setup.sh)
# Run from PowerShell in the project folder:  .\setup.ps1
$ErrorActionPreference = "Stop"

function Ok($m)   { Write-Host $m -ForegroundColor Green }
function Warn($m) { Write-Host $m -ForegroundColor Yellow }
function Err($m)  { Write-Host $m -ForegroundColor Red }

$ProjectDir = $PSScriptRoot
Set-Location $ProjectDir

# --- Step 1: Find a compatible Python (3.9-3.13; NOT 3.14) ---
Write-Host "Checking Python..."
# python-telegram-bot 21.6 breaks on Python 3.14 (removed asyncio APIs).
$python = $null
$candidates = @(
    @("py", "-3.12"), @("py", "-3.11"), @("py", "-3.13"), @("py", "-3.10"),
    @("py", "-3.9"), @("python", $null)
)
foreach ($c in $candidates) {
    $exe = $c[0]; $arg = $c[1]
    if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
    try {
        $verArgs = @()
        if ($arg) { $verArgs += $arg }
        $verArgs += @("-c", "import sys;print(f'{sys.version_info.major}.{sys.version_info.minor}')")
        $ver = (& $exe @verArgs 2>$null).Trim()
    } catch { continue }
    if ($ver -match "^3\.(9|10|11|12|13)$") {
        $python = @($exe); if ($arg) { $python += $arg }
        Ok "Python $ver found ($exe $arg)"
        break
    }
}
if (-not $python) {
    Err "No compatible Python found (need 3.9-3.13)."
    Write-Host "Install Python 3.12 from https://www.python.org/downloads/release/python-3129/"
    Write-Host "During install, tick 'Add python.exe to PATH'."
    exit 1
}

# --- Step 2: Create virtual environment ---
Write-Host "`nSetting up virtual environment..."
if (Test-Path ".venv\Scripts\python.exe") {
    Ok "Virtual environment already exists, skipping."
} else {
    & $python[0] $python[1..($python.Count-1)] -m venv .venv
    Ok "Virtual environment created."
}
$venvPy = Join-Path $ProjectDir ".venv\Scripts\python.exe"

# --- Step 3: Install dependencies (Windows set includes pywin32) ---
Write-Host "`nInstalling dependencies..."
& $venvPy -m pip install --upgrade pip -q
& $venvPy -m pip install -r requirements-windows.txt -q
Ok "Dependencies installed."

# --- Step 4: Check printer ---
Write-Host "`nChecking printers..."
$printers = Get-Printer -ErrorAction SilentlyContinue
if ($printers) {
    $mitsu = $printers | Where-Object { $_.Name -match "MITSUBISHI|CP-D90|D90" }
    if ($mitsu) {
        Ok "Mitsubishi printer detected:"
        $mitsu | ForEach-Object { Write-Host ("  {0}" -f $_.Name) }
        Write-Host ("Set PRINTER_NAME={0} in your .env file" -f $mitsu[0].Name)
    } else {
        Warn "Mitsubishi printer not found. Installed printers:"
        $printers | ForEach-Object { Write-Host ("  {0}" -f $_.Name) }
        Write-Host "Install the CP-D90DW driver, then set PRINTER_NAME in .env to its exact name."
    }
} else {
    Warn "Could not enumerate printers. Connect the printer and install its driver."
}

# --- Step 5: Create .env from template ---
Write-Host "`nSetting up .env..."
if (Test-Path ".env") {
    Ok ".env already exists, skipping. Edit it manually if needed."
} elseif (Test-Path ".env.example") {
    Copy-Item ".env.example" ".env"
    Ok ".env created from .env.example — open it and fill in tokens, password, PRINTER_NAME, WINDOWS_PAPER_FORM_NAME."
} else {
    Warn ".env.example not found; create .env manually."
}

# --- Step 6: logs dir ---
New-Item -ItemType Directory -Force -Path "logs" | Out-Null
Ok "logs\ directory ready."

# --- Step 7: Syntax-check bot files ---
Write-Host "`nSyntax-checking bot files..."
foreach ($f in @("bot.py", "monitor.py", "gallery.py")) {
    if (-not (Test-Path $f)) { Warn "$f not found, skipping."; continue }
    & $venvPy -m py_compile $f
    if ($LASTEXITCODE -eq 0) { Ok "$f OK" } else { Err "$f FAILED" }
}

# --- Step 8: Find the paper form id ---
Write-Host "`nTo find WINDOWS_PAPER_FORM_NAME for .env, run:"
Write-Host "    .venv\Scripts\python.exe scripts\find_paper_form.py"

Write-Host "`n============================================"
Ok "Setup complete!"
Write-Host "============================================"
Write-Host "Next steps:"
Write-Host "  1. Open .env and fill in the 3 bot tokens, channel id, password,"
Write-Host "     PRINTER_NAME, and WINDOWS_PAPER_FORM_NAME (from find_paper_form.py)."
Write-Host "  2. Test manually:   .\run.ps1   then   .\status.ps1"
Write-Host "  3. Production auto-start (services):   .\install-autostart.ps1"
Write-Host "  See WINDOWS_SETUP.md for the full walkthrough."
