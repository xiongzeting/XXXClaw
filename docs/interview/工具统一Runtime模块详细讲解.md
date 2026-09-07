# MiniClaw 工具统一 Runtime 模块详细讲解

> 根据 2026-09-05 当前项目源码整理，面向理解代码和面试讲解。
> 阅读顺序：先看职责 → 再看启动 → 再走一次 Bash → 再走一次 Write → 最后看异常、边界和面试回答。

补充问题快速定位：**第 20 节**比较 ToolManager / ToolExecutor；**第 21 节**解释 Memory、Skill、Goal、Goal Complete 的审批和路径边界；**第 22～26 节**按顺序解释超时、重试、Token、文件过大、等待和工具失败。

**想先看具体过程，可以直接读第 27～35 节的典型例子。**第 27 节用一次完整代码修复串起主流程；第 28～32 节分别展开参数错误、审批、路径边界、超时取消、大输出；第 33～34 节解释 Memory／Skill 和 Goal 的调用；第 35 节给出对照表。案例中的文件、输出和调用 ID 是教学设定，不是本次实际执行记录。

## 1. 先用一句话理解它

**这套模块把模型提出的工具请求，变成在指定工作区、指定执行环境中运行，并能返回结果、处理取消和记录状态的实际操作。**

例如，用户说“修改代码并运行测试”，模型可能依次提出：

```text
search：找文件
read：读代码
edit：改代码
bash：运行 pytest
```

模型只负责提出工具名和参数。真正访问文件、启动进程的是项目代码。

这里的“统一”主要统一三件事：

1. **调用方式统一：**工具都用名称、参数和 ToolResult 交互。
2. **执行前后流程统一：**校验、前置检查、错误处理、结果加工走公共入口。
3. **工作区和命令环境统一：**文件工具和命令工具围绕同一个有效工作区执行，命令后端可以切换。

先记住：**统一调用，不代表所有工具都进入容器。**

## 2. 先分清三个名字，后面就不容易绕

### 2.1 ToolManager：当前能用哪些工具

当前默认用法中，它负责集中注册工具、提供工具列表，并把调用交给继承的公共执行流程。**内置工具注册时没有指定角色限制，默认也没有传入角色 allow/deny 策略，所以不会因为角色名称不同就禁止使用 Bash 等内置工具。**

它还支持按角色筛选、角色专属工具注入、动态启停和注销。这些是可选管理能力，不是当前默认已经启用的角色权限划分。例如，只有显式配置某角色禁止 `bash` 后，模型工具列表才会过滤 Bash，执行入口也会拒绝该角色的 Bash 请求。

当前 `CodingAssistant` 实际创建的是 `ToolManager`。它继承 `ToolExecutor`，因此也拥有下面的公共执行流程。默认不使用角色限制时，Manager 的额外管理能力大多没有启用，主要执行工作仍在 Executor；具体调用能否执行，还要经过指令检查、ApprovalGate 和相应工具的路径/Runtime 边界检查。

### 2.2 ToolExecutor：一次工具调用怎么走

它负责查找工具、准备参数、校验参数、运行前置检查、调用工具、处理错误和加工结果。

可以把它理解成**所有已接入工具的公共调用入口**。

### 2.3 ToolRuntime：工具在哪个工作区、命令在哪儿运行

它保存当前会话的工作区、运行配置、路径边界和命令执行器。

文件工具使用它提供的 `WorkspaceGuard`；Bash 进一步调用它的 `run()`，再由它转交给 Host 或 Docker 后端。

| 组件 | 先回答的问题 | 主要职责 |
| --- | --- | --- |
| ToolManager | 现在允许调用哪个工具？ | 注册、角色筛选、启停 |
| ToolExecutor | 这个工具请求应该怎样处理？ | 校验、前置检查、执行、结果处理 |
| ToolRuntime | 使用哪个工作区和命令后端？ | 工作区、超时配置、后端转发、运行元数据 |
| WorkspaceGuard | 这个文件路径允许访问吗？ | 路径映射、越界检查、按操作限制访问 |
| CommandExecutor | 命令具体怎么启动和停止？ | Host 进程或 Docker 容器执行 |

**代码中的 `ToolRuntime` 本身很薄。面试中讲“统一工具运行体系”可以覆盖这些组件，但不能把所有功能都说成由 `ToolRuntime.run()` 完成。**

## 3. 再看整体关系：只记住一处分叉

```text
用户提出任务
    ↓
AgentLoop 请求模型，获得工具名和参数
    ↓
ToolManager 检查当前角色是否可用
    ↓
ToolExecutor 做参数校验和前置检查
    ↓
调用具体工具
    ├─ read / write / edit / grep / search
    │      ↓
    │   使用共享 WorkspaceGuard
    │      ↓
    │   在宿主执行文件读写或搜索
    │
    └─ bash
           ↓
        ToolRuntime.run()
           ↓
        HostCommandExecutor 或 DockerCommandExecutor
    ↓
具体工具返回 ToolResult
    ↓
ToolExecutor 加工结果、限制文本长度
    ↓
AgentLoop 把结果写入对话，模型决定下一步
```

这里有两个需要提前说明的事实：

- 当前基础编程工具共六个：`read`、`bash`、`edit`、`write`、`grep`、`search`。
- 当前 AgentLoop 对同一轮返回的工具调用使用循环逐个执行；异步接口不等于这批工具自动并行。

## 4. 第一阶段：程序启动时，先把环境组装好

这一阶段通常在创建 `CodingAssistant` 时发生。它与“每次调用工具”是两回事。

### 第一步：读取配置

`load_runtime_settings()` 读取显式参数和 `MINICLAW_*` 环境变量，生成 `RuntimeSettings`。

关键默认值如下：

| 配置 | 默认值 | 含义 |
| --- | --- | --- |
| backend | docker | Bash 默认使用 Docker |
| workspace_mode | direct | 默认直接使用源项目目录 |
| docker_image | miniclaw-runtime:py311 | 预先准备好的运行镜像 |
| docker_network | none | 默认不开放容器外部网络 |
| default_command_timeout_seconds | 120 秒 | Bash 没传 timeout 时使用 |
| max_command_timeout_seconds | 900 秒 | Runtime 接受的命令超时上限 |
| docker_cpus | 1.0 | CPU 配额 |
| docker_memory_mb | 1024 | 内存限制 |
| docker_pids_limit | 256 | 进程数量限制 |
| docker_tmpfs_mb | 128 | `/tmp` 临时空间限制 |
| max_capture_bytes | 10 MiB | Docker 捕获输出的保留上限 |

配置加载时会检查后端名称、工作区模式、网络模式和资源限制等；不合法时提前报错。

注意区分配置加载和直接构造对象：这些检查主要在 `load_runtime_settings()` 中，直接创建 `RuntimeSettings(...)` 不等于自动执行全部检查。

### 第二步：确定三个工作区字段

`create_tool_runtime()` 计算以下字段：

| 字段 | 是什么 | Docker + Direct 示例 |
| --- | --- | --- |
| source_workspace | 用户原始项目目录 | `D:\MIniClaw` |
| host_workspace | 本次工具实际操作的宿主目录 | `D:\MIniClaw` |
| execution_workspace | 执行环境里的工作区路径 | `/workspace` |

如果是 Snapshot 模式：

```text
source_workspace：仍然是原项目目录
host_workspace：session_dir/sandbox/workspace
execution_workspace：Docker 中仍为 /workspace
```

**先确定操作哪份文件，再确定命令在哪个环境中访问这份文件。**

### 第三步：创建共享的 WorkspaceGuard

Guard 的根目录是 `host_workspace`，执行环境路径是 `execution_workspace`。

这让下面两个工具请求可以指向同一份文件：

```text
read(path="src/app.py")
read(path="/workspace/src/app.py")   ← Docker Runtime 的模型可见路径
```

二者会映射到有效宿主工作区中的 `src/app.py`，再接受访问检查。

### 第四步：选择命令后端

```text
backend=host   → HostCommandExecutor
backend=docker → DockerCommandExecutor
```

正常创建 Docker Runtime 时会验证 Docker 服务和镜像是否可用。失败就报错，不会自动降级为 Host。

这里验证的是环境，**还没有为每个工具创建一个常驻容器**。容器在具体 Bash 调用时创建。

### 第五步：把 Runtime 接到工具上

`create_coding_tools(runtime)` 的关键关系可以简化为：

```python
boundary = runtime.workspace

ReadTool(boundary)
BashTool(boundary, operations=runtime)
EditTool(boundary)
WriteTool(boundary)
GrepTool(boundary)
SearchTool(boundary)
```

这是“统一工作区”的关键：六个工具共享同一个边界对象，Bash 另外拿到 Runtime 作为命令运行接口。

工厂也兼容只传一个目录，此时 Bash 会采用本地执行器。当前 `CodingAssistant` 走的是传入完整 Runtime 的路径。

### 第六步：注册公共检查和结果处理

`CodingAssistant` 创建 `ToolManager` 时，按下面顺序接入前置检查：

```text
项目指令检查 → 风险审批
```

结果转换器顺序是：

```text
Goal 结果处理 → 项目指令结果处理 → 工作上下文制品化
```

随后注册基础工具、Memory 工具、Goal 工具以及额外工具。

到这里，启动阶段完成：**工作区、后端、工具列表和公共检查都已经准备好。**

## 5. 第二阶段：模型提出一次工具调用

下面固定用一个例子，后面不换场景：

```json
{
  "call_id": "call_001",
  "name": "bash",
  "arguments": {
    "command": "python -m pytest -q",
    "timeout": 60
  }
}
```

这里表示 `ToolInvocation` 的概念结构，不要求模型服务的原始传输格式与它完全相同。

- `call_id`：用来把这次调用和返回结果对应起来。
- `name`：用来查找工具。
- `arguments`：交给工具的参数。

AgentLoop 发出 `tool_started` 事件，然后调用：

```python
await self.tool_executor.execute(call, cancellation_token)
```

此处的 `tool_executor` 是前面组装好的 ToolManager。

## 6. 第三阶段：先检查请求，再执行工具

### 第一步：检查工具是否可用

ToolManager 先检查角色限制和启停状态，再进入 ToolExecutor。

ToolExecutor 根据名字查找工具。如果不存在，返回错误结果，不启动命令。

### 第二步：复制和准备参数

