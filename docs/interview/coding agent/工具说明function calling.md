下面按“工具职责 → 参数 → 实际执行 → 返回结果 → 面试重点”的方式讲。

## 1. `read`

### 作用

读取工作区内的文件内容。

适合：

- 查看源码；
- 查看配置；
- 分段读取大文件；
- 读取 context artifact 完整内容。

不适合：

- 查找文件路径；
- 搜索文件内容；
- 修改文件。

### 参数

```json
{
  "path": "src/app.py",
  "offset": 1,
  "limit": 200
}
```

- `path`：必填，工作区相对路径；
- `offset`：可选，从第几行开始，当前是 1-based；
- `limit`：可选，读取多少行。

### 实际行为

- 通过 `WorkspaceGuard` 限制路径；
- 不允许越出工作区；
- 必须是文件；
- 大文件支持 `offset + limit` 分段；
- 输出有行数和字节数限制；
- 结果过大时返回截断标记和下一次建议的 offset；
- 返回文件 SHA-256；
- 读取已经存在的 artifact 时不会再次落盘。

### 面试重点

`read` 为什么需要 offset？

因为不能把大文件一次性放入模型上下文。offset 分段可以：

```text
read(file, offset=1, limit=200)
read(file, offset=201, limit=200)
```

这样既控制 token，也保留精确定位能力。

---

## 2. `write`

### 作用

创建或整体覆盖一个 UTF-8 文件。

```json
{
  "path": "config.json",
  "content": "{\n  \"port\": 8080\n}\n"
}
```

### 参数

- `path`：必填；
- `content`：必填，完整文件内容。

### 实际行为

- 路径经过 workspace guard；
- 必要时创建父目录；
- 使用原子写入；
- 支持取消；
- 返回写入字节数；
- 覆盖整个文件，不是局部修改。

### 面试重点

为什么不能所有修改都用 `write`？

因为 `write` 需要模型先拿到完整文件，并且存在整体覆盖风险。局部变更应该优先使用 `edit`，避免：

- 丢失并发修改；
- 错误覆盖无关内容；
- 传输大量原文；
- 修改范围不可审计。

---

## 3. `edit`

### 作用

按照唯一的 `oldText` 替换成 `newText`。

```json
{
  "path": "src/app.py",
  "edits": [
    {
      "oldText": "PORT = 8000",
      "newText": "PORT = 8080"
    }
  ]
}
```

### 参数

- `path`：必填；
- `edits`：必填数组；
- 每个 edit 必须有：
  - `oldText`
  - `newText`

### 实际行为

- 文件必须存在；
- `oldText` 必须匹配；
- `oldText` 不唯一时失败；
- 任意一条 edit 失败，整批失败；
- 检查重叠编辑；
- 保留 BOM 和原文件换行风格；
- 原子写入；
- 返回 diff 和变更位置。

### 面试重点

为什么要求 `oldText` 唯一？

这是为了避免“凭位置盲改”。唯一匹配可以保证：

```text
模型知道它修改的是哪一段
修改前内容确实存在
不会误改另一个相同片段
```

`edit` 是一种乐观并发控制：如果文件已经被其他操作改动，旧的 `oldText` 不再匹配，工具就失败，而不是静默覆盖。

---

## 4. `bash`

### 作用

在工作区执行命令。

```json
{
  "command": "python -m pytest tests/test_app.py",
  "timeout": 120
}
```

### 参数

- `command`：必填；
- `timeout`：可选，单位秒，范围 `0.001～900`。

### 实际行为

Windows 环境通常使用 `cmd.exe`，不是 Bash 或 PowerShell；Docker 模式则执行容器中的 POSIX shell。

执行过程包含：

```text
执行前工作区快照
  ↓
运行命令
  ↓
执行后工作区快照
  ↓
计算工作区变化
  ↓
限制模型可见输出
```

超过当前大工具阈值的成功结果会落盘为 MiniClaw context artifact。模型只收到：

```text
artifact 路径
大小
hash
短预览
```

需要完整内容时，再调用：

```json
{
  "path": ".aster/context-artifacts/..."
}
```

### 面试重点

为什么 `bash` 风险最高？

因为它可能：

- 修改文件；
- 删除文件；
- 访问网络；
- 启动进程；
- 改变环境；
- 产生副作用。

