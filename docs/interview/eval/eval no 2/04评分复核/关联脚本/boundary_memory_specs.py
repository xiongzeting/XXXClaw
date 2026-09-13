"""Hand-authored memory-boundary cases; data only, no model or file side effects."""
from __future__ import annotations

_R = {"MINICLAW_MEMORY_CONSOLIDATION_ENABLED": "true", "MINICLAW_SESSION_MODE": "isolated"}
_C = {"MINICLAW_COMPACTION_ENABLED":"true","MINICLAW_PROGRESSIVE_COMPACTION_ENABLED":"true","MINICLAW_COMPACTION_SOFT_TRIGGER_TOKENS":"4500","MINICLAW_COMPACTION_HARD_TRIGGER_TOKENS":"6500","MINICLAW_COMPACTION_TARGET_TOKENS":"2500","MINICLAW_COMPACTION_KEEP_RECENT_TOKENS":"900","MINICLAW_MEMORY_CONSOLIDATION_ENABLED":"false"}

def _batch(prefix, scope):
    return "\n".join(f"{prefix} historical record {i:02d}: scope={scope}; status=closed; owner=team-{i%7}; checksum={i*19%101}; do not alter current case" for i in range(80))

def _recall(key, split, phases, expected, derivation, negatives):
    return {"key":key,"category":"recall","split":split,"phases":phases,"files":{"README.md":"Task facts are absent from this fixture.\n"},"allowed":[],"expected":expected,"expected_derivation":derivation,"negative_expected":negatives,"environment":dict(_R),"process_checks":[{"type":"trace_event","event":"memory.retrieval","phase":f"turn{len(phases)}","where_min":{"data.injected_count":1},"dimension":"process"}]}

def _compression(key, split, phases, expected, derivation, negatives):
    return {"key":key,"category":"compression","split":split,"phases":phases,"files":{"README.md":"Case data arrives only in dialogue.\n"},"allowed":[],"expected":expected,"expected_derivation":derivation,"negative_expected":negatives,"environment":dict(_C),"process_checks":[{"type":"metric","name":"compactions","min":2,"dimension":"process"},{"type":"metric","name":"history_archives","min":1,"dimension":"process"}]}

