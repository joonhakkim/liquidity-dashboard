# Windows Task Scheduler: 코스피·코스닥 선행 PER 트래커 전용 일일 실행(17:00, 장마감 15:30 이후).
#
# Run:
#   powershell -ExecutionPolicy Bypass -File scripts\register_per_tracker_task_windows.ps1
#
# Check:   Get-ScheduledTask -TaskName "KRX-PER-Tracker"
# Run now: Start-ScheduledTask -TaskName "KRX-PER-Tracker"
# Remove:  Unregister-ScheduledTask -TaskName "KRX-PER-Tracker" -Confirm:$false

$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent $PSScriptRoot
$PythonExe = Join-Path $ProjectDir ".venv\Scripts\python.exe"
$PipelineScript = Join-Path $ProjectDir "scripts\run_per_tracker_pipeline.py"
$TaskName = "KRX-PER-Tracker"

if (-not (Test-Path $PythonExe)) {
    throw "venv python not found: $PythonExe (run 'python -m venv .venv' first)"
}

$Action = New-ScheduledTaskAction -Execute $PythonExe -Argument "`"$PipelineScript`"" -WorkingDirectory $ProjectDir
$Trigger = New-ScheduledTaskTrigger -Daily -At 5:00PM
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopOnIdleEnd -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Description "코스피/코스닥 선행 PER 트래커 일일 갱신(17:00)" -Force

Write-Host "Registered: '$TaskName' will run daily at 5:00 PM"
Write-Host "To test now: Start-ScheduledTask -TaskName `"$TaskName`""
