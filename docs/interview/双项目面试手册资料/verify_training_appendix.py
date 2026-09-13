import hashlib
import json
import math
import re
from pathlib import Path

root = Path(__file__).resolve().parent
doc = root.parent/'双项目与完整简历_面试精讲与口语回答大全.md'
body = doc.read_text(encoding='utf-8')
prior = json.loads((root/'verification.json').read_text(encoding='utf-8'))
prefix = body.split('<a id="chapter-31"></a>')[0].rstrip()+'\n'
original_preserved = hashlib.sha256(prefix.encode('utf-8')).hexdigest() == prior['sha256']
assert original_preserved, 'previous 30 chapters changed'
resources = json.loads((root/'training_resources_audit.json').read_text(encoding='utf-8'))
timers = json.loads((root/'training_loop_timers.json').read_text(encoding='utf-8'))
assert resources['sft']['devices'][0]['name'] == 'NVIDIA RTX PRO 6000 Blackwell Server Edition'
assert resources['sft']['peak_gpu_memory_gib'] == 89.64
assert resources['grpo100']['actor_updates'] == 98
assert resources['grpo230']['actor_updates'] == 172
assert len(resources['grpo230']['timing_s/step']['missing_steps']) == 58
assert math.isclose(resources['grpo230']['timing_s/step']['sum'], 9979.66058363812)
assert timers['observed_loop_seconds_through_step230_including_replayed_work'] == 11715
assert timers['observed_loop_seconds_through_last_logged_step231'] == 11743
for link in re.findall(r'\]\((D:/[^)]+)\)',body):
    path = re.sub(r':\d+$','',link)
    assert Path(path).exists(), path
assert body.count('```') % 2 == 0
assert '\ufffd' not in body
assert len(re.findall(r'^## \d{2}\.',body,re.M)) == 31
assert len(re.findall(r'^### 31\.',body,re.M)) == 9
shop = Path('D:/shopping-grpo-longhorizon-main2-reward-v4')
canonical = shop/'重点/训练过程曲线/GRPO-step230/grpo-step230-actor-updates.csv'
duplicate = shop/'重点/3.grpo阶段数据清洗及相关曲线/curves/GRPO-step230/grpo-step230-actor-updates.csv'
same_copy = hashlib.sha256(canonical.read_bytes()).hexdigest() == hashlib.sha256(duplicate.read_bytes()).hexdigest()
assert same_copy
result = {'status':'passed','original_30_chapters_preserved':True,'added_chapter':31,
          'added_subsections':9,'duplicate_csv_hash_matches':same_copy,
          'all_local_links_exist':True,'document_sha256':hashlib.sha256(doc.read_bytes()).hexdigest(),
          'scope':'Appendix sources, numeric aggregation, links and formatting. Existing illustrative examples unchanged; not rerun.'}
(root/'training_appendix_verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(result,ensure_ascii=True,indent=2))
