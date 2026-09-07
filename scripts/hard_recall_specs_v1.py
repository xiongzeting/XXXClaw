"""Hand-authored hard recall specifications for hard-memory-v1.

This module is data only.  It has no filesystem, provider, or model side effects.
Each tuple is ``(key, turns, expected)`` and is consumed by the suite builder.
"""
from __future__ import annotations

RECALLS = (
    (
        "ledger_twins",
        (
            "会话1（请长期记住并按租户隔离）：North Ledger 租户 NL-1 的生产区是 eu-1，审计主库 ledger-eu，负责人 Mina，截止日为 2026-10-15；另一个同名 North Ledger 租户 NL-2 的生产区是 us-2，主库 ledger-us，负责人 Miro，截止日为 2026-11-15。",
            "会话2：仅 NL-1 的审计主库迁移到 ledger-eu2，NL-1 生产区、负责人、截止日不变；NL-2 的 ledger-us 也不变。",
            "会话3：仅 NL-1 的负责人 Mina 已正式交接给 Sora。NL-2 资料中出现的同名 Mina 是历史联系人，不能当作 NL-2 或 NL-1 当前负责人。",
            "会话4：仅 NL-1 的截止日从 2026-10-15 延期到 2026-10-22；NL-2 的 2026-11-15 不变。",
            "会话5：NL-1 新增灾备库 ledger-dr-eu2，主库仍是 ledger-eu2；NL-2 没有已确认的灾备库，未知必须保留为 null。",
            "会话6：NL-1 的 eu-1 才是生产区，eu-2 只是演练区；任何演练区记录不能覆盖生产区。",
            "会话7：查询 NL-1，回答必须是一个 JSON 对象，且只能有这些 key：primary（字符串）、backup（字符串）、owner（字符串）、region（字符串）、deadline（YYYY-MM-DD 字符串）、same_name_current（布尔值）。不要回答 NL-2，也不要添加解释。",
        ),
        {"primary": "ledger-eu2", "backup": "ledger-dr-eu2", "owner": "Sora", "region": "eu-1", "deadline": "2026-10-22", "same_name_current": False},
    ),
    (
        "temporal_revoke",
        (
            "会话1：记录项目 Aster 的时间线：2026-08-01 起 API v3 生效，重试 5 次，发布窗口 01:00Z，区域 eu-west；v4 只是未执行提案。",
            "会话2：2026-08-20，v4 提案被正式撤销，不能把提案当完成；v3 的当时生效状态不变。",
            "会话3：2026-09-01 曾实际升级到 v5，重试改成 3 次，窗口仍为 01:00Z，区域仍 eu-west。",
            "会话4：2026-09-05 v5 健康检查失败并回滚到 v3，重试恢复 5 次；v4 仍是已撤销。",
            "会话5：2026-09-10 发布窗口改为 02:30Z，版本、重试和区域不变。",
            "会话6：2026-09-12 通知声称 v6 已完成，但随后被正式否定；截至该日 v3 仍运行，v6 不能视为完成。",
            "会话7：2026-09-20 区域从 eu-west 改为 ap-northeast；旧区域仅为历史值。查询明确日期 2026-09-21 的实际状态。",
            "会话8：只输出一个 JSON 对象，且只能包含 version（字符串）、retries（整数）、window（字符串）、region（字符串）、v4_cancelled（布尔值）、v6_completed（布尔值）；不存在或未确认的值必须为 null，不能用今天或最新通知自行推断。",
        ),
        {"version": "v3", "retries": 5, "window": "02:30Z", "region": "ap-northeast", "v4_cancelled": True, "v6_completed": False},
    ),
    (
        "policy_scopes",
        (
            "会话1：记住 2026-09-01 生效的全局规则：production 的 normal 发布需要 two_person，staging 的 normal 发布需要 single_person；任何 emergency 发布默认仍需要 two_person。",
            "会话2：payment-prod 曾提出 emergency 可跳过审批，但提案未获批准；提案内容没有改变规则，也不能推断为 single_person。",
            "会话3：payment-prod 的 emergency 例外于 2026-09-03 获批，只在 UTC 2026-09-10 00:00 至 23:59 有效，期间要求 single_person。",
            "会话4：payment-prod 的该例外在 2026-09-10 之后自动失效；payment-prod 的 normal 始终沿用 production 的 two_person。",
            "会话5：search-staging 的 normal 被单独改为 two_person；search-staging 的 emergency 没有单独授权，必须回退全局 emergency 规则。",
            "会话6：同名 payment-sandbox 是独立环境，没有继承 payment-prod 的限时例外；它的 normal 和 emergency 都只适用全局对应规则。",
            "会话7：2026-09-11 12:00 UTC 有人声称 payment-prod 例外延长，但该声称已被正式撤销；不能用它覆盖失效时间。",
            "会话8：只输出一个 JSON 对象，且只能有 payment_prod_emergency、payment_prod_normal、search_staging_normal、search_staging_emergency、payment_sandbox_emergency、global_prod_emergency 六个 key；每个值必须是精确字符串 two_person 或 single_person。查询时刻固定为 2026-09-11T12:00:00Z；没有授权例外时不得推断 single_person。",
        ),
        {"payment_prod_emergency": "two_person", "payment_prod_normal": "two_person", "search_staging_normal": "two_person", "search_staging_emergency": "two_person", "payment_sandbox_emergency": "two_person", "global_prod_emergency": "two_person"},
    ),
    (
        "multi_hop_assets",
        (
            "会话1：记录请求 req-881 属于作业 job-blue，req-882 属于 job-green；两者都曾被简称为 export，简称不能作为关联依据。",
            "会话2：job-blue 运行在 pod-iris，job-green 运行在 pod-jade；pod 关系按作业分别保存。",
            "会话3：pod-iris 位于 node-a7，pod-jade 位于 node-b4；不能由相似名称推断节点。",
            "会话4：node-a7 的 zone 是 eu-1，node-b4 的 zone 是 us-1。",
            "会话5：req-881 输出到 bucket-ledger，req-882 输出到 bucket-report；桶不是按作业名称猜测。",
            "会话6：bucket-ledger 初始保留 14 天，bucket-report 初始保留 30 天；这是输出桶级约束。",
            "会话7：2026-09-06 req-881 改名为 req-ledger-881，旧 ID 仅是 alias；2026-09-07 job-blue 从 pod-iris 迁到 pod-iris-2，pod-iris-2 位于 node-a8，node-a8 的 zone 仍 eu-1；旧 pod 只作历史，不能返回。",
            "会话8：2026-09-08 bucket-ledger 保留期改为 21 天；2026-09-09 一条‘req-ledger-881 改到 bucket-report’的草稿被撤销，仍使用 bucket-ledger。查询 2026-09-10 的 req-ledger-881，只输出 JSON，且只能有 job（字符串）、pod（字符串）、node（字符串）、zone（字符串）、bucket（字符串）、retention_days（整数）六个 key；必须沿请求→作业→pod→节点→zone→桶→保留期链推导，不能返回历史值或被撤销草稿。",
        ),
        {"job": "job-blue", "pod": "pod-iris-2", "node": "node-a8", "zone": "eu-1", "bucket": "bucket-ledger", "retention_days": 21},
    ),
    (
        "negative_identity",
        (
            "会话1：记住两个独立实体：个人 Hana 的默认代码示例语言 Python、时区 Asia/Tokyo；组织 Hana Labs 的默认语言 Go、时区 UTC。",
            "会话2：个人偏好只适用于 Hana 本人代码示例，不能迁移给组织；组织语言不能反推个人语言。",
            "会话3：Hana 个人时区在 2026-09-01 改为 Asia/Shanghai，个人语言仍 Python。",
            "会话4：Hana Labs 联系人改为 Hana Chen；同名个人 Hana 不是组织联系人。",
            "会话5：2026-09-02 讨论 Rust 只是一次临时示例，明确不改变任何默认语言。",
            "会话6：Hana Labs 的默认语言在 2026-09-04 被撤销，现为未设置；没有任何记录授权用 Python、Go 或 Rust 填回。",
            "会话7：组织时区 UTC、个人时区 Asia/Shanghai 均继续有效；只凭‘常用语言’或姓名相同不能推断其他字段。",
            "会话8：只输出一个 JSON 对象，且只能有 hana_language（字符串）、hana_timezone（字符串）、labs_language（字符串或 null）、labs_contact（字符串）、labs_timezone（字符串）五个 key；查询日期为 2026-09-05，未设置必须是 JSON null，不得补猜。",
        ),
        {"hana_language": "Python", "hana_timezone": "Asia/Shanghai", "labs_language": None, "labs_contact": "Hana Chen", "labs_timezone": "UTC"},
    ),
    (
        "scoped_routes",
        (
            "会话1：记录环境隔离：prod 的支付路由 primary pay-v2、fallback pay-v1、owner Lee、region eu-1；staging 的 primary pay-v1、fallback pay-mock、owner Lei、region test-1。",
            "会话2：prod 的 pay-v2 与 staging 的 pay-v1 名称相似但负责人记录独立，不能按服务名合并。",
            "会话3：2026-09-06 仅 prod 的 fallback pay-v1 被撤销，prod 暂无 fallback；staging fallback 仍为 pay-mock。",
            "会话4：2026-09-07 prod primary 改为 pay-v3，负责人改为 Lin；pay-v2 只是历史，不是当前路由。",
            "会话5：2026-09-08 staging fallback 改为 pay-sim，staging primary 和 owner 不变。",
            "会话6：2026-09-08 prod region 改为 eu-2，staging region 仍 test-1。",
            "会话7：‘所有环境都改为 pay-v3’只是建议，2026-09-09 被否定；不能覆盖 staging 的 pay-v1。prod 被撤销的 fallback 没有替代值。",
            "会话8：只输出 JSON，且只能有 prod_primary（字符串）、prod_fallback（字符串或 null）、prod_owner（字符串）、prod_region（字符串）、staging_primary（字符串）、staging_fallback（字符串）、staging_owner（字符串）、staging_region（字符串）八个 key；查询时刻为 2026-09-09T23:00:00+08:00，撤销项必须是 null，禁止用建议或历史值。",
        ),
        {"prod_primary": "pay-v3", "prod_fallback": None, "prod_owner": "Lin", "prod_region": "eu-2", "staging_primary": "pay-v1", "staging_fallback": "pay-sim", "staging_owner": "Lei", "staging_region": "test-1"},
    ),
)

assert len(RECALLS) == 6
