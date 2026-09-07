# MiniClaw

MiniClaw 是一个使用 Python 实现的 coding agent 框架，面向本地代码助手开发、Agent 架构学习和工具调用安全验证。项目将模型通信、Agent 循环、工具执行、记忆、任务监督、审批、运行时隔离和评测拆成独立模块。

## 主要能力

- 兼容 OpenAI 风格接口，支持 SSE、工具调用、超时、重试和模型回退。
- 通用 `AgentLoop` 与 coding agent 解耦，可替换模型和工具宿主。
- 提供 `read`、`write`、`edit`、`bash`、`grep`、`search` 工具，并校验路径和参数。
- 支持主机执行、Docker 执行、Direct 工作区和 Snapshot 工作区。
- 对文件修改、命令、网络访问和敏感路径提供 `allow / ask / deny` 审批策略。
- 提供 Working、Episodic、Semantic、Procedural 四层记忆和混合召回。
- 支持 Goal 任务状态机、验收条件、验证证据、独立评审和中断恢复。
- 记录统一 JSONL Trace，追踪模型、Token、费用、延迟、工具、审批和任务状态。
- 提供离线 Eval，分别检查结果、过程、效率、安全和可靠性。

## 架构

```text
llm                 模型协议、SSE、超时、重试与回退
  ↑
agent               通用 AgentLoop、事件和工具宿主协议
  ↑
coding_agent        工具、记忆、Goal、审批、Runtime 和指令加载
  ├─ tools           文件、命令、搜索和工具管理
  ├─ memory          上下文、长期记忆、压缩和召回
  ├─ goal            任务状态、验收和外层监督
  ├─ approval        风险识别和用户审批
  ├─ runtime         工作区映射、主机/Docker 执行
  └─ instructions    AGENTS.md / CLAUDE.md 规则加载

platforms / cli / trace / evaluation / benchmark
```

`llm` 不知道文件和任务策略；`AgentLoop` 只依赖抽象工具协议；`coding_agent` 负责组装具体服务；`trace` 只记录脱敏观察结果，不参与执行决策。

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

本仓库不包含 `docs/` 文档归档，也不包含本地运行时数据、实验结果、调研缓存或外部 benchmark 源码。外部 benchmark 需要时，可使用 `scripts/benchmarks/download_benchmarks.ps1` 下载到本地 `external/benchmarks/`。

## 安装

要求 Python 3.11 或更高版本：

```powershell
python -m pip install -e .
```

可选依赖：

```powershell
python -m pip install -e ".[feishu]"      # 飞书适配器
python -m pip install -e ".[retrieval]"   # 向量召回
python -m pip install -e ".[benchmark]"   # 外部评测
python -m pip install -e ".[all]"         # 全部组件
```

## 模型配置

复制 `.env.example` 为 `.env`，填入实际配置。`.env` 已被 Git 忽略，禁止提交真实密钥：

```text
MINICLAW_PROVIDER=primary
MINICLAW_PRIMARY_BASE_URL=https://your-openai-compatible-endpoint/v1
MINICLAW_PRIMARY_API_KEY=your-key
MINICLAW_PRIMARY_MODEL=your-model
```

也可以切换 DeepSeek：

```powershell
$env:MINICLAW_PROVIDER = "deepseek"
$env:MINICLAW_DEEPSEEK_API_KEY = "your-key"
```

常用可靠性参数：

```text
MINICLAW_LLM_TOTAL_TIMEOUT=120
MINICLAW_LLM_CONNECT_TIMEOUT=10
MINICLAW_LLM_FIRST_TOKEN_TIMEOUT=60
MINICLAW_LLM_IDLE_TIMEOUT=30
MINICLAW_LLM_MAX_RETRIES=2
MINICLAW_LLM_FALLBACKS=primary:backup-model,deepseek:deepseek-chat
```

## 启动助手

```powershell
python -m MiniClaw.cli --workspace D:\your-project
```

首次使用 Docker 前构建镜像：

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

只有 `bash` 会进入 Docker；文件和搜索工具在 Python Runtime 中执行，并统一经过 WorkspaceGuard。Snapshot 模式会先复制工作区，后续修改只发生在副本中。

## Goal 和审批

```text
/goal 修复登录流程
验收条件：
- 正常凭据可以登录
- 错误密码被拒绝
- 测试全部通过
```

使用 `/goal status`、`/goal resume`、`/goal cancel` 管理任务。危险操作默认需要审批；自动化环境可设置 `MINICLAW_APPROVAL_POLICY=deny`。

## Trace 和评测

```powershell
python -m MiniClaw.llm.smoke
python -m MiniClaw.trace.cli eval-case D:\session --out D:\evals\failure.json
python -m MiniClaw.evaluation.cli evals\smoke.json --env-file .env
python -m MiniClaw.evaluation.cli evals\regression.json --env-file .env
python -m MiniClaw.evaluation.cli evals\full.json --env-file .env --jobs 4
```

评测会检查：最终结果、工具和任务过程、Token/费用/延迟、安全边界，以及重试、恢复、取消后的可靠性。完整评测可能调用模型并产生费用，请先确认 API 配额。

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

访问 `http://127.0.0.1:8765/`，可查看模块关系、缩放、拖动和 SVG 下载。

## 许可证

项目使用 MIT License，元数据位于 `pyproject.toml`。
