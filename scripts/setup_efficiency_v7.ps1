$ErrorActionPreference = 'Stop'
$taskRoot = 'D:\MIniClaw'
$taskFiles = @('run_efficiency_revision', 'start_efficiency', 'verify_efficiency')
foreach ($taskName in $taskFiles) {
    $taskExtension = if ($taskName -eq 'start_efficiency') { '.ps1' } else { '.py' }
    $taskSource = Join-Path $taskRoot ('scripts\' + $taskName + '_v6' + $taskExtension)
    $taskTarget = Join-Path $taskRoot ('scripts\' + $taskName + '_v7' + $taskExtension)
    if (Test-Path -LiteralPath $taskTarget) { throw "Refusing to overwrite $taskTarget" }
    $taskText = [System.IO.File]::ReadAllText($taskSource).Replace('v6', 'v7')
    if ($taskName -eq 'run_efficiency_revision') {
        $taskText = $taskText.Replace("    shutil.copy2(ROOT/'.aster/evals/efficiency-revision-v7-smoke.json', OUT/'preflight-smoke.json')", '')
        $taskText = $taskText.Replace('alternating protocol failure guard', 'error feedback without intermediate verification pauses')
    }
    if ($taskName -eq 'verify_efficiency') {
        $taskText = $taskText.Replace("from verify_efficiency_v5 import MODULES", "MODULES = ['test_efficiency_revision_v5', 'test_efficiency_revision_v6', 'test_agent_loop', 'test_task_recovery']")
        $taskText = $taskText.Replace("CHANGED = [", "CHANGED = ['agent/loop.py', ")
    }
    [System.IO.File]::WriteAllText($taskTarget, $taskText, [System.Text.UTF8Encoding]::new($false))
}
