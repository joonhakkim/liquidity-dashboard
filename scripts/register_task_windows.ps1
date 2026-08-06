# Windows Task Scheduler: register a daily 8:00 AM run of the pipeline.
# Run:
#   powershell -ExecutionPolicy Bypass -File scripts\register_task_windows.ps1
#
# Check:   Get-ScheduledTask -TaskName "KRX-Liquidity-Dashboard"
# Run now: Start-ScheduledTask -TaskName "KRX-Liquidity-Dashboard"
# Remove:  Unregister-ScheduledTask -TaskName "KRX-Liquidity-Dashboard" -Confirm:$false

$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent $PSScriptRoot
$PythonExe = Join-Path $ProjectDir ".venv\Scripts\python.exe"
$PipelineScript = Join-Path $ProjectDir "scripts\run_pipeline.py"
$TaskName = "KRX-Liquidity-Dashboard"

if (-not (Test-Path $PythonExe)) {
    throw "venv python not found: $PythonExe (run 'python -m venv .venv' first)"
}

$Action = New-ScheduledTaskAction -Execute $PythonExe -Argument "`"$PipelineScript`"" -WorkingDirectory $ProjectDir
$Trigger = New-ScheduledTaskTrigger -Daily -At 8:00AM
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopOnIdleEnd -ExecutionTimeLimit (New-TimeSpan -Hours 1)

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Description "Daily auto-refresh for the Korea market liquidity dashboard" -Force

Write-Host "Registered: '$TaskName' will run daily at 8:00 AM"
Write-Host "To test now: Start-ScheduledTask -TaskName `"$TaskName`""
