```mermaid
flowchart TD
    A[用户任务进入 CodingAssistant]
    A --> B[组装固定工具集]
    B --> C[ToolExecutor 建立 _tool_map]
    C --> D[向模型发送本轮工具定义]

    D --> E[模型发起 Tool Call]
    E --> F[ToolExecutor 接收调用]
    F --> G{工具存在?}

    G -- 否 --> ER[生成错误 ToolResult]
    G -- 是 --> H{本轮已声明该工具?}

    H -- 否 --> ER
    H -- 是 --> I[prepare_arguments]
    I --> J[Schema 参数校验]
    J --> K{参数通过?}

    K -- 否 --> ER
    K -- 是 --> L[项目指令 preflight]

    L --> L1{是否包含目标路径?}
    L1 -- 是 --> L2[按路径解析并激活 agent.md]
    L1 -- 否 --> M[进入审批]
    L2 --> L3{写入类发现新指令?}

    L3 -- 是 --> ERI[PROJECT_INSTRUCTIONS_REFRESH_REQUIRED]
    L3 -- 否 --> M[进入审批]

    M --> N[ApprovalGate 风险分类]
    N --> O{发现风险?}

    O -- 否 --> P[继续]
    O -- 是 --> Q[匹配 allowlist / policy]
    Q --> Q1{策略允许?}

    Q1 -- 是 --> P
    Q1 -- 否 --> R{是否需要人工审批?}

    R -- 否 --> ER
    R -- 是 --> S[ask：等待人工审批]
    S --> T{人工批准?}

    T -- 否/超时 --> ER
    T -- 是 --> P

    P --> U{重复只读结果命中缓存?}
    U -- 是 --> V[返回短缓存提示]
    U -- 否 --> W[开始执行]

    V --> Z[统一结果收敛]
    W --> X{工具类型}

    X --> X1[read / write / edit]
    X1 --> Y1[WorkspaceGuard]
    Y1 --> Y2[路径与访问权限检查]
    Y2 --> Z1[文件操作]

    X --> X2[grep / search / ls / find]
    X2 --> Y3[WorkspaceGuard]
    Y3 --> Y4[搜索范围与受保护路径检查]
    Y4 --> Z2[执行搜索或路径枚举]

    X --> X3[bash]
    X3 --> Y5[ToolRuntime.run]
    Y5 --> Y6[校验 command / cwd / timeout]
    Y6 --> Y7[WorkspaceGuard access=execute]
    Y7 --> Y8{执行后端}

    Y8 -- Docker --> Y9[DockerCommandExecutor]
    Y9 --> Y10[临时容器 / workspace / stdout / stderr]

    Y8 -- Host --> Y11[HostCommandExecutor]
    Y11 --> Y12[主机进程组执行]

    X --> X4[memory / goal]
    X4 --> Z3[各自服务边界执行]

    Z1 --> R0[原始 ToolResult]
    Z2 --> R0
    Y10 --> R0
    Y12 --> R0
    Z3 --> R0

    R0 --> R1{网络错误且调用幂等?}
    R1 -- 是 --> R2[有限指数退避]
    R2 --> W

    R1 -- 否 --> R3[执行成功 / 失败 / 超时 / 取消]
    R3 --> Z

    Z --> Z4[错误标准化]
    Z4 --> Z5[result transforms]
    Z5 --> Z6{正常大结果?}

    Z6 -- 是 --> Z7[≥8KiB：完整结果落盘 Artifact]
    Z7 --> Z8[只返回短摘要、路径、大小、hash]

    Z6 -- 否 --> Z9[保留正常结果内容]
    Z8 --> AA[记录 Trace]
    Z9 --> AA

    AA --> AB[生成 delivered ToolResult]
    AB --> AC[role=tool 回传 AgentLoop]
    AC --> AD[模型下一轮决定继续、修复或结束]

    ER --> AE[保留原始错误内容]
    ERI --> AE
    AE --> AA

    classDef main fill:#123047,stroke:#42c8df,color:#eef7ff;
    classDef decision fill:#3a2a16,stroke:#f3ac52,color:#fff0d0;
    classDef security fill:#12362d,stroke:#63d69c,color:#effff5;
    classDef exec fill:#302248,stroke:#ae91ff,color:#f7f0ff;
    classDef error fill:#421f2b,stroke:#ff737d,color:#fff0f2;
    classDef result fill:#17352d,stroke:#70d6a2,color:#effff5;

    class A,B,C,D,E,F,I,J,L,L2,M,N,P,W,R0,Z,Z4,Z5,AA,AB,AC,AD main;
    class G,H,K,L1,L3,O,Q1,R,T,U,X,Y8,R1,Z6 decision;
    class Y1,Y2,Y3,Y4,Y5,Y6,Y7 security;
    class X1,X2,X3,X4,Z1,Z2,Z3,Y9,Y10,Y11,Y12 exec;
    class ER,ERI,AE error;
    class V,Z7,Z8,Z9,R3 result;
```

## 工具职责与边界

| 工具 | 作用 | 关键边界 |
|---|---|---|
| `read` | 读取文件内容，支持 `offset` / `limit` 分段 | 受 WorkspaceGuard 和 128KiB 模型可见上限约束；读取 artifact 不会二次落盘 |
| `bash` | 执行受控命令 | 经过 timeout、工作目录和执行后端检查；Docker/Host 由 Runtime 配置决定 |
| `write` / `edit` | 创建、覆盖或按唯一文本替换文件 | 写入路径受 WorkspaceGuard 保护，结果进入统一 ToolExecutor 生命周期 |
| `grep` | 搜索文件内容 | 只读搜索，支持正则、glob、大小写、上下文和结果上限 |
| `search` | 按 glob 查找工作区路径 | 自动排除受保护路径，可选择是否包含目录，最多返回 10,000 项 |
| `ls` | 列出单个目录的直接内容 | 只返回路径和目录标记，不读取文件；最多返回 500 项 |
| `find` | 按文件名模式递归查找路径 | 可限定 file/directory/any；只返回路径，不读取文件，最多返回 500 项 |
| `memory` | 检索或维护 semantic memory | 只允许用户明确确认的长期偏好/事实进入 semantic；episodic 由程序维护 |
| `goal` | 查看或更新明确 Goal 状态 | 只在存在长期 Goal 时使用，不承担普通任务验收 |

`ls` 与 `find` 都属于只读路径发现工具，和 `search` 的区别是：`ls` 只看当前目录，`find` 按文件名模式递归查找；三者都不会读取文件内容，后续需要内容时必须显式调用 `read`。
![alt text](miniclaw-tool-runtime-gpt-image-2-complex-v4.png)
