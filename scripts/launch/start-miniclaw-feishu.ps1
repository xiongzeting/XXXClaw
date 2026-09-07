param(
    [Parameter(Mandatory = $true)]
    [string]$EnvFile,
    [string]$Workspace
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "../..")).Path
if ([string]::IsNullOrWhiteSpace($Workspace)) {
    $Workspace = $projectRoot
}
$resolvedWorkspace = (Resolve-Path -LiteralPath $Workspace).Path
$resolvedEnvFile = (Resolve-Path -LiteralPath $EnvFile).Path
$env:PYTHONPATH = Join-Path $projectRoot "src"
$env:PYTHONUNBUFFERED = "1"

Set-Location -LiteralPath $projectRoot
python -m MiniClaw.platforms.feishu.cli --workspace $resolvedWorkspace --env-file $resolvedEnvFile
