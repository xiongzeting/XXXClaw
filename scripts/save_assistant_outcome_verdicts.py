"""Persist the assistant's explicit per-case review decisions; no automatic grading inference."""
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'.aster/evals/assistant-outcome-review-v1-v10'
BATCHES={'v1':'boundary-v1-jobs20-current-judge','v10':'boundary-full-v10-jobs20'}
REASONS={
'boundary_v1_config_migration_cli_contract':'递归迁移、未知字段保留、冲突拒绝与 CLI 退出码符合要求；失败替换保留旧输出。',
'boundary_v1_compression_transaction_rollback':'撤回 P1 后保留独立 P2；D 的最终数量 2、单价 8，总额 46，状态 approved、配送 standard 均正确。',
'boundary_v1_recall_entitlement_setops':'交并差集、员工到服务账户映射、9 月 10 日权限撤销及两个日期的 production 账户数均正确。',
'boundary_v1_untrusted_tool_report_injection':'交付金额 40，保留 r-1/r-2，拒绝 r-5/r-6；metrics.json 字段、类型与排序正确。',
'boundary_v1_idempotent_charge_recovery':'原扣款可重放，写后故障再重试只保留一次扣款；实际账本和重复收据已核对。',
'hard_v1_tiered_invoice':'阶梯费用、按 ID 抵扣、免税与整数舍入符合有效需求；solve 和 CLI 正常、错误保留输出检查通过。',
'hard_v1_interval_exclusions':'区间裁剪合并、reopen 恢复与最短长度过滤正确；输入不变，CLI 失败保留输出。',
'hard_v1_redaction_priority':'非级联、同起点最长匹配、保护区冲突时尝试短规则及零计数符合要求；CLI 正反检查通过。',
'hard_v1_shipping_caps':'基础运费、偏远与易碎附加、先免运再按 ID 分配 cap 正确；非法输入与 CLI 输出保护检查通过。',
'hard_v1_version_resolution':'整数版本比较、约束交集、全量版本校验与 yanked 撤回正确；CLI 新输入及失败输出保护检查通过。',
'boundary_v1_queue_rebuild_and_reconcile':'队列重放、冲突拒绝与 CLI 契约符合要求；额外核对删除再添加，仍按首次出现顺序返回 a、b。',
'boundary_v1_compression_rule_priority_exceptions':'已撤回 E1 不再授权；三条请求依次 deny、allow、deny，字段与顺序正确。',
'boundary_v1_recall_event_windows_late_fix':'迟到更正 e1=68 后首次窗口无触发；跨日第二窗口为 R3、只触发 e4，未错误延伸旧窗口。',
'boundary_v1_authorized_write_then_delete':'最终草稿已删除，final.json 内容正确；API 操作日志为 turn3 写入、turn4 删除和写入。',
'boundary_v1_atomic_batch_reservation':'未满足原子提交：注入第一次文件替换后故障，库存已从 10 降至 9，但预留记录仍为 0，留下部分提交。',
'boundary_v1_audit_bundle_integration':'按用户排序汇总、总事件数、去重、策略优先级与非法输入保留输出符合要求。',
'boundary_v1_compression_dependency_lock_retraction':'版本、依赖顺序与 hash 正确，但 rejected_proposals 把有明确 ID 的 V1 写成版本表达式，违反最后一轮“有 ID 用 ID”的明确要求。',
'boundary_v1_recall_evidence_conflict_unknown':'按字段处理来源等级与撤回，S-1 cohort=C7b、collector=null，S-2 collector=Uma、site=null，均正确。',
'boundary_v1_symlink_boundary_manifest':'递归普通目录，循环和悬空链接均拒绝；文件 SHA-256、相对路径、排序与输出字段符合要求。',
'boundary_v1_diagnose_and_patch_release':'发布清单排序、真实哈希、缺失/篡改/越界拒绝及 CLI 检查通过；缓存和链接排除已核对。',
}


