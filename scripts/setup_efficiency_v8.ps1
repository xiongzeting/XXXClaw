$ErrorActionPreference = 'Stop'
$taskRoot = 'D:\MIniClaw'
foreach ($taskName in @('run_efficiency_revision', 'start_efficiency', 'verify_efficiency')) {
    $taskExtension = if ($taskName -eq 'start_efficiency') { '.ps1' } else { '.py' }
    $taskSource = Join-Path $taskRoot ('scripts\' + $taskName + '_v7' + $taskExtension)
    $taskTarget = Join-Path $taskRoot ('scripts\' + $taskName + '_v8' + $taskExtension)
    if (Test-Path -LiteralPath $taskTarget) { throw "Refusing to overwrite $taskTarget" }
    $taskText = [System.IO.File]::ReadAllText($taskSource).Replace('v7', 'v8')
    if ($taskName -eq 'run_efficiency_revision') {
        $taskText = $taskText.Replace('v8 observation normalization, immutable evidence rebinding, error feedback without intermediate verification pauses', 'v8 explicit acceptance scenarios, shared structured recovery, scoped current and pending errors; no intermediate pause')
    }
    if ($taskName -eq 'verify_efficiency') {
        $taskText = $taskText.Replace("MODULES = ['test_efficiency_revision_v5', 'test_efficiency_revision_v6', 'test_agent_loop', 'test_task_recovery']", "from verify_efficiency_v5 import MODULES")
        $taskText = $taskText.Replace("modules = ['test_efficiency_revision_v8', *MODULES]", "modules = ['test_efficiency_revision_v8', 'test_efficiency_revision_v7', 'test_efficiency_revision_v6', *MODULES]")
    }
    [System.IO.File]::WriteAllText($taskTarget, $taskText, [System.Text.UTF8Encoding]::new($false))
}
$taskSmoke = Join-Path $taskRoot 'scripts\smoke_verification_v8.py'
if (Test-Path -LiteralPath $taskSmoke) { throw 'Refusing to overwrite smoke runner' }
$taskText = [System.IO.File]::ReadAllText((Join-Path $taskRoot 'scripts\smoke_verification_v6.py')).Replace('v6', 'v8')
[System.IO.File]::WriteAllText($taskSmoke, $taskText, [System.Text.UTF8Encoding]::new($false))
