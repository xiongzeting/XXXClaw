$ErrorActionPreference = 'Stop'
$taskRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$fixtureRoot = [IO.Path]::GetFullPath((Join-Path $taskRoot 'evals/fixtures'))
$archiveRoot = [IO.Path]::GetFullPath((Join-Path $taskRoot '.aster/evals/hard-authoring/rejected-fixtures'))
if (-not $archiveRoot.StartsWith($taskRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Unexpected archive location'
}
New-Item -ItemType Directory -Path $archiveRoot -Force | Out-Null
foreach ($draftName in @('hard-memory-v1', 'hard-delivery-v1', 'hard-safety-v1')) {
    $sourcePath = [IO.Path]::GetFullPath((Join-Path $fixtureRoot $draftName))
    $targetPath = [IO.Path]::GetFullPath((Join-Path $archiveRoot $draftName))
    if (-not $sourcePath.StartsWith($fixtureRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Unexpected source location'
    }
    if (-not $targetPath.StartsWith($archiveRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Unexpected destination location'
    }
    if (Test-Path -LiteralPath $targetPath) { throw 'Archive already exists; refusing overwrite' }
    if (Test-Path -LiteralPath $sourcePath) { Move-Item -LiteralPath $sourcePath -Destination $targetPath }
}
Write-Output 'Unexecuted rejected fixture drafts archived; reviewed and frozen fixtures unchanged'
