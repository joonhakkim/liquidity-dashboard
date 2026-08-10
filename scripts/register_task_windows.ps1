# Windows Task Scheduler: register a single daily run of the pipeline (07:30).
#
# 예전엔 아침 8시 + 저녁 7시 하루 두 번 돌렸는데(장마감 15:30 이후 당일 코스피 데이터를
# 저녁 실행에서 잡으려고), 사용자가 모든 자동갱신을 아침 7시 30분 한 번으로 통일해달라고
# 해서 단일 실행으로 변경. 이 경우 KRX/네이버 당일 종가는 다음날 아침 실행에서나 반영된다
# (하루 지연) - KOFIA/ECOS/FRED 등 월간·D-2 지표는 어차피 아침 실행으로도 충분히 최신.
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
$MorningTrigger = New-ScheduledTaskTrigger -Daily -At 7:30AM
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopOnIdleEnd -ExecutionTimeLimit (New-TimeSpan -Hours 1)

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $MorningTrigger -Settings $Settings -Description "Daily auto-refresh for the Korea market liquidity dashboard (07:30)" -Force

Write-Host "Registered: '$TaskName' will run daily at 7:30 AM"
Write-Host "To test now: Start-ScheduledTask -TaskName `"$TaskName`""
