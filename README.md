# MiniClaw

MiniClaw 是一个 Python 原生 coding-agent 框架，将模型调用、工具执行、记忆、上下文压缩、审批和任务监督组合成一条可测试、可恢复的运行链路。

## 核心能力

- OpenAI-compatible SSE：流式输出、超时、重试和显式 fallback
- 可取消的 Agent Loop，支持一轮多个工具调用
- 固定工具边界：`read`、`bash`、`edit`、`write`、`grep`、`search`
- 工作区路径保护、危险操作审批、主机/Docker 运行模式
- Working、Episodic、Semantic、Procedural 四层记忆
- 硬压缩、可恢复 artifact、Goal 状态持久化和重启恢复
- 分层加载 `AGENTS.md` / `CLAUDE.md` 项目指令

## 快速开始

需要 Python 3.11+：

```powershell
git clone https://github.com/xiongzeting/XXXClaw.git
cd XXXClaw
python -m pip install -e .
Copy-Item .env.example .env
```

在 `.env` 中配置 provider、API key 和模型，然后启动：

```powershell
python -m MiniClaw.cli --workspace D:\path\to\your-project
```

完整配置见 [.env.example](.env.example)。不要把真实 API key 写入源码、日志、trace 或提交记录。

## Docker 隔离

构建 `docker/runtime/Dockerfile` 后，可使用 `--sandbox docker:<image>` 和 `--workspace-mode snapshot` 在工作区副本中运行任务。snapshot 模式不会直接修改真实项目文件。

## 架构

```text
LLM providers → AgentLoop → CodingAssistant → Goal / Memory / Approval / Runtime
                              └──────────────→ ToolExecutor → read / bash / edit / write / grep / search
                              └──────────────→ Trace / Evaluation
```

`MiniClaw.llm` 负责模型协议，`MiniClaw.agent` 提供通用循环，`MiniClaw.coding_agent` 负责产品级组合；各层通过明确接口连接。

## 测试

```powershell
python -m unittest discover -s tests -v
python -m MiniClaw.llm.smoke
python -m MiniClaw.evaluation.cli evals/smoke.json --env-file .env
```

## 项目结构

| 目录 | 内容 |
| --- | --- |
| `src/MiniClaw/` | 核心 Python 包 |
| `frontend/` | 本地 Web UI |
| `docker/` | Docker 运行镜像 |
| `tests/` | 离线回归测试 |
| `evals/` | 评测套件与夹具 |
| `scripts/` | 开发、评测和维护脚本 |
| `docs/` | 架构与使用文档 |

## 文档

- [架构说明](ARCHITECTURE.md)
- [本地 Web UI](frontend/README-local-agent.md)
- [脚本索引](scripts/README.md)
- [评测说明](evals/README.md)
