import json
from pathlib import Path

import pytest

from MiniClaw.evaluation.efficiency_observations import observe_request,finalize,FIELDS
from MiniClaw.evaluation.models import EvalCheck,load_eval_suite
from MiniClaw.evaluation.runner import evaluate_check,_dimension_scores,_aggregate_metrics,_combine_metrics
from MiniClaw.llm.openai_compatible import _StreamAccumulator

ROOT=Path(__file__).resolve().parents[1]

def request(inp=100,cached=50,output=10,known=True,rates=(2,0.5,4)):
    return {'type':'model.request','data':{'status':'success','purpose':'agent',
        'usage':{'input_tokens':inp,'cached_tokens':cached,'output_tokens':output,'total_tokens':inp+output},
        'output':{'metadata':{'usage_reported':known,'cache_usage_reported':known,
            **dict(zip(('input_cost_per_million','cached_input_cost_per_million','output_cost_per_million'),rates))}}}}

def test_weighted_cache_and_priced_input_are_not_double_counted():
    a=_aggregate_metrics({'a':[request()]});b=_aggregate_metrics({'b':[request(900,0,20)]})
    result=_combine_metrics([a,b])
    assert result['cache_hit_percent']==5
    assert result['estimated_cost_usd']==pytest.approx((50*2+50*.5+10*4+900*2+20*4)/1e6)

@pytest.mark.parametrize('row',[request(known=False),request(rates=(0,0,0)),request(rates=(None,1,1))])
def test_missing_usage_or_prices_do_not_mean_free(row):
    result=_aggregate_metrics({'a':[row]})
    assert result['estimated_cost_usd'] is None

def test_missing_cache_is_distinct_from_reported_zero():
    a=_StreamAccumulator(0);a.consume(json.dumps({'usage':{'prompt_tokens':100,'completion_tokens':5}}))
    assert a.usage_reported and not a.cache_usage_reported
    a.consume(json.dumps({'usage':{'prompt_tokens':100,'completion_tokens':5,'prompt_tokens_details':{'cached_tokens':0}}}))
    assert a.cache_usage_reported and a.usage.cached_tokens==0

def test_observations_never_change_dimension_score(tmp_path):
    original={'dimension':'efficiency','passed':False,'required':True,'options':{}}
    for value in (None,0,100):
        check=EvalCheck(type='metric',dimension='efficiency',required=False,options={'name':'cache_hit_percent','report_only':True,'unit':'%'})
        result=evaluate_check(check,tmp_path,tmp_path,{}, {},metrics={'cache_hit_percent':value})
        assert result['required'] is False
        assert result['status']==('unavailable' if value is None else 'observed')
        assert _dimension_scores([original,result])['efficiency']['score']==0

def test_current_partitions_and_next_suite():
    policy=json.loads((ROOT/'evals/current-round-partitions.json').read_text(encoding='utf-8'))
    for round_name,expected in [('round1',{'development':15,'test':15}),('round2',{'development':8,'test':7,'regression':5})]:
        group=policy['rounds'][round_name];assert group['counts']==expected
        ids=[]
        for split,count in expected.items():
            suite=load_eval_suite(ROOT/f'evals/{round_name}-{split}-current.json')
            assert len(suite.cases)==count
            assert all(c.source['split']==split for c in suite.cases)
            ids.extend(c.id for c in suite.cases)
        assert len(set(ids))==len(ids)==len(group['assignments'])
    next_suite=load_eval_suite(ROOT/'evals/next-quality-limits-v1.json')
    for case in next_suite.cases:
        assert case.source['split']!='retained'
        observations=[c for c in case.checks if c.options.get('report_only')]
        assert {c.options['name'] for c in observations}=={'cache_hit_percent','estimated_cost_usd'}
        assert all(not c.required and not ({'min','max','equals'} & c.options.keys()) for c in observations)
