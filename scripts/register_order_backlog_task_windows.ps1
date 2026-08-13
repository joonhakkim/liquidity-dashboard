# Windows Task Scheduler: 수주잔고(order backlog) 전용 주간 실행(일요일 09:00).
#
# 전체 종목(~740개) DART 정기보고서를 훑는 무거운 스캔이라 매일 돌리면 fetch_dart_quarterly.py
# 등 다른 단계와 DART API 하루 호출 한도(2만 건)를 다투게 된다. 분기보고서 기반이라 매일
# 바뀔 데이터도 아니므로 주 1회(일요일, 장 마감/평일 DART 트래픽과 안 겹치는 시간)로 분리한다.
# (2026-08-13, 다른 자동갱신 점검 중 수주잔고가 아예 파이프라인에 안 걸려있던 걸 발견해서 추가)
#
# Run:
#   powershell -ExecutionPolicy Bypass -File scripts\register_order_backlog_task_windows.ps1
#
# Check:   Get-ScheduledTask -TaskName "KRX-OrderBacklog"
# Run now: Start-ScheduledTask -TaskName "KRX-OrderBacklog"
# Remove:  Unregister-ScheduledTask -TaskName "KRX-OrderBacklog" -Confirm:$false

$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent $PSScriptRoot
$PythonExe = Join-Path $ProjectDir ".venv\Scripts\python.exe"
$PipelineScript = Join-Path $ProjectDir "scripts\run_order_backlog_pipeline.py"
$TaskName = "KRX-OrderBacklog"

if (-not (Test-Path $PythonExe)) {
    throw "venv python not found: $PythonExe (run 'python -m venv .venv' first)"
}

$Action = New-ScheduledTaskAction -Execute $PythonExe -Argument "`"$PipelineScript`"" -WorkingDirectory $ProjectDir
$Trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At 9:00AM
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopOnIdleEnd -ExecutionTimeLimit (New-TimeSpan -Hours 3)

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Description "수주잔고 주간 갱신(일요일 09:00, DART 전종목 정기보고서 스캔)" -Force

Write-Host "Registered: '$TaskName' will run weekly on Sunday at 9:00 AM"
Write-Host "To test now: Start-ScheduledTask -TaskName `"$TaskName`""
