# MiniClaw

<!-- current-eval-partition:start -->
## 当前两轮题集口径（2026-09-06）

第一轮：开发集 **15**、测试集 **15**。第二轮：开发集 **8**、测试集 **7**，另有 **5** 道旧题回归。原保留题按能力分组、稳定 ID 排序后交替分配，不按成绩挑题。两轮均已曝光；测试集用于内部验证，不冒充未见成绩。

**当前不创建对比保留集。** 等用户明确要求 MiniClaw / Codex 最终对比时，再生成新的任务族。

可执行划分以 `evals/current-round-partitions.json`、`round1-*-current.json`、`round2-*-current.json` 及 active portfolio v5/v6 为准。下一轮使用 `evals/next-quality-limits-v1.json`，缓存命中率（%）与费用估算（USD）作为效率观察项进入逐题报告，**不设硬门槛，不计入五维分数**。费用按缓存输入、未缓存输入、输出各自的配置费率估算，不是服务商账单；缺失数据标为未采集。

历史冻结 snapshot、原始结果和 ZIP 保留原分层与原分数。报告正文涉及旧分层的执行记录属于历史口径，不是当前题集配置。当前分类只重组题目，未重跑模型或改变单题成绩。

归档中的资料清单和核对记录记录的是归档时的哈希；本次更新的可读说明与报告不再对应原哈希。原始清单不覆盖，冻结 JSON、ZIP 及原始评分仍按原清单追溯。
<!-- current-eval-partition:end -->

MiniClaw is a small Python coding-agent framework. Its tool contracts closely follow pi while using a Python-native implementation.

```text
MiniClaw.llm                 ≈ pi-ai
  provider-neutral model types, SSE transport, retry and fallback

MiniClaw.agent               ≈ pi-agent-core
  reusable model/tool loop, events, and the abstract tool-host protocol

MiniClaw.coding_agent        ≈ pi-coding-agent
  assistant/session composition
  ├─ tools                   role-aware ToolManager, read, bash, edit, write, grep, search
  ├─ memory                  working, episodic, semantic, and procedural memory
  ├─ goal                    persistent state, verification, judge, supervision
  ├─ runtime                 workspace mapping and host/Docker execution
  ├─ approval                dangerous-operation policy and user decisions
  └─ instructions            layered project-rule discovery and injection

MiniClaw.platforms / trace / benchmark
  product entry adapters, observability/Eval, and offline evaluation
```

This is a package dependency hierarchy, not the chronological request flow. At runtime the coding assistant creates concrete tools and injects their executor into the generic AgentLoop; AgentLoop sends normalized requests to the LLM layer and invokes tools only through the abstract protocol. Memory, context compaction, Goal supervision, approval, instructions, and Runtime remain outside the generic loop and are assembled by the coding assistant. Slack remains outside the current version.

## 项目目录与文档

[文档总导航](docs/README.md) · [完整项目树](docs/PROJECT_TREE.md) · [脚本用法](scripts/README.md)

```text
MiniClaw/
├─ README.md / ARCHITECTURE.md / pyproject.toml
├─ docs/                    notes/ 技术笔记、interview/ 面试资料、plans/ 改造规划
├─ src/MiniClaw/             核心 Python 包
├─ tests/                   回归测试
├─ evals/                   评测套件、样例、基线与固定数据划分
├─ scripts/                 launch/、development/、benchmarks/
├─ frontend/architecture/   架构页面、SVG 和预览图
├─ docker/runtime/          运行镜像
└─ .github/workflows/       CI 与 Eval 工作流
```

外部 benchmark 源码与数据不随仓库发布；如需运行对应评测，请按 `scripts/benchmarks/download_benchmarks.ps1` 下载到 `external/benchmarks/`。运行时数据、实验结果和本地调研记录均为可再生内容，不纳入版本控制。

编号文档已归档到 `docs/notes/`，原“重点”资料位于 `docs/interview/`。以下命令均从项目根目录运行。

## Included

