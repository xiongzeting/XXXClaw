"""Long, interleaved user conversations; all facts and oracles are synthetic.

Unlike the short controls, the requested answer is not repeated in the last turn.
The fixed 6k/9k stress profile exercises compaction in shared-session cases.
"""
from __future__ import annotations
import json
from build_dialogue_campaign import add, CASES, write_suite, hidden_test, edits_only


def notes(prefix, n=75):
    return "\n".join(
        f"{prefix}-{i:03d}：负责人 User{i:03d}；批大小 {20+i}；重试 {i%5}；工作区路径 teams/{prefix}/{i:03d}；备注已核对交接清单。"
        for i in range(n))


def stress():
    CASES[-1]["environment"] = {
        "MINICLAW_COMPACTION_SOFT_TRIGGER_TOKENS":"6000",
        "MINICLAW_COMPACTION_HARD_TRIGGER_TOKENS":"9000",
        "MINICLAW_COMPACTION_TARGET_TOKENS":"3000",
        "MINICLAW_COMPACTION_KEEP_RECENT_TOKENS":"1200",
    }
    CASES[-1]["source"]["stress_profile"] = "6k soft / 9k hard / 3k target / 1.2k recent"
    CASES[-1]["timeout_seconds"] = 720


for idx, prefix in enumerate(["Larch", "Willow"]):
    target = 37 + idx
    add(f"long_partial_update_{idx}", "memory-update", [
        "这是项目交接资料，之后还要接着用，请保存有用信息。暂时简短确认就好。\n"+notes(prefix),
        f"交接修订：{prefix}-{target:03d} 负责人已改为 NewOwner{idx}，重试改为 9；批大小和路径没变。请以后用修订后的记录。",
        "下面是另一个部门的资料，不要覆盖上一个部门的同编号项目。\n"+notes("Other"+prefix),
        f"继续上次交接：{prefix}-{target:03d} 现在负责人、批大小、重试次数和路径是什么？JSON：owner、batch_size、retries、path。"
    ], {"owner":f"NewOwner{idx}","batch_size":20+target,"retries":9,"path":f"teams/{prefix}/{target:03d}"})

for idx in range(2):
    prefix=f"Estate{idx}"; target=42+idx
    add(f"long_scope_{idx}", "memory-scope", [
        "请记住生产配置清单，后续会按项目询问，暂时简短确认。\n"+notes(prefix),
        "这是同一批项目的开发环境配置，只适用于 development，不得覆盖 production。\n"+
        "\n".join(f"{prefix}-{i:03d}：负责人 Dev{i:03d}，批大小 {200+i}，重试 0，路径 dev/{i:03d}。" for i in range(75)),
        "我们还讨论了给开发环境统一使用批大小 1000，但仅是提案，还未生效。请不要混淆计划和实际。",
        f"{prefix}-{target:03d} 生产和开发的实际批大小各是多少？JSON：production_batch、development_batch。"
    ], {"production_batch":20+target,"development_batch":200+target})

for idx in range(2):
    target=31+idx
    add(f"long_multihop_{idx}", "memory-multihop", [
        "记住项目到组件的对应关系，后面还有其他表。\n"+"\n".join(f"Project-{i:03d} 使用 Component-{(i*7)%89:03d}。" for i in range(89)),
        "继续组件归属表，请保留关联信息。\n"+"\n".join(f"Component-{i:03d} 归 Team-{(i*3)%97:03d} 管理。" for i in range(89)),
        "团队联系人表如下，暂时不用回答具体问题。\n"+"\n".join(f"Team-{i:03d} 联系人 Contact-{i:03d}，邮箱 contact{i:03d}@example.test。" for i in range(97)),
        f"Project-{target:03d} 用哪个组件，应该联系哪个邮箱？给 JSON：component、email。"
    ], {"component":f"Component-{(target*7)%89:03d}","email":f"contact{(((target*7)%89)*3)%97:03d}@example.test"})

for idx in range(2):
    prefix=f"Forget{idx}"; target=28+idx
    add(f"long_selective_forget_{idx}", "memory-forgetting", [
        "请长期保存这些项目交接记录，之后会用。\n"+notes(prefix),
        f"{prefix}-{target:03d} 的负责人记录已经撤销，请忘掉这个负责人的信息，不应再从旧交接资料补回；这个项目的其他字段都仍有效。",
        "现在接着看另一个团队的交接。\n"+notes("Parallel"),
        f"给我 {prefix}-{target:03d} 当前可用的负责人和工作区路径。撤销的负责人用 null。JSON：owner、path。"
    ], {"owner":None,"path":f"teams/{prefix}/{target:03d}"})

for idx in range(2):
    target=35+idx
    rows=[{"seq":i,"service":f"svc-{i%13}","version":f"v{i}","status":"success" if i%4 else "failed"} for i in range(1,121)]
    service=f"svc-{target%13}"
    last=max((r for r in rows if r['service']==service and r['status']=='success'),key=lambda r:r['seq'])
    add(f"long_deploy_history_{idx}", "temporal-evidence", [
        "这些是部署流水，先理解，不要修改任何东西。\n"+json.dumps(rows[:60]),
        "这是后半段流水。失败版本不替换运行版本，按 seq 代表的执行顺序判断。\n"+json.dumps(rows[60:]),
        "接下来是同一天的交接闲项，和部署结果没有关系。\n"+notes("Admin"),
        f"{service} 最后成功部署的版本和 seq 是什么？JSON：version、seq。"
    ], {"version":last['version'],"seq":last['seq']},shared=True)
    stress()

for idx in range(2):
    target=33+idx
    text="\n".join(f"Service-{i:03d} owner=User{i:03d}; staging observed availability={98+i%2}.5%; production SLA=not approved." for i in range(80))
    add(f"long_unknown_sla_{idx}", "negative-evidence", [
        "这是服务资料，仅记录已知事实，之后我会按服务来问。\n"+text,
        "这批资料里的 staging 观测值不能当成已签署的生产 SLA。暂时没有补充审批结果。",
        "顺便看一份其他部门交接信息。\n"+notes("Unrelated"),
        f"Service-{target:03d} 的负责人和已经批准的生产 SLA 百分比是多少？没有批准值则 null。JSON：owner、production_sla。"
    ], {"owner":f"User{target:03d}","production_sla":None},shared=True)
    stress()

write_suite("dialogue-long-history-development",CASES)
print(f"Built {len(CASES)} long-history conversations")
