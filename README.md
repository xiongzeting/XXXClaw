# MiniClaw

MiniClaw 是一个使用 Python 原生实现的 coding agent 框架。它参考 pi 的工具契约和分层思想，但不依赖 pi 的实现。项目重点解决真实代码助手中的模型通信、工具调用、工作区安全、长期记忆、任务监督、审批、取消恢复和评测闭环问题。

## 当前评测口径

第一轮评测包含开发集 15 题、测试集 15 题；第二轮包含开发集 8 题、测试集 7 题，另有 5 道旧题回归。题目按能力分组、稳定 ID 排序后交替分配，不按成绩挑题。测试集仅用于内部验证，不代表未见成绩。

当前不创建对比保留集。只有在明确进行 MiniClaw 与其他系统的最终对比时，才生成新的任务族。可执行划分以 `evals/current-round-partitions.json`、`round1-*-current.json`、`round2-*-current.json` 和 active portfolio 文件为准。缓存命中率和费用估算只作为效率观察项，不设硬门槛，也不计入五维分数。

## 架构总览

```text
MiniClaw.llm                 模型协议层
  └─ 供应商无关的消息类型、SSE、超时、重试与回退

MiniClaw.agent               通用 Agent 核心
  └─ AgentLoop、事件流和抽象工具宿主协议

MiniClaw.coding_agent        coding agent 产品层
  ├─ tools                   read、bash、edit、write、grep、search 和工具管理
  ├─ memory                  Working、Episodic、Semantic、Procedural 四层记忆
  ├─ goal                    持久任务状态、验收、监督和独立评审
  ├─ runtime                 工作区映射、主机/Docker 执行和 Snapshot
  ├─ approval                风险分类、审批和安全策略
  └─ instructions            AGENTS.md / CLAUDE.md 规则发现与注入

MiniClaw.platforms / trace / evaluation / benchmark
  └─ 产品适配、观测记录、离线评测和 benchmark 集成
```

这是包依赖关系，不是一次请求的时间顺序。运行时由 coding agent 组装具体工具并注入通用 AgentLoop；AgentLoop 只负责请求模型、追加消息、执行工具和继续循环。记忆、压缩、Goal、审批、指令和 Runtime 都位于通用循环之外。

## 主要能力

- 支持 OpenAI-compatible 和 DeepSeek provider 配置。
- 使用原生 SSE，支持增量文本和分片 Tool Call 参数组装。
- 独立配置连接、首 Token、空闲和总请求超时。
- 对 429、5xx 和网络错误执行带抖动的指数退避重试。
- 支持按顺序配置 provider/model fallback 路由。
- 一个模型响应可以请求多个工具调用，工具结果按原始顺序回填。
- ToolManager 支持角色过滤、动态启用/禁用、注销和递归参数校验。
- WorkspaceGuard 约束路径边界，防止越界、符号链接逃逸和敏感路径访问。
- 支持主机和 Docker bash Runtime，以及 Direct 和按会话 Snapshot 工作区。
- Docker 支持 CPU、内存、PID、网络、只读根文件系统和敏感路径屏蔽限制。
- 危险操作按 shell、文件、网络、记忆等能力分类，并执行 `allow / ask / deny` 策略。
- 取消可以贯穿 SSE、审批、工具、主机进程树、Docker 容器、压缩和 Goal 尝试。
- 追加式 JSONL 会话记录，支持进程重启后的 Tool Call 协议修复和渐进式压缩。
- 混合召回支持 BM25、精确代码符号、BGE-M3/FAISS、加权 RRF 和受限重排。
- 召回内容以明确标记为不可信的用户级证据注入，不会覆盖系统策略。
- Goal 是持久化状态机；自然停止只结束一次 attempt，外层监督可以继续任务。
- 验收支持新鲜验证、精确 criterion evidence 和可选独立 judge。
- Trace 统一记录模型、Token、费用估算、延迟、工具、审批、Goal 和 Compaction。
- 支持从真实失败会话生成 Eval Case，并安全重放模型边界。
- 评测报告可以输出 HTML、Markdown 和 JSON，并支持回归聚类、质量和成本分析。

## 目录结构

