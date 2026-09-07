"""Remove basic cases from active main coverage without deleting frozen history."""
import copy,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; E=ROOT/'evals'
BASIC={
 'dialogue_cap_recall_alias','dialogue_cap_recall_numerical_similarity','dialogue_cap_recall_preference_exception',
 'dialogue_cap_tool_quoted_paths','dialogue_cap_tool_discover_nested','dialogue_cap_complete_cli',
 'dialogue_recall_warehouse_alias','dialogue_recall_region_owner','dialogue_recall_exception_preference',
 'dialogue_recall_joined_routes','dialogue_compression_endpoint','dialogue_compression_retention',
 'dialogue_compression_status','dialogue_compression_literal','dialogue_compression_export_flags',
 'dialogue_tool_fix_and_verify','dialogue_tool_csv_unicode_path','dialogue_tool_recover_missing_dir',
 'dialogue_complete_package_export','dialogue_complete_csv_roundtrip',
 'dialogue_retained_file_cli_contract','dialogue_retained_root_recursive_manifest',
 'fresh_test_railway_code','fresh_test_schema_owner','fresh_test_shift_assignment',
 'fresh_test_stock_levels','fresh_test_retraction','fresh_test_inventory_report',
 'fresh_test_html_summary','dialogue_test_root_markdown_index','dialogue_test_root_checksum_manifest',
}

def main():
 cases=[]; moves=[]
 for name in ('measured-development-v2.json','retained-release-v2.json','test-release-v2.json'):
  suite=json.loads((E/name).read_text(encoding='utf-8-sig'))
  for c in suite['cases']:
   if c['id'] not in BASIC: continue
   c=copy.deepcopy(c); c['environment']={**suite.get('environment',{}),**c.get('environment',{})}
   c['source'].update(track='smoke-basic',prior_suite=name,exposure='previously executed; excluded from active challenge coverage')
   cases.append(c)
   moves.append({'id':c['id'],'original_suite':name,'destination':'smoke-basic-v3.json',
                 'reason':'Basic field recall, small direct transformation, or short single-artifact delivery; retain as mechanism smoke coverage.'})
 assert {c['id'] for c in cases}==BASIC
 for name,obj in [('smoke-basic-v3.json',{'version':1,'name':'smoke-basic-v3','cases':cases}),
                  ('difficulty-migration-v3.json',{'basic_cases':len(cases),'moves':moves,
                   'main_replacement':'main-challenge-v3.json','legacy_cases_no_longer_main':96,
                   'policy':'Only the explicitly listed basic cases are called smoke. Other prior cases remain historical baselines or known-failure regressions. No original frozen suite was rewritten or deleted.'})]:
  (E/name).write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 print(f'{len(cases)} basic cases moved to smoke view; old 96-case suites retained as historical baselines')

if __name__=='__main__':main()
