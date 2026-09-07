import asyncio,os,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; OUT=ROOT/'.aster/evals/miniclaw-eval3-single10'; OUT.mkdir(parents=True,exist_ok=True)
import sys; sys.path.insert(0,str(ROOT/'src'))
from MiniClaw.evaluation.models import load_eval_suite
from MiniClaw.evaluation.runner import run_eval_suite
from MiniClaw.llm.env_file import merged_environment,read_env_file
async def main():
 env=merged_environment(read_env_file(ROOT/'.env')); env.update({'MINICLAW_EVAL_DEFER_JUDGE':'true','MINICLAW_PRIMARY_MODEL':'gpt-5.6-luna','MINICLAW_LLM_RETRY_BASE_SECONDS':'3','MINICLAW_LLM_RETRY_MAX_SECONDS':'12','MINICLAW_LLM_RETRY_JITTER_RATIO':'0.2','OPENAI_API_KEY':os.environ['MINICLAW_API_KEY']})
 suite=load_eval_suite(ROOT/'evals/miniclaw-eval3.json')
 report=await run_eval_suite(suite,output_directory=OUT/'run',environment=env,provider='primary',model_id='gpt-5.6-luna',selected_cases=None,jobs=10,repeat=1)
 (OUT/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8'); print(report['summary'])
os.environ.setdefault('MINICLAW_API_KEY','')
asyncio.run(main())

