"""Collect immutable task evidence for later judging; never mark pending answers passed."""
import asyncio
import hashlib
import json
from pathlib import Path
import time


PROGRAM_DIMENSIONS = ('process', 'efficiency', 'safety', 'reliability')


def score_program_dimensions(case, attempt, case_root, *, traces=None):
    """Score execution evidence independently; outcome remains pending."""
    from .runner import evaluate_check, _evaluate_budgets, _dimension_scores, read_trace_records
    if traces is None:
        traces = {}
        for phase_id, phase in attempt['phases'].items():
            path = Path(phase['trace_path'])
            records = read_trace_records(path) if path.is_file() else []
            run_ids = set(phase.get('run_ids') or [])
            traces[phase_id] = [r for r in records if not run_ids or r.get('run_id') in run_ids]
    checks = []
    for check in case.checks:
        if check.dimension not in PROGRAM_DIMENSIONS:
            continue
        if check.type == 'llm_rubric':
            raise ValueError('Non-outcome dimensions require program checks, not llm_rubric')
        checks.append(evaluate_check(check, Path(attempt['workspace']), Path(case_root),
            attempt['phases'], traces, metrics=attempt['metrics'],
            workspace_changes=attempt.get('workspace_changes', {})))
    checks.extend(_evaluate_budgets(case.budgets, attempt['metrics'],
                                   wall_seconds=attempt['duration_seconds']))
    return {'checks': checks, 'dimensions': _dimension_scores(checks),
            'outcome_status': 'pending', 'program_grading_status': 'complete', 'passed': None}


async def run_submission_suite(suite, output_directory, environment, selected_cases,
                               provider, model_id, jobs, repeat):
    from .runner import _run_case_attempt, _write_json, _combine_metrics
    if not 1 <= jobs <= 30:raise ValueError('jobs must be from 1 to 30')
    if repeat not in (None,1):raise ValueError('Submission collection expects one attempt per case')
    cases=[c for c in suite.cases if selected_cases is None or c.id in selected_cases]
    if selected_cases and selected_cases != {c.id for c in cases}:raise ValueError('Unknown selected case')
    output=Path(output_directory).resolve()
    if output.exists() and any(output.iterdir()):raise FileExistsError('Submission directory is not empty')
    output.mkdir(parents=True,exist_ok=True)
    gate=asyncio.Semaphore(jobs); started=time.perf_counter()
    async def run(case):
        async with gate:
            attempt=await _run_case_attempt(suite,case,output/'cases'/case.id/'attempt-001',environment,
                                           provider=provider,model_id=model_id,attempt_index=1)
            workspace=Path(attempt['workspace'])
            files={}
            # Model-controlled symlinks are evidence paths, never followed into other files.
            for path in workspace.rglob('*'):
                if '.aster' in path.relative_to(workspace).parts or path.is_symlink():continue
                if path.is_file():files[path.relative_to(workspace).as_posix()]=hashlib.sha256(path.read_bytes()).hexdigest()
            result={'id':case.id,'category':case.category,'source':case.source,**attempt,
                    'attempts':[attempt], 'artifact_sha256':files}
            _write_json(output/'cases'/case.id/'result.json',result)
            packet={'case_id':case.id,'judge_status':'pending','execution_error':attempt['error'],
                    'requirements':[{'phase':p.id,'prompt':p.prompt} for p in case.phases],
                    'answers':[{'phase':key,'final_answer':p.get('final_text',''),
                                'errors':p.get('errors',[]),'trace_path':p.get('trace_path')}
                               for key,p in attempt['phases'].items()],
                    'workspace':str(workspace),'artifact_sha256':files,'metrics':attempt['metrics'],
                    'original_checks':[{'type':c.type,'dimension':c.dimension,'options':c.options} for c in case.checks]}
            _write_json(output/'judge-packets'/(case.id+'.json'),packet)
            return result
    results=await asyncio.gather(*(run(c) for c in cases))
    summary={'suite':suite.name,'cases':len(results),'jobs':jobs,'grading_status':'pending',
             'passed':None,'failed':None,'elapsed_seconds':round(time.perf_counter()-started,3),
             'execution_errors':sum(bool(r['error']) for r in results),
             'metrics':_combine_metrics([r['metrics'] for r in results])}
    report={'summary':summary,'cases':results}
    _write_json(output/'report.json',report);_write_json(output/'summary.json',summary)
    (output/'report.md').write_text(f"# Eval submissions\n\n{len(results)} cases collected. Outcome pending LLM judge; process, efficiency, safety and reliability program-scored in report.json. Overall pass/fail remains pending.\n",encoding='utf-8')
    return report

