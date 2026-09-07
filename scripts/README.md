# MiniClaw 辅助脚本

以下命令从项目根目录执行。Python 脚本运行前先执行 `python -m pip install -e .`；Benchmark 按需安装 `.[benchmark]`。运行真实模型需要本地配置，ContextWeave 执行还依赖 Docker 与对应数据。

| 分类 | 脚本 | 用途 |
|---|---|---|
| 启动 | [launch/start-miniclaw-feishu.ps1](launch/start-miniclaw-feishu.ps1) | 启动飞书入口 |
| 开发检查 | [development/test-architecture-frontend.ps1](development/test-architecture-frontend.ps1) | 启动本地架构页面服务并检查静态资源 |
| Benchmark | [benchmarks/download_benchmarks.ps1](benchmarks/download_benchmarks.ps1) | 下载第三方资源到 `external/benchmarks/` |
| Benchmark | [benchmarks/run_contextweave_miniclaw.py](benchmarks/run_contextweave_miniclaw.py) | 运行 ContextWeave 对照实验 |
| Benchmark | [benchmarks/score_contextweave_workspace.py](benchmarks/score_contextweave_workspace.py) | 对实验工作区评分 |
| Benchmark | [benchmarks/summarize_contextweave_miniclaw.py](benchmarks/summarize_contextweave_miniclaw.py) | 汇总实验结果 |
| Benchmark | [benchmarks/backfill_ai_efficiency_trace_results.py](benchmarks/backfill_ai_efficiency_trace_results.py) | 向已有 Trace 回填评测结果，执行前确认目标结果目录 |

```powershell
pwsh -File scripts/launch/start-miniclaw-feishu.ps1
pwsh -File scripts/development/test-architecture-frontend.ps1
pwsh -File scripts/benchmarks/download_benchmarks.ps1
python scripts/benchmarks/run_contextweave_miniclaw.py --help
python scripts/benchmarks/score_contextweave_workspace.py --help
python scripts/benchmarks/summarize_contextweave_miniclaw.py --help
python scripts/benchmarks/backfill_ai_efficiency_trace_results.py --help
```

脚本依据自身位置定位项目根目录。分类后原来的 `scripts/<文件名>` 路径已迁移到上表位置；输出目录约定保持不变。
