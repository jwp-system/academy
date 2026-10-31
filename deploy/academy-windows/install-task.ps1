# 구축판을 Windows 작업 스케줄러에 등록한다: PC가 켜지면 자동 실행, 꺼지면 1분 뒤 다시 실행.
# 관리자 PowerShell에서:  powershell -ExecutionPolicy Bypass -File deploy\academy-windows\install-task.ps1
param(
    [string]$AppDir = (Resolve-Path "$PSScriptRoot\..\..").Path,
    [int]$Port = 8000
)
$ErrorActionPreference = "Stop"
$exe = Join-Path $AppDir ".venv\Scripts\academy.exe"
if (-not (Test-Path $exe)) { throw "먼저 '$AppDir' 에서 uv sync 를 실행해 주세요 ($exe 가 없어요)." }
New-Item -ItemType Directory -Force -Path (Join-Path $AppDir "data") | Out-Null
$log = Join-Path $AppDir "data\app.log"

# 프로세스 하나로만: 이미 실행 중이면 새로 띄우지 않는다 (스케줄러가 앱 안에서 돌아서 둘이면 문자가 두 번 감)
$action = New-ScheduledTaskAction -Execute "cmd.exe" `
    -Argument "/c `"`"$exe`" serve --host 127.0.0.1 --port $Port >> `"$log`" 2>&1`"" `
    -WorkingDirectory $AppDir
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest

Register-ScheduledTask -TaskName "AcademyApp" -Action $action -Trigger $trigger -Settings $settings `
    -Principal $principal -Description "학원 관리 시스템 (구축판)" -Force | Out-Null
Start-ScheduledTask -TaskName "AcademyApp"
Start-Sleep -Seconds 5
try {
    $r = Invoke-RestMethod "http://127.0.0.1:$Port/healthz"
    Write-Host "실행 중이에요: db=$($r.db), scheduler=$($r.scheduler)" -ForegroundColor Green
} catch {
    Write-Host "아직 응답이 없어요. 잠시 뒤 http://127.0.0.1:$Port/healthz 를 열어 보고, 안 되면 $log 를 확인해 주세요." -ForegroundColor Yellow
}
