param([string]$RepoPath = (Resolve-Path "$PSScriptRoot\..").Path)
$ErrorActionPreference = "Stop"
Set-Location $RepoPath
if (-not (Test-Path ".venv\Scripts\python.exe")) { py -3 -m venv .venv }
& ".venv\Scripts\python.exe" -m pip install --upgrade pip
& ".venv\Scripts\python.exe" -m pip install -r requirements.txt
$pythonw = (Resolve-Path ".venv\Scripts\pythonw.exe").Path
$arguments = "-m app.naver_local_worker watch --interval 300"
$action = New-ScheduledTaskAction -Execute $pythonw -Argument $arguments -WorkingDirectory $RepoPath
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName "Hamjju Naver Draft Worker" -Action $action -Trigger $trigger -Settings $settings -Description "Notion 네이버 저장 요청을 Naver 비공개 임시저장으로 전달" -Force | Out-Null
Write-Host "설치 완료. 최초 1회 로그인: .\.venv\Scripts\python.exe -m app.naver_local_worker login"
