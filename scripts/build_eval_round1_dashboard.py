"""Export first-run evidence for the static dashboard without changing scores."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from eval_partition_policy import reclassify, metadata

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'frontend/eval-round1'
DIMS = ['outcome', 'process', 'efficiency', 'safety', 'reliability']
# Only cases whose original evidence was reviewed as network-caused health
# failures are scored as passed under the user's display convention.
# A model error alone is not sufficient evidence for this adjustment.
NETWORK_ACCEPTED = {'ttl_lru_cache','tiered_invoice','interval_exclusions',
                    'schema_collision_migration','policy_scopes','largest_remainder_caps'}
TITLES = {
 'ledger_twins':'相似账本实体与撤回', 'dependency_waves':'依赖图与分批执行',
 'segmented_router':'多轮路由规则变更', 'release_gate_dependency_closure':'发布门禁与依赖闭包',
 'ttl_lru_cache':'TTL 与 LRU 缓存', 'tiered_invoice':'分级发票计算',
 'tenant_event_reconciliation':'租户事件对账', 'sessionize_events':'事件会话切分',
 'event_idempotency':'事件幂等与重放', 'temporal_revoke':'时间范围内的权限撤销',
 'multi_hop_assets':'跨会话资产关系追溯', 'json_pointer_transaction':'JSON Pointer 事务',
 'interval_exclusions':'区间差集与排除规则', 'incident_evidence_dedup_attribution':'事故证据去重与归属',
 'schema_collision_migration':'字段冲突与数据迁移', 'policy_scopes':'策略作用域',
 'largest_remainder_caps':'最大余数分配与上限', 'redaction_priority':'脱敏规则与优先级',
 'permission_interval_intersection':'权限窗口交集', 'reservation_compensation':'预留失败与补偿',
 'negative_identity':'实体区分与否定记忆', 'shipping_caps':'运费规则与封顶',
 'cash_refund_reconciliation':'支付退款与异常对账', 'cursor_page_contract':'游标分页契约',
 'scoped_routes':'跨作用域路由召回', 'version_resolution':'版本冲突与约束求解',
 'inventory_location_isolation':'库存位置隔离', 'weighted_interval_plan':'带权区间规划',
 'csv_formula_export':'CSV 公式安全导出', 'temporal_price_join':'价格的时间关联',
}

def read(relative):
    return json.loads((ROOT / relative).read_text(encoding='utf-8'))

def dimension(checks):
    return {d:all(x['passed'] for x in checks if x['dimension']==d and x.get('required',True)) for d in DIMS}

def brief(check):
    return {k:check[k] for k in ('type','dimension','required','passed','detail') if k in check}

def main():
    paths = ['.aster/evals/hard-campaign-v1/run/report.json',
             '.aster/evals/hard-campaign-v1/snapshot/evals/hard-campaign-v1.json',
             'evals/evidence/path-oracle-v2.1/regrade.json']
    report, suite, grade = map(read, paths)
    definitions={c['id']:c for c in suite['cases']}
    reviews={c['id']:c for c in grade['cases']}
    cases=[]
    for item in report['cases']:
        spec=definitions[item['id']]
        rev=reviews[item['id']]
        attempt=item['attempts'][0]
        original=attempt['checks']
        corrected=list(original)
        for replacement in rev['replaced_checks']:
            corrected[replacement['index']]=replacement['revised_check']
        assert all(c['passed'] for c in corrected if c.get('required',True))==rev['revised_score']['passed']
        family=spec['source']['family']
        phases=[]
        for phase in spec['phases']:
            result=attempt['phases'].get(phase['id'],{})
            phases.append({'id':phase['id'],'prompt':phase['prompt'],
                           'answer':result.get('final_text',''), 'errors':result.get('errors',[])})
        metrics=attempt['metrics']
        caps={c.get('options',{}).get('name'):c['options']['max'] for c in original
              if c['type']=='metric' and 'max' in c.get('options',{})}
        display_dimensions=dimension(corrected)
        network_adjusted=family in NETWORK_ACCEPTED
        if network_adjusted:
            assert not display_dimensions['reliability'] and metrics['model_errors']>0
            display_dimensions['reliability']=True
        display_passed=all(value is not False for value in display_dimensions.values()) and not attempt.get('error')
        cases.append({'id':item['id'],'family':family,'title':TITLES.get(family,family),
            'category':item['category'],'split':rev['split'],'phaseCount':len(spec['phases']),
            'originalPassed':rev['original_score']['passed'],'revisedPassed':rev['revised_score']['passed'],
            'originalDimensions':dimension(original),'revisedDimensions':dimension(corrected),
            'displayDimensions':display_dimensions,'displayPassed':display_passed,
            'networkAdjusted':network_adjusted,
            'networkAdjustmentReason':'用户明确要求：本轮已复核网络波动造成的可用性失败直接按通过计分；其余四维照常。原始错误保留。' if network_adjusted else None,
            'labels':rev['review_labels'], 'checks':{'original':list(map(brief,original)), 'revised':list(map(brief,corrected))},
            'metrics':{k:metrics.get(k,0) for k in ['total_tokens','input_tokens','output_tokens','tool_calls',
               'tool_errors','model_errors','model_retries','successful_runs','runs','compactions','history_archives',
               'memory_injected_items','agent_tokens','memory_tokens','compaction_tokens','cached_tokens']},
            'tokenCap':caps.get('total_tokens'), 'toolCap':caps.get('tool_calls'),
            'seconds':attempt['duration_seconds'],'phases':phases,'changes':attempt['workspace_changes'],
            'error':attempt.get('error'), 'pathAudit':[x['revised_check'].get('path_audit') for x in rev['replaced_checks']]})
    cases=[reclassify(c,'round1') for c in cases]
    data={'schema':1,'title':'MiniClaw 高难度 Eval 第一轮','model':'gpt-5.6-luna',
          'partition':metadata('round1'),
          'generatedAt':report['summary']['generated_at'], 'elapsedSeconds':report['summary']['elapsed_seconds'],
          'jobs':report['summary'].get('jobs',2), 'originalPassed':grade['original_passed'],
          'revisedPassed':grade['revised_passed'], 'cases':cases,
          'displayPassed':sum(c['displayPassed'] for c in cases),
          'networkAdjustedCount':sum(c['networkAdjusted'] for c in cases),
          'displayPolicy':'路径纠错后，按用户约定将6道已复核网络波动案例的可用性计为通过，分母不变；其余四维照常。原始错误不覆盖。',
          'sources':[{'path':p,'sha256':hashlib.sha256((ROOT/p).read_bytes()).hexdigest()} for p in paths]}
    assert len(cases)==30 and sum(c['phaseCount'] for c in cases)==161
    assert sum(c['originalPassed'] for c in cases)==7 and sum(c['revisedPassed'] for c in cases)==11
    assert [sum(c['revisedDimensions'][d] for c in cases) for d in DIMS]==[29,30,17,29,21]
    assert data['displayPassed']==15 and data['networkAdjustedCount']==6
    assert [sum(c['displayDimensions'][d] for c in cases) for d in DIMS]==[29,30,17,29,27]
    OUT.mkdir(parents=True,exist_ok=True)
    payload=json.dumps(data,ensure_ascii=False,separators=(',',':')).replace('<','\\u003c').replace('\u2028','\\u2028').replace('\u2029','\\u2029')
    (OUT/'data.js').write_text('window.EVAL_ROUND1 = '+payload+';\n',encoding='utf-8')
    (OUT/'round1-data.json').write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    from build_eval_round2_data import build_round2
    build_round2(ROOT, OUT)
    if (OUT/'index.html').exists():
        html=(OUT/'index.html').read_text(encoding='utf-8')
        for stylesheet in ('styles.css','improvements.css','round2.css'):
            html=html.replace(f'<link rel="stylesheet" href="{stylesheet}">','<style>'+(OUT/stylesheet).read_text(encoding='utf-8')+'</style>')
        html=html.replace('<script src="data.js"></script>','<script>window.EVAL_ROUND1 = '+payload+';</script>')
        html=html.replace('<script src="app.js"></script>','<script>'+(OUT/'app.js').read_text(encoding='utf-8')+'</script>')
        for script in ('round2-data.js', 'round2.js'):
            html=html.replace(f'<script src="{script}"></script>', '<script>'+(OUT/script).read_text(encoding='utf-8')+'</script>')
        (OUT/'dashboard.html').write_text(html,encoding='utf-8')
    print('30 cases / 161 phases exported; original 7, corrected 11; strict dimension counts verified.')

if __name__=='__main__':
    main()
