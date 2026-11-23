# Publishes the generated output to a public GitHub repository.
#
# Prerequisites:
#   gh auth login --hostname github.com --git-protocol https --web
#
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\setup_repo.ps1
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\setup_repo.ps1 -RepoName my-configs
#
# The repository must be public: PassWall 2 cannot send a GitHub token when it
# downloads a raw subscription file.

[CmdletBinding()]
param(
    [string]$RepoName = 'v2ray-best10',
    [string]$Branch = 'main'
)

$ErrorActionPreference = 'Continue'
$root = $PSScriptRoot
Set-Location $root

function Fail([string]$Message) {
    Write-Host ''
    Write-Host "ERROR: $Message" -ForegroundColor Red
    exit 1
}

if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    Fail 'GitHub CLI (gh) is not installed. Install it with: winget install GitHub.cli'
}
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Fail 'git is not installed. Install it with: winget install Git.Git'
}

& gh auth status *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host 'GitHub CLI is not authenticated yet.' -ForegroundColor Yellow
    Write-Host ''
    Write-Host 'Run this once, then start setup_repo.ps1 again:'
    Write-Host '  gh auth login --hostname github.com --git-protocol https --web'
    Write-Host ''
    Write-Host 'Choose HTTPS, log in through the browser and authorise the CLI.'
    exit 1
}

$owner = (& gh api user --jq .login).Trim()
if (-not $owner) {
    Fail 'Could not read the GitHub username from gh.'
}

$fullName = "$owner/$RepoName"
Write-Host "=== Publishing to $fullName ===" -ForegroundColor Cyan

# --- Local git repository --------------------------------------------------
if (-not (Test-Path (Join-Path $root '.git'))) {
    Write-Host 'Initialising git ...' -ForegroundColor Cyan
    & git init -b $Branch
    if ($LASTEXITCODE -ne 0) { Fail 'git init failed.' }
}

$currentBranch = (& git branch --show-current).Trim()
if (-not $currentBranch) {
    & git checkout -b $Branch
    if ($LASTEXITCODE -ne 0) { Fail 'Could not create the initial branch.' }
} elseif ($currentBranch -ne $Branch) {
    Write-Host "Switching branch '$currentBranch' -> '$Branch' ..." -ForegroundColor Yellow
    & git checkout -B $Branch
    if ($LASTEXITCODE -ne 0) { Fail "Could not switch to branch $Branch." }
}

if (-not (& git config user.name)) {
    & git config user.name $owner
}
if (-not (& git config user.email)) {
    & git config user.email "$owner@users.noreply.github.com"
}

& git add -A
if ($LASTEXITCODE -ne 0) { Fail 'git add failed.' }

& git diff --cached --quiet
$hasStagedChanges = ($LASTEXITCODE -ne 0)
if ($hasStagedChanges) {
    & git rev-parse --verify --quiet HEAD *> $null
    $hasHead = ($LASTEXITCODE -eq 0)
    $commitMessage = if ($hasHead) {
        'Update generated subscription'
    } else {
        'Initial commit: config scanner and generated subscription'
    }
    & git commit -m $commitMessage
    if ($LASTEXITCODE -ne 0) { Fail 'git commit failed. Check your git identity settings.' }
}

# --- Remote repository -----------------------------------------------------
$remoteUrl = (& git config --get remote.origin.url 2>$null)
if (-not $remoteUrl) {
    & gh repo view $fullName --json name *> $null
    $repoExists = ($LASTEXITCODE -eq 0)

    if (-not $repoExists) {
        Write-Host 'Creating the GitHub repository ...' -ForegroundColor Cyan
        & gh repo create $fullName --public --description 'Automatically refreshed best V2Ray configs for PassWall 2'
        if ($LASTEXITCODE -ne 0) { Fail 'gh repo create failed.' }
    } else {
        Write-Host "Repository $fullName already exists; adding it as origin." -ForegroundColor Yellow
    }

    & git remote add origin "https://github.com/$fullName.git"
    if ($LASTEXITCODE -ne 0) { Fail 'Could not add the origin remote.' }
} else {
    Write-Host "Using existing origin: $remoteUrl"
}

Write-Host 'Pushing ...' -ForegroundColor Cyan
& git push -u origin $Branch
if ($LASTEXITCODE -ne 0) {
    Write-Host ''
    Write-Host 'Push failed. If the remote already contains commits, run:' -ForegroundColor Yellow
    Write-Host "  git pull --rebase origin $Branch"
    Write-Host "  git push -u origin $Branch"
    exit 1
}

$base = "https://raw.githubusercontent.com/$fullName/$Branch/output"

Write-Host ''
Write-Host '=== Published ===' -ForegroundColor Green
Write-Host ''
Write-Host 'PassWall 2 subscription URL (base64, recommended):'
Write-Host "  $base/sub_base64.txt" -ForegroundColor Cyan
Write-Host ''
Write-Host 'Plain links (fallback):'
Write-Host "  $base/sub.txt"
Write-Host ''
Write-Host 'Repository:'
Write-Host "  https://github.com/$fullName"
Write-Host ''
Write-Host 'Copy the base64 URL into PassWall 2 as a node subscription.'