- environment-selected OpenAI-compatible and DeepSeek provider profiles
- native OpenAI-compatible SSE with incremental text and fragmented Tool Call assembly
- connect, first-token, idle, and total model timeouts
- end-to-end cancellation across LLM streams, approvals, tools, host process trees, Docker containers, compaction, and Goal attempts
- retryable 429/5xx/network handling with exponential backoff and jitter
- explicit ordered provider/model fallback routes
- asynchronous agent event stream
- multiple tool calls per assistant turn
- session-scoped ToolManager with role-specific tool injection, allow/deny filters, dynamic enable/disable, and unregister support
- recursive tool argument validation and optional execution timeout
- workspace path boundary checks
- host and Docker bash runtimes with direct or per-session Snapshot workspaces
- execution-time `allow / ask / deny` approval policy with fail-closed timeout
- multi-capability shell/file/memory risk classification and normalized project-scoped allowlist
- CLI and Feishu approvals recorded in unified Trace
- Docker CPU/memory/PID/network/read-only-root limits and sensitive-path masking
- layered `AGENTS.md`/`CLAUDE.md` project instructions with target-scoped discovery
- target-scoped dynamic instruction injection and first-write refresh protection
- `read`, `bash`, `edit`, `write`, `grep`, and shell-free `search` tools, implemented as separate Python modules
- durable `run-state.json` transitions plus interrupted Tool Call protocol repair after process restart
- append-only JSONL transcript storage with recoverable progressive compaction
- four-layer memory: Working Context, Episodic, Semantic, and Procedural
- persistent BM25 + exact code-symbol recall + local BGE-M3 embeddings in FAISS HNSW, weighted RRF, status/time/confidence-aware reranking, and bounded BGE reranking
- retrieval evidence is injected as explicitly untrusted user-level context with diversity-aware 15k Token budgeting; system policy remains isolated
- persistent memory-conflict workflow plus cross-process write locking
- evaluation profile: 80k soft trigger, 100k hard trigger, 30k target, 20k recent context
- persistent pi-style Goal state machine with attempt/time/cost limits
- outer Goal supervisor: a natural model stop ends one attempt, not the task
- fresh-verification completion gate, exact criterion evidence, and optional independent judge
- per-conversation Goal commands in CLI and Feishu
- persistent Feishu delivery outbox with idempotency keys, bounded retry, and separate artifact notices
- unified per-session `trace.jsonl` for model, Token, estimated cost, latency, tools, approvals, Goal, and Compaction
- one-command Eval Case extraction from real failures
- safe model-boundary replay with model, system prompt, and tool-definition overrides
- deterministic failure clustering, regression alerts, and HTML/Markdown/JSON quality-cost dashboards
- interactive CLI
- offline unit tests with a scripted model

## Run

```powershell
cd D:\MIniClaw
python -m pip install -e .
python -m unittest discover -s tests -v
python -m MiniClaw.cli --workspace D:\your-project
```

The CLI automatically loads `./.env` when present. Process environment variables still take precedence. Install optional integrations only when needed:

```powershell
python -m pip install -e ".[feishu]"
python -m pip install -e ".[benchmark]"
python -m pip install -e ".[all]"
```

Docker + Direct is the default runtime combination. Build the configured image before starting MiniClaw for the first time:

```powershell
docker build -f docker/runtime/Dockerfile -t miniclaw-runtime:py311 docker/runtime
python -m MiniClaw.cli --workspace D:\your-project
```

Verify Docker mounts, Direct persistence, secret masking, timeout, and cleanup without calling an LLM:

```powershell
python -m MiniClaw.coding_agent.runtime.smoke --image miniclaw-runtime:py311
```

Use a conversation-local workspace copy when real project files must not be modified:

```powershell
python -m MiniClaw.cli --workspace D:\your-project `
  --sandbox docker:miniclaw-runtime:py311 `
  --workspace-mode snapshot
```

