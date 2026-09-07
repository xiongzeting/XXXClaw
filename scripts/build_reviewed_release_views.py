"""Non-mutating views replacing invalid items with separately frozen new cases."""
import json,hashlib
from collections import Counter
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];E=ROOT/'evals'
def read(name):return json.loads((E/name).read_text(encoding='utf-8'))
replacements={'retained':[],'test':[]}
reasons={
 'dialogue_recall_retracted_contact':'联系人为空没有明确 null/空字符串。',
 'dialogue_complete_json_cli':'未明确 CLI 的输入参数是文件路径还是 JSON 字符串。',
 'dialogue_complete_recursive_manifest':'未明确相对路径的参照目录。',
 'fresh_test_feature_matrix':'enabled 状态未明确必须布尔值。',
 'fresh_test_nested_contract':'required 状态未明确必须布尔值。',
 'fresh_test_execution_boundary':'would_execute 可理解为脚本假设执行的命令列表。',
 'fresh_test_stale_claim':'impact 字段没有明确整数类型。',
 'fresh_test_markdown_index':'未明确 index.json 位于仓库根目录。',
 'fresh_test_checksum_manifest':'未明确 manifest.json 位于仓库根目录及根 JSON 形状。',
}
for file in ['fresh-retained-replacement-v1.json','fresh-test-replacements-v1.json','precise-split-replacements-v1.json']:
    s=read(file)
    for i,c in enumerate(s['cases']):
        split='retained' if c['source'].get('split')=='retained' else 'test'
        if file=='fresh-test-replacements-v1.json':
            c['source']['replaces']=['fresh_test_feature_matrix','fresh_test_nested_contract'][i]
        c['environment']={**s.get('environment',{}),**c.get('environment',{})}
        replacements[split].append(c)
manifest={'excluded_invalid_cases':reasons,'replacements':{},'policy':'Original frozen artifacts preserved. Reviewed release views are amended evaluations, not pristine unseen benchmarks.'}
for split in replacements:
    d=read('fresh-'+split+'-v1.json')
    excluded={c['source']['replaces'] for c in replacements[split]}
    d['cases']=[c for c in d['cases'] if c['id'] not in excluded]+replacements[split]
    d['name']=split+'-release-v2'
    d['coverage']={'dimensions':['outcome','process','efficiency','safety','reliability']}
    d['exposure_policy']=manifest['policy']
    assert len(d['cases'])==25
    assert Counter(c['category'] for c in d['cases'])==dict.fromkeys(['compression','recall','tools','safety','completion'],5)
    path=E/(split+'-release-v2.json')
    path.write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    manifest['replacements'][split]={c['source']['replaces']:c['id'] for c in replacements[split]}
    manifest.setdefault('release_hashes',{})[path.name]=hashlib.sha256(path.read_bytes()).hexdigest()
(E/'reviewed-split-release-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print('25 retained + 25 test cases in reviewed views; 9 invalid original cases retained separately')
