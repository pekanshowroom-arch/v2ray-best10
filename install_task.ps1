# Registers a Windows Task Scheduler task that refreshes the subscription.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\install_task.ps1 -RunNow
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\install_task.ps1 -IntervalHours 12
#
# The task runs as the current user and only while that user is logged on. If
# the PC was off at a scheduled time, the scan starts when it is available next.

[CmdletBinding()]
param(
    [string]$TaskName = 'ConfigScanner',
    [int]$IntervalHours = 6,
    [switch]$RunNow
)

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$runScript = Join-Path $root 'run.ps1'

if (-not (Test-Path $runScript)) {
    throw "run.ps1 was not found next to this script."
}
if ($IntervalHours -lt 1 -or $IntervalHours -gt 24 -or (24 % $IntervalHours) -ne 0) {
    throw 'IntervalHours must be a divisor of 24 (1, 2, 3, 4, 6, 8, 12 or 24).'
}

Write-Host "=== Scheduling $TaskName every $IntervalHours hour(s) ===" -ForegroundColor Cyan

$action = New-ScheduledTaskAction `
    -Execute 'powershell.exe' `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$runScript`""

$triggers = @()
$times = @()
for ($hour = 0; $hour -lt 24; $hour += $IntervalHours) {
    $at = [datetime]::Today.AddHours($hour)
    $triggers += New-ScheduledTaskTrigger -Daily -At $at
    $times += $at.ToString('HH:mm')
}

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
    -MultipleInstances IgnoreNew

$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $triggers `
    -Settings $settings `
    -Principal $principal `
    -Description 'Fetches, tests and publishes the best V2Ray configs for PassWall 2.' `
    -Force | Out-Null

Write-Host "Task registered for user: $identity" -ForegroundColor Green
Write-Host 'Schedule: ' -NoNewline
Write-Host ($times -join ', ')

if ($RunNow) {
    Write-Host 'Starting the first scheduled run now ...' -ForegroundColor Cyan
    Start-ScheduledTask -TaskName $TaskName
    Start-Sleep -Seconds 2
}

Write-Host ''
Write-Host 'Useful commands:'
Write-Host "  Get-ScheduledTaskInfo -TaskName $TaskName"
Write-Host "  Start-ScheduledTask -TaskName $TaskName"
Write-Host "  Unregister-ScheduledTask -TaskName $TaskName -Confirm:`$false"
Write-Host ''
Write-Host 'The PC must be turned on and the user logged on for the task to run.'
