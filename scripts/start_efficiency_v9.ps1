$ErrorActionPreference = 'Stop'
$taskRoot = 'D:\MIniClaw'
$taskOutput = Join-Path $taskRoot '.aster\evals\efficiency-revision-v9'
if ((Test-Path -LiteralPath (Join-Path $taskOutput 'launch.json')) -or (Test-Path -LiteralPath (Join-Path $taskOutput 'execution.json'))) {
    throw 'Batch already launched; inspect or recover instead of duplicating.'
}
if (-not (Test-Path -LiteralPath (Join-Path $taskOutput 'freeze.json'))) {
    throw 'Prepare the immutable snapshot before launching.'
}
$taskLaunch = @{
    FilePath = 'D:\anaconda3\envs\deep_learning\python.exe'
    ArgumentList = @('-X', 'utf8', 'D:\MIniClaw\scripts\run_efficiency_revision_v9.py', 'run')
    WorkingDirectory = $taskRoot
    WindowStyle = 'Hidden'
    RedirectStandardOutput = (Join-Path $taskOutput 'runner.stdout.log')
    RedirectStandardError = (Join-Path $taskOutput 'runner.stderr.log')
    PassThru = $true
}
$taskProcess = Start-Process @taskLaunch
[pscustomobject]@{
    pid = $taskProcess.Id
    stdout = $taskLaunch.RedirectStandardOutput
    stderr = $taskLaunch.RedirectStandardError
    launched_at = (Get-Date).ToString('o')
    jobs = 6
    model = 'gpt-5.6-luna'
} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $taskOutput 'launch.json') -Encoding utf8
Write-Output "Detached v9 evaluation PID: $($taskProcess.Id)"

