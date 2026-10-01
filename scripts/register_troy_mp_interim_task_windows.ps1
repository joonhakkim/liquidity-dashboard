# Windows Task Scheduler: 트로이 MP 트래커 정규장 마감 직후 중간 갱신(15:45).
#
# 20:05 본 실행(register_troy_mp_task_windows.ps1, 애프터마켓 마감 종가 확정분)과 별개로,
# 정규장(09:00~15:30) 마감 직후~애프터마켓(16:00~20:00) 시작 전 사이에 한 번 더 중간
# 스냅샷을 보고 싶다는 요청(2026-10-02 "15:30분 시간외 장이 끝나고 16시되기전에 한번
# 갱신시키는걸 추가하자")으로 추가했다. 15:30~16:00 사이 5분 버퍼를 두고 15:45로 잡았다
# (정규장 체결/정산 반영 지연 대비, 20:05가 20:00+5분인 것과 같은 이유).
#
# 이 시점엔 그날 지시서(mp_orders.csv)를 처리하면 안 된다 - 신규편입/swap의 매수가가
# 아직 확정 전 스냅샷으로 매매일지에 영구 고정되는 사고가 있었다(2026-10-01, 토모큐브
# 신규편입을 장중 조기실행 때 처리해서 확정 종가보다 50원 낮은 가격으로 박제된 뒤, 매매
# 자체는 "처리완료" 표시 때문에 20:05 본 실행에서 재처리되지 않았다). 그래서 이 중간
# 갱신은 run_troy_mp_pipeline.py --skip-orders로 돌려서 가격/페이지만 새로고침하고,
# 지시서 처리는 20:05 본 실행에만 맡긴다.
#
# Run:
#   powershell -ExecutionPolicy Bypass -File scripts\register_troy_mp_interim_task_windows.ps1
#
# Check:   Get-ScheduledTask -TaskName "KRX-TroyMP-Interim"
# Run now: Start-ScheduledTask -TaskName "KRX-TroyMP-Interim"
# Remove:  Unregister-ScheduledTask -TaskName "KRX-TroyMP-Interim" -Confirm:$false

$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent $PSScriptRoot
$PythonExe = Join-Path $ProjectDir ".venv\Scripts\python.exe"
$PipelineScript = Join-Path $ProjectDir "scripts\run_troy_mp_pipeline.py"
$TaskName = "KRX-TroyMP-Interim"

if (-not (Test-Path $PythonExe)) {
    throw "venv python not found: $PythonExe (run 'python -m venv .venv' first)"
}

$Action = New-ScheduledTaskAction -Execute $PythonExe -Argument "`"$PipelineScript`" --skip-orders" -WorkingDirectory $ProjectDir
$Trigger = New-ScheduledTaskTrigger -Daily -At 3:45PM
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopOnIdleEnd -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Description "트로이 MP 트래커 정규장 마감 직후 중간 갱신(15:45, 가격/페이지만 - 지시서 미처리)" -Force

Write-Host "Registered: '$TaskName' will run daily at 3:45 PM"
Write-Host "To test now: Start-ScheduledTask -TaskName `"$TaskName`""
