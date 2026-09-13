# MiniClaw 工具 Runtime 最终流程

> 这是当前源码的面试口径：只有一个 `ToolExecutor` 执行入口；工具在启动时固定组装，不提供运行时注册、角色注入或动态启停。

## 一句话

Runtime 的职责是：把模型的一次 Tool Call，在确定的工作区和执行后端里安全地执行，最后只返回一个有界、可解释、可审计的结果。

模型只提出“工具名 + 参数”；它不直接读文件、不直接启动进程，也不决定是否可以绕过审批。

## 最好记的口诀：收、验、批、跑、收、交

```text
模型 Tool Call
  ↓
收：接收并查找工具
  ↓
验：准备参数、Schema 校验
  ↓
批：项目策略与风险审批（一次）
  ↓
跑：执行具体工具；Bash 再进入 Runtime 后端
  ↓
收：重试 / 取消 / 错误归一化 / 结果限长
  ↓
交：一次交给模型，一次写入 Trace
```

这里的两个“收”含义不同：第一个是收进请求，第二个是收束执行结果。

## 1. 会话启动：先固定边界

`CodingAssistant` 创建时只做一次环境组装：

1. `RuntimeSettings` 验证后端、工作区模式、资源和超时上限。
2. 选择有效工作区：`direct` 使用源目录，`snapshot` 使用会话副本。
3. 为所有文件工具创建同一个 `WorkspaceGuard`，负责路径归一化、越界检查和敏感路径保护。
4. 为 Bash 选择一个 `CommandExecutor`：Host 或 Docker；两者遵守同一个 `run(command, cwd, timeout, cancellation)` 契约。
5. 创建一个 `ToolExecutor`，传入固定工具集合，并装配两类扩展：前置检查和结果转换。

文件工具不需要进入容器；它们直接在有效工作区内通过 `WorkspaceGuard` 运行。Bash 因为可以执行任意命令，才经过 `ToolRuntime` 进入 Host 或 Docker。

```text
source workspace
  ├─ direct   → effective workspace = source
  └─ snapshot → effective workspace = session/sandbox/workspace

effective workspace
  ├─ read / write / edit / grep / search → WorkspaceGuard
  └─ bash → ToolRuntime → HostCommandExecutor 或 DockerCommandExecutor
```

默认的 `docker + direct` 表示“命令在容器里运行，但挂载的项目仍可写”。需要隔离源目录时使用 `snapshot`；Host 是明确选择的开发模式，不被误称为沙箱。

## 2. 一次调用：只有一个入口

AgentLoop 收到模型回复后，始终调用：

```python
await tool_executor.execute(call, cancellation_token)
```

AgentLoop 负责模型协议和消息顺序；`ToolExecutor` 负责工具生命周期。工具集合在 `CodingAssistant` 启动时一次性确定，Executor 只做名称查找和执行，不承担运行时注册、角色注入或动态启停。

## 3. Runtime 内部的六步

### 收：接收和查找

先记录 `call_id`，再按工具名查找。未知工具直接返回结构化错误，不准备参数、不启动进程。

模型可见的工具定义只保留模型真正需要的三部分：`name`、`description`、`parameters`。版本、幂等性、重试和副作用是 Runtime 内部策略，不重复塞进每次模型请求，减少前缀 Token，也避免模型把实现提示当成授权。

### 验：准备和校验

1. 深拷贝原始参数，避免工具修改模型历史。
2. 执行工具自己的 `prepare_arguments`（例如兼容旧 Edit 参数）。
3. 按 `input_schema` 检查类型、必填项、枚举、范围、数组元素和额外字段。

失败结果统一带有：

```json
{
  "code": "INVALID_ARGUMENT",
  "stage": "validation",
  "message": "...",
  "retryable": false,
  "not_started": true,
  "uncertain_side_effect": false
}
```

### 批：策略和审批

校验通过后执行前置检查链。当前产品顺序是：

```text
项目指令刷新检查 → 风险分类与 ApprovalGate
```

任何检查返回阻断结果，都不会进入工具主体；审批请求绑定规范化调用哈希，审批通过后再次核对调用没有被改变。

前置检查只对一次“逻辑调用”执行一次。后面如果发生安全重试，不会重复弹审批，也不会重复刷新策略。

### 跑：执行具体工具

普通工具在自己的实现里完成工作；Bash 统一走：

```text
BashTool
  → ToolRuntime.run
  → 校验 cwd 和有效超时
  → CommandExecutor.run
  → 返回 CommandExecution
```

Host 和 Docker 的差异只在后端：

- Host 创建独立进程组，捕获 stdout/stderr，取消时终止进程树。
- Docker 每次 Bash 使用独立容器，默认断网、只读根文件系统、去除 capabilities、限制 CPU/内存/PID/tmpfs，并动态遮蔽敏感路径；取消或超时时先移除容器，再回收 Docker CLI 进程。

两条后端路径最终都返回相同的 `CommandExecution`，所以上层不需要写两套 Bash 逻辑。

