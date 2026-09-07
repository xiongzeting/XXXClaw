# 下一轮质量限制配置

`quality-limits-policy-v1.json` 保存下一轮可调整的初始限制；`scripts/build_quality_limits_v1.py` 从当前 `boundary-campaign-v1.json` 生成 `next-quality-limits-v1.json`。

生成套件保留每个 case 的现有检查，并追加两项：`process` 维度的 `tool_errors <= 5`，以及 `efficiency` 维度的 `wall_duration_seconds <= 600`。`compression` 类的时间上限为 900 秒。

这些阈值只是待下一轮观察的初始值，尚未验证其合理性。网络中断、连接错误等可用性事件需单独复核，并保留原始错误、重试和 trace 数据；不得把生成但未执行的套件或限制宣称为已完成结果。

## 缓存与费用观察（下一轮启用）

新 suite 对每题追加 `cache_hit_percent`（%）和 `estimated_cost_usd`（USD），均设置 `report_only=true`、`required=false`，无 min/max。观察成功或数据缺失都不改变五维评分及通过率。`cache_hit_percent` 按所有主模型和辅助模型请求的缓存输入 / 输入加权计算，不平均各次百分比。价格按每次实际路由配置费率计算，缓存输入不重复计入未缓存输入。

原始 cost_usd 继续作为可追溯的已记录小计；新的完整费用观察在 usage、缓存统计或费率不足时为 null，不能将缺失显示为免费。断连但未返回 usage 的服务端费用未知；价格估算不是账单。LLM 裁判费用未取得同等完整计费证明时，也标为不完整。

生成：`python scripts/build_quality_limits_v1.py`。入口已在 active-eval-portfolio-v6.json 的 next_evaluation 中指定。当前仅配置与离线验证，没有启动下一轮模型。
