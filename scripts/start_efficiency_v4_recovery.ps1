$ErrorActionPreference = 'Stop'
$taskRoot = 'D:\MIniClaw'
$taskRecovery = Join-Path $taskRoot '.aster\evals\efficiency-revision-v4\recovery'
$taskStamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$taskLaunch = @{
    FilePath = 'D:\anaconda3\envs\deep_learning\python.exe'
    ArgumentList = @('-X', 'utf8', 'D:\MIniClaw\scripts\resume_efficiency_v4.py', 'run')
    WorkingDirectory = $taskRoot
    WindowStyle = 'Hidden'
    RedirectStandardOutput = (Join-Path $taskRecovery "resume-$taskStamp.stdout.log")
    RedirectStandardError = (Join-Path $taskRecovery "resume-$taskStamp.stderr.log")
    PassThru = $true
}
$taskProcess = Start-Process @taskLaunch
[pscustomobject]@{
    pid = $taskProcess.Id
    stdout = $taskLaunch.RedirectStandardOutput
    stderr = $taskLaunch.RedirectStandardError
    launched_at = (Get-Date).ToString('o')
} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $taskRecovery "launch-$taskStamp.json") -Encoding utf8
Write-Output "Detached recovery PID: $($taskProcess.Id)"