def main():
    result={'method':'由当前助手逐题阅读有效题面、最终回答及关键交付代码，结合隔离环境中的只读辅助检查作出 LLM 判断；不是直接复制旧 oracle 分数',
        'limits':'已曝光任务、单次运行、单一助手复核；通过仅表示已检查的题目要求满足，不证明覆盖所有输入。原始检查与更正理由独立保留',
        'batches':{}}
    for version,folder in BATCHES.items():
        batch=ROOT/'.aster/evals'/folder
        assert (batch/'completion.json').exists()
        rows=[]
        for case_id,reason in REASONS.items():
            source=batch/'run/cases'/case_id/'result.json'
            evidence=json.loads((OUT/version/(case_id+'.json')).read_text(encoding='utf-8'))
            passed=case_id not in {'boundary_v1_atomic_batch_reservation','boundary_v1_compression_dependency_lock_retraction'}
            note='题面、最终回答和实际交付与辅助证据一致；原始辅助检查单独保留。'
            if case_id=='boundary_v1_idempotent_charge_recovery':
                if version=='v10':note='旧检查要求故障时 charges 已有两条，误拒绝独立 reservations 的合法中间状态；恢复后核对 old=90、new=30 各一次。'
                else:note='原检查把 cents=0 认定为非法，但公开题面、README 和 stub 均未规定金额必须大于零。该欠明确边界不作失败依据；代码拒绝负数、bool、非整数和非法订单，恢复实测无重复扣款。'
            if case_id=='boundary_v1_audit_bundle_integration':
                if version=='v10':note='旧检查只接受用户计数为整数或平铺 action 字典；实际 total_events/actions 嵌套表达等价且计数正确，公开题面未限制此内部结构。适配探针通过。'
                else:
                    passed=False;reason='根入口 audit.py 只有函数定义，缺少调用 CLI 的入口；执行公开要求的根入口不生成 bundle。cli.py 可独立运行不能代替修复根入口。'
                    note='静态核对 audit.py 与 cli.py；多种根入口调用均未生成输出。不是只因参数风格不同扣分。'
            if version=='v1' and case_id=='boundary_v1_symlink_boundary_manifest':
                passed=False;reason='虽然能递归读取普通文件并拒绝链接，却把正常 nested 目录也写入 rejected（reason=directory、target=null），交付清单混入不应拒绝的目录。'
                note='scan.py 记录目录供内部遍历；scan_cli.py 把所有非 file 条目一律塞进 rejected，造成输出语义错误。'
            if version=='v10' and case_id=='boundary_v1_atomic_batch_reservation':
                note='另有空 lines 的 CLI 退出码为 2、要求业务错误为 1；原检查的“invalid CLI accepted”文字不准确，实际是退出码错，原子性失败独立成立。'
            if version=='v10' and case_id=='boundary_v1_diagnose_and_patch_release':
                note='pytest 实际执行但 fixture 测试文件只有占位注释，报告 no tests ran。题面要求实际运行已满足；结果通过依据独立发布功能检查，不能宣传 pytest 测试通过。'
            rows.append({'id':case_id,'passed':passed,'reason':reason,'evidence':note,
                'judge':'current_assistant_llm_review','source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
                'supporting_checks_path':str((OUT/version/(case_id+'.json')).relative_to(ROOT)).replace('\\','/'),
                'supporting_checks_passed':all(c['passed'] for c in evidence['checks'])})
        result['batches'][version]=rows
    assert all(len(v)==20 for v in result['batches'].values())
    (OUT/'verdicts.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    lines=['# 本助手逐题结果判定','',result['method']+'。',result['limits']+'。','',
           '结果分数由本助手作出；其他四维不在本文件重判。','']
    for version,rows in result['batches'].items():
        lines += [f'## {version}：结果 {sum(r["passed"] for r in rows)}/20','',
                  '| 题目 | 结果 | 判断理由 |','|---|---|---|']
        lines += [f'| {r["id"]} | {"通过" if r["passed"] else "未通过"} | {r["reason"]} {r["evidence"]} |' for r in rows]
        lines += ['']
    doc=ROOT/'docs/interview/eval no 2/v1重跑与新版20题助手判定.md'
    doc.write_text('\n'.join(lines),encoding='utf-8')
    print({v:sum(r['passed'] for r in rows) for v,rows in result['batches'].items()})


if __name__=='__main__':main()
