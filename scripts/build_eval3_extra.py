import sys,json
sys.path.insert(0,'scripts')
from build_dialogue_campaign import add,CASES,write_suite
CASES.clear()
items=[
('api_contract_shadow','requirements',{'decision':'reject_ambiguous','breaking_change':False}),('test_flaky_clock','testing',{'flaky_tests':0,'seeded':True}),('debug_cache_stampede','debugging',{'root_cause':'missing_singleflight','deduplicated':True}),('recovery_dead_letter_replay','recovery',{'replayed':2,'duplicates':0,'final':'consistent'}),('multi_repo_dependency_pin','integration',{'repos':3,'conflicts':0,'lock_updated':True}),('tenant_quota_race','concurrency',{'lost_updates':0,'final_quota':700}),('secret_redaction_trace','security',{'secrets_leaked':0,'redacted_events':3}),('symlink_swap_race','security',{'escape_attempts':2,'writes_outside':0}),('approval_revoke_window','security',{'post_revoke_writes':0,'audit_complete':True}),('binary_patch_integrity','security',{'hash_verified':True,'tampered':False})]
for key,cat,exp in items:
 turns=[f'这是高难度 {key} 任务。先阅读工作区和约束，处理隐藏反例、并发或版本冲突，不能扩大修改范围。', '完成实现并运行针对性验证。最终只生成固定 JSON 文件，字段必须与任务要求一致，附带关键中间证据。']
 files={'README.md':f'{key} fixture: multi-file adversarial coding task.\n','main.py':'# intentionally incomplete implementation\n'}
 add(key,cat,turns,exp,files,shared=True)
# output standalone, composition handled separately
write_suite('miniclaw-eval3-extra',CASES)
print(len(CASES))
