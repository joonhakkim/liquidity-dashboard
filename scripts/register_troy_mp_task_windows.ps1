# Windows Task Scheduler: 트로이 MP 트래커 전용 일일 실행(20:05, 애프터마켓 마감 이후).
#
# 아침 07:30 메인 파이프라인에 같이 있었는데, 그 시점엔 당일 종가가 아직 안 나와서
# 하루 지연된 가격으로 갱신되는 문제가 있었다(PER 트래커가 겪었던 것과 동일한 문제,
# register_per_tracker_task_windows.ps1 참고). 그래서 당일 종가가 확정되는 오후
# 5시 30분으로 분리했었다(2026-08-19, 사용자 요청).
#
# 2026-09-15: KRX가 9/14부터 애프터마켓(16:00~20:00, 접속매매)을 도입하면서 네이버
# 일별시세가 20:00까지 계속 갱신되는 값을 "당일 종가"로 반환하게 됐다 - 17:30에 받으면
# 애프터마켓 중간의 스냅샷일 뿐이라 나중에 또 달라지는 문제를 겪었다(같은 날 두 번
# 정정하는 소동). "20:00 애프터마켓 마감가를 종가로 쓰기로" 결정(사용자 요청)하고
# 20:05로 미뤘다 - 5분 버퍼는 마감 직후 데이터 반영 지연 대비.
#
# Run:
#   powershell -ExecutionPolicy Bypass -File scripts\register_troy_mp_task_windows.ps1
#
# Check:   Get-ScheduledTask -TaskName "KRX-TroyMP"
# Run now: Start-ScheduledTask -TaskName "KRX-TroyMP"
# Remove:  Unregister-ScheduledTask -TaskName "KRX-TroyMP" -Confirm:$false

$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent $PSScriptRoot
$PythonExe = Join-Path $ProjectDir ".venv\Scripts\python.exe"
$PipelineScript = Join-Path $ProjectDir "scripts\run_troy_mp_pipeline.py"
$TaskName = "KRX-TroyMP"

if (-not (Test-Path $PythonExe)) {
    throw "venv python not found: $PythonExe (run 'python -m venv .venv' first)"
}

$Action = New-ScheduledTaskAction -Execute $PythonExe -Argument "`"$PipelineScript`"" -WorkingDirectory $ProjectDir
$Trigger = New-ScheduledTaskTrigger -Daily -At 8:05PM
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopOnIdleEnd -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Description "트로이 MP 트래커 일일 갱신(20:05, 애프터마켓 마감 종가 반영)" -Force

Write-Host "Registered: '$TaskName' will run daily at 8:05 PM"
Write-Host "To test now: Start-ScheduledTask -TaskName `"$TaskName`""