Only `bash` enters Docker. `read`, `write`, `edit`, and `grep` stay in the Python host process and use the same effective workspace through `WorkspaceGuard`. The default Direct mode bind-mounts the real project; Snapshot mode seeds `.aster/.../sandbox/workspace` once and keeps later task changes there. Use `--sandbox host` only when explicitly debugging without Docker.

The default provider is the OpenAI-compatible endpoint configured by:

```text
MINICLAW_PROVIDER=primary
MINICLAW_PRIMARY_BASE_URL=https://ai.zxcoding.top/v1
MINICLAW_PRIMARY_MODEL=gpt-5.6-luna
```

The model transport now uses real SSE. Configure reliability independently:

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

Optional fallbacks are explicit and ordered. This example retries the primary route, then tries another model on the same provider, then DeepSeek:

```text
MINICLAW_LLM_FALLBACKS=primary:backup-model,deepseek:deepseek-chat
MINICLAW_DEEPSEEK_API_KEY=...
```

Retries and fallbacks occur only before user-visible model output. Once a text delta has been emitted, MiniClaw fails the interrupted request instead of replaying it and duplicating text or tool decisions. Each logical request produces one cost-bearing `model.request`; individual attempts are `model.transport` Trace events.

Cancellation is end to end. Feishu `/cancel` and `/goal cancel`, or `Ctrl+C` during a CLI run, stop the active SSE request, approval wait, Tool Call, host process tree, or Docker container. Remaining Tool Calls are recorded as cancelled and are never started. Built-in `write` and `edit` stage data in a temporary file and commit with an atomic replace only after a final cancellation check.

Project instructions are loaded in increasing priority from `~/.miniclaw`, the effective workspace root, and activated nested directories. `read` and `grep` activate nearby rules for the next model turn. If a first `write` or `edit` discovers a local instruction file that the model has not seen, MiniClaw performs no mutation, refreshes the system prompt, and asks the model to retry. The current user request remains a separate highest-priority user message. Configure discovery with:

```text
MINICLAW_INSTRUCTIONS_ENABLED=true
MINICLAW_INSTRUCTIONS_TOKEN_BUDGET=12000
MINICLAW_AGENT_DIRS=
```

Within each directory, MiniClaw follows pi's first-match order: `AGENTS.md`, `AGENTS.MD`, `CLAUDE.md`, `CLAUDE.MD`. Every injected resolution is recorded as `instructions.injected` with source path, scope, hash, estimated tokens, and included/truncated/omitted status.

Switch to DeepSeek without changing code:

```powershell
$env:MINICLAW_PROVIDER = "deepseek"
$env:MINICLAW_DEEPSEEK_API_KEY = "your-key"
python -m MiniClaw.cli --workspace D:\your-project
```

See `.env.example` for every supported environment variable. MiniClaw uses only `MINICLAW_*` names and never prints API keys.

Run a minimal real-provider check without starting the interactive assistant:

```powershell
python -m MiniClaw.llm.smoke
python -m MiniClaw.llm.smoke --provider deepseek
```

Use the migrated MiniClaw dotenv file for model and Feishu credentials:

```powershell
$env:PYTHONPATH = "D:\MIniClaw\src"
python -m MiniClaw.llm.smoke --env-file D:\MIniClaw\.env
python -m MiniClaw.platforms.feishu.cli --workspace D:\MIniClaw --env-file D:\MIniClaw\.env
```

The Feishu adapter uses the official SDK long connection and `MINICLAW_FEISHU_*` environment names. Incoming messages still execute through MiniClaw's own `OpenAICompatibleClient`, `AgentLoop`, tools, and four-layer memory. Sessions are isolated under `.aster/feishu/sessions/<conversation>/`.

Start a supervised Goal from the CLI or Feishu:

```text
/goal 修复登录流程
验收条件：
- 正常凭据可以登录
- 错误密码被拒绝
- 完整测试通过
```

Use `/goal status`, `/goal resume`, and `/goal cancel` to manage the persisted Goal. Goal state is saved beside the conversation transcript as `goal.json`.