### 收：重试、取消和收束

这是可靠性与安全性的平衡点：

- 只对可判断为暂时网络错误、且调用幂等的操作做有限次指数退避重试，最多五次。
- `read`、`grep`、`search`、`skill` 是纯读；`memory.search` 和 `goal.status` 也可重试。
- `write`、`edit`、`bash` 以及 `memory.remember/replace/forget` 不自动重放。网络错误发生在可能已有副作用之后时，返回 `uncertain_side_effect=true`，让模型先核实状态。
- 取消令牌贯穿审批、工具等待和 Host/Docker 进程；原子写入在提交前再次检查取消，避免“用户已取消但后台晚到写入”。

这样既避免把一次短暂网络波动直接暴露给模型，又不为了追求成功率而重复不可逆操作。

### 交前：统一结果

所有成功、拒绝、异常、超时和取消都经过同一个收尾函数：

1. 执行结果转换器（例如大结果制品化）。
2. 统一错误结构。
3. 应用最后一道输出长度上限；超长内容只保留有界预览并标记 `warning=OUTPUT_TRUNCATED`。
4. 生成一次 `ToolResult`，附带 `status`、`phase=delivered`、重试次数、是否真正启动和副作用不确定性。

生命周期事件保持短而唯一：

```text
received → validated → preflighted → started
         → succeeded / failed / timed_out / cancelled
         → transformed → delivered
```

`transformed` 和 `delivered` 各记录一次；不会因为多个转换器重复制造多条“完成”事件。

## 4. 结果如何回到模型和 Trace

AgentLoop 把结果变成与 `call_id` 对应的 `tool` 消息，追加到会话历史，再请求模型决定下一步。没有出现在当前请求定义中的工具也通过同一个 Executor 返回结构化拒绝，不再由 AgentLoop 构造绕过 Runtime 的假结果。

Trace 只保留一条 `tool.call` 记录，包含：工具名、调用 ID、结果状态、执行时长、重试次数、取消/不确定副作用信息和精简生命周期事件。完整工具正文由工具自己的 Artifact 机制按需保存，不把同一份正文复制到每一层日志。

## 5. 和 Memory / Skill 的边界

Runtime 不负责记忆召回，也不把 Skill 当成语义记忆：

- `memory` 是一个独立工具；它的公开动作是 `search / remember / replace / forget`。
- `SKILL.md` 是按需加载的工作流指令，任务开始时选择并读取一次；压缩提交后再刷新一次。
- Skill 选择、Memory 召回和工具执行是三个不同阶段，不能互相冒充授权来源。

## 6. 我主动删掉的冗余

这次收敛不是单纯增加字段，而是删掉会让流程变长或产生歧义的路径：

1. 删除旧的工具管理兼容层，以及 Executor 内部的运行时注册、角色注入和动态启停接口；工具只在组装阶段确定。
2. 删除未被 AgentLoop 使用的 `execute_batch` 并行入口；当前按模型返回顺序执行，依赖关系更明确。
3. 删除 AgentLoop 对不可用工具的 `SimpleNamespace` 假结果分支，所有拒绝都回到 Executor。
4. 删除每次模型请求中的内部 capability/retry/side-effect 元数据，降低 Token 和前缀变化。
5. 合并重复的结果转换事件、重复的工具计时和重复的输出字段；内部 trace 使用一个输出对象。
6. 删除“对每次重试重新跑审批”的行为，审批绑定逻辑调用而不是某一次尝试。

## 30 秒面试回答

> 我把模型协议、工具执行和执行环境分开了。AgentLoop 只负责收发消息，所有 Tool Call 都进入一个 ToolExecutor，按“接收、校验、审批、执行、收束、交付”走统一生命周期。文件工具共享 WorkspaceGuard，Bash 通过 ToolRuntime 复用 Host/Docker 的同一命令契约。可靠性上只重试幂等的暂时网络错误，写操作遇到响应丢失不自动重放，而是显式标记副作用不确定；安全上把路径边界、审批、取消和输出上限放在公共边界；性能上只把紧凑工具 schema 发给模型，结果超限就制品化。最后每次调用只交付一个标准结果并写一条 Trace，所以流程可解释、可恢复，也容易定位问题。

## 代码入口

- `src/MiniClaw/coding_agent/tools/executor.py`：单一 Tool Call 入口、校验、重试、取消、结果收尾。
- `src/MiniClaw/coding_agent/runtime/core.py`：有效工作区、超时和命令后端组合。
- `src/MiniClaw/coding_agent/runtime/execution.py`：Host/Docker 的统一命令执行契约。
- `src/MiniClaw/coding_agent/runtime/workspace.py`：路径和敏感文件边界。
- `src/MiniClaw/coding_agent/runtime/state.py`：运行级持久状态与中断恢复。
- `src/MiniClaw/coding_agent/assistant/coding.py`：把产品策略组装进通用 AgentLoop。
