# Runs one scan with the project virtual environment.
#
#   .\run.ps1
#   .\run.ps1 --no-push
#   .\run.ps1 --limit 20 --top 10
#   .\run.ps1 --concurrency 8 -v
#
# This file intentionally has no param() block: every argument is forwarded to
# scanner.py unchanged (including options such as --no-push and -v).

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
Set-Location $root

$venvPython = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $venvPython)) {
    Write-Host 'ERROR: .venv was not found. Run setup.ps1 first.' -ForegroundColor Red
    exit 1
}

& $venvPython (Join-Path $root 'scanner.py') @args
exit $LASTEXITCODE
