# 업데이트 후 앱 다시 시작 (관리자 PowerShell)
$ErrorActionPreference = "Stop"
Stop-ScheduledTask -TaskName "AcademyApp"
Get-Process academy -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 2
Start-ScheduledTask -TaskName "AcademyApp"
Write-Host "다시 시작했어요." -ForegroundColor Green