ToolExecutor 对 `call.arguments` 做深拷贝。如果工具提供 `prepare_arguments()`，就先调用它。

这样可以在不直接修改原始调用参数的前提下，做工具自己的参数准备。

### 第三步：按 input_schema 校验

以 Bash 为例：

- 必须提供 `command`，且它必须是字符串。
- `timeout` 必须是数值，范围是 0.001 到 900 秒。
- `goal_verification` 如果提供，必须是布尔值。
- 不接受声明之外的参数。

例如 `timeout="一分钟"` 会在这里失败，根本不会进入 Docker。

当前校验器实现了类型、必填项、枚举、范围、长度、数组元素和对象属性等常用规则，并非完整 JSON Schema 标准实现。

### 第四步：运行项目指令前置检查

这一步位于 `CodingAssistant._instruction_preflight()`。

对于存在目标路径的调用，它会激活相关路径的项目规则；对 `write/edit`，如果相关规则尚未注入或发生变化，就返回 `PROJECT_INSTRUCTIONS_REFRESH_REQUIRED`。

含义是：先让下一轮模型看到最新规则，再重新发起修改。本次文件修改不会执行。

这不是“自动证明模型完全遵守了所有自然语言规则”。它主要保证修改前先刷新相关规则。

顺序细节：当前 `_instruction_target()` 对 Read/Write/Edit/Grep 已经调用 Guard 解析目标，所以这些调用可能在项目指令检查阶段就因路径被保护而失败，早于 ApprovalGate。工具主体里的 Guard 检查仍然保留；不能把路径检查理解成全流程只在审批后执行一次。

### 第五步：运行风险审批

`ApprovalGate.authorize()` 先识别调用包含的风险能力。

顺序是：

1. 没有识别到风险项：直接放行。
2. 有风险项：建立审批请求，绑定调用标识、能力集合和规范化参数哈希。
3. 白名单覆盖全部风险能力：放行。
4. 否则，根据 `allow/deny/ask` 策略处理。
5. 获得允许后重新核对调用哈希；不一致则拒绝。

`ask` 没有交互入口、等待超时或处理失败时，不会默认为批准。

对例子中的测试命令，如果没有触发已实现的风险规则，通常直接通过。**没有识别出风险不等于证明测试代码没有副作用。**

### 第六步：真正调用工具

前置检查返回 `None` 表示继续；返回 `ToolResult` 表示提前结束本次工具执行，并将该结果送到后续处理。

全部通过后，ToolExecutor 调用 `BashTool.execute()`，并尽可能把共享取消令牌传下去。

到这里才开始实际执行。前面的步骤都属于“这次请求能不能执行”。

## 7. 第四阶段：Bash 进入 Runtime，再进入后端

### 第一步：BashTool 把命令交给 ToolRuntime

实际关系如下：

```text
BashTool.execute(arguments)
    ↓
runtime.run(command, boundary.root, timeout, cancellation_token)
```

BashTool 不自己拼 Docker 参数，也不自己决定切换 Host。

这就是依赖注入的具体作用：工具使用统一 `run()` 接口，执行环境由启动阶段决定。

### 第二步：ToolRuntime 确定有效超时

对当前例子，用户传入了 60 秒，因此使用 60 秒。

没传则使用默认 120 秒；超过 Runtime 配置的最大值则拒绝。

还有一个容易忽略的细节：Bash 的 Schema 自带 900 秒上限。因此即使 Runtime 最大值配置得更大，正常 Bash 工具调用仍受 Schema 限制；Runtime 最大值配得更小时，则还要通过这个更小的限制。

### 第三步：转发给具体 CommandExecutor

统一接口是：

```python
async def run(command, cwd, timeout, cancellation_token=None) -> CommandExecution:
    ...
```

返回值 `CommandExecution` 包含：

```text
output：原始输出 bytes
exit_code：退出码
details：运行后端、容器信息等元数据
```

Host 和 Docker 的启动方式不同，但对 BashTool 返回相同结构。

### 第四步 A：如果选择 Host

Host 后端使用 `asyncio.create_subprocess_shell()`，在有效工作区启动宿主 Shell，并合并标准输出和标准错误。

它建立独立进程组或会话，便于取消时清理进程树。

注意：工具名叫 Bash，并不保证 Host 在所有系统上都启动 Bash。Windows 下这条实现使用平台默认 Shell；Docker 路径明确使用 `sh -c`。

Host Bash 是可信本地执行模式：工作目录限制不等于文件系统沙箱，Shell 内部仍可能访问其他宿主路径和继承的环境。

### 第四步 B：如果选择 Docker——默认路径

Docker 后端按顺序完成以下操作：

1. 核对 `cwd` 必须是 Runtime 工作区根目录。
2. 检查是否已经取消。
3. 生成本次调用独有的容器名。
4. 构建 `docker run` 参数，并准备敏感路径遮蔽挂载。
5. 启动 Docker CLI，容器内部运行 `sh -c "python -m pytest -q"`。
6. 一边读取合并输出，一边等待退出、超时或取消。
7. 返回结果或抛出异常，并进入清理逻辑。

主要参数及用途如下：

| 设置 | 用途 |
| --- | --- |
| `--rm` | 容器退出后自动删除 |
| `--pull never` | 执行时不隐式下载镜像 |
| `--network none` | 默认隔离外部网络 |
| CPU / memory / PIDs 限制 | 控制命令资源消耗 |
| `--read-only` | 容器根文件系统只读 |
| `--cap-drop ALL` | 丢弃 Linux capabilities |
| `no-new-privileges` | 限制提权 |
| 受限 `/tmp` tmpfs | 为临时文件提供有上限的可写空间 |
| `/workspace` bind mount | 让命令访问有效工作区 |
| 敏感路径只读空挂载 | 遮蔽当前发现的受保护路径 |

每次 Bash 都创建新容器；工作区挂载中的文件可以保留，前一次容器里的临时状态通常不会保留。

例如，上一条命令在容器里执行 `export X=1`，下一条 Bash 不能依赖这个变量仍然存在。

## 8. 单独解释：Docker 和 Snapshot 是两个维度

先问“命令在哪里运行”，再问“操作哪份文件”：

| 组合 | 命令在哪儿运行 | 工具操作哪份文件 |
| --- | --- | --- |
| Host + Direct | 宿主 | 原项目 |
| Host + Snapshot | 宿主 | 会话副本 |
| Docker + Direct | 容器 | 挂载的原项目 |
| Docker + Snapshot | 容器 | 挂载的会话副本 |

### 8.1 为什么默认 Docker 仍然会修改真实项目

因为默认是 `docker + direct`：

```text
宿主 D:\MIniClaw
      ↕ 可写挂载
容器 /workspace
```

容器内修改 `/workspace/src/app.py`，就是修改宿主的真实文件。

`--read-only` 只约束容器根文件系统，不会自动让单独挂载的 `/workspace` 只读。

### 8.2 Snapshot 按什么顺序准备

1. 确定会话副本目录和 `manifest.json`。
2. 已有 Manifest 时，核对任务 ID 和源工作区，符合则复用。
3. 首次创建时，扫描允许复制的文件并核对文件数、总大小限制。
4. 排除敏感路径、符号链接、常见依赖目录和构建缓存。
5. 复制文件，写入 Manifest。
6. 后续文件工具与 Bash 都使用这个副本作为有效工作区。

默认限制为 50,000 个文件、512 MiB。

Snapshot 不会自动把改动合并回原项目，也不意味着命令本身获得进程隔离。尤其 Host + Snapshot 仍是宿主 Shell，不能只靠副本阻止命令访问原项目或其他路径。

## 9. 再走一遍 Write：理解文件工具为什么不需要进容器

这次例子换成：

```json
{
  "name": "write",
  "arguments": {
    "path": "docs/demo.md",
    "content": "Hello MiniClaw\n"
  }
}
```

前面流程不变：工具可用性 → 参数校验 → 项目指令检查 → 风险审批。

通过后，`WriteTool.execute()` 按下面顺序执行。

### 第一步：解析和检查目标路径

```python
path = boundary.resolve(arguments["path"], access="write")
```

WorkspaceGuard 会先把路径映射到有效工作区，解析真实路径，再确认它仍位于工作区内，并应用写入权限规则。

对于尚不存在的文件，还会检查最近的已有父目录，防止通过父目录符号链接跳到外部。

`..` 不是一出现就必然被 Guard 拒绝；关键是规范化后的路径是否越界。审批白名单对父目录引用有另外的拒绝规则，两者不要混淆。

### 第二步：同一路径的修改排队

`with_file_mutation_queue()` 按规范化绝对路径建立 `asyncio.Lock`，让本进程内接入此队列的同文件 Write/Edit 顺序执行。

它避免两个文件工具同时改同一路径造成交叉覆盖。它不是跨进程锁，也管不到外部编辑器或 Bash 对文件的直接修改。

### 第三步：先写临时文件

`atomic_write_bytes()` 在目标文件旁边创建临时文件，将完整内容写进去。

这个阶段还没有替换目标文件。

### 第四步：提交前再检查取消

临时文件写完后，再检查取消令牌。如果已经取消，不继续替换目标。

### 第五步：原子替换目标文件

使用 `os.replace()` 将临时文件替换为目标文件，最后清理临时文件。

这里的原子性针对单文件替换，不代表多个文件一起提交，也不等于断电后必然持久化的数据库事务。

### 第六步：返回 ToolResult

结果包含成功文本，以及目标路径和写入字节数等信息。

**整个 Write 流程都在宿主执行；如果采用 Snapshot，它写的就是宿主上的会话副本。**

## 10. WorkspaceGuard 的权限规则再看一眼

Guard 按操作区分 `read/write/search/execute`，同一路径在不同操作下可以有不同权限。

| 路径例子 | 当前规则的重点 |
| --- | --- |
| `src/app.py` | 在工作区内时可正常读写和搜索 |
| 工作区外路径 | 文件工具访问被拒绝 |
| `.env`、密钥文件、凭据目录 | 按规则保护 |
| `.git/HEAD` | 可读，但文件工具不能直接写 `.git` |
| `.git/config` | 作为敏感路径保护 |
| `.aster` 内部状态 | 默认保护 |
| `.aster/tool-output`、`.aster/context-artifacts` | 允许通过 Read 读取制品，仍限制写入和搜索等操作 |

