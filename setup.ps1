# ZapZap Photobooth - Windows setup (equivalent of setup.sh)
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

# Ask an interpreter for its version. Returns "3.12" style, or $null with the
# reason written to $script:PyWhy. Never throws: a bad candidate must not abort
# the search, but it must not vanish silently either.
function Get-PyVersion($exe, $arg) {
    $script:PyWhy = ""
    $a = @()
    if ($arg) { $a += $arg }
    # Use .format() rather than an f-string so the probe works on any Python 3.x
    $a += @("-c", "import sys;print('{0}.{1}'.format(sys.version_info[0], sys.version_info[1]))")
    $old = $ErrorActionPreference
    $ErrorActionPreference = "Continue"   # 2>&1 on a native command throws under Stop
    try {
        $out = & $exe @a 2>&1
    } catch {
        $script:PyWhy = $_.Exception.Message
        return $null
    } finally {
        $ErrorActionPreference = $old
    }
    $text = (@($out) | ForEach-Object { "$_" }) -join "`n"
    if ($text -match '(?m)^\s*(\d+\.\d+)\s*$') { return $Matches[1] }
    if (-not $text.Trim()) { $script:PyWhy = "no output (Microsoft Store stub?)" }
    else { $script:PyWhy = ($text -split "`n")[0].Trim() }
    return $null
}

# py launcher first (most reliable), then PATH, then the standard install dirs
# for people who installed without ticking "Add python.exe to PATH".
$candidates = @()
foreach ($v in @("3.12", "3.11", "3.13", "3.10", "3.9")) { $candidates += , @("py", "-$v") }
$candidates += , @("python", $null)
$candidates += , @("python3", $null)
foreach ($v in @("312", "311", "313", "310", "39")) {
    $candidates += , @("$env:LOCALAPPDATA\Programs\Python\Python$v\python.exe", $null)
    $candidates += , @("$env:ProgramFiles\Python$v\python.exe", $null)
    $candidates += , @("C:\Python$v\python.exe", $null)
}

foreach ($c in $candidates) {
    $exe = $c[0]; $arg = $c[1]
    $label = if ($arg) { "$exe $arg" } else { "$exe" }

    if ($exe -like "*\*") {
        if (-not (Test-Path $exe)) { continue }        # absolute path, not installed
        $src = $exe
    } else {
        $cmd = Get-Command $exe -ErrorAction SilentlyContinue
        if (-not $cmd) { continue }                    # not on PATH at all
        $src = $cmd.Source
    }

    # The Store stub in WindowsApps is a no-op that opens the Microsoft Store.
    if ($src -like "*\WindowsApps\*") {
        Warn "  skip $label - Microsoft Store stub, not a real Python"
        continue
    }

    $ver = Get-PyVersion $exe $arg
    if ($ver -match "^3\.(9|10|11|12|13)$") {
        $python = @($exe); if ($arg) { $python += $arg }
        Ok "Python $ver found ($label)"
        break
    }
    if ($ver) { Warn "  skip $label - Python $ver is outside the supported 3.9-3.13 range" }
    else      { Warn "  skip $label - $script:PyWhy" }
}
if (-not $python) {
    Err "No compatible Python found (need 3.9-3.13)."
    Write-Host ""
    Write-Host "Versions the py launcher knows about:"
    $ErrorActionPreference = "Continue"
    & py -0p 2>&1 | ForEach-Object { Write-Host "  $_" }
    $ErrorActionPreference = "Stop"
    Write-Host ""
    Write-Host "If 3.12 is installed but not listed above, re-run its installer,"
    Write-Host "choose Modify, and tick both 'py launcher' and 'Add python.exe to PATH'."
    Write-Host "Otherwise install 3.12 from https://www.python.org/downloads/release/python-3129/"
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
    Ok ".env created from .env.example - open it and fill in tokens, password, PRINTER_NAME, WINDOWS_PAPER_FORM_NAME."
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
