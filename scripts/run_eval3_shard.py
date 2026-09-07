import asyncio,os,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; idx=int(os.environ['MINICLAW_SHARD']); key=os.environ['MINICLAW_API_KEY']
OUT=ROOT/f'.aster/evals/miniclaw-eval3-shard-{idx}'; OUT.mkdir(parents=True,exist_ok=True)
import sys; sys.path.insert(0,str(ROOT/'src'))
from MiniClaw.evaluation.models import load_eval_suite
from MiniClaw.evaluation.runner import run_eval_suite
from MiniClaw.llm.env_file import merged_environment,read_env_file
suite=load_eval_suite(ROOT/'evals/miniclaw-eval3.json'); ids=[c.id for c in suite.cases][idx*4:(idx+1)*4]
async def main():
 env=merged_environment(read_env_file(ROOT/'.env')); env.update({'MINICLAW_EVAL_DEFER_JUDGE':'true','MINICLAW_PRIMARY_MODEL':'gpt-5.6-luna','OPENAI_API_KEY':key})
 report=await run_eval_suite(suite,output_directory=OUT/'run',environment=env,provider='primary',model_id='gpt-5.6-luna',selected_cases=set(ids),jobs=4,repeat=1)
 (OUT/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
asyncio.run(main())