所以需要：

- ApprovalGate；
- 超时；
- cancellation；
- workspace 快照；
- 命令执行审计；
- 结果截断；
- artifact 恢复源；
- Docker 隔离或 host policy。

---

## 5. `ls`

### 作用

列出目录内容。

```json
{
  "path": "src",
  "limit": 100
}
```

### 参数

- `path`：可选，默认当前目录；
- `limit`：可选，最多 500 项。

### 返回

类似：

```text
src/agent/
src/config.py
src/main.py
```

目录通常带 `/` 标记。

### 特点

- 只列路径；
- 不读取文件内容；
- 自动排除受保护路径；
- 结果结构简单；
- 适合快速了解当前目录。

### 面试重点

`ls` 和 `search/find` 的区别：

```text
ls
  → 当前目录的一层内容

find
  → 递归按文件名模式查找

search
  → 按 glob 查找工作区路径

grep
  → 查找文件内容
```

---

## 6. `find`

### 作用

递归按文件名模式查找路径。

```json
{
  "pattern": "*.py",
  "path": "src",
  "type": "file",
  "limit": 100
}
```

### 参数

- `pattern`：必填，例如 `*.py`；
- `path`：可选，搜索根目录；
- `type`：`file`、`directory`、`any`；
- `limit`：最多 500。

### 特点

- 只返回路径；
- 不读取文件内容；
- 支持文件、目录、任意类型；
- 跳过符号链接和受保护路径。

### 面试重点

为什么不直接让模型用 bash `find`？

独立工具可以：

- 统一跨平台行为；
- 限制搜索范围；
- 自动过滤保护目录；
- 结构化返回；
- 更容易审计和测试；
- 避免 shell 注入和命令差异。

---

## 7. `search`

### 作用

按 glob 查找工作区路径。

```json
{
  "pattern": "src/**/*.py",
  "path": ".",
  "includeDirectories": false,
  "limit": 1000
}
```

### 参数

- `pattern`：必填；
- `path`：可选；
- `includeDirectories`：是否包含目录；
- `limit`：最多 10,000。

### 和 `find` 的区别

可以这样理解：

```text
find
  更偏向“文件名匹配”
  类型过滤更明确
  结果上限较小

search
  更偏向“glob 路径匹配”
  支持 src/**/*.py 这种工作区相对模式
  可以返回更多结果
```

面试时要说明：二者功能有重叠，但保留两个工具是为了让模型表达不同意图更清晰。

---

## 8. `grep`

### 作用

搜索文件内容。

```json
{
  "pattern": "TODO|FIXME",
  "path": "src",
  "glob": "*.py",
  "ignoreCase": true,
  "literal": false,
  "context": 2,
  "limit": 100
}
```

### 参数

- `pattern`：必填；
- `path`：搜索文件或目录；
- `glob`：文件过滤，例如 `*.py`；
- `ignoreCase`：忽略大小写；
- `literal`：按纯文本而不是正则匹配；
- `context`：返回匹配行前后文；
- `limit`：最多返回多少条匹配。

### 实际行为

- 使用 ripgrep；
- 返回路径、行号、内容；
- 支持正则；
- 支持上下文；
- 自动排除受保护路径；
- 输出受字节和条数限制；
- 结果太多时需要缩小 pattern、path 或 glob。

### 面试重点

`grep` 为什么不能无限返回？

因为搜索结果可能极大。无限输出会：

- 爆上下文；
- 触发工具截断；
- 降低相关性；
- 让模型重复读取。

更好的方式是：

```text
先 grep 精确关键词
再缩小 path 或 glob
最后 read 具体文件和行号
```

---

## 9. `memory`

### 作用

维护少量、稳定、长期的 semantic memory。

当前 semantic memory 主要记录：

- 用户明确确认的偏好；
- 稳定项目事实；
- 长期环境约定；
- 明确要求持续遵守的规则。

### 当前动作

```json
{
  "action": "search"
}
```

```json
{
  "action": "remember"
}
```

```json
{
  "action": "replace"
}
```

```json
{
  "action": "forget"
}
```

### `search`

只搜索 semantic 长期记忆，不是：

- 工作区搜索；
- 历史任务搜索；
- artifact 搜索；
- episodic 搜索。

例如：

