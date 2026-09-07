# MiniClaw 项目目录

更新日期：2026-09-05。以下为当前实际目录；省略缓存和每个包内的细节文件。

```text
MiniClaw/
├─ README.md                  项目介绍、安装和运行入口
├─ ARCHITECTURE.md            架构边界与设计说明
├─ pyproject.toml             包配置、依赖、命令入口与测试配置
├─ .env.example               环境变量示例（本地密钥放 .env）
├─ .github/workflows/         持续集成与 Eval 工作流
├─ docs/
│  ├─ README.md               文档总导航
│  ├─ PROJECT_TREE.md         完整目录说明与归档约定
│  ├─ notes/                 01—31 技术笔记，保留原编号顺序
│  ├─ interview/             项目全构造详解与面试重点
│  └─ plans/                 实习简历项目改造方案
├─ src/MiniClaw/
│  ├─ cli.py                 命令行入口
│  ├─ cancellation.py        跨层取消信号
│  ├─ frontend.py            架构页面静态服务
│  ├─ llm/                   模型契约、配置、SSE、重试与回退
│  ├─ agent/                 通用 AgentLoop、事件与抽象工具协议
│  ├─ coding_agent/
│  │  ├─ assistant/          产品装配与会话存储
│  │  ├─ tools/              编码工具、注册、校验与执行管理
│  │  ├─ memory/             四层记忆、压缩、检索与蒸馏
│  │  ├─ goal/               持久目标、验收、Judge 与监督
│  │  ├─ runtime/            工作区、命令执行、快照与运行状态
│  │  ├─ approval/           风险识别、审批策略与交互
│  │  └─ instructions/       分层项目指令发现与注入
│  ├─ platforms/feishu/      飞书接入；platforms/delivery.py 负责投递
│  ├─ trace/                 事件存储、模型包装、回放与分析
│  ├─ evaluation/            端到端 Eval 与数据划分
│  └─ benchmark/             外部 Benchmark 适配、执行与评分
├─ tests/                    各模块回归测试
├─ evals/
│  ├─ *.json                 smoke、full、regression、resilience 套件
│  ├─ fixtures/              评测任务工作区与输入样例
│  ├─ baselines/             已保存的评测基线
│  └─ splits/v1/             固定数据划分、清单与暴露记录
├─ scripts/
│  ├─ README.md              辅助脚本入口与用法
│  ├─ launch/                飞书启动脚本
│  ├─ development/           前端检查脚本
│  └─ benchmarks/            下载、执行、评分、汇总与 Trace 回填
├─ frontend/architecture/    架构 SVG、预览图片与交互页面
├─ docker/runtime/           编码命令运行镜像
├─ external/benchmarks/      第三方 Benchmark 源码与数据
├─ benchmark-results/        本地历史实验输出
├─ .aster/                   会话、记忆、Trace、沙箱与内部实验数据
└─ .codex-research/           本地调研资料与整理记录
```

## 文件归档约定

| 内容 | 放置位置 | 说明 |
|---|---|---|
| 入口文档 | 根目录 `README.md`、`ARCHITECTURE.md` | 便于初次打开项目和安装包读取 |
| 技术讲解与演进记录 | `docs/notes/` | 原编号保留；历史文章的能力描述以写作时为准 |
| 面试复习 | `docs/interview/` | 原根目录“重点”中的两篇资料 |
| 改造规划 | `docs/plans/` | 区分计划与已实现能力 |
| 核心源码 | `src/MiniClaw/` | 按现有架构边界组织；Python 导入和命令入口不变 |
| 开发辅助脚本 | `scripts/` 对应分类 | 文档中的运行命令均从项目根目录执行 |
| 评测定义与基线 | `evals/` | 与下载的数据、运行结果分开维护 |
| 外部源码与数据 | `external/benchmarks/` | 包含第三方实现及本地适配，不属于核心产品源码 |
| 历史运行结果 | `benchmark-results/`、`.aster/` | 保留原位置和内容，避免历史证据及已配置路径失效 |
| 本地调研 | `.codex-research/` | 保存来源、下载状态和中间记录，不作为产品源码 |

`__pycache__/`、`.pytest_cache/`、`src/miniclaw.egg-info/` 是可重新生成的缓存或安装元数据，由忽略规则管理。`.env` 含本地配置和密钥；`.aster` 名称是运行时既有约定。

## 本次迁移

- 根目录 31 篇编号文章 → `docs/notes/`。
- `重点/` 两篇资料 → `docs/interview/`。
- `实习简历项目改造方案.md` → `docs/plans/`。
- `18.md` → `docs/notes/18.Tool-Runtime与Docker完整讲解.md`。
- `19.md` → `docs/notes/19.工具审批与Docker完整执行流程.md`。
- `22.md` → `docs/notes/22.Goal监督与验收状态机.md`。
- 7 个辅助脚本 → `scripts/launch/`、`scripts/development/`、`scripts/benchmarks/`，同步调整项目根目录定位。

文档里的 Markdown 链接按文件所在位置解析；代码块中的 `src/`、`evals/`、`scripts/` 等路径仍相对于项目根目录。历史 Trace 和实验结果中的路径保留为原始记录。

入口：[文档导航](README.md) · [项目说明](../README.md) · [架构说明](../ARCHITECTURE.md) · [脚本说明](../scripts/README.md)。