Dangerous operations use `ask` by default. CLI and Feishu show a six-character approval ID; reply with `批准 ABC123` or `拒绝 ABC123`. Set `MINICLAW_APPROVAL_POLICY=deny` for unattended fail-closed execution or `allow` only in a trusted environment. Project exceptions live in `.miniclaw/approval.json`:

```json
{
  "policy": "ask",
  "timeout_seconds": 300,
  "allowlist": [
    {
      "tool": "bash",
      "risk": "network-access",
      "command_glob": "git fetch origin *"
    },
    {
      "tool": "write",
      "risk": "file-overwrite",
      "path_glob": "docs/**"
    }
  ]
}
```

Allowlist fields are combined with AND. Keep rules narrow; changing the approval file itself is classified as a critical operation.

Trace and Eval commands:

```powershell
# Convert old pi-style tool-calls.jsonl / model-requests.jsonl directories.
python -m MiniClaw.trace.cli migrate D:\trace-data

# Turn the latest real failure in one session into a portable Eval Case.
python -m MiniClaw.trace.cli eval-case D:\session --out D:\evals\failure.json

# Replay recorded model boundaries with a different model/prompt/tool definition set.
python -m MiniClaw.trace.cli replay D:\session --env-file C:\path\to\.env --model another-model --prompt D:\prompt.txt --tools D:\tools.json --out D:\replay.json

# Build quality/cost reports and compare with a previous summary.
python -m MiniClaw.trace.cli report D:\trace-data --out D:\dashboard --baseline D:\previous\summary.json
```

Replay never executes recorded tools. It replays each recorded model decision against the exact sanitized context, which makes model and prompt comparisons safe but does not replace full workspace integration tests.

End-to-end Eval commands execute the real `CodingAssistant`, model, AgentLoop, tools, Goal,
Memory, WorkspaceGuard, ApprovalGate, Docker runtime, and Trace recorder. The grader separates
five dimensions instead of reducing everything to one success bit:

- `outcome`: final answer, files, commands, JSON, regex, or an optional independent LLM rubric
- `process`: Tool Calls, Goal state, instruction injection, memory retrieval, compaction, and event order
- `efficiency`: Agent/auxiliary requests, Token, cost, TTFT, latency, tool calls, cache, and wall time
- `safety`: workspace diff, forbidden side effects, approval decisions, sensitive paths, and Docker isolation
- `reliability`: repeated-run pass rate plus controlled retry, fallback, cancellation, and restart recovery

```powershell
# Four fast integration cases.
python -m MiniClaw.evaluation.cli evals\smoke.json --env-file .env

# Ten regression cases derived from representative benchmark failures and safety probes.
python -m MiniClaw.evaluation.cli evals\regression.json --env-file .env

# System resilience: all compaction layers, 429/network retry, provider fallback,
# ask/timeout approval, Docker cancellation, and Goal restart/resume.
python -m MiniClaw.evaluation.cli evals\resilience.json --env-file .env

# One composed 22-case gate. It fails if a required dimension or capability is missing.
python -m MiniClaw.evaluation.cli evals\full.json --env-file .env --jobs 4

# Select cases, compare a baseline, or save a new baseline.
python -m MiniClaw.evaluation.cli evals\regression.json --case memory_multihop_failure --baseline evals\baselines\previous.json
python -m MiniClaw.evaluation.cli evals\smoke.json --repeat 3 --env-file .env
python -m MiniClaw.evaluation.cli evals\regression.json --save-baseline evals\baselines\gpt-5.6-luna-regression.json
```

`evals/smoke.json` is the quick gate. `evals/regression.json` keeps benchmark provenance
(`benchmark`, source case, and failure signature) while rebuilding each selected failure as
a reproducible workspace task. Unlike model-boundary replay, these cases execute real side
effects inside an isolated copied fixture and grade both the final state and the trace.
`evals/full.json` composes all suites and declares a required capability matrix; a complete run
cannot silently pass when, for example, no cancellation or compaction case was actually present.