访问时会动态判断路径规则；Docker 每次准备命令时也会重新发现当前存在的受保护路径，因此启动后新增的 `.env` 可以被下一次容器调用遮蔽。

这些是路径规则和执行前检查，不能描述成对任意恶意并发文件变动的完整防护。尤其 Guard 不会逐条拦截 Shell 内部的系统调用。

## 11. 第五阶段：执行结果怎样回到模型

### 第一步：底层命令返回 CommandExecution

以 Bash 为例：

```text
output = 测试输出的 bytes
exit_code = 0 或非零
details = runtime、image、container_name 等
```

### 第二步：BashTool 转成 ToolResult

它把 bytes 解码为文本，截取尾部输出，将非零退出码标记为 `is_error=True`。

统一工具结果结构为：

```python
ToolResult(
    content="给模型看的结果文本",
    is_error=False,
    details={"用于事件和审计的结构化信息": "..."},
)
```

文件工具直接构造 ToolResult，不需要先构造 CommandExecution。

### 第三步：ToolExecutor 运行结果转换器

当前 CodingAssistant 接入 Goal、项目指令和上下文制品化处理。

比如较大的结果可以保存为制品，只把引用和预览放回工作上下文。

转换器发生普通异常时，会在 `details` 中记录 `result_transform_error`，而不是因此丢掉已有工具结果。

多数在公共执行流程中产生的错误、前置阻断结果也会经过转换器；未知工具或角色不可用属于提前返回的分支。

### 第四步：应用最终文本上限

ToolExecutor 默认最多保留 100,000 个内容字符，并在末尾追加截断提示。

### 第五步：AgentLoop 写入工具消息

AgentLoop 把 `result.content` 写成 `role="tool"` 的消息，并使用 `tool_call_id` 对应之前的请求。

`is_error` 和 `details` 随 `tool_finished` 事件传递，供上层记录和展示；不会自动作为完整字典塞进工具消息正文。

下一次请求模型时，模型看到工具结果：测试成功就继续收尾，测试失败就根据输出继续定位。

## 12. 输出限制为什么分好几层

按实际经过的顺序理解即可：

| 层次 | 当前限制 | 保护什么 |
| --- | --- | --- |
| Docker 输出捕获 | 默认保留最后 10 MiB | 避免持续输出无限占用捕获缓冲区 |
| BashTool 文本裁剪 | 最后 2,000 行或 50 KiB | 控制单次工具结果大小 |
| 上下文制品化 | 根据工作上下文策略保存引用和预览 | 控制进入模型的上下文 |
| ToolExecutor 最终兜底 | 100,000 字符内容，再附提示 | 限制最终返回文本 |

BashTool 发生裁剪时，会将 Runtime 返回的 bytes 保存到 `.aster/tool-output/bash-<uuid>.log`。

如果 Docker 捕获阶段已经丢弃了更早输出，这个文件保存的也只是剩下的部分。它不是无限量的完整日志。

另外，当前 Host 后端使用 `process.communicate()` 收集输出，没有 Docker `_capture_tail()` 的这层 10 MiB 保留限制，不能把这个上限说成两个后端都已经实现。

## 13. 出错、超时、取消，分别会怎样

### 13.1 普通错误：变成工具结果

参数类型不对、文件不存在、路径越界等普通异常，会被 ToolExecutor 转换成错误 ToolResult。

```text
content = "异常类型: 错误信息"
is_error = True
```

这样模型可以根据错误调整下一次调用。

命令退出码非零则由 BashTool 构造错误结果，不要求底层一定抛出异常。

### 13.2 超时：停止执行，再返回错误

这里有两个主要执行超时层次：

- ToolExecutor 的 `timeout_seconds`：约束具体工具执行等待，默认 `None`，不是默认包住整条审批链。
- Runtime 的命令超时：正常 Bash 路径默认 120 秒，最大值由配置和工具 Schema 一起限制。

审批等待还有 ApprovalGate 自己的超时。

Host 超时会尝试终止进程树；Docker 超时会尝试删除容器并终止 Docker CLI 进程树，然后抛出超时错误。

### 13.3 取消：作为控制信号向上传递

共享的 `CancellationToken` 会贯穿 AgentLoop、ToolExecutor、支持它的工具和 Runtime。

等待过程中会同时观察“操作完成”和“取消信号”。用户取消时，不应仅停止等待结果，还要清理仍然运行的资源。

```text
用户取消
    ↓
共享令牌触发
    ↓
ToolExecutor / Runtime 取消等待中的操作
    ↓
Host：终止进程树
Docker：尝试 docker rm -f，并终止 Docker CLI 进程树
    ↓
取消向上传递，AgentLoop 停止当前运行并报告取消状态
```

代码使用 shield 等机制尽量让清理完成；Docker 删除过程也有自己的超时和异常处理，因此不能把清理尝试描述成在所有故障下都保证成功。

不是所有工具都支持同样粒度的协作取消。例如不接收取消令牌、把扫描交给工作线程的工具，取消外层等待不等于立即强杀线程。

### 13.4 最重要的边界：取消不等于回滚

如果命令已经删除了文件，或者 Write 已完成 `os.replace()`，后续取消不会自动恢复原内容。

取消的作用是停止后续工作并清理运行资源；恢复已完成的副作用需要备份、版本控制、补偿或事务机制。

## 14. 运行状态和 Trace 怎么接进来

这里按“记录环境、记录过程、发现中断”理解，不需要先钻进整个 Trace 系统。

### 14.1 开始时记录 Runtime 环境

`ToolRuntime.trace_metadata()` 提供后端、工作区模式、三个工作区路径、超时和 Docker 资源配置；Snapshot 时还包含复制统计。

它的价值是事后能回答：当时究竟在原项目还是副本执行，使用 Docker 还是 Host，网络是什么设置。

### 14.2 调用过程中记录事件

AgentLoop 发出工具开始、工具结束等事件，上层 CodingAssistant 负责衔接记录。审批也有请求和决定记录。

不要说成 `ToolRuntime.run()` 独自记录了整个工具生命周期：它主要提供命令执行和相关元数据。

### 14.3 RunStateStore 保存最近运行状态

`runtime/state.py` 保存 `run-state.json`，包括运行状态、活动工具和已完成调用 ID 等。

恢复时，如果之前仍是非终态、但记录的进程已经不存在，会标记为 `interrupted`，便于解释上次在哪一步中断。

这属于中断检测，不是从任意指令自动恢复执行，也不是 Exactly-Once 保证。已完成调用 ID 列表本身不会让外部副作用自动去重。

## 15. 为什么要这样拆：面试里讲设计价值

### 15.1 改运行环境，不必重写模型循环

模型仍请求同一个 `bash` 工具；BashTool 仍调用 `run()`；启动时替换后端即可改变命令执行位置。

不过“接口相同”不等于“行为完全相同”：Host 和 Docker 的系统、依赖、网络、Shell 和输出捕获机制都可能不同。

### 15.2 文件工具保留清晰的文件语义

Read、Write、Edit 可以直接实现路径检查、编码处理和原子替换，不必把每个文件操作都拼成 Shell 命令。

Grep、Search 在宿主做搜索实现，也共享工作区规则。

### 15.3 各种限制分别解决不同问题

| 机制 | 解决的问题 | 不应承诺的能力 |
| --- | --- | --- |
| Schema | 参数结构是否正确 | 理解用户真实意图 |
| 项目指令刷新 | 修改前是否看到当前相关规则 | 证明完全遵守自然语言规则 |
| ApprovalGate | 已识别风险是否被允许 | 识别任意 Shell 的全部副作用 |
| WorkspaceGuard | 文件工具的路径和操作权限 | 拦截 Shell 的每个文件访问 |
| Docker | 命令进程、资源和网络隔离 | 保证可写工作区不会被破坏 |
| Snapshot | 为正常工作区操作提供副本 | 自动合并或完整事务回滚 |
| 原子写入 | 单文件替换的一致性 | 跨文件事务、跨进程互斥 |
| Trace / RunState | 解释运行过程和中断 | 自动恢复所有操作 |

当前 Dockerfile 没有声明固定非 Root 用户；Shell 风险识别和路径遮蔽也都有适用边界。因此可以讲“多层约束”，不应讲“绝对安全沙箱”。

## 16. 如果要新增一个工具，按什么顺序做

1. **先定义能力和输入：**明确工具名、说明和 `input_schema`。
2. **实现统一接口：**提供异步 `execute()`，返回 ToolResult。
3. **涉及文件时复用 Guard：**使用有效工作区，按 read/write/search 选择权限。
4. **涉及文件修改时复用一致性机制：**按需接入同路径队列和原子写入。
5. **涉及命令时复用 Runtime：**不要在新工具里随意绕开后端和超时策略启动命令。
6. **有副作用时接入风险分类：**新工具被注册，不代表审批规则已经能识别它。
7. **按需要支持取消：**尤其是耗时操作和产生副作用的提交点。
8. **注册并验证：**通过工厂、额外工具或角色工具入口注册，检查正常调用、拒绝和错误分支。

要强调：统一框架提供公共机制，但新增工具仍必须主动接入正确的工作区、风险和执行边界。

## 17. 读源码就按这个顺序，不要一上来读最长的文件

下面给出当前项目的绝对路径，方便直接打开。

| 顺序 | 文件 | 此时只关注什么 |
| --- | --- | --- |
| 1 | [base.py](D:/MIniClaw/src/MiniClaw/coding_agent/tools/base.py) | Tool 和 ToolResult 的统一形状 |
| 2 | [core.py](D:/MIniClaw/src/MiniClaw/coding_agent/runtime/core.py) | ToolRuntime 字段、创建流程、run 转发 |
| 3 | [factory.py](D:/MIniClaw/src/MiniClaw/coding_agent/tools/factory.py) | 六个工具怎样共享 Runtime |
| 4 | [manager.py](D:/MIniClaw/src/MiniClaw/coding_agent/tools/manager.py) | 角色筛选与执行入口 |
| 5 | [executor.py](D:/MIniClaw/src/MiniClaw/coding_agent/tools/executor.py) | execute 从前置检查到结果返回 |
| 6 | [bash.py](D:/MIniClaw/src/MiniClaw/coding_agent/tools/bash.py) | 命令请求和命令结果怎样转换 |
| 7 | [execution.py](D:/MIniClaw/src/MiniClaw/coding_agent/runtime/execution.py) | 先看两个 run，再看 Docker 参数和清理 |
| 8 | [workspace.py](D:/MIniClaw/src/MiniClaw/coding_agent/runtime/workspace.py) | 路径映射、真实路径检查、操作权限 |
| 9 | [write.py](D:/MIniClaw/src/MiniClaw/coding_agent/tools/write.py) | 文件分支怎样执行 |
| 10 | [atomic.py](D:/MIniClaw/src/MiniClaw/coding_agent/tools/atomic.py) 与 [mutation_queue.py](D:/MIniClaw/src/MiniClaw/coding_agent/tools/mutation_queue.py) | 写入顺序与提交点 |
| 11 | [coding.py](D:/MIniClaw/src/MiniClaw/coding_agent/assistant/coding.py) | 启动装配、前置检查和结果转换 |
| 12 | [loop.py](D:/MIniClaw/src/MiniClaw/agent/loop.py) | 调用怎样进入和返回对话 |