```json
{
  "action": "search",
  "query": "Python 偏好",
  "limit": 5
}
```

### `remember`

写入长期记忆：

```json
{
  "action": "remember",
  "category": "preference",
  "content": "用户明确要求新项目优先使用 Python。",
  "evidence": "以后新项目一定优先用 Python"
}
```

### `replace`

修改已有记录，通常需要：

```json
{
  "action": "replace",
  "category": "preference",
  "recordId": "mem_xxx",
  "expectedRevision": 2,
  "content": "用户明确要求新项目优先使用 TypeScript。",
  "evidence": "以后前端项目优先使用 TypeScript"
}
```

### `forget`

删除已有长期记忆：

```json
{
  "action": "forget",
  "category": "preference",
  "recordId": "mem_xxx",
  "expectedRevision": 2
}
```

### 使用原则

模型不应该频繁调用 `memory`：

```text
没有长期记忆需求
  → 不调用 search

用户没有明确要求记住
  → 不调用 remember

没有先确认旧记录
  → 不调用 replace/forget

普通工具输出
  → 不写 semantic

临时验证结果
  → 不写 semantic
```

### 面试重点

为什么 semantic memory 不保存所有对话？

因为会产生：

- 噪声；
- 过期事实；
- 错误偏好；
- 任务状态污染；
- 召回冲突；
- token 膨胀。

Semantic memory 的核心是“稳定、少量、可复用”。

---

## 10. `goal`

### 作用

管理用户明确要求的长期 Goal 状态。

它不是普通任务进度，也不是 checklist 工具。

### Schema

```json
{
  "action": "status"
}
```

```json
{
  "action": "checkpoint",
  "summary": "已完成配置解析，正在验证迁移兼容性。"
}
```

```json
{
  "action": "waiting_for_user",
  "summary": "需要用户确认生产数据库连接方式。"
}
```

```json
{
  "action": "failed",
  "summary": "依赖服务不可用，无法继续验证。"
}
```

### 动作含义

- `status`：查看 Goal 状态；
- `checkpoint`：保存长期任务中间状态；
- `waiting_for_user`：任务需要用户输入；
- `failed`：Goal 失败。

### 重要限制

如果没有 active Goal：

```text
goal(status)
  → 可以返回没有活动 Goal

goal(checkpoint/failed/...)
  → 不会创建新的 Goal
```

Goal 只有在用户明确要求长期跟踪任务时才应该使用。

---

## 11. `goal_complete`

### 作用

提交明确 Goal 的最终回答。

```json
{
  "final_result": "已完成配置迁移，测试通过，相关文件为……"
}
```

### 重要限制

`goal_complete` 只在存在明确 Goal 时暴露或有效。

没有 active Goal 时：

```text
GOAL_NOT_ACTIVE
```

不会创建 Goal，也不会把普通任务错误标记为长期任务。

### 提交不等于通过

`goal_complete` 的作用是：

```text
模型提交最终结果
  → GoalStore 保存提交
  → 后续独立 judge 审核
```

它不代表：

- 自动验收通过；
- 测试一定成功；
- 用户一定满意；
- 文件一定存在。

所以工具结果会明确类似：

```text
Final answer submitted for later review
judge_status: pending
```

### 面试重点

为什么要把提交和验收分开？

因为模型自我宣布完成不等于真实完成。独立 judge 可以根据：

- 用户要求；
- 工具证据；
- 测试结果；
- 文件状态；
- Goal criteria；

重新判断是否通过。

---

# Function Calling 的实际格式

MiniClaw 使用 OpenAI-compatible 的工具调用结构。模型请求大致是：

```json
{
  "model": "model-name",
  "messages": [
    {
      "role": "system",
      "content": "system prompt"
    },
    {
      "role": "user",
      "content": "请读取 src/app.py"
    }
  ],
  "tools": [
    {
      "type": "function",
      "function": {
        "name": "read",
        "description": "读取工作区文件……",
        "parameters": {
          "type": "object",
          "properties": {
            "path": {
              "type": "string"
            },
            "offset": {
              "type": "integer",
              "minimum": 1
            },
            "limit": {
              "type": "integer",
              "minimum": 1
            }
          },
          "required": ["path"],
          "additionalProperties": false
        }
      }
    }
  ]
}
```