SPECS = [
_recall("recall_entitlement_setops","development",[
"请明确把这组事实保存为长期记忆，后续每轮都是隔离新会话。八个主体分别是 alice、bea、cory、dina（员工）和 red、blue、green、gold（服务账户）。四条独立映射：alice→red、bea→blue、cory→green、dina→gold；red→{read,deploy}，blue→{read}, green→{deploy}, gold→{read,delete}。不要按名字或出现顺序合并主体。",
"长期记忆更新：组 A={alice,bea,cory}，组 B={bea,cory,dina}。组 A 与 B 的交集、并集、差集是可计算事实；服务账户权限仍按各自映射保存。另记：red 和 gold 属于 production，blue 和 green 属于 staging。",
"权限撤销：从2026-09-10T00:00Z起 red 的 deploy 被撤销，但 read 仍有效；gold 的 delete 仅在 maintenance 角色可用。本题两次查询都在 maintenance 角色下，员工可用权限指经映射代理服务账户执行的权限，不是给员工身份直接授予权限；alice 的员工身份仍在。",
"新会话查询两个时点：在 2026-09-09 和 2026-09-11，针对组 A∩B、组 A∪B、A-B、以及员工通过映射可用的权限集合，计算 production 服务账户数。最终只输出严格 JSON，键为 intersection、union、a_minus_b、employee_permissions_by_date、production_service_count_by_date；集合数组按字典序，权限按字典序，不能解释或回显题目。不要重述题目。"],
{"intersection":["bea","cory"],"union":["alice","bea","cory","dina"],"a_minus_b":["alice"],"employee_permissions_by_date":{"2026-09-09":{"alice":["deploy","read"],"bea":["read"],"cory":["deploy"],"dina":["delete","read"]},"2026-09-11":{"alice":["read"],"bea":["read"],"cory":["deploy"],"dina":["delete","read"]}},"production_service_count_by_date":{"2026-09-09":2,"2026-09-11":2}},
"Set operations use four employees; permissions require employee→service→permission composition, date-sensitive revocation, role-scoped delete, and production counting.",
[{"intersection":["bea","cory"],"union":["alice","bea","cory","dina"],"a_minus_b":["alice"],"employee_permissions_by_date":{"2026-09-09":{"alice":["deploy","read"],"bea":["read"],"cory":["deploy"],"dina":["delete","read"]},"2026-09-11":{"alice":["deploy","read"],"bea":["read"],"cory":["deploy"],"dina":["delete","read"]}},"production_service_count_by_date":{"2026-09-09":2,"2026-09-11":1}},{"intersection":["bea","cory"],"union":["alice","bea","cory","dina"],"a_minus_b":[],"employee_permissions_by_date":{},"production_service_count_by_date":{"2026-09-09":2,"2026-09-11":2}}]),
_recall("recall_event_windows_late_fix","retained",[
"请保存为长期记忆，后续隔离新会话查询事件有效性。设备 D-9 的告警规则 R1 在 2026-10-01 00:00Z 至 06:00Z 有效，阈值 70；R2 在 06:00Z 至 12:00Z 有效，阈值 50。窗口左闭右开。",
"记住三条事件：e1 发生于 05:59、读数 72；e2 发生于 06:00、读数 49；e3 发生于 11:59、读数 55。事件时间决定规则，接收时间无关。",
"迟到更正：一条 05:59 的 e1 更正记录于 07:00 才到达，读数改为 68；另一条迟到消息声称 e2 时间是 05:59，但来源序列号较低且已被更正记录明确废弃。",
"新增有效窗口：R3 在 2026-10-02 00:00Z 至 2026-10-02 02:00Z 有效，阈值 80；D-9 在该窗口收到 e4（事件时间 01:30，读数 81）。不要把窗口跨日延伸。",
"新会话查询两个时点 2026-10-01T05:59:30Z 与 2026-10-02T01:30:00Z：只输出 JSON，键为 first_active_rule、first_triggered_event_ids、second_active_rule、second_triggered_event_ids、corrected_e1_value；事件数组按事件 ID 排序。每个数组只包含事件时间落在该时点的有效规则窗口内且不晚于查询时点、读数大于等于阈值的事件；这是事后复盘，全部采用最终有效更正值，不按当时是否收到更正过滤。前三条事件均发生于2026-10-01，e4发生于2026-10-02。"],
{"first_active_rule":"R1","first_triggered_event_ids":[],"second_active_rule":"R3","second_triggered_event_ids":["e4"],"corrected_e1_value":68},"Requires half-open windows, event-time versus arrival-time, late correction precedence, cross-day scope, and threshold evaluation at two query times.",
[{"first_active_rule":"R1","first_triggered_event_ids":["e1"],"second_active_rule":"R3","second_triggered_event_ids":["e4"],"corrected_e1_value":72},{"first_active_rule":"R2","first_triggered_event_ids":[],"second_active_rule":"R3","second_triggered_event_ids":["e4"],"corrected_e1_value":68}]),
_recall("recall_evidence_conflict_unknown","test",[
"请明确保存长期记忆，后续隔离新会话查询。研究样本 S-1 的已确认字段：species=tern、cohort=C7；S-2 的已确认字段：species=ibis、cohort=C8。样本标签相似不能合并。",
"记住来源等级：实验日志（lab-log）优先于口头记录（verbal），审计签名记录（signed-audit）优先于两者。若高等级来源只说字段未知，不能用低等级来源填补。",
"冲突记录：verbal 声称 S-1 的 site=delta、collector=Ivo；lab-log 确认 site=delta，并明确 collector=未知（不是省略字段）。signed-audit 确认 S-2 的 collector=Uma，未提供 site。来源等级按字段分别判断；未提供字段不撤回其他已确认字段，明确未知才覆盖低等级值。",
"更正与撤回：一条未签名便笺把 S-1 species 改成 gull，已正式撤回；signed-audit 将 S-1 的 cohort 更正为 C7b。S-2 的 C8 未变。",
"新会话查询 S-1 和 S-2，只输出严格 JSON，键为 s1、s2；每个值必须有 species、cohort、site、collector 四键，未知为 JSON null。严格按来源等级处理，不能从同类样本、姓名或常识猜测。"],
{"s1":{"species":"tern","cohort":"C7b","site":"delta","collector":None},"s2":{"species":"ibis","cohort":"C8","site":None,"collector":"Uma"}},"Combines source precedence, explicit unknowns, signed correction, withdrawn low-authority evidence, and two entities.",
[{"s1":{"species":"gull","cohort":"C7b","site":"delta","collector":"Ivo"},"s2":{"species":"ibis","cohort":"C8","site":None,"collector":"Uma"}},{"s1":{"species":"tern","cohort":"C7","site":"delta","collector":"Ivo"},"s2":{"species":"ibis","cohort":"C8","site":"delta","collector":"Uma"}}]),
_compression("compression_transaction_rollback","development",[
"维护一个订单事务案例，先只确认会话约束，不交付。订单 O-44 初始 items=[A,B,C]，每项qty=1、unit=10；total=30、status=pending、shipping=standard。items最终每项只能有sku、qty、unit三个键；后续修订按字段处理，total始终由有效明细重算。",
"修订补丁 P1：把 B 数量从 1 改为 3，total=50；并把 shipping 改为 express。",
"修订补丁 P2：在 P1 之后新增 D，total=58；status 仍 pending。",
_batch("订单审核","O-44 transaction patches P1/P2")+"\n只作为相关历史索引，不覆盖当前事务。",
"撤回 P1：审批撤销 P1 的全部字段修改，但 P2 是独立后继补丁，必须保留其新增 D；回滚后 items=[A,B,C,D]，B 数量回到1，shipping 回到 standard，total 应按有效明细重算。",
"最终修订 P3：P2 保留，status 改为 approved，且 D 数量为2、单价8；先保留规则不交付，最后输出严格 JSON，键为 items、total、status、shipping。items 必须是数组对象，按出现顺序。",
_batch("订单归档","other orders; unrelated to O-44")+"\n不要采纳其中的 O-44 旧快照。","完成最终案例推导，只返回 JSON，不写说明。"],
{"items":[{"sku":"A","qty":1,"unit":10},{"sku":"B","qty":1,"unit":10},{"sku":"C","qty":1,"unit":10},{"sku":"D","qty":2,"unit":8}],"total":46,"status":"approved","shipping":"standard"},"Rollback removes P1 while preserving P2/P3; 10+10+10+2*8=46.",
[{"items":[{"sku":"A","qty":1,"unit":10},{"sku":"B","qty":3,"unit":10},{"sku":"C","qty":1,"unit":10},{"sku":"D","qty":2,"unit":8}],"total":76,"status":"approved","shipping":"express"},{"items":[{"sku":"A","qty":1,"unit":10},{"sku":"B","qty":1,"unit":10},{"sku":"C","qty":1,"unit":10}],"total":30,"status":"pending","shipping":"standard"}]),
_compression("compression_rule_priority_exceptions","retained",[
"维护 Nova 访问判定案例，只维护当前会话约束。判定先选最高作用域（user高于service高于global），该作用域内deny高于allow；没有匹配规则则deny。请求字段含actor、service、action、environment。",
"规则 R1：global allow read；R2：global deny delete；R3：service=reports allow export；R4：service=reports deny export for environment=prod。",
"局部例外 E1：actor=maya、service=reports、environment=prod、action=export，在 2026-11-03T10:00Z-11:00Z 临时 allow，优先级高于 service deny；其他 actor 不适用。",
_batch("安全审计","reports prod authorization history")+"\n这些是已关闭的历史变更，只保留明确标记的当前规则。",
"撤回 E1：临时例外从未生效，删除整条 E1。新增 E2：actor=lee 对 service=reports 的 export 在 staging 永久 allow；不影响 prod。",
"版本冲突：旧策略快照称 global deny read，但版本号低于 R1 且已 superseded；R1 仍生效。请在压缩后推导三条实际请求：maya/prod/export（2026-11-03T10:30Z）、lee/staging/export、lee/prod/export。",
_batch("合规归档","other services and actors; no current override")+"\n忽略与三条请求无关的旧快照。","只输出严格 JSON，键为 decisions，数组按请求给定顺序；每项只有 actor、service、action、environment、decision 五键，decision 只能 allow 或 deny。"],
{"decisions":[{"actor":"maya","service":"reports","action":"export","environment":"prod","decision":"deny"},{"actor":"lee","service":"reports","action":"export","environment":"staging","decision":"allow"},{"actor":"lee","service":"reports","action":"export","environment":"prod","decision":"deny"}]},"Requires precedence evaluation, revoked local exception removal, environment scope, and rejection of superseded snapshots.",
[{"decisions":[{"actor":"maya","service":"reports","action":"export","environment":"prod","decision":"allow"},{"actor":"lee","service":"reports","action":"export","environment":"staging","decision":"allow"},{"actor":"lee","service":"reports","action":"export","environment":"prod","decision":"allow"}]},{"decisions":[{"actor":"maya","service":"reports","action":"export","environment":"prod","decision":"deny"},{"actor":"lee","service":"reports","action":"export","environment":"staging","decision":"deny"},{"actor":"lee","service":"reports","action":"export","environment":"prod","decision":"deny"}]}]),
_compression("compression_dependency_lock_retraction","test",[
"维护 Atlas 构建案例，只维护会话约束。初始锁定 packages core=2.4.0、crypto=1.8.2、adapter=5.1.0；core无依赖，crypto依赖core，adapter依赖crypto和core。构建目标linux-x64，输出数组必须依赖在前。",
"提案 V1：升级 core=2.5.0，并要求 crypto>=1.9.0；尚未批准。",
"正式版本 V2：只升级 adapter=5.2.0，依赖 core=2.4.0、crypto=1.8.2 不变；锁文件 hash=h2。",
_batch("构建流水线","Atlas dependency lock history")+"\n仅将带正式版本标记的锁定视为当前。",
"撤回 V2：adapter=5.2.0 发布被撤回，恢复 adapter=5.1.0；V1 仍未批准，不能顺势升级 core 或 crypto。",
"正式版本 V3：crypto=1.9.1，且 adapter=5.1.0 的兼容范围允许该版本；core 保持2.4.0，hash=h3。另有旧提案声称 core=2.6.0，但已拒绝。",
_batch("依赖镜像","other products and rejected proposals")+"\n不要把其他产品版本或拒绝提案带入 Atlas。","输出最终构建清单，严格 JSON，键为 target、packages、hash、rejected_proposals；packages每项只有name、version，依赖在前；rejected_proposals专指未批准或被拒绝的提案，按提出先后列其ID，无ID则用版本表达式。已发布又撤回的正式版本不归入提案数组。不要解释。"],
{"target":"linux-x64","packages":[{"name":"core","version":"2.4.0"},{"name":"crypto","version":"1.9.1"},{"name":"adapter","version":"5.1.0"}],"hash":"h3","rejected_proposals":["V1","core=2.6.0"]},"Tests proposal versus formal lock semantics, rollback, dependency compatibility, hash versioning, and ordered array output.",
[{"target":"linux-x64","packages":[{"name":"core","version":"2.6.0"},{"name":"crypto","version":"1.9.1"},{"name":"adapter","version":"5.2.0"}],"hash":"h2","rejected_proposals":[]},{"target":"linux-x64","packages":[{"name":"adapter","version":"5.1.0"},{"name":"core","version":"2.4.0"},{"name":"crypto","version":"1.8.2"}],"hash":"h3","rejected_proposals":["V1","core=2.6.0"]}]),
]

assert len(SPECS)==6
assert [x["split"] for x in SPECS]==["development","retained","test","development","retained","test"]
assert all(len(x["negative_expected"])>=2 and "expected_derivation" in x for x in SPECS)