理解主线之后，再补读 [config.py](D:/MIniClaw/src/MiniClaw/coding_agent/runtime/config.py)、[snapshot.py](D:/MIniClaw/src/MiniClaw/coding_agent/runtime/snapshot.py)、[state.py](D:/MIniClaw/src/MiniClaw/coding_agent/runtime/state.py) 和 [gate.py](D:/MIniClaw/src/MiniClaw/coding_agent/approval/gate.py)。

验证思路可参考 [test_runtime.py](D:/MIniClaw/tests/test_runtime.py) 中的默认配置、路径映射、快照过滤、动态敏感文件保护、工厂路由和 Docker 参数测试。

真实 Docker 环境检查另有 [smoke.py](D:/MIniClaw/src/MiniClaw/coding_agent/runtime/smoke.py)，覆盖文件落盘、敏感文件遮蔽和超时清理。本文是源码讲解，没有在撰写时实际启动这项 Docker 检查。

## 18. 面试时按这段顺序讲

### 18.1 30 秒版本

> 我把工具调用和执行环境分开了。ToolManager 管工具注册和角色可用性，ToolExecutor 统一做参数校验、前置检查、取消和结果处理；ToolRuntime 提供共享工作区和可切换的命令后端。文件工具在宿主通过 WorkspaceGuard 执行，Bash 通过 Runtime 进入 Host 或 Docker。默认是 Docker 加 Direct，所以命令有容器约束，但项目文件仍然是真实可写的。

### 18.2 两分钟版本：按五步讲

1. **先初始化。**读取配置，确定原项目、有效工作区和执行路径，创建 Guard 和命令后端，再把它们注入工具。
2. **再接收调用。**模型返回工具名和参数，ToolManager 检查可用性，ToolExecutor 复制参数并校验 Schema。
3. **然后执行前置检查。**先处理相关项目规则刷新，再识别风险并按白名单或审批策略决定是否继续。
4. **接着执行。**文件工具走 Guard 和宿主实现；Bash 调用 Runtime，由 Host 或 Docker 执行，负责超时与取消清理。
5. **最后返回结果。**工具统一返回 ToolResult，经过结果转换和输出限制，再写回模型对话，同时上层记录事件与运行状态。

补充一句边界：容器隔离、审批、原子写入和取消解决的是不同问题，都不能单独保证整个任务可回滚。

### 18.3 常见追问：直接回答

**问：统一 Runtime 是不是所有工具都调用 `runtime.run()`？**

答：不是。文件工具共享 Runtime 的 WorkspaceGuard；Bash 才通过 `run()` 进入命令后端。公共工具调用流程由 ToolExecutor 统一。

**问：Docker 已经存在，为什么还要审批？**

答：默认工作区是可写挂载，命令仍可以破坏项目。审批处理授权，Docker 限制运行能力，职责不同。

**问：为什么要同时保留 Direct 和 Snapshot？**

答：Direct 让修改直接作用于项目，适合实际开发；Snapshot 将正常工具工作区切换到副本，便于隔离文件改动，但不会自动回写。

**问：批准联网命令后，默认就能联网了吗？**

答：不能。审批通过不改变 Docker 的 `network=none` 配置。授权和执行环境约束都需要满足。

**问：`async` 是不是意味着工具并行执行？**

答：不是。当前 AgentLoop 逐个等待工具完成；异步主要支持不阻塞等待、取消信号和子进程 I/O。

**问：崩溃重启后会自动从上一条命令继续吗？**

答：当前 RunStateStore 能发现并标记中断，不能据此承诺自动重放或 Exactly-Once。执行过的副作用还需要单独核对。

**问：你认为最值得继续加强的点是什么？**

答：固定非 Root 容器用户、加强 Shell 风险分析、给 Host 输出捕获增加上限、完善工作区改动恢复机制。先补清晰的边界，再考虑复用容器降低启动成本。

## 19. 复习时只按这条顺序回忆

```text
启动：配置 → 工作区 → Guard → 后端 → 工具注册

调用：工具可用性 → 参数校验 → 项目规则 → 风险审批

执行：文件工具走 Guard；Bash 走 Runtime → Host / Docker

返回：ToolResult → 结果加工 → 输出限制 → 工具消息 → 下一轮模型

异常：普通错误给反馈；超时和取消做清理；已经完成的副作用不自动回滚
```

## 20. ToolExecutor 和 ToolManager 到底有什么区别

### 20.1 先看继承关系

```python
class ToolManager(ToolExecutor):
    ...
```

**ToolManager 是带有角色和动态管理能力的 ToolExecutor。**不是两个互不相干的服务，也不是两套执行流程。

当前对象关系为：

```text
CodingAssistant 创建一个 ToolManager 对象
    ↓
赋给 self.tool_executor
    ↓
AgentLoop 通过 tool_executor 调用它
    ↓
ToolManager.execute() 先检查可用性
    ↓
super().execute() 进入 ToolExecutor 公共流程
```

所以看到变量名 `tool_executor` 时，要继续看对象实际类型；这里实际是 ToolManager。

### 20.2 哪些能力原来就有，哪些是新增的

| 能力 | ToolExecutor | ToolManager |
| --- | --- | --- |
| 保存名称到工具对象的注册表 | 有，`_tools` 字典 | 继承使用 |
| register | 有，拒绝重名注册 | 扩展 roles 和 replace |
| definitions | 返回已注册工具定义 | 只返回当前角色可用的定义 |
| execute | 公共校验、检查、执行和结果处理 | 先检查可用性，再调用父类 |
| 按角色注入工具 | 无 | `inject()` |
| 删除工具注册 | 无专门接口 | `unregister()` |
| 动态启停工具 | 无专门接口 | `set_enabled()` |
| 切换当前角色 | 无 | `set_role()` |
| allow / deny 工具名单 | 无 | `ToolRolePolicy` |

因此不能说“ToolExecutor 完全不管注册”。它本来就有最基本的注册表；ToolManager 在这个基础上增加管理策略。

### 20.3 当前默认行为与可选限制示例

当前 `CodingAssistant` 的 `tool_role_policies` 和 `role_tools` 默认都是 `None`。内置编码、记忆、Goal 和 Goal Complete 工具都通过不带 `roles` 的 `register(tool)` 注册。`ToolManager._available()` 在工具未禁用、未绑定专属角色、也没有角色策略时返回 true。

因此，**当前默认下所有角色都可以使用这些已注册工具**。源码中的 reviewer 限制示例位于测试中，用于验证可选功能；不能把它讲成当前产品默认的角色分工。

Manager 的价值是提供统一的动态管理接口，以便调用方需要时启用；如果一直使用固定工具集合，也不需要角色限制或动态管理，仅使用 ToolExecutor 的基础能力也能承担核心执行职责。不能把 Manager 描述为当前不可缺少的角色安全防线。

下面是假设调用方显式配置限制之后的行为：Bash 已经注册，但当前角色的工具策略禁止 Bash。

1. `definitions()` 先过滤，模型本轮看不到 Bash。
2. 如果旧上下文里的模型仍然发出 Bash 请求，`execute()` 再检查。
3. 返回 `tool_not_available` 错误结果。
4. 不进入父类执行流程，也不会启动命令。

这里的角色 allow/deny 限制的是**工具是否可调用**；ApprovalGate 的 allow/ask/deny 处理的是**这次具体调用的风险授权**。两套策略不能混为一谈。

### 20.4 面试时一句话回答

> ToolExecutor 提供基础注册表和统一执行管线；ToolManager 继承它，增加可选的角色策略、工具注入和动态启停。当前默认内置工具向所有角色开放，未启用角色权限划分；Manager 检查通过后，仍由 Executor 执行参数校验、前置检查和结果处理。

## 21. Memory、Skill、Goal、Goal Complete 怎么过审批和 WorkspaceGuard

### 21.1 先给结论表

以下是当前 CodingAssistant 的注册方式及当前风险分类规则，不能套用成未来所有插件的行为。

| 工具 | 经过 ToolExecutor？ | 经过 ApprovalGate 入口？ | 当前有专门风险规则？ | 使用 Runtime 的 WorkspaceGuard？ |
| --- | --- | --- | --- | --- |
| memory | 是 | 是，前面未被阻断时 | 没有，返回空风险集合 | 否，使用 Memory 各 Store |
| skill | 是 | 是，前面未被阻断时 | 没有，返回空风险集合 | 否，使用 ProceduralMemoryStore |
| goal | 是 | 是，前面未被阻断时 | 没有，返回空风险集合 | 否，使用 GoalStore |
| goal_complete | 是 | 是，前面未被阻断时 | 没有，返回空风险集合 | 否，使用 GoalStore 和可选 Judge |

`classify_tool_risks()` 当前只对 `bash`、`write`、`edit` 有专门分支，其余工具直接返回 `()`。

ApprovalGate 一看到没有风险项，就返回 `None` 放行，发生在白名单和 policy 分支之前。**因此把审批 policy 改成 deny，也不会自动禁止 memory 的 remember/forget。**需要角色层禁用工具，或新增该 action 的风险规则。

项目指令 preflight 的文件目标解析同样只处理基础路径工具；这四个工具不会因为内部状态文件位于 `.aster` 就被自动拦下。

### 21.2 Memory：受控业务操作，不是任意路径读写

以 `memory(action="remember", category="project", content="...")` 为例：

