# Windows Task Scheduler: 코스피·코스닥 선행 PER 트래커 전용 일일 실행(07:30).
#
# 예전엔 17:00(장마감 이후)에 돌렸는데, 사용자가 모든 자동갱신을 아침 7시 30분 한 번으로
# 통일해달라고 해서 변경. KRX 종가는 보통 밤사이 발행되니 다음날 아침 실행에서 전날 종가를
# 잡는다(당일 갱신은 아님 - PER/PBR 트래커 특성상 그날그날 실시간 반영보다는 하루 단위 누적이
# 목적이라 큰 문제 없음).
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
$Trigger = New-ScheduledTaskTrigger -Daily -At 7:30AM
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopOnIdleEnd -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Description "코스피/코스닥 선행 PER 트래커 일일 갱신(07:30)" -Force

Write-Host "Registered: '$TaskName' will run daily at 7:30 AM"
Write-Host "To test now: Start-ScheduledTask -TaskName `"$TaskName`""
