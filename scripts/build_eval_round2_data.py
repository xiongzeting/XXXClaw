"""Apply evidence-backed display corrections while preserving frozen raw scores."""
import csv
import hashlib
import json
from eval_partition_policy import reclassify, metadata


def build_round2(root, out):
    # Once the completed reruns have explicit assistant verdicts, keep later
    # dashboard builds on this reviewed data instead of restoring old scores.
    if (root / '.aster/evals/assistant-outcome-review-v1-v10/verdicts.json').is_file():
        from build_eval_current_round2 import build_current
        return build_current(root, out)
    archive = root / 'docs/interview/eval no 2'
    source = archive / '原始成绩与复核标签.json'
    data = json.loads(source.read_text(encoding='utf-8'))
    cases = data['cases']
    assert len(cases) == data['raw_overall']['cases'] == 20
    assert sum(c['raw_passed'] for c in cases) == data['raw_overall']['passed'] == 5
    for dim, count in data['raw_strict_dimensions'].items():
        assert sum(c['raw_dimensions'][dim] for c in cases) == count
    for metric, field in [('total_tokens', 'tokens'), ('tool_calls', 'tool_calls'),
                          ('tool_errors', 'tool_errors'), ('compactions', 'compactions'),
                          ('paused_runs', 'pauses')]:
        assert sum(c[field] for c in cases) == data['totals'][metric]
    with (archive / '逐题结果.csv').open(encoding='utf-8-sig', newline='') as stream:
        csv_rows = {row['案例ID']: row for row in csv.DictReader(stream)}
    assert set(csv_rows) == {c['id'] for c in cases}
    for c in cases:
        row = csv_rows[c['id']]
        assert row['名称'] == c['name'] and int(row['总tokens']) == c['tokens']
        for dim, label in zip(data['raw_strict_dimensions'], ['结果', '过程', '效率', '安全', '可用性']):
            assert (row[label] == '通过') == c['raw_dimensions'][dim]
    review_paths = [root / '.aster/evals/boundary-campaign-v1/interim-review.json',
                    root / '.aster/evals/boundary-campaign-v1/final-contract-diagnostics.json']
    reviews = {}
    for path in review_paths:
        document = json.loads(path.read_text(encoding='utf-8'))
        for review in document['cases'] if isinstance(document, dict) else document:
            case_id = review.get('id', review.get('case'))
            assert case_id not in reviews
            reviews[case_id] = (review, str(path.relative_to(root)).replace('\\', '/'))
    for c in cases:
        c['display_dimensions'] = dict(c['raw_dimensions'])
        c['display_corrections'] = []
        c['display_notes'] = list(c['review_notes'])
        if c['review_label'] == 'oracle_contract_mismatch':
            review, evidence_path = reviews[c['id']]
            assert review['review_label'] == c['review_label']
            assert review.get('diagnostic_exit_code', review.get('exit_code')) == 0
            failures = [f for f in c['raw_failures'] if f['dimension'] == 'outcome']
            assert len(failures) == 1 and failures[0]['type'] == 'command'
            assert c['raw_dimensions']['outcome'] is False
            c['display_dimensions']['outcome'] = True
            c['display_corrections'].append({
                'dimension': 'outcome', 'from': False, 'to': True,
                'reason': '原检查超出题面约定；适配合法接口或结构后，诊断检查通过。按用户要求纠正展示成绩。',
                'source': evidence_path, 'diagnostic': review,
            })
            c['display_notes'] = ['结果误报已纠正：诊断通过'] + [
                note for note in c['review_notes'] if note != '结果判分契约错配']
        c['display_passed'] = all(c['display_dimensions'].values())
        c['display_failures'] = [f for f in c['raw_failures'] if not any(
            fix['dimension'] == f['dimension'] for fix in c['display_corrections'])]
    data['display_strict_dimensions'] = {dim: sum(c['display_dimensions'][dim] for c in cases)
                                         for dim in data['raw_strict_dimensions']}
    data['display_overall'] = {'passed': sum(c['display_passed'] for c in cases), 'cases': len(cases)}
    data['display_correction_count'] = sum(bool(c['display_corrections']) for c in cases)
    data['display_policy'] = ('按用户要求，将有成功诊断证据的判分契约误报改为结果通过；'
                              '其余维度沿用冻结规则。追加库存故障单列，不混作原检查成绩。'
                              '原始评分保留；没有重跑模型任务，不代表模型能力提升。')
    assert data['display_correction_count'] == 5
    assert data['display_strict_dimensions'] == dict(outcome=18, process=20, efficiency=6, safety=20, reliability=17)
    assert data['display_overall']['passed'] == 5
    data['sources'] = [{'path': str(p.relative_to(root)).replace('\\', '/'),
                        'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
                       for p in [*sorted(archive.iterdir()), *review_paths] if p.is_file()]
    data['cases']=[reclassify(c,'round2') for c in data['cases']]
    data['partition']=metadata('round2')
    payload = json.dumps(data, ensure_ascii=False, separators=(',', ':')).replace('<', '\\u003c').replace('\u2028', '\\u2028').replace('\u2029', '\\u2029')
    (out / 'round2-data.js').write_text('window.EVAL_ROUND2 = ' + payload + ';\n', encoding='utf-8')
    (out / 'round2-data.json').write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('Round 2: raw outcome 13/20 retained; 5 evidence-backed corrections => display outcome 18/20, overall 5/20.')
