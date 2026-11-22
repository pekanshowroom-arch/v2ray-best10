# Creates the virtual environment, installs dependencies and Xray-core.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1 -SkipScan

[CmdletBinding()]
param(
    [switch]$SkipScan
)

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
Set-Location $root

function Fail([string]$Message) {
    Write-Host ''
    Write-Host "ERROR: $Message" -ForegroundColor Red
    exit 1
}

function Show-PythonHint {
    Write-Host 'Install Python 3.11 or newer, then run this script again:' -ForegroundColor Yellow
    Write-Host '  winget install Python.Python.3.12'
    Write-Host '  winget install GitHub.cli'
}

Write-Host '=== Config Scanner setup ===' -ForegroundColor Cyan

# --- Locate Python ---------------------------------------------------------
$python = $null
$pythonArgs = @()

if (Get-Command py -ErrorAction SilentlyContinue) {
    try {
        $versionText = (& py -3 -c "import sys; print(str(sys.version_info.major) + '.' + str(sys.version_info.minor))" 2>$null)
        if ($LASTEXITCODE -eq 0 -and $versionText) {
            $version = [version]$versionText.Trim()
            if ($version -ge [version]'3.11') {
                $python = 'py'
                $pythonArgs = @('-3')
            }
        }
    } catch {
        # Fall through to the plain python launcher.
    }
}

if (-not $python -and (Get-Command python -ErrorAction SilentlyContinue)) {
    try {
        $versionText = (& python -c "import sys; print(str(sys.version_info.major) + '.' + str(sys.version_info.minor))" 2>$null)
        if ($LASTEXITCODE -eq 0 -and $versionText) {
            $version = [version]$versionText.Trim()
            if ($version -ge [version]'3.11') {
                $python = 'python'
            }
        }
    } catch {
        # Reported below.
    }
}

if (-not $python) {
    Show-PythonHint
    Fail 'Python 3.11+ was not found on PATH.'
}

Write-Host "Python launcher : $python $($pythonArgs -join ' ')"
Write-Host "Project folder  : $root"

# --- Virtual environment ---------------------------------------------------
$venvDir = Join-Path $root '.venv'
$venvPython = Join-Path $venvDir 'Scripts\python.exe'
$venvPip = Join-Path $venvDir 'Scripts\pip.exe'

if (-not (Test-Path $venvPython)) {
    Write-Host ''
    Write-Host 'Creating .venv ...' -ForegroundColor Cyan
    & $python @pythonArgs -m venv $venvDir
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $venvPython)) {
        Fail 'Could not create the virtual environment.'
    }
} else {
    Write-Host 'Virtual environment already exists.'
}

Write-Host ''
Write-Host 'Installing Python dependencies ...' -ForegroundColor Cyan
& $venvPython -m pip install --disable-pip-version-check --upgrade pip
if ($LASTEXITCODE -ne 0) {
    Fail 'pip upgrade failed. Check the internet connection.'
}

& $venvPython -m pip install --disable-pip-version-check -r (Join-Path $root 'requirements.txt')
if ($LASTEXITCODE -ne 0) {
    Fail 'Installing requirements.txt failed. Check the internet connection.'
}

# --- Xray-core -------------------------------------------------------------
Write-Host ''
Write-Host 'Preparing Xray-core ...' -ForegroundColor Cyan
& $venvPython (Join-Path $root 'bootstrap.py')
if ($LASTEXITCODE -ne 0) {
    Fail 'Xray-core could not be prepared. Try: .\.venv\Scripts\python.exe bootstrap.py --force'
}

# --- Optional first scan ---------------------------------------------------
if (-not $SkipScan) {
    Write-Host ''
    Write-Host 'Running the first scan (this can take a few minutes) ...' -ForegroundColor Cyan
    & (Join-Path $root 'run.ps1') --no-push
    if ($LASTEXITCODE -ne 0) {
        Write-Host 'The first scan did not finish successfully. You can retry with: .\run.ps1 --no-push' -ForegroundColor Yellow
    }
}

Write-Host ''
Write-Host '=== Setup complete ===' -ForegroundColor Green
Write-Host ''
Write-Host 'Next steps:'
Write-Host '  1. Log in to GitHub (one time):'
Write-Host '       gh auth login --hostname github.com --git-protocol https --web'
Write-Host '  2. Create the public repository and push the output:'
Write-Host '       powershell -NoProfile -ExecutionPolicy Bypass -File .\setup_repo.ps1'
Write-Host '  3. Schedule automatic scans:'
Write-Host '       powershell -NoProfile -ExecutionPolicy Bypass -File .\install_task.ps1 -RunNow'
Write-Host ''
Write-Host 'Manual scan: run.cmd'
Write-Host 'Quick test:  run.cmd --limit 20 --no-push'
