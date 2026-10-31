# 자동 실행 해제 (관리자 PowerShell)
Stop-ScheduledTask -TaskName "AcademyApp" -ErrorAction SilentlyContinue
Get-Process academy -ErrorAction SilentlyContinue | Stop-Process -Force
Unregister-ScheduledTask -TaskName "AcademyApp" -Confirm:$false
Write-Host "자동 실행을 해제했어요."