模型返回工具调用：

```json
{
  "role": "assistant",
  "content": null,
  "tool_calls": [
    {
      "id": "call_abc123",
      "type": "function",
      "function": {
        "name": "read",
        "arguments": "{\"path\":\"src/app.py\",\"offset\":1,\"limit\":200}"
      }
    }
  ]
}
```

注意 `arguments` 在很多 OpenAI-compatible API 中是 JSON 字符串，不一定是已经解析好的对象。

MiniClaw 接下来会：

```text
解析 JSON arguments
  ↓
检查工具是否注册
  ↓
校验 schema
  ↓
检查参数类型和 required
  ↓
执行权限和路径检查
  ↓
执行工具
  ↓
返回 ToolResult
```

工具结果消息：

```json
{
  "role": "tool",
  "tool_call_id": "call_abc123",
  "name": "read",
  "content": "文件内容……"
}
```

然后再次请求模型：

```json
{
  "messages": [
    {
      "role": "system",
      "content": "system prompt"
    },
    {
      "role": "user",
      "content": "请读取 src/app.py"
    },
    {
      "role": "assistant",
      "content": null,
      "tool_calls": [
        {
          "id": "call_abc123",
          "type": "function",
          "function": {
            "name": "read",
            "arguments": "{\"path\":\"src/app.py\"}"
          }
        }
      ]
    },
    {
      "role": "tool",
      "tool_call_id": "call_abc123",
      "name": "read",
      "content": "文件内容……"
    }
  ],
  "tools": [...]
}
```

模型随后可能：

```text
继续调用工具
```

或者：

```text
返回最终文本
```

---

# Tool Calling 的完整执行链

```text
用户请求
  ↓
构造 system prompt、历史消息、tool schema
  ↓
发送 ModelRequest
  ↓
模型返回普通文本或 tool_call
  ↓
ToolExecutor 找到工具
  ↓
schema 参数校验
  ↓
ApprovalGate 权限判断
  ↓
WorkspaceGuard / Docker / 运行时检查
  ↓
执行工具
  ↓
生成 ToolResult
  ↓
记录 trace
  ↓
追加 assistant tool call + tool result
  ↓
再次请求模型
```

---

# 工具注册机制

MiniClaw 的工具通常包含：

```python
class ReadTool:
    name = "read"
    description = "..."
    input_schema = {...}

    async def execute(self, arguments):
        ...
```

`ToolExecutor` 会建立：

```text
tool name
  → Tool object
```

然后导出 schema：

```python
{
    "type": "function",
    "function": {
        "name": tool.name,
        "description": tool.description,
        "parameters": tool.input_schema,
    },
}
```

面试可以这样说：

> Tool schema 是声明层，ToolExecutor 是执行层。模型只能请求工具，不能直接执行工具。执行器负责注册、校验、授权、取消、执行、结果封装和 trace。

---

# 参数校验和安全边界

## Schema 校验

检查：

- 必填字段；
- 类型；
- enum；
- minimum/maximum；
- additionalProperties；
- 数组元素；
- 参数结构。

例如：

```json
{
  "path": 123
}
```

对 `read` 来说应该拒绝，因为 `path` 要求 string。

## WorkspaceGuard

所有文件工具都经过路径边界检查：

```text
用户提供路径
  ↓
规范化路径
  ↓
解析绝对路径
  ↓
检查是否仍在 workspace 内
  ↓
检查保护路径
  ↓
允许或拒绝
```

可以防止：

```text
../../secret.txt
D:\other-project\file.txt
符号链接越界
```

## ApprovalGate

用于决定：

```text
allow
ask
deny
```

典型场景：

- `read`：通常 allow；
- `grep/search/ls/find`：通常 allow；
- `edit/write`：根据策略 ask 或 allow；
- `bash`：高风险命令 ask；
- 网络、删除、外部副作用：可能 deny。

---

# 工具错误和模型重试

工具错误不能只返回一句普通文本，最好包含：

```json
{
  "error": {
    "code": "PATH_OUTSIDE_WORKSPACE",
    "message": "Path is outside workspace",
    "retryable": false,
    "not_started": true,
    "uncertain_side_effect": false
  }
}
```

错误至少要区分：

```text
参数错误
权限拒绝
路径错误
工具未找到
命令非零退出
超时
取消
部分执行
副作用不确定
```

