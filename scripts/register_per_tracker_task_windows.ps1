# Windows Task Scheduler: 코스피·코스닥 선행 PER 트래커 전용 일일 실행(18:00).
#
# 한때 모든 자동갱신을 아침 7시 30분으로 통일했었는데, PER 트래커는 KRX 당일 종가가
# 아침 7시 30분 시점엔 아직 발행 전이라 매번 하루 지연된 데이터만 잡히는 문제가 반복됐다
# (사용자가 "매번 자동갱신이 안되네"라고 지적). KRX는 보통 늦은 오후~저녁 사이엔 당일
# 종가를 발행하므로, 이 트래커만 저녁 6시로 다시 분리해서 당일 갱신이 되게 한다.
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
$Trigger = New-ScheduledTaskTrigger -Daily -At 6:00PM
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopOnIdleEnd -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Description "코스피/코스닥 선행 PER 트래커 일일 갱신(18:00, 당일 KRX 종가 반영)" -Force

Write-Host "Registered: '$TaskName' will run daily at 6:00 PM"
Write-Host "To test now: Start-ScheduledTask -TaskName `"$TaskName`""