```text
Manager 检查 memory 可用
    ↓
Executor 校验参数
    ↓
项目指令检查没有对应文件目标
    ↓
ApprovalGate 无专门风险项，放行
    ↓
MemoryTool 按 action 分发
    ↓
SemanticMemoryStore 写入配置好的记忆存储
    ↓
按实际写入结果记录证据，返回 ToolResult
```

MemoryManager 在初始化时建立语义记忆、情节记忆、归档索引和证据存储。基本路径是**源项目**下的 `.aster/memory`；有用户或频道作用域时，会再分出 scope 子目录。

它没有让模型直接传入一个任意宿主路径去写文件；但它也没有走 `WorkspaceGuard.resolve(..., access="write")`。这里依赖的是专用 Store 的接口约束、内容处理和文件持久化机制。

例如语义冲突会返回 `memory_conflict` 和可选处理方式，让模型重新决定；不是把冲突默默当成功。

### 21.3 Skill：读取指南，不会自动执行指南里的命令

当前 SkillTool 只有 `list/read` 两种 action。

执行 `skill(action="read", name="某技能", resource="SKILL.md")` 时：

1. 经过公共工具入口和当前无风险项的审批入口。
2. 在启动时发现的技能目录表里查找 name。
3. 将 resource 与技能目录组合并解析真实路径。
4. 检查解析结果必须仍位于**该技能目录**内。
5. 检查必须是文件，且不超过 64 KiB。
6. 读取文本并返回。

这里的路径检查由 `ProceduralMemoryStore.read()` 自己完成，边界是单个已发现的技能目录，不是 Runtime 的统一 Guard。技能根目录来自源项目的 `.aster/skills`。

读到的指南如果要求跑命令，模型还必须再发起 `bash` 调用。那条新调用才会按 Bash 的风险规则审批，并进入 Runtime。

**读取技能并不等于授权执行技能中的所有操作。**

### 21.4 Goal：只能通过有限 action 修改会话目标状态

GoalTool 暴露 `status/checkpoint/waiting_for_user/failed`。

修改时先确认存在目标，再校验非空 summary，然后调用 GoalStore 对应方法。没有目标时，这个工具不会凭空创建一个目标。

GoalStore 的路径在初始化时固定为 `session_dir/goal.json`。它有状态解析、文本长度、文件大小、普通文件和符号链接检查，并使用锁和临时文件替换进行持久化。

这条路径也不经过通用 WorkspaceGuard。

### 21.5 Goal Complete：完成判定不等于风险审批

按顺序看就不会混淆：

1. 公共 Schema 校验检查 `final_result` 和逐项 `criteria_evidence`。
2. 公共 ApprovalGate 当前没有专门风险项，放行。
3. GoalStore 校验目标处于可完成的验证状态。
4. 有未完成验证时拒绝；必须有新的成功验证记录，并检查验收项和证据。
5. 如果启用了独立 GoalJudge，再把目标、证据和验证输出交给 Judge。
6. Judge 拒绝或普通调用错误时保持未完成，返回错误反馈；取消继续传播。
7. 通过后调用 Store 的完成操作，再校验并持久化 complete 状态。

独立 Judge 默认关闭；启用时默认等待上限 60 秒。

验证命令是此前单独执行的 `bash(..., goal_verification=true)`，那条 Bash 自己会经过风险审批和 Runtime。`goal_complete` 不会替你偷偷启动一次测试命令。

Judge 的模型请求使用 `tools=[]`，也不会通过 GoalJudge 绕到命令执行器。

### 21.6 为什么 `.aster` 受保护，专用工具还能修改它

因为两个入口的能力不同：

```text
通用 write：模型指定文件路径和任意内容 → 受 WorkspaceGuard 限制

专用 goal：模型指定有限 action → Store 修改它自己管理的 goal.json
```

保护 `.aster` 是阻止通用文件工具随意改内部状态；内部模块仍需要合法写入这些状态。

**当前 Snapshot 只切换基础编程工具的有效工作区。Memory/Skill 仍依据源项目初始化，Goal 仍在会话目录；它不是整个 Agent 内部状态的完整沙箱。**

这也意味着未来增加有副作用的 Memory action 时，要单独评估审批覆盖，不能因为“走了 ToolExecutor”就认为已经受到所有 Guard 和审批规则保护。

## 22. 什么超时：先分清你正在等谁

下表为当前实现的默认值，实际可由对应配置覆盖。

| 等待对象 | 默认时间 | 超时后怎样 | 会不会自动再来一次 |
| --- | --- | --- | --- |
| 模型连接、连接池或请求写入 | 10 秒 | 模型传输失败 | 满足条件时有限重试 |
| 模型首次有效输出 | 60 秒 | `MODEL_FIRST_TOKEN_TIMEOUT` | 尚未输出且可重试时才重试 |
| 模型流后续读取 | 30 秒 idle | `MODEL_IDLE_TIMEOUT` | 已输出则不自动重放 |
| 单次模型请求整体 | 120 秒 | `MODEL_TOTAL_TIMEOUT` | 同样受重试条件限制 |
| 风险审批 | 300 秒 | `APPROVAL_TIMEOUT`，拒绝执行 | 不自动批准或重发工具 |
| Bash 命令 | 默认 120 秒 | 清理进程或容器，返回超时错误 | 工具层不自动重跑 |
| ToolExecutor 工具执行等待 | 默认没有额外上限 | 配置后超时取消本次执行等待 | 不自动重跑 |
| 独立 GoalJudge | 启用时默认 60 秒 | 无法判定完成，失败关闭 | 由后续调用决定是否再申请 |
| MemoryFileLock 抢锁 | 3 秒 | 抛出锁等待 TimeoutError | 锁内部短暂轮询；超时不无限等待 |

模型的 120 秒是**单次尝试**的整体时间；重试、退避和切换备用路由可能让整个模型调用长于 120 秒。

模型 idle 计时由流读取逻辑实施，不能直接理解成“模型 30 秒没显示文字”。首次有效输出检查和后续读取检查的口径不同。

## 23. 什么重传：模型重试、工具重跑、Goal 续跑分别讲

### 23.1 模型请求重试

当前 OpenAI-compatible 传输层的顺序是：

```text
发起模型请求
    ↓
失败
    ↓
是否已经发出文本或工具调用事件？
    ├─ 是：不自动重放，也不继续自动切备用路由
    └─ 否：检查是否可重试和剩余次数
               ↓
           退避等待 → 再发模型请求
               ↓
           当前路由未成功 → 有备用路由时尝试备用路由
```

默认 `max_retries=2`，即单条路由最多首次加两次重试，共三次尝试。

可重试 HTTP 状态包括 408、409、425、429 和 5xx；网络错误和特定超时也会被标为可重试。参数解析错误、非 SSE 响应等不会在当前路由盲目重试；有备用路由且尚未输出时仍可能进入路由回退。

退避基础为 0.5 秒，按指数增长，考虑数字形式的 Retry-After，并带随机扰动；配置的退避上限是 8 秒。

等待退避时同样可以取消。它不是从网络字节断点接着传，而是重新发起一次模型请求。

### 23.2 为什么已输出就不自动重放

用户可能已经看到了部分文本。如果重新生成，内容可能重复、冲突，调用状态也更难对应。

工具参数会先累积并解析成完整调用，AgentLoop 才执行工具。不能把流中半截 JSON 当成命令直接运行。

断流最终失败时，传输层会给出错误事件和错误结束信息，可能保留部分文本；不会保证把半截回答自动补齐。

### 23.3 工具失败不自动重跑

ToolExecutor 没有通用的工具重试循环：一次调用失败，通常先返回 ToolResult，再由模型决定是否调整参数或重新调用。

例如测试失败后，正确链路是“看错误 → 修改代码 → 再发新的测试调用”。重新执行的那次还要重新走公共检查。

如果命令部分完成后才超时，自动重复执行可能重复外部副作用，因此不能把模型网络重试机制套用到工具上。

### 23.4 Goal 续跑不是重传

GoalSupervisor 管的是更外层的多次工作尝试：一次模型自然结束后，目标如果仍是 active/verifying 且继续条件满足，生成续跑提示并开始下一次尝试。

遇到错误、aborted、需要用户或终止状态时，它不会无条件继续。

默认 Goal 上限是 20 次尝试、4 小时、10 美元成本，限制在尝试边界等状态逻辑中核对；不是每次工具调用都启动一个四小时计时器，也不是精确到 token 的实时中断器。

## 24. 什么超过 Token：输入上下文和输出长度分开

### 24.1 输入上下文越来越长

当前主要由 WorkingMemory 的压缩机制处理，Runtime 不负责压缩模型上下文。

顺序是：

1. 工具大结果先按需制品化，默认阈值为 16 KiB，默认预览 4,000 字符。
2. `turn_finished` 时，CodingAssistant 使用模型报告的输入 token 数，或本地估算，判断压缩阈值。
3. 低于软阈值：不压缩。
4. 超过软阈值：尝试保留近期消息、归档旧消息并生成确定性检查点。
5. 软压缩如果需要模型摘要才能达到目标，会先延期。
6. 超过硬阈值：必要时调用模型生成摘要。
7. 成功后更新活动消息列表，下一轮使用压缩后的上下文。

参考大窗口配置是软阈值 80,000、硬阈值 100,000、目标 30,000、近期保留 20,000 token；实际默认值会根据模型窗口和输出预留缩小，不能说所有模型都固定用这一组数。

### 24.2 压缩失败怎么办

硬压缩的模型摘要路径对捕获到的 RuntimeError 有确定性检查点回退，并记录降级信息。

取消会停止压缩流程；其他未在压缩内部处理的异常会由 CodingAssistant 记录 `compaction.failed` 后继续抛出，不是任何故障都能无损继续。

压缩目标也是估算目标，不能承诺压缩后必定满足服务端窗口限制。

### 24.3 服务端已经拒绝“上下文太长”怎么办

当前看到的是按阈值主动压缩机制，没有专门实现“收到任何 context-length 错误 → 强制压缩 → 自动重发同一请求”的通用恢复链。

如果服务端以普通 HTTP 400 拒绝，会进入模型 HTTP 错误处理；400 本身不属于当前路由的可重试状态。有备用路由时可能回退，但不是自动修复上下文。

### 24.4 模型生成到 max_output_tokens

传输层把无工具调用时的 `finish_reason="length"` 转成 `stop_reason="length"`。