模型根据错误决定：

- 修正参数；
- 换工具；
- 读取更多上下文；
- 询问用户；
- 停止重试。

最重要的是不要无限自动重试。

---

# 工具结果截断和 artifact

大结果不能全部直接放进上下文：

```text
完整结果
  → 保存在 artifact 文件
  → 模型收到短预览、路径、大小、hash
```

模型需要完整内容时：

```json
{
  "name": "read",
  "arguments": {
    "path": ".aster/context-artifacts/abc.txt"
  }
}
```

artifact 不是普通 memory：

```text
artifact
  ≠ semantic memory
  ≠ episodic memory
  ≠ 自动召回源
```

它只是完整证据的恢复源。

---

# 面试常见问题

## 为什么工具 schema 要有 `additionalProperties: false`？

防止模型偷偷传入未定义字段，避免：

- 参数歧义；
- 版本不一致；
- 隐藏行为；
- 兼容性问题；
- 错误参数被静默忽略。

## 为什么 tool call 和 tool result 必须成对？

因为模型需要知道：

```text
哪个调用产生了哪个结果
```

如果 tool_call_id 不匹配，模型可能：

- 把结果归到错误工具；
- 重复执行；
- 判断工具失败；
- 破坏上下文协议。

## 为什么工具结果也要记录到 transcript？

因为后续模型需要看到执行事实，同时 trace 需要支持：

- 调试；
- 重放；
- 评测；
- 失败归因；
- 成本分析；
- 上下文压缩。

## 为什么不让模型直接写文件？

模型只生成工具调用，实际写入由工具执行器完成，这样可以：

- 统一安全策略；
- 统一审批；
- 统一原子写入；
- 统一 trace；
- 统一错误处理；
- 防止模型绕过 workspace guard。

## 为什么 `goal_complete` 不等于完成？

因为模型提交只是一个声明，实际结果还需要：

- 测试；
- 文件检查；
- Goal judge；
- 用户验收；
- 证据审查。

## 为什么 `memory` 不应该频繁调用？

因为长期记忆一旦污染，后续每个任务都可能受到影响。写入 semantic 必须比普通工具调用更谨慎。

## 工具调用和普通文本有什么区别？

普通文本：

```text
assistant content
```

工具调用：

```text
assistant tool_calls
```

工具调用必须经过：

```text
schema → executor → result → next model request
```

模型不能通过普通文本直接让系统执行命令。

## 如何处理流式 tool call？

流式响应中，工具调用参数可能被拆成多个 delta：

```text
delta 1: {"pa
delta 2: th":"src/
delta 3: app.py"}
```

系统需要：

```text
按 tool_call_id 聚合
  ↓
拼接 arguments
  ↓
流结束后解析 JSON
  ↓
再执行工具
```

不能每收到一个 delta 就执行一次。

## 如何防止重复工具调用？

常用方法：

- 记录 tool call ID；
- 保存规范化参数 hash；
- 对相同命令和未变化文件复用验证结果；
- tool executor 记录执行历史；
- mutation epoch 改变后再允许重新验证；
- 工具失败时不要盲目原样重放。

## 如何处理取消？

取消时要区分：

```text
尚未开始
正在执行
已经完成但结果尚未回传
副作用不确定
```

如果命令可能已经产生副作用，不能简单告诉模型“肯定没执行”，而应该标记：

```text
uncertain_side_effect: true
```

然后要求模型检查当前工作区状态。

---

# 最简面试总结

如果面试官让你整体介绍这套工具系统，可以这样回答：

> MiniClaw 采用 schema-driven function calling。模型只负责选择工具和生成 JSON 参数，ToolExecutor 负责注册、schema 校验、审批、路径安全、执行、取消、错误封装和 trace。文件类工具通过 WorkspaceGuard 限制工作区边界，bash 根据运行环境进入 cmd、POSIX shell 或 Docker。大工具结果会落盘为 artifact，模型只接收摘要和恢复路径。每次工具执行后，系统把 assistant tool call 和对应 tool result 追加回消息历史，再请求模型继续决策。memory、goal 和 goal_complete 属于状态型工具，其中长期记忆和 Goal 提交都有独立的持久化和验收约束，不能把模型声明直接当成事实或完成结果。