# Windows Task Scheduler: register two daily runs of the pipeline (08:00 and 19:00).
#
# 소스별로 데이터가 언제 올라오는지 확인해보니 시차가 있다:
#   - KRX/네이버(코스피 종가·거래대금·시가총액): 장마감(15:30) 이후 당일 데이터가 올라옴
#   - KOFIA FreeSIS(예탁금/CMA/신용거래융자/MMF): 보통 T-2일까지만 반영됨 (아침에 확인해봐도
#     이틀 전 날짜까지만 있음 - KOFIA 자체 발표 주기가 그런 것으로 보임)
#   - ECOS/FRED(월간 지표): 월 단위라 하루 중 시점은 거의 안 중요함
# 그래서 아침 8시 한 번만 돌리면 코스피는 항상 "어제까지"만 잡힌다. 저녁에 한 번 더 돌리면
# 그날 장마감 데이터까지 같은 날 반영되므로, 아침(전일 확정치 정리) + 저녁(당일 마감 반영)
# 두 번 실행으로 소스별로 그때그때 가장 최신 데이터를 받아오게 한다.
#
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
$MorningTrigger = New-ScheduledTaskTrigger -Daily -At 8:00AM
$EveningTrigger = New-ScheduledTaskTrigger -Daily -At 7:00PM
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopOnIdleEnd -ExecutionTimeLimit (New-TimeSpan -Hours 1)

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger @($MorningTrigger, $EveningTrigger) -Settings $Settings -Description "Daily auto-refresh for the Korea market liquidity dashboard (08:00 + 19:00)" -Force

Write-Host "Registered: '$TaskName' will run daily at 8:00 AM and 7:00 PM"
Write-Host "To test now: Start-ScheduledTask -TaskName `"$TaskName`""