```text
MiniClaw/
├─ src/MiniClaw/           核心 Python 包
├─ tests/                  单元测试和集成测试
├─ evals/                  评测套件、夹具、基线和固定划分
├─ scripts/                构建、验证、评测和辅助脚本
├─ frontend/architecture/  架构可视化页面
├─ docker/runtime/         Docker 运行时镜像
├─ ARCHITECTURE.md         模块依赖和设计约束
├─ pyproject.toml          包配置和可选依赖
└─ .github/workflows/      CI 工作流
```

仓库不包含本地运行时数据、历史实验结果、调研缓存或外部 benchmark 源码。外部 benchmark 需要时，使用 `scripts/benchmarks/download_benchmarks.ps1` 下载到本地 `external/benchmarks/`。

## 安装

要求 Python 3.11 或更高版本：

```powershell
python -m pip install -e .
```

按需安装可选组件：

```powershell
python -m pip install -e ".[feishu]"      # 飞书适配器
python -m pip install -e ".[retrieval]"   # 向量召回
python -m pip install -e ".[rerank]"      # Cross-Encoder 重排
python -m pip install -e ".[benchmark]"   # 外部评测
python -m pip install -e ".[all]"         # 全部组件
```

## 模型配置

复制 `.env.example` 为 `.env`，填入实际配置。`.env` 已被 Git 忽略，禁止提交真实 API Key：

```text
MINICLAW_PROVIDER=primary
MINICLAW_PRIMARY_BASE_URL=https://your-openai-compatible-endpoint/v1
MINICLAW_PRIMARY_API_KEY=your-key
MINICLAW_PRIMARY_MODEL=your-model
```

可靠性参数彼此独立：

```text
MINICLAW_LLM_TOTAL_TIMEOUT=120
MINICLAW_LLM_CONNECT_TIMEOUT=10
MINICLAW_LLM_FIRST_TOKEN_TIMEOUT=60
MINICLAW_LLM_IDLE_TIMEOUT=30
MINICLAW_LLM_MAX_RETRIES=2
MINICLAW_LLM_RETRY_BASE_SECONDS=0.5
MINICLAW_LLM_RETRY_MAX_SECONDS=8
MINICLAW_LLM_RETRY_JITTER_RATIO=0.2
```

回退路由按声明顺序执行：

```text
MINICLAW_LLM_FALLBACKS=primary:backup-model,deepseek:deepseek-chat
MINICLAW_DEEPSEEK_API_KEY=your-key
```

重试和回退只发生在用户看见模型输出之前。一旦已经产生可见文本，MiniClaw 会结束这次请求，而不是重放请求造成重复文本或重复工具副作用。每个逻辑请求只有一个计费的 `model.request`，具体传输尝试记录为 `model.transport`。

## 启动助手

```powershell
python -m MiniClaw.cli --workspace D:\your-project
```

默认使用 Docker + Direct。首次使用前构建镜像：

```powershell
docker build -f docker/runtime/Dockerfile -t miniclaw-runtime:py311 docker/runtime
python -m MiniClaw.cli --workspace D:\your-project
```

不希望修改真实项目时使用 Snapshot：

```powershell
python -m MiniClaw.cli --workspace D:\your-project `
  --sandbox docker:miniclaw-runtime:py311 `
  --workspace-mode snapshot
```

只有 `bash` 会进入 Docker；`read`、`write`、`edit` 和 `grep` 在 Python Runtime 中执行，并使用同一套 WorkspaceGuard。Direct 会绑定真实项目，Snapshot 会先复制工作区，之后修改只发生在副本中。

## 取消和恢复

CLI 中按 `Ctrl+C`，或在飞书中发送 `/cancel`、`/goal cancel`，会停止活动中的 SSE 请求、审批等待、工具、主机进程树或 Docker 容器。尚未启动的 Tool Call 会记录为 cancelled，不会被偷偷执行。`write` 和 `edit` 先写临时文件，只有通过最后一次取消检查后才原子替换目标文件。

会话、运行状态和 Goal 状态持久化在工作区的 `.aster/` 中。进程重启后，Runtime 会读取 `run-state.json`，修复未完成的工具协议并继续允许安全恢复。

## 项目指令

