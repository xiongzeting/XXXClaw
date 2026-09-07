$ErrorActionPreference='Stop'
Get-Process python -ErrorAction SilentlyContinue | Stop-Process -Force
$root=(Resolve-Path 'D:\MIniClaw\.aster\evals').Path
$target=Join-Path $root 'miniclaw-eval3-single20'
if(Test-Path -LiteralPath $target){Remove-Item -LiteralPath $target -Recurse -Force}
$env:MINICLAW_API_KEY='sk-53hUw7wlPY9nhFLwTPGKYIwteoRjetjLRC5hDsKt5X1W1XlD'
python scripts/run_eval3_single20.py
