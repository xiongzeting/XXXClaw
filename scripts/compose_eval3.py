import json, shutil
from pathlib import Path
root=Path('.')
old=json.loads((root/'.aster/evals/boundary-full-v10-jobs20/snapshot/evals/boundary-submissions-v10.json').read_text(encoding='utf-8'))
ids=['boundary_v1_config_migration_cli_contract','hard_v1_shipping_caps','hard_v1_version_resolution','boundary_v1_audit_bundle_integration','boundary_v1_compression_transaction_rollback','boundary_v1_compression_rule_priority_exceptions','boundary_v1_diagnose_and_patch_release']
oldcases=[c for c in old['cases'] if c['id'] in ids]
new=json.loads((root/'evals/miniclaw-eval3-new.json').read_text(encoding='utf-8'))['cases']
for c in oldcases: c.setdefault('source',{})['split']='development'
for i,c in enumerate(new): c['source']={'kind':'synthetic_eval3','campaign':'miniclaw-eval3','split':'development' if i<3 else 'test','difficulty':'hard'}
suite={'version':1,'name':'miniclaw-eval3','environment':old.get('environment',{}),'cases':oldcases+new}
out=root/'evals/miniclaw-eval3.json'; out.write_text(json.dumps(suite,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print('cases',len(suite['cases']))

