$ErrorActionPreference='Stop'
$targets=@('.aster/evals/miniclaw-eval3-30','.aster/evals/miniclaw-eval3/run','.aster/evals/miniclaw-eval3/report.json')
foreach($t in $targets){$p=[IO.Path]::GetFullPath($t); if($p -like 'D:\MIniClaw\*' -and (Test-Path -LiteralPath $p)){Remove-Item -LiteralPath $p -Recurse -Force}}
python scripts/run_eval3.py
