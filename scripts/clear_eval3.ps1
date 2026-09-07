$ErrorActionPreference='Stop'
Get-Process python -ErrorAction SilentlyContinue | Stop-Process -Force
$root=(Resolve-Path 'D:\MIniClaw\.aster\evals').Path
Get-ChildItem -LiteralPath $root -Directory -Filter 'miniclaw-eval3-rerun2-shard-*' | ForEach-Object { $target=$_.FullName; if(-not $target.StartsWith($root+'\')){throw 'unsafe target'}; Remove-Item -LiteralPath $target -Recurse -Force }
