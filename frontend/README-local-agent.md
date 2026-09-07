# MiniClaw 本地 Agent 前端

这是一个不需要 Node.js 的本地浏览器界面，直接调用真实 `CodingAssistant`、工具、Runtime、Memory 和 Goal。

在项目根目录运行：

```powershell
python frontend/local_agent_server.py --workspace D:\MIniClaw --port 8765
```

然后打开 <http://127.0.0.1:8765>。

常用参数：

```powershell
python frontend/local_agent_server.py `
  --workspace D:\MIniClaw `
  --env-file D:\MIniClaw\.env `
  --sandbox docker `
  --workspace-mode direct `
  --approval-policy ask
```

默认读取项目根目录的 `.env`，浏览器前端使用原有 `primary` 路由及其模型配置（当前为 `gpt-5.6-luna`，凭据为 `MINICLAW_PRIMARY_API_KEY`）；CLI、Eval 和 benchmark 仍使用各自原来的 provider。可用 `--provider deepseek --model deepseek-chat` 显式切换 DeepSeek。前端默认使用 `host` Runtime，因此不要求预先安装 Docker 镜像；需要 Docker 隔离时再显式传 `--sandbox docker`。浏览器请求会串行复用一个 `CodingAssistant` 会话；刷新页面不会重建后端会话，重新启动服务可通过 `--session-id` 恢复现有会话。

Windows Conda 环境若出现 `libiomp5md.dll` 重复加载，启动脚本会在导入 MiniClaw 前设置本地演示所需的兼容变量；长期使用仍建议统一 NumPy、FAISS、PyTorch 的 OpenMP 运行库。

提示：浏览器默认使用 `--approval-policy allow`，避免工具调用等待不存在的 CLI 输入框。`--approval-policy ask` 仍使用 CLI 审批处理器，只适合在能看到并回答 PowerShell 输入的情况下使用；如果需要浏览器内审批，应另行接入 Web 审批事件。

提示词的行为规则集中在 `src/MiniClaw/coding_agent/assistant/prompts.py`，启动时按 host/Docker 和实际操作系统补充终端说明。Windows host 的 `bash` 工具仍通过 cmd 执行；复杂 PowerShell 工作先读取技能，写入 `.ps1`，再调用检测到的 PowerShell 7 路径。

本工作区已从个人 Codex 技能目录复制 `powershell-safe-invocation/SKILL.md` 和 `reference.md` 到 `.aster/skills/powershell-safe-invocation/`。MiniClaw 在系统提示词中列出技能名称与描述，正文通过 `skill(action="read", name="powershell-safe-invocation")` 按需读取；这不是自动同步所有 Codex 指令。添加技能或修改 Python 提示词后需重启服务。

重启时可传 `--session-id <现有会话 UUID>` 恢复对应 `.aster/web/<UUID>/session.jsonl` 中的历史。`/api/info` 返回当前 `session_id`；新建对话仍生成新的 UUID。
