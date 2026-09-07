param(
    [int]$Port = 8766
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "../..")).Path
$pythonExe = (Get-Command python -ErrorAction Stop).Source
$psi = [System.Diagnostics.ProcessStartInfo]::new()
$psi.FileName = $pythonExe
$psi.UseShellExecute = $false
$psi.CreateNoWindow = $true
$psi.WorkingDirectory = $projectRoot
$psi.Environment["PYTHONPATH"] = Join-Path $projectRoot "src"
$nativeArgs = @(
    "-m"
    "MiniClaw.frontend"
    "--host"
    "127.0.0.1"
    "--port"
    [string]$Port
)
foreach ($nativeArg in $nativeArgs) {
    [void]$psi.ArgumentList.Add($nativeArg)
}

$server = [System.Diagnostics.Process]::Start($psi)
try {
    Start-Sleep -Seconds 2
    $index = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/" -UseBasicParsing
    $svg = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/miniclaw-architecture.svg" -UseBasicParsing
    [pscustomobject]@{
        ServerPid = $server.Id
        IndexStatus = $index.StatusCode
        IndexType = $index.Headers["Content-Type"]
        SvgStatus = $svg.StatusCode
        SvgType = $svg.Headers["Content-Type"]
        SvgBytes = $svg.RawContentLength
    } | ConvertTo-Json
}
finally {
    if (-not $server.HasExited) {
        Stop-Process -Id $server.Id -Force
    }
}