普通 AgentLoop 对“没有工具调用”的回答结束本次运行，因此不会仅因为 length 自动发一个“继续写”。如果处于 Goal 模式，是否再开始一次尝试由 GoalSupervisor 决定。

如果输出截断导致工具参数不是合法 JSON，传输层返回 `MODEL_TOOL_ARGUMENTS_INVALID`，不会把半截参数送进工具执行。

### 24.5 Token 上下文和 Goal 预算不相同

上下文 token 限制影响“这一轮能带多少信息”；输出 token 限制影响“这一轮最多生成多少内容”；Goal 的成本、时间、次数限制影响“整个目标还能推进多久”。

当前 GoalConfig 没有一个统一的累计 token_budget 字段，不应描述成超过累计 token 后 Runtime 自动取消所有工具。

## 25. 什么文件过大、什么等待：按具体工具处理

### 25.1 Read 读取很大的文本文件

当前实现是**先读取整个文件 bytes，再解码、分行和裁剪返回内容**。

返回最多开头 2,000 行或 50 KiB，并给出下一次 offset。模型想继续看时，需要发新的 Read 调用。

```text
read(path="large.log")
    ↓
返回前面一段和 next offset 提示
    ↓
模型决定还需要后面的内容
    ↓
read(path="large.log", offset=提示值, limit=需要的行数)
```

因此 offset/limit 当前主要限制展示，不是底层按需流式读取；超大文件仍可能带来内存和读取时间问题。

如果单行就超过 50 KiB，会返回说明，建议通过 Bash 查看有限字节范围。它不会一直等这行“变小”。

### 25.2 Skill 资源太大

`ProceduralMemoryStore.read()` 先检查大小，超过 64 KiB 就抛出 `Skill resource is too large`，再被工具入口转成错误结果。没有自动分块等待或后台下载。

这是 read 方法的资源限制；初始化发现技能时读取 SKILL.md 的逻辑不应被误说成同样已经做了这一检查。

### 25.3 Goal 状态文件太大

GoalStore 读取 `goal.json` 时，超过 128 KiB 会拒绝并给出 `GOAL_TOO_LARGE`。

如果发生在工具调用中，可以变成错误 ToolResult；如果发生在外围 Goal 初始化或监督循环读取中，则由外围错误路径处理。不能把所有错误都说成一定经过 ToolExecutor。

### 25.4 Snapshot 工作区太大

首次准备副本时超过文件数或总大小限制，立即报 `SNAPSHOT_FILE_LIMIT` 或 `SNAPSHOT_SIZE_LIMIT`，不会继续无上限复制。

这个错误发生在 Runtime 准备阶段，可能连工具循环都还没有开始。

### 25.5 Write 内容太大

当前 Write Schema 没有给 content 配置专门的最大长度；写入时会编码内容并写临时文件。

没有统一的“大文件写入等待后自动分块”机制。ToolExecutor 的 100,000 字符限制针对返回内容，不是输入文件大小限制。

### 25.6 实际有哪些等待

| 等待情况 | 在等什么 | 当前处理 |
| --- | --- | --- |
| Write/Edit 同路径排队 | 前一个文件修改释放 asyncio.Lock | 队列自身没有固定等待时限，受外围取消/可选工具超时影响 |
| Memory/Goal 文件锁 | 其他写入者释放锁文件 | 默认 3 秒上限，50 毫秒轮询；按年龄处理过期锁 |
| 审批等待 | 用户的明确决定 | 默认 300 秒，到时拒绝 |
| 命令等待 | 进程退出或容器结束 | 按命令 timeout 处理 |
| 模型退避等待 | 网络重试前延迟 | 短暂退避，可以取消 |
| Goal waiting_for_user | 目标需要用户提供决定 | 持久化为等待状态，外层停止续跑 |
| Goal 验证未结束 | 完成申请发现 pending 验证 | 返回 `GOAL_VERIFICATION_RUNNING`，不是在完成工具里无限挂起 |

MemoryFileLock 使用同步短睡眠，并非完整异步可取消锁；如果在事件循环线程直接等待，可能推迟其他协程和取消响应。这与文件工具的 asyncio.Lock 队列不同。

## 26. 工具失败后究竟怎么继续：最后用几条流程串起来

### 26.1 参数合法，但测试失败

```text
Bash 真实执行 → exit_code 非零 → 错误 ToolResult
    ↓
结果写入工具消息
    ↓
模型看测试错误，决定修代码或换验证方法
    ↓
需要重试时发起新的工具调用
```

普通工具错误不必然结束整个 AgentLoop。同一轮后面还有工具调用时，当前循环通常也会继续处理它们；模型到下一次生成时才综合这些结果。

### 26.2 工具参数缺失或文件找不到

```text
Schema 校验失败或工具抛普通异常
    ↓
ToolExecutor 转为错误 ToolResult
    ↓
模型根据具体错误修正参数
```

Memory/Skill 某些 action 所需字段在 Schema 中不是条件必填，缺失时可能在工具内部成为 KeyError，再被统一错误处理捕获；并非所有业务参数问题都一定在 Schema 层发现。

### 26.3 审批被拒绝

```text
ApprovalGate 返回拒绝结果
    ↓
不执行工具主体
    ↓
模型看到拒绝原因，调整方案或等待用户
```

没有自动换工具绕过拒绝的授权。底层也没有“拒绝三次就允许”的逻辑。

### 26.4 工具执行超时

```text
达到命令或已配置的工具超时
    ↓
尝试终止运行资源
    ↓
超时错误返回模型
    ↓
先判断有没有部分副作用，再决定是否重新执行
```

比如下载完成、后续测试超时，不代表下载没有发生。

### 26.5 用户取消

```text
取消令牌触发
    ↓
支持取消的等待和工具观察到信号
    ↓
清理当前命令资源，取消向上传播
    ↓
AgentLoop 报告当前及剩余调用的取消状态，结束本次运行
```

它与普通错误不同：模型不会在同一次被取消运行里继续自行重试。Memory/Skill 等同步业务方法中没有完整协作取消检查，已经开始的短同步操作可能执行完才让出控制权。

### 26.6 模型请求最终失败

这是模型传输层错误，不是某个 ToolResult。传输层报告 error 和完成信息，上层按模型结束状态收尾；GoalSupervisor 遇到 error 不会盲目续跑。

### 26.7 一直失败、一直修怎么办

工具层没有自动诊断修复器，主要靠下一轮模型根据错误调整。AgentLoop 有 `max_turns`，超过会产生运行错误；GoalSupervisor 另外有次数、时间和成本上限。

所以完整回答是：**局部工具失败通常反馈给模型继续决策；取消停止当前运行；模型传输故障有限重试；上下文增长交给 Memory 压缩；Goal 外层决定是否继续下一次尝试。**

补充实现入口：[Memory/Skill 工具](D:/MIniClaw/src/MiniClaw/coding_agent/memory/tools.py)、[技能资源边界](D:/MIniClaw/src/MiniClaw/coding_agent/memory/procedural.py)、[Goal 工具](D:/MIniClaw/src/MiniClaw/coding_agent/goal/tools.py)、[Goal 存储](D:/MIniClaw/src/MiniClaw/coding_agent/goal/store.py)、[模型传输](D:/MIniClaw/src/MiniClaw/llm/openai_compatible.py)、[工作上下文压缩](D:/MIniClaw/src/MiniClaw/coding_agent/memory/working.py)、[文件锁](D:/MIniClaw/src/MiniClaw/coding_agent/memory/locking.py)。

## 27. 完整典型例子：修复分页计算，从用户输入一直走到交付

### 27.1 先明确例子的条件

用户说：

> 修复 `paging.py` 的分页计算。总数 7、每页 3 条应该是 3 页；总数 6 应该是 2 页；总数 0 应该是 0 页。运行现有 `check.py` 验证，只修改 `paging.py`。

设初始工作区里有两个文件。`paging.py` 当前错误实现是：

```python
def page_count(total, size):
    return total // size
```

`check.py` 是已有检查脚本：

```python
from paging import page_count

assert page_count(7, 3) == 3
assert page_count(6, 3) == 2
assert page_count(0, 3) == 0
print("CHECK_OK")
```

本例限定 `total >= 0`、`size > 0`，不把未定义的负数、零页大小行为混进讲解。

为方便看主线，设当前使用 Docker + Direct，容器已具备 Python；内置工具未禁用，没有角色限制，项目指令和审批也允许本例的读文件、目标修改与测试命令。

**这些是案例前提，不表示每条命令永远免审批，也不表示本次文档编辑真的运行了这些命令。**

### 27.2 第一步：程序组装环境，模型还没有执行任何操作

`CodingAssistant` 先准备 Runtime、工作区、指令、审批、记忆和工具对象。

随后把 `read`、`edit`、`bash` 等工具注册到 ToolManager。默认不限定角色，因此这些工具能出现在给模型的工具定义里。

此时的状态：

| 对象 | 内容或状态 |
| --- | --- |
| 工作区文件 | 原始错误代码，没有变化 |
| 工具注册表 | 工具名对应实际 Python 工具对象 |
| 模型上下文 | 用户要求、系统说明、相关指令与可用工具定义等 |
| 工具运行 | 尚未发生 |

模型看到工具定义，只是知道“可以申请这些操作”，还没有因此读到 `paging.py` 的内容。

### 27.3 第二步：模型决定先读代码

以下用统一的教学格式表示工具请求：外层 `name` 指明工具，`arguments` 是工具参数。它用于解释含义，不规定 provider 的原始通信 JSON 必须长这样。

```json
{
  "call_id": "call_read_1",
  "name": "read",
  "arguments": {"path": "paging.py"}
}
```

先发生的是“模型提出请求”，接下来才轮到执行系统。

### 27.4 第三步：ToolManager 放行，再进入 ToolExecutor

按当前案例配置：

```text
read 已注册、未禁用、没有角色限制
    ↓
ToolManager.execute() 调用父类
    ↓
ToolExecutor 查到 ReadTool 对象
    ↓
检查取消状态，复制参数
    ↓
校验 path 是字符串、必填项存在
    ↓
项目指令 preflight
    ↓
ApprovalGate：当前 read 没有专门风险项，放行
    ↓
ReadTool.execute()
```

这里没有“某个角色不能 read”的实际分支，也没有为例子凭空加一个 reviewer 限制。

