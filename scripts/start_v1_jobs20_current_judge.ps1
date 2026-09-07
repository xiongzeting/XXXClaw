$ErrorActionPreference = 'Stop'
$taskRoot = 'D:\MIniClaw'
$taskOutput = Join-Path $taskRoot '.aster\evals\boundary-v1-jobs20-current-judge'
if ((Test-Path -LiteralPath (Join-Path $taskOutput 'launch.json')) -or (Test-Path -LiteralPath (Join-Path $taskOutput 'execution.json'))) {
    throw 'Batch already launched; do not duplicate model work.'
}
$taskLaunch = @{
    FilePath = 'D:\anaconda3\envs\deep_learning\python.exe'
    ArgumentList = @('-X', 'utf8', 'D:\MIniClaw\scripts\run_v1_jobs20_current_judge.py', 'run')
    WorkingDirectory = $taskRoot
    WindowStyle = 'Hidden'
    RedirectStandardOutput = (Join-Path $taskOutput 'runner.stdout.log')
    RedirectStandardError = (Join-Path $taskOutput 'runner.stderr.log')
    PassThru = $true
}
$taskProcess = Start-Process @taskLaunch
@{pid=$taskProcess.Id; jobs=20; model='gpt-5.6-luna'; launched_at=(Get-Date).ToString('o')} |
    ConvertTo-Json | Set-Content -LiteralPath (Join-Path $taskOutput 'launch.json') -Encoding utf8
Write-Output "V1 batch started: $($taskProcess.Id)"