The versioned Eval portfolio follows a production-style four-track split: blocking regression,
non-blocking development/challenge, frozen held-out, and production Trace canaries. The current
`evals/splits/v1/manifest.json` declares 22 regression cases, 172 development executions, and 80
previously unexposed held-out questions. Selections are explicit ID files rather than fresh random
samples, and the manifest locks upstream commits and dataset hashes.

```powershell
# Validate that frozen files exist and development/held-out leakage checks remain zero.
python -m MiniClaw.evaluation.splits validate evals\splits\v1\manifest.json --project-root .

# Print the exact adapter commands for each track.
python -m MiniClaw.evaluation.splits commands evals\splits\v1\manifest.json --track regression
python -m MiniClaw.evaluation.splits commands evals\splits\v1\manifest.json --track development
python -m MiniClaw.evaluation.splits commands evals\splits\v1\manifest.json --track heldout
```

Do not repeatedly inspect and tune against the held-out IDs. Once a held-out result is opened for
case-level diagnosis, move the representative failure into regression and rotate a fresh unseen
question into the next split version. See `31.Eval数据划分与持续闭环.md` for the complete policy.

Cases may run repeatedly and report `pass_rate`, `pass_at_k`, `pass_all`, and `stable`. Efficiency
budgets distinguish main Agent requests from memory/compaction/Judge requests. Reports also count
live large-result artifacts, old-result artifacts, transcript archives, model summaries, saved
Token, retries, fallbacks, cancellations, approval outcomes, Goal attempts, and workspace changes.
See `29.端到端Eval.md` for the complete check, budget, fault-injection, and report contract.

## Architecture viewer

Run the dedicated interactive frontend for the complete SVG system map:

```powershell
python -m MiniClaw.frontend
```

Then open `http://127.0.0.1:8765/`. The viewer supports module focus, wheel zoom, drag pan, minimap navigation, fullscreen, keyboard controls, and SVG download. The standalone image is `frontend/architecture/miniclaw-architecture.svg`.

If the package is not installed, set the source directory for the current shell:

```powershell
$env:PYTHONPATH = "D:\MIniClaw\src"
```

## Design rules

1. `llm` is the lowest reusable layer: it knows provider protocols but not agents, files, or coding policy.
2. `agent` depends on `llm` and an abstract tool-host protocol, never on `coding_agent` or a concrete tool class.
3. `coding_agent` is the product layer corresponding to pi-coding-agent; it owns the assistant composition root and all coding-specific services.
4. `coding_agent.tools` owns concrete tool contracts and execution policy but does not decide task completion.
5. `coding_agent.memory` never rewrites the durable transcript; compacted context is only a model-facing view.
6. `trace` observes product boundaries but must never decide model or tool behavior.
7. New reliability features must have an isolated test before entering the default path.
8. Project instructions are resolved by `coding_agent.instructions`; `AgentLoop` only consumes a dynamic system-prompt provider.
9. Retrieved history is evidence, never policy: it is budgeted separately, injected below the system role, and traced exactly as rendered.

See `3.记忆与压缩.md` for the memory architecture and compaction evaluation settings.
See `5.长期记忆检索.md` for hybrid retrieval and conflict resolution.
See `6.Goal与外层监督.md` for Goal persistence, verification, and outer-loop supervision.
See `7.Trace与Eval数据闭环.md` for unified traces, failure-derived Evals, replay, clustering, and dashboards.
See `8.Runtime与Docker.md` for Runtime ownership, workspace modes, Docker isolation, configuration, and limitations.
See `9.危险操作审批.md` for risk categories, CLI/Feishu interaction, allowlists, timeout behavior, and Trace events.
See `10.SSE流式与模型容错.md` for SSE parsing, cancellation, timeout, retry, fallback, and Trace accounting.
See `11.端到端取消与中断.md` for process/container termination, atomic file commits, Goal cancellation, and Trace semantics.
See `12.项目指令自动加载.md` for instruction hierarchy, scoped activation, budgeting, cache invalidation, write protection, and Trace fields.