### 27.5 第四步：ReadTool 在宿主检查路径并读取

ReadTool 调用共享 Guard 的 `resolve(..., access="read", must_exist=True)`。

Guard 将相对路径映射到有效工作区，检查没有越界、目标不是被保护的读取对象，工具再确认它是文件。

随后读取并解码内容，返回 `ToolResult`。**这一步不启动 Docker 容器来读文件。**

对于这个很短的文件，结果内容就是错误函数文本；结果转换器依次处理，未达到大结果阈值就不需要 artifact 化。

### 27.6 第五步：结果成为工具消息，模型才真正看到代码

AgentLoop 把返回内容关联到 `call_read_1`，形成工具消息，并加入后续模型请求的上下文。

可以把上下文变化理解为：

```text
用户：修复分页计算并验证。
助手工具请求：read paging.py，ID=call_read_1。
工具结果：该 ID 对应文件里是 return total // size。
```

`call_id` 用于把结果对应到请求，避免多个调用的结果混淆。

结果对象里的 `details` 可供运行记录与其他内部逻辑使用；不能假设其中每个字段都自动原样进入模型可见文本。

### 27.7 第六步：先运行已有检查，得到真实失败

模型请求：

```json
{
  "call_id": "call_bash_1",
  "name": "bash",
  "arguments": {"command": "python check.py", "timeout": 30}
}
```

Manager、Executor、项目指令检查和审批依次运行。允许后进入 BashTool。

此时才走命令后端分支：

```text
BashTool
 → ToolRuntime.run()
 → 确定有效超时与工作目录
 → DockerCommandExecutor
 → 在容器映射工作区运行 python check.py
```

Docker + Direct 下，容器读取的是映射进来的当前工作区文件。因此它看到的仍是尚未修复的代码。

测试第一条断言失败，命令以非零退出码结束。BashTool 将输出与退出码转成 `ToolResult(is_error=True)`。

### 27.8 第七步：失败反馈给模型，不是工具层自动重跑

模型看到类似：

```text
AssertionError
Command exited with code 1
```

在没有取消或其他终止条件时，AgentLoop 可以进入下一轮模型决策。

**ToolExecutor 不会自行推断正确公式，也不会自动再跑一次同样命令。**下一步修什么、调用哪个工具，是模型根据代码和失败证据作出的决定。

### 27.9 第八步：模型申请局部修改

模型发起：

```json
{
  "call_id": "call_edit_1",
  "name": "edit",
  "arguments": {
    "path": "paging.py",
    "edits": [
      {
        "oldText": "    return total // size",
        "newText": "    return (total + size - 1) // size"
      }
    ]
  }
}
```

这次仍从 Manager 与 Executor 入口开始，不会因为前一次 Bash 已获准，就跳过当前 Edit 的检查。

公共检查放行后，EditTool 依次：

1. 用 Guard 校验 `paging.py` 的写权限和存在性。
2. 进入该文件的修改队列，避免同一路径修改互相穿插。
3. 读取文件并检查旧文本匹配。
4. 在内存构造完整新内容，保留 BOM 和换行风格。
5. 通过临时文件和原子替换提交，提交过程包含取消检查。
6. 返回成功信息和 diff 等详情。

这一步也在宿主文件工具里执行，不需要 Bash 来改文件。

### 27.10 第九步：再次 Bash 验证，容器能看见新代码

模型再次调用 `bash` 执行 `python check.py`。

由于 Direct 模式使用共享映射的有效工作区，前面宿主 Edit 提交的新文件能被后续容器命令读取。

假设本例检查通过，命令返回：

```text
CHECK_OK
```

退出码为 0，BashTool 返回非错误结果，AgentLoop 将其加入模型上下文。

**不是把第一次失败结果改成成功。**两次 Bash 调用各自有自己的请求、输出、退出状态和 trace。

### 27.11 第十步：给出最终交付说明

模型现在有三类证据：

- 原始代码与需求。
- 实际 Edit 成功结果。
- 修改之后的测试成功结果。

它可以据此回答“已修复向上取整，三个用例通过”。

这是普通任务的结束示例，**没有活动 Goal 时，不需要为了回答完成而调用 `goal_complete`**。

### 27.12 把文件、上下文和运行状态放在同一张表里看

| 时刻 | 代码文件 | 模型已获得的信息 | 执行状态 |
| --- | --- | --- | --- |
| 用户刚提要求 | 错误版本 | 要求与约束 | 尚未调用工具 |
| Read 后 | 未改变 | 看到错误实现 | Read 成功 |
| 第一次 Bash 后 | 未改变 | 看到断言失败 | 该命令失败，运行可继续 |
| Edit 后 | 修复版本 | 收到修改成功反馈 | 文件修改已提交 |
| 第二次 Bash 后 | 修复版本 | 看到 CHECK_OK | 新的命令成功 |
| 最终回答 | 修复版本 | 汇总实际证据 | 当前任务收尾 |

核心顺序是：**请求 → 检查 → 执行 → 结果 → 下一轮决策**。不是模型一句话直接操纵宿主文件，也不是 Runtime 自己负责规划修复。

## 28. 典型例子二：参数写错、旧文本匹配失败，分别在哪里停？

### 28.1 Bash 请求缺少 command

模型错误提交：

```json
{"name": "bash", "arguments": {"timeout": 30}}
```

过程是：

```text
Manager 可用性检查通过
 → Executor Schema 校验发现缺少 command
 → 转成 is_error=True 的 ToolResult
 → 不运行项目指令/审批 preflight
 → 不进入 BashTool，不启动容器
 → 模型下一轮看到参数错误
```

如果模型随后补上 `command`，那是一次新的工具调用，不是 Executor 自动修复参数。

### 28.2 Edit 的格式合法，但 oldText 不存在

假设其他操作已把文件改过，模型仍用旧文本去替换。

这次参数校验可以通过，前置检查也可能通过，但 EditTool 读取实际文件后无法唯一匹配旧文本，因此返回错误。

当前文件不会因为匹配失败就随便替换某一段。模型通常应该重新 Read，确认当前内容，再重新构造 Edit。

两种错误发生位置不同：

| 情况 | 失败位置 | 已经执行实际修改吗？ |
| --- | --- | --- |
| 缺少 command | 公共参数校验 | 没有 |
| oldText 不匹配 | Edit 的业务校验 | 没有提交本次修改 |

不能把所有 `is_error=True` 都理解成“容器启动后失败”。

## 29. 典型例子三：Bash 已开放，但审批拒绝了这一次操作

### 29.1 前提：工具开放，当前命令命中审批规则

设模型提出一个被当前风险分类器识别为高风险的 Bash 命令，策略为 ask，审批处理器最终返回拒绝。

这里使用“命中规则的命令”这个前提，是为了避免误以为所有 Bash 调用都会弹审批。

### 29.2 按顺序走

1. Bash 已注册、未禁用，Manager 放行。
2. Executor 参数校验通过。
3. 项目指令检查没有先阻断。
4. ApprovalGate 识别风险，进入等待。
5. 审批被拒绝，preflight 返回阻断结果。
6. Executor 不调用 BashTool。
7. 阻断结果经过结果处理，回到上层与模型。

关键点是：**这一条调用尚未启动实际命令，不能描述为“先执行，然后审批把它撤销”。**

### 29.3 模型之后应该怎样做？

如果运行未被取消，模型可以解释受阻原因、继续允许的工作，或在有合法替代方案时调整计划。

它不能把命令改个写法、换个工具来绕过已经拒绝的操作。

### 29.4 为什么所有角色都能 Bash，还需要这一层？

因为它们检查的是不同问题：

```text
工具开放：这个角色是否可以提出 Bash 调用？
风险审批：这一次具体 Bash 操作是否获准？
```

当前默认没有角色限制，不影响具体调用仍经过审批入口。

## 30. 典型例子四：审批允许，也不能越过文件边界

### 30.1 模型请求写工作区外文件

假设有效工作区为某个项目目录，模型请求：

```json
{
  "name": "write",
  "arguments": {
    "path": "../outside.txt",
    "content": "should not be written"
  }
}
```

项目指令或审批可能更早阻止它；为了单独理解 Guard，这里假设前置检查没有阻断，进入了 WriteTool。

### 30.2 Guard 如何处理？

WriteTool 解析真实目标路径，发现 `../outside.txt` 逃出了有效工作区。

路径检查失败，Executor 将普通异常转成工具错误；本次不会继续创建临时文件和原子提交。

审批“允许尝试该操作”不会修改 Guard 的根目录，也不会把工作区外路径变成合法路径。

### 30.3 位于项目内，也可能被保护

例如通用 Write 请求修改 `.aster` 中的目标状态文件，仍可能被保护规则拒绝。

而 `goal(action="checkpoint", ...)` 能通过专用 Store 修改固定状态，这不是前后矛盾：

```text
write：允许模型指定文件与任意内容，必须限制通用写入边界。
goal：只暴露有限状态操作，由 Store 管理固定目标文件。
```

这个例子也说明：“都继承同一个 Executor”不意味着所有工具内部都调用同一个 Guard。具体工具的路径检查仍要分别看实现。

## 31. 典型例子五：同一个长命令，超时与取消的结局不同

### 31.1 超时：命令超过自己的等待上限

设 `slow_check.py` 需要很久，模型提交：

```json
{
  "name": "bash",
  "arguments": {"command": "python slow_check.py", "timeout": 2}
}
```

命令经检查启动，但超过有效超时尚未完成。相应执行后端处理停止与资源清理，返回失败信息，或者由上层把超时异常转换成错误结果。

模型收到超时后，可以重新分析：测试是否卡住、是否应该缩小测试范围、是否需要合理调整超时。

**不会自动无限延长，也不保证无条件重新执行。**

### 31.2 用户取消：停止当前运行

换一种情况：命令 timeout 是 120 秒，但用户在第 2 秒点击停止。

```text
取消令牌触发
 → 当前等待/命令观察到取消
 → 尝试停止执行并清理资源
 → ToolCancelledError 等取消信号向上传播
 → AgentLoop 收尾，结束当前运行
```

这次不会把取消简单当成普通工具失败，然后在同一次运行里让模型继续修复并重试。

### 31.3 两种情况都不能承诺回滚

假设命令先写出了 `partial.json`，随后开始耗时分析，在分析期间超时或取消。

