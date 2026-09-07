param(
    [string]$DestinationRoot = (Join-Path $PSScriptRoot '../../external/benchmarks')
)

$ErrorActionPreference = 'Stop'
$destination = [System.IO.Path]::GetFullPath($DestinationRoot)
$downloadRoot = Join-Path $destination '.downloads'
New-Item -ItemType Directory -Path $destination -Force | Out-Null
New-Item -ItemType Directory -Path $downloadRoot -Force | Out-Null

$benchmarks = @(
    [pscustomobject]@{ Name = 'bfcl-gorilla'; Repository = 'ShishirPatil/gorilla'; Ref = 'main' },
    [pscustomobject]@{ Name = 'ToolEmu'; Repository = 'ryoungj/ToolEmu'; Ref = 'main' },
    [pscustomobject]@{ Name = 'os-harm'; Repository = 'tml-epfl/os-harm'; Ref = 'main' },
    [pscustomobject]@{ Name = 'ToolSandbox'; Repository = 'apple/ToolSandbox'; Ref = 'main' },
    [pscustomobject]@{ Name = 'tau2-bench'; Repository = 'sierra-research/tau2-bench'; Ref = 'main' },
    [pscustomobject]@{ Name = 'docker-bench-security'; Repository = 'docker/docker-bench-security'; Ref = 'master' }
)

function Assert-ChildPath {
    param(
        [Parameter(Mandatory)] [string]$Root,
        [Parameter(Mandatory)] [string]$Candidate
    )
    $rootPath = [System.IO.Path]::GetFullPath($Root).TrimEnd([System.IO.Path]::DirectorySeparatorChar)
    $candidatePath = [System.IO.Path]::GetFullPath($Candidate)
    $prefix = $rootPath + [System.IO.Path]::DirectorySeparatorChar
    if (-not $candidatePath.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing path outside benchmark root: $candidatePath"
    }
    return $candidatePath
}

$status = foreach ($benchmark in $benchmarks) {
    $target = Assert-ChildPath -Root $destination -Candidate (Join-Path $destination $benchmark.Name)
    $archive = Assert-ChildPath -Root $downloadRoot -Candidate (
        Join-Path $downloadRoot "$($benchmark.Name)-$($benchmark.Ref).zip"
    )
    $url = "https://codeload.github.com/$($benchmark.Repository)/zip/refs/heads/$($benchmark.Ref)"
    $started = Get-Date
    try {
        if (Test-Path -LiteralPath $target -PathType Container) {
            [pscustomobject]@{
                name = $benchmark.Name
                repository = $benchmark.Repository
                ref = $benchmark.Ref
                url = $url
                status = 'skipped-existing-directory'
                target = $target
                archive = $archive
                elapsed_seconds = 0
                error = $null
            }
            continue
        }

        if (-not (Test-Path -LiteralPath $archive -PathType Leaf)) {
            $request = @{
                Uri = $url
                OutFile = $archive
                TimeoutSec = 180
                MaximumRetryCount = 2
                RetryIntervalSec = 2
            }
            Invoke-WebRequest @request
        }

        $archiveInfo = Get-Item -LiteralPath $archive
        if ($archiveInfo.Length -lt 1024) {
            throw "Downloaded archive is unexpectedly small: $($archiveInfo.Length) bytes"
        }

        $temporary = Assert-ChildPath -Root $destination -Candidate (
            Join-Path $destination ".extract-$($benchmark.Name)-$([guid]::NewGuid().ToString('N'))"
        )
        New-Item -ItemType Directory -Path $temporary | Out-Null
        $expand = @{
            LiteralPath = $archive
            DestinationPath = $temporary
            Force = $true
        }
        Expand-Archive @expand
        $topDirectories = @(Get-ChildItem -LiteralPath $temporary -Directory)
        if ($topDirectories.Count -ne 1) {
            throw "Expected one top-level directory in $archive, found $($topDirectories.Count)"
        }
        Move-Item -LiteralPath $topDirectories[0].FullName -Destination $target
        Remove-Item -LiteralPath $temporary

        [pscustomobject]@{
            name = $benchmark.Name
            repository = $benchmark.Repository
            ref = $benchmark.Ref
            url = $url
            status = 'ready'
            target = $target
            archive = $archive
            elapsed_seconds = [math]::Round(((Get-Date) - $started).TotalSeconds, 3)
            error = $null
        }
    }
    catch {
        [pscustomobject]@{
            name = $benchmark.Name
            repository = $benchmark.Repository
            ref = $benchmark.Ref
            url = $url
            status = 'failed'
            target = $target
            archive = $archive
            elapsed_seconds = [math]::Round(((Get-Date) - $started).TotalSeconds, 3)
            error = $_.Exception.Message
        }
    }
}

$manifest = Join-Path $destination 'download-manifest.json'
$status | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $manifest -Encoding utf8
$status | Format-Table name, status, elapsed_seconds, target, error -AutoSize -Wrap

if (@($status | Where-Object status -eq 'failed').Count -gt 0) {
    exit 1
}
