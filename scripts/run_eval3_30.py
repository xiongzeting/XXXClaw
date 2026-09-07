import asyncio,sys,os,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; OUT=ROOT/'.aster/evals/miniclaw-eval3-30'; OUT.mkdir(parents=True,exist_ok=True)
# use current source and eval3 suite; fixtures are in evals/fixtures
sys.path.insert(0,str(ROOT/'src'))
from MiniClaw.evaluation.models import load_eval_suite
from MiniClaw.evaluation.runner import run_eval_suite
from MiniClaw.llm.env_file import merged_environment,read_env_file
async def main():
 env=merged_environment(read_env_file(ROOT/'.env')); env.update({'MINICLAW_EVAL_DEFER_JUDGE':'true','MINICLAW_PRIMARY_MODEL':'gpt-5.6-luna'})
 report=await run_eval_suite(load_eval_suite(ROOT/'evals/miniclaw-eval3-30.json'),output_directory=OUT/'run',environment=env,provider='primary',model_id='gpt-5.6-luna',selected_cases=None,jobs=30,repeat=1)
 (OUT/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8'); print(report.get('summary'))
asyncio.run(main())