已经完成的写入可能保留。停止进程不等于文件系统事务回滚。

后续重新启动任务时，应先检查现状，避免盲目重跑导致重复副作用。

### 31.4 Write 的原子提交与“所有操作可回滚”也不同

Write/Edit 在提交前检查取消，通常可以避免提交半截文件。

但如果替换已经完成后才收到取消，新文件不会自动恢复成旧版本。原子替换解决单次文件提交完整性，不提供整场任务撤销。

## 32. 典型例子六：测试输出 300 KB 日志，怎么回到上下文？

### 32.1 先设定输出与配置

假设 Bash 执行成功，后端交给 BashTool 的输出约 300 KB。设 progressive artifact 化启用，阈值为 16 KiB，预览长度为 4000 字符。

这里的 300 KB 是教学数值，讨论的是进入 BashTool 之后的处理，不承诺任何后端都会无限量收集完整输出。

### 32.2 第一层：BashTool 保留尾部，另存当前拿到的完整输出

BashTool 按最多 2000 行或 50 KB 的规则截取尾部。

因为发生截断，它把当前收到的完整命令输出写到：

```text
.aster/tool-output/bash-<随机标识>.log
```

返回文本包括尾部日志与“完整输出在哪里”的提示，详情中有 `fullOutputPath`。

这一步形成两个东西：

- 磁盘上的完整输出文件。
- 准备返回给模型的尾部文本与路径提示。

### 32.3 第二层：WorkingContext 再把较大的工具结果变成 artifact 引用

上述返回文本如果仍超过配置阈值，`artifactize_live_result()` 将这个 **ToolResult 的 content** 保存为 context artifact，用引用和预览替换原文本。

注意保存对象不同：

| 对象 | 保存的是什么？ |
| --- | --- |
| `tool-output` 日志 | BashTool 收到并另存的完整命令输出 |
| context artifact | BashTool 整理之后的返回文本，可能已经是尾部加提示 |

因此，读取 context artifact 不能保证直接拿到原始 300 KB。它可能只帮你恢复尾部文本和完整输出路径。

### 32.4 第三层：Executor 的最终文本上限

结果转换后，Executor 还检查最终 `content` 长度，默认上限 100000 字符。

这是字符限制；前面的 artifact 阈值按 UTF-8 字节判断，Bash 截断还涉及行数。三者不是同一把尺子。

### 32.5 第四层：模型看见引用，按需要发起新的 Read

如果关键错误在日志开头，模型不能仅凭尾部就断言“没有错误”。它应使用返回的真实路径读取所需片段，例如：

```json
{
  "name": "read",
  "arguments": {
    "path": ".aster/tool-output/bash-<实际返回的标识>.log",
    "offset": 1,
    "limit": 100
  }
}
```

路径中的标识必须取自实际工具结果，不能照抄占位符。

这条 Read 是新的工具调用，要重新经过公共入口和路径检查。当前 Guard 对 `.aster/tool-output` 和 `.aster/context-artifacts` 有专门的读取例外，但并不因此允许任意修改内部文件。

### 32.6 上下文最后变成什么？

```text
最初：用户要求诊断测试失败。
第一次 Bash 后：日志 artifact 引用、预览、恢复线索。
追加 Read 后：与故障相关的具体日志片段。
下一次模型决策：基于片段判断需要修什么。
```

它没有必要把所有日志一次性塞回上下文，也不能因为“已经保存到磁盘”就宣称模型已经看过全部内容。

另外，当前 Read 对文本仍会先读取文件再做行选择。`offset/limit` 控制返回范围，不应描述为底层流式只读取那几行。

## 33. 典型例子七：Memory 记事实、Skill 读步骤，为什么不进 Docker？

### 33.1 用户明确要求保存一个项目事实

用户说：

> 记住 Cedar 开发端口是 8318，后续这个项目都按它来。

模型可以提出：

```json
{
  "name": "memory",
  "arguments": {
    "action": "remember",
    "category": "project",
    "content": "Cedar 开发端口为 8318。"
  }
}
```

按顺序：Manager 可用性 → Executor 参数校验 → 前置检查 → MemoryTool 分发 → SemanticMemoryStore 处理 → 返回实际保存或冲突结果。

当前 memory 没有专门的审批风险分类，因此审批入口放行；真正存储由专用 Store 负责，不使用 Bash，也不经过 Runtime 的通用 WorkspaceGuard 写文件。

如果检测到语义冲突，应根据冲突结果处理，不能提前回答“已成功记住”。记忆存储的作用域由初始化配置决定，不能从这个调用推导成所有用户共享。

### 33.2 后续用户要求按某个技能处理

假设项目已经发现一个名为 `release-check` 的技能。模型请求：

```json
{
  "name": "skill",
  "arguments": {
    "action": "read",
    "name": "release-check",
    "resource": "SKILL.md"
  }
}
```

SkillTool 通过专用 ProceduralMemoryStore 查找已发现技能，并检查资源解析后仍在技能目录内、是文件且大小允许，然后返回文本。

它不是在任意路径搜索并执行一个同名程序。

### 33.3 技能文本提到测试命令，接下来仍是一次新调用

假设指南写着“运行 `python check.py`”。读到指南只增加了模型掌握的信息。

模型决定执行时，仍要另发 `bash` 请求；该请求重新经过风险审批与 Runtime。

```text
skill.read：把步骤读出来。
bash：按获得的信息申请执行一个具体命令。
```

技能内容不能凭“自己是技能”跳过工具边界。

## 34. 典型例子八：已有 Goal，怎样从修复走到 goal_complete？

### 34.1 前提：目标已经创建

设会话中已有活动 Goal，验收项是：

> 分页计算覆盖 7/3、6/3、0/3 三个用例，并通过 check.py。

本例从已有目标开始。`goal` 工具的 `checkpoint` 不是创建目标的入口；普通任务也不自动等于 Goal 模式。

### 34.2 完成代码修改后，可以记录阶段进度

```json
{
  "name": "goal",
  "arguments": {
    "action": "checkpoint",
    "summary": "已修改 paging.py，接下来运行最终验证。"
  }
}
```

调用经公共入口后由 GoalStore 校验状态并持久化。checkpoint 表示进度，不代表目标已完成，也不替代测试证据。

### 34.3 发起带验证标记的真实 Bash 调用

```json
{
  "name": "bash",
  "arguments": {
    "command": "python check.py",
    "timeout": 30,
    "goal_verification": true
  }
}
```

这条命令仍走 Bash 自己的指令检查、审批和 Runtime。

执行结果经 Goal 相关结果处理记录为验证证据。假设真实命令返回退出码 0 和 `CHECK_OK`，且目标状态等条件都满足，才有资格继续申请完成。

`goal_verification=true` 是标记，不是魔法：错误命令、失败结果不能因为加了标记就变成成功验证。

### 34.4 模型提交完成申请与逐项证据

```json
{
  "name": "goal_complete",
  "arguments": {
    "final_result": "已修复分页计算，check.py 的三个用例通过。",
    "criteria_evidence": [
      {
        "criterion": "分页计算覆盖 7/3、6/3、0/3 三个用例，并通过 check.py。",
        "evidence": "修改 paging.py 后执行 python check.py，退出码为 0，输出 CHECK_OK。"
      }
    ]
  }
}
```

`criterion` 应逐字对应实际目标的验收项。本例的文本只是案例中事先设定的验收项，不适用于任意目标。

### 34.5 按顺序检查，不能跳步

1. Executor 校验参数结构。
2. 当前审批分类对 goal_complete 没有专门风险项，放行。
3. Store 检查目标状态、未完成验证、最新成功验证和逐项证据。
4. 若配置了 GoalJudge，额外进行独立判断。
5. 满足条件后写入 complete 状态。
6. 返回完成结果，交给上层继续收尾。

当前 Judge 默认关闭，不应把这一步讲成每次必有另一个模型审批。

### 34.6 直接说“完成了”为什么不行？

如果没有合格的新验证记录就直接调用 goal_complete，Store 的业务校验会拒绝完成。

即便公共 ApprovalGate 放行，业务校验仍可能失败；两者分别检查“调用风险”和“目标是否满足完成条件”。

如果启用的 Judge 拒绝，目标也不会因此被标为 complete。模型应依据反馈补充实际工作或验证，不能伪造证据。

最后，即使内部状态 complete，外部 Eval 仍可以用独立测试发现遗漏。内部目标完成机制与独立验收不是同一个裁判。

## 35. 典型例子对照表：看到现象，先判断停在哪一层

| 现象 | 主要位置 | 是否进入实际工具/命令 | 后续通常怎样处理 |
| --- | --- | --- | --- |
| 请求了未注册工具 | Executor 查表 | 否 | 根据可用定义重新选择 |
| 工具被显式禁用 | Manager 可用性 | 否 | 尊重禁用，处理允许的工作 |
| Bash 缺 command | Executor Schema | 否 | 模型修正参数后新调用 |
| 审批拒绝 | ApprovalGate preflight | 本次不进入工具执行 | 返回受阻原因，不绕过 |
| Write 路径越界 | 文件路径 Guard，可能更早被阻断 | 未提交文件写入 | 使用授权范围内的合法目标 |
| Edit 旧文本不匹配 | Edit 业务检查 | 未提交本次修改 | 重新读取当前内容 |
| 测试断言失败 | 实际 Bash 命令 | 是 | 模型分析、修复、再验证 |
| 命令超时 | Runtime/执行等待 | 通常已经启动 | 检查部分副作用，再决定下一步 |
| 用户取消 | 取消控制链 | 取决于收到信号的时刻 | 当前运行收尾，不继续自动重试 |
| 工具结果过大 | Bash/Read 与结果转换层 | 通常已得到结果 | 引用、预览、按需追加读取 |
| Memory 事实冲突 | 记忆 Store | 已进入业务处理 | 根据冲突结果明确处理 |
| Skill 资源路径逃逸 | ProceduralMemoryStore | 不返回越界文件 | 使用技能目录内合法资源 |
| goal_complete 无有效验证 | GoalStore | 未提交完成状态 | 补充真实验证后再申请 |

阅读这些例子时始终区分：**模型负责下一步决策；Manager 管可选可用性；Executor 管公共执行管线；具体工具处理自己的语义；Runtime 负责命令运行环境；结果再回到模型。**