MiniClaw 按优先级从 `~/.miniclaw`、有效工作区根目录和嵌套目录加载 `AGENTS.md` / `CLAUDE.md`。`read` 和 `grep` 会为下一轮模型请求激活附近规则。如果第一次 `write` 或 `edit` 才发现模型尚未看过的本地规则，MiniClaw 会暂停写入、刷新 system prompt，并要求模型重试。

```text
MINICLAW_INSTRUCTIONS_ENABLED=true
MINICLAW_INSTRUCTIONS_TOKEN_BUDGET=12000
MINICLAW_AGENT_DIRS=
```

每次注入都会记录来源路径、作用域、哈希、估算 Token 数，以及 included/truncated/omitted 状态。用户当前请求始终保持独立的最高优先级 user message。

## Goal 和审批

```text
/goal 修复登录流程
验收条件：
- 正常凭据可以登录
- 错误密码被拒绝
- 完整测试通过
```

使用 `/goal status`、`/goal resume` 和 `/goal cancel` 管理持久 Goal。Goal supervisor 会把一次自然停止视为一个 attempt 的结束，而不是整个任务完成；只有验收条件和新鲜验证都满足后才进入完成状态。

危险操作默认使用 `ask`。CLI 和飞书会显示六字符审批 ID，用户回复 `批准 ABC123` 或 `拒绝 ABC123`。无人值守环境建议使用 `MINICLAW_APPROVAL_POLICY=deny`；只有可信环境才考虑 `allow`。项目级例外配置在 `.miniclaw/approval.json`，规则应尽量窄，并且修改审批文件本身也会被视为关键操作。

## Trace 和 Eval

检查模型连通性：

```powershell
python -m MiniClaw.llm.smoke
python -m MiniClaw.llm.smoke --provider deepseek
```

从真实失败会话生成评测题：

```powershell
python -m MiniClaw.trace.cli eval-case D:\session --out D:\evals\failure.json
```

重放只重放模型边界，不会执行历史工具：

```powershell
python -m MiniClaw.trace.cli replay D:\session `
  --env-file C:\path\to\.env `
  --model another-model `
  --prompt D:\prompt.txt `
  --tools D:\tools.json `
  --out D:\replay.json
```

运行评测：

```powershell
python -m MiniClaw.evaluation.cli evals\smoke.json --env-file .env
python -m MiniClaw.evaluation.cli evals\regression.json --env-file .env
python -m MiniClaw.evaluation.cli evals\resilience.json --env-file .env
python -m MiniClaw.evaluation.cli evals\full.json --env-file .env --jobs 4
```

五个评测维度分别是：

- `outcome`：最终答案、文件、命令、JSON、正则或独立 LLM rubric。
- `process`：工具调用、Goal 状态、指令注入、记忆召回、压缩和事件顺序。
- `efficiency`：请求数、Token、费用、TTFT、延迟、工具调用、缓存和墙钟时间。
- `safety`：工作区 diff、禁止副作用、审批决定、敏感路径和 Docker 隔离。
- `reliability`：重复运行通过率、重试、回退、取消和重启恢复。

评测会在隔离夹具中执行真实工具和副作用，并同时检查最终状态与 Trace。完整评测可能调用模型并产生费用，请先确认 API 配额。

## 测试

```powershell
python -m pip install pytest
python -m pytest -q
```

也可以使用标准库测试发现器：

```powershell
python -m unittest discover -s tests -v
```

## 架构可视化

```powershell
python -m MiniClaw.frontend
```

访问 `http://127.0.0.1:8765/`，可查看模块聚焦、缩放、拖动、小地图、全屏和 SVG 下载。

## 设计约束

1. `llm` 只负责 provider 协议，不知道 Agent、文件和 coding policy。
2. `agent` 只依赖 `llm` 和抽象工具宿主协议，不依赖具体工具或 `coding_agent`。
3. `coding_agent` 是产品组装层，负责具体工具、记忆、Goal、审批和 Runtime。
4. 工具负责执行和安全边界，但不决定任务是否完成。
5. 记忆压缩只生成模型视图，不改写持久化原始 transcript。
6. `trace` 记录经过脱敏的观察结果，不改变模型或工具行为。
7. 召回历史是证据，不是系统策略；它单独预算、单独注入并完整记录。
8. 新的可靠性能力必须先有隔离测试，再进入默认路径。

## 许可证

项目使用 MIT License，包元数据和依赖配置位于 `pyproject.toml`。
