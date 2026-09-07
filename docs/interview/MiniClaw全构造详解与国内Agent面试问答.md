# MiniClaw 全构造详解：从一条消息到完整 Agent 系统，再到国内面试

> 源码核对日期：2026-09-05。阅读对象：准备国内大模型应用开发、Agent 开发、AI 应用后端、相关实习岗位的同学。本文以当前 `src/MiniClaw` 实现为准，旧笔记用于辅助定位。
>
> 本文的示例轨迹、公式演算和口述模板用于教学；除明确列出本次核验结果或标明历史报告来源的内容外，不代表本次真实调用模型、部署上线或取得某个评测成绩。公开面经属于作者自述，技术追问属于结合项目整理的练习，不把推导题冒充某家公司原题。

<a id="chapter-01"></a>

## 怎么读这份文档，才能真正讲懂项目

你需要掌握的不是“项目用了很多模块”，而是四件事：一个请求怎样一步步变成真实动作；系统怎样知道动作做成了；任务很长或中途失败怎么办；你怎样用证据证明某次改进有用。

第一遍先看项目定位、总图、核心对象、主循环和完整案例，不用背参数。第二遍看工具、记忆、Goal、安全和评测，把每项能力都连接到它解决的问题。第三遍按面试题反向找源码：每次回答都至少能说出一个函数、一个失败场景和一个验证方法。只背“ReAct、RAG、MCP、多 Agent”这些词，经不起连续追问。

全文有四类表述：

| 标记/表述 | 应当怎样理解 |
|---|---|
| 当前实现 | 能从当前源码中找到实际路径，不等于在你机器上已开启 |
| 默认配置 | 不传额外参数、不被环境变量覆盖时的程序默认值 |
| 可选能力 | 已有实现，通常需要额外依赖、模型文件或开关 |
| 扩展设计/面试拓展 | 用于回答架构演进问题，不能写成项目已经交付 |

任何默认值都可能被启动参数、进程环境或配置文件覆盖。本文没有读取你的实际 `.env` 密钥文件，也不会把你个人当前配置当成代码默认值。

建议做一个自测：合上文档，画出“用户消息 → CodingAssistant → AgentLoop → ModelClient → ToolManager → Runtime → 工具结果 → 下一次模型请求”。如果这条线还画不出来，先别背后面的高级名词。

### 全文导航

1. [怎么读这份文档，才能真正讲懂项目](#chapter-01)
2. [先用人话解释：MiniClaw 到底是什么](#chapter-02)
3. [全局结构：先看依赖，再看运行流程](#chapter-03)
4. [核心数据结构：先把系统里流动的东西分清楚](#chapter-04)
5. [CodingAssistant：把零件装成产品的地方](#chapter-05)
6. [AgentLoop：最核心的循环，逐段讲懂](#chapter-06)
7. [模型接入与流式传输：把不稳定网络变成可用接口](#chapter-07)
8. [工具体系：模型如何把一句话变成一次真实操作](#chapter-08)
9. [Runtime 与 Docker：命令在哪里执行，文件到底改在哪里](#chapter-09)
10. [Approval：把“模型想做”与“系统允许做”分开](#chapter-10)
11. [Project Instructions：为什么新目录里的 AGENTS.md 必须先看再改](#chapter-11)
12. [Goal：把“模型这一轮说完了”与“用户的任务完成了”分开](#chapter-12)
13. [取消、超时与恢复：为什么不能只写 task.cancel()](#chapter-13)
14. [飞书接入：真正困难的是会话、重复消息、背压和投递](#chapter-14)
15. [架构前端：它展示系统，不参与 Agent 执行](#chapter-15)
16. [国内 Agent 工程面试：围绕本项目的深入追问与回答](#chapter-16)
17. [本部分源码核验与易错说法速查](#chapter-17)
18. [记忆系统先讲透：模型没有自动记住，程序在替它管理证据](#chapter-18)
19. [工作上下文与渐进压缩：保住当前任务，同时能回到原始证据](#chapter-19)
20. [长期事实、会话摘要与蒸馏：什么时候值得写入，什么时候必须拒绝](#chapter-20)
21. [检索系统逐层拆解：精确符号、BM25、向量、RRF、重排与上下文预算](#chapter-21)
22. [用一个真实开发场景串起所有记忆环节](#chapter-22)
23. [记忆部分必须能回答的国内 Agent 面试追问](#chapter-23)
24. [记忆模块核验清单：已经实现的事实与最容易误讲的地方](#chapter-24)
25. [把所有模块串起来：修复加法函数的一条完整轨迹](#chapter-25)
26. [再看失败轨迹：出问题时系统如何接住](#chapter-26)
27. [排障地图：根据现象找到应该看的模块](#chapter-27)
28. [Trace：Agent 的飞行记录仪，记录的不只是聊天](#chapter-28)
29. [从失败到改进：评测闭环的完整构造](#chapter-29)
30. [Benchmark 适配层：接上一个榜，不代表完成了榜上的全部评测](#chapter-30)
31. [中国国内 Agent 开发面试：有来源的问题与能落地的回答](#chapter-31)
32. [国内面试怎样介绍这个项目：三个长度的口述模板](#chapter-32)
33. [连续追问模拟：从会背到真正理解](#chapter-33)
34. [从当前项目扩展到企业系统：面试设计题的正确展开方式](#chapter-34)
35. [动手练习：用这些题检验你是否真的懂](#chapter-35)
36. [简历与答辩中最容易被追问穿的说法](#chapter-36)
37. [配置和运行入口：读懂如何把系统启动起来](#chapter-37)
38. [本文核验范围与维护方法](#chapter-38)
39. [附录：当前全部 Python 源文件导航](#chapter-39)

### 65 道面试问答直达

C 为循环与分层，L 为模型传输，E 为执行工程，M 为记忆，I 为带公开面经来源的问题。I 系列逐题列来源；其他系列是与本项目直接相关的工程追问训练。

| 编号 | 问题 |
|---|---|
| C01 | [不借助框架，你怎么从零实现一个 Agent？](#interview-c01) |
| C02 | [为什么不直接用 LangChain 或 LangGraph？](#interview-c02) |
| C03 | [你怎么证明 AgentLoop 与编码业务解耦？](#interview-c03) |
| C04 | [模型说完成了，你怎么相信？](#interview-c04) |
| C05 | [同一用户连续发两条消息会怎样？](#interview-c05) |
| C06 | [为什么恢复后不直接重跑最后一个工具？](#interview-c06) |
| L01 | [SSE 与 WebSocket 怎么选？](#interview-l01) |
| L02 | [Function Calling 为什么还会失败？](#interview-l02) |
| L03 | [429、401、输出一半断流分别怎么处理？](#interview-l03) |
| L04 | [如何降低延迟？](#interview-l04) |
| L05 | [同一模型换供应商为什么可能不兼容？](#interview-l05) |
| L06 | [总超时 120 秒为何任务等了几分钟？](#interview-l06) |
| L07 | [为什么流式工具调用要按 index 聚合，而不是把所有参数拼一起？](#interview-l07) |
| L08 | [async 是否等于并行、是否能直接停止所有任务？](#interview-l08) |
| E01 | [工具调用你怎么保证参数正确？模型 JSON 错了怎么办？](#interview-e01) |
| E02 | [你为什么做自己的工具执行层，不把 function call 直接 dispatch 给函数？](#interview-e02) |
| E03 | [用了 Docker，是不是就不需要审批？](#interview-e03) |
| E04 | [为什么命令进 Docker，read/edit 却在宿主？这算隔离吗？](#interview-e04) |
| E05 | [两个 Agent 同时改一个文件怎么办？原子写能解决吗？](#interview-e05) |
| E06 | [用户点击取消后，Python 协程取消了为什么命令还在跑？](#interview-e06) |
| E07 | [长任务如何防止模型说“完成”但其实没做完？](#interview-e07) |
| E08 | [为什么 Goal 不能不停重试直到成功？](#interview-e08) |
| E09 | [飞书同一个用户连续发三条消息，你怎么处理？不同用户同时发呢？](#interview-e09) |
| E10 | [消息去重和幂等你怎么做？保证 exactly-once 吗？](#interview-e10) |
| E11 | [项目里有 AGENTS.md，直接放 system prompt 不就行了？](#interview-e11) |
| E12 | [如果把这个项目接进国内企业内部环境，你最先补哪三件事？](#interview-e12) |
| E13 | [面试官让你做项目演示，你展示什么能证明你理解工程？](#interview-e13) |
| M01 | [你的 Agent 记忆和普通聊天记录、RAG 有什么区别？](#interview-m01) |
| M02 | [为什么采用 BM25＋向量，为什么不只用 BGE？](#interview-m02) |
| M03 | [Embedding 与 Reranker 有何区别，为什么不能全库都重排？](#interview-m03) |
| M04 | [长上下文模型都有 128K、1M 了，还要压缩吗？怎样防止压缩丢失重要约束？](#interview-m04) |
| M05 | [记忆自动写入很容易幻觉和污染，你怎么控制？](#interview-m05) |
| M06 | [用户今天换了 Python 版本，系统怎么避免继续用旧记忆？](#interview-m06) |
| M07 | [为什么分块检索以后还要 parent 扩展？chunk 大小怎么选？](#interview-m07) |
| M08 | [向量库重启后为什么不重新编码？换模型会发生什么？如何避免租户串数据？](#interview-m08) |
| M09 | [Agent 一直重复 search，如何判断是在推理还是在原地打转？](#interview-m09) |
| M10 | [怎样证明你的记忆模块有用，而不是做了一堆组件？](#interview-m10) |
| I01 | [介绍一下你的 Coding Agent。Harness 到底是什么？](#interview-i01) |
| I02 | [ReAct 和 Plan 有什么区别，什么时候选哪种？](#interview-i02) |
| I03 | [怎么保证结构化输出和工具参数可靠？](#interview-i03) |
| I04 | [工具从 10 个增加到 100 个，Prompt 太长而且老选错，怎么办？](#interview-i04) |
| I05 | [MCP 是什么？与 Function Calling、Tools、Skills 有什么关系？](#interview-i05) |
| I06 | [RAG 离线入库和在线问答各怎么做？](#interview-i06) |
| I07 | [600GB—1TB 的金融 PDF 怎样建 RAG？Chunk 怎么定？](#interview-i07) |
| I08 | [为什么同时用 BM25 和向量？余弦相似度怎么算？](#interview-i08) |
| I09 | [Context 和 Memory 有什么区别，完整记忆系统怎么设计？](#interview-i09) |
| I10 | [上下文越来越长，怎么压缩，还不忘用户最早的要求？](#interview-i10) |
| I11 | [工具一下返回 10MB 日志怎么办？](#interview-i11) |
| I12 | [摘要和记忆为什么可以用小模型？模型怎样选？](#interview-i12) |
| I13 | [Prompt Cache 为什么能省钱加速，怎样提高命中？](#interview-i13) |
| I14 | [Agent 总是重复尝试、来回改文件，怎么处理？](#interview-i14) |
| I15 | [原本 5 分钟的 Agent 任务变成 20 分钟，怎么排查？](#interview-i15) |
| I16 | [不同任务怎样隔离？有了 Docker 就够了吗？](#interview-i16) |
| I17 | [Multi-Agent 如何分工、通信、终止？](#interview-i17) |
| I18 | [多个 Agent 同时修改同一个文件，怎么避免冲突？](#interview-i18) |
| I19 | [长期负责提升一个 Agent 的准确率，你第一步做什么？](#interview-i19) |
| I20 | [Agent 怎样保证输出准确？测试用例覆盖哪些？](#interview-i20) |
| I21 | [为什么自研？LangChain 和 LangGraph 的区别是什么？](#interview-i21) |
| I22 | [后端上千用户来用，如何部署、限流和扩容？](#interview-i22) |
| I23 | [讲一下 Transformer、自注意力、多头注意力和激活函数](#interview-i23) |
| I24 | [手写一个线程安全的 LRU，为什么有 GIL 还要锁？](#interview-i24) |
| I25 | [给数字集合 A，求能组成且严格小于 n 的最大数](#interview-i25) |
| I26 | [怎样 O(n) 判断二叉树是否平衡？](#interview-i26) |
| I27 | [最长无重复字符子串怎么做？](#interview-i27) |
| I28 | [如果面试官说“你这些模块是不是堆出来的”，怎样证明价值？](#interview-i28) |

<a id="chapter-02"></a>

## 先用人话解释：MiniClaw 到底是什么

MiniClaw 是一个 Python 编写的编码 Agent 框架。用户给它一个仓库和任务，它向外部大模型提交当前上下文和工具说明，接收模型提出的工具调用，再由本地程序检查并执行工具。执行结果会回到模型，模型据此继续找代码、修改、验证，或者输出回答。

大模型在这里提供判断和生成能力；框架负责把模型提出的动作变成可管理的执行过程。大模型不会因为说了“文件已修改”就真的改好文件，真正的文件变化来自 `write`、`edit` 或命令执行。大模型也不会自动记得上次程序退出前的事情，持续记忆来自持久化和检索。

你可以把它理解成一个会根据反馈做下一步决定的编码助手：

1. 接任务：明确现在要解决什么。
2. 查资料：读取代码、搜索符号、召回历史记录和技能。
3. 做动作：修改文件、运行命令。
4. 看反馈：读取退出码、测试结果、错误信息。
5. 调整下一步：继续修、换思路、请求审批或结束。
6. 保存过程：记录对话、执行状态、记忆和 Trace，方便恢复、复盘与评测。

这里最重要的是反馈闭环。只有“输入问题 → 模型回答”的应用还没有形成环境动作闭环。只有固定顺序执行一串步骤的流程，也不一定需要由模型动态决定下一步。MiniClaw 的通用循环允许模型根据每次工具结果重新决策，因此属于以工具调用驱动的 Agent。

### 与聊天机器人、RAG、工作流、基础模型的关系

| 概念 | 主要作用 | MiniClaw 的对应位置 |
|---|---|---|
| 基础大模型 | 根据上下文预测和生成输出 | 外部模型服务，由 `llm` 层接入 |
| 聊天界面 | 收消息、展示回复 | CLI、飞书适配器 |
| RAG | 检索外部证据，帮助生成 | 记忆检索是其中一种应用；代码 `grep/read` 也是获取证据的手段 |
| 工具调用 | 模型提出结构化动作，程序执行 | `ToolInvocation` + `ToolManager` |
| Agent 循环 | 反复“决策—动作—反馈” | `AgentLoop` |
| 工作流 | 规定步骤与状态迁移 | Goal、审批、投递等外围机制有确定性状态逻辑 |
| Agent 框架 | 管理模型、工具、状态、上下文及边界 | MiniClaw 的整体工程 |

MiniClaw 没有在这个仓库里训练出基础大模型。支持模型 API、调用本地 embedding/reranker，也不等于完成了预训练或微调。面试时应当说清自己的工作是应用和系统工程，训练能力需要另外提供经历。

### 项目价值要落在什么地方

适合强调的价值是：把一个工具调用循环做成可以控制、观察、恢复和评估的工程系统。

- **能执行：** 工具参数有契约，模型调用能落到文件和命令上。
- **能持续：** 长上下文有压缩，历史有检索，Goal 能跨一次模型停止继续推进。
- **能控制：** 工具可见性、路径边界、审批、Docker、取消形成不同层的约束。
- **能解释：** Trace 留下模型请求、工具结果、预算和状态变化。
- **能验证：** 单元测试、端到端 Eval、外部 benchmark 适配分别验证不同问题。

这也决定了面试重点：不要只讲“我接了一个模型”，要讲你如何处理模型之外的不确定性。

<a id="chapter-03"></a>

## 全局结构：先看依赖，再看运行流程

### 代码依赖图：谁可以依赖谁

```mermaid
flowchart TD
    Entry[CLI / 飞书 / Eval / Benchmark] --> Product[CodingAssistant 编码产品装配层]
    Product --> Loop[agent 通用 AgentLoop]
    Product --> LLM[llm 模型契约与传输]
    Loop --> LLM
    Product --> Tools[ToolManager 与具体工具]
    Product --> Memory[记忆与上下文压缩]
    Product --> Goal[Goal 状态与外层监督]
    Product --> Rules[项目指令与审批]
    Tools --> Runtime[Runtime 与 WorkspaceGuard]
    Product --> Trace[Trace 观测与分析]
```

箭头表示代码使用/依赖关系，不能把它误读成每次请求的执行时间线。

三个核心层级如下：

| 层 | 应该知道什么 | 不应该知道什么 |
|---|---|---|
| `llm` | 消息、工具描述、模型响应、SSE、超时重试、provider 差异 | 当前仓库路径、Goal 状态、飞书用户 |
| `agent` | 调模型、接回复、执行抽象工具、追加结果、何时停止一次 run | 文件编辑实现、Docker 命令、记忆数据库和审批界面 |
| `coding_agent` | 编码工具、会话、记忆、审批、指令、Goal 和 Runtime 的组合方式 | 不应让这些规则倒灌进通用 Agent 核心 |

源码：[架构边界测试](../../tests/test_architecture.py)、[通用工具协议](../../src/MiniClaw/agent/types.py)、[产品装配](../../src/MiniClaw/coding_agent/assistant/coding.py)。边界测试用 AST 检查导入关系，防止 `llm` 依赖 `agent/coding_agent`，以及 `agent` 反向依赖编码产品。它检验的是包依赖规则，不是所有运行时设计都自动正确。

这种结构接近 pi 的分层思想，但当前是 Python 原生实现。借鉴已有工具契约和架构思想应当如实说明，自己的工作贡献则讲到具体实现、差异和验证上。

### 一次任务的运行图

```mermaid
flowchart TD
    U[用户输入] --> E[入口解析配置与会话]
    E --> C[CodingAssistant 准备状态]
    C --> M[加载指令 / 召回证据 / 恢复上下文]
    M --> A[AgentLoop 构造模型请求]
    A --> L[ModelClient 流式请求]
    L --> D{完整回复是否含工具调用}
    D -- 是 --> T[逐个进入 ToolManager]
    T --> P[参数校验 / 指令检查 / 审批]
    P --> X[具体工具执行]
    X --> R[结果追加到对话与 Trace]
    R --> K[一轮结束 / 必要时压缩]
    K --> A
    D -- 否 --> F[一次 run 自然结束]
    F --> G{存在仍在推进的 Goal}
    G -- 是 --> S[Supervisor 检查状态和预算]
    S --> C
    G -- 否 --> O[收尾 / 记忆沉淀 / 交付]
```

图是概念流程：Goal 有完成、等待用户、失败和取消等分支，不能只看到回边就以为它会永远执行。错误和取消也会让一次 run 提前结束。

### 仓库目录地图

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

完整归档说明见 [项目目录树](../PROJECT_TREE.md)。

`src/miniclaw.egg-info`、`__pycache__`、`.pytest_cache` 是打包或运行产物。`external` 下第三方源码不是你自己的核心实现。`.aster` 这个名字和系统提示中的 “Aster” 是项目命名遗留，包名和入口仍然是 MiniClaw，不能误以为仓库里还有一个独立的第二套 Agent。

### 当前能力边界速查

| 项目 | 当前情况 | 面试中的准确说法 |
|---|---|---|
| 模型调用 | OpenAI-compatible Chat Completions SSE，含 DeepSeek 路由差异 | “我做了统一模型契约和兼容传输” |
| 工具数 | 基础装配是六个编码工具，加 memory、skill、goal、goal_complete，共 10 个；角色可裁剪或注入 | “数量以当前注册表为准” |
| 工具并行 | 一次回复可提出多个，当前按顺序执行 | “支持多工具调用，尚未实现通用并行调度” |
| 多 Agent | 有角色工具机制，没有完整多 Agent 协作调度器 | “角色隔离为扩展留了接口” |
| MCP | 当前主路径没有 MCP client/server 集成 | “能解释如何接入，但不会说已经实现” |
| Docker | 默认命令后端；文件工具仍在宿主受 Guard 约束 | “不是整个 Python Agent 进程都在容器里” |
| 工作区 | 默认 Direct，可选 Snapshot | “默认会修改有效源工作区；快照不会自动回写” |
| 向量与重排 | 有本地 hash 向量和可选 BGE/FAISS/BGE reranker | “BGE、模型重排、模型蒸馏默认关闭，需显式启用” |
| 长任务 | Goal 状态、验收与外层监督 | “不是多 Agent，也不是通用分布式作业平台” |
| 前端 | SVG 架构查看器 | “不是聊天管理后台” |
| 飞书 | SDK 长连接、会话路由、去重和 outbox | “可靠性做了本地持久化设计，不宣称分布式 exactly-once” |
| Replay | 模型决策边界回放，不执行历史工具 | “与执行真实工具的端到端 Eval 分工不同” |
| 生产规模 | 仓库有工程机制和实验资产 | “用户数、QPS、线上稳定性必须提供真实证据” |

<a id="chapter-04"></a>

## 核心数据结构：先把系统里流动的东西分清楚

源码：[llm/types.py](../../src/MiniClaw/llm/types.py)、[agent/events.py](../../src/MiniClaw/agent/events.py)、[agent/types.py](../../src/MiniClaw/agent/types.py)。

### ChatMessage：对话记录，不是整个系统状态

`ChatMessage` 的关键字段是 `role`、`content`、`tool_calls`、`tool_call_id` 和 `name`。

- `system` 放系统级规则，由产品层构造。
- `user` 放用户请求；检索证据也被显式包成不可信参考消息，使用 user 层而非 system。
- `assistant` 放模型回复，可以同时包含文本和多个工具调用。
- `tool` 放程序执行工具得到的结果，通过 `tool_call_id` 对应原调用。

例如：

```json
[
  {"role":"user","content":"读取 calculator.py 并修复加法"},
  {"role":"assistant","content":"我先看实现","tool_calls":[
    {"call_id":"c1","name":"read","arguments":{"path":"calculator.py"}}
  ]},
  {"role":"tool","tool_call_id":"c1","name":"read","content":"def add(a,b): return a-b"}
]
```

这是 MiniClaw 内部对象的简化 JSON 表示；发给服务商时由适配器转换成它要求的格式，字段不会直接照抄。

不要把对话和系统状态混在一起。对话里可能写着“测试通过”，但 Goal 需要单独存储验证记录；对话里可能写着“已发送”，但应查看 outbox 是否记录了成功确认。远端已接收、本地确认丢失时仍会出现投递不确定窗口，本地日志不等于端到端 exactly-once 证明。自然语言不是数据库事务。

### ToolInvocation：模型提出的动作申请

它只包含 `call_id`、`name`、`arguments`。有这个对象，只证明模型请求做某件事，不证明参数有效、权限允许、工具启动了，更不证明执行成功。

因此要分清五个阶段：提出调用 → 参数合法 → 获准执行 → 执行结束 → 结果符合任务目标。每一阶段都可能失败，后一个不能由前一个推定。

### ModelProfile、ModelRequest 与 AssistantReply

| 对象 | 重要字段 | 为什么需要它 |
|---|---|---|
| `ModelProfile` | model_id、context_window、max_output_tokens、价格、supports_tools | 描述模型能力与预算；默认窗口 128000、输出上限 8192 是配置值 |
| `ModelRequest` | profile、messages、tools、temperature、metadata、cancellation_token | 表达一次逻辑模型请求，不把所有参数散落在调用点 |
| `AssistantReply` | content、tool_calls、stop_reason、usage、error、metadata | 表达完成后的归一化结果，支持失败和取消 |
| `TokenUsage` | input_tokens、output_tokens、cached_tokens | 用于预算、观测和估算成本 |
| `ModelEvent` | text_delta、tool_call、usage、completed、error、transport | 传输过程中陆续产生的事件 |

`supports_tools` 是类型里记录的能力字段，不代表所有 provider 的能力都被自动探测和严格约束。接入新模型仍要做协议和能力测试，特别是工具参数格式、上下文窗口、输出长度和流式行为。

### 为什么需要两套事件

`ModelEvent` 描述“模型传输发生了什么”；`AgentEvent` 描述“Agent 执行发生了什么”。例如模型的一段文本对应 `text_delta`，但“工具开始”是 Agent 层事件，因为模型服务并没有真的执行本地工具。

`AgentEvent` 包括 `run_started`、`turn_started`、`message_added`、`text_delta`、`tool_started`、`tool_finished`、`turn_finished`、`run_finished`、`error`。产品层消费这些事件来持久化和记录 Trace，CLI 消费它们来展示文字和工具状态，飞书消费它们来合并进度和回复。

事件流带来的优势是：同一个 Agent 循环可以服务不同入口。代价是必须把事件生命周期规定清楚，否则可能重复持久化、漏记终态、把取消当失败，或者在最终消息到来之前向用户宣称完成。

### session、run、turn、attempt、Tool Call 的区别

| 名词 | 当前项目里的含义 | 举例 |
|---|---|---|
| session | 持续对话和相关持久化目录 | 今天与同一助手连续交流 |
| run | 一次 `AgentLoop.run` 产品执行过程 | 用户的一次任务执行，或 Goal 的一次尝试 |
| turn | 一次模型请求加该次回复要求的工具执行 | 模型先调用 read 的这一轮 |
| Goal attempt | 外层 Supervisor 发起的一次产品 run | 模型第一次停下后，Goal 尚未验收，再启动第二次 |
| transport attempt | 同一个逻辑模型请求中的一次实际 HTTP 尝试 | 请求 429 后的第二次发送 |
| Tool Call | 一次带 ID 的工具调用 | `read(path="calculator.py")` |

面试被问“你重试了多少次”，先说清重试的是网络请求、工具、副作用任务还是 Goal。它们的安全性和计费口径完全不同。

<a id="chapter-05"></a>

## CodingAssistant：把零件装成产品的地方

源码：[coding.py](../../src/MiniClaw/coding_agent/assistant/coding.py)。如果只能选一个文件解释项目整体，选这个；如果只能选一个文件解释 Agent 原理，选 `agent/loop.py`。

### 构造阶段发生了什么

`CodingAssistant.__init__` 接受模型 client/profile、workspace、session_path、runtime/approval/Goal/instruction 配置、角色和额外工具等参数，完成以下装配：

1. 解析源工作区和会话位置。未传 session_path 时，默认是工作区下 `.aster/session.jsonl`。
2. 创建 Runtime，决定有效工作区和命令后端。
3. 创建 TraceRecorder、RunStateStore，检查上一个运行是否中断。
4. 创建项目指令加载器，绑定 `runtime.host_workspace`，以实际可修改目录为准。
5. 创建 ApprovalGate，并接入 CLI 或飞书提供的审批处理器。
6. 用 TracingModelClient 包装模型 client，让主模型、摘要、Judge 等调用都可以被观察。
7. 创建 MemoryManager、GoalStore，按配置创建可选 GoalJudge。
8. 创建 ToolManager，挂接指令预检查、审批预检查，以及结果处理链。
9. 注册六个编码工具、两个记忆/技能工具、两个 Goal 工具，以及额外或角色工具。
10. 创建 AgentLoop，注入模型、工具执行协议、动态 system prompt 和记忆消息提供器。
11. 加载持久上下文，给未匹配的历史 Tool Call 补充中断结果。
12. 创建 GoalSupervisor，把 `_run_once` 作为一次尝试的执行函数。

这就是“装配根”的意思：具体实现选择在这里完成；通用循环只消费接口，自己不决定去哪读数据库、是否启用飞书、如何执行 Docker。

### 系统提示是动态构造的

`_build_system_prompt` 放入工作区、Runtime、角色、当前可用工具、基本编码行为、项目指令和 Goal 状态。`_provide_system_prompt` 在后续模型请求前重新获取需要刷新的内容。

工具说明由当前 ToolManager 的定义生成，所以禁用工具后，不应该继续在提示中宣称它可用。Goal 状态变化后，也应反映在下一次模型请求中。指令文件变化和检索线索变化则通过相应模块触发更新。

实际请求的大体顺序是：

```text
system: 产品规则 + 已解析项目指令 + Goal 状态
user: 显式标为不可信的历史检索证据（如有）
历史/压缩后的 messages
本轮用户请求及随后发生的对话
```

这解决的是“把什么送进模型”的工程问题，不是模型必然服从某条自然语言规则的证明。项目规则中声称用户要求优先，也仍是提示语义；真正不可越过的路径和执行边界要靠程序检查。

### 一次 run 的产品包装

`_run_once` 先准备指令和检索上下文、创建 run_id、记录 `run.started`，再消费 AgentLoop 的事件。

- `turn_started`：更新持久运行状态，说明当前在等待模型。
- `tool_started`：记下活动调用 ID、工具名和开始时间。
- `tool_finished`：写工具 Trace、更新 Goal 快照、收集产物、结束活动调用状态。
- `message_added`：交给 MemoryManager 追加持久对话。
- `turn_finished`：检查上下文大小，必要时运行压缩并替换模型当前视图。
- `run_finished`：进行记忆收尾，把 Trace/run ID、成本估计和产物列表交回入口。
- `finally`：记录终态、清理活动 run 上下文和关闭本轮观测。

一个细节值得理解：AgentLoop 是异步生成器，产品层在消费一轮结束事件时可以处理压缩，随后循环恢复执行时使用更新后的 `loop.messages`。所以不必把所有压缩代码硬塞进通用循环内部。

### 恢复中断 Tool Call 为什么要补消息

假设进程已经保存了 assistant 的 `bash` 调用，命令可能执行过，但 tool 结果尚未持久化，程序崩溃了。恢复后直接继续发消息可能违反服务商的工具协议：某个调用还没有对应结果。

`_repair_interrupted_tool_calls` 找出没有对应 tool 消息的调用，补一个 `[INTERRUPTED]` 结果，明确告诉后续模型检查当前工作区，不能假定副作用已经发生。

它修复的是**消息协议完整性和后续决策前提**，不是把之前的操作回滚。比如文件已经写入，补一条中断消息不会自动恢复旧文件。这个区别是恢复和幂等性面试题的关键。

<a id="chapter-06"></a>

## AgentLoop：最核心的循环，逐段讲懂

源码：[loop.py](../../src/MiniClaw/agent/loop.py)。默认 `max_turns=32`，`_running` 防止同一个循环实例重入。

### 用接近真实结构的伪代码理解

```python
async def run(prompt):
    reject_if_already_running()
    token = CancellationToken()
    append_user_message(prompt)

    for turn in range(1, max_turns + 1):
        if token.cancelled:
            finish_aborted()
            return

        request = build_request(
            dynamic_system_prompt(),
            optional_historical_evidence(),
            current_messages,
            tool_executor.definitions(),
            cancellation_token=token,
        )
        reply = await consume_model_stream(request)
        handle_abort_or_error(reply)
        append_assistant_message(reply)

        if not reply.tool_calls:
            finish_this_run(reply.stop_reason)
            return

        for call in reply.tool_calls:       # 当前是顺序执行
            result = await tool_executor.execute(call, token)
            append_tool_message(call.call_id, result)

        emit_turn_finished()

    report_turn_limit_error()
```

这是教学伪代码；真实实现还会发事件、补取消结果、处理空 completed reply、异常和清理。

### Tool Calling 为什么比让模型输出一段自然语言命令可靠

模型收到工具名称、用途和 JSON 参数 schema 后，可以输出结构化调用。框架不需要从“我觉得应该运行某命令”这类自由文本里猜动作。

但结构化仅降低歧义，不保证正确性。合法 JSON 可以引用不存在的路径，合法路径可以是敏感文件，合法命令可以删除目录。必须继续经过语法校验、访问控制、审批和执行结果校验。

MiniClaw 中模型提出动作的概率性部分与程序执行边界的确定性部分分开，正是这个原因。

### 一轮多个工具为什么没有直接并行

代码使用 `for call in reply.tool_calls` 和逐个 `await execute`，按源顺序把结果加入历史。这有几个直接好处：

- `write` 后 `read` 有稳定的先后顺序。
- 同文件多个编辑不容易发生写入竞争。
- 审批、取消和 Trace 的先后更容易解释。
- 一批中的某工具需要刷新项目规则时，模型可以看到明确结果。

代价是无依赖的多次只读调用也串行，延迟可以优化。未来并行需要工具副作用分类、依赖图、文件级锁、审批并发、取消传播、稳定的结果顺序和预算管理。不能简单把 `await` 改成 `gather` 后就宣称调度正确。

### 工具失败后是否立刻停止

通常不一定。统一执行器会把未知工具、参数错误、权限错误或执行错误变成 `ToolResult(is_error=True)`，AgentLoop 把内容作为 tool 消息返回，下一轮模型可以改参数或换办法。

模型传输失败、无法获得完整回复、超过循环轮数等则可能直接让 run 以错误结束。取消属于独立的停止语义，会补齐待处理的 Tool Call 结果，并阻止后续调用启动。

所以“错误恢复”不是一个统一的无限重试按钮，而是不同层各自处理可恢复错误。

### 这个循环是不是 ReAct

它体现了 ReAct 的关键思想：模型依据观察选择动作，工具执行后再观察和推理。但当前实现没有强制模型输出 `Thought/Action/Observation` 三段文本，也不需要保存或展示模型的完整内部思维链。

准确回答是：“我实现的是基于结构化 Tool Calling 的反馈循环，可以承载 ReAct 式决策；工程上保留动作和可见证据，不依赖文本格式解析思维过程。”

### 什么条件会结束一次 run

主要是模型回复没有工具调用、用户取消、模型或循环错误，以及达到轮数上限。注意 `stop_reason=length` 在没有工具调用时也能结束一次 run，这代表输出截断，不是任务验收成功。

`run.completed status=success` 更接近“本轮没有执行层错误而自然结束”；业务是否正确仍然需要 Goal 验收、文件/测试检查或 Eval。把运行成功率当任务成功率，是面试里的高频陷阱。

### 核心循环面试问答

<a id="interview-c01"></a>

**C01｜不借助框架，你怎么从零实现一个 Agent？**

先定义消息和工具契约，再写循环：组装上下文与工具 schema，流式调用模型，聚合完整回复，执行结构化工具调用，将每个结果按 ID 追加回消息，直到模型不再调用工具或触发限制。然后在循环外补持久化、取消、权限、预算和评测。MiniClaw 的 AgentLoop 就负责这个最小闭环，CodingAssistant 负责外围装配。追问“最容易漏什么”时，回答工具调用与结果配对、半截流和副作用重试。

<a id="interview-c02"></a>

**C02｜为什么不直接用 LangChain 或 LangGraph？**

自建小循环便于学习和明确控制消息协议、工具边界和恢复语义，也降低初始抽象负担；成熟框架在生态集成、图编排、检查点和多人维护上有价值。应根据团队经验、复杂度和交付时间选型。不能说所有框架都慢，更不能把“没有依赖框架”当成天然性能优势；性能要测实际调用链。

<a id="interview-c03"></a>

**C03｜你怎么证明 AgentLoop 与编码业务解耦？**

接口上只依赖 ModelClient、ModelProfile 和 AgentToolExecutor 协议，没有 import 编码工具、会话存储或 Docker。装配点传入具体实现，架构测试检查反向依赖。若换成数据查询产品，可以保留 loop，换工具和产品包装。追问“是否完全无策略”时，要承认循环本身有最大轮数、顺序执行和自然停止等基础执行策略。

<a id="interview-c04"></a>

**C04｜模型说完成了，你怎么相信？**

不把自然语言声明等同验收。普通 run 的结束只表示循环停止；需要更强保证时用 Goal 的新鲜验证证据和逐条验收，或端到端 Eval 读取真实文件状态。模型工具调用成功，也不代表业务正确，例如写出了能运行但答案错误的代码。

<a id="interview-c05"></a>

**C05｜同一用户连续发两条消息会怎样？**

同一个 AgentLoop 实例不能重入，入口层要决定排队、拒绝、取消旧任务还是建立新会话。当前飞书有会话级串行处理；不同会话可以并发，但共享同一 Direct 工作区时仍可能竞争文件。会话隔离、执行并发和资源隔离是三个问题。

<a id="interview-c06"></a>

**C06｜为什么恢复后不直接重跑最后一个工具？**

程序可能死在“副作用已发生、结果没保存”的窗口。自动重跑付款、发消息、数据库写入或文件修改会造成重复。当前做法补协议结果并提示检查实际状态；更完整的业务方案还需要幂等键、状态查询、事务或补偿动作。恢复对话不等于恢复整个外部世界。

<a id="chapter-07"></a>

## 模型接入与流式传输：把不稳定网络变成可用接口

源码：[config.py](../../src/MiniClaw/llm/config.py)、[factory.py](../../src/MiniClaw/llm/factory.py)、[openai_compatible.py](../../src/MiniClaw/llm/openai_compatible.py)、[env_file.py](../../src/MiniClaw/llm/env_file.py)。

### 配置、工厂和 client 分别做什么

配置层读取并验证 provider、base URL、model、API key、上下文窗口、输出上限、价格与超时；工厂层把配置转成具体 client 和 ModelProfile；client 负责 HTTP 与流式协议。这让切换模型时不用改 AgentLoop。

CLI 的显式模型/地址参数优先于对应环境配置，进程环境优先于 dotenv 文件，最后才是代码默认值。缺失必要 key、输出上限不小于上下文窗口、超时不合理等会在配置阶段报错。

当前配置文件里的 primary 默认地址是项目指定的兼容网关，模型名称也是项目默认路由标签；它们不构成官方产品能力或可用性的保证。实际连接到什么服务，应以你自己配置和供应商说明为准。DeepSeek 默认模型标签以当前 `llm/config.py` 的常量为准，不能直接背旧 README 的示例字符串。

### 一次内部请求如何变成 HTTP

传输层把消息转换为 wire 格式，请求 `base_url + /chat/completions`，设置 `stream=true`、输出上限以及可选 tools、temperature。工具参数在 assistant wire 消息里是 JSON 字符串，内部则是字典。

工具定义被包成服务商需要的 function 格式；如果有工具，设置 `tool_choice=auto`。客户端请求 usage 流信息。当前 DeepSeek 路由还显式传 `thinking: {type: disabled}`，避免该实现所针对的可切换思考接口把预算消耗在不返回答案的思考上；这属于 provider 适配行为，不应推广成所有模型都要关闭推理。

### SSE 是什么，为什么不等回复完整后再假装打字

SSE 是服务端通过一条 HTTP 响应持续发送事件的方式。模型产生文本后，客户端就可以消费该段内容，减少用户看到首段回答的等待。

真实链路是网络增量到达 → 解析事件 → 立刻发 `text_delta` → 入口展示。完整输出后按字循环展示属于动画效果，不会降低真正的首 token 等待。MiniClaw 实现的是前一种。

`_SSEDecoder` 按空行切分事件，忽略以冒号开头的注释，将同一事件的多个 `data:` 行合并。`_StreamAccumulator` 再解析 JSON、累计内容、聚合工具参数并统计 usage。把行级协议解析与模型语义聚合分开，能更容易测试不完整片段和协议错误。

### Tool Call 参数为什么不能收到一段就执行

流式工具调用可能这样到达：

```text
第 1 段：index=0, name="read", arguments="{\"pa"
第 2 段：index=0, arguments="th\":\"src/app.py\"}"
```

第一段既不是合法 JSON，也不是完整动作。当前实现用工具 `index` 做 buffer key，累积 ID、函数名与参数片段；流结束后一次性解析成字典，按 index 排序构造 ToolInvocation，再发出完成结果。

所以文本可以即时展示，工具必须等完整调用通过解析后再执行。否则可能在参数还没传完时修改错误文件。这也是为什么流式协议测试必须覆盖交错工具片段，而不是只测几段普通文本。

### 四类超时分别守哪道门

| 参数 | 代码默认 | 守住的问题 |
|---|---:|---|
| connect | 10 秒 | socket 建连及相关连接池/发送等待 |
| first token | 60 秒 | 模型长时间没有产生有意义内容，包括等待响应头阶段 |
| idle | 30 秒 | 产生首个有意义内容后，后续读取长期停滞 |
| total | 120 秒 | 一次实际传输尝试的整体上限 |

这里有两个很适合深入回答的细节。

第一，等待 HTTP 响应头不完全等同 TCP 建连。服务商可以已连上网络但仍在排队推理，当前代码不会简单用 10 秒把整个 `client.send` 包住，而是按首 token 与总时限限制等待头部。

第二，`total` 的 deadline 在 `_stream_attempt` 内建立，因此是**每次实际尝试**的上限，不是包括所有重试、退避和 fallback 的整次逻辑请求全局 deadline。若有多个路由和重试，用户等待总时长可能大于 120 秒。

当前 idle 等待围绕后续行读取实现，心跳/注释流的行为和“有意义 token”闲置不是完全同一个定义；first-token 则基于首次有意义内容，工具片段也算。因此 Trace 的 TTFT 可能是首个工具片段延迟，不一定是首个可见汉字。

### 重试与 fallback 的条件

默认 `max_retries=2` 表示每个路由最多首次请求加两次重试，共三次实际尝试。408、409、425、429 和 5xx 属于可重试 HTTP 状态；一些网络错误和流中断也可重试。非法 SSE JSON、非法工具参数等协议错误通常不在同一路由自动重试。

退避近似为：

```text
基础等待 = min(retry_max, retry_base × 2^retry_index)
有 Retry-After 时先参考它，再受 retry_max 限制
最终等待 = 基础等待 × [1-jitter, 1+jitter] 范围内的随机因子
```

默认 base 0.5 秒、max 8 秒、jitter 0.2。随机扰动减少大量请求同时重试造成的拥塞。因为 jitter 在基础上限之后乘上，最终等待可能略大于 8 秒；这里的上限是退避计算基数的上限。

fallback 是显式有序列表，例如主路由失败后换另一个模型，再换另一个 provider。当前实现里，一个路由遇到不可重试错误也可能继续下一个已配置路由；“不重试本路由”与“绝不 fallback”不是同一个条件。

### 为什么输出过文字后不再自动重试

用户已经看到“我准备修改 A”，重试后的模型可能又输出一次甚至改成另一套动作。更严重的是若副作用已经被执行，盲目重放会造成重复。

当前传输层一旦发出 `text_delta` 或最终工具调用事件，就禁止自动重试和 fallback，保留已有可见内容并以失败结束该请求。尚在内部聚合、没有发出的工具参数碎片不等于工具已执行。

这项策略保证“不会为恢复网络而自动重播已展示的输出”。它并不保证任务一定继续完成，后续恢复应重新建立明确上下文，结合任务状态处理。

### 流结束并不只认网络连接关闭

实现识别 `[DONE]`，也接受有 finish reason 的正常结束。若连接断开而既没有 DONE 也没有 finish reason，则判断不完整流。非法工具 arguments 不会被当成可执行字典。

取消时返回 `stop_reason=aborted`，错误时返回 error/completed 事件；AgentLoop 不把这些当成普通正常答案。某些失败路径中 UI 可能看过部分增量，但它们未必都进入持久 assistant 消息，后续不能仅凭界面显示重建完整协议状态。

### Token 与成本：会算账，也要知道账的边界

常见估算式是：

```text
估计费用 = ((输入 token - 缓存输入 token) × 普通输入单价
          + 缓存输入 token × 缓存单价
          + 输出 token × 输出单价) / 1,000,000
```

价格来自配置，默认价格为零时，Trace 的零费用不能解释为供应商免费。模型服务是否按缓存、失败请求、推理 token 计费，也应与供应商账单对齐。

MiniClaw 区分逻辑 `model.request` 和多次 `model.transport`，避免一个逻辑请求因为重试事件而被重复计入成功回复费用。但只掌握成功回复 usage 时，失败尝试在供应商侧产生的实际费用可能未被统计。面试中说“配置价格下的可观测成本估计”，比说“精确还原所有账单”严谨。

### 模型与流式面试问答

<a id="interview-l01"></a>

**L01｜SSE 与 WebSocket 怎么选？**

模型 token 下发主要是服务端到客户端的连续数据，HTTP SSE 简单并适合现有基础设施。双向实时语音、频繁交互控制等场景可能适合 WebSocket。MiniClaw 调模型用 SSE，飞书 SDK 长连接属于另一条接入链路，不要把两者混称为一种协议。

<a id="interview-l02"></a>

**L02｜Function Calling 为什么还会失败？**

模型可能输出不合法 JSON、参数类型错误、工具不存在、缺字段或选错工具；参数合法也可能违反权限或不满足业务要求。分层处理：传输聚合解析、schema 校验、角色与路径控制、审批、工具业务执行、结果验证。追问“schema 能解决幻觉吗”，回答只能限制结构，不能证明语义正确。

<a id="interview-l03"></a>

**L03｜429、401、输出一半断流分别怎么处理？**

429 在未输出时做有界退避，参考 Retry-After；401 通常先检查鉴权配置，不在同一路由盲目重复；已有文字后断流保留已见内容并报中断，禁止自动重播。显式备用路由可以在尚未输出时被选中。业务层重启任务还要考虑先前工具副作用。

<a id="interview-l04"></a>

**L04｜如何降低延迟？**

先用 Trace 拆 TTFT、生成耗时、工具时间、检索、重排、摘要和排队，再针对瓶颈优化。减少无用上下文和回合，缓存稳定检索索引，把只读无依赖工具并行作为后续设计，按任务选模型，限制不必要 reranker/Judge。不能只换一个模型就声称架构优化有效。

<a id="interview-l05"></a>

**L05｜同一模型换供应商为什么可能不兼容？**

兼容 API 仍可能在 usage、tool arguments 增量、stream_options、thinking 参数、max_tokens、错误状态、finish reason 上有差异。应使用协议测试和最小真实 smoke 分开验证。MiniClaw 已有抽象和部分 provider 适配，但不是自动兼容所有模型的万能驱动。

<a id="interview-l06"></a>

**L06｜总超时 120 秒为何任务等了几分钟？**

这里是单次 transport attempt 的总超时，每个路由有重试，多路由又有 fallback，之间还可能退避。整个 Agent 还有多 turn 和工具执行。若业务要求用户请求总时限，应增加跨尝试、跨工具的统一 deadline，并让下层按剩余时间运行。

<a id="interview-l07"></a>

**L07｜为什么流式工具调用要按 index 聚合，而不是把所有参数拼一起？**

同一响应可包含多个工具，片段可能交错到达。按 index 分组才能保持每个工具 ID、名称和参数的归属；完成后做 JSON 解析和 schema 校验，再按原次序交给执行层。追问边界测试时，覆盖多个工具交错、分片 UTF-8/行、断流、非法 JSON 与缺 finish marker。

<a id="interview-l08"></a>

**L08｜async 是否等于并行、是否能直接停止所有任务？**

async 主要让等待 I/O 时交出执行权，不会自动把顺序工具变成并行，也不会自动停止线程里的同步写入或宿主子进程。需要取消信号、协作检查、进程树/容器清理以及提交前检查。MiniClaw 的取消机制正是跨层贯通这些位置。

<a id="chapter-08"></a>

## 工具体系：模型如何把一句话变成一次真实操作

先把最容易混淆的边界讲清楚：大模型返回 `{"name":"edit","arguments":...}`，只代表它提出了一次操作请求。真正查找工具、验证参数、检查项目规则、申请审批、写入文件、控制超时、封装结果的是 MiniClaw 的 Python 代码。理解这一点，就能理解 Agent 和普通聊天最大的区别：Agent 的输出接上了可执行的外部能力，而这条执行链必须由程序约束。

对应源码：[工具装配](../../src/MiniClaw/coding_agent/tools/factory.py)、[工具协议](../../src/MiniClaw/coding_agent/tools/base.py)、[执行器](../../src/MiniClaw/coding_agent/tools/executor.py)、[角色管理](../../src/MiniClaw/coding_agent/tools/manager.py)、[总装配](../../src/MiniClaw/coding_agent/assistant/coding.py)。

### 先纠正工具数量：当前默认是十个

`create_coding_tools()` 按顺序创建 `read、bash、edit、write、grep、search` 六个编码工具。`CodingAssistant` 再注册 `memory、skill`，以及 `goal、goal_complete`，因此没有额外注入和角色过滤时，当前默认合计 **10 个工具**。旧笔记中的“九个工具”不能直接当成当前实现事实。

| 工具 | 模型要表达的意图 | 当前公开参数 | 关键执行语义 |
|---|---|---|---|
| `read` | 看一个文件 | 必填 `path`；可选 `offset、limit` | 行号从 1 开始；返回选定范围的头部，最多 2000 行或 50 KiB |
| `bash` | 执行一条命令 | 必填 `command`；可选 `timeout、goal_verification` | 名称叫 bash，实际 shell 由 Runtime 决定；输出取尾部 |
| `edit` | 精确修改已有文件 | 必填 `path、edits`；每项必填 `oldText、newText` | 每块匹配原始文件且应唯一，各块不得重叠；统一成功才提交 |
| `write` | 创建文件或整体重写 | 必填 `path、content` | UTF-8 写入，自动创建父目录，已有文件会被整体替换 |
| `grep` | 按内容找代码 | 必填 `pattern`；可选 `path、glob、ignoreCase、literal、context、limit` | 依赖宿主的 ripgrep，默认最多 100 个匹配，尊重 `.gitignore` |
| `search` | 按路径找文件 | 必填 `pattern`；可选 `path、includeDirectories、limit` | 默认最多 1000 项，最大 10000；跳过受保护路径及符号链接 |
| `memory` | 查询或管理持久记忆 | 具体 action 与字段见记忆章节及 schema | 由 MemoryManager 暴露，不是允许任意读写底层记忆文件 |
| `skill` | 查询、提案、维护可复用流程 | 具体 action 与字段见记忆章节及 schema | 面向程序性经验，不能等同于“自动安装并执行外部插件” |
| `goal` | 查看或更新目标状态 | 必填 `action`；可选 `summary` | action 只有 `status、checkpoint、waiting_for_user、failed` |
| `goal_complete` | 提交目标完成申请 | 必填 `final_result、criteria_evidence` | 每个证据项必须有 `criterion、evidence`；必须通过完成门槛 |

工具 schema 均使用对象形式；上述编码工具与 Goal 工具设置 `additionalProperties: false`，意味着模型胡乱追加字段会被拒绝。比如 `bash` 没有 `cwd` 参数，工作目录固定为有效工作区根目录；需要进入子目录，模型应在命令里明确表达。不能把别的 Agent 平台的 `exec` 参数照搬到 MiniClaw。

### 一次工具调用的完整执行顺序

```mermaid
flowchart TD
  A[模型生成 ToolInvocation] --> B[ToolManager 检查角色和启用状态]
  B --> C[ToolExecutor 查找工具]
  C --> D[深拷贝参数 / prepare_arguments]
  D --> E[递归校验 schema]
  E --> F[项目指令 preflight]
  F --> G[ApprovalGate 风险审批]
  G --> H[执行工具 / 监听取消和超时]
  H --> I[Goal 证据处理]
  I --> J[指令目标激活]
  J --> K[长结果转为可追溯 artifact]
  K --> L[统一长度限制 / ToolResult]
  L --> M[进入消息历史供下一轮模型观察]
```

1. `ToolManager` 在执行时检查工具是否对当前角色可用。因此即使模型记住旧 schema，或者手工构造一个当前不可用的工具名，执行端也不会仅因它曾经注册过而放行。
2. `ToolExecutor` 深拷贝调用参数，防止执行前处理直接改写上层原始对象。`EditTool.prepare_arguments()` 会兼容旧式顶层 `oldText/newText`，也允许将字符串形式的 `edits` 尝试解析成列表。
3. 校验器支持对象、数组、字符串、数值、整数、布尔类型，以及 required、enum、最小/最大值、长度限制和额外字段限制。它还特意防止 Python 中 `bool` 被误当 `int`。但它是手写的 JSON Schema 子集，不能声称完整实现了所有 JSON Schema 标准，如 `oneOf`、`$ref` 等并没有在这里实现。
4. `preflight` 可以返回一个 `ToolResult` 阻止工具运行，也可以返回 `None` 表示继续。这里把“要不要执行”与“怎么执行”分开了。
5. 工具错误一般转换成 `is_error=True` 的结果，让模型看到错误并调整；用户取消异常则继续向上抛出，防止模型把“用户叫停”当成普通工具报错后换个工具继续做。
6. 结果先经若干 transform，再统一限制最大字符数，执行器默认 `max_output_chars=100000`。注意单个工具自己的 50 KiB 限制与这个最终 10 万字符限制，是两层不同规则。

`ToolManager` 的角色逻辑很朴素：工具可以注册成全角色可用，或绑定某些角色；策略再应用 allow/deny，deny 优先；还可以临时禁用。`inject(role, tools)` 只是把工具集合分配给角色，**不会自动启动一个新的子 Agent，也不代表已经有多 Agent 协作调度器**。

### 为什么文件读取取头部，而命令输出取尾部

读代码时，用户通常从文件开头或明确的行偏移进入，所以 `read` 用 `truncate_head`，被截断时告诉模型下一次应该从哪一行继续。运行测试时，失败摘要、统计结果、堆栈末尾常在最后，所以 `bash` 用 `truncate_tail`。

示例：

```json
{"name":"read","arguments":{"path":"src/example.py","offset":101,"limit":80}}
```

这代表读取从第 101 行开始、至多 80 行，但仍受总输出字节上限约束。`offset` 超过文件尾会报错，不能假定它返回空字符串。若第一行本身就大于 50 KiB，工具不会随便返回半行伪装成完整代码，而是提示改用有界字节范围检查。

`read` 会识别 JPEG、PNG、GIF、WebP 的 MIME 类型，但当前 `ToolResult` 并未承载真正的图像附件。它返回“读到图像文件，但 Python ToolResult 尚不支持图像附件”的说明。面试中不能说这个 Agent 只要 `read(image.png)` 就能完成多模态识图。

`bash` 输出截断时，会把 Runtime 已经捕获到的输出落在 `.aster/tool-output/bash-<uuid>.log`，并返回相对路径。这里的“完整输出”必须加一个限定：Docker 层本身最多保留输出尾部 10 MiB，因此超过 Runtime 捕获上限的更早内容已经不存在，工具日志不能把它恢复出来。

### edit 为什么不是简单的字符串 replace

源码：[edit](../../src/MiniClaw/coding_agent/tools/edit.py)、[差异匹配](../../src/MiniClaw/coding_agent/tools/edit_diff.py)、[原子写](../../src/MiniClaw/coding_agent/tools/atomic.py)、[文件修改队列](../../src/MiniClaw/coding_agent/tools/mutation_queue.py)。

```json
{
  "name":"edit",
  "arguments":{
    "path":"src/example.py",
    "edits":[
      {"oldText":"timeout = 30","newText":"timeout = 60"},
      {"oldText":"retries = 1","newText":"retries = 3"}
    ]
  }
}
```

两个块都对**修改前的同一份文件**定位，不是先替换第一块再拿第二块匹配变化后的文本。这样能避免某一块插入的文字刚好被后续块再次命中。文本找不到、有多个候选、块互相重叠时应失败，不应凭感觉选一个位置。

执行时先读原文件，严格按 UTF-8 解码，剥离并记住 BOM，把 CRLF/LF 归一化后匹配，最后恢复文件原来的换行风格和 BOM。这解决 Windows 仓库中“只改一行却出现整文件换行差异”的常见问题。工具结果的 `details` 还带差异字符串和首个变化行。

还有一个不能省略的细节：匹配先尝试 exact，失败后会做有限模糊归一化，包括 Unicode NFKC、行尾空格、智能引号、特殊破折号与空格。如果任一块走模糊匹配，代码会以归一化后的整份内容作为编辑基底，因此文件中其他位置的字符或行尾空格也可能一起变化。它不是任意语义相似修改，也不能绝对保证“只变 oldText 那几字”；严谨使用时要检查完整差异，尽量提供原样精确 oldText。

`write/edit` 通过按绝对路径建立的 `asyncio.Lock` 串行修改同一文件，避免同一进程内两个协程互相覆盖。实际提交则先写同目录临时文件，再检查取消信号，最后 `os.replace`。因此它提供“单文件尽量完整地提交”的语义，不能扩大为“整个任务可事务回滚”：连续改了 A、B 两个文件，修改 B 失败时，并不会自动撤销已经成功的 A。

还要看清并发保护的范围：这个文件队列是进程内全局字典，不是跨进程文件锁；不管 `bash` 里自己发起的写操作，也不锁住用户的编辑器。Linux 上路径统一 `casefold()` 还可能把仅大小写不同的文件额外串行，表现为保守的并发限制。

### grep 与 search 不是同一种搜索

`grep` 搜的是内容，借助 ripgrep 的 JSON 流逐项读取匹配，达到默认 100 条后终止进程，并把剩余 stdout/stderr 排空。这个排空动作有实际工程意义：Windows 管道缓冲区里若仍有输出，单独等待另一条管道可能卡死。测试里专门覆盖了“大量匹配到达上限后不能管道死锁”。

`search` 搜的是文件路径，使用 Python `os.walk + fnmatch`，不依赖外部进程，不跟随符号链接。它并没有像 `grep` 那样通过 ripgrep 获得 `.gitignore` 语义；不能把二者的忽略规则讲成完全一致。`search.path` 选择扫描根，但结果和模式匹配使用工作区相对路径，写 `path=src` 时仍应注意模式是否需要 `src/` 前缀。

二者都过滤受保护路径，但只有 `grep` 的命中路径会在当前 `CodingAssistant` 结果处理中进一步激活对应目录的项目指令。当前 `_instruction_target()` 明确处理 `read/write/edit/grep`，没有处理 `search/bash`。这是实现边界，不要因为 search 也返回 `matchedPaths` 就推断它享有同样的动态规则激活功能。

<a id="chapter-09"></a>

## Runtime 与 Docker：命令在哪里执行，文件到底改在哪里

源码入口：[Runtime 配置](../../src/MiniClaw/coding_agent/runtime/config.py)、[Runtime 装配](../../src/MiniClaw/coding_agent/runtime/core.py)、[执行实现](../../src/MiniClaw/coding_agent/runtime/execution.py)、[工作区边界](../../src/MiniClaw/coding_agent/runtime/workspace.py)、[快照](../../src/MiniClaw/coding_agent/runtime/snapshot.py)、[镜像](../../docker/runtime/Dockerfile)。

### 两组正交的配置，不要混成一个开关

| 维度 | 可选值 | 回答的问题 | 当前默认 |
|---|---|---|---|
| `backend` | `host / docker` | shell 命令在哪个执行环境跑 | `docker` |
| `workspace_mode` | `direct / snapshot` | 工具读写的是源工作区还是任务副本 | `direct` |

因此默认组合是 `docker + direct`：命令在容器里执行，但 `/workspace` 绑定的是源项目目录，写文件会真实修改源项目。看到 Docker 不能马上得出“修改都留在容器中，退出就恢复”的结论。

`docker + snapshot` 才是命令在容器执行、文件改在 session 内副本。`host + snapshot` 也成立，代表使用任务副本，但 shell 仍运行在宿主。四种组合表达的是两个不同问题。

### ToolRuntime 保存的几个路径必须区分

| 字段 | 含义 | Docker + snapshot 示例 |
|---|---|---|
| `source_workspace` | 用户原始项目 | `D:/project` |
| `host_workspace` | 本次任务实际读写的宿主路径 | `<session>/sandbox/workspace` |
| `execution_workspace` | 命令在执行环境看到的路径 | `/workspace` |
| `session_dir` | 会话记录、Goal、trace 等的归属目录 | `<session>` |
| `workspace` | 统一路径映射与访问规则对象 | 指向有效 `host_workspace` 的 Guard |

工具传入 `/workspace/src/a.py` 时，`WorkspaceGuard` 会识别容器可见前缀并映射到有效宿主路径。这样模型从测试输出看到 `/workspace/...` 后，可以用同样的路径调用 `read/edit`，不会由于 Windows 盘符和 Linux 路径不同而失效。

**只有 `bash` 通过 ToolRuntime 把 shell 命令送进 Docker。** `read/write/edit` 使用宿主 Python 操作有效工作区，`grep` 调用宿主 `rg`，`search` 在宿主遍历。Docker 是命令执行隔离层，并不是把整个 Python Agent、模型 API 密钥、记忆管理器一起塞进容器。该设计让宿主控制平面掌握日志、模型和文件边界，容器执行工作负载。

### Docker 默认限制具体到什么程度

| 配置 | 当前默认值 | 作用 |
|---|---:|---|
| 镜像 | `miniclaw-runtime:py311` | 固定使用本地已有运行镜像 |
| CPU | `1.0` | 限制命令的 CPU 资源 |
| 内存 | `1024 MiB` | `--memory` 和 `--memory-swap` 同值 |
| 进程数 | `256` | 限制 fork/进程膨胀 |
| 网络 | `none` | 默认无法访问外网 |
| `/tmp` | `128 MiB` tmpfs | 临时可写空间，带 nosuid、nodev、noexec |
| 根文件系统 | 只读 | `--read-only` |
| Linux capabilities | 全部移除 | `--cap-drop ALL` |
| 提权 | 不允许获取新特权 | `no-new-privileges` |
| 打开文件数 | `1024:1024` | ulimit |
| 容器日志驱动 | `none` | 避免 Docker 日志无限增长 |
| 命令默认超时 | `120 秒` | ToolRuntime 在没有传 timeout 时设置 |
| 命令最大超时 | `900 秒` | 超出就拒绝 |
| Docker 捕获输出 | 尾部 `10 MiB` | 持续读流但只保留有界尾部 |

每条命令创建一个独立命名容器：`miniclaw-<task_hash>-<随机后缀>`，使用 `docker run --rm --pull never`。启动前校验 Docker 服务和本地镜像；不可用会明确报错，不自动退回宿主执行，也不自动从网络拉取镜像。

每次新容器意味着：文件变化可以通过工作区挂载保留，但上一条命令中的 shell 变量、后台进程、容器内未挂载位置的变更不会成为下一条命令的长期环境。`cd` 也不会跨工具调用保留。依赖应该放进已准备的镜像，或在允许且持久的工作区环境中准备，不能以为上一轮在容器内安装了系统包，下一轮还在。

镜像基于 `python:3.11-slim-bookworm`，预装 bash、编译工具、证书、curl、git、ripgrep，并安装 `lark-oapi==1.7.3` 和 pytest。镜像中有 curl 不等于运行时允许联网；默认网络仍是 none。Dockerfile 没有显式 `USER` 切换，因此不能宣传它采用了非 root 用户隔离。它提供了多层限制，但不是完整多租户安全平台。

### WorkspaceGuard 解决路径逃逸与操作级保护

`normalize()` 不靠字符串前缀判断，而是解析路径、解析符号链接、验证结果仍能相对于工作区根路径计算；对于尚不存在的新文件，还继续向上找已存在的父目录，确认真实祖先没有逃到工作区外。`../../secret` 和指向工作区外的链接不能仅因表面名字位于项目下就通过。

保护规则按 `read/write/search/execute` 区分：

- `.env`、`.env.*`、常见凭据文件、证书私钥扩展名、`.ssh/.aws/.kube` 等目录被保护。
- `.aster` 是运行时私有目录，通常禁止工具访问；`read` 只对 `.aster/tool-output`、`.aster/context-artifacts` 两类受控产物提供例外。
- `.git` 对写入和搜索严格限制；读取和执行时仍特别保护 `.git/config`、`.git/credentials`，不是一律把整个 `.git` 挡住。
- `memory.md`、`log.jsonl` 等名称也在受保护规则内，不应使用普通文件工具绕过专门记忆 API。

这里有一个重要设计点：`protected_paths_for()` 每次扫描当前存在的敏感路径，而不是只记住启动瞬间的一张列表。Docker 在每次启动命令时，给执行环境中的敏感文件挂载空文件、敏感目录挂载只读空目录，遮挡源文件内容。因此任务启动后新建 `.env`，后续容器仍能把它遮挡。

但不要把规则说成绝对无漏洞的安全证明：它按已知路径和名称保护，不会识别任意普通文件中藏着的密钥；校验与实际使用之间仍有外部进程改变路径的竞态空间；`host` shell 有宿主用户权限，Guard 只控制传给 Runtime 的工作目录，无法约束任意命令读取宿主绝对路径。对不可信模型输出而言，Docker 隔离与审批应共同使用。

### snapshot 是可恢复的任务副本，不是自动合并系统

快照位置通常为 `<session>/sandbox/workspace`，伴随 `manifest.json` 记录 task_id、源路径、任务路径、时间、sandbox、复制文件数/字节数、排除项数量。

快照默认最多 `512 MiB / 50000 文件`。复制前扫描预算，排除 `.venv、venv、node_modules、dist、build、coverage、.cache、__pycache__`、符号链接及执行层保护的敏感路径。再次加载相同 manifest 时验证 task_id 和源工作区一致，然后复用已有快照，让任务可以接着修改已有副本。

它没有实现“验收通过后自动把 diff 合并回源项目”的通用逻辑。对面试官应说：当前可隔离任务文件变化，并持久保留工作副本；要进入产品级交付，还需要差异审阅、冲突检测、源版本检查、显式应用与回滚流程。也不能假设 snapshot 一定完全没有 `.git`：它复用的是 execute 保护规则，而不是简单排除所有 Git 元数据。

<a id="chapter-10"></a>

## Approval：把“模型想做”与“系统允许做”分开

源码：[风险识别](../../src/MiniClaw/coding_agent/approval/risk.py)、[审批门](../../src/MiniClaw/coding_agent/approval/gate.py)、[配置](../../src/MiniClaw/coding_agent/approval/config.py)、[交互](../../src/MiniClaw/coding_agent/approval/interaction.py)。

### 默认并非每个工具都问用户

`ApprovalSettings` 默认 `policy=ask`、等待 `300 秒`。风险识别没有发现风险就直接返回，普通代码读取、一般精确编辑、新建非敏感文件不会仅因为使用工具而弹出审批。

对 shell，规则检测外部写入、删除文件、破坏性 Git 操作、数据库删除、敏感文件修改、系统变更、联网、直接覆盖等风险；对 `write/edit`，会识别修改审批策略、敏感文件，以及 `write` 整体覆盖已有文件。

例如：

```text
git push --force
```

它可能同时命中 `external-write、destructive-git、network-access`。系统选择最高风险等级给人展示，同时保留完整的 `capabilities`，不能只取最高等级后把其余风险丢掉。否则一条只授权联网的白名单可能被错误理解成也允许强制推送。

### 放行顺序与白名单的真实语义

审批路径是：所有识别到的能力都由 allowlist 覆盖 → 允许；否则 policy=allow → 允许；policy=deny → 拒绝；policy=ask → 调用交互 handler。没有 handler、handler 出错、等待超时都拒绝，不静默执行。

因此 `deny` 不是“无条件禁止所有有风险操作”，它仍允许满足白名单所有约束的调用。这与常见的“默认拒绝，有限例外”模型一致。

白名单可约束 `tool、risk、command_glob、path_glob`。一条规则不能只写 tool 而完全不收窄风险、命令或路径。命令匹配还比较 `&&、;、|、重定向、换行、反引号、$(` 等 shell 结构，防止 `pytest*` 被追加 `; rm ...` 后仍凭宽泛通配符放行。路径规则拒绝 `..` 片段，并用 Guard 归一化后的工作区相对路径匹配。

### 审批绑定的是具体调用，不是永久口头授权

`ApprovalRequest` 带短审批编号、tool_call_id、tool_name、风险原因、脱敏预览、能力集合、规范化调用哈希。哈希涵盖 call_id、工具名、参数；路径先按工作区统一。审批通过后再算一遍，发现调用变化则返回 `APPROVAL_CALL_CHANGED`。

这一设计防止“用户看的是删除 A，执行时换成删除 B”的对象变化。它不能代替文件版本校验：即便参数未变，文件内容也可能在等待审批期间被其他进程更新，因此严格的生产审阅还可以绑定目标文件版本、摘要和差异。

预览会遮盖常见 token/Bearer/密码变量形式并截断展示，减少把密钥直接发给群聊的风险。但正则脱敏只能覆盖已知模式，不等于能识别任何凭据。

配置可来自显式参数、`MINICLAW_APPROVAL_POLICY` 等环境变量，以及默认 `.miniclaw/approval.json`。修改默认审批策略文件会被标为 critical，避免模型悄悄把自己的审批关掉。当前识别函数针对默认相对路径做精确判断；若自定义策略文件位置，不能直接断言所有自定义路径都自动拥有同样专属保护。

### 审批不能替代沙箱

风险识别是可解释的正则规则，并不是 shell AST、污点分析、操作系统安全策略或完备的恶意行为检测器。`python -c`、自定义脚本、编码载荷等可能把危险操作藏在检测不到的语法里；联网下载的脚本也可能在随后执行时产生额外副作用。正确的工程表述是：审批降低可见高风险操作误执行的概率，Docker 限制即使执行后的影响范围，路径规则保护显式文件 API。三者各管一层。

<a id="chapter-11"></a>

## Project Instructions：为什么新目录里的 AGENTS.md 必须先看再改

源码：[规则配置](../../src/MiniClaw/coding_agent/instructions/config.py)、[规则加载器](../../src/MiniClaw/coding_agent/instructions/loader.py)、[规则来源模型](../../src/MiniClaw/coding_agent/instructions/model.py)、[与工具结合](../../src/MiniClaw/coding_agent/assistant/coding.py)。

想象一个仓库：根目录说“修改后运行测试”，`frontend/AGENTS.md` 说“组件必须使用设计 token”，`backend/AGENTS.md` 说“数据库变更必须写迁移”。把所有子目录规则每轮全部塞给模型，会浪费上下文，也会让不相关规则互相干扰。MiniClaw 使用“访问目标激活作用域”的方式加载。

默认开启，估算预算 `12000 tokens`；默认全局目录 `~/.miniclaw`，可以使用 `MINICLAW_AGENT_DIRS` 配置多个目录。每个目录按顺序查找 `AGENTS.md、AGENTS.MD、CLAUDE.md、CLAUDE.MD`，找到第一个就使用，并非同目录四份规则全部合并。

优先关系在提示中写明：用户全局 < 工作区根 < 更深局部目录 < 当前用户请求。这里只讨论用户/项目指引内部的优先关系，不能把项目文件说成高于平台系统约束。

一轮 run 开始先激活工作区根；随后 `read/grep` 的目标和 grep 命中文件会激活其父目录链。`resolve()` 只收集这些路径相关的规则，输出来源、作用域、哈希、状态、估算成本和 digest。规则内容按 mtime_ns + size 缓存，变化时重新读取并计算 SHA-256。

最关键的是写入前检查：模型第一次想写 `frontend/Button.tsx`，但适用于它的局部规则尚未送给模型，preflight 返回 `PROJECT_INSTRUCTIONS_REFRESH_REQUIRED`，明确本次没有修改文件。下一轮系统提示刷新，模型先看到新规则，再重试 edit。这避免“执行完才发现违反目录约束”。规则文件修改之后，同样可以触发刷新。

预算不足时，优先给更深、更近期激活的局部规则分配；全局内容优先级较低。每个来源会标注 included、truncated 或 omitted。截断尽量保留头部和尾部，中间放明确标记。这里 token 估算约为字符数除以 4，并不是模型精确 tokenizer，尤其中文和代码的估算误差应承认。

这个设计的能力边界也很明确：规则最终靠模型理解和执行，不是把每条自然语言都编译成硬约束；`bash` 内部写文件不会获得同样的目标级预检；被预算完全省略的规则不是 magically 被遵守。应该把它说成“有来源、作用域、预算和刷新机制的项目上下文管理”，不能说成“自动证明所有生成代码符合规范”。

<a id="chapter-12"></a>

## Goal：把“模型这一轮说完了”与“用户的任务完成了”分开

源码：[状态](../../src/MiniClaw/coding_agent/goal/state.py)、[存储与验收](../../src/MiniClaw/coding_agent/goal/store.py)、[外层监督循环](../../src/MiniClaw/coding_agent/goal/supervisor.py)、[目标工具](../../src/MiniClaw/coding_agent/goal/tools.py)、[命令与续跑提示](../../src/MiniClaw/coding_agent/goal/prompts.py)、[可选独立审查](../../src/MiniClaw/coding_agent/goal/judge.py)。

### 三个概念：模型 turn、Agent attempt、Goal

| 层次 | 结束条件 | 对应含义 |
|---|---|---|
| 模型 turn | 一次模型响应结束 | 模型暂时说完，可能提出工具调用 |
| Agent attempt | 一次 AgentLoop 运行结束 | 一串模型与工具交互暂时结束 |
| Goal | 验收完成、等待用户、失败、取消等状态 | 持久化任务的生命周期 |

普通 Agent 在模型不再调用工具时可以结束一次 attempt。对于“把这个模块改好，所有验收条件都满足”这样的长任务，只靠自然结束就可能产生假完成：模型说“已完成”并不保证测试真通过、边界情况真覆盖。

GoalSupervisor 因此包在 AgentLoop 外面。一次自然停止后，它读取持久状态：任务还 active 或 verifying 且需要继续，就生成续跑提示再开一个 attempt。它不是模型内部推理模式，也不是多个 Agent 并行规划；核心是一个确定性外层状态机。

### 状态与转换

```mermaid
stateDiagram-v2
  [*] --> active: create
  active --> active: checkpoint / another attempt
  active --> verifying: recorded verification passed
  verifying --> active: edit/write invalidates evidence
  verifying --> active: verification failed
  verifying --> complete: goal_complete accepted
  active --> waiting_for_user: unavoidable user decision
  verifying --> waiting_for_user: unavoidable user decision
  waiting_for_user --> active: resume
  active --> failed: budget limit / explicit failure
  verifying --> failed: budget limit / explicit failure
  active --> cancelled: user cancel
  verifying --> cancelled: user cancel
  waiting_for_user --> cancelled: user cancel
```

`goal.json` 保存 goal、精确验收条目、状态、attempt 次数、创建/更新时间、checkpoint、成本、最近验证、验证历史、完成证据、独立审查拒绝意见等。修改通过 `MemoryFileLock` 串行化，再以同目录临时文件、flush、fsync、原子替换持久化，降低进程意外退出造成半个 JSON 的概率。

Goal 文件上限为 `128 KiB`；目标文本和 checkpoint 单项上限 `8000 字符`；验收条件去重后最多 20 条；验证历史最多保留 4 条；单条验证输出最多尾部 16000 字符。它是有界任务状态，不是无限堆积所有原始日志。

用户可以输入：

```text
/goal 修复订单重复扣款问题
验收条件：
- 相同业务请求号只扣款一次
- 并发请求不会重复扣款
- 原有支付相关测试通过
```

这组验收条件是讲解示例，不代表本仓库存在支付业务。解析器也识别 `goal`、`目标` 以及 status/resume/cancel 的部分中文形式。目标创建属于入口能力；`goal` 工具没有 `create` action，模型不能用这个工具凭空把普通聊天升级成新目标。

### 完成门槛到底检查了什么

`goal_complete` 并不因为模型传了一个漂亮总结就直接结束。它要求：

1. 当前存在可运行 Goal，且状态为 verifying。
2. 有新鲜验证记录，`passed=True、exit_code=0`。
3. 相关验证不能还处于 pending。
4. `criteria_evidence` 必须把所有验收条目**原文各写一次**，不能漏、重复、翻译或改写。
5. 对可识别的“覆盖率至少 X%”目标，程序还会从验证输出提取 coverage/TOTAL 数值，缺证据或不达标直接拒绝。
6. 如果开启独立 GoalJudge，还需其逐项审查通过。

真实验证命令要显式标记：

```json
{"name":"bash","arguments":{"command":"python -m pytest tests/test_example.py","goal_verification":true}}
```

不是所有 exit code 0 的命令都自动当验收；没有标记不进入 Goal 验证记录。记录接线在工具 result transform 中：工具返回后，才调用 start_verification/finish_verification。因此当前持久状态里的开始时间不是底层进程真实启动瞬间，也不能声称整个 shell 运行期间都由该字段精确反映 pending 状态。

成功 `edit/write` 会清掉旧验证历史，确保“先测通过、后改坏、拿旧结果宣布完成”的简单路径被阻断。checkpoint 之后、失败验证之后的旧成功记录也受新鲜度筛选。**但当前接线没有监视任意 `bash` 文件修改**：如果测试后又用 Python 脚本改代码，系统不会自动因文件树变化而清除旧证据。严谨改进应把验证绑定到工作区版本、文件摘要或 Git tree hash，而不是只依赖工具名。

同样，exit code 0 本身不是任务完成的证明。`echo success` 也可以成功退出；未开启 Judge 时，大多数非覆盖率验收证据仍是模型声明加验证记录的形式检查。应准确评价为“比自然语言自报完成更强的验收门槛”，不是形式化正确性证明。

### 独立 Judge 的价值和局限

默认 `GoalJudgeConfig.enabled=False`。开启后，独立模型请求不提供工具，temperature=0，默认超时 60 秒，输出额度最多 4096 tokens；提示明确把目标、Agent 的总结、证据声明、命令输出都视为不可信证据。

返回必须是 JSON，覆盖每个原文 criterion，且 `approved` 必须恰好等于所有 criterion passed 的合取。JSON 错、漏条件、总判断与子判断不一致、请求异常都会拒绝完成，属于 fail closed。审查记录写到 `goal-judge.jsonl`。

“独立”指独立调用和审查职责，不保证模型家族、提供商、训练偏差都独立；默认不指定 judge_model 时可以使用相同模型。它也不会自动进入项目重新运行测试，而是审查已经提交的记录，所以仍可能被不充分测试或误导输出影响。

### 长任务预算不能讲成严格的实时抢占

默认限制为 `20 attempts / 4 小时 / 10 美元`。开始和结束 attempt 时检查次数、从创建时间起的持续时间、累计成本；成本上限设为 0 代表不按这项限制，不是零预算。

这些检查在 attempt 边界发生，因此单个 attempt 可能把时间或成本推过阈值后才停止。底层模型和命令还有各自超时，但不能把 Goal 的 4 小时说成随时抢占运行任务的硬截止。成本依赖使用量与价格配置，报价缺失或统计不足时，预算也会偏离实际账单。普通错误或 aborted 会停止本次监督流程，避免无限异常重试；不会保证任何暂时失败都自动恢复成成功。

<a id="chapter-13"></a>

## 取消、超时与恢复：为什么不能只写 task.cancel()

源码：[共享取消信号](../../src/MiniClaw/cancellation.py)、[工具取消等待](../../src/MiniClaw/coding_agent/tools/executor.py)、[进程回收](../../src/MiniClaw/coding_agent/runtime/execution.py)、[运行状态持久化](../../src/MiniClaw/coding_agent/runtime/state.py)、[取消测试](../../tests/test_cancellation_e2e.py)。

### 取消沿着整条调用链传播

```text
用户取消
  → CodingAssistant.cancel
  → AgentLoop 的 CancellationToken
  → 模型请求 / 工具 preflight / 工具执行 / Runtime
  → 终止流、取消等待、杀进程树或移除容器
  → 停止后续工具、记录 aborted/cancelled、落 trace 与 run-state
```

`CancellationToken` 内部用 `asyncio.Event`，保存首次取消原因和时间；重复取消不覆写首次语义。它是协作式信号，不是操作系统自动暂停所有代码。每一层必须主动监听，或者在被取消后执行清理。

工具执行器用 `asyncio.wait(FIRST_COMPLETED)` 同时等待操作和取消信号；超时、用户取消、外层 task 被取消分别处理。`ToolCancelledError` 不被转换为普通错误吞掉。这一点决定用户叫停之后能否阻断后续副作用。

宿主命令在 Unix 建立新 session，回收时杀进程组；Windows 使用新进程组并调用 `taskkill /T /F` 回收子进程树，然后等待父进程结束。Docker 取消会先 `docker rm -f <name>`，再终止宿主 Docker CLI 进程树。只杀 CLI 不足以证明容器中的命令已经停止。

清理使用 shield 等方式，尽量避免“清理过程又被取消”导致资源残留；Docker finally 还会再次尝试移除容器。需要承认：工具返回的 `container_removed=True` 表达代码进入了清理路径，而 `_remove_container` 会对部分 OS 异常或超时做容错，不是向 Docker 查询后形成的严格证明。生产监控还应独立核对残留容器。

### 文件提交是取消链上最敏感的瞬间

`atomic_write_bytes` 在写临时文件前检查一次，在 `os.replace` 提交前再检查一次。用户在准备阶段取消，就不应让目标文件被新内容覆盖。写完临时文件但还没提交时取消，临时文件要清理。

已经提交的修改不会由于随后取消而自动撤销；取消语义是停止尚未完成的继续执行，不等于事务回滚。`asyncio.to_thread` 的线程任务也不天然支持强制终止，因此不能宣传“任何工具都能立即被硬中断”。本项目的重点是关键提交点和外部进程的协作式回收。

### 运行状态恢复不等于自动重放副作用

`run-state.json` 记录 `running、waiting_model、executing_tool、finalizing、completed、failed、cancelled、interrupted`，以及 run_id、owner_pid、当前工具、已完成工具调用 ID、错误和恢复说明。

启动时发现上次状态非终态，且 owner_pid 已不存活，就把它标为 interrupted，说明“上个进程在执行哪个阶段/工具时停止”。这给用户与 trace 提供可解释恢复线索。它没有直接重跑上一条工具，因为进程崩溃可能发生在“外部副作用已发生、成功记录未写入”的窗口；盲目重放可能再次扣款、再次推送、再次修改。

PID 存活检查也不是分布式租约：PID 可能复用，不同机器不共享这个身份。它适合本地单机项目的中断识别，不适合直接包装成分布式任务恢复协议。

`assistant/session.py` 中还保留兼容类 `JsonlSessionStore`：逐条追加消息，加载时跳过坏行/半行。它不是当前产品持久化主路径；当前 `CodingAssistant` 通过 MemoryManager / WorkingContext 加载与追加上下文，再由产品层修复缺失的工具协议对应关系，详见记忆章节。兼容 store 本身没有跨进程串行 append 的显式锁，不能把这个旧接口描述成完整的事务存储；飞书入口的同会话串行处理则是另一层并发约束。

<a id="chapter-14"></a>

## 飞书接入：真正困难的是会话、重复消息、背压和投递

源码：[长连接入口](../../src/MiniClaw/platforms/feishu/bot.py)、[消息结构](../../src/MiniClaw/platforms/feishu/models.py)、[路由器](../../src/MiniClaw/platforms/feishu/router.py)、[去重](../../src/MiniClaw/platforms/feishu/dedupe.py)、[发送适配](../../src/MiniClaw/platforms/feishu/transport.py)、[DeliveryManager](../../src/MiniClaw/platforms/delivery.py)。

### 接入链路与线程模型

FeishuBot 使用官方 lark-oapi SDK 长连接接收消息，SDK 有自身事件循环；MiniClaw 把 Agent 处理放到单独 worker thread 中的 asyncio loop，再通过 `asyncio.run_coroutine_threadsafe` 投递路由任务。这样长耗时模型调用不会直接堵塞 SDK 的消息回调。

SDK 在 import 时有事件循环副作用，因此 bot/transport 在实际构造时才导入 SDK。通用 router/runtime 导入不会无缘无故创建飞书循环；关闭时取消工作、停止 worker、尽量清理 SDK pending tasks。这个细节在本地服务“按 Ctrl+C 后进程退不干净”的问题里很有价值。

消息归一化要求 sender_type=user，至少有用户 ID、消息 ID、chat ID；支持 text 与 post 的文本提取，移除 mention 占位符。其他消息类型会生成“暂不支持”的文字描述，不会自动下载附件或识别图片。当前 `mentioned` 是是否有 mention 列表，不能夸大为代码已严格校验“被 @ 的对象一定是本机器人”。

当前文本提取最终执行 `" ".join(text.split())`，会把连续空白与换行折叠成单空格。它适合普通聊天，却意味着不能直接假设真实飞书多行消息能原样保留 `/goal` 的“验收条件：”分段，也会影响复制代码的缩进。上文多行 Goal 示例解释的是命令解析器的输入协议；真实飞书链路还需修复/验证保留换行的行为，才能承诺同样的多行验收录入体验。

群聊通常需要 mention 才进入；默认 thread 模式下，属于已有托管话题的回复可以不再次 @。私聊直接进入。这是控制群噪声与维持线程连续性之间的取舍。

### session_key 决定哪些消息共享上下文

默认 `session_scope=thread`，支持三种模式：

| 模式 | 会话键组合 | 直接影响 |
|---|---|---|
| `thread` | 私聊按 chat；群聊按 chat + thread/root/parent/message | 同一话题共用上下文，不同话题隔开 |
| `channel` | chat + channel 标记 | 同一个群中所有参与者共享上下文 |
| `user` | chat + user | 同群按用户隔开；同用户不同群仍分别建会话 |

session key 经过字符清理并限制 180 字符，用于 `.aster/feishu/sessions/<key>/context.jsonl`。消息结构保存 tenant_id，但当前 conversation key 没有显式把 tenant_id 拼进去；channel/thread 模式也不会为同一会话不同用户建立单独的消息历史。若部署成多租户服务，不能仅凭这些字符串就声称已经完成企业级租户隔离。

还有一个更细的点：router 缓存每个 session 的 CodingAssistant，创建时给它设置 memory_user_scope 和 memory_channel_scope。共享 channel/thread 模式中这个 assistant 会复用，用户身份与长期记忆作用域是否完全符合预期，需要按业务验证，不能自动认为“群上下文共享，但用户长期记忆一定逐条精确切换”。

### 同会话串行，不同会话有限并行

每个会话有一个 `asyncio.Queue`，默认等待队列大小 `5`；每个会话一个消费 worker，按顺序处理消息，防止同一上下文出现两次并发 run。跨会话则使用全局 `asyncio.Semaphore`，默认同时允许 `8` 个会话处理。

这相当于：每桌点单顺序不乱，厨房同时做有限数量的桌。不是收到多少消息就开多少无限任务，也不是所有群共用一把大锁排长队。

队列满时立即回复“队列已满，请稍后再试”，然后把这条 inbound 标为完成，防止平台重试把拒绝消息再塞一次。队列大小主要限制单会话积压；会话总数、assistant 缓存和 queue 字典没有通用 LRU/全局积压上限，所以不能声称系统整体内存已经完全有界。

审批回复和取消命令在入普通队列之前处理。这是一个非常适合面试追问的设计：当前工作正等待审批，如果“批准”消息也排在当前工作后面，就会形成自己等自己的逻辑死锁。取消同理，用户必须能在会话忙的时候控制正在运行的任务。

审批收件箱还检查 session_key 和 user_id，只有当前任务发起者才能批准对应六位审批 ID，防止同群其他人替它放行。这个用户校验针对审批回复；取消分支本身并没有同样的 requested_by 校验，不能把审批权限保护外推到所有控制操作。

### 入站去重：防止平台重投，不是分布式 exactly-once

`PersistentEventDeduplicator` 按 message_id 维护 processing/completed/failed，日志写到 `.aster/feishu/events.jsonl`。默认记住最多 10000 个 ID；processing 超过 300 秒可再次 claim；failed 可以重试；completed 被抑制。

状态持久化意味着重启后可以继续识别一部分已经处理的消息。但“300 秒 stale”是重新 claim 的判断，不是后台调度器自动扫描并重放任务。重启不会凭空从日志恢复一个完整消息队列，因为日志中主要记状态和 ID，不保存所有业务消息执行载荷。

此外内存状态是单实例的，文件锁只包 append，不把“读最新状态 → 比较 → claim”做成跨进程原子事务。两个 router 进程不能靠这个类天然互斥消费同一消息。处理超过 300 秒的长任务也没有通过这段代码持续续租，应注意重复投递被判 stale 的可能性。

### 出站 DeliveryManager：减少重复并可靠重试，但存在经典不确定窗口

发送前写 queued，再写 attempting；异常后 retrying/failed；成功写 delivered。默认最多尝试 3 次，退避间隔从 0.25 秒开始按指数增加，即前两次失败之后通常等待 0.25、0.5 秒。

同一 idempotency_key 的并发发送共用一个进程内锁；已经 delivered 时直接返回之前结果，不重复调用 sender。进度回复 key 基于消息和内容 hash，最终分片 key 基于原消息 ID + final 序号，产物另有固定 key。可以把它叫“小型持久化 outbox + 有界重试”，但必须补足边界：

- 没有常驻后台恢复器自动扫描 queued/failed 并重发；重试主要发生在当前 deliver 调用内，后续再次调用相同 key 才有进一步机会。
- 出站业务锁是进程内锁，不能直接保证多个进程不会同时发同一个 key。
- 如果飞书已经成功收到消息，但响应丢失，本地会认为失败；下一次重试可能再发一次。
- 当前 transport 每次 reply 都生成新的 UUID，没有把稳定业务 idempotency_key 传给飞书作为跨重试去重 ID，因此不能承诺端到端 exactly-once。
- `latest()` 每次扫描日志查最新状态，日志未在这里建立数据库索引或压缩；消息量大后应改成具唯一约束的持久存储，并设计租约和投递恢复。

处理时先回复“正在处理…”，执行结束再 update 这条消息为最终结果；超长文本默认按 29000 字符分片追加发送，产物则单独列路径。当前不是把每个 token 实时编辑进飞书，也不是自动上传本地文件为飞书附件。直接按字符切片还可能拆开 Markdown 代码块，产品化时应按内容结构和平台字节限制进一步处理。

<a id="chapter-15"></a>

## 架构前端：它展示系统，不参与 Agent 执行

源码：[静态服务器](../../src/MiniClaw/frontend.py)、[前端目录](../../frontend/architecture/README.md)。

`frontend/architecture` 是无需构建工具的 HTML/CSS/JavaScript + SVG 架构查看器。支持缩放、拖拽、模块索引、缩略图、适应窗口、原尺寸、全屏、下载 SVG 和键盘导航；仓库包含总架构、工具 Runtime Docker、记忆压缩、长期召回等图。

`python -m MiniClaw.frontend` 使用 Python 标准库 `ThreadingHTTPServer` 提供静态文件，默认只监听 `127.0.0.1:8765`。它没有 Chat Web API、用户登录、WebSocket token 流、任务列表操作、在线审批控制台，也不实时订阅 trace。不要在简历中写成“完整可视化 Agent 管理平台”；准确说是“附带可交互架构浏览器，便于讲解和导航系统模块”。

<a id="chapter-16"></a>

## 国内 Agent 工程面试：围绕本项目的深入追问与回答

以下题目按国内 Agent 应用研发、LLM 应用工程、后端 Agent 平台岗位常见项目深挖方式组织，属于**可真实出现在工程面试中的问题与回答训练**，不冒充某家公司的未经核验原题。回答应先讲当前已实现，再讲局限，最后讲有优先级的改进；不要把设想说成已经落地的能力。

<a id="interview-e01"></a>

### E01：工具调用你怎么保证参数正确？模型 JSON 错了怎么办？

**参考回答：**我把模型生成参数和程序执行分开。MiniClaw 用 ToolManager 限定当前角色可用工具，ToolExecutor 先深拷贝、做兼容性预处理，然后递归校验 schema，检查 required、额外字段、enum、类型、范围以及数组元素。比如 edit 的每个对象必须含 oldText/newText，bool 不能冒充整数。失败返回结构化 ToolResult，让 Agent 用真实错误修正下一次调用，取消异常则单独上抛，不让它继续自救式执行。当前是 JSON Schema 子集，生产中可以使用成熟 validator，并加入业务语义验证和工具调用成功率指标。

**继续追问“schema 通过就安全吗”：**不安全。`command` 是合法字符串，不代表它不会删库。参数形状、业务语义、操作授权、执行隔离是四个不同问题，必须在不同层解决。

<a id="interview-e02"></a>

### E02：你为什么做自己的工具执行层，不把 function call 直接 dispatch 给函数？

**参考回答：**直接 dispatch 能跑 demo，但把角色权限、审批、超时、取消、日志和输出预算散在每个工具里，容易漏。这里先经过统一 preflight，再执行，再经结果 transform 做 Goal 验证记录、指令激活、长输出产物化。工具主要负责自身操作，横切约束集中处理。代价是链路顺序要严格定义，例如审批必须在执行前，证据失效必须在成功修改后，取消不能被通用 Exception 吞掉。测试要验证这些顺序产生的行为，而不只是函数返回字符串。

<a id="interview-e03"></a>

### E03：用了 Docker，是不是就不需要审批？

**参考回答：**需要，因为 Docker 限制进程能力和资源，审批确认业务动作是否合法。默认 direct 模式把真实项目可写挂到 `/workspace`，容器仍然能修改授权挂载的代码；若开 bridge 网络，仍然可能向外部发请求。MiniClaw 默认 network=none、只读根文件系统、去 capabilities、限 CPU/内存/PID，再对高风险工具做审批。正则审批不完备，Docker 也不是绝对安全边界，所以应采用纵深约束，不能用其中一层替代全部控制。

<a id="interview-e04"></a>

### E04：为什么命令进 Docker，read/edit 却在宿主？这算隔离吗？

**参考回答：**这是控制平面和命令执行面的分工。宿主掌握模型连接、会话和文件 API；read/edit 都通过 WorkspaceGuard 操作同一有效工作区，bash 才需要隔离任意子进程和 shell。好处是路径、日志和编辑语义容易统一，模型密钥也不用传到运行容器里。但安全前提是宿主文件 API 有可靠边界，不能让模型直接调用任意 Python 文件操作；它也不等于整个 Agent 进程被容器化。若面向恶意多租户，还要加强文件服务隔离、身份与运行账户隔离以及资源回收。

<a id="interview-e05"></a>

### E05：两个 Agent 同时改一个文件怎么办？原子写能解决吗？

**参考回答：**原子写只保证提交时读者看到旧文件或新文件，不能阻止丢失更新。MiniClaw 的 write/edit 还有按绝对路径的进程内 async 锁，保护读旧内容、生成新内容、提交的完整单文件操作。但跨进程、用户编辑器、bash 写文件不受这把锁管。要支持多个工作者，优先给任务独立快照或 worktree；合并时用 base revision 和文件摘要做乐观并发检查，发生冲突就停止合并。多文件变更要用 patch 集合或版本提交管理，不能指望 os.replace 提供全任务事务。

<a id="interview-e06"></a>

### E06：用户点击取消后，Python 协程取消了为什么命令还在跑？

**参考回答：**协程只是等待者，外部子进程有独立生命周期。取消等待不会自动杀 shell 子树，杀 Docker CLI 也不会保证容器停止。项目用共享 CancellationToken 从入口传到模型、工具和 Runtime；宿主回收进程组或 taskkill /T，Docker 回收容器后再清理 CLI；文件提交前再次检查 token；Agent 不再启动后续工具。我会用会产生长时间子进程的命令测取消，不只测 asyncio.sleep，并检查 trace、目标文件和残留进程是否一致。

<a id="interview-e07"></a>

### E07：长任务如何防止模型说“完成”但其实没做完？

**参考回答：**把自然 stop 和业务 complete 分开。GoalSupervisor 在 AgentLoop 外管理持久状态，一次 stop 只结束 attempt。完成需要新鲜的 `bash(goal_verification=true)` 成功记录和每条原文验收条件的证据，代码通过 edit/write 修改后旧验证失效；还能可选调用无工具的 Judge 逐条审查。当前局限是任意 bash 写文件未自动使证据失效，而且成功命令不一定是真验收。下一步我会将验证绑定具体代码版本，并对关键业务使用独立可执行验收器，Judge 只补语义覆盖，不能替代测试。

<a id="interview-e08"></a>

### E08：为什么 Goal 不能不停重试直到成功？

**参考回答：**无限重试会放大费用、重复副作用和错误循环。当前有 attempt、时间和美元预算，正常停止且任务仍未完成才由监督器续跑，错误和 aborted 会停止当前监督流程；必要决策用 waiting_for_user。预算当前在 attempt 边界检查，不是实时抢占。更成熟的做法是区分网络暂时失败、参数错误、权限不足、业务失败，每类单独退避和重试上限，并基于错误指纹、重复工具序列、连续无进展检测提前停止。

<a id="interview-e09"></a>

### E09：飞书同一个用户连续发三条消息，你怎么处理？不同用户同时发呢？

**参考回答：**先由 session_scope 确定消息是否共享上下文，然后每会话有独立有界 Queue 和单消费 worker，按顺序执行；不同 session 通过全局 semaphore 并行，默认最多 8 个。这样同一上下文不被并发写乱，同时不同群或线程不会被一个长任务完全挡住。队列满有明确反馈。审批和取消走队列前的控制通道，否则当前任务等待批准时，批准又排在它后面会死锁。若扩展多实例，需要把会话所有权和队列迁到分布式存储，当前进程内结构不能直接横向扩展。

<a id="interview-e10"></a>

### E10：消息去重和幂等你怎么做？保证 exactly-once 吗？

**参考回答：**当前不保证端到端 exactly-once。入站按 message_id 记 processing/completed/failed，持久日志抑制重复；出站用稳定本地 key、同 key 锁、delivered 记录和有限退避重试。但远端已经成功、本地未收到确认的窗口仍可能重复发送，而且 transport 每次新建 uuid，未把稳定 key 用作远端去重。生产中我会用数据库唯一约束确保消费/状态转换原子性，做事务 outbox 和租约恢复，并尽量使用下游幂等键；对于不支持幂等的外部系统，明确不确定结果并做查询对账，而不是盲目再执行。

<a id="interview-e11"></a>

### E11：项目里有 AGENTS.md，直接放 system prompt 不就行了？

**参考回答：**根目录这样可以，但大型仓库子目录规则有不同作用域，把全仓库全部注入会增加成本并引入不相关约束。MiniClaw 按访问目标激活目录链，记录来源、哈希、预算和局部优先级。第一次写到有新规则的目录会先拒绝本次修改，下一轮刷新规则后再试，防止先改后看。当前只对显式文件工具做目标感知，不完整解析 bash 内的路径；规则本身还是自然语言，关键安全和业务要求必须进一步做成可执行约束。

<a id="interview-e12"></a>

### E12：如果把这个项目接进国内企业内部环境，你最先补哪三件事？

**参考回答：**第一是身份和数据边界：把 tenant/user/channel/session 权限统一到服务端，检索和工具都按同一授权过滤，不能只靠提示词与字符串命名。第二是可靠执行：把会话状态、入站去重和出站 outbox 放到有事务和唯一约束的存储，按任务租约恢复，副作用接口接幂等键。第三是可验证交付：任务使用独立工作区，验收绑定代码版本，补真实业务用例和人工审阅入口。已有 Docker、审批、Goal、trace 是良好工程骨架，但目前不能直接声称具备企业多租户、分布式调度或严格 exactly-once 能力。

<a id="interview-e13"></a>

### E13：面试官让你做项目演示，你展示什么能证明你理解工程？

**参考回答：**我会用一个离线小项目，按可观察结果演示四个场景：给错 edit 参数，证明 schema 在执行前拒绝且文件没变；首次写入有局部 AGENTS.md 的目录，证明先刷新规则再修改；测试通过后 edit 改文件，证明旧 Goal 验证失效；运行长命令后取消，证明进程被回收、后续工具不执行、trace 标记取消。飞书部分用假的 transport 演示重复消息、同会话串行和审批绕过队列，不需要真实发群消息或消耗付费模型。这样展示的是故障边界和证据，而不只是一次聊天恰好成功。

<a id="chapter-17"></a>

## 本部分源码核验与易错说法速查

| 容易背错的话 | 应当采用的准确表述 |
|---|---|
| “MiniClaw 默认九个工具” | 当前六个编码 + 两个记忆/技能 + 两个 Goal，共十个 |
| “所有工具都在 Docker 跑” | bash 走 Runtime；文件工具和搜索主要在宿主操作有效工作区 |
| “Docker 退出项目自动恢复” | 默认 direct，源工作区真实改变；snapshot 才使用副本 |
| “snapshot 完成后自动合并” | 当前没有通用自动回写/冲突合并系统 |
| “host 也有强沙箱” | host shell 具有宿主权限，cwd 不是安全沙箱 |
| “审批白名单匹配一次就够” | 所有识别出的风险能力都必须获白名单覆盖 |
| “正则风险检测能拦所有恶意脚本” | 正则有盲区，应依赖多层隔离和授权 |
| “原子写保证所有并发修改不冲突” | 单文件原子替换 + 进程内锁，不是跨进程/多文件事务 |
| “每次写文件后旧验证必失效” | 明确接线覆盖成功 edit/write，任意 bash 修改没有自动覆盖 |
| “独立 Judge 默认开启” | 默认关闭；开启也不保证绝对正确 |
| “四小时预算是硬实时截止” | Goal 在 attempt 边界检查预算 |
| “取消能撤销所有操作” | 停止剩余执行并清理资源，已提交副作用不会自动回滚 |
| “飞书做了严格 exactly-once” | 当前是持久去重与本地幂等重试，仍有跨进程和远端确认窗口 |
| “日志里 queued 的消息会自动恢复发送” | 当前无后台 outbox 恢复调度器 |
| “前端是完整在线 Agent 控制台” | 当前是静态 SVG 架构查看器 |

关键行为有对应测试可定位：[工具测试](../../tests/test_tools.py)、[Runtime 测试](../../tests/test_runtime.py)、[审批测试](../../tests/test_approval.py)、[规则测试](../../tests/test_instructions.py)、[Goal 测试](../../tests/test_goal.py)、[取消测试](../../tests/test_cancellation_e2e.py)、[飞书测试](../../tests/test_feishu.py)、[投递测试](../../tests/test_delivery.py)。这里的依据是已阅读的源码和测试定义；测试文件存在不代表本文撰写过程中运行了所有测试，也不代表当前机器完成过真实飞书/Docker/付费模型端到端联调。

<a id="chapter-18"></a>

## 记忆系统先讲透：模型没有自动记住，程序在替它管理证据

先建立一个具体认识：语言模型每次响应，主要根据本次请求中实际收到的消息推理。把聊天记录留在硬盘，不会自动变成模型的长期记忆；把一段历史写入向量数据库，也不会自动让模型记起它。程序必须完成“留下历史 → 挑出有用信息 → 找回相关证据 → 在有限上下文中展示 → 必要时继续查证”这一整套工作。

MiniClaw 的核心设计是：**近期原始消息负责当前任务连续性，结构化摘要负责会话定位，稳定事实负责跨任务偏好，技能文件负责重复工作流程；原始归档与证据账本为它们提供可追溯的底座。**

入口是 [memory/manager.py](../../src/MiniClaw/coding_agent/memory/manager.py) 中的 `MemoryManager`。它自己不是另一只 Agent，而是装配并协调各类存储、检索器和压缩器的组件。

### 四种记忆的分工

| 种类 | 通俗理解 | 当前实现 | 典型内容 | 什么时候有用 |
|---|---|---|---|---|
| Working Context，工作上下文 | 桌面上正在翻的资料 | `WorkingContext`，会话 JSONL 与当前消息列表 | 最近用户请求、工具调用、工具结果、当前检查点 | 本轮继续改代码、读错误、验证 |
| Semantic Memory，语义记忆 | 长期保留的小本子 | `SemanticMemoryStore`，`MEMORY.md` 托管块 | 用户偏好、项目约定、环境事实 | 新任务仍需要遵守的稳定信息 |
| Episodic Memory，情景记忆 | 按会话写的工作记录 | `EpisodicMemoryStore`，每会话一个 Markdown | 目标、状态、结果、工具活动、证据、相关文件 | 找“上次处理过这个问题”的会话 |
| Procedural Memory，程序性记忆 | 经确认可复用的操作手册 | `ProceduralMemoryStore`，`SKILL.md` 和资源文件 | 测试流程、排错步骤、项目惯用操作 | 知道“这类事情怎样做” |

“四种”按用途与生命周期划分，不意味着只有四个文件或四个类。`ArchiveMemoryIndex` 保存压缩历史的可搜索证据；`MemoryEvidenceStore` 保存来源、可信度、作用域；`ContextArtifactStore` 保存大工具输出；`MemoryConsolidator` 负责可选蒸馏。这些是支持四类记忆的基础设施，不能简单说成“四个独立向量库”。

举个连贯例子：用户说“记住 [environment]：项目 Python 版本：3.11”，它适合进入稳定事实；本次报错的五万字日志适合保存为工件；“本次因解释器错误导致依赖导入失败，切换解释器后通过验证”适合进入情景摘要；经过确认的“创建环境、装依赖、运行验证”步骤才可能成为技能。四份信息有联系，但不应存成同一种内容。

### 当前文件布局与作用域

无用户/渠道作用域时，主要路径如下。具体会话路径可由调用方传入，`CodingAssistant` 的构造默认路径是 `.aster/session.jsonl`。

```text
工作区/
└─ .aster/
   ├─ session.jsonl                         # 默认会话事件日志，可传入其他路径
   ├─ context-artifacts/<session>/          # 原始工具结果与压缩归档
   ├─ memory/
   │  ├─ MEMORY.md                          # 稳定事实托管块
   │  ├─ MEMORY.conflicts.jsonl             # 事实冲突的创建与解决事件
   │  ├─ episodes/<session>.md              # 会话摘要
   │  ├─ archive-index.jsonl                # 历史消息/工具工件的分块索引
   │  ├─ evidence.jsonl                     # 来源与可信度账本
   │  ├─ retrieval-cache.sqlite3            # 倒排数据、向量、ANN 元数据
   │  └─ retrieval-cache.sqlite3.ann/*.faiss # 可选 FAISS HNSW 索引文件
   └─ skills/<skill>/
      ├─ SKILL.md
      └─ 其他技能资源
```

当 `user_scope` 或 `channel_scope` 非空时，`MemoryManager` 将二者以空字符分隔，做 SHA-256，取前 16 位作为目录名，把主要记忆根放到 `.aster/memory/scopes/<scope_key>/`。这使不同用户/渠道的稳定事实、情景摘要、归档索引和检索缓存分开。

同时要准确理解隔离边界：`.aster/skills` 仍按工作区共享；工件按 `session_id` 分目录；会话路径由上层负责选择。作用域哈希只是目录组织方式，不是密码学访问控制，也不等价于操作系统级多租户沙箱。面试时可以说“已有作用域过滤和目录隔离”，不能直接说“已经实现企业级租户权限系统”。

### 三种容易混淆的数据结构

| 结构 | 保存什么 | 重要字段 | 不应误解成什么 |
|---|---|---|---|
| `ArchiveMemoryRecord` | 原始历史的一个分块 | `record_id/session_id/source_kind/source_path/role/message_index/chunk_index/content/created_at/action_block_id/event_kind` | 不是模型生成的事实 |
| `MemoryEvidenceRecord` | 一条记忆的出处和状态 | `kind/content/session_id/source_path/parent_id/user_scope/channel_scope/workspace_scope/confidence/status/expires_at/metadata` | 不是对内容真假的独立证明 |
| `MemoryDocument` | 送入共享检索算法的统一视图 | `record_id/category/content/subject/relation/value/version/created_at/status/confidence/source_kind` | 不是所有持久化文件的统一数据库表 |

`RetrievalHit` 还保留最终分、RRF 分、BM25 分、向量分、精确命中分、各路名次、规则重排分、Cross-Encoder 分和被新版本替代的 ID。这样出了问题，可以区分“没召回”“召回后排低了”“预算截掉了”。

<a id="chapter-19"></a>

## 工作上下文与渐进压缩：保住当前任务，同时能回到原始证据

主要源码：[working.py](../../src/MiniClaw/coding_agent/memory/working.py)、[config.py](../../src/MiniClaw/coding_agent/memory/config.py)、[artifacts.py](../../src/MiniClaw/coding_agent/memory/artifacts.py)。

### 会话消息怎样进入硬盘

主循环产生 `message_added` 事件后，`CodingAssistant` 调用 `memory.append(message)`。它做两件事：通过 `WorkingContext.append_message()` 追加事件到 JSONL，再把消息加入 `active_messages`。

一条持久化消息大体长这样，以下是说明性示例，ID 与时间为示意：

```json
{
  "type": "message",
  "id": "持久化消息ID",
  "message": {
    "role": "tool",
    "content": "执行结果或工件引用",
    "tool_calls": [],
    "tool_call_id": "对应调用ID",
    "name": "工具名称"
  },
  "timestamp": "UTC时间"
}
```

压缩时不会把旧 JSONL 消息删掉，而是追加一条 `type=compaction` 事件，保存摘要、`first_kept_message_id`、压缩前 token 数以及压缩元数据。当前模型看到的内容则改为：

```text
一个合成的 system 检查点
+ 从 first_kept_message_id 开始的近期真实消息
```

这就是“日志完整性”与“模型上下文大小”分离。前者解决恢复与追溯，后者解决窗口和成本。

还要区分两条注入路径：`MemoryManager` 检索得到的历史参考由助手包成不可信 user 消息；这里的压缩 continuation checkpoint 在 `WorkingContext` 中实际构造成 system 消息。不能因此声称“所有历史派生内容都已放到低优先级角色”。检查点会包含用户与工具过程的摘要，后续若加强信任边界，应进一步审查这条路径，区分真正政策与压缩后的历史证据。

恢复时 `load()` 顺序读取日志，兼容旧版直接含 `role` 的消息格式；坏 JSON 行、字段异常行会被跳过。它找到最后一次压缩事件，根据第一条保留消息的 ID 重建“检查点＋近期消息”，并追加 `recovery` 事件。合成检查点在内存中的 ID 为 `None`，不能被当作真正持久化消息切点。

仓库还有 [assistant/session.py](../../src/MiniClaw/coding_agent/assistant/session.py) 的 `JsonlSessionStore`，它是简单的追加/加载兼容组件。讲当前主流程应讲 `WorkingContext`，不能把两个实现混为一谈。

### 为什么不直接每次把完整历史发给模型

完整历史至少会造成三类问题：输入 token 越来越多，推理成本和首 token 延迟上升；旧工具日志挤占当前任务空间；重要约束埋在长上下文中，模型未必能稳定利用。即使模型声明支持百万 token，也不意味着每轮都输入几十万 token 最划算。

MiniClaw 采取渐进压缩：大工具输出先外置，闭合历史再归档，近期原文尽量保留，只有必要时才用模型生成摘要。注意，它没有承诺“压缩完全无损”。严格保存的是可追溯的原始材料；模型即时看到的检查点仍然是有选择的信息表示。

### 默认阈值：参考值、计算值、实际生效值要分开

`MemoryConfig` 默认启用压缩与渐进处理，策略是 `layered-current`。大窗口下的参考配置为：

| 参数 | 参考值 | 用途 |
|---|---:|---|
| `target_tokens` | 30,000 | 压缩后希望接近的工作上下文规模 |
| `keep_recent_tokens` | 20,000 | 选择近期原文保留边界的预算 |
| `soft_trigger_tokens` | 80,000 | 超过后尝试低成本压缩 |
| `hard_trigger_tokens` | 100,000 | 超过后允许模型摘要 |
| `reserve_tokens` | 最多参考 16,384，另受模型输出预算约束 | 给模型输出等保留窗口 |
| `deterministic_semantic_tokens` | 4,500 | 判断旧非工具语义内容是否适合规则检查点 |
| `artifact_threshold_bytes` | 16×1024 字节 | 新工具结果达到此大小便外置 |
| `artifact_preview_chars` | 4,000 字符 | 大结果首尾预览的目标字符数 |

这不是所有模型都固定使用的绝对值。设窗口为 `W`，最大输出为 `O`，在未手工覆盖环境变量时：

```text
reserve = max(O, min(16384, max(1, W // 10)))
available = max(1, W - reserve)
hard = min(100000, available)
target = min(30000, max(1, int(hard × 0.3)))
soft = min(80000, max(target+1, target+(hard-target)//2, int(hard×0.8)))
keep = min(20000, max(1, int(target × 2/3)))
semantic_budget = min(4500, max(1, int(target × 0.15)))
```

例如测试中的默认 `ModelProfile("fake")` 是 128,000 窗口，保留量计算为 12,800，并不是 16,384。对于 64,000 窗口、4,000 最大输出，保留量为 6,400，hard 为 57,600，target 为 17,280，soft 为 46,080，keep 为 11,520。

配置校验要求 `keep < target < soft < hard ≤ W-reserve`，且 reserve 必须覆盖最大输出。手动改一个阈值不能假定其他阈值都会跟着按比例重新推导；实现里有些默认值由默认 hard 推导，最后再统一校验。因此应成组确认有效配置。

`target_tokens` 是目标而非强制裁剪上限：当前实现不会在摘要后无限重试，保证最终绝对小于 target。模型返回过长摘要、单轮特别大等情况，仍可能高于目标，应看 `estimated_tokens_after`。

### 大工具输出外置，具体外置了什么

渐进策略启用时，`artifactize_live_result()` 计算工具内容的 UTF-8 字节数。达到 16 KiB 默认阈值后，它把完整文本写入 `.aster/context-artifacts/<session>/`，保留：

```text
[MiniClaw context artifact]
Tool: 工具名
Full result: .aster/context-artifacts/.../结果.txt
UTF-8 bytes: 原始字节数
SHA-256: 内容摘要

Preview:
前半段预览
... [artifact preview truncated] ...
后半段预览
```

返回结果依然保存原来的 `is_error` 与其他 `details`，并增加 `context_artifact` 元数据。首尾预览适合同时看到命令启动信息和尾部错误/测试结论。实际输出还包含截断标记，所以不能把“4,000 预览字符”理解为整个引用内容严格只有 4,000 字符。

SHA-256 可以标识保存的内容，但这里不能说“每次读取都会自动验证哈希”：保存记录里有哈希，不代表读取路径已经实现全程校验。工件目录也没有在该模块中实现自动生命周期清理。

压缩旧历史时，`_artifactize_old_results()` 会将旧工具消息中尚未外置的结果保存为工件，**不限于超过 16 KiB 的结果**，并改成无预览引用。然后把这些旧消息副本序列化为归档 JSONL。原始会话日志不修改，归档中的工具内容可以通过引用回到完整工件。

### 切点为什么不能随便取“最后 N 条消息”

假设历史如下：

```text
用户 A：修复导入问题
助手：调用工具，call_id=t1
工具：t1 的结果
助手：解释原因
用户 B：再验证另一种输入
助手：调用工具，call_id=t2
工具：t2 的结果
```

如果只按条数切，可能把 `t2` 的调用丢到旧历史、只留下工具结果。模型 API 通常需要调用与结果对应，这种断裂既影响推理，也可能直接导致请求格式错误。

`_find_cut_point()` 从后往前累计 token，找到约略保留边界，再向后寻找用户消息，优先保留完整轮次。若单个轮次超大、找不到用户边界，才尝试在助手消息之前切，保留它之后的工具结果。找不到可用边界就放弃本次压缩，而不是强行制造残缺对话。

### 一次压缩按什么顺序发生

`turn_finished` 后，主助手优先使用提供商报告的 `input_tokens`，没有时使用本地估计。默认策略中，只有严格大于 soft 才尝试；严格大于 hard 才归类为 hard。

完整步骤为：

1. 检查取消状态、压缩开关和消息数量。
2. 找到安全切点，复制切点前非 system 消息为 `archived`，留下近期消息。
3. 把旧工具结果变成工件引用，再写出闭合历史归档。
4. 尝试把归档和引用的完整工件加入 `ArchiveMemoryIndex`；失败会记 `archive_index_error`，不是删掉已经保存的历史。
5. 从旧用户消息中提取显式“记住”指令，尝试写稳定事实。
6. 计算旧非工具消息的语义 token 量，提取当前用户请求、读过/改过的文件，生成规则检查点。
7. 如果旧语义内容太多，或“规则检查点＋近期原文”的估计超过 target，则认为需要模型摘要。
8. soft 阶段若需要模型摘要，返回 `None`，等到 hard；hard 阶段允许模型摘要。
9. 摘要成功或可降级后，再检查取消，找到真实 `first_kept_message_id`，追加压缩事件，替换当前活动上下文。

步骤顺序带来一个细节：soft 阶段最后返回 `None`，不代表完全没有动作。工件、归档、索引写入可能已经发生。重复尝试时，搜索端会对重复内容去重，但磁盘仍可能留下重复归档。这是当前实现的实际行为，不宜讲成严格无副作用的预检查。

### 规则检查点、模型检查点、降级检查点

规则检查点由确定性代码组成，包括当前请求、归档路径与哈希、之前检查点、最近语义记录、工具工件和文件操作。语义记录最多保留最近 12 条用户/助手内容，每条压至最多 1,600 字符。这种方式成本低、可预测，但不能理解所有隐含约束。

其中“文件状态”也不是重新扫描磁盘得到的完整事实：当前只从助手工具调用参数中的 `path` 提取路径，`read/grep` 记为读过，`write/edit` 记为修改过。通过 shell、别名工具、其他参数完成的文件操作未必被这张表覆盖；它记录的是调用痕迹，也不独立证明写操作成功。

模型检查点通过同一个 `ModelClient` 调用摘要请求，`supports_tools=False`，元数据 `purpose=compaction`。提示要求保留目标、约束、已完成工作、决策、精确路径、错误和下一步，并合并前一检查点。输出预算是当前模型最大输出与 `reserve×0.8` 中较小者。

若摘要返回空内容、报错，导致 `_model_checkpoint()` 抛出 `RuntimeError`，压缩器会退回规则检查点，策略标记为 `deterministic-fallback`，并记录 `model_summary_error`。这是针对该类错误的降级，并非所有任意异常都会在这个函数内被吞掉。

另一个策略 `legacy-summary-recent` 是保留的旧式对照路线：等 hard，模型总结旧消息，保留近期原文；它跳过新的实时工件化和分层归档处理。描述 benchmark 对照时要说明这两个策略区别，不能把旧策略说成生产默认。

### token 估计不是 tokenizer

工作上下文估计器把消息内容、工具名称、调用 ID、参数 JSON 的字符数相加，再约按每 4 字符 1 token 估计。它不是调用模型专用 tokenizer，对中文、代码、特殊格式都可能存在偏差。

而检索注入预算另有一个更保守的估计器：ASCII 约 4 字符 1 token，非 ASCII 每字符按 2 token 计。两者不是同一个算法。面试时直接声称“项目统一精确计算 token”会与源码不符。更稳妥的改进是优先采用可靠 usage，补上与实际模型对应的 tokenizer，并监测估计误差。

<a id="chapter-20"></a>

## 长期事实、会话摘要与蒸馏：什么时候值得写入，什么时候必须拒绝

主要源码：[semantic.py](../../src/MiniClaw/coding_agent/memory/semantic.py)、[episodic.py](../../src/MiniClaw/coding_agent/memory/episodic.py)、[distillation.py](../../src/MiniClaw/coding_agent/memory/distillation.py)、[evidence.py](../../src/MiniClaw/coding_agent/memory/evidence.py)。

### 稳定事实为什么放在受控 Markdown

`SemanticMemoryStore` 管理 `MEMORY.md` 中以下两个标记之间的内容：

```markdown
<!-- miniclaw-managed-memory:start -->
## MiniClaw Managed Semantic Memory

### preference
- 回答语言：中文

### project
- 测试框架：pytest

### environment
- 项目 Python 版本：3.11

### fact
- 文档目录：docs
<!-- miniclaw-managed-memory:end -->
```

标记外的人类笔记被保留。托管块之外的文本不是这套 `entries()` 检索的自动事实集合，所以“把内容随便写进 MEMORY.md 就一定会召回”不成立。

四种 category 是 `preference/project/environment/fact`；每条最多 2,000 字符，文件总大小上限为 32 KiB。这个上限体现定位：它是少量高价值事实库，不是原始日志仓库。写入会归一化空白，拒绝空内容、常见密钥形态和带明显临时性措辞的信息。

### 显式写入与显式自动提取

第一条路线是模型调用 `memory` 工具，例如：

```json
{"action":"remember","category":"environment","content":"项目 Python 版本：3.11"}
```

工具还支持 `read/search/replace/forget/conflict_list/conflict_resolve`，以及情景和归档查询动作。写入 `remember/replace` 或解决冲突后，可把来源写入证据账本。

第二条路线由 `StableFactIngestor` 负责，专门识别**用户消息**中按行写出的高精度指令：

```text
记住 [preference]：回答语言：中文
长期记忆 [project]：测试框架：pytest
remember [environment]: project Python: 3.11
```

它不是通用自然语言事实抽取器；“我昨天好像用过 Python 3.11”不会仅凭这句话被自动提升为稳定事实。支持的显式类别仍是那四种，不写类别则默认 `fact`。同批次重复指令先去重，再以 `conflict_policy=reject` 写入。成功时记录 `confidence=1.0` 和 `explicit_user_directive=True`，含义是来源明确，不是该事实永远正确。

这条显式提取路线发生在闭合历史压缩及 `finalize_session()`，不是每条用户消息到达时无条件立刻写入。

### 事实冲突如何处理

系统会提取内容的“主体”：优先使用冒号前的键，也识别部分“是/为/使用/采用/偏好”中文模式和 `is/uses/prefers/defaults to` 英文模式。

例如：

```text
已有：项目 Python 版本：3.11
新提议：项目 Python 版本：3.12
```

这两条有同一个主体，值不同。默认 `reject` 会创建持久化冲突记录并报 `MemoryConflictError`，通过工具返回旧值、新值和可选解决方式。处理政策包括：保留旧值、替换、同时保留；显式 `replace` 要指定旧内容。

不能只用向量相似度决定哪条事实为真。相关性高说明“它和问题接近”，不说明“它是现在生效的版本”。模型蒸馏路线遇到冲突采取 `keep_existing`，并写入 `inactive` 的 `semantic_conflict` 观察记录，避免模型推断静默覆盖既有事实。

这里的冲突发现是规则启发式，不是完整的实体关系抽取与自然语言推理。两句意思相反但主体写法差异很大，可能检测不到；两条同一主体下本可并存的不同事实，也可能被判冲突。实际录入时用稳定键值格式能提高可控性。

写事实采用 `.lock` 文件排他创建控制并发读改写，默认等锁 3 秒、超过 30 秒视为陈旧锁；新内容先写临时文件，再替换目标。该方案适合本地轻量使用，不是分布式锁。特别长的写入或不同进程复杂竞争，还需要更严密的事务方案。

### Episodic Memory 保存工作经历，而不是所有聊天细节

每个会话一个 Markdown，顶部记录 `Started/Updated/Status`，之后是摘要正文。确定性摘要包含：

- 最新用户目标，压至 1,500 字符；最新助手结果，压至 2,000 字符。
- 最近 12 次工具调用及部分参数。
- 最近 10 条含错误/成功信号的工具证据。
- 最多 20 个去重文件路径。

这个摘要让系统能根据“上次做过什么”定位旧会话，但它没有承诺保留每个细节。文件头保存开始时间，之后 checkpoint 更新同一文件。检索时把完整会话摘要变成 `MemoryDocument`，从 Goal 取主题、Updated 取时间、Status 取状态；completed 置信度为 1.0，其他通常为 0.7。

还要区分“本次 Agent run 结束”和“任务完成”：`run_finished` 时上层调用 `finalize_session()`，但传入状态可能是 active、waiting_for_user、failed 等。只有状态是 `completed` 才允许走可选模型蒸馏，不能因为本轮暂时停下就把任务写成已完成。

### 可选蒸馏的真实默认值

`MemoryConsolidator` 默认**关闭模型蒸馏**。即使关闭，仍生成确定性的情景摘要，并运行显式事实提取。开启 `MINICLAW_MEMORY_CONSOLIDATION_ENABLED` 且会话状态为 completed 后，才会调用模型抽取。

| 参数 | 默认值 | 含义 |
|---|---:|---|
| `max_input_chars` | 24,000 | 截断后的蒸馏输入字符上限 |
| `max_facts` | 6 | 最多处理的事实数量 |
| `min_fact_confidence` | 0.90 | 接受模型事实的自报置信度阈值 |
| `max_procedures` | 2 | 最多处理的流程候选数量 |
| `min_procedure_confidence` | 0.95 | 接受流程候选的阈值 |
| 蒸馏模型最大输出 | `min(profile.max_output_tokens, 2048)` | 控制附加调用开销 |

蒸馏输入先把工具消息压至 2,500 字符、其他消息压至 4,000 字符，拼接后只取最后 24,000 字符。因此“对所有历史进行完整自动学习”不是当前事实。`finalize_session()` 接收当前 `loop.messages`，较早已经压缩的内容可能只以检查点表示，也不会为蒸馏自动展开全部归档。

模型要求返回 JSON，包含 `episode_summary`、`facts` 和 `procedures`。一条事实不仅需要足够置信度，还必须有 evidence 原文引用，且该引用在归一化后的传入 transcript 中能找到；类别必须合法，内容不得像密钥，不得是明显临时结果。

实现还特别过滤一次 benchmark/debug/smoke/pilot/replay 实验描述、准确率/耗时/token 数等临时测量。因为“这次 10 个样本全通过”通常不应该变成永久的“项目准确率 100%”。原始实验应留在情景证据和归档，而不是永久事实。

这套校验提高可追溯性，但**原文引用存在，不等于事实被原文逻辑蕴含**。模型可以摘对一句话、总结错含义；0.90 也是模型报的数，不是经校准的真实概率。若做生产增强，应对关键事实加入结构化校验、时间/来源规则、冲突复核和离线人工标注评估。

### 流程候选不会直接变成正式技能

模型给出的 procedure 至少需要标题、两步非空步骤、足够置信度和有效 evidence。通过后写入证据账本，`kind=procedure_candidate`，标记 `promotion=requires_review_or_repetition`。

**当前模块没有把候选自动升级为 `.aster/skills/<name>/SKILL.md` 的完整晋升流程。** 候选只是低权重可检索证据。正式程序性记忆由 `ProceduralMemoryStore` 读取技能目录，其资源路径必须留在技能自身目录内，单资源最多 64 KiB。

技能发现发生在 store 初始化，通过 `*/SKILL.md` 读取简单 frontmatter；它不是完整 YAML 解析器，也没有持续文件监听自动刷新。模型可以通过 `skill` 工具按需读取正文及资源。自动检索同样可以返回匹配技能，但候选流程与正式技能的可信度和权重不同。

### 证据账本能做什么，不能做什么

`evidence.jsonl` 保存内容、会话、来源路径、用户/渠道/工作区、时间、置信度、状态和过期时间。ID 由内容与来源/作用域等字段的哈希生成，可做精确去重。`is_active` 要求状态为 active 且未过期，日期格式无效会被视为不活跃。超过 110,000 条时，会话收尾可触发保留最多 100,000 条 active 记录的压缩。

这是出处记录，不是知识图谱，也不是严格事件溯源状态机。尤其 `forget` 当前直接删除语义托管块里的事实，没有在所有归档、情景、缓存、证据记录中做级联遗忘；事实仍可能存在于旧原文。要实现隐私意义上的“彻底忘记”，需要专门的删除、墓碑、索引失效和重建机制。

<a id="chapter-21"></a>

## 检索系统逐层拆解：精确符号、BM25、向量、RRF、重排与上下文预算

主要源码：[retrieval.py](../../src/MiniClaw/coding_agent/memory/retrieval.py)、[archive.py](../../src/MiniClaw/coding_agent/memory/archive.py)、[manager.py](../../src/MiniClaw/coding_agent/memory/manager.py)。

### 先看整条检索链

```mermaid
flowchart TD
    Q[当前问题] --> E[检索情景摘要]
    Q --> S[检索稳定事实]
    Q --> A[检索当前会话归档]
    E --> HA[跟随前几个命中会话检索历史归档]
    Q --> P[检索技能与流程候选]
    S --> C[各来源内部候选]
    A --> C
    HA --> C
    E --> C
    P --> C
    C --> F[跨来源加权 RRF 与内容去重]
    F --> R[规则重排与可选 Cross-Encoder]
    R --> D[来源数量约束]
    D --> B[共享 15000 估计 token 预算]
    B --> I[注入实际可见的历史证据]
    I --> T[不足时调用 memory 继续查证]
```

每个来源的内部检索又包含三路召回：精确符号、BM25、向量；然后内部 RRF 与规则重排。`MemoryManager` 为避免每个来源都调用重排模型，内部召回通常传 `use_cross_encoder=False`，跨来源融合后再做一次可选 Cross-Encoder。这里有两个“融合层次”，面试叙述时最好分清：**同一来源内融合检索通道，来源之间再融合各自结果。**

### 第一路：为什么代码 Agent 要专门查精确符号

用户可能问“上次 `ModuleNotFoundError` 是怎么解决的”，也可能问“`src/main.py` 当时在哪修改”。代码任务中，路径、文件名、错误码、限定符号、下划线标识符往往比宽泛语义更可靠。

例如 `ECONNREFUSED` 与 `ETIMEDOUT` 都可能被语义模型归入“连接错误”，但排障方法不一定相同。`test_runtime.py` 和 `test_memory.py` 都是测试文件，但不应因为语义相似就混用。`exact_search_symbols()` 为这些形式生成精确检索信号，单独参与召回与 RRF，而不是完全寄托于 embedding。

默认 `exact_weight=0.36`。这个通道补充代码特征，不能理解成“命中任意文件名就永远排第一”；最终还有融合和内容重排。

### 第二路：BM25 解决关键词的稀有程度和文档长度

MiniClaw 的 BM25 参数默认 `k1=1.5`、`b=0.75`。标准形式可以写为：

```text
BM25(q,d) = Σ IDF(t) × f(t,d)(k1+1)
                         ──────────────────────────────
                         f(t,d)+k1(1-b+b·|d|/avgdl)

IDF(t) = ln(1 + (N - df(t) + 0.5)/(df(t) + 0.5))
```

其中，`f(t,d)` 是词在文档内出现次数，`df(t)` 是包含该词的文档数，`N` 是当前参与检索的文档数，`|d|/avgdl` 衡量文档是否过长。稀有词通常更能区分问题；一个词出现 100 次不应是出现一次的 100 倍价值；长日志也不应仅凭重复词多霸占排名。

当前实现会对查询 token 去重，让查询词频最多贡献一次。这避免长问题或选择题里重复出现的常见实体淹没真正关键的稀有错误词。

中文使用轻量字符＋相邻双字片段，例如“导入失败”生成单字及“导入/入失/失败”；英文删除常见停用词，并作很轻的词尾变体扩展。它不是 jieba 分词，也不是训练出来的中文分词器。中文双字可以缓解没有空格的问题，但也会产生“入失”之类没有语义的片段，这就是无需依赖的实现取舍。

### 持久化全文检索不等于 SQLite FTS5

这一点请特别记牢：**当前 `retrieval.py` 建的是普通 SQLite 倒排表，不是 `CREATE VIRTUAL TABLE ... USING fts5`，也没有用 `MATCH` 执行 FTS5 搜索。**

主要表如下：

| 表 | 作用 |
|---|---|
| `retrieval_documents` | 文档 key、token 数、更新时间等 |
| `retrieval_postings` | `(token, document_key) → term_frequency` 倒排关系 |
| `retrieval_embeddings` | `(encoder_key, text_hash) → 向量 BLOB` |
| `retrieval_ann_meta` | HNSW 索引对应的模型、维度、M、下一标签 |
| `retrieval_ann_items` | 文档与 FAISS 标签映射、向量、active 状态 |

SQL 负责缓存分词结果和取候选词频，BM25 分数由 Python 算。每次检索仍传入明确的 `documents` 列表，`df` 等统计只对当前集合有效，防止缓存中别的来源/作用域改变结果语义。

缓存把文档内容、记录 ID、类别、主题、时间、状态等一起哈希为 `document_key`，内容改变得到不同 key，避免错误复用旧表示。普通词法缓存和向量缓存有约 110,000 条触发、100,000 条目标的容量回收策略；这不意味着归档原文文件也被同步删除。

SQLite 使用 DELETE journal 与 `synchronous=NORMAL`，源码解释是避免 Windows 中 WAL 映射句柄影响临时目录删除。不能照着常见模板说这里“默认开启 WAL 高并发读写”。

### 第三路：默认哈希向量与可选 BGE-M3

生产工厂 `create_hybrid_memory_retriever()` 中，`MINICLAW_MEMORY_VECTOR_ENABLED` 默认 False。关闭这个开关意味着不启用真实 SentenceTransformer 模型，**不意味着向量分支完全消失**：检索器默认用 `LocalHashVectorEncoder`，384 维。

哈希向量把分词特征和字符三元组映射到固定维度，使用有符号 feature hashing：

```text
vector[hash(feature) % D] += sign(feature) × (1 + ln(count(feature)))
再做 L2 归一化
```

它无需下载、不调收费 API，适合中文字符与代码词形的低成本补充召回。但它没有经过语义训练，不能把“词形近似能力”夸成“理解同义改写”。不同词还可能哈希碰撞。

显式启用真实向量时，默认模型名是 `BAAI/bge-m3`，通过 `SentenceTransformer` 编码，默认 batch 8，设备 auto 优先 CUDA、否则 CPU，支持可配置 query/document 前缀。输出调用 `normalize_embeddings=True`，并缓存最多 50,000 个文本编码。不能把配置里的 `vector_dimensions=384` 当作 BGE-M3 固定输出维度：真实维度取决于已加载的模型向量，ANN 用实际维度建立索引。

模型只从本地已完整缓存的 Hugging Face snapshot 或指定本地模型目录加载；找不到依赖或模型缓存时记录 `load_error`，退到 local-hash，不会在运行期间自动联网下载。重启缓存还区分真实模型与 hash fallback 的 encoder key，避免把两个向量空间混用。

对已归一化向量，有：

```text
cos(q,d) = q·d / (||q||·||d||) = q·d
```

实现中名为 `cosine_similarity()` 的函数实际计算点积，因此依赖编码器正确归一化。若以后接入自定义 encoder，必须自己遵守这个前提；函数不会再次替你算两边模长。

### 向量缓存与 ANN 持久化分别省什么

向量缓存省的是“重新编码文档”的成本；ANN 索引省的是“在大量文档向量间做相似度扫描”的成本。两者不能互相替代。

`retrieval_embeddings` 用编码器身份与文本哈希作键，BLOB 保存 float 向量。因此进程重启后，已有文档和已经查询过的文本可复用编码。稳定状态下，如果当前文档集合已经在 ANN 索引内，系统只需要为新 query 编码，然后查持久化索引。

ANN 默认开关为 True，但实际应用需要有持久化 cache，并成功加载 FAISS 与 numpy。索引是 `IndexIDMap2(IndexHNSWFlat(..., METRIC_INNER_PRODUCT))`，默认 `M=32`、`efSearch=96`。先对向量归一化，内积即可对应余弦相似度。

HNSW 可理解为多层邻近图：搜索先在稀疏高层快速接近目标，再到密集底层细找。M 控制连接密度，增加一般提高召回但占更多内存；efSearch 控制查询探索宽度，增加一般提高召回但增加延迟。这是常见经验趋势，不是对任何数据都成立的精确速度公式。

索引 key 包含 encoder key、实际维度和 M，换模型或维度不会盲用旧索引。磁盘索引文件坏了、维度/数量不匹配时，可以用 SQLite 中保存的向量重建；新文件先写临时路径，再替换。

ANN 搜索之后按调用者提供的 `document_keys` 白名单过滤。初始搜索深度至少 64 或约目标数的四倍，不足时扩大直至足够或触达索引总量，避免其他作用域的近邻抢走所有位置。缺 FAISS、向量维度不一致或 ANN 出错，会记录诊断并走线性向量扫描。

这里已有“缓存持久化＋近似检索＋作用域过滤”，但不能据此称为成熟向量数据库。程序仍会读取源记录构造候选文档，FAISS 文件和 SQLite 跨介质更新不是一个数据库事务，进程内锁也不等价于多个实例共同写 ANN 的一致性协议。数据量和并发上来后需要进一步设计。

### RRF 为什么先融合名次

BM25 的原始数值、点积值、精确命中值量纲不同，直接 `0.7×BM25 + 0.3×cosine` 很容易被尺度支配。RRF 把“第几名”作为共同语言：

```text
RRF(d) = Σ weight_i / (k + rank_i(d))
```

某文档未进入某路有效排序，不贡献那一项。默认每路候选池 24，取三路前 24 的并集，最多可能有 72 个不同候选；`k=60`；权重为 BM25 0.68、vector 0.22、exact 0.36。它们不需要加和为 1，这不是概率分布。

假设某候选 BM25 排第一、向量排第十、未命中精确符号：

```text
RRF = 0.68/61 + 0.22/70 ≈ 0.01429
```

另一候选同时在三路靠前，通常能累积更高排名贡献。k 较大使第一名与第二名差距变小，更鼓励多路一致性。RRF 的局限也来自名次：原始分差有多大被部分抹平，因此融合后还要根据内容重排。

### 规则重排：不仅看主题接近，还看状态与时间

规则重排基础信号包括归一化 RRF、查询词覆盖率、主题词重叠、完整查询短语、偏好类别加分和有效多词短语。源码中的主体加权为：

```text
S = 0.70×归一化排名分
  + 0.18×查询覆盖
  + 0.07×主体重叠
  + 0.04×完整查询短语
  + 0.01×偏好类别
  + 0.13×有效连续短语命中
```

随后乘上状态与 confidence 的温和系数。completed/resolved 的状态权重高于 failed/cancelled，并不意味着失败记录被全部删除——失败历史可能恰好是排错关键。只有查询出现“当前/最新/最近/current/latest”等时间意图时，才追加按 `exp(-age_days/90)` 衰减的新鲜度加分。

这里的时间与状态还需看来源含义：archive 的 `created_at` 是建索引记录时的时间，episode 的时间取最近 checkpoint 更新时间；归档检索视图统一填 `status=completed`，不能把它理解成归档内每个工具动作都执行成功。新鲜度只是辅助信号，无法替代严格的业务事件时间和事实有效期建模。

通用 `MemoryDocument` 还支持结构化版本冲突：具有相同 subject、relation 且给定 version 的文档，先选最高 version，再做相关性检索，记录被替代 ID。**普通 `MEMORY.md` 条目并没有自动填齐 relation/version，所以不能说所有自然语言事实都已经具备完整的版本化知识更新。**

### Cross-Encoder 为什么更准也更贵

Bi-Encoder 把 query 和 document 分别编码，可提前缓存所有文档。Cross-Encoder 把两段文本一起输入模型，让它们充分交互，更适合判断“这一条究竟能不能回答这个问题”，但每个 query-document 对都要计算，难以对全库无差别使用。

项目 Cross-Encoder 默认关闭，显式开启后默认模型为 `BAAI/bge-reranker-v2-m3`，batch 4，最大输入长度 512，候选池最多默认 48、配置允许上限 50，设备 auto。只对规则重排后的有限候选调用，并缓存 query-document 分数，最多 20,000 项。

模型返回原始分，经截断到 [-60,60] 再 sigmoid。默认最终分为：

```text
S_final = 0.30×归一化规则分 + 0.70×sigmoid(CrossEncoder原始分)
```

Cross-Encoder 本地缓存或依赖不可用时，保留规则排名，记录 load error。sigmoid 得分不是经业务标注校准的“答案正确概率”，0.95 不等于回答正确率 95%。最大长度限制也意味着长文本尾部可能没参与模型打分。

### 归档不能只检索碎片：要把动作过程拿回来

`ArchiveMemoryIndex` 默认按 1,600 字符分块：尽量按行累积，超长单行按字符硬切，没有默认固定 overlap。索引会读取工具引用指向的完整工件，且要求真实路径留在工作区内，所以工件预览之外的错误也能被召回。

它不索引 `name=memory` 的工具结果，因为这些结果是同一记忆索引的派生视图。若把检索结果再次归档成源证据，下次又检索到它，容易出现“检索结果引用检索结果”的递归膨胀。

普通消息先检索小 child chunk，按 parent 分组，选最优 anchor，默认扩展左右各 1 个相邻 chunk；最多返回 5 个 parent。小块提高定位精度，扩展块补齐上下文。这不需要再单独训练或构建 parent 向量索引。

对工具链，索引额外记录 `action_block_id`：把助手工具调用、对应工具结果、随后助手决策连成一个动作块。命中动作块时，会保留同块控制消息；如果 anchor 来自工具工件，还补上该工件相邻证据块。这样模型看到的不是孤立“失败”两个字，而是“执行了什么 → 观察到什么 → 后来如何决定”。

例如用户问“上次为什么把执行方式改了”，一条单独的报错日志往往不够。动作块可以一起给出命令参数、错误、修改理由。管理器给 Cross-Encoder 评分时使用 anchor 内容，给模型展示时使用展开的 parent；这样避免长 parent 开头无关内容挤掉真正命中段。

归档搜索对完全相同的归一化内容去重，选择较新的副本，防止重复压缩产生的副本占满 Top-K。这个去重也有代价：内容完全相同但发生在不同时间/场景的历史可能被合并，若要严格还原事件次数，需要进一步保留事件身份。

### 跨会话不是每轮全库扫所有原文

自动检索先找当前会话归档，再跟随排名前 3 个情景摘要命中的历史会话，每个补找最多 2 个 parent，最后汇总去重取最多 5 个 parent。这样让 episode 充当“哪段经历可能相关”的入口。

如果关键旧会话没有进入前几个 episode，自动检索仍可能漏掉。模型可先 `episode_search` 或 `episode_list` 找会话，再用：

```json
{"action":"archive_search","sessionId":"旧会话ID","query":"新的实体或错误码","limit":5}
```

`memory` 工具的 archive_search 默认只查当前会话，支持明确 `sessionId`。内部管理器有跨全部会话搜索参数，不代表工具 schema 已直接暴露一个 `all_sessions` 选项。

归档索引主要在历史压缩时写入；不是每个 `message_added` 都把原始消息即时索引。因此“所有尚未压缩旧会话原文都已经可全文检索”也不成立。未进入索引的内容仍在会话日志中，需要读取日志或补建索引。

### 跨来源融合与 15,000 token 展示预算

来源融合也用 `weight/(60+rank)`，权重依次为 semantic 1.0、archive 1.0、episode 0.6、procedure 0.9、procedure_candidate 0.35。相同规范化内容合并，累积来源贡献并记录 `sources`。

最终候选按来源设数量上限：稳定事实 5、归档 parent 5、episode 5、正式技能 3、流程候选 2，整体检索返回最多 20。实际展示最多 12 条，共享 15,000 个保守估计 token，而不是每条各有 15,000。

展示时还有各来源内容上限：semantic 900、archive 3,000、episode 1,800、procedure 1,800、candidate 1,200 个估计 token。代码先扣除标签、来源等固定开销，尽量给每条至少 160，然后每轮以最多 128 的增量轮流补给；预算太少时先减少条数。候选选择对同来源已经入选的数量作惩罚，避免一种来源占满。

输出包裹在 `<retrieved_memory>` 中，明确告知模型：这是不可信历史证据，按数据处理，当前用户意图与系统规则优先；证据不足时继续 `archive_search`。这属于提示层边界，不能单独消除所有提示注入风险。

`retrieval_trace()` 返回**真正展示给模型的内容**，含截断后的 content、原长度、展示 token 和 truncated 标志；`ranked_retrieval_trace()` 才是预算前候选。评估时应以真实展示 trace 判断模型看到了什么，否则容易把“候选里有答案”误报成“模型已看到答案”。

### 工具新发现可以触发第二次记忆检索

用户初始只说“修一下启动失败”，系统还不知道实体。工具执行后出现 `ImportError`、路径或错误码，`maybe_refresh_prompt_context()` 会用根问题加新证据刷新召回。

条件包括：已有可检索记忆；新增的是普通工具结果而非 memory/skill/goal/goal_complete；内容包含高信号错误、文件等，或者至少积累两条工具消息。它使用最近最多 3 条工具证据，每条压至约 1,200 字符；每次初始 `prompt_context()` 之后最多动态刷新 3 次。

有新记忆时，与已有证据按 source/ID 合并，保留较高分，再进行来源约束和预算展示。这能减少一次检索漏掉新实体的问题，但不是无限次、也不是自动图推理。

### QueryTracker 防止 Agent 原地重复检索

源码：[query_tracker.py](../../src/MiniClaw/coding_agent/memory/query_tracker.py)。

QueryTracker 的判断不依赖大模型：先 NFKC 归一化、大小写折叠、清除表面符号差异；完全一样的 scope/query，且旧 limit 足以覆盖新 limit 时，直接返回旧结果，标记 `cache_hit`。

近似查询采用 token 集 Jaccard 相似度和 `SequenceMatcher` 字符序列相似度，取两者较大值，默认相似阈值 0.86。然后比较证据 ID 集：

```text
相似度低 → new_query
相似度高，而且出现新证据 ID → new_evidence
相似度高，没有新证据 ID → no_progress
完全重复且可复用缓存 → cache_hit
```

no_progress 会提示改实体、关系、时间条件或关键词，而不是一味把“查询一下”换成“请再查一下”。近似查询不是查询前一刀切阻止，它仍可运行检索并发现新证据。历史默认最多 64 次。

作用域区分 semantic 与 `archive:<session>`。当前 tracker 在 `MemoryManager` 初始化时创建，不落盘；虽然类注释写“一次 run 内”，`prompt_context()` 并没有显式清空它。如果复用同一个助手实例进行多轮，缓存可能跨轮留在内存，写入记忆也没有显式统一失效。这是应当识别的实现边界：更完善的版本可按 run 重置，或给缓存键加记忆版本，写入后失效。

<a id="chapter-22"></a>

## 用一个真实开发场景串起所有记忆环节

下面是便于理解的模拟情境，不是仓库已经跑出的实验结果：用户要求修复 Windows 上的 Python 依赖导入问题，并明确写“记住 [environment]：项目 Python 版本：3.11”。

1. 启动或恢复会话时，`WorkingContext` 从日志重建近期消息和检查点。`prompt_context()` 用当前问题检索稳定事实、episode、归档、技能，预算化注入。
2. 模型调用工具检查解释器、依赖和报错，助手消息与工具结果都进入追加日志。若输出达到 16 KiB，完整结果写工件，模型先看引用和首尾预览。
3. 工具暴露 `ModuleNotFoundError` 和具体路径，动态刷新使用这些实体，可能找回旧会话“解释器混用”的证据。
4. 自动注入只给出一部分线索时，模型调用 `episode_search` 定位旧会话，再用指定 session 的 `archive_search` 找动作块，获得旧命令、结果与后续决策。
5. 如果模型原样重复同一检索，QueryTracker 复用结果并提醒；若它换成具体虚拟环境路径，且获得新证据，则继续允许推进。
6. 多轮工具输出让上下文超过 soft，系统尝试工件化旧结果并归档。若旧语义复杂到需要模型压缩，则等 hard，再生成 continuation checkpoint。
7. 压缩保留调用/结果关系，近期原文继续可用；归档索引让被移出上下文的旧日志仍可检索。
8. 用户显式记忆指令在压缩或收尾时写入 Semantic，记录来源。若原来已经是 Python 3.12，就产生冲突，不自动猜该用哪版。
9. 本轮结束生成 episode。只有上层判定 completed 且模型蒸馏开关开启，才额外抽取长期事实与流程候选。
10. 下一次用户来问另一问题，系统不会把所有日志硬塞回去，而是用新 query 找必要证据；当前用户的新要求仍优先于旧偏好。

这里真正的闭环是“可追溯事实与过程被复用”，不是“模型参数被在线训练”。项目没有因为记忆写入就更新 LLM 权重，也没有由这些模块自动做 SFT、LoRA 或强化学习。

<a id="chapter-23"></a>

## 记忆部分必须能回答的国内 Agent 面试追问

以下按国内大模型应用、RAG、智能体工程岗常见的“项目设计 → 原理 → 故障 → 评估 → 改进”方式组织，是结合本仓库的练习题与参考回答；不冒称某家公司某一天的逐字面试记录。面试中先用 30～60 秒给结论，再按追问展开源码细节。

<a id="interview-m01"></a>

### M01：你的 Agent 记忆和普通聊天记录、RAG 有什么区别？

**可以这样回答：**聊天记录负责记录发生了什么，只有真正注入模型的片段才影响下一次推理。RAG 是根据问题检索外部信息的一类方法，Agent 记忆除了检索，还需要管理什么能写、什么时候更新、哪些事实已过期、上下文何时压缩、怎样追溯原文。我的项目按工作、语义、情景、程序性四个生命周期管理；检索算法共享，但写入策略和用途不同。工作上下文保近期原文，稳定事实保小量约定，episode 定位过去任务，技能承载可复用流程，归档保存可重新查证的细节。

**继续追问“这不还是 RAG 吗”：**长期记忆读取部分确实复用了 RAG 技术，没必要否认。项目差异在写入、状态、作用域、压缩恢复和 Agent 多步调用形成的闭环，而不是给向量搜索换个名字。

<a id="interview-m02"></a>

### M02：为什么采用 BM25＋向量，为什么不只用 BGE？

**可以这样回答：**代码 Agent 大量查询是路径、错误码、标识符，精确词对答案很重要；纯语义向量可能把相近的错误混淆。BM25 强调稀有关键词，向量补充改写召回，额外精确符号通道提高定位，然后 RRF 融合名次。项目默认 BM25 权重 0.68，大于向量 0.22；精确通道另为 0.36。默认无真实 BGE，启用后用本地 BGE-M3，加载失败降到哈希向量并记录状态。

**继续追问“为什么用这些权重”：**这是当前工程默认，不能宣称数学最优。需要在真实任务和时间隔离的评估集上，做 BM25-only、vector-only、hybrid、hybrid＋exact 的消融，对比 Recall@K、MRR/nDCG、任务成功率、延迟与成本，再根据收益调整。

<a id="interview-m03"></a>

### M03：Embedding 与 Reranker 有何区别，为什么不能全库都重排？

**可以这样回答：**Embedding 让 query/doc 分别编码，文档可提前缓存，适合大规模召回；Reranker 同时读 query/doc，交互更充分，适合精排，但每个候选都要推理。项目先用稀疏、精确和向量召回，跨来源融合后，对有限候选调用 Cross-Encoder，默认最多 48；最终用 70% sigmoid 后的模型分与 30% 规则分融合。它默认关闭、本地加载、故障时退回规则排序。

**继续追问“reranker 开了就必然提升吗”：**不一定。模型语言/领域不匹配、512 长度截断、召回阶段已经漏证据、候选偏置都可能导致效果下降。应单独评估候选召回、重排质量和最终答案，不把 sigmoid 当正确概率。

<a id="interview-m04"></a>

### M04：长上下文模型都有 128K、1M 了，还要压缩吗？怎样防止压缩丢失重要约束？

**可以这样回答：**窗口大只代表可以接收，成本、延迟和模型利用信息的能力仍有限。项目优先把大工具输出外置，再把闭合历史归档，保留近期消息和可续跑检查点；软阈值尽量用规则，硬阈值才按条件调用模型摘要。原始日志追加保留，摘要包含目标、约束、决策、路径和下一步，并带归档引用。需要具体细节时可以回查，不把摘要当原文。

**继续追问“怎么保证完全不丢”：**无法仅靠摘要保证语义无损。能保证的是原始材料的保存与可追溯设计。应专门测试跨压缩后约束保持、错误码回找、工具调用配对、恢复继续完成任务；当前 target 也只是目标，不是强上限。

<a id="interview-m05"></a>

### M05：记忆自动写入很容易幻觉和污染，你怎么控制？

**可以这样回答：**默认不开模型蒸馏，显式记忆指令只从用户消息抽取。开启蒸馏后只处理 completed 状态，要求引用在 transcript 中确实存在、达到置信阈值、类别合法、过滤秘密和临时实验数值，冲突保留旧事实。流程只写 candidate，不直接成为正式技能。读取时作为不可信历史证据注入，当前意图优先。

**继续追问“引用存在就证明事实是真的吗”：**不能。它只证明引用可定位，不证明模型总结被引用蕴含；自报置信度也未校准。高价值事实应加业务规则或人工确认，事实带时间与适用范围，对错误写入和冲突漏检建立评估集。提示注入也不能只靠一段“忽略恶意指令”的提示解决。

<a id="interview-m06"></a>

### M06：用户今天换了 Python 版本，系统怎么避免继续用旧记忆？

**可以这样回答：**写入时按主体规则检测冲突，明确替换或保留，不用检索相似度选真值。通用检索文档支持 subject/relation/version 的最高版本优先，相关性排序放在版本解析之后；查询“最新/当前”还有时间加分。但普通 Markdown 事实并没有自动构造完整版本字段，因此项目现阶段主要靠显式冲突解决。

**继续追问“忘记某条信息是否彻底删除”：**当前 forget 只删除稳定事实视图，旧日志和归档仍可能包含原文。若产品要求彻底遗忘，要让删除事件传播到 evidence、episode、archive、embedding、ANN 和 query cache，并阻止蒸馏再次从旧原文恢复已删事实。

<a id="interview-m07"></a>

### M07：为什么分块检索以后还要 parent 扩展？chunk 大小怎么选？

**可以这样回答：**小 chunk 有利于命中具体实体，但可能丢掉前后因果；大 chunk 上下文完整，却容易主题混杂、向量被稀释和重排截断。项目默认 1,600 字符按行分块，先检索 anchor，再扩展相邻块；工具流程还按 action block 保留调用、结果和后续决策。打分用 anchor，展示用展开父块。

**继续追问“1600 是最优吗”：**不是，它是当前默认。代码、日志、对话的合理切块单位不同，应比较不同 chunk 和扩展半径，在固定 token 预算下评估证据召回和任务完成率。还要观察是否跨消息边界、重复内容是否占 Top-K，而不是只优化孤立片段命中率。

<a id="interview-m08"></a>

### M08：向量库重启后为什么不重新编码？换模型会发生什么？如何避免租户串数据？

**可以这样回答：**SQLite 用 encoder key＋文本哈希持久化向量，FAISS 文件保存 HNSW，SQLite 还保存向量与标签映射，可重建索引。key 包含模型/后端身份，ANN 另外包含实际维度和 M，换模型或从 fallback 切到真模型会分开缓存。记忆根按用户/渠道作用域分目录，检索再次按当前 document keys 过滤 ANN 结果。

**继续追问“这就能支持千万数据、多进程高并发吗”：**不能这么承诺。当前还存在构造全量源文档、JSONL 读取、本地锁、SQLite 与 FAISS 非原子联合更新等边界。大规模要考虑专门的向量/全文服务、权限前置过滤、批量增量索引、事务或版本切换、持久化删除与恢复演练。

<a id="interview-m09"></a>

### M09：Agent 一直重复 search，如何判断是在推理还是在原地打转？

**可以这样回答：**不能只看工具调用次数，也不能把相似 query 全部禁掉。项目 QueryTracker 同时看查询相似度和新证据 ID：完全重复可用缓存；近似查询有新证据继续推进，没有新证据就提示换实体、关系或时间约束。阈值默认 0.86、历史最多 64 条；scope 不同分别比较。新增工具报错还能触发最多三次自动记忆刷新。

**继续追问“这样有没有误判”：**有。词面相似不等于语义相同，新证据 ID 不一定代表有用的新信息；完全相同 ID 的内容如果更新也需考虑。当前实例缓存没有在每轮和写入后显式重置，是可改进点，可以引入 run 生命周期和记忆版本，并结合剩余预算、证据覆盖与连续无进展次数决定下一步。

<a id="interview-m10"></a>

### M10：怎样证明你的记忆模块有用，而不是做了一堆组件？

**可以这样回答：**我会把评估分成三层。存储层验证追加日志恢复、工具配对、冲突更新、隔离与缓存重建；检索层看正确证据的 Recall@K、MRR/nDCG、展示后 evidence coverage 和延迟；任务层看跨会话、跨压缩后实际任务完成率、错误事实采纳率、重复工具调用和 token 成本。消融时固定模型、任务、输入与 token 预算，只改变记忆组件。

**继续追问“只看 Recall@5 为什么不够”：**因为排到候选第五名的证据可能在来源配额、父块展开或 token 预算阶段被截断，模型根本没看到。项目分别有预算前 trace 与真正注入 trace，应以实际可见证据做分析。另外检索命中也不保证模型使用正确，所以必须有端到端任务结果和反事实对照。

**继续追问“有哪些现成证明”：**仓库已有相应单元测试和 benchmark 模块，但不能把“测试名存在”当作“当前机器所有测试已通过”，也不能无运行记录地报提升百分比。面试展示应给出具体测试配置、模型、样本、基线、运行日志与失败案例。文档本节依据源码审查，不宣称进行了收费模型评测。

<a id="chapter-24"></a>

## 记忆模块核验清单：已经实现的事实与最容易误讲的地方

| 核验点 | 当前源码结论 | 面试时应避免的说法 |
|---|---|---|
| 四类记忆 | Working/Semantic/Episodic/Procedural，另有归档和证据支撑 | “四个独立向量数据库” |
| 主会话存储 | `WorkingContext` 事件日志，兼容旧版消息 | “主流程只用 JsonlSessionStore” |
| 默认神经向量 | 真实 BGE 默认关闭，保留 384 维 hash 分支 | “开箱默认已经用 BGE-M3 做语义检索” |
| 默认模型重排 | Cross-Encoder 默认关闭 | “每次检索都调用 bge-reranker” |
| 默认自动学习 | 模型蒸馏默认关闭，规则摘要/显式提取仍可工作 | “每次聊天都会自动模型提炼永久知识” |
| 词法持久化 | SQLite 普通倒排表＋Python BM25 | “当前使用 SQLite FTS5 的 MATCH” |
| 归档切块 | 默认 1,600 字符，按行，无默认 overlap | “固定 512 token＋20% overlap” |
| 归档时机 | 主要在闭合历史压缩时入索引 | “每条历史一写入就必然进入全文索引” |
| 软压缩 | 超 soft 尝试；需模型则暂缓，可能已有落盘副作用 | “soft 一定调用摘要模型/一定无副作用” |
| token 估计 | 工作字符/4；检索 ASCII/4＋非 ASCII×2 | “全程精确 tokenizer 计数” |
| 压缩后大小 | target 是期望，不是硬保证 | “压缩后必定严格小于 30K” |
| 保真性 | 原始证据可追溯；即时摘要仍可能遗漏 | “摘要是信息论意义完全无损” |
| 程序性晋升 | 仅写 candidate，正式技能仍需外部处理 | “模型自动把一次成功升级为可执行技能” |
| 冲突版本 | 通用结构支持 version；普通事实主要规则冲突 | “所有记忆天然具备时间知识图谱” |
| 遗忘 | 删除语义视图，不级联全历史和索引 | “forget 保证所有副本彻底消失” |
| QueryTracker | 实例内缓存、不落盘，未每轮显式重置 | “严格一轮一个全新 cache，写入自动失效” |
| ANN | 可选 FAISS HNSW，缺依赖线性降级 | “项目必需外部 Milvus，或所有环境必然 ANN” |
| 证据与状态 | 可记录来源、confidence、expires_at | “confidence 就是校准正确率，原文引用就是逻辑证明” |
| 安全边界 | 记忆注入作为不可信数据，有路径与作用域过滤 | “一段提示就完全防止历史提示注入” |

建议结合以下测试阅读边界案例：[test_memory.py](../../tests/test_memory.py)、[test_memory_config.py](../../tests/test_memory_config.py)、[test_archive_memory.py](../../tests/test_archive_memory.py)、[test_memory_retrieval.py](../../tests/test_memory_retrieval.py)、[test_memory_distillation.py](../../tests/test_memory_distillation.py)、[test_query_tracker.py](../../tests/test_query_tracker.py)。它们分别包含工件化、追加恢复、配对切分、模型空摘要降级、动作块、跨会话跟随、共享预算、作用域过滤、ANN 重启复用、精确符号、版本冲突、临时指标拒绝和查询进展等测试场景。

<a id="chapter-25"></a>

## 把所有模块串起来：修复加法函数的一条完整轨迹

这一章使用仓库现有 [fix-add fixture](../../evals/fixtures/fix-add/calculator.py) 作为教学案例，工具 JSON 按当前接口编写。下面是**讲解轨迹**，不是本次真实模型运行日志，不代表模型每次都会采取相同步骤。

### 场景和可验证目标

已有代码是：

```python
def add(left: int, right: int) -> int:
    """Return the sum of two integers."""
    return left - right
```

已有 [test_calculator.py](../../evals/fixtures/fix-add/test_calculator.py) 测两个情况：`add(2, 3) == 5` 和 `add(-4, -7) == -11`。用户要求修复加法函数，并保证这两个测试通过。这个任务适合解释所有层的职责，因为“模型理解需求”和“文件真实变更、测试真实通过”可以明确分开。

### 第零步：启动时确定实际环境

入口读取配置，创建 CodingAssistant。本例假设把 fixture 的两个文件复制到独立教学目录，并将该目录作为 workspace，所以后续 `calculator.py` 和 `test_calculator.py` 是工作区根目录下的路径；如果把整个 MiniClaw 仓库作为 workspace，就应改用 fixture 的实际相对路径，并在测试命令中进入相应目录。如果用默认配置，命令在 Docker 执行，有效工作区使用 Direct；教学中也可以显式选 Snapshot，使改动留在会话副本。

这里必须先理解两个路径：宿主机上的 `runtime.host_workspace` 是文件工具访问的目录；模型看到的执行路径和 bash 的容器工作区由 Runtime 映射。源码读取和命令运行必须针对同一份有效文件，否则会出现“修改了宿主一份代码，却测试了容器里的另一份代码”。

再加载项目指令、会话历史、Goal 状态及历史检索证据。若历史里留下一个没有结果的旧调用，先补中断消息，再请求模型。恢复不是跳过这些步骤直接继续生成。

### 第一步：把任务变成模型能处理的上下文

用户输入“修复加法，并让已有两个测试通过”。普通聊天可以走 `assistant.run`；需要持续验收的任务可以先通过 `/goal` 创建 Goal，然后由 Supervisor 发起尝试。

记忆检索此时可以找类似“这个仓库用 unittest”或以前的修复过程，但搜不到也不应该编造历史。与任务无关的长期偏好不应挤占主要证据预算。

`run.started` 记录模型、工具配置、Runtime、指令摘要和本次请求；模型真正收到的是明确的 system、参考证据和当前消息，而不是整个数据库。

### 第二步：模型先读取代码和测试

假设模型输出两次调用：

```json
[
  {"call_id":"read-impl","name":"read","arguments":{"path":"calculator.py"}},
  {"call_id":"read-test","name":"read","arguments":{"path":"test_calculator.py"}}
]
```

传输层完成流式聚合后，AgentLoop 把完整 assistant 消息加入历史，随后按顺序执行两个工具。每个调用都经过工具查找、参数校验和项目指令/审批预检查。

WorkspaceGuard 解析路径并确认访问合法。ReadTool 返回文件内容。即使模型一轮提出两个调用，当前也不是并行。返回的两条 tool 消息分别绑定 `read-impl` 和 `read-test`，不能只把两个文件拼成没有调用 ID 的文本。

读取还可能激活相应目录的项目指令，供下一轮模型请求使用。假设目录规定“不能删测试”，该规则应在后续修改决策前可见。

### 第三步：模型提出精确修改

根据函数名、文档字符串和断言，模型定位到减号问题，提出：

```json
{
  "call_id":"edit-add",
  "name":"edit",
  "arguments":{
    "path":"calculator.py",
    "edits":[
      {"oldText":"    return left - right","newText":"    return left + right"}
    ]
  }
}
```

这次调用能成功，需要同时满足：目标文件在允许路径内；工具对当前角色可用；schema 合法；没有未注入的新项目指令；审批允许；oldText 在原文件中唯一且编辑不重叠；最终提交前未收到取消。

EditTool 先进入该文件的修改队列，再在锁内读取内容、处理换行与 BOM、计算修改，最后使用临时文件和原子替换提交。锁覆盖“读—计算—提交”，否则先在锁外读旧内容仍可能丢失并发更新。结果表示“这个文本替换成功”，仍不等于整个任务成功。

若存在活跃 Goal，成功 edit 会使以前的验证证据失效。逻辑是：对代码做了新改动，就不能继续拿改动前的测试结果宣称完成。

### 第四步：真实运行测试

普通验证的工具调用可以是：

```json
{
  "call_id":"verify-add",
  "name":"bash",
  "arguments":{
    "command":"python -m unittest -v",
    "timeout":60,
    "goal_verification":true
  }
}
```

`goal_verification=true` 是告诉 Goal 系统“把这次命令当成正式验证”，不是让任何命令自动变成可靠测试。实际命令仍必须检验验收条件。

BashTool 交给 Runtime 执行。Docker 模式会启动受限临时容器，挂载有效工作区，等待进程，捕获合并输出，返回退出码。长输出按尾部限额反馈，超大捕获结果另存产物；当前捕获本身也有上限，不能把“完整日志文件”解释成无限保存原始进程输出。

退出码零、工具没有错误且输出确实展示测试通过，才构成可用的验证证据。退出码非零时，工具结果回到模型，模型应该继续修复。若命令根本找不到 Python 或 Docker 镜像，失败属于执行环境，不能修改业务断言来掩盖它。

### 第五步：普通结束和 Goal 完成的区别

普通 run 中，模型可以回复“已将减法修复为加法，两个现有测试通过”，且不再调用工具；循环自然结束。

Goal 模式中，还要提交逐条证据。例如 Goal 的两个验收条件原文恰好是下面两句，则调用可以是：

```json
{
  "call_id":"complete-add",
  "name":"goal_complete",
  "arguments":{
    "final_result":"已修复 add，并完成现有测试验证。",
    "criteria_evidence":[
      {
        "criterion":"add(2, 3) 返回 5",
        "evidence":"最新 python -m unittest -v 的 test_adds_positive_numbers 通过。"
      },
      {
        "criterion":"add(-4, -7) 返回 -11",
        "evidence":"同次验证中 test_adds_negative_numbers 通过，命令退出码为 0。"
      }
    ]
  }
}
```

`criterion` 必须对应实际存储的验收条件，不能随意改写为更容易完成的目标。GoalStore 检查新鲜验证和条件证据，启用 Judge 时还要经过独立评审。通过后写入 complete；没通过则继续推进或按状态停止。

但也要有科学边界：这两个测试只覆盖两个例子，不证明所有数值类型和输入都正确。Judge 也不会凭空把不足的测试覆盖变成形式化证明。

### 第六步：交付和沉淀

产品层收集修改产物、记录 `run.completed`、更新持久运行状态，完成会话记忆收尾。飞书入口通过投递服务发送最终文字；产物提示可以独立发送，避免修改已经成功发出的最终答案。

这次修复过程可形成 episodic checkpoint；适合长期保存的事实要通过语义记忆规则，临时测试输出通常不应该成为永久事实。显式开启模型蒸馏、会话以 completed 收尾且证据支持可复用流程时，可以提出 procedure candidate；当前没有按重复次数自动晋升为正式技能的机制，也不能把一次偶然成功直接提升成所有项目通用技能。

### 从这条轨迹理解五种“成功”

| 层次 | 成功意味着什么 | 不能据此推出什么 |
|---|---|---|
| 模型请求成功 | 收到可解析完整回复 | 代码正确 |
| 工具执行成功 | 参数执行没有报告错误 | 任务验收通过 |
| 测试命令成功 | 当前命令退出码及输出满足判断 | 所有未覆盖情况正确 |
| Goal complete | 满足当前 Goal 的门禁条件 | 真实世界所有需求均被证明 |
| 用户收到交付 | 消息投递成功且可见 | 用户认可方案或线上结果必然稳定 |

这张表适用于许多国内 Agent 面试中的“你怎么保证可靠性”。好的答案会拆开问题，而不是用一个成功率把所有层揉在一起。

<a id="chapter-26"></a>

## 再看失败轨迹：出问题时系统如何接住

### 场景一：第一次写文件才发现局部 AGENTS.md

模型先前没有访问该目录，直接提出 edit。指令预检查发现适用规则未在当前模型请求中注入，返回 `instructions_refresh_required`，这次不修改文件。下一轮动态 prompt 加入规则后，模型再决定是否重试。

解决的是“规则发现太晚”。它会增加一轮模型调用，这是为保证修改前看到规则付出的代价。只在整个 run 开始时扫描根目录，无法处理深层目录的局部约束。

### 场景二：用户批准前任务被取消

工具已通过参数校验，但正在等审批。共享取消信号使等待退出，这个工具没有获准执行；AgentLoop 为当前和后续未执行调用补取消结果。Trace 应看到取消，而不是工具成功。

“弹了审批”不是“已经执行”，“用户没有回复”不是“默认批准”。超时与拒绝都必须落到明确的结果。

### 场景三：bash 运行期间按取消

只取消 Python 协程不够，子进程可能继续写文件。当前 Runtime 会终止宿主进程树，或先清除 Docker 容器再回收本地 CLI。取消之后仍需检查已经发生的副作用；停止未来动作不会撤销过去动作。

如果想提供事务式回滚，需要快照、版本控制、数据库事务或业务补偿，当前取消机制本身不是回滚系统。

### 场景四：工具写成功了，落盘结果前进程崩溃

重启后 RunStateStore 可以标识中断，消息修复补齐未匹配 Tool Call。下一轮应该读取当前文件来确认状态。若发现减号已改为加号，不能再盲目按旧文本替换一次。

这个窗口说明“可恢复”需要谨慎表述：当前支持恢复会话执行的上下文和协议，不承诺外部动作严格 exactly-once。

### 场景五：长日志撑大上下文

即时结果先走工具截断和 Artifact 引用，旧结果在压缩时还可以缩短，旧对话进入可检索归档，必要时才使用模型摘要。后续如果需要某行证据，再按产物路径或记忆检索回查。

压缩必须让模型知道“完整证据在哪里”，否则把日志删短以后，模型只记住模糊结论，无法验证细节。它也必须保留当前任务约束和近期工具协议，不能按字节机械截掉任意一半消息。

### 场景六：召回的旧经验已经过期

历史说“测试命令是 pytest”，当前仓库只有 unittest 测试。检索结果只是候选证据，模型应结合当前文件和项目指令判断。状态、置信度、时间和冲突机制可以降低旧信息干扰，但不会保证所有过期事实自动被发现。

跨用户或跨工作区隔离也不能靠“检索时少搜一点”。应以 metadata/filter 和存储访问边界约束作用域，防止把另一个用户的历史当成本任务证据。

### 场景七：模型停止了，但 Goal 还在 active

Supervisor 将其视为一次尝试结束，更新尝试次数和消耗，判断还能不能继续。如果仍有预算且状态可推进，重新启动一次 run，提示模型继续满足验收。

这是一种带状态的外层监督，不是“模型永远不能停”。等待用户、预算耗尽、明确失败或取消都要有状态出口。默认 turn 上限、Goal 上限、网络尝试上限共同构成不同层的边界。

### 场景八：飞书回复请求超时，但服务端可能已经收到了

本地 outbox 记录投递状态并有重试策略，可以降低丢消息概率；但远端已接收、本地未确认时，重新发送仍可能重复。只有下游真正按稳定幂等键去重，才能进一步收紧这种歧义。

所以当前可讲的是“持久投递记录、幂等键和有界重试”，不能直接说“消息严格只会出现一次”。分布式系统面试很容易围绕这个失败窗口继续追问。

<a id="chapter-27"></a>

## 排障地图：根据现象找到应该看的模块

| 现象 | 先找的证据 | 常见归属 | 下一步 |
|---|---|---|---|
| 模型一直没有文字 | transport 时间、首 token、连接/响应头阶段 | 模型端、网络、排队或只输出工具片段 | 检查超时类别与服务商响应 |
| 一直循环读文件 | 每轮工具序列和上下文 | 检索噪声、任务不清、没有进展判断 | 检查重复动作与可用证据 |
| 工具突然不可用 | 当前 role 的 definitions | 工具策略或动态注册 | 对比允许/禁止/启用状态 |
| edit 匹配失败 | 原文件与 oldText | 内容变化、非唯一匹配或格式差异 | 重读局部内容，不强行整文件覆盖 |
| 第一次写入被阻止 | `instructions_refresh_required` | 局部规则尚未注入 | 等下一轮刷新指令再决策 |
| Docker 找不到文件 | 有效工作区、mount、snapshot | Runtime 映射或环境依赖 | 确认改动和验证是同一个目录 |
| 检索有结果但回答没用到 | ranked_count 与 rendered_count | 预算分配或截断 | 查看实际注入的文本 |
| BGE 没起作用 | 开关、依赖、模型初始化/回退信息 | 可选检索配置 | 核对运行配置，不能只看代码存在 |
| 同一问题再次召回不更新 | QueryTracker cache 与存储写入 | 实例内缓存失效边界 | 检查是否需要刷新或新实例 |
| Goal 拒绝完成 | 最新验证、时间、验收证据、Judge | 验收门禁 | 对缺失条件做真实验证 |
| 用户取消后还有文件变动 | 子进程、线程提交时刻、外部服务 | 已发生副作用或取消传播 | 检查进程和实际文件状态 |
| 会话恢复后模型误重试 | run-state 与未配对 Tool Call | 崩溃窗口和幂等设计 | 先查工作区，再决定重试 |
| 费用显示 0 | 价格配置、usage 返回 | 观测口径 | 对齐服务商账单，别宣称免费 |
| Eval 全绿但线上仍差 | 测试覆盖与数据切分 | 分布差异、泄漏、评分器过松 | 引入代表性失败和未见样本 |

<a id="chapter-28"></a>

## Trace：Agent 的飞行记录仪，记录的不只是聊天

阅读入口：[trace/store.py](../../src/MiniClaw/trace/store.py)、[trace/model_client.py](../../src/MiniClaw/trace/model_client.py)、[trace/analysis.py](../../src/MiniClaw/trace/analysis.py)。先把三个词分清：**Session 是会话持久化；Trace 是执行证据；Eval 是对证据和结果作出判定。** 用户看到一段回答，不代表能知道 Agent 为什么这样回答；Trace 补上这一层。

假设用户要求修复一个函数，Agent 搜索、读文件、修改、运行测试，再说“完成”。Trace 要回答：读了哪个文件；模型发出了什么工具调用；运行在哪个后端；审批允许还是拒绝；测试有没有真正执行；这次模型调用用了多少 token；中途有没有压缩、重试和换供应商。如果只是保存最终回复，上面这些问题基本无法回答。

### 一条事件到底长什么样

`TraceRecorder` 把事件逐行追加到会话目录下的 `trace.jsonl`。一行是一个 JSON 对象，主要字段如下。

| 字段 | 人话解释 | 为什么需要 |
|---|---|---|
| `schema_version` | 记录格式版本，目前为 1 | 读取时识别兼容的数据 |
| `trace_id` | 渠道和会话身份计算出的稳定哈希 | 把同一会话的多次运行串起来 |
| `run_id` | 每次运行的独立标识 | 多轮对话不会混成一次统计 |
| `event_id` | 单个事件的 UUID | 精确定位某一次动作 |
| `parent_event_id` | 可选父事件标识 | 格式支持事件关联；不能据此声称每条事件都有完整父子图 |
| `type` | 如 `model.request`、`tool.call` | 区分模型、工具、审批、记忆等行为 |
| `timestamp` | UTC 时间 | 建立时间线和定位问题 |
| `data` | 事件具体内容 | 记录模型上下文、用量、工具结果等 |

写入使用 `MemoryFileLock` 防止同一个文件被并发追加坏掉。这里采取“可观测性失败不阻断主任务”的策略：写日志异常被捕获，放入 `last_error`。这让一次磁盘日志故障不至于直接终止用户任务，但也意味着 **“没有日志”不能直接推导为“没有发生”**。上线时还需要监控日志写入异常和磁盘容量。读取函数会跳过无法解析的行及不符合基本格式的事件，这提高容错性，也可能掩盖日志缺损，需要额外的完整性审计才更严谨。

### 为什么模型调用要包一层 TracingModelClient

它是原有 `ModelClient` 的装饰器：向内发送同一个 `ModelRequest`，向外继续转发流式事件。在 `finally` 中统一记录 `model.request`，所以正常完成、异常、取消都尽量留下记录。网络重试和供应商回退另记为 `model.transport`，通过 `request_id` 与逻辑模型请求关联。

一次请求可记录完整消息、工具定义、温度、模型配置和最终输出，以及持续时间、首 token 时间、input/output/cached tokens、停止原因和错误。`purpose` 特别有用：主 Agent 思考、上下文压缩、记忆加工、评审模型，都可以分别归因。否则很容易把“记忆模块多调用了几次模型”误判成“主模型变贵了”。

成本按配置价格估算：

```text
未缓存输入 = max(输入 token - 缓存 token, 0)
估算美元成本 = (未缓存输入 × 输入单价
             + 缓存输入 × 缓存单价
             + 输出 token × 输出单价) / 1,000,000
```

这是观测口径，不是供应商账单保证。价格配置为零时得到零美元，不等于免费；失败请求如果供应商未返回 usage，系统也不能凭空知道实际消耗。面试时应主动说明这个限制。

### Trace 如何脱敏，以及为什么不能说“绝对不会泄密”

`sanitize_trace_value` 会按敏感字段名掩码，处理常见 token 字符串、Bearer 值和部分环境赋值；跳过二进制，处理循环对象，并限制超长字符串。单个字符串超过 200,000 字符会截断，部分大 `data` 字符串会被省略。

这种规则很实用，但匹配不到所有业务秘密，也不能理解所有自然语言中的隐私。完整上下文本身可能包含代码、订单内容或用户资料。公开展示前应做专门筛选；生产系统还需要访问控制、留存周期和删除机制。另一个代价是：经过脱敏和截断的 Trace，不保证与原始模型输入逐字相同，重放时必须知道这一点。

<a id="chapter-29"></a>

## 从失败到改进：评测闭环的完整构造

### MiniClaw 有两种不同的“重跑”

阅读入口：[trace/replay.py](../../src/MiniClaw/trace/replay.py) 和 [evaluation/runner.py](../../src/MiniClaw/evaluation/runner.py)。这是项目答辩中最容易讲错、也最值得讲清楚的地方。

| 比较项 | Trace 模型边界重放 | 完整 Eval 执行 |
|---|---|---|
| 输入 | 过去记录的模型请求上下文 | 固定 fixture、任务提示、环境和验收条件 |
| 做什么 | 将历史上下文再次送给模型 | 真正启动 CodingAssistant，让 Agent 操作工作区 |
| 工具会执行吗 | 不执行；历史工具返回仍在历史上下文里 | 会执行，取决于测试环境和工具策略 |
| 能验证什么 | 同一个决策点换提示词/模型/工具描述后怎样回答 | 最终文件、命令结果、动作顺序、副作用和预算 |
| 不能证明什么 | 新决策后产生的完整新轨迹是否成功 | 未覆盖任务、未知攻击和真实生产流量是否成功 |

`replay_trace` 的模式字符串直接写成 `model-boundary-replay`。它逐个读取历史请求，每次独立向模型发送记录中的上下文；上一条重放产生的新回答不会自动替代下一条历史请求中的旧回答。因此它适合问：“在这段已知证据前，改后的模型会不会继续误判？”不适合回答：“改后的 Agent 从头执行一定能把项目修好？”

重放可替换首个 system prompt，切换模型/供应商，选择启用或禁用哪些工具，并覆盖工具描述、参数定义。报告里 `tool_call_sequence_match` 只比较工具**名称序列**，并没有比较全部参数、路径和实际文件副作用。两次都调用 `write`，一次写 `README.md`、另一次写错文件，名称匹配仍可能为真。

失败转用例由 `create_eval_case` 完成：提取失败运行、来源、配置哈希、历史请求边界、失败签名和可用评分规则。来自 benchmark 的答案题可恢复规范化匹配或 rubric；对 benchmark 模型边界还会过滤 judge/grader/evaluator 等请求，避免把“裁判模型的回答”当成“Agent 的答案”。普通真实失败如果没有 judge，只能按模型边界是否出错判断，置信标记是 `model-boundary-only`。生成的数据里也明确写着：加上 workspace fixtures 和确定性检查，才能当成完整任务正确性评测。

不要把生成用例里的 `forbidden_failure_signature` 当成已经执行的完整反作弊证明；重放主要按边界错误和 judge 结果判定。正确闭环应当是：**失败 Trace → 确认失败类别 → 生成边界实验 → 建立可执行 fixture 与验收器 → 修改方案 → 回归与留出评估**。Trace 可帮助诊断，但不能替代验收器。

### Eval 数据模型：不是一个 prompt 加一句“答得不错”

阅读入口：[evaluation/models.py](../../src/MiniClaw/evaluation/models.py)。

`EvalSuite` 是一套题，`EvalCase` 是一个任务，`EvalPhase` 是任务内一个阶段，`EvalCheck` 是一个验收条件。Suite 可以通过 `includes` 组合其他 suite；加载器检查版本、重复 ID、递归包含循环、未知预算与未知检查类型。`evals/full.json` 组合 `smoke.json`、`regression.json` 和 `resilience.json`。

一个 case 可声明：复制哪份 fixture；问几轮；阶段间是否共用会话；是否创建长期目标；运行环境；运行超时；重复次数；最低通过率；能力标签；必须满足的文件、Trace、预算等检查。`session_mode=isolated` 表示阶段分开存会话，`shared` 表示阶段共用会话目录；不同阶段依然使用同一 attempt 的工作区，所以 **isolated session 不等于阶段之间文件系统重置**。每次 repetition 才有独立 attempt 工作区。

一次执行可以按这条链理解：

```text
加载 Suite 并选择 cases
  → 每个 Case 创建 attempt 目录、复制 fixture、记录初始文件哈希
  → 合并基础 / Suite / Case 环境
  → 按顺序执行 phases，收集 final、goal、run_id、Trace
  → 比较执行前后工作区变化
  → 跑确定性检查或 rubric judge
  → 算预算、维度、覆盖率
  → 汇总重复结果并和 baseline 比较
  → 保存结果、摘要和可读报告
```

环境默认策略要单独注意：评测 attempt 在没有显式设置时默认 `MINICLAW_APPROVAL_POLICY=allow`、`MINICLAW_GOAL_JUDGE_ENABLED=false`，是为了可控运行；特定审批和目标题会用自己的设置。不能拿普通 allow 用例的通过，证明真实 ask/deny 安全策略可靠。

### 五个维度：结果正确只是第一层

| 维度 | 需要回答的问题 | MiniClaw 的验证方式 |
|---|---|---|
| outcome | 交付物真的对吗 | 文件、命令退出码、最终文本/JSON、rubric |
| process | 走过必要过程吗 | `trace_event`、`trace_sequence`、目标状态 |
| efficiency | 是否在合理成本内完成 | token、费用、模型请求、工具次数、耗时预算 |
| safety | 是否出现不允许的副作用 | 拒绝/审批事件、敏感路径和文件变化限制 |
| reliability | 重跑、取消、网络故障后表现怎样 | repetitions、多阶段恢复、故障注入 |

确定性检查的具体用法也要知道：

- `file_*` 检查存在、缺失、内容、正则；路径解析必须落在规定工作区。
- `command` 用参数数组及 `shell=False` 执行，比较退出码，可附输出包含或正则；这里是**评测进程在宿主运行的验收命令**，不自动走 Agent 的 Docker runtime，测试代码的运行边界必须自行设好。
- `final_*` 可以包含、等于、规范化等于、正则、JSON 字段匹配；“有关键词”比“语义事实正确”弱。
- `trace_event` 依据事件类型及字段条件统计次数；`trace_sequence` 检查按顺序出现的**子序列**，允许中间插入其他事件。
- `workspace_diff` 依据前后哈希检查创建/修改/删除路径，支持必改、禁改、允许改的 glob 与最大改动文件数；它主要回答“改了哪里”，不是证明补丁业务语义正确。
- `llm_rubric` 要裁判返回 `passed/score/reason` JSON，并同时满足阈值、passed 和无调用错误。要防止被评回答中的注入、裁判偏好和随机性影响评分。

单次 `passed` 取决于没有 case 级异常、有执行阶段、所有 required 检查通过。因此可选检查失败时 case 仍可通过。默认检查都是 required；只有明确标成 false 才是非阻断项。不要误读为“所有维度都 100% 才会通过”。

例如“修复加法”这个可执行思路，比只判断最终回答中有“修好”强很多：

```json
{
  "version": 1,
  "name": "learning-add-check",
  "cases": [{
    "id": "fix-add",
    "category": "coding",
    "fixture": "fixtures/fix-add",
    "prompt": "修复 calculator.py 的加法错误，并运行验证。",
    "checks": [
      {"type": "command", "command": ["python", "-m", "unittest", "discover", "-p", "test_calculator.py"], "dimension": "outcome"},
      {"type": "workspace_diff", "required_paths": ["calculator.py"], "forbidden_paths": ["test_calculator.py"], "dimension": "safety"}
    ],
    "budgets": {"max_model_requests": 12}
  }]
}
```

这是教学样例，不是本轮新执行的测评。它还可继续加强：加“确实调用了测试工具”的过程检查，覆盖负数/零等输入，保留独立隐藏验收；否则 Agent 可能改成只适配有限断言的实现。

### 取消、重试和重启怎么测

`EvalPhase.control` 支持定时取消、指定工具开始后取消；支持模拟 HTTP 状态、网络断开、Retry-After 和固定成功文本；也支持审批同意/拒绝/超时及延迟、恢复 goal。这意味着可以让一条任务稳定走到原本偶发的故障分支，然后验证状态和副作用。

这类注入能证明某个受控故障路径被处理，不能证明供应商所有网络故障都被覆盖。“模拟审批超时返回 false”也不等同于一次真实用户在聊天软件中等待超时的全链路测试，两者最好配合。完整基准涉及网络或 Docker 的题，还需要对应环境确实可用。

### 指标的真实含义：这部分比背术语更重要

每个 case 重复 `k` 次，单次成功数量为 `c`：

```text
case.pass_rate = c / k
case.passed = pass_rate ≥ min_pass_rate
case.pass_at_k = (c > 0)       # 本次重复中至少成功一次的布尔值
case.pass_all = (c == k)
case.stable = (c == 0 或 c == k)
```

这里的 `pass_at_k` 不是论文里常见的 `1 - C(n-c,k)/C(n,k)` 无偏估计器，`pass_all` 也不是由独立概率拟合出来的理论 `p^k`。`stable` 包括“一直失败”；稳定性高不能单独当成好成绩。例如 3 次全部失败很稳定，业务成功率仍为零。某题重复 10 次只过 1 次，允许多次重试可以碰到成功，但线上用户往往只给一次机会。

Suite 的 case 通过率与 attempt 通过率也不同：一个题重复十次、另一个只测一次，在 case 通过率中二者同权，在 attempt 通过率中前者权重大。五维分数先在 case 中汇总再取均值，也不应随意读成全部断言的全局成功率。

Trace 指标区分 agent token、memory token、compaction token，记录重试、fallback、审批、工具阻断和压缩层次。rubric judge 有独立 `judge_*` token 字段，费用会并入 `cost_usd`；不能假定普通 `total_tokens` 就覆盖全部裁判 token。性能看板另一个细节是 `_combine_metrics`：跨 attempts/cases 汇总时，p50/p95 字段取各子结果对应分位数的**最大值**，没有把所有原始延迟样本合并重算。它是保守的子组分位数摘要，不是严格的全局 p95。

`coverage_complete` 也有明确边界：它检查要求的维度是否有计分、要求的 capability 是否有 case 声明，并非自动检查每行实现或证明该能力被完整触发。能力标签需要人工正确标注，Trace 过程断言和单元测试还要继续兜底。

### Baseline 与回归告警是怎样工作的

`_regression_alerts` 会检查总通过率、重复尝试通过率、各维度分数是否下降至少 5 个百分点；检查 token 超过旧值 15%、成本超过 20%、p95 延迟超过 25% 等；并检查旧 case 消失、旧 case 从过到不过、旧 capability 消失/退化。CLI 在存在 regression alerts 时退出码为 2；普通失败或覆盖不足为 1。仓库已经定义了下述 GitHub Actions 工作流，但本次没有访问远端运行记录，不能宣称这些任务已在托管平台成功跑过。

### 已有 CI 工作流怎样连接这些检查

[.github/workflows/eval.yml](../../.github/workflows/eval.yml) 定义两项 job：

- `offline-contracts`：相关源码、测试、eval 配置变更触发 push/PR 检查，也支持手动触发；在 Ubuntu、Python 3.11 上安装依赖，跑 `pytest tests -q`，然后核验固定评测 portfolio manifest，job 时限 10 分钟。
- `live-regression`：只在手动 workflow_dispatch 且显式打开 `live_regression` 时运行，依赖离线 job 成功；读取仓库 secret/变量，安装 benchmark 依赖、构建受限镜像，再以 `--jobs 4` 执行 `evals/full.json` 并比较保存的 baseline。时限 30 分钟，结束后即使失败也尝试上传 Eval 证据。

因此准确说法是“已提供自动离线门禁与手动真实模型回归的工作流定义”。离线检查并不会因普通 push 就自动消耗真实模型费用。工作流的镜像标签写作 `py313-bench`，但构建引用的 Dockerfile 当前基础镜像仍是 Python 3.11；镜像标签是名字，不能代替实际运行时版本检查。

仓库自带 [gpt-5.6-luna-full-v1.json](../../evals/baselines/gpt-5.6-luna-full-v1.json)，文件记录 `generated_at=2026-09-05T06:25:03.432393Z`，22 个 case 通过、23/23 attempts 通过；同时 process 维度并非满分。这里引用的是**现存历史基线文件的自报记录**，本轮没有为了写文档重新运行模型测评，也没有独立核验上游账单。22 个固定回归题通过，不能变成“所有 Agent 任务 100% 成功”“公开基准第一”或“完整安全证明”。

### 数据切分：为什么修复过的题不能继续当盲测

阅读入口：[evaluation/splits.py](../../src/MiniClaw/evaluation/splits.py)、[manifest.json](../../evals/splits/v1/manifest.json)。项目把评测分成四个轨道：

| 轨道 | 用途 | 当前 manifest 声明 |
|---|---|---|
| regression | 修改代码后必须守住已知能力，允许看题修复 | 22 个固定回归 case |
| development | 观察分布、定位坏例、做消融，允许分析 | 60 LongMemEval-V2 + 72 RepoGuard 执行 + 40 AI Efficiency 执行，共 172 |
| heldout | 冻结后做发布验证，不用单题反复调参 | 80 个 LongMemEval-V2 问题 |
| production | 真实会话按时间窗口采样、观察漂移 | manifest 目前写的是未来采集计划 |

`274 = 22 + 172 + 80` 是声明执行量，不是 274 个不同原始任务，也不是本轮实际执行次数。RepoGuard 的 12 个任务各做 clean 与 5 种载体共 72 次；AI Efficiency 20 个任务各做两组共 40 次。实际 API 请求数、重试数和重复运行数通常还会更大。

LongMemEval-V2 选择先按 domain 与 question type 分层，使用固定种子的哈希排序稳定抽样；历史结果暴露过的题、失败题优先用于开发集。留出集排除已暴露题以及与开发/已暴露题同规范化 query family 的题。规范化会忽略大小写和部分标点，所以能避开简单重复；它不是语义去重模型，不能识别所有改写、相同答案模板或近义问题。

代码选择的泄漏单位是 query family，而不是把所有共享检索证据的题都塞进同组：这个数据集本来共享检索语料，作者把语料视为测试时可检索证据。这个决定适合该设定，但不意味着任何 RAG benchmark 都可随便共用标签或私有答案。

还要看清 `validate_split_manifest` 的实际力度：它检查 manifest 中声明的泄漏数为零、选择文件存在、曝光登记结构合法、failed 是 exposed 子集、heldout 与 exposed/development ID 不交叠、ID 文件与登记逐项一致。**它不会重新读取全部问题重算语义相似性，也不会自动重新计算源文件哈希并与 manifest 比对。** 源 commit 和 SHA-256 是溯源信息，要在发布流程额外验证；未来看过新坏例也必须更新曝光登记，否则“冻结”只是文档上的词。

<a id="chapter-30"></a>

## Benchmark 适配层：接上一个榜，不代表完成了榜上的全部评测

MiniClaw 的 `benchmark` 目录是把不同数据、任务协议、评分器接到自身模型/工具/记忆上的适配层。不同适配器测的系统边界不同，不能把一个分类器分数当成整个 Coding Agent 的分数。

| 适配文件 | 实际接入及观察目标 | 关键边界 |
|---|---|---|
| [memory_agent_bench.py](../../src/MiniClaw/benchmark/memory_agent_bench.py) | 用 hybrid/compaction 模式处理记忆任务，支持事实文档、检索和 Agent 搜索工具 | 默认报告包含 substring match；答案子串命中不等于复杂语义完全正确；缩放压缩 profile 要与生产 profile 区分 |
| [longmemeval.py](../../src/MiniClaw/benchmark/longmemeval.py) | 会话记忆、跨会话、时间、知识更新、拒答；有 oracle/compaction/summary-only 模式 | `oracle` 模式关闭压缩并保留传入历史；`--data` 可传官方 oracle 或 S 数据，模式名本身不会自动按答案标签过滤历史。oracle 数据条件与完整 S 条件必须区分，不能当作同等检索难度 |
| [longmemeval_v2.py](../../src/MiniClaw/benchmark/longmemeval_v2.py) | web/enterprise 轨迹检索、父级证据读取、Agent 主动检索及官方 checker 适配 | 官方仓库/数据版本与问题过滤必须固定；部分 checker 需要模型；只测选中切片不等于整个榜 |
| [contextweave.py](../../src/MiniClaw/benchmark/contextweave.py) | 实现外部 memory component 协议；灌入已完成会话，在 coding 任务开始前取相关记忆 | 记忆准备代码使用不调用模型的 `_UnusedModelClient`；后续 coding 执行是另一阶段；官方容器与评分脚本还需单独运行 |
| [ai_efficiency.py](../../src/MiniClaw/benchmark/ai_efficiency.py) | 固定任务比较 `full` 与 `artifact-off` 两组，用成本、正确性、工具结果判断产物外置是否有效 | 配对任务、相同模型和运行约束才可归因；不能只比平均 token 而忽略失败率 |
| [code_compression.py](../../src/MiniClaw/benchmark/code_compression.py) | SWE-bench 风格 coding 任务比较 `legacy-summary-recent` 与 `layered-current` | 固定 coding scaffold、模型、提示、runtime、审批和 turn cap；产出 patch 不等于官方 harness 判定 resolved |
| [swebench_runner.py](../../src/MiniClaw/benchmark/swebench_runner.py) | 包装官方 SWE-bench Docker harness，修正 Windows 写 Linux 文件的 LF 换行 | 只是官方评分入口兼容包装，不是另一个 Agent，也不是自行定义的通过标准 |
| [agentdojo.py](../../src/MiniClaw/benchmark/agentdojo.py) | 把 MiniClaw AgentLoop 和模型接到 AgentDojo 虚拟工具；保留上游环境、攻击和 scorer | AgentDojo 拥有虚拟环境和工具；这不是把全部真实文件 runtime/审批栈都照搬过去 |
| [repoguard.py](../../src/MiniClaw/benchmark/repoguard.py) | 真实 CodingAssistant、ToolExecutor、ApprovalGate、WorkspaceGuard、Docker，观察攻击尝试、完成与合法任务效用 | `normal` 与 `compromised` 是不同实验条件；必须连同 clean/attacked utility 报告，全部拒绝不等于好 Agent |
| [agentsafety.py](../../src/MiniClaw/benchmark/agentsafety.py) | 读取官方系统提示，分类 allow/ask/refuse，记录模型边界 | 判断建议类别的能力，不等于真实危险动作已被执行器阻断；解析错误和分母要单列 |
| [toolsafe.py](../../src/MiniClaw/benchmark/toolsafe.py) | TS-Bench/AgentHarm 轨迹的待执行动作判定，区分 0/0.5/1 三类 | 固定分层切片且每条 interaction 至多一步；三分类、strict/loose 二分类口径不同；有效输出分母与解析错误不能混淆 |

如果面试官问“你做过哪些实验”，要按四档作答：①源码已有适配；②仓库有历史结果记录；③自己本轮实际运行且可展示原始结果；④计划做但未运行。本轮文档工作核验了实现和现存基线，没有重新跑收费 API、下载全量榜或执行全部官方容器。不要把 ① 说成 ③。

对应测试证据包括 [test_trace.py](../../tests/test_trace.py) 的重放不执行工具、失败转评测、模型裁判过滤与脱敏；[test_evaluation.py](../../tests/test_evaluation.py) 的路径不能逃逸、Trace 顺序、成本归因、预算与回归；[test_eval_splits.py](../../tests/test_eval_splits.py) 的稳定分层和登记验证。测试文件存在说明有针对这些场景的验证设计；是否在当前环境通过，应以本轮实际测试报告为准。

<a id="chapter-31"></a>

## 中国国内 Agent 开发面试：有来源的问题与能落地的回答

### 来源说明：什么是真的，什么是本文推导的

下面的“原问归纳”来自 2026-09-05 读取的六篇牛客公开原帖正文，作者自述的岗位覆盖国内 Agent 开发/实习和 AI 应用开发。这些**不是公司官方发布的真题**；能核验的是“原帖确实写了这些问题”，无法独立核验作者面试身份、经历及转述精确度。本文回答根据本地 MiniClaw 源码重新编写，追问是工程推演，不伪装成原作者答法或公司标准答案。

| 编号 | 已读原帖 | 能直接支撑的主题 |
|---|---|---|
| S1 | [字节跳动9.3 Agent开发一面面经](https://www.nowcoder.com/discuss/925342611194286080)；作者“小妞脂”，标题自述 9.3 一面，页面于核验日显示“昨天 11:04 已编辑” | Harness、长任务变慢、工具可靠、RAG、结构化、压缩、算法 |
| S2 | [pdd agent二面](https://www.nowcoder.com/feed/main/detail/f5e7351df8364147ac8da085b99d9d18)；作者“在抱佛脚的芝士很完美”，正文自述 8.25 面试约 40 分钟 | 隔离、准确性、评测、完整业务流、框架、线程安全 LRU |
| S3 | [帆软二面 面经](https://www.nowcoder.com/feed/main/detail/b22f8f811465486aa912b80efe8f5cd1)；作者“莫莫牛”，页面显示 09-03 12:53，为作者归纳整理 | 准确率提升闭环、Benchmark、指标定位、组件必要性、并发资源 |
| S4 | [字节AI Agent实习面经](https://www.nowcoder.com/feed/main/detail/8a553bb6ea8445d0b0abe11e87614cea)；作者“随波逐牛”，正文自述 8.13 一面、8.16 二面，页面显示 08-18 16:43 | Coding Agent、ReAct/Plan、记忆、缓存、MCP、多 Agent、文件冲突、平衡树 |
| S5 | [深信服Agent开发实习生一面二面](https://www.nowcoder.com/feed/main/detail/14b2c379ae434062a009aefea9fc5df9)；作者“杰尼龟准备秋招中”，页面显示 09-02 10:42，正文自述一两周前面试 | 工具注册/检索、端到端流程、框架比较、最长无重复子串 |
| S6 | [9.4 某小厂 AI Agent hr+技术面](https://www.nowcoder.com/feed/main/detail/10b2fcaf73d2401f8636bd0459e1cd08)；作者“牛客826994329号”，公司未具名，页面于核验日显示“昨天 11:07 已编辑” | 选型、海量 PDF RAG、embedding、Transformer、注意力、相似度/BM25 |

上表已列出可直接打开核对的原帖，回答和追问分别标明来源与推演关系。这些题考“系统发生什么、为什么、怎样量化与兜底”，不只是问定义。回答时可以用下面的技术表述，但不能把尚未完成的扩展说成已经上线。

<a id="interview-i01"></a>

### I01：介绍一下你的 Coding Agent。Harness 到底是什么？

**来源：S1、S4 原问归纳。**

**回答：**“MiniClaw 是围绕模型构建的可执行 coding assistant。模型负责根据当前上下文产出文本或工具请求；AgentLoop 负责反复调用模型与消费工具结果；CodingAssistant 把项目指令、记忆、工具、运行环境、审批、会话存储和目标监督组装起来。CLI/飞书负责接入和交互。Trace 与 Eval 负责解释过程、检验结果。Harness 可以理解为让模型在现实任务里可靠运行的这套周边系统，包括上下文组织、执行循环、工具协议、隔离、重试取消和评测。它不是某个独立神奇模型。”

**追问与陷阱：**如果问“去掉 Harness 还剩什么”，回答仍可调用模型聊天，但失去可靠的动作执行、状态恢复与验收。别说“项目亮点是用了 RAG、MCP、多 Agent”；MiniClaw 的实际完整度要按源码说明，MCP 与多 Agent 扩展尤其不能冒领。

**源码落点：**[CodingAssistant](../../src/MiniClaw/coding_agent/assistant/coding.py)、[AgentLoop](../../src/MiniClaw/agent/loop.py)。

<a id="interview-i02"></a>

### I02：ReAct 和 Plan 有什么区别，什么时候选哪种？

**来源：S4。**

**回答：**ReAct 的核心是结合观察逐步选择动作，工具返回新证据后再决定下一步，适合环境不确定的调试和检索。Plan 更强调先拆目标、记录步骤/依赖，然后执行并按反馈更新，适合多阶段交付；它同样需要重新规划。MiniClaw 的基础 AgentLoop 体现“模型决策→工具→观察→继续”的循环，goal supervisor 在循环外维持目标与验收。长期目标监督不自动等于完整的 DAG planner，当前不能声称有一个任意任务图调度引擎。

**追问与陷阱：**“计划错了怎么办？”保留高层验收目标，允许局部重排、记录失败证据，避免让过期步骤硬覆盖新事实。也别将 ReAct 等同于必须展示模型私有推理；工程上真正需要的是动作、观察与可审计决策证据。

**源码落点：**[AgentLoop](../../src/MiniClaw/agent/loop.py)、[目标监督](../../src/MiniClaw/coding_agent/goal/supervisor.py)。

<a id="interview-i03"></a>

### I03：怎么保证结构化输出和工具参数可靠？

**来源：S1。**

**回答：**分三层：生成时提供清晰且不过载的 schema；接收时解析并验证类型、必填、枚举、范围；执行前验证业务权限、路径和前置状态。JSON 能解析只证明语法，不证明操作正确。MiniClaw 将模型工具请求表示为 `ToolInvocation`，工具通过执行器调用，并在相应工具与 runtime 中做参数和边界检查；审批是执行权限的一层。错误要反馈成明确结果，有限次修正，避免无限自动重试有副作用的操作。

**追问与陷阱：**“输出是合法 JSON，但是用户 ID 错了怎么办？”用服务端鉴权和可信上下文绑定用户，不让模型自由指定租户。结构化评分还需要量表、证据字段、未知值约定和评分校准。不能只说“temperature=0 就稳定”。当前 ToolExecutor 确实递归校验了类型、必填、枚举、数值范围、字符串长度、数组元素及额外字段等约束，但这是自实现的 schema 子集，不能声称覆盖 JSON Schema 全部标准。

**源码落点：**[工具结果类型](../../src/MiniClaw/coding_agent/tools/base.py)、[执行器](../../src/MiniClaw/coding_agent/tools/executor.py)、[审批](../../src/MiniClaw/coding_agent/approval/gate.py)。

<a id="interview-i04"></a>

### I04：工具从 10 个增加到 100 个，Prompt 太长而且老选错，怎么办？

**来源：S4、S5。**

**回答：**先治理工具语义：名字可区分、描述写触发条件和不适用条件、schema 不重复、相近工具合并合理粒度。再按任务域/权限筛选候选，做工具目录检索，第一轮只提供少量高相关工具，缺失时允许继续发现。工具检索也要评估 Recall@k、误调用率和调用成功率，不能只看描述 token 降了多少。MiniClaw 现有工具工厂和工具定义可作为接入点，但没有证据时不能说已经实现百工具动态注册中心和工具向量路由。

**追问与陷阱：**正确工具没被召回怎么办？保留目录查询和扩召回退路；依赖工具必须一起暴露。小模型路由、向量检索都有可能漏召回，不应成为无法纠错的单点。

**源码落点：**[工具工厂](../../src/MiniClaw/coding_agent/tools/factory.py)、[工具类型](../../src/MiniClaw/coding_agent/tools/base.py)。

<a id="interview-i05"></a>

### I05：MCP 是什么？与 Function Calling、Tools、Skills 有什么关系？

**来源：S4、S5。**

**回答：**Function Calling 是模型表达“我要调用某工具及参数”的接口能力；Tool 是可执行能力的抽象；MCP 是宿主应用与外部服务暴露工具、资源、提示等能力的标准化协议。常见调用链是宿主建立 MCP client/server 会话、初始化与能力协商、获取工具目录、把定义转成模型可用格式、接收模型调用、发给 MCP server、把结果转回模型。Skills 更像任务说明与可复用流程/资源组织，具体机制依平台而定，不是 MCP 的另一种名字。

**MiniClaw 边界：**当前源码以本地工具接口和工厂为主，没有因此自动具备 MCP client/server。若要接入，新增适配器，同时保留统一权限、取消、超时、Trace 与结果截断；从 server 发现了工具不代表用户已授权执行。

**追问与陷阱：**“用了 MCP 就安全了吗？”协议解决互操作，鉴权、最小权限、结果注入和敏感操作审批仍要自己负责。

**源码落点：**[本地工具工厂](../../src/MiniClaw/coding_agent/tools/factory.py)、[模型工具调用类型](../../src/MiniClaw/llm/types.py)。

<a id="interview-i06"></a>

### I06：RAG 离线入库和在线问答各怎么做？

**来源：S1、S6。**

**回答：**离线链路是文档接入→解析/OCR→清洗和版本识别→按语义结构分块→保留来源与权限→embedding/关键词索引→质量检查。在线链路是识别意图与权限→必要的 query 改写→混合召回→融合/重排→在预算内组装证据→模型回答→核验引用和可答性。检索和生成要分别测：没召回证据、证据过期、召回了却回答错，是三类不同故障。

**MiniClaw 边界：**它有本地记忆和混合检索机制，可以解释这些原则；不是开箱即用的 TB 级金融 PDF 知识库平台。OCR、表格版面、文档级 ACL、增量大规模索引服务属于需新增的工程。

**追问与陷阱：**“有 RAG 就没幻觉吗？”没有。召回可错、证据可错、模型可忽视证据；用引用、拒答与验收缓解。别把“检索命中”直接当作“回答正确”。

**源码落点：**[检索实现](../../src/MiniClaw/coding_agent/memory/retrieval.py)、[记忆管理](../../src/MiniClaw/coding_agent/memory/manager.py)。

<a id="interview-i07"></a>

### I07：600GB—1TB 的金融 PDF 怎样建 RAG？Chunk 怎么定？

**来源：S6 场景题归纳。**

**回答：**先抽样估算电子/扫描比例、页数、表格密度、更新频率和权限粒度；压缩包大小不是文本 token 数。解析按类型分流，扫描件 OCR，表格保留结构，跨页标题和页眉页脚单独处理。优先按章节、段落、表格切块，再按模型限制设长度和少量重叠；小块召回、大块补足上下文。以 chunk 内容哈希缓存 embedding，只重算变化块；任务队列分批处理，持久化进度和失败样本。索引版本化发布，支持增量、删除和回滚。

**追问与陷阱：**“为什么不是固定 512 字？”合适大小依语料、语言、embed 模型与问答粒度；用召回/正确率/时延联合选。PDF 页码、段落边界和引用来源不能在清洗时丢掉。以上是扩展设计，MiniClaw 未实现这套 TB 级链路。

**源码落点：**[当前本地检索边界](../../src/MiniClaw/coding_agent/memory/retrieval.py)、[记忆产物](../../src/MiniClaw/coding_agent/memory/artifacts.py)。

<a id="interview-i08"></a>

### I08：为什么同时用 BM25 和向量？余弦相似度怎么算？

**来源：S6。**

**回答：**余弦相似度 `cos(q,d)=(q·d)/(||q||·||d||)` 比方向相近程度；归一化后可用点积。向量擅长一定程度的语义近似，关键词检索对函数名、错误码、产品编号、明确术语很有价值。BM25 用词频饱和、逆文档频率和文档长度归一化计分，不是简单词数相加。混合召回后可做基于排序的融合，例如 RRF 使用 `Σ 1/(k+rank)`，不用硬假设两种原始分数同尺度。

**MiniClaw 边界：**本地 `memory/retrieval.py` 是讲清混合检索的实际入口；其向量实现和开关应按配置说明，不能只因有向量接口就说默认跑了某个大型 embedding 模型。

**追问与陷阱：**“权重 0.7/0.3 凭什么？”用带标签的开发集验证，分任务类型看召回，不凭感觉拍定；调好后在留出集测泛化。

**源码落点：**[混合检索](../../src/MiniClaw/coding_agent/memory/retrieval.py)、[记忆配置](../../src/MiniClaw/coding_agent/memory/config.py)。

<a id="interview-i09"></a>

### I09：Context 和 Memory 有什么区别，完整记忆系统怎么设计？

**来源：S4。**

**回答：**Context 是本次请求实际送入模型的有限信息；Memory 是请求之外可持续保存、按需召回的信息资产。记忆要区分当前工作状态、发生过的事件、稳定事实、可复用流程；还要存来源、时间、适用作用域、置信度和失效条件。MiniClaw 把 working/episodic/semantic/procedural 以及 archive/artifacts 分成模块，MemoryManager 组织读写和注入。保存全部聊天不等于记忆有效，关键是新问题能否用有限预算取回正确证据。

**追问与陷阱：**“用户偏好改了怎么办？”保留版本/冲突处理和新旧关系，不能让旧偏好无限压过新指令。检索出的记忆是证据，不应被抬成更高优先级的系统政策。对模型总结出的事实尤其要保留回原文的路。

**源码落点：**[记忆管理](../../src/MiniClaw/coding_agent/memory/manager.py)、[语义记忆](../../src/MiniClaw/coding_agent/memory/semantic.py)。

<a id="interview-i10"></a>

### I10：上下文越来越长，怎么压缩，还不忘用户最早的要求？

**来源：S1、S4。**

**回答：**先预留输出空间和安全余量，监测使用量；优先减少无用工具输出、去重复内容，保留系统约束、用户目标/验收、未解决问题、关键路径和最新执行状态。大量原文移到可恢复产物/档案，摘要记录索引和证据位置，真正需要时再读取。必要时用结构化摘要保留当前任务，并保留最近相关交互。MiniClaw 的分层压缩与产物机制可用来讲这个取舍：上下文变短，但重要细节仍可回查。

**追问与陷阱：**“压缩能保证无损吗？”自然语言摘要通常不能。指标既看压缩后 token，也看关键约束保存率、档案恢复成功、后续任务成功率与成本。summary 有事实幻觉风险，不能把模型摘要当原始证据。

**源码落点：**[工作记忆和压缩](../../src/MiniClaw/coding_agent/memory/working.py)、[档案](../../src/MiniClaw/coding_agent/memory/archive.py)。

<a id="interview-i11"></a>

### I11：工具一下返回 10MB 日志怎么办？

**来源：S4。**

**回答：**先在源头限制范围，比如只查相关时间、文件、行或错误片段；超大结果保存成产物，向模型提供尺寸、摘要、路径或 ID 与后续读取方法；只读必要片段，保留足够前后文。针对日志可先定位错误类型再读取上下文，针对代码可按符号/文件范围展开。MiniClaw 有截断、大结果产物和档案回查机制，适合说明为什么不是把日志整段塞回 prompt。

**追问与陷阱：**“截掉后半段不就行了？”关键根因可能在尾部或中间，截断必须明确告知并可恢复；索引不能指向已删除临时文件。还要考虑产物路径权限与留存占用。日志内容属于外部数据，其中“忽略指令”等文字不能成为工具授权。

**源码落点：**[产物](../../src/MiniClaw/coding_agent/memory/artifacts.py)、[工具截断](../../src/MiniClaw/coding_agent/tools/truncate.py)。

<a id="interview-i12"></a>

### I12：摘要和记忆为什么可以用小模型？模型怎样选？

**来源：S4、S6。**

**回答：**某些任务输入输出结构明确、只需抽取或局部总结，小模型可能在足够质量下更快更便宜；复杂规划、跨文件修改或歧义证据综合可用能力更强的模型。选型先固定任务集和输出要求，比较任务成功率、工具参数正确率、长上下文稳定性、结构化输出、首 token 与总时延、限流、成本、中文场景表现、部署和数据约束。把不同用途的调用成本分开统计，才能判断路由是否划算。

**MiniClaw 边界：**有模型客户端与配置抽象，不意味着已实现自动学习的动态模型路由。供应商 fallback 是可用性策略，也不自动等同于按难度智能选模型。

**追问与陷阱：**“小模型摘要错了会怎样？”后续大模型也可能被误导，关键字段要结构化校验、保留原文并对坏例做回归。不要回答某型号永远最好。

**源码落点：**[模型配置](../../src/MiniClaw/llm/config.py)、[客户端工厂](../../src/MiniClaw/llm/factory.py)、[用途追踪](../../src/MiniClaw/trace/model_client.py)。

<a id="interview-i13"></a>

### I13：Prompt Cache 为什么能省钱加速，怎样提高命中？

**来源：S4。**

**回答：**服务端常可复用重复输入前缀的计算，减少重复 prefill 开销；具体缓存粒度、保持时间和计费依供应商。稳定的系统提示、工具 schema 和项目规则放前面，把频繁变化的内容尽量放后面，避免每次随机重排工具定义或在前缀塞时间戳。用实际返回的 cached tokens 和输入 token 算命中比例，并同时看延迟、成本，不能把猜测的命中当事实。

**MiniClaw 边界：**它会记录 cached usage 和相应估算成本；这个记录功能不是自己实现了模型 KV-cache 服务。

**追问与陷阱：**“和答案缓存一样吗？”答案缓存复用最终结果，需要语义、权限、时效一致；Prompt Cache 复用前缀计算，模型仍可生成新答案。多租户缓存还要避免越权复用业务结果。

**源码落点：**[缓存用量类型](../../src/MiniClaw/llm/types.py)、[用量和成本](../../src/MiniClaw/trace/store.py)。

<a id="interview-i14"></a>

### I14：Agent 总是重复尝试、来回改文件，怎么处理？

**来源：S4 的路径震荡问题。**

**回答：**先找原因：工具失败没被读懂、工作状态没同步、压缩丢了已试方案、搜索返回重复证据、验收目标含糊，或者规划模型能力不够。记录已尝试动作和结果，规范化工具名/参数与错误签名，对重复且无进展的路径设上限；新一轮要求改变假设、读取新证据或转交人类。MiniClaw 的查询追踪、记忆和目标监督可分别帮助检索去重与持续验收，但不应把它描述成完备的自动循环证明器。

**追问与陷阱：**不能见到重复就禁掉，编译、测试、轮询本来就可能合法重复；要结合文件变化、环境状态和新信息判断。限制最大轮数只能防止失控，不会自动提高正确率。

**源码落点：**[查询追踪](../../src/MiniClaw/coding_agent/memory/query_tracker.py)、[目标监督](../../src/MiniClaw/coding_agent/goal/supervisor.py)。

<a id="interview-i15"></a>

### I15：原本 5 分钟的 Agent 任务变成 20 分钟，怎么排查？

**来源：S1。**

**回答：**先确认任务规模与模型配置是否可比，再把耗时拆成排队、模型首 token/生成、工具、重试、审批等待、压缩和目标续跑。用同一 run 的 Trace 看哪部分增加：请求次数上涨说明路径变长；单次模型变慢可能是输入变长、输出变长、服务拥塞；工具变慢要看网络、磁盘、容器启动和命令本身；重试/fallback 多要看 429、超时和断连。优先修最大的瓶颈，再用相同任务集验证时延与成功率。

**追问与陷阱：**“把全部工具并行就好了？”有读写依赖和共享状态时并行会出错。MiniClaw 现有 Trace 有模型/工具耗时等字段，但外部网关排队、CPU/GPU 细指标需扩展观测。平均值好看也不代表长尾改善。

**源码落点：**[模型追踪](../../src/MiniClaw/trace/model_client.py)、[指标汇总](../../src/MiniClaw/evaluation/runner.py)。

<a id="interview-i16"></a>

### I16：不同任务怎样隔离？有了 Docker 就够了吗？

**来源：S2。**

**回答：**至少隔离会话上下文、记忆作用域、工作目录、执行进程/容器、凭据与资源预算。MiniClaw 有 session、memory scope、workspace guard 和 runtime 边界；文件工具要限制解析后的路径，shell 执行按 runtime 策略隔离，不能只检查表面字符串。对于多租户，还需把认证身份传给检索与工具执行，把读写权限绑定可信租户而非模型参数。

**追问与陷阱：**Docker 共享宿主内核，挂载过宽、特权运行或挂载 Docker socket 都可能放大风险。路径保护也不等于整个主机沙箱；模型请求若读取到不应读取的秘密，禁止网络工具也无法撤销已经发给模型服务的数据。本项目不能直接宣称达到企业级多租户安全隔离。

**源码落点：**[工作区边界](../../src/MiniClaw/coding_agent/runtime/workspace.py)、[运行时](../../src/MiniClaw/coding_agent/runtime/core.py)。

<a id="interview-i17"></a>

### I17：Multi-Agent 如何分工、通信、终止？

**来源：S4、S5。MiniClaw 未实现项的扩展设计题。**

**回答：**按可独立交付的职责分工，例如一个分析测试失败、一个核对接口契约，主协调者维护目标、依赖和最终验收。消息用结构化任务 ID、输入、输出、证据、状态和截止时间，长资料用共享产物引用。子任务应有失败/取消/预算和结果审查；主任务要处理部分成功、超时与重复消息。决定使用多个 Agent 前，先确认并行收益是否超过沟通 token、状态协调和结果合并成本。

**追问与陷阱：**多个 agent 不是越多越好；同一错误模型的多次意见不等于独立专家多数票。MiniClaw 主循环、goal、memory 的职责分层不等于已经有多 Agent 调度；本次使用协作工具撰文也不能记到 MiniClaw 运行时实现头上。

**源码落点：**[现有单Agent循环](../../src/MiniClaw/agent/loop.py)、[目标监督边界](../../src/MiniClaw/coding_agent/goal/supervisor.py)。

<a id="interview-i18"></a>

### I18：多个 Agent 同时修改同一个文件，怎么避免冲突？

**来源：S4。**

**回答：**优先按模块/文件分配独占写权限，各自 worktree 或独立副本产出 patch，由一个集成者合并；有真实依赖时按顺序执行。共享写场景可使用文件级串行队列或锁，再加版本/内容哈希前置条件，发现文件已变就重新读取与重算补丁。合并后跑相关测试，因为文本无冲突不代表逻辑无冲突。MiniClaw 的 mutation queue、精确 edit 和原子写机制可说明本地写入治理，但不是现成的跨机器协同版本控制系统。

**追问与陷阱：**原子写只防止读到半个文件，两个完整旧版本先后覆盖仍会丢更新。锁的作用域、锁超时、死锁处理及外部编辑器绕过锁，都要明确。

**源码落点：**[变更队列](../../src/MiniClaw/coding_agent/tools/mutation_queue.py)、[原子写](../../src/MiniClaw/coding_agent/tools/atomic.py)、[编辑前置校验](../../src/MiniClaw/coding_agent/tools/edit.py)。

<a id="interview-i19"></a>

### I19：长期负责提升一个 Agent 的准确率，你第一步做什么？

**来源：S3。**

**回答：**先定义业务成功：修复任务看独立测试和允许的副作用，问答看证据支持与可答性；再建立覆盖真实分布的开发与留出集，固定模型/配置/代码版本。用 Trace 将失败归因到理解、检索、规划、工具、执行环境、验证或输出，然后优先处理高频且影响大的类别。每次只改清楚的一项，配对 A/B，报告成功率、成本和尾部延迟；新故障变回归题，留出只做发布检查。MiniClaw 的 Trace→Eval、五维指标和 split manifest 是这套思路的具体落点。

**追问与陷阱：**看了 heldout 单题并据此修复后，这题已暴露，要迁入开发/回归并换新的盲测。小样本提升还需置信区间与重复运行；本项目配置没有自动提供全部统计检验，不能编造显著性。

**源码落点：**[评测执行](../../src/MiniClaw/evaluation/runner.py)、[数据切分](../../src/MiniClaw/evaluation/splits.py)。

<a id="interview-i20"></a>

### I20：Agent 怎样保证输出准确？测试用例覆盖哪些？

**来源：S2。**

**回答：**无法无条件保证，只能用明确验收逐层提高可信度。代码题执行独立测试，检查文件改动范围；检索题检查证据、引用和无法回答时拒答；有副作用的业务操作检查权限和实际状态。覆盖正常、多轮、错误参数、工具失败、取消、429/断连、压缩后恢复、重启、重复消息与敏感操作。MiniClaw 的 Eval 同时检查 outcome/process/efficiency/safety/reliability，所以不是只看最终一句“完成”。

**追问与陷阱：**“LLM-as-a-Judge 不就够了？”能补语义判断，但应优先确定性检查；裁判需要校准、不同错误类别的人工样本、固定 rubric 和错误统计。模型自我宣布完成与实际验收通过是不同证据等级。

**源码落点：**[验收类型](../../src/MiniClaw/evaluation/models.py)、[确定性与模型验收](../../src/MiniClaw/evaluation/runner.py)。

<a id="interview-i21"></a>

### I21：为什么自研？LangChain 和 LangGraph 的区别是什么？

**来源：S2、S5。**

**回答：**LangChain 提供模型、工具、检索等集成与高层 Agent 抽象；LangGraph 更聚焦有状态执行图、节点边、持久化和人类介入等编排。现代框架存在组合关系，不能简单背“LangChain 只能线性、LangGraph 才能循环”。自研的好处是控制事件、协议、上下文、审批与隔离细节、减少难定位的隐式行为；代价是维护适配、兼容、恢复和生态接入。MiniClaw 的意义在于它把这些层显式拆出来，是否比成熟框架划算要结合团队目标。

**追问与陷阱：**问“去掉一个组件会坏哪里”时应回答具体：没有持久化会丢会话/目标恢复，没有外置产物会增加上下文压力。不能把“没用框架”本身当作性能更高或技术更强的证据。

**源码落点：**[自研组装入口](../../src/MiniClaw/coding_agent/assistant/coding.py)、[执行循环](../../src/MiniClaw/agent/loop.py)。

<a id="interview-i22"></a>

### I22：后端上千用户来用，如何部署、限流和扩容？

**来源：S3 资源/线程池问题的工程延伸，不冒充该帖 Agent 部署原题。**

**回答：**先把日活换成峰值到达率、平均任务时长和目标尾延迟；近似在途任务数可用 `L≈λW`，不能拿“每天一千人”直接拍线程数。入口尽快确认请求，长任务进入持久队列；按用户/租户/模型限制并发和 token 预算，设置退避、取消与超时。会话内有序，独立会话并发；模型配额、检索、容器、数据库分别看瓶颈。状态外置、worker 可恢复，发布版本与评测基线绑定。

**MiniClaw 边界：**目前本地文件、会话与接入实现可以支持开发验证；分布式任务队列、集群调度、统一鉴权、跨实例幂等是需要新增的生产能力。

**追问与陷阱：**无限增加并发可能只会触发更多 429。请求重试必须区分模型读取与有副作用工具，后者需要幂等键/状态查询，不能盲重放。

**源码落点：**[飞书会话路由](../../src/MiniClaw/platforms/feishu/router.py)、[当前主路径会话与压缩存储](../../src/MiniClaw/coding_agent/memory/working.py)。

<a id="interview-i23"></a>

### I23：讲一下 Transformer、自注意力、多头注意力和激活函数

**来源：S6。**

**回答：**以常见 decoder-only 大模型为例，token 先变成向量并引入位置信息，经过多层因果自注意力与前馈网络，配合残差和归一化，最终输出下一个 token 的概率。自注意力令当前位置通过 Q 与历史 K 的相似度分配权重，对 V 加权：`Attention(Q,K,V)=softmax(QKᵀ/√d_k + mask)V`。多头用多个投影子空间捕捉不同关系后合并；它不是“多个 Agent”。激活函数给前馈网络引入非线性，常见有 GELU、门控结构中的 SiLU 等。

**追问与陷阱：**普通密集 attention 在长输入上的注意力矩阵随长度平方增长，具体时间/显存还受实现影响。推理 KV cache 保存历史 K/V，减少生成阶段重复计算，但占用显存；它与应用层 memory 是不同层。MiniClaw 调用模型服务，不实现 Transformer 训练或底层推理内核。

**源码落点：**[MiniClaw模型调用边界](../../src/MiniClaw/llm/client.py)、[供应商适配](../../src/MiniClaw/llm/openai_compatible.py)。

<a id="interview-i24"></a>

### I24：手写一个线程安全的 LRU，为什么有 GIL 还要锁？

**来源：S2。**

**回答：**哈希表定位节点，双向链表记录最近使用顺序；`get` 命中后移到头，`put` 更新/插入后也移到头，超容量从尾淘汰，平均 O(1)。同一把锁保护哈希表和链表之间的不变量，读命中也会改顺序，所以不是纯读。Python 的 GIL 不保证多步业务操作原子，更不提供“查→删→插”这组操作的完整事务语义。

```python
from collections import OrderedDict
from threading import Lock

class LRU:
    def __init__(self, capacity):
        if capacity < 0:
            raise ValueError("capacity must be non-negative")
        self.capacity = capacity
        self.data = OrderedDict()
        self.lock = Lock()

    def get(self, key, default=None):
        with self.lock:
            if key not in self.data:
                return default
            self.data.move_to_end(key)
            return self.data[key]

    def put(self, key, value):
        with self.lock:
            self.data[key] = value
            self.data.move_to_end(key)
            if len(self.data) > self.capacity:
                self.data.popitem(last=False)
```

**追问与陷阱：**若要求不能用有序字典，自己实现双向链表与哨兵节点。缓存“get miss→昂贵加载→put”不在这段锁里自动合并，同 key 并发加载还要 single-flight；别持全局锁等远程模型，否则所有请求串行阻塞。这是面试示例，不是声称 MiniClaw 使用这段 LRU。

**源码关系：**这道算法题为独立面试基础题，仓库没有对应专用算法模块；不得把示例说成项目已实现功能。项目并发/状态管理的相关入口为[项目内另一种并发治理：变更队列](../../src/MiniClaw/coding_agent/tools/mutation_queue.py)，仅供对照。

<a id="interview-i25"></a>

### I25：给数字集合 A，求能组成且严格小于 n 的最大数

**来源：S1，原帖示例 `n=23121, A={2,4,9}`，结果 `22999`。**

**回答：**先澄清数字是否可重复、是否允许 0 和前导零；按例子可重复。从最高位开始尽量选与 n 当前位相同的允许数字；若相等前缀之后无法继续，就在当前位置或更早位置回退，选能使前缀变小的最大数字，剩下全部填集合最大值。若整个同位数都无解，则尝试少一位，首位取合法最大非零数字，其余取最大数字。特别处理最后完全等于 n 的情况，因为要求严格小于。

**追问与陷阱：**不能独立逐位选“≤当前位最大”，一旦前面已经变小，后面可以全取最大；如果一直贴着上界走到底，最后还需回退。可以写带 tight 状态的数字 DP/回溯，字典序降序搜索并记忆失败状态；十进制数字集合很小，状态数量随位数线性量级增长。不要为了讲复杂度忽略无解与前导零。

**源码关系：**这道算法题为独立面试基础题，仓库没有对应专用算法模块；不得把示例说成项目已实现功能。

<a id="interview-i26"></a>

### I26：怎样 O(n) 判断二叉树是否平衡？

**来源：S4。**

**回答：**做后序遍历，每个节点只计算一次子树高度；左右高度差大于 1 或任一子树已不平衡，返回失败标记 `-1`，否则返回 `max(left,right)+1`。根返回非负即平衡，时间 O(n)，递归栈 O(h)，最坏退化链为 O(n)。空树按通常约定平衡。

```python
def is_balanced(root):
    def height(node):
        if node is None:
            return 0
        left = height(node.left)
        if left == -1:
            return -1
        right = height(node.right)
        if right == -1 or abs(left - right) > 1:
            return -1
        return max(left, right) + 1
    return height(root) != -1
```

**追问与陷阱：**每个节点重新递归算一次完整高度可能 O(n²)；只检查根左右高度差不够。Python 遇很深的树还要考虑显式栈，不能简单无限抬递归上限。Agent 岗依然可能要求普通数据结构算法，项目讲得好不会代替基础。

**源码关系：**这道算法题为独立面试基础题，仓库没有对应专用算法模块；不得把示例说成项目已实现功能。

<a id="interview-i27"></a>

### I27：最长无重复字符子串怎么做？

**来源：S5。**

**回答：**滑动窗口维护 `[left,right]`，哈希表记每个字符最近出现位置。读到字符 c 时，`left=max(left,last[c]+1)`，更新 c 的位置，再用窗口长度更新答案。每个字符处理一次，时间 O(n)，空间 O(min(n,字符集大小))。

```python
def longest_unique_substring(text):
    last = {}
    left = best = 0
    for right, char in enumerate(text):
        left = max(left, last.get(char, -1) + 1)
        last[char] = right
        best = max(best, right - left + 1)
    return best
```

**追问与陷阱：**`abba` 是关键反例：遇到最后 a 时不能让 left 回退到 1。注意题目要的是连续“子串”，不是可跳跃“子序列”；返回长度还是原子串需先澄清。复杂 emoji 的用户视觉字符可能由多个 Unicode code point 组成，若业务按视觉字符计数要另做分段，普通面试通常按字符序列约定即可。

**源码关系：**这道算法题为独立面试基础题，仓库没有对应专用算法模块；不得把示例说成项目已实现功能。项目并发/状态管理的相关入口为[项目内查询状态管理对照](../../src/MiniClaw/coding_agent/memory/query_tracker.py)，仅供对照。

<a id="interview-i28"></a>

### I28：如果面试官说“你这些模块是不是堆出来的”，怎样证明价值？

**来源：S3 的组件必要性、S4 的系统优化问题归纳。**

**回答：**“我会选一个明确痛点，用去掉组件的对照实验说明价值。例如大工具结果挤爆上下文，就比较同模型、同任务、同 turn cap 下开启/关闭 artifact；同时报告任务正确率、token、回查次数和耗时。若分层压缩只省 token 却明显降低验收通过率，就不是有效优化。再用 Trace 展示坏例：错误发生在检索、摘要丢信息、工具执行还是验证；用修复后的回归题证明针对同类问题有改善。”

**MiniClaw 可展示材料：**`ai_efficiency.py` 的两组产物策略、`code_compression.py` 的两种压缩策略、`trace/` 的过程记录和 `evaluation/` 的验收体系。适配器存在是实验基础，实验结论必须有实际产物支持。

**追问与陷阱：**不要回答未经测量的“提升 30%”；也不要把“22 个回归用例全过”换算成真实用户成功率。最可信的表述是：明确我做了什么、量了什么、分母是什么、还没覆盖什么。能坦然讲清这些，往往比堆更多名词更有说服力。

**源码落点：**[产物策略实验](../../src/MiniClaw/benchmark/ai_efficiency.py)、[压缩策略实验](../../src/MiniClaw/benchmark/code_compression.py)。

<a id="chapter-32"></a>

## 国内面试怎样介绍这个项目：三个长度的口述模板

以下模板中的“我”应当只用于你实际完成或参与过的工作。接手阅读过某模块，可以说“项目中实现了，我重点理解/维护的是……”，不要把理解源码冒充全部独立开发。

### 30 秒版：先说明项目与核心贡献

“MiniClaw 是一个 Python 编码 Agent，模型通过结构化工具调用读代码、改文件和运行验证。我把它理解为三层：模型接入、通用 Agent 循环、编码产品装配。项目的工程重点是长上下文记忆与压缩、受控工具执行、Goal 验收和 Trace/Eval 闭环。我可以用一条从读取代码到修复测试的完整轨迹说明这些机制。”

如果你真实负责的是记忆模块，就把最后一句换成具体贡献：“我主要负责记忆检索和上下文压缩，重点解决历史证据召回和长任务上下文膨胀。”这比一口气报十几个框架名更能引出你能回答的问题。

### 3 分钟版：讲一条线，再讲两个难点

第一段讲产品任务：用户给仓库和需求，模型决定调用工具，程序执行并反馈，直到本轮结束；长期任务通过 Goal 继续监督。

第二段讲分层：LLM 层统一消息与 SSE，AgentLoop 只管理模型/工具循环，CodingAssistant 装配记忆、工具、审批、Runtime 和状态；CLI、飞书和 Eval 复用这套产品路径。

第三段挑两个你最熟的难点，按“现象—原因—方案—验证”讲。例如：

- 长日志导致上下文越来越大，旧信息被机械裁切会丢证据。采用大结果 Artifact、旧消息归档、检索补回和分层压缩，比较压缩 token、任务正确率、延迟和检索命中。
- 模型宣称完成不等于真正完成。用持久 Goal、最新命令验证和逐条验收证据做门禁，Trace 中保留执行与验收过程，端到端 Eval 检查最终工作区和过程。

最后说明结果与边界：报你确实运行过的指标、模型配置、样本规模和失败案例。没有完整测量时，直接说已实现机制和已完成哪些验证，不编“准确率提升 30%”。当前通用工具执行仍串行，MCP 和完整多 Agent 调度属于后续扩展。

### 10 分钟版：用六个问题组织深挖

1. 任务怎样从入口进入 Agent？讲一次完整消息、工具调用和反馈。
2. 为什么这样分层？讲接口与反向依赖测试。
3. 最难的机制是什么？选记忆压缩、恢复、审批、流式容错之一，讲代码与失败窗口。
4. 怎样证明有效？讲 Trace → 失败案例 → Eval → 消融 → 固定未见集。
5. 哪些设计仍有限制？讲默认未开启能力、单机状态、并发与安全边界。
6. 如果做企业产品怎样演进？讲身份与租户隔离、队列、存储、统一预算、监控和发布门禁；明确是扩展方案。

不要把 10 分钟全花在术语定义。面试官最需要判断你是否真的遇过问题、作过选择、验证过结果，以及能不能承担下一步工程工作。

<a id="chapter-33"></a>

## 连续追问模拟：从会背到真正理解

### 追问链一：记忆为什么要四层

**第一问：四层是什么？** 工作上下文保当前推理材料，episodic 记任务经历，semantic 存稳定事实，procedural 管可复用步骤。

**第二问：为什么不用一个向量库全部存？** 因为生命周期、更新方式、可信度和注入方式不同。当前消息要保工具协议，稳定事实要处理冲突，技能适合先给目录按需展开。一个存储后端可以容纳多类数据，但仍应区分数据类型和策略。

**第三问：向量召回不到怎么办？** 对代码符号、路径和版本号，字面匹配很有价值，采用 BM25、精确符号、本地 hash 向量及可选真实 BGE dense 混合，多源融合再重排；同时检查候选有没有进入库以及 scope 过滤是否过严。

**第四问：加 reranker 一定好？** 不一定。候选里没答案时无法补救，输入截断可能丢关键证据，推理成本会增加。应该测同一数据集的 top-k、任务正确率和延迟，开关消融。

**第五问：当前项目默认全开吗？** 没有。BGE embedding、BGE reranker、模型蒸馏默认关闭；代码有能力不等于实际运行启用。

### 追问链二：用了 Docker 是否就安全

**第一问：Docker 做了什么？** 隔离命令环境并限制网络、能力、资源和挂载。

**第二问：哪个组件在 Docker？** bash 执行进入容器，文件工具仍由宿主 Python 经 WorkspaceGuard 操作有效工作区。

**第三问：Direct 模式容器能改源文件吗？** 可以，默认挂载有效源工作区，写入会持久到实际目录；Snapshot 使用会话副本。

**第四问：容器里 rm 文件怎么办？** Docker 不是阻止所有业务破坏的审批器；需要工具风险分类与审批，加上隔离策略。用户授权的破坏也需要明确目标，容器没有自动语义理解。

**第五问：是不是能完全防提示注入？** 不能。分离检索证据和 system 规则、路径边界、审批和网络限制能降低风险；还要评估误拦与漏拦，不能用自然语言提示或正则黑名单证明绝对安全。

### 追问链三：你说任务可以恢复，究竟恢复什么

**第一问：保存哪些东西？** 持久对话、运行状态、Goal、记忆与 Trace，以及单独的投递状态。

**第二问：崩溃时文件已写但 tool 结果未存怎么办？** 补中断结果恢复消息协议，然后读取实际文件，避免盲目重复执行。

**第三问：能保证 exactly-once 吗？** 不能只靠本地日志保证任意外部动作 exactly-once。需要业务幂等和下游配合。

**第四问：Snapshot 可以一键恢复世界吗？** 只能处理选定工作区的副本与隔离，不自动回滚已经发出的消息、远端 API 或外部数据库。

**第五问：恢复后为什么还需要重新验证？** 文件、外部状态和依赖可能已变；旧验证只证明旧时间点的状态。完成之前要建立最新证据。

### 追问链四：为什么不能只给模型换一个好提示

**第一问：Prompt 能不能提升正确率？** 能，尤其在任务描述、工具说明、证据格式和输出约束上。

**第二问：为什么仍做 Runtime 和审批？** 模型遵循提示是概率性的，文件权限、进程资源和授权必须由程序执行。

**第三问：为什么还要 Eval？** Prompt 改好一个例子可能让其他任务变差，没有固定回归和未见集就无法识别过拟合。

**第四问：为什么还要 Trace？** 只有最终分数很难判断错误来自召回、模型、工具、权限、超时还是验收标准。

**第五问：怎样证明某组件必要？** 做消融：固定模型、数据、预算和其他设置，只改变该组件，对质量、延迟、成本与安全共同评估。

<a id="chapter-34"></a>

## 从当前项目扩展到企业系统：面试设计题的正确展开方式

本节是**扩展设计**，不是声称 MiniClaw 已拥有这些生产能力。

### 多用户服务化

把“一个 Python 进程持有几个助手实例”扩展为 API 网关、认证、任务队列、worker、状态存储和对象存储。请求先绑定 user/tenant/project/session，所有记忆、产物、检索和工具执行都必须带作用域。

不能只把飞书会话 ID 当权限系统。标识用于寻址，认证决定是谁，授权决定能做什么。并发任务要有租户配额、排队上限和资源回收。

文件路径不要由外部用户直接决定宿主存储位置；通过受控 workspace ID 映射到沙箱。日志和 Trace 也需要隔离、脱敏、保留期限与访问权限，不能只保护工具能读的文件而让运维接口导出全部历史。

### 更严格的任务预算

当前存在模型尝试超时、工具超时、turn 上限和 Goal 预算。企业接口通常需要统一 deadline，把请求剩余时间传到模型重试、检索、审批和工具执行，不能每层都重新获得完整预算。

可以预估每次调用的最大成本，达到阈值前禁止再启动昂贵任务；对并发调用预留预算并在结束时结算，避免多个工具同时检查“预算还够”而总体超支。价格未知时要单独显示“未配置”，不要作为零成本参与强保证。

### 多 Agent 协作

先看任务是否能分解成独立子问题，再决定是否用多 Agent。可并行的仓库分析、测试设计和文档审查有收益；高度耦合的同文件修改可能只增加协调成本。

建议明确角色输入输出协议、任务依赖图、独立工作区或 worktree、文件所有权、合并与验收门禁、总预算及取消传播。每个子 Agent 要有最小工具集，而不是共享全权限 shell。

评估时比较“相同模型总预算下的单 Agent”与“多 Agent”，否则多花几倍 token 带来的提升可能被误写成架构本身更好。协调者也可能成为瓶颈或错误放大点。

### MCP 接入

可以在 ToolManager 旁增加 MCP 适配层：发现远端工具，把 schema 转成内部定义，将 ToolInvocation 转为协议请求，把远端结果转为 ToolResult。仍需做来源信任、schema 限额、权限过滤、审批、超时、取消和 Trace。

MCP 解决连接与描述协议，不替你决定业务成功，也不自动解决恶意工具说明、工具重名和远端副作用幂等。远端工具列表动态变化时，还要更新模型可见定义和缓存版本。

### 工具特别多时

工具数量从 10 增加到几百，全部 schema 塞进 prompt 会增加 token、选择难度和注入面。可先按角色筛选，再按当前任务检索候选工具，给候选返回完整且真实的 schema，执行前再次验证工具版本与权限。

应当测工具候选 Recall@k、最终工具选择正确率、参数成功率和任务成功率。只把候选压到很小却漏掉必要工具，会让模型在错误工具集中自信选择。

### 数据库与索引演进

当前文件/SQLite/本地索引便于个人或小规模部署，规模增大后可将权威状态迁移到事务数据库，原始日志与产物迁移到对象存储，检索做版本化索引更新。

源记录、embedding 和 ANN index 要区分：索引是派生数据，应能从源记录重建。上线新 embedding 模型时应记录模型版本、维度、归一化方式、chunk 规则和数据快照；切换时避免新 query vector 搜旧 embedding 空间。

<a id="chapter-35"></a>

## 动手练习：用这些题检验你是否真的懂

建议先口述答案，再去指定代码检查。这里不要求你修改正式项目，也不默认运行付费模型。

| 练习 | 你应能解释的答案要点 | 源码入口 |
|---|---|---|
| 画出一次 read 后 edit 的对话列表 | assistant 调用与 tool 结果按 ID 配对，下一次请求包含反馈 | `agent/loop.py` |
| 去掉某角色的 bash，会影响什么 | 模型定义、提示工具列表和执行可见性都要一致 | `tools/manager.py` |
| 模型输出一半断流，是否重试 | 已输出后不自动 retry/fallback；记录失败 | `llm/openai_compatible.py` |
| 120 秒总超时与三次重试怎样组合 | total 是每次传输尝试，整个逻辑请求可更长 | 同上 |
| edit oldText 匹配两处时如何处理 | 不应随意选一处；需要唯一、非重叠约束 | `tools/edit_diff.py` |
| 只删历史消息能否完成可靠压缩 | 不行，还需保存原始证据、工具协议边界与恢复视图 | `memory/working.py` |
| top20 检索结果是否全给模型 | 不一定，预算和多样性分配决定实际 rendered | `memory/manager.py` |
| 新事实与旧事实冲突如何处理 | 不静默覆盖，进入冲突决策流程 | `memory/semantic.py` |
| Goal 验证后又 edit 一次 | 旧验证失效，需要重新验证 | `assistant/coding.py` |
| 使用 bash 改文件是否自动同等失效 | 当前没有对任意 bash 副作用做统一识别，需承认边界 | 同上 |
| 进程崩溃后旧 bash 是否必然重跑 | 不应盲目重跑；修复消息并检查实际状态 | `runtime/state.py`、`assistant/coding.py` |
| replay 为何不能代替端到端测试 | 历史模型边界独立回放，工具不会执行，环境不随新决策演化 | `trace/replay.py` |
| 全部重复尝试都失败为何也可能 stable | stable 表示结果一致，不代表成功 | `evaluation/runner.py` |
| 本地去重为何不等于分布式幂等 | 多进程 claim、远端确认窗口和业务副作用仍需处理 | `platforms/feishu/dedupe.py`、`platforms/delivery.py` |

### 七天学习路径

**第 1 天：** 画总图，读 types、AgentLoop，手写六条消息的工具调用轨迹。验收：不用看文档讲清 Tool Calling 与执行的区别。

**第 2 天：** 读 CodingAssistant 和 ToolManager，再看 read/edit/bash。验收：能解释一个编辑从参数到原子提交的检查链。

**第 3 天：** 学模型 SSE、超时、重试、取消。验收：能把 429、首 token 超时、输出后断流和进程取消分开回答。

**第 4 天：** 学四层记忆和分层压缩。验收：能说明数据在哪里、何时写、何时读、丢失信息如何找回。

**第 5 天：** 学 BM25、dense、RRF、reranker 和记忆冲突。验收：能手算一例 RRF，能解释默认开关和消融设计。

**第 6 天：** 学 Goal、Runtime、审批、飞书恢复与投递。验收：能说清四个失败窗口及保证边界。

**第 7 天：** 学 Trace/Eval，练公开面经题和三分钟口述。验收：能提供一次有依据的难点分析、一个改进实验设计和一个当前限制。

如果准备时间只有一天，优先总图、完整轨迹、核心循环、工具边界、记忆压缩、评测闭环和源码默认值。不要临时背一堆你无法落到这个项目的框架名。

<a id="chapter-36"></a>

## 简历与答辩中最容易被追问穿的说法

| 容易失真的说法 | 更准确、也更经得起追问的说法 |
|---|---|
| “从零训练了一个 Agent 大模型” | “实现了使用外部大模型的编码 Agent 框架”；若有训练另列数据、方法和结果 |
| “支持所有大模型” | “实现 OpenAI-compatible 传输与已测试 provider 适配” |
| “十几个工具并行调用” | “基础注册 10 个工具，一次回复可发多个，当前顺序执行” |
| “做了多 Agent” | “有角色工具隔离接口，完整协作调度未实现” |
| “MCP 就是 Tool Calling” | “MCP 是连接协议，Tool Calling 是模型提出动作的机制” |
| “默认用 BGE-M3 + reranker” | “可配置启用，当前源码默认关闭模型 embedding/reranker” |
| “用了 SQLite FTS5” | “当前检索持久化是自建倒排表与 BM25，具体以 retrieval.py 为准” |
| “Docker 保证绝对安全” | “受限命令容器结合路径与审批边界，仍需按威胁模型评估” |
| “取消会撤回全部修改” | “终止未完成动作，已发生副作用不自动回滚” |
| “恢复保证只执行一次” | “恢复运行状态与消息协议，外部副作用仍需幂等设计” |
| “模型停了就是任务成功” | “run 结束与 Goal/Eval 验收分开” |
| “测试通过说明没有 bug” | “通过当前测试覆盖的条件，还需边界与代表性场景” |
| “Replay 完全复现 Agent 环境” | “重放模型请求边界，不执行历史工具” |
| “稳定率就是成功率” | “稳定性衡量重复一致性，必须结合成功率看” |
| “默认 0 美元，所以没有成本” | “价格未配置时估计为零，实际费用以服务商为准” |
| “上线支撑百万用户” | “只有真实部署与监控证据才能写规模数字” |
| “做了某 benchmark 就有官方排名” | “说明适配范围、样本、协议、运行配置和结果证据” |

<a id="chapter-37"></a>

## 配置和运行入口：读懂如何把系统启动起来

项目要求 Python 3.11 及以上。基础依赖是 httpx；飞书、检索、重排和 benchmark 是可选依赖。见 [pyproject.toml](../../pyproject.toml)。下面是供你后续使用的命令示例，本次编写文档没有替你安装依赖、构建镜像或运行收费模型。

```powershell
cd D:\MIniClaw
python -m pip install -e .
python -m pip install -e ".[retrieval,rerank]"
python -m pip install -e ".[feishu]"
```

要使用默认 Docker 命令后端，先具备可用 Docker 和对应镜像：

```powershell
docker build -f docker/runtime/Dockerfile -t miniclaw-runtime:py311 docker/runtime
python -m MiniClaw.cli --workspace D:\your-project
```

模型密钥通过自己的环境配置提供。普通启动会真实调用配置的模型服务并可能修改所选 Direct 工作区；要在会话副本中操作，可以显式选择：

```powershell
python -m MiniClaw.cli --workspace D:\your-project --workspace-mode snapshot
```

其他入口及用途：

| 入口 | 用途 | 是否涉及真实模型 |
|---|---|---|
| `python -m MiniClaw.cli --help` | 查看编码 CLI 参数 | 否 |
| `python -m MiniClaw.llm.smoke` | 单独验证真实模型兼容传输 | 是 |
| `python -m MiniClaw.coding_agent.runtime.smoke` | 检查 Docker 执行和工作区机制 | 不调用 LLM，但执行命令/容器 |
| `python -m MiniClaw.platforms.feishu.cli` | 启动飞书接入 | 收任务后可调用模型并投递消息 |
| `python -m MiniClaw.trace.cli` | Trace 提取、分析和 replay 子命令 | report 等离线；replay 会调用模型 |
| `python -m MiniClaw.evaluation.cli` | 端到端评测 | 通常会调用配置模型、工具和 Runtime |
| `python -m MiniClaw.evaluation.splits` | 固定划分核验与命令生成 | 划分校验本身不需要 LLM |
| `python -m MiniClaw.frontend` | 架构图静态展示服务 | 否 |

优先按配置责任找文件，不要盲目把所有参数塞进 AgentLoop：

| 配置领域 | 源码位置 | 主要参数 |
|---|---|---|
| 模型 | [llm/config.py](../../src/MiniClaw/llm/config.py) | provider、model、URL、窗口、输出、价格、超时、retry/fallback |
| Runtime | [runtime/config.py](../../src/MiniClaw/coding_agent/runtime/config.py) | host/docker、direct/snapshot、资源、网络、命令时限 |
| 压缩 | [memory/config.py](../../src/MiniClaw/coding_agent/memory/config.py) | reserve、soft/hard/target、近期预算、artifact |
| 检索与蒸馏 | [memory/manager.py](../../src/MiniClaw/coding_agent/memory/manager.py)、[retrieval.py](../../src/MiniClaw/coding_agent/memory/retrieval.py)、[distillation.py](../../src/MiniClaw/coding_agent/memory/distillation.py) | source、query、embedding、rerank、蒸馏开关 |
| Goal | [goal/config.py](../../src/MiniClaw/coding_agent/goal/config.py) | attempt、时间、成本、Judge；验证新鲜度由状态规则判断，未配置固定有效期 |
| 审批 | [approval/config.py](../../src/MiniClaw/coding_agent/approval/config.py) | allow/ask/deny、timeout、allowlist |
| 指令 | [instructions/config.py](../../src/MiniClaw/coding_agent/instructions/config.py) | 开关、预算、搜索目录 |
| 飞书 | [feishu/config.py](../../src/MiniClaw/platforms/feishu/config.py) | 接入凭据、域名、会话 scope、队列、并发、分片、投递重试与入站 stale 时限 |

安装检索依赖并不等于打开 BGE 功能；开功能也不等于模型文件已经下载。应该同时确认配置、依赖、模型初始化状态和 Trace 中实际使用的检索路径。

<a id="chapter-38"></a>

## 本文核验范围与维护方法

本次工作是文档整理和源码核验，没有修改 MiniClaw 产品源码，也没有运行真实模型、Docker 集成评测或重新生成完整 benchmark 成绩。

实际通过的离线测试为：`test_architecture.py`、`test_agent_loop.py`、`test_llm_config.py`、`test_llm_streaming.py`、`test_session_store.py`，共 **32 passed**。这些测试验证包依赖、通用循环、角色工具注入、流式聚合、超时重试、fallback、取消和中断消息修复等对应行为；不能代表本文所有模块和生产场景都在本次被完整重测。

完整源码文件索引见后续附录，按实际 Python 文件自动清点，包括 `__init__.py` 的导出层。文档还检查了本地源码链接、目录锚点和代码块配对。未来源码变动时，先看默认配置、工具注册、关键状态机和评测口径，再更新对应章节。

旧笔记中“九个工具”、把可选检索能力写成默认启用、把 replay 误解成真实环境回放等说法，不应该覆盖当前源码。本文没有可用 Git 提交号可引用，因此通过源码清单和哈希记录本次对应快照。

<a id="chapter-39"></a>

## 附录：当前全部 Python 源文件导航

以下清单来自本次实际源码扫描，覆盖 `src/MiniClaw` 的全部 Python 文件。表中行号用于定位代表性顶层符号，行数表示文件规模，不表示代码质量或个人贡献。包初始化文件负责导出；具体机制见正文。

共 **111 个 Python 源文件，26,907 行**。不计第三方 external、测试、缓存和打包生成目录。

本次源码快照组合 SHA-256：`19c96ee09e25521c6b9e876c25bb54768487f079e0bd74797c7c26774b89883f`。组合顺序为相对路径排序后，逐项拼接相对路径、NUL、原始字节、NUL；单文件清单保存在研究记录中。

### `包根目录`

| 文件 | 责任 | 代表性源码符号 | 行数 |
|---|---|---|---:|
| [__init__.py](../../src/MiniClaw/__init__.py) | 包初始化与公开接口导出；不视为独立运行服务 | 导出、常量或内部辅助结构 | 8 |
| [cancellation.py](../../src/MiniClaw/cancellation.py) | 统一取消信号、取消时间与不同层取消异常 | `OperationCancelledError`（8 行）；`ModelCancelledError`（23 行）；`ToolCancelledError`（29 行）；`CancellationToken`（33 行） | 71 |
| [cli.py](../../src/MiniClaw/cli.py) | 读取配置，启动交互 CLI，转发 Goal 命令并展示 Agent 事件 | `interactive`（17 行）；`build_parser`（133 行）；`main`（151 行） | 156 |
| [frontend.py](../../src/MiniClaw/frontend.py) | 提供静态架构图查看器的本地 HTTP 服务 | `architecture_directory`（9 行）；`serve`（13 行）；`build_parser`（29 行）；`main`（36 行） | 42 |

### `agent`

| 文件 | 责任 | 代表性源码符号 | 行数 |
|---|---|---|---:|
| [__init__.py](../../src/MiniClaw/agent/__init__.py) | 包初始化与公开接口导出；不视为独立运行服务 | 导出、常量或内部辅助结构 | 4 |
| [events.py](../../src/MiniClaw/agent/events.py) | 定义产品可消费的 Agent 生命周期事件 | `AgentEvent`（10 行） | 28 |
| [loop.py](../../src/MiniClaw/agent/loop.py) | 执行模型—工具—反馈循环与中断消息补齐 | `AgentLoop`（24 行） | 258 |
| [types.py](../../src/MiniClaw/agent/types.py) | 定义抽象工具执行与结果结构协议 | `AgentToolResult`（9 行）；`AgentToolExecutor`（22 行） | 34 |

### `benchmark`

| 文件 | 责任 | 代表性源码符号 | 行数 |
|---|---|---|---:|
| [__init__.py](../../src/MiniClaw/benchmark/__init__.py) | 包初始化与公开接口导出；不视为独立运行服务 | 导出、常量或内部辅助结构 | 26 |
| [agentdojo.py](../../src/MiniClaw/benchmark/agentdojo.py) | AgentDojo 场景和攻击/效用评估适配 | `QueryMetrics`（46 行）；`AgentDojoFunctionTool`（55 行）；`MiniClawAgentDojoPipeline`（87 行）；`run_benchmark`（299 行）；另有 2 个顶层公开符号 | 407 |
| [agentsafety.py](../../src/MiniClaw/benchmark/agentsafety.py) | AgentSafetyBench 场景的 allow/ask/refuse 模型分类评估适配 | `load_official_system_prompt`（39 行）；`load_cases`（54 行）；`parse_prediction`（67 行）；`complete_case`（94 行）；另有 4 个顶层公开符号 | 270 |
| [ai_efficiency.py](../../src/MiniClaw/benchmark/ai_efficiency.py) | AI Efficiency 任务准备、Agent 执行与结果记录适配 | `selected_tasks`（88 行）；`prepare_workdir`（147 行）；`changed_files`（181 行）；`dynamic_expected`（218 行）；另有 7 个顶层公开符号 | 652 |
| [code_compression.py](../../src/MiniClaw/benchmark/code_compression.py) | 代码压缩策略对照与任务评估 | `Instance`（44 行）；`arm_environment`（52 行）；`build_task_prompt`（75 行）；`resolve_instance`（86 行）；另有 9 个顶层公开符号 | 690 |
| [contextweave.py](../../src/MiniClaw/benchmark/contextweave.py) | ContextWeave 长任务/压缩相关评测适配 | `prepare_memory_store`（78 行）；`retrieve_memory_context`（138 行）；`concatenate_memory_context`（157 行）；`plugin_inject`（201 行）；另有 4 个顶层公开符号 | 287 |
| [longmemeval.py](../../src/MiniClaw/benchmark/longmemeval.py) | 早期 LongMemEval 接入与评测路径 | `LongMemEvalCase`（75 行）；`load_cases`（107 行）；`retrieval_stress_score`（153 行）；`history_messages`（183 行）；另有 12 个顶层公开符号 | 791 |
| [longmemeval_v2.py](../../src/MiniClaw/benchmark/longmemeval_v2.py) | 新版 LongMemEval 端到端记忆任务和评分路径 | `SelectedQuestion`（85 行）；`TrajectoryOffset`（95 行）；`TrajectoryChunkMetadata`（101 行）；`RetrievedParent`（109 行）；另有 20 个顶层公开符号 | 1296 |
| [memory_agent_bench.py](../../src/MiniClaw/benchmark/memory_agent_bench.py) | MemoryAgentBench 记忆任务转换与执行 | `BenchmarkCase`（65 行）；`parse_fact_documents`（89 行）；`normalize_answer`（111 行）；`score_answer`（115 行）；另有 8 个顶层公开符号 | 620 |
| [repoguard.py](../../src/MiniClaw/benchmark/repoguard.py) | RepoGuard 仓库约束/安全任务适配 | `compute_summary`（431 行）；`run_runtime_probes`（506 行）；`run_model_runtime_probes`（644 行）；`run`（810 行）；另有 2 个顶层公开符号 | 1023 |
| [swebench_runner.py](../../src/MiniClaw/benchmark/swebench_runner.py) | SWE-bench 数据、workspace/patch 与验证执行入口 | `write_text_lf`（19 行）；`main`（35 行） | 44 |
| [toolsafe.py](../../src/MiniClaw/benchmark/toolsafe.py) | ToolSafe 数据与工具安全评估适配 | `SelectedCase`（58 行）；`load_cases`（69 行）；`select_cases`（84 行）；`build_user_prompt`（124 行）；另有 6 个顶层公开符号 | 338 |

### `coding_agent`

| 文件 | 责任 | 代表性源码符号 | 行数 |
|---|---|---|---:|
| [__init__.py](../../src/MiniClaw/coding_agent/__init__.py) | 包初始化与公开接口导出；不视为独立运行服务 | 导出、常量或内部辅助结构 | 27 |

### `coding_agent/approval`

| 文件 | 责任 | 代表性源码符号 | 行数 |
|---|---|---|---:|
| [__init__.py](../../src/MiniClaw/coding_agent/approval/__init__.py) | 包初始化与公开接口导出；不视为独立运行服务 | 导出、常量或内部辅助结构 | 32 |
| [config.py](../../src/MiniClaw/coding_agent/approval/config.py) | 项目审批文件、环境配置与白名单加载 | `ApprovalAllowRule`（14 行）；`ApprovalSettings`（22 行）；`load_approval_settings`（29 行） | 121 |
| [gate.py](../../src/MiniClaw/coding_agent/approval/gate.py) | 策略/白名单决策、审批等待、超时与取消 | `ApprovalGate`（29 行） | 209 |
| [interaction.py](../../src/MiniClaw/coding_agent/approval/interaction.py) | CLI 审批展示与回复解析 | `ApprovalResponse`（14 行）；`ApprovalInbox`（27 行）；`parse_approval_response`（61 行）；`format_approval_request`（70 行）；另有 1 个顶层公开符号 | 150 |
| [models.py](../../src/MiniClaw/coding_agent/approval/models.py) | 风险、审批请求、决策和 allowlist 数据结构 | `RiskFinding`（22 行）；`ApprovalRequest`（30 行） | 39 |
| [risk.py](../../src/MiniClaw/coding_agent/approval/risk.py) | shell/文件动作多能力风险识别与规范化 | `classify_tool_risks`（114 行）；`classify_tool_risk`（167 行）；`sanitize_preview`（179 行） | 222 |

### `coding_agent/assistant`

| 文件 | 责任 | 代表性源码符号 | 行数 |
|---|---|---|---:|
| [__init__.py](../../src/MiniClaw/coding_agent/assistant/__init__.py) | 包初始化与公开接口导出；不视为独立运行服务 | 导出、常量或内部辅助结构 | 4 |
| [coding.py](../../src/MiniClaw/coding_agent/assistant/coding.py) | 产品装配根与每次 run 的状态、记忆、Trace、Goal 协调 | `CodingAssistant`（54 行） | 1034 |
| [session.py](../../src/MiniClaw/coding_agent/assistant/session.py) | 简单追加 JSONL 会话存储兼容组件，主路径使用 WorkingContext | `JsonlSessionStore`（10 行） | 32 |

### `coding_agent/goal`

| 文件 | 责任 | 代表性源码符号 | 行数 |
|---|---|---|---:|
| [__init__.py](../../src/MiniClaw/coding_agent/goal/__init__.py) | 包初始化与公开接口导出；不视为独立运行服务 | 导出、常量或内部辅助结构 | 18 |
| [config.py](../../src/MiniClaw/coding_agent/goal/config.py) | Goal 尝试/时间/费用、验证及 Judge 配置 | `GoalConfig`（9 行）；`GoalJudgeConfig`（36 行）；`load_goal_config`（42 行）；`load_goal_judge_config`（58 行） | 127 |
| [judge.py](../../src/MiniClaw/coding_agent/goal/judge.py) | 独立无工具模型审查与严格结果解析 | `GoalJudgeDecision`（19 行）；`GoalJudge`（25 行） | 151 |
| [prompts.py](../../src/MiniClaw/coding_agent/goal/prompts.py) | Goal 命令解析、状态展示和监督提示 | `GoalCommand`（13 行）；`parse_goal_command`（20 行）；`format_goal_status`（65 行）；`build_goal_prompt`（97 行）；另有 3 个顶层公开符号 | 168 |
| [state.py](../../src/MiniClaw/coding_agent/goal/state.py) | Goal 状态、验收证据、验证记录的数据模型 | `GoalCheckpoint`（20 行）；`GoalVerification`（27 行）；`GoalCriterionEvidence`（39 行）；`GoalState`（45 行） | 170 |
| [store.py](../../src/MiniClaw/coding_agent/goal/store.py) | 目标状态持久化、预算与完成条件检查 | `GoalAttemptDecision`（31 行）；`GoalCompletionCandidate`（38 行）；`GoalVerificationBundle`（45 行）；`GoalStore`（50 行） | 526 |
| [supervisor.py](../../src/MiniClaw/coding_agent/goal/supervisor.py) | 外层 attempt 监督，区分自然停止与 Goal 完成 | `GoalSupervisor`（21 行） | 96 |
| [tools.py](../../src/MiniClaw/coding_agent/goal/tools.py) | goal 与 goal_complete 的 schema 和执行 | `GoalTool`（17 行）；`GoalCompleteTool`（71 行） | 153 |

### `coding_agent/instructions`

| 文件 | 责任 | 代表性源码符号 | 行数 |
|---|---|---|---:|
| [__init__.py](../../src/MiniClaw/coding_agent/instructions/__init__.py) | 包初始化与公开接口导出；不视为独立运行服务 | 导出、常量或内部辅助结构 | 11 |
| [config.py](../../src/MiniClaw/coding_agent/instructions/config.py) | 指令发现的开关、目录与预算配置 | `InstructionConfig`（14 行）；`load_instruction_config`（21 行） | 70 |
| [loader.py](../../src/MiniClaw/coding_agent/instructions/loader.py) | 分层规则加载、目标激活、缓存与渲染 | `ProjectInstructionLoader`（30 行） | 299 |
| [model.py](../../src/MiniClaw/coding_agent/instructions/model.py) | 指令来源、作用域、哈希和预算决议对象 | `InstructionSource`（13 行）；`InstructionResolution`（58 行） | 88 |

### `coding_agent/memory`

| 文件 | 责任 | 代表性源码符号 | 行数 |
|---|---|---|---:|
| [__init__.py](../../src/MiniClaw/coding_agent/memory/__init__.py) | 包初始化与公开接口导出；不视为独立运行服务 | 导出、常量或内部辅助结构 | 49 |
| [archive.py](../../src/MiniClaw/coding_agent/memory/archive.py) | 历史分块、动作块关联、邻块与父证据扩展 | `ArchiveMemoryRecord`（56 行）；`ArchiveMemoryHit`（71 行）；`ArchiveParentHit`（85 行）；`ArchiveMemoryIndex`（106 行）；另有 1 个顶层公开符号 | 680 |
| [artifacts.py](../../src/MiniClaw/coding_agent/memory/artifacts.py) | 上下文工件、引用、预览、归档与摘要标识 | `ContextArtifact`（24 行）；`ContextArtifactStore`（37 行）；`format_artifact_reference`（68 行） | 78 |
| [config.py](../../src/MiniClaw/coding_agent/memory/config.py) | 分层压缩水位、保留量与工件大小配置 | `MemoryConfig`（24 行）；`load_memory_config`（66 行） | 156 |
| [distillation.py](../../src/MiniClaw/coding_agent/memory/distillation.py) | 显式用户事实提取及可选模型事实/流程蒸馏 | `MemoryConsolidationConfig`（45 行）；`ConsolidationResult`（55 行）；`load_consolidation_config`（100 行）；`deterministic_episode_summary`（142 行）；另有 1 个顶层公开符号 | 412 |
| [episodic.py](../../src/MiniClaw/coding_agent/memory/episodic.py) | 按会话保存和检索情景摘要 | `EpisodicMemoryStore`（21 行） | 137 |
| [evidence.py](../../src/MiniClaw/coding_agent/memory/evidence.py) | 来源、作用域、置信度、过期和状态证据账本 | `contains_secret`（33 行）；`MemoryEvidenceRecord`（38 行）；`MemoryEvidenceStore`（76 行） | 230 |
| [locking.py](../../src/MiniClaw/coding_agent/memory/locking.py) | 本地记忆写入的跨进程文件锁工具 | `MemoryFileLock`（8 行） | 42 |
| [manager.py](../../src/MiniClaw/coding_agent/memory/manager.py) | 记忆装配、多源检索、动态刷新、预算展示和收尾 | `estimate_retrieval_tokens`（65 行）；`truncate_retrieval_text`（75 行）；`RetrievedMemoryItem`（94 行）；`MemoryManager`（102 行） | 861 |
| [procedural.py](../../src/MiniClaw/coding_agent/memory/procedural.py) | 技能目录、按需资源、候选流程和归档 | `SkillInfo`（14 行）；`ProceduralMemoryStore`（34 行） | 107 |
| [query_tracker.py](../../src/MiniClaw/coding_agent/memory/query_tracker.py) | 查询去重、相似度、缓存与新证据进展判断 | `normalize_query`（35 行）；`query_similarity`（42 行）；`SearchAttempt`（60 行）；`SearchProgress`（70 行）；另有 2 个顶层公开符号 | 191 |
| [retrieval.py](../../src/MiniClaw/coding_agent/memory/retrieval.py) | BM25/精确符号/hash或BGE向量、FAISS、RRF与重排 | `RetrievalConfig`（83 行）；`MemoryDocument`（100 行）；`RetrievalHit`（123 行）；`RerankResult`（139 行）；另有 12 个顶层公开符号 | 1990 |
| [semantic.py](../../src/MiniClaw/coding_agent/memory/semantic.py) | 稳定事实托管 Markdown、冲突处理与受控更新 | `MemoryConflict`（44 行）；`MemoryConflictError`（56 行）；`SemanticMemoryStore`（65 行） | 372 |
| [tools.py](../../src/MiniClaw/coding_agent/memory/tools.py) | memory/skill 工具契约、动作分派与来源记录 | `MemoryTool`（18 行）；`SkillTool`（293 行） | 314 |
| [working.py](../../src/MiniClaw/coding_agent/memory/working.py) | 持久对话、恢复视图、工件化与渐进压缩 | `estimate_message_tokens`（37 行）；`estimate_context_tokens`（46 行）；`CompactionOutcome`（62 行）；`WorkingContext`（70 行） | 597 |

### `coding_agent/runtime`

| 文件 | 责任 | 代表性源码符号 | 行数 |
|---|---|---|---:|
| [__init__.py](../../src/MiniClaw/coding_agent/runtime/__init__.py) | 包初始化与公开接口导出；不视为独立运行服务 | 导出、常量或内部辅助结构 | 30 |
| [config.py](../../src/MiniClaw/coding_agent/runtime/config.py) | 命令后端、工作区模式与资源限制配置 | `RuntimeSettings`（14 行）；`load_runtime_settings`（38 行） | 194 |
| [core.py](../../src/MiniClaw/coding_agent/runtime/core.py) | ToolRuntime 装配和宿主/执行路径映射 | `ToolRuntime`（16 行）；`create_tool_runtime`（79 行） | 119 |
| [execution.py](../../src/MiniClaw/coding_agent/runtime/execution.py) | 宿主进程与 Docker 命令执行、捕获、终止和清理 | `CommandExecution`（23 行）；`CommandExecutor`（29 行）；`terminate_process_tree`（39 行）；`HostCommandExecutor`（69 行）；另有 2 个顶层公开符号 | 391 |
| [smoke.py](../../src/MiniClaw/coding_agent/runtime/smoke.py) | 无 LLM 的 Runtime 集成验证入口 | `run`（16 行）；`build_parser`（79 行）；`main`（85 行） | 91 |
| [snapshot.py](../../src/MiniClaw/coding_agent/runtime/snapshot.py) | 一次性工作区副本创建、排除规则、预算和 manifest | `SnapshotManifest`（20 行）；`is_safe_snapshot_path`（32 行）；`prepare_snapshot_workspace`（42 行） | 135 |
| [state.py](../../src/MiniClaw/coding_agent/runtime/state.py) | 运行状态原子持久化、PID 中断识别与当前工具记录 | `RunState`（27 行）；`RunStateStore`（84 行） | 205 |
| [workspace.py](../../src/MiniClaw/coding_agent/runtime/workspace.py) | 路径规范化、访问类别、动态敏感路径和边界检查 | `is_sensitive_relative_path`（30 行）；`is_protected_relative_path`（53 行）；`WorkspaceGuard`（83 行） | 260 |

### `coding_agent/tools`

| 文件 | 责任 | 代表性源码符号 | 行数 |
|---|---|---|---:|
| [__init__.py](../../src/MiniClaw/coding_agent/tools/__init__.py) | 包初始化与公开接口导出；不视为独立运行服务 | 导出、常量或内部辅助结构 | 32 |
| [atomic.py](../../src/MiniClaw/coding_agent/tools/atomic.py) | 同目录临时写入、取消检查和原子文件替换 | `atomic_write_bytes`（11 行） | 28 |
| [base.py](../../src/MiniClaw/coding_agent/tools/base.py) | 具体工具协议和 ToolResult | `ToolResult`（12 行）；`Tool`（18 行） | 27 |
| [bash.py](../../src/MiniClaw/coding_agent/tools/bash.py) | 命令工具契约、Runtime 委派、尾部展示和长输出归档 | `BashTool`（26 行） | 96 |
| [edit.py](../../src/MiniClaw/coding_agent/tools/edit.py) | 精确/兼容文本块编辑与提交 | `EditTool`（26 行） | 96 |
| [edit_diff.py](../../src/MiniClaw/coding_agent/tools/edit_diff.py) | 唯一匹配、有限归一化、重叠检查、换行与 diff 生成 | `Edit`（17 行）；`AppliedEdits`（23 行）；`detect_line_ending`（28 行）；`normalize_to_lf`（36 行）；另有 5 个顶层公开符号 | 138 |
| [executor.py](../../src/MiniClaw/coding_agent/tools/executor.py) | 参数准备与校验、preflight、执行、结果转换、取消和输出上限 | `ToolExecutor`（22 行） | 256 |
| [factory.py](../../src/MiniClaw/coding_agent/tools/factory.py) | 装配六个编码工具 | `create_coding_tools`（17 行） | 27 |
| [search.py](../../src/MiniClaw/coding_agent/tools/search.py) | 无 shell 文件路径发现与保护过滤 | `SearchTool`（19 行） | 121 |
| [grep.py](../../src/MiniClaw/coding_agent/tools/grep.py) | 基于宿主 ripgrep 的内容搜索和有界管道处理 | `GrepTool`（19 行） | 187 |
| [manager.py](../../src/MiniClaw/coding_agent/tools/manager.py) | 角色级工具注册、注入、启用与 allow/deny 策略 | `ToolRolePolicy`（15 行）；`ToolManager`（37 行） | 169 |
| [mutation_queue.py](../../src/MiniClaw/coding_agent/tools/mutation_queue.py) | 同进程、同文件异步修改队列 | `with_file_mutation_queue`（28 行） | 41 |
| [read.py](../../src/MiniClaw/coding_agent/tools/read.py) | 按行读取文件、头部截断和图片类型识别提示 | `ReadTool`（15 行） | 95 |
| [truncate.py](../../src/MiniClaw/coding_agent/tools/truncate.py) | 字节/行数有界的头尾截断与提示 | `TruncationResult`（15 行）；`format_size`（32 行）；`truncate_head`（44 行）；`truncate_tail`（96 行）；另有 1 个顶层公开符号 | 141 |
| [workspace.py](../../src/MiniClaw/coding_agent/tools/workspace.py) | 工作区 Guard 的兼容导出 | 导出、常量或内部辅助结构 | 3 |
| [write.py](../../src/MiniClaw/coding_agent/tools/write.py) | 创建或整体覆盖 UTF-8 文件 | `WriteTool`（15 行） | 45 |

### `evaluation`

| 文件 | 责任 | 代表性源码符号 | 行数 |
|---|---|---|---:|
| [__init__.py](../../src/MiniClaw/evaluation/__init__.py) | 包初始化与公开接口导出；不视为独立运行服务 | 导出、常量或内部辅助结构 | 13 |
| [cli.py](../../src/MiniClaw/evaluation/cli.py) | 选择、并行、重复、基线与端到端评测入口 | `build_parser`（15 行）；`main`（86 行） | 91 |
| [models.py](../../src/MiniClaw/evaluation/models.py) | 端到端 Eval case/suite/check/budget 配置结构 | `EvalPhase`（82 行）；`EvalCheck`（89 行）；`EvalCase`（97 行）；`EvalSuite`（116 行）；另有 1 个顶层公开符号 | 399 |
| [runner.py](../../src/MiniClaw/evaluation/runner.py) | 隔离任务运行、故障注入、五维检查、重复与结果聚合 | `run_eval_suite`（112 行）；`evaluate_check`（442 行） | 1338 |
| [splits.py](../../src/MiniClaw/evaluation/splits.py) | 固定数据划分、上游锁定与泄漏/曝光核验 | `LongMemQuestion`（50 行）；`build_split_manifest`（224 行）；`validate_split_manifest`（437 行）；`track_commands`（528 行）；另有 2 个顶层公开符号 | 584 |

### `llm`

| 文件 | 责任 | 代表性源码符号 | 行数 |
|---|---|---|---:|
| [__init__.py](../../src/MiniClaw/llm/__init__.py) | 包初始化与公开接口导出；不视为独立运行服务 | 导出、常量或内部辅助结构 | 37 |
| [cancellation.py](../../src/MiniClaw/llm/cancellation.py) | 模型层取消类型的兼容导出 | 导出、常量或内部辅助结构 | 6 |
| [client.py](../../src/MiniClaw/llm/client.py) | 模型客户端流式接口协议 | `ModelClient`（9 行） | 12 |
| [config.py](../../src/MiniClaw/llm/config.py) | provider 路由、超时、重试、fallback、价格与模型预算配置 | `LLMFallbackSettings`（17 行）；`LLMSettings`（28 行）；`load_llm_settings`（103 行） | 289 |
| [env_file.py](../../src/MiniClaw/llm/env_file.py) | 读取 dotenv 并合并进程环境，避免直接改全局环境 | `read_env_file`（8 行）；`merged_environment`（41 行） | 49 |
| [factory.py](../../src/MiniClaw/llm/factory.py) | 把配置装配成具体客户端和模型 Profile | `create_model_client`（8 行）；`model_profile_from_settings`（37 行） | 45 |
| [openai_compatible.py](../../src/MiniClaw/llm/openai_compatible.py) | Chat Completions SSE、增量聚合、超时、取消与路由容错 | `OpenAICompatibleRoute`（27 行）；`OpenAICompatibleClient`（240 行） | 792 |
| [smoke.py](../../src/MiniClaw/llm/smoke.py) | 真实模型最小传输验证入口 | `run_smoke`（16 行）；`main`（47 行） | 56 |
| [types.py](../../src/MiniClaw/llm/types.py) | 消息、调用、usage、请求、回复、profile 与模型事件契约 | `ToolInvocation`（17 行）；`ChatMessage`（24 行）；`TokenUsage`（33 行）；`AssistantReply`（40 行）；另有 3 个顶层公开符号 | 78 |

### `platforms`

| 文件 | 责任 | 代表性源码符号 | 行数 |
|---|---|---|---:|
| [__init__.py](../../src/MiniClaw/platforms/__init__.py) | 包初始化与公开接口导出；不视为独立运行服务 | 导出、常量或内部辅助结构 | 1 |
| [delivery.py](../../src/MiniClaw/platforms/delivery.py) | 本地持久 outbox、key 锁、有限发送重试与产物提示 | `DeliveryManager`（24 行） | 145 |

### `platforms/feishu`

| 文件 | 责任 | 代表性源码符号 | 行数 |
|---|---|---|---:|
| [__init__.py](../../src/MiniClaw/platforms/feishu/__init__.py) | 包初始化与公开接口导出；不视为独立运行服务 | 导出、常量或内部辅助结构 | 4 |
| [bot.py](../../src/MiniClaw/platforms/feishu/bot.py) | 官方 SDK 长连接、工作线程与路由连接 | `FeishuBot`（13 行） | 169 |
| [cli.py](../../src/MiniClaw/platforms/feishu/cli.py) | 飞书产品启动入口 | `run`（19 行）；`build_parser`（85 行）；`main`（103 行） | 108 |
| [config.py](../../src/MiniClaw/platforms/feishu/config.py) | 飞书凭据、会话/消息策略和入口配置 | `FeishuSettings`（14 行）；`load_feishu_settings`（35 行） | 107 |
| [dedupe.py](../../src/MiniClaw/platforms/feishu/dedupe.py) | 入站 message_id 去重状态的本地持久记录 | `PersistentEventDeduplicator`（11 行） | 108 |
| [models.py](../../src/MiniClaw/platforms/feishu/models.py) | 平台消息结构与文字提取归一化 | `FeishuConversation`（15 行）；`FeishuInboundMessage`（22 行）；`sanitize_session_key`（44 行）；`build_conversation`（49 行）；另有 1 个顶层公开符号 | 107 |
| [router.py](../../src/MiniClaw/platforms/feishu/router.py) | 会话队列、并发上限、审批/取消控制与回复 | `FeishuAssistantRouter`（36 行） | 387 |
| [transport.py](../../src/MiniClaw/platforms/feishu/transport.py) | 飞书回复、更新与发送结果适配 | `FeishuApiError`（12 行）；`FeishuTransport`（17 行） | 102 |

### `trace`

| 文件 | 责任 | 代表性源码符号 | 行数 |
|---|---|---|---:|
| [__init__.py](../../src/MiniClaw/trace/__init__.py) | 包初始化与公开接口导出；不视为独立运行服务 | 导出、常量或内部辅助结构 | 14 |
| [analysis.py](../../src/MiniClaw/trace/analysis.py) | 聚类、聚合、基线回归和 JSON/Markdown/HTML 报表 | `group_trace_runs`（16 行）；`load_trace_runs`（66 行）；`create_eval_case`（70 行）；`migrate_legacy_traces`（140 行）；另有 3 个顶层公开符号 | 946 |
| [cli.py](../../src/MiniClaw/trace/cli.py) | Trace 迁移、提取、回放和报告命令入口 | `build_parser`（14 行）；`main`（82 行） | 87 |
| [model_client.py](../../src/MiniClaw/trace/model_client.py) | 模型包装与逻辑请求/传输尝试关联 | `set_active_run`（18 行）；`reset_active_run`（22 行）；`TracingModelClient`（26 行） | 132 |
| [replay.py](../../src/MiniClaw/trace/replay.py) | 失败 Eval Case 提取与不执行工具的模型边界回放 | `replay_trace`（26 行） | 459 |
| [store.py](../../src/MiniClaw/trace/store.py) | 统一事件存储、脱敏、标识、usage 和费用记录 | `TraceRecorder`（35 行）；`read_trace_records`（189 行）；`sanitize_trace_value`（211 行）；`sanitize_string`（248 行）；另有 5 个顶层公开符号 | 307 |


---

<!-- MINICLAW_INTERVIEW_RESEARCH_APPENDIX_20260905 -->

<a id="exp-section-01"></a>

## 新增专题：从国内真实面经与招聘要求，反推 MiniClaw 会被怎样追问

> 本轮检索与核验日期：2026-09-05。原有章节和 65 道问答保留，以下内容追加在原文最后。新的题目侧重情境推演、源码局限、设计改进和实验验证；每题回答由招聘信号或公开面经结合 MiniClaw 重新组织，不是复制原帖答案。

这次研究的目标不是收集最多的“Agent 八股”，而是回答：**一个招聘 Agent 实习生的团队，看到你的 MiniClaw 项目之后，能用什么问题分辨你是会调用模型，还是理解并能维护一套 Agent 系统。**

公开面经能提供“别人实际转述过什么问题”的线索，招聘说明能提供“团队希望你进去做什么”的线索，两者不能互相替代。官方岗位职责也不能证明某个面试官一定会问某题；因此下面明确区分原始信号、项目推导和技术回答。

本轮采用 **24 个不同的可读正文页面**：8 个官网岗位、6 个第三方实习职位页、10 篇候选人自述帖子。其中 1 篇复用原文已引用的面经，另有 23 个页面是相对原文新加入的来源。这是围绕项目筛选的公开样本，不是对所有网站、公司和岗位的穷尽收集。

### 本轮怎样筛选与转化

采用五步筛选：

1. **先判断岗位方向。** 优先 Agent 应用研发、Coding Agent、AI 工程、Agent 平台、检索与记忆、评测实习等方向；研究岗中只保留和你的项目能建立直接联系的要求。
2. **再确认来源内容。** 能读取正文的具体岗位或原帖才用于支撑事实。网站首页、推荐区、只有标题的搜索摘要，以及验证码页面不当成已经读过的 JD 或面经。
3. **找独立重复信号。** 同一岗位在不同平台的转载不能算多个独立需求，同一作者转发自己的面经也不能证明问题更常见。
4. **映射到真实代码。** 要求“稳定交付”，就问 Goal、验证、恢复与副作用；要求“效果优化”，就问数据、指标、消融与归因；要求“检索”，就追到切块、融合、重排、权限和缓存。
5. **把名词题变成有条件的问题。** 不停留在“什么是 RAG”，而是问“同一 query 写入新记忆后仍返回旧结果，你在哪里失效缓存、怎样证明没有串用户”。

下文的“高优先级”表示这些主题与 MiniClaw 强相关，并能由公开要求或面经支持。它是复习排序，不是全国面试题出现概率；没有随机抽样调查，就不编“必问率 90%”。岗位页面可访问，也不代表岗位当日仍有编制、允许投递或尚未招满，实习资格和在招状态仍以招聘方后续确认信息为准。

### 招聘条件如何变成针对你的问题

| 招聘材料中的能力信号 | 面试官实际想判断什么 | MiniClaw 最容易被追问的位置 | 你应该准备的证据 |
|---|---|---|---|
| 熟悉 Agent 框架与工具使用 | 你是否理解执行协议和边界，还是只会调库 | AgentLoop、ToolManager、动态工具定义、调用与结果配对 | 一条成功轨迹和一条失败轨迹，指出每一层的职责 |
| 有实际应用落地经验 | 能不能在长任务和故障时持续正确工作 | 飞书队列、审批控制消息、取消、run-state、outbox | 超时/取消/重复消息的状态变化与实际副作用 |
| 熟悉 RAG、检索、知识库 | 能否定位证据为什么没进最终答案 | BM25、hash/BGE、FAISS、RRF、rerank、rendered 预算 | 召回前、排序后、真正展示给模型的三份证据对比 |
| 有 Memory/Context 管理经验 | 能否处理更新、过期、压缩损失和信任 | 四种记忆、archive、QueryTracker、compaction checkpoint | 跨会话、跨压缩、写入后再次查询的可复现场景 |
| 有评测与优化意识 | 能否定义成功、做实验并避免过拟合 | Trace、五维 Eval、固定 split、历史 baseline | 固定配置、分母、坏例分类、消融和未见集结果 |
| 熟悉 Python/异步/服务端 | 能否处理真实 I/O 和资源竞争 | SSE、同步检索、线程/进程、会话队列与信号量 | 阻塞点、队列上限、并发上限和取消时序 |
| 关注性能与成本 | 是否知道优化了哪个瓶颈和牺牲了什么 | transport attempt、长输入、索引、摘要/Judge 请求 | 正确率、TTFT、任务耗时、token、费用的联合比较 |
| 具备安全与隔离意识 | 是否理解授权、执行环境和证据污染的差别 | WorkspaceGuard、Docker、审批、历史角色、scope | 越界/误拦/漏拦样例，明确哪些保证仍未实现 |
| 了解模型与训练基础 | 能否解释模型行为并选择恰当工具 | 模型适配、结构化生成、embedding 与 reranker | 解释使用条件、误差和成本；训练经历另行证明 |
| 能快速学习和协作交付 | 是否能把原型变成可审查的代码与实验 | 包边界、单元测试、工作流、问题复盘 | 可运行最小示例、清晰变更、真实失败与修复记录 |

这张表是项目映射表；每一项具体的 JD/面经依据在后续来源登记和题目中标明。它不表示每个被检索的公司都要求表中的全部能力。

### 哪些主题有跨来源重复信号，应该优先复习

下表把“高频”落实为可回查的重复线索。J 是本轮官网岗位，C 是新读到的候选人帖子，P 是补充平台材料；完整链接见后面的来源表。它不做全国频次排名，也不把同一篇帖子的多个问题算成多份证据。

| 重复出现的主题 | 能回查的不同来源 | 对 MiniClaw 应当内化成什么追问 |
|---|---|---|
| 评测、坏例与可复现收益 | J01/J02/J04/J05、C03/C05、P01 | 你如何定义任务成功？改进来自组件还是数据泄漏？错误发生在哪个节点？ |
| 工具边界、调用正确性与大工具集 | J02、C05、P02/P09 | JSON正确为什么还会做错？工具太多如何筛选？谁有权执行副作用？ |
| 检索与长期记忆质量 | J08、C01/C04/C07、P01 | 没召回、排错、被截断和生成误用怎么区分？旧记忆如何更新或删除？ |
| 上下文压缩、约束保留与缓存 | C02/C03/C04/C07、P09 | 压缩后仍超窗怎么办？摘要是否改变信任级别？省token是否损害成功率？ |
| 长任务恢复、重复执行与状态一致性 | C06/C07、P05/P06 | 外部成功但本地记录丢了怎么办？旧worker恢复后能不能继续写？ |
| 时延、吞吐与每成功任务成本 | J02/J04/J06、C04/C06、P05/P07 | 为什么加并发反而变慢？如何分层定位？降低总费用是否真的更划算？ |

其中 J06/J08 是社招岗位，P07 是 Go 岗位，P09 复用原文 S4。它们只提供能力方向和延伸追问信号；实习准备优先看对应的实习 JD 和与本项目直接相关的代码证据。C06 与原文 S6 同作者，其新帖不能当成又一名独立候选人。这些相关性限制也应保留，不能为了突出“高频”隐去。

### 应用研发实习与模型算法实习，不能混着准备

**Agent 应用研发 / 平台实习：** MiniClaw 的直接相关度高。重点准备 Python 工程、工具协议、长任务状态、检索记忆、权限、恢复、评测和性能。面试官可以追问设计缺口，但通常允许你提出尚未落地的合理演进方案，前提是说清“现在有”和“计划做”。

**RAG / 知识工程实习：** 项目的混合检索、证据来源和预算管理是入口，但面向真实文档时还可能需要解析、OCR、结构化表格、数据清洗、增量更新、权限过滤和索引发布。不要把本地聊天记忆直接包装成已上线的大型文档平台。

**Agent 算法 / 后训练研究实习：** MiniClaw 能证明你理解任务环境和 rollout/评测的数据生成过程，但不能单凭它证明 SFT、偏好优化、强化学习、训练稳定性或分布式训练经验。如果 JD 主要工作是训练而你没有做过，应如实讲项目边界，同时准备训练数据如何从 Trace 派生、为什么只用成功轨迹不够、奖励与任务验收怎样定义等连接问题。

**Coding Agent / AI 软件工程实习：** 最适合展示完整修复轨迹、文件修改正确性、独立验证、仓库指令、真实工具与恢复逻辑。面试官很可能关心你能否解释某个失败窗口，而不满足于看见一次“修复成功”的演示。

### 新题怎样回答才像真正维护过项目的人

每题尽量沿着这条线讲：

```text
明确业务目标与前提
→ 解释当前实现
→ 找出一个具体失败窗口
→ 提出最小可行改进
→ 说明怎样验证收益及回归风险
→ 交代还不能保证什么
```

例如，不能只说“我会加 Redis”。应该说“当前去重状态的读—判断—写不是跨进程原子操作；如果扩到多个 worker，需要一个具唯一约束或原子 claim 能力的持久存储，并带租约和 fencing token，避免旧 worker 续写；是否采用 Redis 取决于持久性、事务边界和恢复要求”。技术名词是方案中的工具，业务不变量才是回答的主体。

新增题的示例数字用于讲计算方法，未明确标注实验来源的数字都不是本次实测成绩。源码引用用于说明现状，扩展方案不因此成为已经实现的功能。

### 本轮新增 34 题与专题导航

O 系列从官方岗位要求推导，R 系列从新面经提炼为记忆与检索深问，PQA 系列从公开实习岗位与工程面经转为落地情境题。相关知识可以交叉阅读，来源信号不等于公司规定的标准面试题。

- [新增专题：从国内真实面经与招聘要求，反推 MiniClaw 会被怎样追问](#exp-section-01)
- [从国内官方招聘要求反推：面试官会怎样继续深挖 MiniClaw](#exp-section-02)
- [新增公开面经依据：这批问题具体来自哪里](#exp-section-03)
- [针对 MiniClaw 源码局限的十二道深度扩展题](#exp-section-04)
- [这十二题怎样练到能连续追问](#exp-section-05)
- [公开招聘平台与面经补充：把工程要求变成 MiniClaw 情境设计题](#exp-section-06)
- [结合本轮材料，你的复习优先级应该怎样排](#exp-section-07)
- [把“我会回答”变成“我能证明”：一份项目面试证据包](#exp-section-08)
- [本轮研究结论应当怎样使用](#exp-section-09)

| 新题编号 | 题目 |
|---|---|
| O01 | [岗位要求建设 Golden Sample。你会怎样把 MiniClaw 的 JSON 题集升级为可维护的评测资产？](#exp-o01) |
| O02 | [新模型、新提示词、新记忆策略一起上线，你怎样证明提升来自哪里，并决定是否发布？](#exp-o02) |
| O03 | [J01 同时写工作流质量、节点质量和模型输出质量。它们有什么不同，你怎样用 MiniClaw 区分？](#exp-o03) |
| O04 | [把 trace.jsonl 做成真正能排障的 Agent debugging 产品，你会补哪些能力？](#exp-o04) |
| O05 | [一天要并发跑几千个 Coding Agent 评测，怎样把 MiniClaw 的 Docker Runtime 做成沙盒平台？](#exp-o05) |
| O06 | [你说能做 badcase 归因。怎样防止把第一个报错当成真正根因？](#exp-o06) |
| O07 | [官方实习JD提到 verifier 引导搜索与算力—质量曲线。MiniClaw 的 Goal 能直接叫自进化 Agent 吗？](#exp-o07) |
| O08 | [如果评分模型本身会胡说、被注入、返回错误类型，你怎样保证评测报告可信？](#exp-o08) |
| O09 | [收到用户点赞、追问、撤销和投诉，你怎样把反馈变成改进数据，而不是污染记忆？](#exp-o09) |
| O10 | [实习职责要求定义工具能力边界。给 MiniClaw 接一个企业业务工具，你会怎样完成从接口到上线？](#exp-o10) |
| O11 | [官方岗位强调 AI Coding，但同时要求代码质量。你怎样证明自己会用AI提高研发效率，又没有把质量交给AI运气？](#exp-o11) |
| O12 | [如果只给你三个月 Agent 实习，你会怎样用 MiniClaw 交付一个能被团队接着维护的成果？](#exp-o12) |
| R01 | [中文问题夹着英文错误码与 Windows 路径，怎样证明你的分词和混合检索没有把关键区别抹掉？](#exp-r01) |
| R02 | [正确证据明明在 Top-20，模型为什么仍答错？如何定位分块、父块扩展和展示预算之间的损失？](#exp-r02) |
| R03 | [删掉一条长期事实后重启 Agent，它又从归档里记起来了：你会怎样设计可验证的删除与索引更新协议？](#exp-r03) |
| R04 | [Embedding 换了模型或 query prefix，怎样避免复用错误缓存？怎么证明真实 BGE 的收益，而不是冷启动差异？](#exp-r04) |
| R05 | [Cross-Encoder 只精排前 48 条，没被精排的候选还能参与最终排序吗？如何检查分数尺度造成的反直觉结果？](#exp-r05) |
| R06 | [两次 query 归一化后一样，但路径含义不同，或者记忆刚被更新：你的 QueryTracker 会不会复用错误结果？](#exp-r06) |
| R07 | [中文任务压缩后仍超限，甚至 soft 阶段反复生成归档：你会怎样设计预算守卫与有界退化？](#exp-r07) |
| R08 | [原本不可信的工具内容进入 system 检查点，会不会在压缩时被当成更高权限的指令？怎样测试这种信任变化？](#exp-r08) |
| R09 | [蒸馏事实有原文引用、confidence 也超过 0.90，为什么仍可能错？怎样评估接纳与撤销策略？](#exp-r09) |
| R10 | [SQLite 已提交向量，FAISS 文件还没更新就崩溃；两个进程同时建索引又会怎样？你如何设计故障注入测试？](#exp-r10) |
| R11 | [Agent 刚跑过测试又修改了文件，旧的成功记录还能作为验收吗？通过 shell 或外部编辑发生的变化怎么办？](#exp-r11) |
| R12 | [你的检索准确率提升 5 个点，是组件有效，还是题库泄漏、缓存预热或随机波动？怎样构建能接受追问的实验？](#exp-r12) |
| PQA01 | [飞书突发来了100个任务，你把 async worker 从8加到80，为什么可能更慢？](#exp-pqa01) |
| PQA02 | [八个任务都在等审批，用户的“取消”消息为什么必须走另一条路？迟到的批准怎么办？](#exp-pqa02) |
| PQA03 | [测试命令每秒输出2 MiB、持续50秒，同时取消发生，怎么避免把Agent服务拖死？](#exp-pqa03) |
| PQA04 | [把MiniClaw搬到两个worker，消息已处理但进程在写completed前宕机，如何避免重复副作用？](#exp-pqa04) |
| PQA05 | [JSON schema已经通过，但工具参数仍把任务做错，你怎样设计“参数成功率”指标？](#exp-pqa05) |
| PQA06 | [从10个工具扩到500个MCP工具，怎么在不漏掉必要工具的前提下减少token？](#exp-pqa06) |
| PQA07 | [要把MiniClaw接到实时任务面板，断线重连后怎样避免“还在跑”和“已取消”同时显示？](#exp-pqa07) |
| PQA08 | [Goal已complete、测试全绿，但用户本地代码后来变了，你敢直接把Snapshot覆盖回去吗？](#exp-pqa08) |
| PQA09 | [你说“小模型压缩更省钱”，给你1000个任务，怎样算总成本并验证没把成功率压没了？](#exp-pqa09) |
| PQA10 | [请求量翻四倍、吞吐没变、P95暴涨，你如何在一天内找出MiniClaw真正的瓶颈？](#exp-pqa10) |

<a id="exp-section-02"></a>

## 从国内官方招聘要求反推：面试官会怎样继续深挖 MiniClaw

这一组补充题来自**具体官方岗位的职责与要求**，然后转换成 MiniClaw 场景题；它们不是招聘方公开的面试真题。核验日期为 2026-09-05，样本包含 4 条日常实习、1 条校招和 3 条社招。前两类决定应届生与实习的准备重点；社招用于理解系统做大以后需要什么，不意味着实习生必须满足两年、五年工作经验。

这次官网样本的共同信号很清楚：企业会把“做一个能聊天、能调工具的 Demo”，继续追问到**评测资产、坏例归因、可验证交付、引擎与平台、团队协作**。下面不重复前文的 ReAct、RAG、MCP 基础定义，而是把已经讲过的模块放进真实研发职责里。涉及扩展时明确写“我会如何设计”，不把未实现的生产能力说成项目成绩。

### 本组官方岗位来源与使用方式

| 编号 | 具体官方岗位 | 招聘类型与时间 | 已核验的岗位信号 |
|---|---|---|---|
| J01 | [百度 ACG：Agent应用开发实习 J105397](https://talent.baidu.com/jobs/detail/INTERN/83a5dbcc-48a6-4356-b6d6-b4da9f0a2528) | 日常实习；发布/更新 2026-09-03 | Golden Sample、工作流结果/节点/模型输出分层评估、人工反馈、质量扫描、问题归因、状态追踪与归档 |
| J02 | [百度 MEG：AI Agent研发实习生 J105162](https://talent.baidu.com/jobs/detail/INTERN/15f823cf-26b3-4fc5-bc2d-832fed50e7f9) | 日常实习；发布/更新 2026-08-28 | 工具能力边界、接口与数据源、badcase根因、成功率/幻觉率/时延/成本、可维护代码与跨角色协作；正文要求每周 3 天以上、持续 3 个月以上 |
| J03 | [百度 ACG：Dumate Agent研发实习生 J104380](https://talent.baidu.com/jobs/detail/INTERN/1bc72acb-643b-4db0-831d-3c0e44a4149d) | 日常实习；发布/更新 2026-08-10 | Agent后端与引擎、需求沟通、数据库、编码习惯；Docker/Kubernetes生态经验是优先项 |
| J04 | [百度 ACG：Agent算法实习生 J103894](https://talent.baidu.com/jobs/detail/INTERN/70a8d8a6-5aff-47ad-88c8-24aa47ab1812) | 日常实习；发布/更新 2026-07-29 | 难任务族、可复现benchmark、失败归因、verifier引导搜索、算力—质量曲线、轨迹学习；偏算法研究与工程结合 |
| J05 | [百度：北京-AI测试开发工程师 J101055](https://talent.baidu.com/jobs/detail/GRADUATE/6061be03-f92b-4430-a1b8-9168de0f91f6) | 校招；发布 2026-07-08，更新 2026-07-21 | 模型测试资产库、专项数据/脚本/指标、故障注入、输出质量/多轮一致性/鲁棒性/性能、质量前置 |
| J06 | [腾讯 TEG：混元AI Agent Harness Engineer](https://careers.tencent.com/jobdesc.html?postId=2052685072754196480) | 社招，接口写“两年以上工作经验”；最后更新 2026-09-01，未显示首次发布日期 | 全链路tracing/observability、自动化eval、A/B testing、regression detection、debugging、代码可读性与架构性 |
| J07 | [腾讯 TEG：Agent Infra高级研发工程师](https://careers.tencent.com/jobdesc.html?postId=2086743606953160704) | 社招，两年以上工作经验；最后更新 2026-08-10 | 大规模沙盒、高并发任务稳定性、评估bench与运行环境接入、实验复现、Linux/Docker/Kubernetes与资源调度 |
| J08 | [腾讯 IEG：Agent开发工程师](https://tencent.wd1.myworkdayjobs.com/Tencent_Careers/job/China-Shenzhen/Agent_R108051-1) | 社招，正文要求五年以上软件研发经验；最后更新 2026-09-03 | 可实现/可验证/可量化的全栈交付、内部权限身份集成、Benchmark与观测、CI/CD、灰度、故障恢复、审计和数据隔离 |

百度证据读取的是各详情页 `postInfo` 对应岗位正文；腾讯证据读取的是 `careers.tencent.com` 官方按 `postId` 返回的职责和要求，J08 返回其官方 Workday 岗位链接。不是从首页猜要求，也没有把推荐列表中的其他岗位算进去。这是目的性选取的岗位样本，可以证明需求确实存在；不能据此统计“全国多少百分比面试会问”。

<a id="exp-o01"></a>

### O01：岗位要求建设 Golden Sample。你会怎样把 MiniClaw 的 JSON 题集升级为可维护的评测资产？

**岗位信号与为什么会追问：**J01 直接要求“支持构建评估样本、Golden Sample、评分规则和评估报告”，J05 要求建设模型测试资产库。简历如果写“搭建评测体系”，面试官就可能让你讲一条题从产生到失效的全生命周期，而不是只问 Eval 是什么。

**90 秒回答：**“我会先把题当成一个有版本、有来源、有负责人、有验收边界的数据资产。每条题记录任务输入、环境初始状态、可用工具/权限、评分方式、标准结果或不变量、所属任务族以及是否暴露。MiniClaw 现在的 EvalCase 已经承载 fixture、phases、checks、预算、source 和能力标签，是很好的执行契约；我会在外层补题目审核、数据版本、失效与替换流程。Golden Sample 是经过人工确认的高可信样本，用来校准评分器，不是让模型背一份唯一措辞。最后把题集版本、代码版本、模型和运行环境共同写进每次报告，才能比较两次实验。”

**深入知识与方案：**

1. 先区分“标准答案”和“可接受结果集合”。加法题可以精确验算；修仓库可能有多种正确 patch，应检查测试、接口契约和改动边界；业务问答应记录可引用的证据、允许的不确定性及拒答条件。
2. 一个样本应有 `case_id + case_version`，记录来源、创建原因、审核人、标签、环境内容哈希、checker 版本。题意不变但修正验收器也要版本化，否则旧分数失去解释基础。
3. 将采集、审核、执行、归档分开。用户坏例进入候选池不等于立即进入阻断回归；先确认事实、可复现性与隐私处理，再进入适当轨道。重复任务按任务族去重，不能同一坏例改写十遍就声称覆盖增加十倍。
4. 给评分器建立正例、反例和边界例。比如“输出包含测试成功”与“测试命令真实退出成功”要故意构造反例区分；缺少证据时应该判未知或失败，而不是奖励自信表达。

**源码边界：**[evaluation/models.py](../../src/MiniClaw/evaluation/models.py) 有执行数据结构；[evaluation/splits.py](../../src/MiniClaw/evaluation/splits.py) 有切分和曝光登记；还没有多人样本审核平台、审批工作流或完整数据目录服务。当前 `capabilities` 是人工声明的标签，不能拿标签存在当完整覆盖证明。

**后续追问：**“产品改了，旧题怎么办？”把需求变更和模型退化分开；旧题标失效并说明原因，新旧题集可短期双跑桥接，不能无痕删除失败题。“留出集突然出现已看过的题呢？”移出盲测、更新曝光并替换同类新样本，保留变更记录。

<a id="exp-o02"></a>

### O02：新模型、新提示词、新记忆策略一起上线，你怎样证明提升来自哪里，并决定是否发布？

**岗位信号与为什么会追问：**J06 明确自动化评测、A/B 和回归发现，J08 明确 CI/CD 与灰度，J05 强调全流程质量。MiniClaw 已有 baseline 和多个消融适配器，很容易被追问“有脚本之后，发布流程怎么闭合”。

**90 秒回答：**“我先冻结比较条件，拆成可归因的改动。若同时换模型、提示和压缩策略，最终分数变好也不知道是哪一项的贡献。先在相同题目、初始工作区和预算下做配对离线实验，记录题目级升降、成本、失败类型与尾延迟；关键能力不能被总平均掩盖。通过预先约定的发布门槛后，再对真实流量做有限灰度，并设错误率、危险副作用、成本和成功率的回滚条件。MiniClaw 的回归告警可以作为第一层门禁，但离线阈值不是统计显著性，也没有自动替我完成线上灰度。”

**深入知识与方案：**同一批题上比较新旧系统，可以统计“旧过新不过”和“旧不过新过”的数量，而不仅比较两次总通过率；同题重复用于估计随机波动，独立任务数量仍应单独报告。先约定主要指标和最低可接受差异，不能看完结果后挑一个涨得最多的维度作为主指标。

发布包至少固定代码提交、prompt/template版本、模型标识/参数、工具schema版本、记忆与索引版本、runtime镜像、评测集/checker版本。对缓存与持久记忆影响大的改动，应隔离实验组的存储，避免 A 组写的记忆提高 B 组分数。只读的影子执行可以观察新模型决策；有外部副作用的工具不能把线上业务操作执行两次来做 A/B，应采用受控副本、模拟工具或只比较读路径。

**源码边界：**[evaluation/runner.py](../../src/MiniClaw/evaluation/runner.py) 能比较历史 baseline；[benchmark/code_compression.py](../../src/MiniClaw/benchmark/code_compression.py) 固定模型与 scaffold 比较压缩；[benchmark/ai_efficiency.py](../../src/MiniClaw/benchmark/ai_efficiency.py) 比较产物策略。当前不存在完整部署编排、实时指标回滚和实验分桶服务；汇总 p95 也不是从全局原始延迟重算，发布前需正确解释统计口径。

**后续追问：**“新模型总分提高，但支付/删除相关安全用例退化呢？”这类能力设独立硬门槛，不能以其他题的进步抵消。“灰度用户不满意而离线分数提高？”检查线上分布、数据时效与代理指标错位，必要时回滚并补样本，而不是先指责用户不会提问。

<a id="exp-o03"></a>

### O03：J01 同时写工作流质量、节点质量和模型输出质量。它们有什么不同，你怎样用 MiniClaw 区分？

**岗位信号与为什么会追问：**J01 把三层评估直接列成实习职责；J02 要求遇到 badcase 主动定位根因。若只报最终准确率，面试官会问“到底哪里错了，哪个模块该改”。

**90 秒回答：**“最终任务质量回答用户有没有拿到正确交付；节点质量回答某一步是否履行它的局部契约；模型输出质量回答该次文本、结构化参数是否符合要求。三者不是一个数。MiniClaw 可以用文件和命令检查最终成果，用 Trace 检查模型、检索、工具、审批、压缩等节点，再用结构或rubric检查具体输出。例如读错文件导致修错函数，执行器可能完全正确地执行了错误意图，这应该归因到证据选择或规划，而不是说工具调用失败。”

**深入知识与方案：**给每类节点定义局部契约：检索节点关注相关证据覆盖、来源与预算；模型决策节点关注可解析、参数正确和动作适用；执行节点关注前置权限、运行完成、副作用；验证节点关注独立性、结果解析和覆盖；交付节点关注结果是否送达及陈述是否忠实。

建立一张“题目→运行→节点→证据”的关联表，失败时先定位最早偏离契约的节点，再看后续是否恢复。工具第一次失败但模型纠正了参数并成功，最终任务应算成功，节点失败仍应统计为额外成本。反过来，所有请求 HTTP 200、工具全退出 0，仍可能改错业务逻辑，因此 operational success 不能替代 task success。

不要把节点错误率简单相乘成整链路成功率：节点之间并不独立，重复调用又受前面反馈影响。比较两版时还要看“有机会执行该节点的样本集合”，否则新版本提前失败，工具错误率反而可能因为工具没机会运行而下降。

**源码边界：**[trace/model_client.py](../../src/MiniClaw/trace/model_client.py) 记录模型调用；[evaluation/runner.py](../../src/MiniClaw/evaluation/runner.py) 支持 outcome/Trace/metric 检查；`trace_sequence` 只检查有序子序列，不自动证明每个中间步骤合理。项目没有自动推断所有节点语义契约的评分器，需要按任务补规则与标注。

**后续追问：**“结果对了但乱试了 50 次工具，要不要过？”结果维度可过，效率/过程可能不过；发布门槛按业务设置。“节点都好为什么结果错？”先检查局部契约是否太弱，以及跨节点状态、信息丢失和任务理解是否被漏测。

<a id="exp-o04"></a>

### O04：把 trace.jsonl 做成真正能排障的 Agent debugging 产品，你会补哪些能力？

**岗位信号与为什么会追问：**J06 直接写全链路 observability 和 debugging；J08 写 Tracing、日志监控与反馈闭环。MiniClaw 有完整事件基础，但“有日志”和“研发能在五分钟内定位故障”之间还差一段产品与数据工程。

**90 秒回答：**“我会先从真实排障任务设计界面：给定一次失败，看到时间线、当前目标、模型请求与工具调用、审批等待、压缩前后变化以及最终验收。默认先展示结构化摘要，按需展开原始证据，避免用户在几十万字日志里找线索。数据层明确 session/run/request/tool_call 的关联和时间口径，能按模型版本、任务族、错误签名过滤。Metrics 给趋势，Trace解释单次过程；同时观测Trace写入是否失败，否则看板安静可能只是日志丢了。”

**深入知识与方案：**时间线应使用真实动作开始时间和持续时间构造区间，不只是按“日志行写入时间”排列：MiniClaw 的模型请求在流结束后的 `finally` 才统一记录，而 `started_at` 描述开始时刻。重试属于同一逻辑请求的多个 transport attempts，需要同时显示“逻辑请求慢”与“第几次尝试失败”。

可做四个视图：①从用户输入到验收的关键路径；②相邻模型请求的上下文差异，看哪些约束被加入/丢弃；③工具入参、审批决定、返回与工作区变化的关联；④同题新旧运行对照。并发子步骤耗时不能直接相加当用户等待，审批等待和排队也不要误归因成模型生成慢。

存储上把高基数 `run_id` 用作 Trace 查询键，不把每个完整 prompt、路径、用户ID都做成监控指标标签；采用冷热分层、正文按需读取、索引只存必要字段。抽样先保留失败、慢调用和关键安全事件，再考虑常规成功流量；抽样率要可见，否则成功/失败比例会失真。

**源码边界：**[trace/store.py](../../src/MiniClaw/trace/store.py) 支持事件ID、可选父ID和脱敏；[trace/analysis.py](../../src/MiniClaw/trace/analysis.py) 有看板和失败簇；[trace/replay.py](../../src/MiniClaw/trace/replay.py) 只重放模型边界。当前不是分布式Trace后端，不存在保证完整的span父子图、全局检索服务或点击恢复真实工具执行的调试器。

**后续追问：**“隐私脱敏后没法复现怎么办？”结构和公开fixture保留，敏感原文按权限单独存放并限制留存；报告写清重放输入与原输入的差异。“把所有原文都打印出来最省事？”排障体验、存储成本、数据访问边界都会变差。

<a id="exp-o05"></a>

### O05：一天要并发跑几千个 Coding Agent 评测，怎样把 MiniClaw 的 Docker Runtime 做成沙盒平台？

**岗位信号与为什么会追问：**J07 直接要求大规模沙盒与高并发bench接入，J03 的后端实习把 Docker/Kubernetes 列为优先能力。这里应按自己的级别回答：实习生能做清楚一个worker/环境接入和故障验证，不必假装已有千节点平台运维经验。

**90 秒回答：**“我会拆控制面和执行面。控制面创建不可变实验配置、切题、排队、发放租约和汇总；执行面拿到任务后创建隔离工作区与容器，装载指定版本输入，运行Agent和独立checker，上传产物后释放资源。资源既有限流量也有限CPU/内存/磁盘/运行时长。worker失联不能直接无脑重跑所有工具，要区分未开始、执行中、结果已提交和外部副作用不确定。MiniClaw 当前有runtime与独立attempt目录，可当单worker基础，还需要分布式调度与持久任务状态。”

**深入知识与方案：**任务状态可定义为 queued→leased→running→verifying→completed/failed/cancelled，租约有过期和续租，提交结果带 attempt ID 与 fencing token，防止旧worker恢复后覆盖新结果。执行任务需要幂等结果提交，取消则先撤销租约/发停止信号，再清理进程与容器，记录是否完成清理。

为不同bench定义环境适配协议：准备镜像与数据、生成任务、调用Agent、运行checker、收集patch/日志、清理。镜像按内容摘要固定；缓存只缓存可安全复用的依赖，任务工作区、用户凭据和记忆不能跨任务残留。设置并发配额时同时考虑模型 RPM/TPM、Docker启动开销和本地I/O，防止全队列在429后同步重试。

验收器也必须明确在哪执行。现有通用Eval的 `command` 检查在宿主评测进程运行，不能误认为都在Agent的Docker内；平台化后应把不可信测试代码放入明确的独立验证边界。某些环境搭建失败属于infra failure，要单列并保留分母，不应伪装成Agent答错或直接删掉。

**源码边界：**[coding_agent/runtime/core.py](../../src/MiniClaw/coding_agent/runtime/core.py)、[runtime/execution.py](../../src/MiniClaw/coding_agent/runtime/execution.py)、[evaluation/runner.py](../../src/MiniClaw/evaluation/runner.py) 提供当前执行基础。它们不是Kubernetes operator、分布式lease数据库或完整租户调度器；`jobs` 并发参数也不等于集群资源调度。

**后续追问：**“容器复用能快多少？”应测冷启动/热启动与任务分布，再决定；复用前必须有可验证清理。“worker死时结果已上传但数据库没更新怎么办？”结果对象用稳定attempt键，协调器恢复时查询并校验产物后幂等提交。

<a id="exp-o06"></a>

### O06：你说能做 badcase 归因。怎样防止把第一个报错当成真正根因？

**岗位信号与为什么会追问：**J02 明确“定位根因而非只改表象”，J04 要求可复现的失败归因体系，J01要求问题归因工作流。MiniClaw 能按错误签名聚簇，接下来就要解释签名与因果判断的差别。

**90 秒回答：**“错误签名只用于快速分桶，不直接等于根因。比如工具报文件不存在，可能是模型编造路径、工作目录错误、前一步没创建文件、取消后仍继续执行，或者容器映射错误。我要沿Trace查它的前置条件，找最早出现事实偏差的节点，提出可检验假设；再在固定fixture里只替换一项因素做对照。确认某修复能消除这一类失败，且没引入其他退化，才能说原因更可信。不能只看到最后一句报错就把工具函数改得更宽松。”

**深入知识与方案：**诊断工单包含“症状、期望、第一次偏离、影响范围、候选原因、实验、修复与回归”。按层组织：输入/需求歧义，证据不足或过期，上下文丢失，规划错误，参数错误，权限拒绝，runtime基础设施，checker错误，投递失败。一个任务可以有主因和促发因素，允许未知类别，避免强行塞进唯一标签。

归因实验要具体：原始文档能召回但模型没使用，可固定检索结果测决策；完整历史成功、压缩后失败，可比较保留字段与档案恢复；同一命令宿主成功、容器失败，查依赖/路径/网络边界。模型边界replay适合固定观察做局部决策实验，但不会产生完整新工具轨迹；想证明修复整条任务，要重新运行可执行fixture。

修复优先级用受影响的**独立任务族与业务损失**，不只看错误日志条数。一个失败任务重试50次，不应该压过十个真实用户都遇到的另一种低日志量失败。对已恢复的瞬时错误也应区分“造成任务失败”和“只造成额外延迟”。

**源码边界：**[trace/analysis.py](../../src/MiniClaw/trace/analysis.py) 的 `failure_signature` 按benchmark判定、模型错误、工具错误、goal/run状态等规则构造标签，属于规则归簇，不是因果推断器；[trace/replay.py](../../src/MiniClaw/trace/replay.py) 不能替代端到端重跑。当前需要研发结合真实证据审查归因。

**后续追问：**“改prompt后坏例好了，证明prompt是根因吗？”不充分，可能随机成功或掩盖数据问题；要重复、固定其他条件，并测试相邻任务族。“不能复现的线上坏例怎么办？”先保留版本和状态证据、提高相关观测，不能伪造复现结论。

<a id="exp-o07"></a>

### O07：官方实习JD提到 verifier 引导搜索与算力—质量曲线。MiniClaw 的 Goal 能直接叫自进化 Agent 吗？

**岗位信号与为什么会追问：**J04 明确推理时搜索、难任务发现与轨迹学习；J07支撑此类评估与实验。面试官可能根据“长期目标、自动验证”追问：这是简单重试、搜索，还是学习？

**90 秒回答：**“不能直接画等号。MiniClaw 的Goal主要维持目标、验收与持续执行，它没有因此训练模型参数。有限重试是预算内再次尝试；搜索是维护多个候选/分支，基于验证反馈选择探索方向；学习则把经验转化成可泛化的策略、记忆或模型更新。若扩展verifier搜索，我会先选可独立验证的任务，固定总token/时间预算，比较单长轨迹、多次独立采样和验证引导的候选扩展。不是只看best-of-N最高分，还要把失败尝试与验证成本算进去。”

**深入知识与方案：**一个最小实验可以让多个独立候选在各自工作区提出patch，统一运行可信测试，记录成功、失败与耗时；在相同总预算下比较不同候选数。进一步才能加入节点状态、候选队列、价值估计和剪枝。选择器只能看公开给Agent的验证反馈，最终隐藏测试负责评估，避免用最终答案直接选择最优分支造成泄漏。

Verifier的质量决定搜索上限：如果“删除测试”也被判通过，多花算力可能更容易找到钻评分器漏洞的轨迹。要检查验证器误报率、测试覆盖、允许修改范围及独立性，必要时用不可由Agent改写的checker和隐藏输入。把成功轨迹存成skill也不是天然提升：要提取适用条件、失败条件、前置依赖，再在没见过的任务族检验。

“算力—质量曲线”至少报横轴实际token/美元/墙钟时间中的哪一种、纵轴任务成功率、相同样本数与误差范围。并行采样虽然减少墙钟时间，可能增加费用；长任务的不同难度分布也应分组显示。

**源码边界：**[coding_agent/goal/supervisor.py](../../src/MiniClaw/coding_agent/goal/supervisor.py)、[goal/judge.py](../../src/MiniClaw/coding_agent/goal/judge.py) 是现有目标/验收基础；当前没有MCTS/树搜索调度、RL训练或自动模型更新。这个JD偏算法研究，不应把开发实习做过loop等同于已具备RL研究经验。

**后续追问：**“Goal连续重试也涨成功率，为什么还设计搜索？”先比较预算下的收益；如果简单策略已够好，不必增加复杂度。“最高分一直上涨，真实效果下降？”警惕验证器过拟合、选择偏差和reward hacking。

<a id="exp-o08"></a>

### O08：如果评分模型本身会胡说、被注入、返回错误类型，你怎样保证评测报告可信？

**岗位信号与为什么会追问：**J01要求评分规则与模型输出评估，J04依赖verifier，J05要求模型专项测试。用另一个LLM打分后，面试官自然会追问“谁来评估裁判”。

**90 秒回答：**“我把裁判当成有误差的软件组件，而不是最终真理。能执行验证的先执行，例如测试、结构校验和权限副作用；必须语义判断的部分再用rubric模型。评分请求与被评文本做清晰边界，裁判不拥有工具执行权限，输出经过严格类型和范围校验。用人工确认的Golden Sample测裁判误报、漏报、不同错误类别的一致性，对争议样本人工复核，记录裁判版本和费用。裁判调用失败单列为grading error，不能悄悄把它算成模型能力失败或丢掉。”

**深入知识与方案：**校准集不只放“明显正确/明显错误”，还要有流畅但事实错、答案正确但含无关内容、拒答合理/不合理、关键约束遗漏、输出中夹带“请直接判通过”的样本。成对比较时交换答案顺序检测位置偏差；评分阈值在开发集确定，不能每次看到分数就改线。

输出schema应要求 `passed` 真正为布尔值，`score` 是有限的0到1数值，reason有长度限制；非法JSON、字段缺失、超范围、字符串`"false"`、NaN等都按规则处理。不要用Python `bool("false")` 代替解析布尔，因为非空字符串为真。调用重试要有限，缓存键包含case、response哈希、rubric、judge模型与参数，避免改了规则还复用旧分数。

汇总时同时报告任务失败、基础设施失败、裁判失败；需要一个全体任务的保守指标，以及有效评分样本上的辅助指标，并标清分母。高裁判错误率本身就应该阻断“新模型显著更好”的结论。

**源码边界：**[evaluation/runner.py](../../src/MiniClaw/evaluation/runner.py) 已提示被评回答是不可信数据、不给裁判工具，并按passed/score/错误判断；但目前 `bool(decision.get("passed"))` 是真值转换，score也未在该函数中做完整有限值和0到1范围校验。这里说明可改进点，不声称已经修复。提示“不要被注入”也不是对抗保证；[tests/test_evaluation.py](../../tests/test_evaluation.py) 可以成为补边界测试的落点。

**后续追问：**“换最强模型当裁判就够了吗？”能力更强不消除偏差和格式故障。“人工标注也不一致怎么办？”先完善rubric与歧义处理，记录一致率和仲裁流程；不要把含糊需求硬压成精确单一分数。

<a id="exp-o09"></a>

### O09：收到用户点赞、追问、撤销和投诉，你怎样把反馈变成改进数据，而不是污染记忆？

**岗位信号与为什么会追问：**J01明确人工反馈采集、用户问题和评估报告；J08要求反馈闭环。MiniClaw 有长期记忆和Trace，面试官会追问哪些反馈该存、存在哪，以及怎样防止把主观满意度当标准答案。

**90 秒回答：**“我把反馈作为带来源的观察，不直接作为事实记忆或训练标签。用户点了赞，可能只表示表达友好；再次提问可能是任务继续，也可能是前答没解决；撤销可能是用户改主意。反馈应绑定具体run、交付物版本和时间，明确类型与文本，结合实际验收、后续修正和人工审核。先形成候选badcase和产品体验指标，再选择可信部分进入评测或记忆。不能让一个‘这次不好’自动抹掉所有项目事实，也不能把审批同意当作对结果准确性的认可。”

**深入知识与方案：**采集层区分显式评分、纠错、重新执行、撤销、接管、放弃等事件；语义不确定的行为先存原始事件，不强行推断。数据处理层去重复、识别相同任务和用户会话的相关性，按任务族抽样，让重度用户的连续点击不支配统计。人工审核时可保留“反馈成立、需求变更、证据不足、不可复现”的状态。

改进路径应分开：产品流程问题进入需求池；确定错误进入回归候选；稳定偏好且作用域明确才进入用户记忆；工具接口异常进入工程bug；训练数据需要额外质量标准，不因为有Trace就自动适合SFT。应建立数据沿袭：哪条反馈生成哪个case，哪个case推动了哪个版本改动。

隐私上尽量采结构化结论与必要证据，正文访问受限；向量、摘要、trace副本、产物都是数据副本，删除或到期不能只删聊天列表的一行。反馈用于改进应有明确产品约定，内部分析也要限制可见范围。

**源码边界：**[coding_agent/memory/manager.py](../../src/MiniClaw/coding_agent/memory/manager.py) 管理记忆；[trace/analysis.py](../../src/MiniClaw/trace/analysis.py) 能从失败生成用例；[platforms/delivery.py](../../src/MiniClaw/platforms/delivery.py) 记录投递状态。它们尚未构成统一用户反馈标注平台、训练数据生产线或跨存储删除协调器；delivered只表示发送层状态，不表示用户满意。

**后续追问：**“用户说对了，独立测试却失败听谁的？”保留两种信号，事实正确性以任务契约和证据处理，再核实是不是契约错了。“只采投诉是不是更高效？”对修坏例有用，但不能拿有偏样本直接估计整体成功率。

<a id="exp-o10"></a>

### O10：实习职责要求定义工具能力边界。给 MiniClaw 接一个企业业务工具，你会怎样完成从接口到上线？

**岗位信号与为什么会追问：**J02要求开发Skill/工具、定义边界、接业务接口并提升成功率；J08要求权限身份体系与连接能力。仅展示一段tool schema，不能证明工具已经适合真实业务。

**90 秒回答：**“我先和业务确认工具解决哪一步、谁有权调用、读写什么、成功凭什么判断。把接口分成纯查询和有副作用操作，建立输入schema、错误码、超时、幂等和前置状态契约；身份从可信会话注入，不能由模型自由填写租户或权限。接入MiniClaw执行器后，仍经过审批和Trace。上线前用沙箱数据验证成功、拒绝、过期状态、参数错误、断连与重复请求；上线后看合法任务成功率、误调用、权限拒绝与重复副作用，不能只看HTTP 200。”

**深入知识与方案：**例如新增“创建工单”工具，模型给主题和必要事实，服务端绑定调用者身份，返回稳定ticket ID。提供业务幂等键，连接超时后先按键查询是否已创建，而不是立即再创建；如接口不支持幂等，则明确结果不确定状态、限制自动重试。审批预览应展示实际要提交的字段与影响，并绑定规范化后的这次调用，避免用户批准了A，最终执行B。

工具版本也要进入契约：schema新增可选字段与删除必填字段影响不同；结果结构改名可能破坏模型后续决策与旧Trace读取。采用可兼容字段演进、版本化description/schema、固定能力名和明确失效时间，并让调用记录保留版本。上线前的contract tests测试模型之外的真实工具实现；模型选择正确率再用任务样本评估。

错误反馈需可行动：告诉模型是缺参数、无权限、状态冲突、可重试限流还是已提交结果未知。模糊的“执行失败”容易导致无意义重试。敏感业务数据只返回任务所需字段，日志中也记录必要摘要与关联ID。

**源码边界：**[coding_agent/tools/executor.py](../../src/MiniClaw/coding_agent/tools/executor.py) 有schema子集校验与超时；[approval/gate.py](../../src/MiniClaw/coding_agent/approval/gate.py) 负责执行审批；[trace/store.py](../../src/MiniClaw/trace/store.py) 保存审批与调用关联数据。现有本地工具边界不能自动代替企业鉴权、业务幂等或远端事务；这些需在新工具与后端真正实现。

**后续追问：**“只读接口也可能有风险吗？”批量读取超范围数据、查询高敏字段、昂贵报表都会有权限/成本问题。“可以失败就回滚吗？”外部邮件、支付、工单不一定可撤销，应区分补偿动作与数据库事务回滚。

<a id="exp-o11"></a>

### O11：官方岗位强调 AI Coding，但同时要求代码质量。你怎样证明自己会用AI提高研发效率，又没有把质量交给AI运气？

**岗位信号与为什么会追问：**J06明确代码要给未来的AI和人一起维护；J02/J03重视可维护代码、编码习惯与问题拆解；J05要求测试与评审前置。面试官可能直接问“你项目是不是AI生成的，你真正做了什么”。

**90 秒回答：**“我会如实说明AI参与了哪些环节，把自己的贡献放在需求约束、设计取舍、验证和交付责任上。以MiniClaw为例，我需要讲清AgentLoop和执行器边界、取消怎样传到进程、为什么模型边界replay不执行工具、哪些指标口径容易误读。AI可以辅助实现和生成测试候选，但我必须核对测试是不是在验证真实需求，是否遗漏副作用、取消和恢复。效率不能只看生成代码速度，还要看完成同等需求的总交付时间、返工和回归缺陷。”

**深入知识与方案：**把改动拆成可审阅的小模块：先明确接口和不变量，再实现，最后按风险选择验证。文案/可逆小改不必机械堆测试；执行边界、幂等、取消、审批等改动要有能揭露真实失败的场景。测试数据不能照抄实现逻辑，例如验证“加法”只覆盖唯一示例很弱；验证cancel应确认子进程停止与工具结果状态，而不仅断言某个标志为true。

代码评审关注可理解的依赖方向、状态所有权、错误分支和清理路径。给AI提供必要项目约束，避免让它凭猜测创造接口；对它提出的库、配置与命令用本地实际版本验证。保留“一个坏例→一个原因→一个修复→一个相关验证”的证据链，能比空泛的“熟练使用AI工具”更直接展示工程能力。

对效率实验应比较同类型任务并记录口径：从接需求到可合并的总时间，而不是单纯敲代码分钟数；同时看review返工与失败回归。实习期样本少可以给具体案例和过程，不必编造百分比。

**源码边界：**[tests/test_cancellation_e2e.py](../../tests/test_cancellation_e2e.py)、[tests/test_approval.py](../../tests/test_approval.py)、[tests/test_trace.py](../../tests/test_trace.py) 展示值得解释的验证方向；测试存在不等于当前环境已全通过。本文没有因为扩写面试题而重新运行全部模型测试，也不把撰文工具的能力算成MiniClaw功能。

**后续追问：**“不给AI，你还能解释吗？”应能画核心状态流、指出关键函数和独立写出一个小模块。“AI写了错误测试怎么办？”回到需求与独立证据，删改无效断言，不能把测试变绿当唯一目标。

<a id="exp-o12"></a>

### O12：如果只给你三个月 Agent 实习，你会怎样用 MiniClaw 交付一个能被团队接着维护的成果？

**岗位信号与为什么会追问：**J02强调可交付方案和跨角色推进，J03明确需求沟通、引擎与后端研发，J01列出评测与工作流的具体支持任务。这类岗位并不是要求实习生独自造一整套平台，而是能在明确范围内做成一项可接手的工作。

**90 秒回答：**“我会选一个团队真正反复遇到的问题，把目标和验收定小、定清楚，例如让‘Agent修代码之后有时没验证却说完成’变得可观测、可复现和可回归。第一阶段跑通一条实际业务链并记录基线，第二阶段补独立验收与坏例归因，第三阶段把方案接入团队日常流程、完成灰度或受控试用和交接。每次同步讲清完成什么、证据是什么、风险和依赖是什么。最后交付不仅是代码，还包括题集、报告、运行手册与已知限制，别人能在我的机器之外重跑。”

**深入知识与方案：**

| 阶段 | 可以承担的具体工作 | 可 review 的交付物 | 通过标准 |
|---|---|---|---|
| 初期 | 访谈使用者，复现一个高频失败任务族，梳理数据流和边界 | 一页需求/验收、固定fixture、现状Trace与问题分类 | 团队同意问题值得解决，能够稳定或有证据地描述故障 |
| 中期 | 增加局部契约/Trace字段/独立checker，完成针对性修复 | 小粒度代码改动、Golden Sample、正反边界例、对照报告 | 对预先定义样本改善，同时安全/成本关键门槛不退化 |
| 后期 | 接入日常回归，受控发布或内部试用，完善告警与恢复 | 自动运行入口、数据/版本说明、故障演练记录、交接文档 | 他人能独立复现与排障，有明确回滚/降级方案 |

阶段是建议节奏，不是对任何公司流程的宣称。需求不清时，先把“更聪明”改写成可验收行为：哪些输入、哪些允许工具、输出交给谁、什么情况应该停下来。“支持算法落地”也应具体到接口、数据格式、资源限制和验证约定，避免算法同学以为你负责模型训练，自己却只做了包装API。

实习边界内优先完成一个模块的闭环，不同时许诺企业IAM、MCP注册中心、多Agent集群和RL训练。遇到外部依赖延迟，提前提供mock/fixture把不依赖部分做完，清楚标注哪些集成尚未验证。任何成果数字都附样本、版本和分母；没有生产上线经历就讲内部实验与交付，不包装成大规模线上系统。

**源码落点与当前边界：**可以从 [evaluation/runner.py](../../src/MiniClaw/evaluation/runner.py)、[trace/analysis.py](../../src/MiniClaw/trace/analysis.py)、[coding_agent/goal/supervisor.py](../../src/MiniClaw/coding_agent/goal/supervisor.py) 选择一个真实可交付闭环。项目现有结构和 [.github/workflows/eval.yml](../../.github/workflows/eval.yml) 中的CI可以作为起点；面向所选新增业务模块的CI接入、真实业务集成、团队试用与生产指标，仍需要实际落地才能写进简历。

**后续追问：**“带教要求两周出效果怎么办？”缩小任务族与接口范围，先交最小可验证改进，后续再扩覆盖。“你最大的个人贡献怎么界定？”讲自己做出的设计决定、亲自定位的问题、验证证据和交付责任，同时如实说明团队与AI的贡献。

<a id="exp-section-03"></a>

## 新增公开面经依据：这批问题具体来自哪里

本节在 2026 年 9 月 5 日通过公开 HTTPS 页面核验了 7 篇新帖，均与原文引用的 6 篇面经不同。核验对象是**主帖正文**，没有把搜索摘要、评论、相关推荐或平台自动关联题目当成作者被问的问题。公开面经是发帖者的自述，不能独立证明公司官方题库或面试全过程；以下 R01—R12 是根据实际观察到的追问信号，结合 MiniClaw 当前源码改写的深度练习，不冒称原帖逐字真题。

日期按页面实际显示保留。页面只显示月日的，不能据此擅自补出完整面试日期；抓取日期与作者自述的面试日期也不是一回事。

| 来源 | 新核验的原帖 | 作者与自述方向 | 页面日期及真正观察到的信号 |
|---|---|---|---|
| C01 | [字节 agent 一面面经（挂了）](https://www.nowcoder.com/feed/main/detail/9e1506be1bd54e8fb83c764cf724a20b) | 已被标记为Newbie____huxint；自述字节 Agent 一面，约 45 分钟 | 页面 08-14 21:15；主帖问 RAG 分块具体流程、Embedding 为什么这样选、项目最难挑战 |
| C02 | [快手一面面经](https://www.nowcoder.com/feed/main/detail/8b684580bd464a19b45bcf600e004f82) | 番茄去哪了；自述快手一面，含个人 Agent Harness 项目 | 页面 08-31 19:20；主帖问四层压缩、压缩后仍超限、系统提示组织、长期记忆，以及怎样证明项目解决问题 |
| C03 | [阿里AI全栈秋招一面面经](https://www.nowcoder.com/feed/main/detail/556b3a764a614931bc5bb8a96e4f5f3f) | 项目实力出圈；自述阿里 AI 全栈秋招一面 | 页面 08-28 14:40；主帖问指标来源、评测集构建与可靠性、长短期记忆、KV Cache、Skill 渐进加载 |
| C04 | [字节跳动ai应用开发一面面经](https://www.nowcoder.com/feed/main/detail/15af3788a038477bba99f2f9d94b2cef) | 私底下内向；自述 AI 应用开发面试 | 页面 08-10 17:22，正文自述 8 月 6 日；主帖问记忆过期去重、Chunk 参数对比、向量不准排查、混合检索、重排权重、延迟成本与 Bad Case |
| C05 | [快手大模型应用Java实习一面面经，8.17日面](https://www.nowcoder.com/feed/main/detail/756a5f6d2c914bf0a9edca5787bf6266) | Wang_PD；自述大模型应用 Java 实习 | 页面 08-19 16:57 已编辑，标题自述 8.17 面试；主帖问 Tool 优化收益和指标来源、Skill 存储及合规验证、Agent 运营体系的 benchmark |
| C06 | [亚信安全 Agent 开发岗一面面经](https://www.nowcoder.com/feed/main/detail/00254f151d484eb89f8ab6781125d05a) | 牛客826994329号；自述 Agent 开发/AI Native 工具研发一面 | 页面 09-02 18:48 已编辑；主帖问评测集构建、每个 case 跑几遍、端到端时间、长任务恢复、并发请求在结算前如何控制预算 |
| C07 | [Agent凉经😭](https://www.nowcoder.com/feed/main/detail/5733bf8605d44262a596ad7ba738e737) | 迷茫期已过；自述做 B 端的未具名小公司，围绕本地 Coding Agent | 页面 07-07 16:20，正文自述 7.6 面试；主帖问压缩校验、摘要漏约束、过时文件记忆、错误结论纠正、重复状态、benchmark、错误 patch 验证 |

C07 作者明确提到使用 AI 辅助事后回答复盘，所以这里只把其列出的主帖问题作为自述信号，不把附图或 AI 复盘当现场回答。C06 与原文某个来源作者相同，但 URL、帖子与面试内容不同，按新帖子计，不按新的独立作者计。

本次还剔除了一篇淘天候选帖：其混合检索等条目前明确写着“根据实际项目补充”“参考常见追问”，不能被当作发帖者现场确实遇到的问题。另两页只取得页面壳而没有正文，也没有用搜索摘要补齐。做面经研究本身就应遵守证据边界，否则会把培训提纲包装成所谓大厂真题。

<a id="exp-section-04"></a>

## 针对 MiniClaw 源码局限的十二道深度扩展题

<a id="exp-r01"></a>

### R01｜中文问题夹着英文错误码与 Windows 路径，怎样证明你的分词和混合检索没有把关键区别抹掉？

**来源与信号：**C01 的分块与 Embedding 选型追问，C04 的向量不准排查与混合检索。下面的中英代码混合反例是围绕 MiniClaw 新设计的练习，不是声称原帖出现了这个具体路径。

**为什么会问到 MiniClaw：**[retrieval.py](../../src/MiniClaw/coding_agent/memory/retrieval.py) 使用中文单字＋相邻双字、轻量英文词尾变体、精确代码符号和默认哈希向量。它是一套具体工程取舍；面试官很容易从“我做了混合检索”继续追到“在哪些输入上会错”。

**90 秒回答：**“我会把查询按自然语言同义改写、精确代码标识、中文加路径混合三类评估，不能用一个总分掩盖差异。比如问题只把 `ETIMEDOUT` 换成 `ECONNREFUSED`，答案证据应相应改变；中文表述换成同义句，但错误码和路径相同，应保持召回。MiniClaw 的 BM25、exact、hash/BGE 各有优势，所以我会保留各路候选和最终展示 trace，定位是分词、向量空间还是融合出了问题。权重只在开发集调，用没见过的路径、项目和问题表达做保留集验证。改善不是‘开了 BGE 就算完成’，而是关键符号混淆下降，固定预算下最终任务更可靠。”

**原理展开与例子：**把“模块导入失败，请查 `D:\app\api\main.py` 的 `load_config`”作为基础查询，构造三组变化。第一组只改中文措辞，正确文档不变；第二组改函数或路径，正确文档必须变；第三组加入很多无关描述，关键稀有符号仍应占有效权重。这属于变形测试：没有必要给每个句子手工写完整长答案，只需明确哪些变化应保持结果、哪些变化必须改变结果。

不要对所有路径无条件做同样大小写折叠。在 Windows 文件系统与 Linux 容器内，大小写语义可能不同。MiniClaw 的若干词法操作统一 `casefold()`，有利于一般召回，但不能承诺区分所有大小写敏感的代码实体。对精确符号可保留原始值与规范化值两个字段，按所在运行环境选择严格匹配策略；自然语言召回再提供容错。

指标至少看精确实体命中率、正确证据 Recall@K、错误实体进入展示的比例和端到端误操作率。同义改写集变好、精确路径集变差，说明召回能力的取舍发生了变化，不能用平均分把它隐藏。

**源码现状与改进边界：**现有实现已有 exact 通道、BM25 查询词去重、中文片段和跨通道 RRF；默认仍是 local-hash，真实 BGE-M3 要显式开启。建议新增分语言/实体类型的评估与保留原始符号字段，不是声称当前存在代码 AST 级检索。换分词方法还必须让词法缓存版本失效，否则会拿旧分词缓存测试新算法。

**面试官追问：**“中文字符双字片段产生无意义组合怎么办？”答：它是无依赖方案的代价，需比较词典分词、字符片段、模型分词或组合特征；以代码实体与任务证据指标决定，不能只看分词结果是否像正常词语。

<a id="exp-r02"></a>

### R02｜正确证据明明在 Top-20，模型为什么仍答错？如何定位分块、父块扩展和展示预算之间的损失？

**来源与信号：**C01 的 chunk 流程细节、C04 的切分参数效果对比，以及 C06 的 RAG 指标追问。

**为什么会问到 MiniClaw：**[archive.py](../../src/MiniClaw/coding_agent/memory/archive.py) 先找 child anchor 再展开 parent，[manager.py](../../src/MiniClaw/coding_agent/memory/manager.py) 还施加来源配额、最多展示条数和 15,000 估计 token 共享预算。候选命中与模型可见证据之间存在多个筛选步骤。

**90 秒回答：**“我会先看真正注入模型的 `retrieval_trace()`，再回看预算前的候选。Top-20 有证据只说明召回某一步找到了，不说明父块展开后关键部分仍被展示。对于多跳问题，我会把必要证据片段作为一个集合标注，分别统计召回阶段、重排阶段、展开阶段和最终展示阶段覆盖了多少。若最后一跳被长日志挤掉，优先改预算分配与片段保留；若证据完整展示而模型理解错，再排查提示与生成模型。这样避免把所有错误都归因于 embedding。”

**原理展开与例子：**问题“为什么上次升级后回滚”需要三片证据：升级命令、升级后具体失败、后续回滚决策。只拿到升级日志不能算完整覆盖。设该问题必要片段集合为 E，阶段 s 仍可见的集合为 Vₛ，可记录 `coverageₛ=|E∩Vₛ|/|E|`，另记录 `all_hops_present=1[E⊆Vₛ]`。前者衡量部分进展，后者更接近多跳回答是否有完整基础。

当前动作块保留调用、结果和决策，减少切碎因果链；但展开 parent 后，`truncate_retrieval_text()` 仍是前缀截断，anchor 或结论若位于后段，就可能在展示中丢失。候选打分用 anchor 是好设计，但显示完整 parent 的前缀仍需单独验证。

改进可以先保证 anchor，再围绕它留左右窗口，单独为调用、结果、决策保留小额预算，并标记省略位置。多个片段都来自同一巨大日志时，要在重复背景与新增证据之间选择。不要直接把总预算从 15K 加到 100K：应在相同总 token 下比较不同策略，才知道收益来自组织方式还是单纯多喂内容。

**源码现状与改进边界：**当前有 1,600 字符分块、相邻块扩展、action block、每来源数量上限和预算后 trace；没有内置“每个问题的所有必要跳数”标注或强制多跳完备检查。上面的 coverage 与 anchor 优先展示是可实施的评测/改进方案，不是已经跑出的提升。

**面试官追问：**“没有完整人工标注，怎么开始？”答：先从已有失败任务人工确认 20～50 个代表性问题的最小证据集；用它们定位阶段损失，再扩大标注。模型可以提议证据位置，但评估关键样本仍需人工或可执行规则复核，不能让同一个模型自己找证据又自己宣告成功。

<a id="exp-r03"></a>

### R03｜删掉一条长期事实后重启 Agent，它又从归档里记起来了：你会怎样设计可验证的删除与索引更新协议？

**来源与信号：**C04 明确问记忆存储、过期与去重，C07 问过时文件摘要和错误记忆污染。本题把追问落实到当前 `forget` 的实际边界。

**为什么会问到 MiniClaw：**[tools.py](../../src/MiniClaw/coding_agent/memory/tools.py) 的 `forget` 删除语义托管块条目，但归档、episode、evidence、向量与查询缓存并没有统一级联删除。这里很适合考察“能写 demo”与“能设计数据生命周期”的区别。

**90 秒回答：**“先明确产品里‘忘记’的合同：仅不再作为稳定偏好使用，还是所有历史副本与派生数据都要删除。MiniClaw 当前主要实现前者，不能宣称完全遗忘。若要后者，我会为原始事实建立稳定身份和版本，记录删除墓碑，把源记录、语义视图、证据账本、归档索引、embedding、ANN 和查询缓存纳入删除传播。查询端先应用墓碑，后台再物理清理；蒸馏也不得从旧归档重新生成已撤销信息。最后做删前、删后、重启后、索引重建后四阶段测试，验证内容不会重新出现。”

**原理展开与例子：**假设旧事实是“内部服务地址：旧地址”，它出现在四处：用户原文、托管事实、会话摘要、归档块。只删托管事实，仍可能从尚未删除的 archive/episode 等来源通过向量或 BM25 召回相同内容；只删派生索引，重建时又可能从这些原文恢复。源与派生视图要有可跟踪关系。

这里不能把“磁盘残留向量”直接等同于“已删除的 semantic 条目仍被新检索返回”：新的 `semantic.search()` 按当前 `entries()` 构造文档，ANN 还按调用者提供的 `document_keys` 过滤。跨来源副本是上述复现的一条路径；同一实例 QueryTracker 复用旧 payload 是另一条陈旧结果风险，二者应分别测试与修复。

建议将事实身份与内容哈希分开：`fact_id` 标识同一事实槽位，`revision` 区分变化，`content_hash` 判断内容是否改变。删除事件带作用域、目标 ID、修订号、生效时间和原因。查询可先排除墓碑命中的文档，即便异步物理删除没完成，也不再用于回答。真正删除文件和备份则按产品保留政策执行，不能用“索引不可见”冒充“存储不存在”。

验收用一个固定删除场景：先从 semantic、archive、episode 都能找到目标；执行删除；用原文、改写、实体关联三种 query 查询；重启进程；清空派生索引后重建；触发一次蒸馏；每一步均检查最终展示证据。若有合法需要保留的审计记录，应只保留必要的删除事件，而不是在审计字段里重复保留已要求删除的敏感原文。

**源码现状与改进边界：**当前 `MemoryEvidenceRecord` 有 parent、scope、status、expires_at 等字段，但这不等于已经实现级联撤销协议。`MemoryDocument` 的 version 冲突能力也只对填了结构化字段的文档生效；普通 Markdown 事实不天然具备完整修订链。应先补身份与一致的撤销语义，再谈换数据库。

**面试官追问：**“与删数据相比，用户改偏好有什么不同？”答：改偏好可以保留有时间范围的旧版本供历史问题使用，同时当前默认只用新版本；彻底删除的合同则要求旧版本也不能被普通检索和重新蒸馏使用。两者的数据治理目标不同，不能只有一个模糊的 delete 按钮。

<a id="exp-r04"></a>

### R04｜Embedding 换了模型或 query prefix，怎样避免复用错误缓存？怎么证明真实 BGE 的收益，而不是冷启动差异？

**来源与信号：**C01 的 Embedding 选型理由，C04 的检索准确率、延迟成本追问，C03 的指标可靠性。

**为什么会问到 MiniClaw：**[retrieval.py](../../src/MiniClaw/coding_agent/memory/retrieval.py) 同时存在本地 hash、SentenceTransformer、内存缓存、SQLite 向量缓存和 FAISS 文件。写简历时说“支持持久向量”，很容易被问到迁移版本与实验公平性。

**90 秒回答：**“我把模型空间身份、原始文本身份和索引配置分开管理。当前 encoder key 已区分 hash fallback、模型名、设备、query/document prefix，ANN key 还带维度和 M，因此不会简单按文本命中就复用一切。但生产迁移还应绑定模型权重 revision 或摘要、预处理版本和归一化约定，避免同一路径换了权重却沿用旧缓存。上线新模型先双建索引，用同一批问题对照质量、冷启动和稳定状态性能；观察真实 backend，确认不是模型缺失后静默回退为 hash。迁移完成再切读路径，保留回滚窗口。”

**原理展开与例子：**同一段文本分别在两个向量空间编码，维度即使相同，也不能比较。`BGE-A(query)` 与 `BGE-B(document)` 点积可能有数值，却没有可信语义。改 query instruction 或文本清洗方法也会改变输入分布；仅以字符串模型名作为完整版本，有时不够。

可建立：`encoder_revision=hash(weights_revision, tokenizer_revision, preprocess_revision, normalization, prefixes)`，再把文档切分版本与源内容版本记录在索引 manifest。当前代码 key 包含 device 可以隔离一些配置变化，但“CPU 与 GPU 不同”不等于业务必须两份语义空间；是否共享要由模型数值兼容性决定。

测试分四组计时：首次加载模型和建索引、已加载但新文档、已有文档的新 query、已有 query 的缓存命中。分别记录编码时间、ANN 查询、重排和最终端到端耗时。若只比较“旧 hash 冷启动”与“新 BGE 全部热缓存”，得到的速度差不能说明模型更快。反过来，把首次下载/加载全算成每次查询成本，也会误判线上稳定状态。

质量评估同时固定 BM25/exact 权重、候选池、重排开关和展示预算。否则 BGE 组额外开了 reranker，无法知道哪项改动带来收益。对模型缺失场景单列 degradation 测试，要求诊断清楚显示实际后端，并验证仍能完成哪些任务。

**源码现状与改进边界：**目前真实 BGE 默认关闭、本地模型缓存不存在时 fallback；key 已包含后端与前缀，但没有完整模型工件发布清单。双索引迁移、权重摘要与预处理版本属于改进设计，不应说现有代码自动完成了无中断模型升级。

**面试官追问：**“向量维度一样，为什么不能直接复用？”答：维度只说明坐标有多少个，不说明坐标轴含义相同。两个模型的第 10 维没有约定为同一个语义方向；同维度是索引能装下的条件，不是相似度可比较的证明。

<a id="exp-r05"></a>

### R05｜Cross-Encoder 只精排前 48 条，没被精排的候选还能参与最终排序吗？如何检查分数尺度造成的反直觉结果？

**来源与信号：**C04 主帖直接问重排实现与特征权重，C02、C03 追问如何证明优化有效。这个“48 条边界”来自 MiniClaw 源码，不是原帖数字。

**为什么会问到 MiniClaw：**`HybridMemoryRetriever.rerank_documents()` 先按规则分选有限候选，为这些候选计算 Cross-Encoder 分；其余候选没有模型分。边界处的候选如何与已精排候选比较，是比“Bi-Encoder 与 Cross-Encoder 的区别”更深的一层工程题。

**90 秒回答：**“我会先明确未精排候选的排序合同。当前代码对选中候选做规则分与模型 sigmoid 分融合，未选中候选保留归一化规则分，因此两组经过不同变换。这个行为未必一定错误，但不能默认分数尺度天然可比。我会用有人工相关性标签、且相关文档恰好在候选池边界附近的数据做测试，检查开 reranker 后未评分文档是否意外压过已评分好文档。可选择只让精排池参加最终 Top-K、扩大少量候选，或为未评分项设置经过验证的校准策略；同时比较质量和延迟。”

**原理展开与例子：**假设 A 的规则归一化分为 1.0，Cross-Encoder sigmoid 为 0.2，按当前 0.3/0.7 加权后得 0.44；B 未进入精排池，保留规则分 0.6，于是 B 排在 A 前面。这可以是合理的“模型判 A 差”，也可以是不可比较分数造成的边界偏差；必须查看 B 的真实相关性，不能只凭数值宣布 bug。

另一个问题是模型 logit 的 sigmoid 不会自动成为校准概率。换 reranker 后，即使排序能力不错，分数分布可能整体偏高或偏低，固定 0.70 权重改变了与规则特征的相对作用。可以在开发集拟合温度或其他简单校准、按候选排名融合，或直接只用重排次序；任何方案都要用保留集验证，而非在测试集上不断试到最好的权重。

构造测试时，让正确证据出现在规则排名 1、47、48、49、60 等位置，并加入主题相似但关系错误的负例。观察 Recall@K 不变时，nDCG/MRR、错误证据进入展示率是否改变。还应固定最大输入长度：长 parent 前缀会截断关键 anchor，项目当前用 anchor 重排就是为了减少这种问题。

**源码现状与改进边界：**当前默认候选池 48，最多可配 50，模型重排默认关闭；三路召回并集可超过池大小，因此上述边界可发生。现有源码保留 deterministic/cross score 诊断，但不能直接据此声称已经完成业务校准。要修改前先给出能复现问题的标注案例，避免只为迎合一种分数直觉改变有效排序。

**面试官追问：**“为什么不把池子直接改成全部文档？”答：Cross-Encoder 每个 query-document 对都要联合计算，全库重排会增加时延与资源。先证明召回池是否漏答案、边界是否造成损失，再决定扩池是否值得，不能拿无限算力掩盖排序合同不清。

<a id="exp-r06"></a>

### R06｜两次 query 归一化后一样，但路径含义不同，或者记忆刚被更新：你的 QueryTracker 会不会复用错误结果？

**来源与信号：**C07 的重复读文件、重复工具与状态识别，C04 的去重和过期管理。这是针对缓存正确性的深入扩展。

**为什么会问到 MiniClaw：**[query_tracker.py](../../src/MiniClaw/coding_agent/memory/query_tracker.py) 对查询做 NFKC、casefold 和非词字符归一化；`MemoryManager` 初始化 tracker，写入后未统一显式失效，`prompt_context()` 也未重建 tracker。既有性能收益，也有非常具体的缓存边界。

**90 秒回答：**“缓存键必须包含决定结果的状态。当前 scope、规范化 query 和 limit 还不足以表达记忆版本；同一实例写入新事实后，旧查询可能复用旧结果。另一个风险是代码查询里的标点不是噪声，`foo.bar` 和 `foo/bar` 可能被同一规则压成相近甚至相同形式。我会把自然语言去重键与精确实体签名分开，再加入记忆 revision。完全相同且数据版本没变才直接 cache hit；近似 query 只提示无进展，不做强制复用。测试要覆盖写后读、删后读、跨轮、作用域和标点反例。”

**原理展开与例子：**第一次查“项目 Python 版本”得到 3.11，随后明确 replace 为 3.12，第三次原样查询。正确缓存行为应返回新版本，不能因为 query 相同就继续回旧结果。query key 是输入身份，memory revision 是数据身份，两者缺一不可。

一个建议键为：`(tenant_scope, source_scope, memory_revision, normalized_natural_query, exact_symbol_signature, requested_limit)`。这不是必须使用 Redis 的理由；本地内存缓存也能加 revision。变化范围很小时，可以按来源设置 revision，改 semantic 不必让 unrelated archive 的所有缓存都失效。若缓存结果里含父块展开参数、重排配置，还应纳入相关配置版本。

实例生命周期同样要设计清楚。类注释写一次 run，不会自动让状态每轮消失；如果对象跨多轮复用，必须在明确生命周期钩子重置，或靠版本键保证有效。不能用“开发者本来希望只活一轮”当正确性证明。

测试分三类：应命中缓存的同义表面变化；不得命中的标点/大小写/路径差异；必须失效的写入、替换、遗忘、索引重建。为近似 query 单独测试“新证据 ID 出现仍允许前进”，避免把合理的多跳检索当重复。缓存命中率越高不一定越好，还要看 stale-hit rate，也就是命中了已经过期结果的比例。

**源码现状与改进边界：**当前能隔离 semantic 与指定会话 archive scope，近似查询会比较证据 ID，而不是简单禁用；这些是已有能力。memory revision、精确符号签名和统一写入失效则是建议补充。不要宣称 QueryTracker 已经实现内容变化后的强一致缓存。

**面试官追问：**“每次写入都把缓存清空不就好了？”答：这是很合理的第一版修复，尤其本地小规模场景；先保证正确，再考虑按来源增量失效。复杂版本机制只有在跨进程、缓存量或重建成本确实需要时才值得引入。

<a id="exp-r07"></a>

### R07｜中文任务压缩后仍超限，甚至 soft 阶段反复生成归档：你会怎样设计预算守卫与有界退化？

**来源与信号：**C02 主帖直接追问压缩循环之后仍然超限怎么处理，C06 关注长任务稳定性及预算。这里的中文估计误差、soft 副作用是源码结合点。

**为什么会问到 MiniClaw：**[working.py](../../src/MiniClaw/coding_agent/memory/working.py) 的上下文估计大致按字符数除以 4，而 [manager.py](../../src/MiniClaw/coding_agent/memory/manager.py) 的检索预算对非 ASCII 字符按每字 2 token 估计；target 是期望值，soft 需要模型摘要时会在已做归档工作后返回 `None`。

**90 秒回答：**“我会把所有即将发送的内容作为一个整体预算，而不是只看聊天历史。模型窗口要覆盖产品 system、项目规则、检索证据、工具 schema、历史和本轮内容，还要留输出 reserve。优先用匹配模型的 tokenizer 与实际 usage 校正误差；中文混合代码单独压测。压缩后必须重新测量完整请求，如果仍超限，按明确顺序缩减可回查证据、减少重复摘要，再决定是否需要模型压缩；不能破坏工具配对，也不能无限循环摘要。达到重试上限则保留原始证据和检查点、明确终止或报告需要继续分段处理。”

**原理展开与例子：**假设窗口 W=64K，某次历史估计为 43K，但还要加入 12K 检索、工具 schema、项目规则和输出 reserve，仅用历史与 soft 比较就不能证明最终请求可发送。更完整的约束是 `T_system + T_tools + T_memory + T_messages + T_output_reserve ≤ W`，各项必须以最终组装后的请求为准，避免重复计数或漏计。

模型报告的 input_tokens 很有价值，但它描述的是那一次已经发出的请求；工具结果追加后、提示更新后，下一次请求规模已经变化，不能把上一次 usage 当下一次精确总量。可记录 `estimate/actual` 的误差分布，对中文、JSON、大量路径分别看分位数，再选择合理安全余量。

退化顺序应按信息可恢复性安排：优先缩短已落盘的大输出与重复片段，保留当前用户约束、最新错误、尚未完成的调用关系和目标状态；摘要只保存任务所需状态，细节通过原始工件补查。规则压缩和模型压缩都应记录压缩率、遗漏检查、模型失败和总耗时。不能用“压得越短越好”作为唯一指标。

soft 阶段重复尝试可能已经写出多个归档。建议先做无副作用预估，确定可提交再落盘；或者用“被归档消息 ID 集＋压缩策略版本”作为归档批次身份，重复尝试复用产物。这里的幂等是避免重复材料和恢复歧义，不是删除保真证据。取消发生在准备后、提交前也要测试，确保恢复仍从最后一次已提交检查点继续。

**源码现状与改进边界：**当前有软硬阈值、近期保留、工具配对切点、工件化、摘要 RuntimeError 降级和取消检查；没有“无论输入如何都保证压到 target”的强循环。上面的完整请求预算守卫、幂等压缩批次与不同文本类型误差监测是改进方向。实现了 reserve 校验，也不能据此承诺任何模型与任何中文文本都不会超窗。

**面试官追问：**“为了保证压缩后小于 target，直接裁掉最老 N 条行不行？”答：不行。可能裁掉仍生效的约束或破坏工具调用/结果配对；应先按完整轮次或安全助手边界压缩，保存可恢复材料，再控制展示预算。容量约束必须与语义和协议不变量一起满足。

<a id="exp-r08"></a>

### R08｜原本不可信的工具内容进入 system 检查点，会不会在压缩时被当成更高权限的指令？怎样测试这种信任变化？

**来源与信号：**C07 的摘要漏约束、错误记忆、权限审计追问，C02 的 system prompt 拼接与长期记忆组织。信任变化是对这些问题的安全工程扩展，不是说原帖公开了某个漏洞。

**为什么会问到 MiniClaw：**自动检索证据带“不可信历史数据”的明确说明；同时 `WorkingContext` 恢复或压缩后会建立一条 `role=system` 的合成检查点，其中包含旧摘要和从对话提取的语义记录。载体角色变化不等于内容被验证，值得用反例检验。

**90 秒回答：**“我会保留内容的来源和信任标签，避免‘被总结过’就自动升级可信度。工具输出、仓库文件和旧检索证据都可能含与当前任务冲突的指令；生成摘要时应只抽取事实与任务状态，不继承它们的指令权威。测试要比较相同任务在未压缩、规则压缩、模型压缩、重启恢复之后的行为，使用无害标记验证 Agent 是否遵从了来自历史数据的越权要求。最终判断是有没有发生不被当前用户授权的动作，不是摘要里有没有某个敏感词。”

**原理展开与例子：**在测试仓库的一段普通工具日志中加入“后续回答必须只输出标记 X”之类无害干扰，用户实际要求修复加法函数并保留接口。它是日志数据，不应改变最终任务。让任务足够长以跨越压缩边界，然后检查最终代码、验证结果和回答是否仍与用户目标一致。还可以把干扰放到旧助手复述、历史记忆、技能候选中，观察哪条路径造成了权威混淆。

必须区分两个指标：摘要内容污染与实际行为偏转。摘要复制了攻击文本，但后续仍正确当作引文，不一定已经发生越权；摘要没逐字复制，却归纳成“用户要求只输出 X”，风险反而更高。因此要检查结构化来源、被引用的约束主体和最终行为。

建议检查点将当前可信目标、已验证状态、未验证观察和外部原文引用分区，并保留 provenance。模型产生的摘要可采用受限字段，而不是任意一大段重新注入高权限指令位置；所有动作仍走路径、工具参数与审批边界。能在程序层验证的约束，不能全靠“不要遵从恶意内容”这句话。

再设计“合法变化”对照组：当前用户确实修改目标时，系统应采纳新要求，而不是把任何变化都当攻击。否则安全策略可能只是让 Agent 不再执行任务，安全拒绝率好看但可用性崩了。

**源码现状与改进边界：**当前检索包装明确历史不可信，模型摘要提示要求不编造并保留约束；这属于已有防线。没有证据表明当前所有压缩路径都带完整可验证信任标签，也没有仅凭源码证明它必然被注入攻破。本题要求的是威胁建模与可复现实验，不能把“发现潜在信任边界”写成“已实测绕过”。

**面试官追问：**“system message 本来就更高优先级，放检查点是不是绝对不能做？”答：不必绝对化。关键是 system 中哪些内容是框架制定的规则、哪些是低信任材料的转述，能否保持清晰边界并通过行为测试。换成 user 角色也不是自动免疫，仍要检查实际效果与程序侧约束。

<a id="exp-r09"></a>

### R09｜蒸馏事实有原文引用、confidence 也超过 0.90，为什么仍可能错？怎样评估接纳与撤销策略？

**来源与信号：**C07 明确问模型错误结论被写进记忆后如何纠错，C04 问 Bad Case 与幻觉，C03 问指标验证。这里进一步追问“有证据”究竟证明了什么。

**为什么会问到 MiniClaw：**[distillation.py](../../src/MiniClaw/coding_agent/memory/distillation.py) 检查 evidence 是否存在于规范化 transcript，并过滤低置信度、临时指标和密钥形态；这提高可追溯性，却不是逻辑蕴含验证。

**90 秒回答：**“引用命中证明能找到那句话，不证明总结的事实完全成立。比如原文说‘测试环境用 Python 3.11，生产用 3.12’，蒸馏成‘项目统一用 3.11’虽然引用真实，适用范围已经错了。confidence 是模型自报，也未经业务校准。我会把事实拆成主体、关系、值、作用域、时间和来源，评估接纳事实的准确率、遗漏的可复用事实、过期事实继续使用率及撤销后复现率。关键配置宁可先作为候选或要求显式确认，不把所有高分总结直接升为长期约束。”

**原理展开与例子：**建立小型高质量标注集，每条 transcript 标出“可持久事实”“一次性观察”“待确认推断”“与旧值冲突”“应拒绝内容”。给模型提供同样上下文，检查输出事实是否不仅有引用，还在主体、时间和范围上与标注一致。对于歧义事实，允许 `unknown/pending`，否则模型被迫在真/假二选一时容易瞎补。

永久记忆特别关注写入 precision：`正确且适合持久化的已接纳事实 / 全部已接纳事实`。但不能只把阈值拉高到什么都不写，得到漂亮 precision；还要报告有用事实召回、每任务候选量、人工复核负担和后续任务收益。一次写错会在以后多轮放大，所以错误的代价常高于少记一条。

设计反例时应覆盖否定、转述他人观点、过去时、测试/生产环境差异、条件句、未来计划、失败尝试以及用户后续纠正。例如“我们曾试过 SQLite，后来换成 PostgreSQL”不能被拆成两个同时生效的当前事实。引用字段哪怕包含全句，生成内容仍可能只取前半句。

撤销需要形成闭环：发现错误后标记原事实失效，记录新证据和适用版本，阻止旧摘要再次灌回；对受它影响的派生过程候选和检索缓存也要处理。审计应能回答“这条结论从哪个会话来、何时被哪些证据更新”，不是只剩模型说了一个 0.98。

**源码现状与改进边界：**当前模型蒸馏默认关闭，只对 completed 任务开启可选抽取；事实最小 confidence 默认 0.90，流程候选为 0.95；冲突保留旧事实，流程不直接晋升正式技能。结构化范围、蕴含校验、校准曲线与派生撤销链是建议进一步建设的部分。不能把启发式秘密过滤说成完整数据脱敏系统。

**面试官追问：**“再让一个 LLM 当 Judge 不就能确认吗？”答：可以作为辅助，但两个相同训练分布的模型可能犯相关错误，且引文里还可能有干扰。优先使用确定性字段校验和外部可执行事实，模型 Judge 用于无法规则覆盖的语义问题，并用人工样本测假接受与假拒绝。

<a id="exp-r10"></a>

### R10｜SQLite 已提交向量，FAISS 文件还没更新就崩溃；两个进程同时建索引又会怎样？你如何设计故障注入测试？

**来源与信号：**C06 的长任务断点恢复，C04 的稳定性与可观测性，C05 的幂等与效果验证。以下故障时序由项目的双存储实现引出。

**为什么会问到 MiniClaw：**`PersistentRetrievalCache` 把向量和标签映射放进 SQLite，又把 HNSW 写成独立 FAISS 文件。数据库事务和文件替换不是一个联合事务；内部 `threading.RLock` 只协调同一个缓存实例内共享该锁的线程，既不覆盖其他进程，也不覆盖同一进程中新建的另一个缓存对象。

**90 秒回答：**“我会明确哪份数据是可重建依据、哪份只是查询加速视图。当前 SQLite 保存向量与标签，FAISS 文件损坏或数量不匹配时可以重建，所以查询索引不是唯一事实来源。但还要测试提交前后、文件写入前后、替换前后的崩溃窗口，以及多进程同时写相同 index key。更可靠的演进可以采用单写入者、索引代际 manifest 与原子切换，查询固定读一代；失败时降级线性检索或重建，并用日志报告。不能说有 SQLite transaction 就等于 SQLite 与 FAISS 全链路原子一致。”

**原理展开与例子：**设索引原来含 A/B，新加入 C。时序可能是：SQLite 提交 C 和标签 → 进程退出 → FAISS 仍只有 A/B。恢复时比较维度与条数可发现部分不一致，然后由 SQLite 向量重建。另一个时序是 A 进程和 B 进程都加载旧 FAISS，各自加不同文档再替换文件；后写入者可能暂时覆盖另一个进程的内存视图。具体恢复能否兜住，必须通过测试观察，不能仅靠“最终大概会重建”推断。

故障注入可在受控临时目录用假的中断点实现，不必真的破坏生产文件。检查点包括 DB 插入提交前、提交后、临时 FAISS 写完、原子替换前、替换后。每个点重启检索器，要求旧已提交文档仍可搜到，新文档要么按定义可见、要么明确待重建，不能串到不属于当前 scope 的记录，也不能返回错位 ID。

再做两进程同时加入互不相交文档的压力测试，最终用精确线性向量基线检查允许文档集合与标签对应关系。HNSW 是近似搜索，不能要求它的 Top-K 每次与暴力检索完全一致；但记录 ID 与内容对应、作用域隔离、维度一致这些属于必须成立的不变量。

若引入 generation 机制，可先完成新代 DB/文件并记录校验信息，再原子发布“当前使用第 N 代”指针。读请求绑定同一代，旧代等无读者或过安全时间再清理。单写者架构往往比在本地文件上补很多复杂分布式锁更容易维护，尤其目前这个项目定位是轻量本地 Agent。

**源码现状与改进边界：**当前已有临时文件替换、SQLite 向量备份、元数据检查与 ANN 错误线性降级，也有 scope 白名单过滤。没有完整多进程索引发布协议；仅按维度和数量核验，也不是任意内容损坏的万能校验。建议补故障注入、索引代际与写入者约束，不把已有恢复分支夸成严格零丢失承诺。

**面试官追问：**“把 FAISS 换成 Milvus 就全解决了吗？”答：它能提供不同级别的索引和并发管理，但原始事实、文档版本、权限过滤、删除传播、query cache 和服务重试仍需设计。换基础设施不能自动修正业务身份与一致性合同。

<a id="exp-r11"></a>

### R11｜Agent 刚跑过测试又修改了文件，旧的成功记录还能作为验收吗？通过 shell 或外部编辑发生的变化怎么办？

**来源与信号：**C07 的错误 patch 回滚、验证和重试，C05 的优化指标有效性，C06 的稳定恢复。本题关注证据对当前版本是否仍有效，比“模型说完成是否可信”再深入一步。

**为什么会问到 MiniClaw：**[assistant/coding.py](../../src/MiniClaw/coding_agent/assistant/coding.py) 对成功 `write/edit` 调用触发 `invalidate_verification()`；[goal/store.py](../../src/MiniClaw/coding_agent/goal/store.py) 根据 checkpoint、失败时间和完成状态筛选新鲜验证。但测试记录没有天然绑定整个当前工作区的内容快照。

**90 秒回答：**“验证通过只证明某个时间、环境和代码版本下的一组检查通过，不能无限期复用。MiniClaw 已经在成功 write/edit 后让 Goal 验证失效，并要求新鲜成功命令；这是很好的基础，但普通 shell 修改、外部编辑器或另一个 Agent 改文件未必经过同一钩子。我会给验证证据绑定目标文件或工作区的版本指纹、命令、环境与依赖信息，完成前再次确认这些前提没有改变。先用保守失效保证正确，再根据依赖关系优化只重跑受影响检查。”

**原理展开与例子：**测试 `add(2,3)==5` 通过后，再把 `return a+b` 改成 `return a-b`，旧日志还写着 passed。无论改动由 edit、bash 内脚本还是外部编辑器完成，它对应的源代码都已经不同。时间更近的测试也不一定覆盖新增需求，因此需要把“版本新鲜”与“覆盖充分”分别判断。

设计一组验收回归：验证成功后分别通过 write/edit、普通 shell、另一进程、外部文件替换修改目标；再调用完成检查，应该按既定合同要求重新验证。另加“不影响代码的纯读取不应失效”和“依赖锁文件变化应使相关验证失效”作为对照。验证过程中发生修改也要测，防止它恰好读取一半新一半旧文件而产生不可复现结果。

第一版可以对关键源文件、配置和依赖清单做内容摘要，记录在 verification artifact 中。开始和结束校验一致，完成时再次核对；数据规模大时再用文件变化日志或依赖图优化。目录 mtime 不是可靠内容身份，Git HEAD 也不能覆盖未提交变更，不能只记录一次 commit ID 就宣布绑定了实际文件状态。

命令 exit code 0 也不是业务结论：`echo passed` 同样返回 0，或者测试被错误过滤导致 0 个用例执行。验收应保留命令、实际运行的检查、结果与对应 acceptance criterion；项目现有 objective evidence 检查和可选 Judge 能增加约束，但不构成形式化证明。

**源码现状与改进边界：**当前成功 write/edit 失效是直接钩子，Goal 会排除 checkpoint 前或最近失败前的旧验证，并阻止还有 pending 的验证时完成。它不是对任意宿主文件变化的全局观察器，也没有全工作区内容指纹。上面提出的跨入口变化测试和版本绑定属于增强方案，不能说项目已经保证 shell 改动也自动全覆盖。

**面试官追问：**“每改一个文件都跑全部测试太慢怎么办？”答：先建立可信的失效依赖关系，再按变更影响选测试，并保留周期性全量或关键发布检查。选择性测试本身也要用漏检率评估，不能因为速度快就假定相关测试总选对。

<a id="exp-r12"></a>

### R12｜你的检索准确率提升 5 个点，是组件有效，还是题库泄漏、缓存预热或随机波动？怎样构建能接受追问的实验？

**来源与信号：**C03 直接问评测集来源和结果可靠性，C06 直接问每个 case 跑几遍与指标，C05 问优化收益如何得出，C07 问怎样判断一次改动真的增强 Agent。

**为什么会问到 MiniClaw：**仓库同时有 [evaluation/splits.py](../../src/MiniClaw/evaluation/splits.py)、[benchmark/longmemeval_v2.py](../../src/MiniClaw/benchmark/longmemeval_v2.py)、[benchmark/code_compression.py](../../src/MiniClaw/benchmark/code_compression.py) 等代码。简历出现任何提升百分比，都应能回答数据怎么选、结果怎样记录，以及这份测试是否已经被用来调参。

**90 秒回答：**“我会先冻结基线、模型路由、工具能力、任务输入、展示预算和评估规则，把开发集用于调权重，保留集只做最终确认。按查询家族、来源会话和近重复事实检查泄漏，不能只确保 ID 不同。每个任务在相同配置下做配对比较，并根据模型随机性重复若干次，报告均值、区间、失败类型、token 与延迟，而不是只选最好的一次。冷热缓存分开测，模型 fallback 单列。最后对提升最大和退步最大的案例查真实注入 trace，解释提升来自召回、重排、预算还是模型使用证据。”

**原理展开与例子：**如果“用户目前用哪个数据库”和“用户现在使用的数据库是什么”分到开发/保留两边，题目 ID 不同不代表独立。若开发时反复看过某会话的答案，又在保留集问同一事实，只换说法，也可能高估泛化。另一方面，多题共享公开检索语料并不自动构成标签泄漏：要区分可用的测试证据与已经暴露的问答标签，不能把所有共享文档都视为违规。

当前 split 代码用规范化 query 的哈希标记家族，能隔离精确及部分表面重复，并记录已有结果中的 exposure；它不自动识别所有语义改写或同一事实的跨语言问法。进一步可增加近重复聚类、按事件/会话分组和人工抽检，明确“防哪种泄漏”，而不是宣称任何语义重复都已消失。

对同一批 N 个 case，可记录配对差值 `dᵢ=score_new(i)-score_base(i)`，总体提升为 `mean(dᵢ)`。置信区间可以按 case 或更高层的会话簇进行 bootstrap；若同一会话多题强相关，不宜把每题当完全独立样本。报告区间横跨 0 时，应承认现有样本不足以确认稳定提升。

重复次数不能拍脑袋用一个通用数字。先用小规模 pilot 估计波动与成本，再决定每 case 重复多少次、是否增加 case 更划算。假设某方法每次成功概率 p，多试 r 次“至少成功一次”的概率为 `1-(1-p)^r`；因此把新版本多试 5 次的最佳结果与旧版本只跑 1 次比较，成功率自然会偏高，并不能证明系统变强。

建议把实验分层：确定性组件测试验证缓存失效、作用域和配对切分；固定候选检索评估只测索引与排序；完整 Agent 任务验证真实文件与验收结果。消融一次只改一个重要因素，必要时再做交互实验，例如 dense 开关与 reranker 开关的 2×2 组合，避免把总收益全归因给某一个组件。

**源码现状与改进边界：**已有 split/exposure 和多个 benchmark 适配器提供评估基础，不代表某一组配置已经经过足够大样本验证。本次是面经与源码研究，没有新增真实收费模型结果；所有“提升 5 点”都是题目假设，不是 MiniClaw 实测成绩。面试时应带可复跑配置、样本清单、逐 case 结果和失败分析，用证据解释机制价值。

**面试官追问：**“预算很少，难道就没法比较了吗？”答：可以。先用确定性和小规模人工标注找明确错误，采用配对设计减少方差，优先测影响最大的假设，并坦白区间较宽。小预算下最不该做的是反复挑最好结果然后给出虚假的精确提升。

<a id="exp-section-05"></a>

## 这十二题怎样练到能连续追问

每题先用自己的语言讲 90 秒版本，再打开对应源码指出一个真实边界，最后说出一个能失败的测试用例。能说出“准备怎么改”还不够，要能说明“什么观察会证明我的方案没改善，甚至更差”。

例如 R05 可以用边界排名反例验证两组分数，R06 用写入后原样查询验证缓存失效，R07 用中文加长日志验证完整请求预算，R11 用测试后再改文件验证证据是否仍有效。它们都比背一段框架介绍更贴近国内实际项目追问：面试官在确认你是否知道实现的前提、失败窗口和验证办法。

答题时把三个层次分开说清楚：**源码已实现的能力、本次真正核验过的事实、为解决边界提出的改进方案。** 不把开放问题包装成已修复，不把公开面经作者自述包装成公司官方题库，也不把没有运行过的实验写成简历成绩。

<a id="exp-section-06"></a>

## 公开招聘平台与面经补充：把工程要求变成 MiniClaw 情境设计题

这组问题来自 2026-09-05 实际可读取的公开职位描述与用户面经，再转换为 MiniClaw 情境。**PQA01—PQA10 是结合来源设计的追问题，不是声称这些公司的面试官逐字问过 MiniClaw，也不是本项目已实现的新增功能。** 数值均为题设，不能当成项目实测性能、线上业务量或真实模型价格。

### 这次实际读到了哪些来源

| 来源 | 页面及类型 | 实际招聘/面试信号 | 与本项目的关系 |
|---|---|---|---|
| P01 | [实习僧：网易有道 Agent测试实习生](https://www.shixiseng.com/intern/inn_vl8o4lazzdpx)，第三方平台 JD，刷新2026-09-04 | 短引：“工具调用准确性、任务完成率”；“维护和扩展评测数据集（Golden Dataset）”。还列多轮一致性、RAG效果、异常场景、上线回归 | 直接支持工具正确性、任务验收与故障回归题 |
| P02 | [实习僧：屿智同行 Agent开发实习生](https://www.shixiseng.com/intern/inn_sto9ogs3qzgi)，第三方平台 JD，刷新2026-09-03 | 短引：“Python基础扎实”；“参数入参、执行逻辑、边界条件”。包括Prompt/Skill调试、Harness回归和任务分发 | 支持 Python 工程、工具契约、链路稳定性情境 |
| P03 | [实习僧：爱奇艺 Agent开发实习生(A11471)](https://www.shixiseng.com/intern/inn_dybolbgacrq1)，第三方平台 JD，偏前端，刷新2026-09-03 | 短引：“任务流可视化、多智能体状态面板”；明确要求 React/Next.js | 仅辅助说明跨端交付能力；不把前端要求推断成 Python 必问 |
| P04 | [实习僧：得物App Agent产品开发实习生](https://www.shixiseng.com/intern/inn_qwwrrxscnw1d)，第三方平台 JD，刷新2026-09-01 | 短引：“从产品需求到上线的端到端研发交付路径”；强调AI Coding与现有研发流程衔接 | 支持版本、验收、交付和Git协作题 |
| P05 | [牛客：8.31 阿里云 Agent Infra 一面](https://www.nowcoder.com/discuss/924245733962719232)，用户自述面经 | 短引：“PV变成原来的4倍，但吞吐量却没有上升”；还涉及多租户资源、持久化及异常情况。页面显示09-01 10:22，正文称8.31面试 | 支持资源瓶颈与故障窗口设计；原帖SGX情境不能等同于项目Docker |
| P06 | [牛客：滴滴 后端实习一面](https://www.nowcoder.com/discuss/924334862113198080)，匿名用户自述面经 | 短引：“异步任务执行失败或连接中断时，如何保证任务不丢失？”；“如何防止同一个任务被重复入队或重复执行？”。页面显示09-01 16:18，正文称8.27面试 | 支持调度、背压、租约、CAS、幂等与恢复题 |
| P07 | [实习僧：百度 Ai Agent后端实习生](https://www.shixiseng.com/intern/inn_ayrmhzkekdgr)，第三方平台 JD，Go 后端，刷新2026-09-02 | 短引：“对接日志、监控、并发调度”；“自动化联调、性能压测、版本回归工具” | 能借鉴其跨语言系统要求，再用 MiniClaw 的 Python 实现作答；岗位本身要求Go |
| P08 | [实习僧：字节跳动 Agent开发实习生-抖音](https://www.shixiseng.com/intern/inn_pceqm0fghwlg)，第三方平台 JD，前端，刷新2026-08-14 | 短引：“流式输出（Streaming）、状态管理和工具调用（Tool Calling）链路的可视化” | 支持可选实时UI对接情境；不把MiniClaw静态架构图说成已完成该能力 |
| P09 | [牛客：字节AI Agent实习面经](https://www.nowcoder.com/feed/main/detail/8a553bb6ea8445d0b0abe11e87614cea)，用户自述面经 | 短引：“当Agent拥有大量工具时，如何避免工具描述导致Prompt过长？”；“工具返回的数据量过大”。页面显示08-18 16:43，正文称8.13/8.16两轮 | 支持动态工具发现、输出预算、模型分工及成本计算 |

这些链接均实际取得 HTTP200 并读到具体职位描述或主帖正文，引用没有依赖搜索引擎摘要。**P09 复用原文 S4 的同一 URL，本轮重新读取并用于扩展情境，不计为本轮新增独立来源。** 实习僧页面标注了企业名，但它们仍是**第三方招聘平台职位页，不是企业官网**；未独立核验发布者身份、剩余名额或岗位有效性。牛客来源是发帖者自述，不能升级为公司认证题库；页面只有月日的条目不擅自补年份。

P01 还说明了为什么要仔细读 JD：页面卡片写“北京、本科、200—250元/天、5天/周、实习3个月”，正文却写“每周4—5天、连续不少于6个月”。文档保留这个差异，不能擅自替招聘方统一条件。此处研究岗位能力，没有代用户联系或投递。

### BOSS、小红书、知乎的实际访问结果

| 平台 | 本次访问入口 | 实测返回 | 因此能够/不能得出的结论 |
|---|---|---|---|
| BOSS直聘 | [Agent实习搜索](https://www.zhipin.com/web/geek/job?query=AI%20Agent%20%E5%AE%9E%E4%B9%A0) | HTTP200，正文仅“加载中，请稍候” | 读取到动态页面壳，没有岗位正文，不据此编造招聘要求 |
| 小红书 | [Agent面试搜索](https://www.xiaohongshu.com/search_result?keyword=agent%20%E9%9D%A2%E8%AF%95) | HTTP200，只有导航和分类，未获得具体笔记正文 | 不声称本次读到了小红书笔记；没有使用登录账号或浏览器 |
| 知乎 | [Agent实习面试搜索](https://www.zhihu.com/search?type=content&q=agent%20%E5%AE%9E%E4%B9%A0%20%E9%9D%A2%E8%AF%95) | HTTP403 | 访问受限，没有绕过限制，不能把搜索摘要当原文 |
| 补充公开平台 | 实习僧、牛客的上表具体页面 | HTTP200，完整主要内容可读 | 采用这些可核验来源完成研究 |

以下十题不要求先背新框架名。答题顺序是：明确业务约束 → 说明现有代码 → 指出失败窗口 → 给出改进设计 → 给出能推翻方案的验证方法。

<a id="exp-pqa01"></a>

### PQA01｜飞书突发来了100个任务，你把 async worker 从8加到80，为什么可能更慢？

**来源与招聘信号：**P02 的 Python/任务分发要求，P07 的并发调度与性能分析要求，P06 关于异步调度、线程数量和压测的自述追问。这里把它们转成 MiniClaw 的排队问题，不是把 Go JD 说成 Python 原题。

**项目为何被问：**`FeishuAssistantRouter` 已有会话队列和全局 semaphore，但“用了async”不能回答容量问题。代码默认同会话串行、队列5、跨会话并发8；多个会话还可能共用同一个 Direct 工作区。

**详细回答：**先量化服务时间和到达率。假设一个任务平均占用执行槽40秒，有8个槽，理想服务能力为 `8/40=0.2任务/秒`，约每分钟12个。若长期到达率 `λ=0.12/秒`，利用率近似 `ρ=λ×40/8=0.6`；若到达率变成0.3/秒，ρ=1.5，队列必然越积越长。加大等待队列只延迟拒绝，不创造吞吐。

不能直接加到80：模型提供商可能限RPM/TPM；容器总内存、CPU、磁盘和网络会竞争；默认每容器1 GiB并不意味着宿主能同时安全支撑80个；文件共享还可能产生逻辑冲突。并发过高触发429，再重试，实际请求量反而上升。async只让等待时让出执行权，纯Python CPU工作或同步文件锁仍可能阻塞循环。

我会设置全局接入限额、租户配额、会话队列上限和最大排队时间，先按任务类别估算资源；只读短任务、长测试和等待审批任务分别观察。稳定状态可用 Little 定律 `L=λW` 验算平均在途量：若λ=0.12、平均总停留W=55秒，则平均在途约6.6个。这个平均值不能替代P95等待时间，更不表示突发100个都能及时处理。

**扩展知识与验证：**进行并发1/2/4/8/16阶梯压测，记录accepted/rejected、队列时长、执行时长、429率、event-loop lag、CPU/内存和最终正确率。测试同时包含“100个不同session”与“100条同session”；后者串行限制不同，不能用前者结果包装。

**源码边界：**当前全局semaphore管执行槽，不是全局消息积压上限；会话缓存未在这里实现LRU；没有自适应并发或租户调度。拟新增的限流/优先队列是扩展设计。定位：[FeishuAssistantRouter](../../src/MiniClaw/platforms/feishu/router.py)、[飞书默认配置](../../src/MiniClaw/platforms/feishu/config.py)。

**追问：**审批等待算不算占槽？当前算，因为 `_handle()` 在semaphore内等待。优化时可以释放昂贵执行配额，但必须保留同session的状态所有权；恢复前重验授权、目标版本和剩余预算，不能让后续普通消息越过旧任务乱改上下文。

<a id="exp-pqa02"></a>

### PQA02｜八个任务都在等审批，用户的“取消”消息为什么必须走另一条路？迟到的批准怎么办？

**来源与招聘信号：**P02 强调任务分发、结果同步和边界条件，P01 强调多轮一致性与异常场景，P06 强调任务状态与恢复。审批控制通道是据此延伸的工程情境，并非原帖逐字题。

**项目为何被问：**当前飞书把审批回复、取消命令放在普通会话入队前处理；这恰好可以解释控制消息为什么不能与普通工作共用堵塞路径。

**详细回答：**假设任务A拿着会话执行权等待用户批准，批准消息若排在A后面，消费者只有A完成才取批准，A只有取到批准才完成，就形成逻辑死锁。全局8个执行槽全在等待时，控制消息若也要求先拿同一槽，会把所有任务锁死。因此控制请求要有独立且有界的快速通道，不能被业务队列阻塞，但仍需要鉴权和限频。

批准不是一句泛化“同意”。至少绑定 `tenant/session/run/approval_id/requester/normalized_call_hash` 和有效期；请求者身份由服务端认证上下文提供，不能相信消息正文里的user_id。取消先变更任务状态，再发协作取消信号，防止后续步骤继续启动。批准与取消同时到达时，使用状态版本或CAS，只允许合法前置状态转换，例如 `waiting_approval(v7) → approved(v8)`；若已cancelled或版本不一致，迟到批准只能得到明确拒绝，不能复活旧任务。

当前ApprovalInbox会检查session和发起者，future已完成不会二次批准，超时/取消收尾会移除pending。ApprovalGate还复算规范化调用hash。这是已有基础。但当前取消分支没有与审批完全相同的requested_by检查，扩成群共享企业服务时要明确“谁能取消谁的任务”。未来把pending放数据库，还要加入稳定run标识和原子状态迁移，而非只用六位短码做全局身份。

**扩展知识与验证：**画五种竞态：批准后取消、取消后批准、超时后批准、另一用户批准、同文本不同session批准。为每种排列断言状态、工具启动次数、审批审计和最终回复。最重要的断言是“未经有效授权的副作用工具启动次数为0”，不只是UI显示拒绝。

**源码边界：**当前inbox是进程内future，不持久化跨进程审批等待；控制消息绕过队列也不等于拥有完整多租户授权系统。定位：[ApprovalInbox](../../src/MiniClaw/coding_agent/approval/interaction.py)、[ApprovalGate](../../src/MiniClaw/coding_agent/approval/gate.py)。

**追问：**审批时文件被人改了，hash没变怎么办？调用hash绑定参数，不绑定文件内容。应把预览对应的base revision/文件摘要纳入批准对象，提交前重验；变化后重新生成差异，不能让用户批准旧内容却执行新版本。

<a id="exp-pqa03"></a>

### PQA03｜测试命令每秒输出2 MiB、持续50秒，同时取消发生，怎么避免把Agent服务拖死？

**来源与招聘信号：**P09 直接出现大工具结果超上下文问题；P07 要求资源调度与性能瓶颈定位；P05 的资源受限情境提供进一步追问信号。本题不把SGX机密计算能力映射为Docker已有能力。

**项目为何被问：**MiniClaw有三层不同预算：Runtime进程输出捕获、BashTool展示截断、最终模型上下文预算。很多候选人只想到最后删日志，却忽略前面已经占满内存或堵住管道。

**详细回答：**题设输出总量 `2×50=100 MiB`。Docker Runtime持续排空stdout，只保留最后10 MiB，BashTool再把返回给模型的内容压成尾部2000行或50 KiB；如果被工具层截断，日志保存的是Runtime已经捕获到的内容。因此 `.aster/tool-output` 中所谓完整日志，不能恢复已经丢弃的前90 MiB。

关键是持续读取而非只在结束时读取：子进程stdout缓冲区满了会阻塞写，导致程序看似卡住；stdout与stderr分开时更要避免只读其中一条。MiniClaw的bash把二者合并。取消要停止容器内执行、回收Docker CLI并停止reader，不能只取消等待协程。

宿主后端是重要差异：`HostCommandExecutor` 当前 `communicate()` 聚合全部输出，不应用Docker的有界tail捕获。假设同时8个宿主任务各产生100 MiB，仅原始捕获就可能超过800 MiB，解码字符串和中间拷贝还会放大。要统一后端，应该流式排空并用有界ring buffer，或流式落盘到有配额的产物文件；到达磁盘配额后明确记录截断/停止策略，而不是继续无界写盘。

**扩展知识与验证：**用假的长输出进程构造三个场景：大量短行、单条超长行、stderr持续输出；再在中途取消。测峰值RSS、落盘字节、停止延迟、残留进程/容器、模型收到的字节数。不能把“函数返回cancelled”当成进程真的被回收的唯一证据。

**源码边界：**Docker限制已有，host有界流捕获尚未实现；文件产物机制不是无限日志仓库。图像read也只是类型提示，不能通过日志路径自动获得图像理解。定位：[两种命令执行器](../../src/MiniClaw/coding_agent/runtime/execution.py)、[BashTool](../../src/MiniClaw/coding_agent/tools/bash.py)。

**追问：**日志头部藏着第一次失败原因，尾部全是重试怎么办？可采用“头部保留+尾部ring+错误锚点+外部日志引用”的分层策略，同时保持原始偏移和截断标记。不能用模型摘要替代唯一原始证据，也不能继续宣称保存了全部日志。

<a id="exp-pqa04"></a>

### PQA04｜把MiniClaw搬到两个worker，消息已处理但进程在写completed前宕机，如何避免重复副作用？

**来源与招聘信号：**P06明确问重复入队、数据库一致性、租约和实例宕机恢复；P05要求数据到状态持久化并考虑异常。

**项目为何被问：**现有 `PersistentEventDeduplicator` 与 `DeliveryManager` 有持久日志，但内存状态和同key锁属于单进程。仅在append时加文件锁，不能把claim与执行变成跨进程事务。

**详细回答：**先承认不可能单靠本地completed日志保证任意远端动作只发生一次。我会拆成“任务只被一个有效租约拥有”和“业务副作用按稳定幂等键执行”两层。数据库以 `tenant_id + inbound_message_id` 建唯一约束，事务内创建任务和outbox记录；worker用CAS领取 `queued → running`，携带lease_owner、lease_until、attempt_token和version。长任务续租，过期后另一个worker可领取新token。

为什么还需要fencing token？旧worker可能只是在网络分区中暂停，租约过期后仍继续执行。新worker拿到更大epoch并不自动停止旧worker。关键下游写入必须拒绝旧epoch，或使用唯一业务operation_key拒绝重复；如果下游不支持这些能力，就要把结果标为unknown并查询对账，不能盲目再调用。

出站发送另用稳定业务键，例如 `tenant/session/run/final/chunk_index`；同一次重试沿用该键。如果飞书已收到、本地响应丢失，只有下游也按稳定键去重才进一步减少重复。当前FeishuTransport每次reply生成新UUID，不能把DeliveryManager本地key说成已传到远端的幂等键。

**扩展知识与验证：**假设续租周期10秒、租约60秒，停止进程后理论发现时间在0—60秒再加扫描延迟；这只是恢复时延上界的组成，不代表60秒一定合适。要根据P99调度停顿、网络故障和最长不可中断区间选择，并验证旧worker复活。至少在“外部成功→本地记录前”“数据库提交→队列ack前”“续租失败后旧worker继续”三个窗口注入故障。

**源码边界：**当前没有这套数据库租约/fencing/outbox恢复器；`run-state.json` 的owner_pid检测适合本地中断说明，PID不是跨机器租约。源码已有状态可作为迁移起点，不能包装成现成分布式调度。定位：[入站去重](../../src/MiniClaw/platforms/feishu/dedupe.py)、[DeliveryManager](../../src/MiniClaw/platforms/delivery.py)、[RunStateStore](../../src/MiniClaw/coding_agent/runtime/state.py)。

**追问：**只用Redis分布式锁行不行？锁能协调持有权，但TTL过期后旧持有者继续执行的问题仍存在；需要下游校验token或业务幂等。锁与幂等是互补，不能互相替代。

<a id="exp-pqa05"></a>

### PQA05｜JSON schema已经通过，但工具参数仍把任务做错，你怎样设计“参数成功率”指标？

**来源与招聘信号：**P02明确强调入参、执行逻辑、边界条件与结构化输出稳定性；P01要求工具调用准确性、边界Case和Golden Dataset。P09的大工具集也会放大参数选择错误。

**项目为何被问：**MiniClaw执行器会校验schema，但 schema只处理一部分结构问题。比如bash的command是合法字符串，仍可能引用错目录；edit的oldText类型正确，仍可能匹配多处；一条测试成功也可能测错模块。

**详细回答：**不要只报“JSON可解析率”。我会把漏斗拆成：工具选择正确率 → 参数可解析率 → schema通过率 → 业务约束通过率 → 工具执行成功率 → 动作目标正确率 → 任务验收通过率。每一层要明确分母，不能排除失败样本再把结果报高。

题设100次预期调用，90次schema通过，其中80次实际执行成功，最终70次作用于正确目标，则schema通过率90%，条件执行成功率80/90≈88.9%，端到端正确动作率70%。若只把80次正常退出当“工具准确率80%”，会漏掉10次执行成功但改错对象的失败。未知工具、授权拒绝、基础设施不可用还应独立归因：授权正确拒绝并不是安全失败，但业务任务可能仍未完成。

测试集要包括缺字段、额外字段、bool冒充integer、路径逃逸、符号链接、重复oldText、输入是字符串化edits、超时高于Runtime最大配置、schema版本更新等。对于扩展工具，用成熟schema validator支持清晰的标准子集，再用业务校验器检查对象版本、范围和权限。不能期望多写一句工具description替代这些判断。

**扩展知识与验证：**记录tool_name、schema_version、validation_stage、error_code、target_hash和最终验收结果；对修复前后同一Golden Dataset比较。不要把全部原始敏感参数直接写入可公开日志。模型重试次数也要计入成本和延迟，不能只看最后一次成功。

**源码边界：**当前手写校验器是JSON Schema子集，不支持所有oneOf/$ref语义；edit有兼容prepare_arguments；ToolManager运行时还会再次检查角色可用性，但没有远端schema版本协议。定位：[ToolExecutor](../../src/MiniClaw/coding_agent/tools/executor.py)、[EditTool](../../src/MiniClaw/coding_agent/tools/edit.py)。

**追问：**为了提高成功率，失败就让模型无限修参数可以吗？不可以。确定性语法错误可给有限修正机会；权限拒绝不应诱导换路径绕过；有副作用且结果unknown时先对账。错误类型决定重试策略，而不是统一再问模型。

<a id="exp-pqa06"></a>

### PQA06｜从10个工具扩到500个MCP工具，怎么在不漏掉必要工具的前提下减少token？

**来源与招聘信号：**P09主帖直接问MCP调用流程和工具描述导致Prompt过长；P02强调工具触发、入参与联动稳定性。下面的数字与版本管理方案是扩展设计。

**项目为何被问：**当前默认10个工具可直接传definitions；扩到500个时，ToolManager的角色筛选仍有用，但不足以解决语义选工具、描述预算和动态版本变化。

**详细回答：**假设每个完整schema平均300 tokens，500个合计150000，已经超过示例128000上下文窗口，更不用说用户任务。先按租户授权与role做硬过滤，再用简短目录按任务检索候选，选中后加载完整且带版本的schema。假设10个候选占3000 tokens，相比全部展开减少98%；这是按题设算出的描述量差异，不是项目实测总费用降低98%。

不能只优化token。建立“必要工具全集”的标注集，测候选Recall@k、最终选择正确率和任务完成率。假设每次需要的单工具进入候选概率0.95，某任务需3个工具且暂按独立事件演算，全覆盖概率只有 `0.95³≈85.7%`。现实工具相关性会改变结果，但这个例子说明单工具召回高不代表多步任务可靠。可保留受控的二次发现工具，让模型表达缺失能力并扩充候选，设置次数与总预算。

MCP客户端完成连接初始化与能力协商，再发现远端工具，转换为内部定义；模型提出调用后，经当前权限/审批/参数检查，再由适配器执行远端调用，结果归一化回ToolResult。catalog需缓存版本或摘要：模型看的是v1，服务端变v2时不能盲目用旧参数执行新语义。工具撤权、重名、description夹带指令和远端超时都要有策略。

**扩展知识与验证：**区分模型tool_call_id、MCP请求ID和业务幂等键：第一个配对对话，第二个配对协议请求，第三个抑制业务副作用；它们不应因为都叫ID就混用。测试工具改名/删字段/撤权/同名不同server/发现后失联，断言不会因旧catalog继续执行未授权动作。

**源码边界：**MiniClaw当前没有MCP client/server主路径，也没有500工具检索器；ToolManager可做角色过滤和注入，是适配起点，不是已完成MCP发现。来源是自述面经，不说明该公司实际采用上述方案。定位：[ToolManager](../../src/MiniClaw/coding_agent/tools/manager.py)。

**追问：**把所有完整schema缓存到模型侧不就好了？缓存可能减少价格或重复计算，却不解除上下文上限，也不解决工具混淆、权限变化和恶意描述。缓存、候选检索、授权过滤解决不同问题。

<a id="exp-pqa07"></a>

### PQA07｜要把MiniClaw接到实时任务面板，断线重连后怎样避免“还在跑”和“已取消”同时显示？

**来源与招聘信号：**P08明确要求Streaming、状态管理、Tool Calling可视化；P03仅辅助提示任务流/状态面板需求；P07提供接口联调与标准化服务信号。P03/P08都是前端岗位，本题是可选跨端协作设计，不等于Python后端岗位都要精通React。

**项目为何被问：**MiniClaw已经有AgentEvent、ModelEvent、trace和run-state，适合作为事件源；但当前frontend只有静态SVG展示，飞书也主要是“处理中→最终更新”，不是完整实时控制台。

**详细回答：**首先区分文字增量与权威任务状态。UI不能看到模型说“任务已完成”就把状态设complete，也不能因为socket断开就把后端任务设cancelled。服务端发布带 `run_id、event_id、sequence、revision、type` 的事件，状态变迁由后端确认，文字只用于展示。

客户端按同run的序号去重并有序应用；取消请求的响应仅表示已接受取消，随后等待后端cancelled终态或显示“正在取消”。断线重连先拉当前状态快照和已保存的最后事件位置，再续接；若事件保留窗口已过，重建快照而不是猜缺失增量。旧run的迟到token不能追加到新run的文本区，终态也不能被较旧revision回退。

题设模型每秒80个token、每token一条UI更新，100个并发就是每秒8000次渲染触发。可以每50—100ms合并文字delta，同时状态/审批事件独立及时发送；但不要为了减少渲染把多个Tool Call合成无ID的字符串。慢客户端应有有界发送缓冲和重同步机制，不能让每个浏览器的无限队列拖垮服务器。

**扩展知识与验证：**构造事件重复、乱序、缺口、旧run迟到、取消后断线、服务端重启等序列，检查同一日志重放后UI状态确定。用网络限速验证后端执行是否被慢前端背压；正文可丢增量后重取快照，关键状态不可无声丢失。

**源码边界：**AgentEvent本身不是完整的可断线续传网络协议；当前没有这些前端事件序号/快照API。run-state描述最新运行状态，不能直接当所有历史事件存储。输出和trace还需做鉴权与脱敏，不能直接把整个私有trace目录暴露为静态资源。定位：[AgentEvent](../../src/MiniClaw/agent/events.py)、[当前静态前端服务](../../src/MiniClaw/frontend.py)。

**追问：**SSE还是WebSocket？后端到UI单向事件流可用SSE，控制操作用独立HTTP；需要频繁双向实时数据时可选WebSocket。关键是状态与重放语义，换传输协议本身不会解决重复和乱序。

<a id="exp-pqa08"></a>

### PQA08｜Goal已complete、测试全绿，但用户本地代码后来变了，你敢直接把Snapshot覆盖回去吗？

**来源与招聘信号：**P04直接要求从需求到上线的端到端交付，P01要求修复验证、发布验收和回归。这里考察交付证据，不是再背“Goal和Agent有何不同”。

**项目为何被问：**MiniClaw当前snapshot可保留任务副本，但没有通用自动回写系统；Goal验证与代码版本绑定也不完整，成功edit/write会使旧验证失效，任意bash写文件没有同等自动检测。

**详细回答：**不能直接覆盖。开始任务时记录源项目base revision和相关文件摘要；产物应交付为“从base到候选版本的patch+验证记录+验收证据”，而不是一整个目录覆盖。验证记录绑定被测试的tree hash、依赖环境、命令、退出码和产物摘要。Goal complete只证明当前门槛接受，不自动授予合并或上线权限。

应用时检查源工作区是否仍对应base。若用户改了相同文件，应做三方合并，冲突不能让模型悄悄选择一方；即使文本能干净合并，也要在合并后的新tree上重新跑相关验证，因为语义冲突可能不表现为Git冲突。新验证结果才可支持最终交付。

题设10个验收条件，只有2个测试命令，不能按“2次测试都绿=100%完成”。建立criterion→check/artifact映射，标明自动、人工、尚未验证；覆盖率80%也不是需求满足率80%。例如代码生成界面要求“可访问性达标”，单元测试通过无法代替键盘和屏幕阅读器验证。

**扩展知识与验证：**设计四个fixture：源文件未变化、用户修改无关文件、相同文件非冲突修改、相同函数语义冲突。断言不会覆盖用户改动，验证hash与提交hash一致，失败时保留可审阅差异。涉及部署的外部副作用还应独立做版本与回滚策略；本地snapshot不能撤回已发送消息或数据库变更。

**源码边界：**现在没有通用patch合并器和tree绑定验收。GoalJudge默认关闭，开启也只是审查已提供证据，不自动建立发布权限。提出这些改进时应说明是下一步设计。定位：[snapshot](../../src/MiniClaw/coding_agent/runtime/snapshot.py)、[Goal验收](../../src/MiniClaw/coding_agent/goal/store.py)、[验证失效接线](../../src/MiniClaw/coding_agent/assistant/coding.py)。

**追问：**用户已经同意最初计划，后来是否还要再审批？要看原授权是否覆盖最终操作及目标版本；不要把“同意修bug”自动升级成“任意覆盖用户新代码或发布生产”。系统应把最终具体差异和版本关系变得可审阅，权限判断基于已经获得的授权范围。

<a id="exp-pqa09"></a>

### PQA09｜你说“小模型压缩更省钱”，给你1000个任务，怎样算总成本并验证没把成功率压没了？

**来源与招聘信号：**P09主帖问为何用小模型管理记忆/压缩以及Prompt Cache效率，P01要求任务完成率与持续评测。下面不引用任何真实供应商价格。

**项目为何被问：**MiniClaw有TokenUsage、价格配置、压缩、Trace和Goal成本，但成本下降可能来自少做任务、漏工具或提前停止，不能只看平均token。

**详细回答：**题设每任务6次模型调用，每次平均输入6000、输出500 tokens；假定输入4元/百万、输出12元/百万，不考虑缓存，则每任务成本为 `6×(6000×4+500×12)/1,000,000=0.18元`，1000任务180元。再假定每次请求有独立10%失败概率、最多两次重试、每次失败按同样费用收费，则期望尝试数 `1+0.1+0.01=1.11`，期望约199.8元。这是简化题设；真实失败计费、上下文增长、provider路由和缓存价格必须查实际账单。

把摘要交给小模型不能只比较单次摘要价格。总成本应包括主Agent调用、摘要、embedding/rerank、Judge、fallback/retry，以及失败重跑。假设新方案总成本从200降到150元，但成功任务从900降到600，则每成功任务成本由约0.222升到0.25元，反而更差；还损失用户体验。

做配对实验：固定任务集、主模型、工具环境、总预算与随机性设置，比较“无压缩/确定性压缩/小模型摘要/大模型摘要”；分别报告任务成功率、关键约束保留率、证据可回查率、总费用、每成功任务费用、P50/P95延迟。未见测试集只作最后评估，不围着失败样本反复改后再当泛化成绩。

**扩展知识与验证：**缓存命中率与缓存收益是两件事。前缀稳定可以提高命中机会，动态时间戳、工具顺序或频繁改写系统提示可能破坏缓存；但不能为了缓存冻结过时的权限、指令或工具schema。需要把可稳定部分与必须刷新部分按真实协议组织，再从usage验证实际缓存token。

**源码边界：**当前 `WorkingContext._model_checkpoint()` 与 `MemoryConsolidator._extract()` 都复用主 `profile.model_id`，没有内置独立的“小模型摘要路由”；若要做小模型与大模型摘要对照，需要先扩展模型配置与调用路径。模型蒸馏、BGE、reranker默认并非全开启；Goal预算以美元配置且在attempt边界检查，题设人民币不代表项目会自动做汇率折算。价格默认零也不代表免费；未知价格必须作为未知披露。定位：[上下文摘要调用](../../src/MiniClaw/coding_agent/memory/working.py)、[记忆蒸馏调用](../../src/MiniClaw/coding_agent/memory/distillation.py)、[Goal配置](../../src/MiniClaw/coding_agent/goal/config.py)、[GoalStore预算检查](../../src/MiniClaw/coding_agent/goal/store.py)、[模型费用字段](../../src/MiniClaw/llm/types.py)。

**追问：**能否上线前保证不会超预算？更严格做法是调用前原子预留最大可能成本，结束后结算，多个worker共享预算账本并传统一deadline。当前依靠run/attempt后累计不足以构成实时硬预算保证。

<a id="exp-pqa10"></a>

### PQA10｜请求量翻四倍、吞吐没变、P95暴涨，你如何在一天内找出MiniClaw真正的瓶颈？

**来源与招聘信号：**P05原帖明确给出“PV四倍而吞吐不升”的场景，P07要求性能分析、瓶颈定位、性能压测，P06涉及线程池参数验证。这是以MiniClaw为对象的诊断改编。

**项目为何被问：**Agent链路很长：入站排队、规则/记忆准备、模型等待、工具启动、进程执行、审批、压缩、结果投递。只看总耗时或CPU平均值，找不到究竟哪一层限制吞吐。

**详细回答：**先确认口径：请求量是收到消息、接受任务、启动run还是成功任务？吞吐是每秒完成attempt，还是验收通过的业务任务？如果400请求里只有100被接受，完成100不一定是执行退化；如果accepted持续增长却completed不变，积压才在扩大。

为每run建立时间分解：queue_wait、context_prepare、model_first_token、model_stream、tool_preflight、approval_wait、process_start、process_run、compaction、delivery。对于有重试的请求再按attempt展开，不把3次HTTP尝试当1次模型服务时间。结合资源看event-loop lag、容器CPU throttling/OOM、磁盘等待、模型429、投递重试和文件锁竞争。

例如题设并发8、平均占槽40秒，则不考虑其他瓶颈的上限约0.2任务/秒。流量从0.1加到0.4时吞吐停在0.2，本来就符合执行槽饱和，先做背压；如果并发从8增到16后吞吐仍0.2而429上升，优先怀疑上游配额。若模型耗时不变但event-loop lag从10ms升至2秒，应找同步CPU/IO/锁，例如上下文准备、大目录扫描、同步持久化，而不是盲目换更快模型。

**扩展知识与验证：**采用分层替身做隔离：把模型换成固定延迟fake，看本地调度极限；把工具换成无副作用固定结果，看模型服务瓶颈；固定工作区和查询，单独测检索/Guard扫描；最后恢复真实组合。每次只改一个变量，输出时间分布和成功率，不能只报“一次快了30%”。单日目标是定位主瓶颈并证伪替代解释，而不是重写整个框架。

**源码边界：**目前跨会话8并发、同session串行、工具调用顺序执行；grep/文件操作大多在宿主，Docker不是所有耗时的所在地；trace可提供部分观测，但上述细粒度span并非全部现成。需要补采样或指标，不能假装已经有完整OpenTelemetry链路。定位：[产品事件与Trace接线](../../src/MiniClaw/coding_agent/assistant/coding.py)、[WorkspaceGuard扫描](../../src/MiniClaw/coding_agent/runtime/workspace.py)、[飞书执行槽](../../src/MiniClaw/platforms/feishu/router.py)。

**追问：**平均延迟降低而P95上升，能上线吗？看业务SLO、成功率和尾延迟受影响人群。可能快路径缓存变好、慢路径重试变差；必须按缓存命中、长短任务、租户、工具类型分桶。总体均值掩盖特定用户长时间等待，不能作为唯一放行指标。

### 面对这类情境题，回答要守住的共同边界

招聘JD给出能力需求，面经给出发帖者报告的追问方式；它们都不能替代项目实测。合格回答应把“源码已有的机制”“从已有接口能扩展的方案”“尚需实验验证的假设”分开。本文没有因为研究招聘而给MiniClaw添加任何MCP、多租户调度、实时前端、自动发布或分布式幂等实现。

<a id="exp-section-07"></a>

## 结合本轮材料，你的复习优先级应该怎样排

下面的排序综合了“与你的代码有多直接的关系、多个公开材料是否重复出现、是否容易被连续追问”。它不把社招年限要求强加给实习生，也不把 Java/Go 或前端岗位直接当作 Python 岗位要求。

### 第一优先级：面试前应当脱离文档也能讲清楚

| 主题 | 为什么对 MiniClaw 尤其重要 | 准备到什么程度 | 可以拿出的现有材料 |
|---|---|---|---|
| 真实执行链与职责边界 | 你介绍的是编码 Agent，必然要能解释模型如何真正改文件 | 画出入口、装配、模型、工具、Runtime、结果回传；说清一次 run 与 Goal attempt | `coding.py`、`agent/loop.py`、本文完整轨迹 |
| 评测闭环与指标口径 | 官方岗位与实习 JD 都要求质量评估、回归和 badcase 定位 | 知道数据从哪来、每题跑几次、分母是什么、哪些指标只是近似 | `evaluation/`、`trace/`、固定 manifest 和历史报告 |
| 记忆和压缩 | 四层记忆是项目辨识度较高的设计，也最容易被追到细节 | 写入→冲突→索引→召回→预算注入→更新；压缩后如何回查 | `memory/manager.py`、`working.py`、`retrieval.py` |
| 检索效果为何不稳定 | chunk、embedding、BM25、rerank 在新面经中都有明确线索 | 区分源数据缺失、候选漏召回、精排错误、预算截断和生成误用 | ranked/rendered 记录、检索测试、消融方案 |
| 工具可靠性与副作用 | “返回合法 JSON”远不足以说明完成任务 | 参数、授权、隔离、执行、验证五层分别有什么保证 | ToolManager、schema、审批、Guard、Goal |
| 失败恢复与取消 | 长任务和真实入口会把偶发问题放大 | 能解释写成功但记录丢失、审批等待取消、子进程残留等窗口 | run-state、取消链、outbox、恢复测试 |
| 成本与延迟 | 招聘材料要求效果/时延/成本共同优化，面经会追测量方法 | 分清每次传输、每轮模型、整个任务的时限；知道哪些费用未必可见 | model.request/transport、usage、purpose、工具耗时 |

“第一优先级”并不意味着必须当场背出每一个默认数字。应当记住为什么设置这个限制、在哪个配置文件、覆盖哪一层，以及在什么条件下会失效。

### 第二优先级：能够给出具体演进设计

| 主题 | 目前已经有的起点 | 新设计至少要讲到的内容 |
|---|---|---|
| 多实例任务处理 | 会话队列、信号量、本地持久状态 | 原子 claim、租约、fencing、幂等、状态查询和恢复 |
| 企业权限与数据隔离 | workspace/scope、工具角色、审批和 Docker | 身份绑定、ACL 前置过滤、存储访问控制、删除传播与审计 |
| 多 Agent | 单 Agent 协议、角色工具注入、Goal | 任务依赖、最小工具集、独立工作区、合并验收、总预算与取消 |
| MCP 与工具发现 | ToolManager 与 provider-neutral 调用对象 | 初始化协商、schema 转换、权限、版本、连接故障、结果大小与注入 |
| 统一预算 | 模型/工具超时和 Goal 边界预算 | 全局 deadline、启动前预算预留、并发结算、未知价格策略 |
| 在线灰度与回滚 | 离线/手动真实模型 CI 和 baseline | 发布版本、影子/灰度流量、独立观测、阈值、回滚与数据污染防护 |

这些是“会设计”与“已经做过”之间最需要诚实区分的题。你可以清楚地提出数据库事务或任务租约方案，但如果没有实现和跑过压测，就不要补一句“已经支持高并发生产”。

### 第三优先级：理解与项目的连接，避免跑偏

Transformer、KV cache、embedding 训练目标、LoRA/SFT/RL、Linux/数据库/缓存基础仍可能出现，但应围绕投递岗位准备。对于 MiniClaw，最自然的连接是：

- KV cache 解释为什么稳定输入前缀可能省计算，与应用侧答案缓存、检索缓存分开。
- embedding/reranker 训练目标帮助解释模型领域差异和中文代码混合语料效果，不代表仓库自己训练了检索模型。
- SFT/RL 连接到 Trace 的状态—动作—结果、任务验收器和数据质量；当前仓库有实验与任务环境，不等于已有训练流水线。
- 数据库与并发基础连接到状态一致性、去重、缓存失效和索引发布，不必背一整套与项目无关的中间件清单。
- 前端 Agent 岗提到流式输出和任务状态可视化，可以借此讨论接口设计；MiniClaw 现有架构 SVG 页面仍不是在线任务控制台。

纯视觉/语音训练、与编码 Agent 无关联的推荐系统指标、他人独有业务的产品话术和未经核验的“公司必问清单”，不应占据这次复习主体。

<a id="exp-section-08"></a>

## 把“我会回答”变成“我能证明”：一份项目面试证据包

证据包可以很小，但必须真实。你不需要为一次面试先搭完整企业平台；应该挑出能清楚证明自己理解的几个场景。以下是建议准备的内容，本轮仅编写文档，没有替你运行这些新增实验。

### 证据一：一个正常修复任务的完整链路

准备一个有独立测试的小仓库，展示用户要求、模型调用、工具结果、代码 diff、真实测试输出和最终验收。讲清测试为什么能检验目标，不允许修改测试来“制造通过”。如果任务使用 Docker，还应确认模型修改和测试运行看到的是同一份有效工作区。

这份材料能回答“你是怎么实现的”；仅截一张聊天成功图片通常不够，因为看不见工具是否执行和哪些结果被验证。

### 证据二：一个明确的失败窗口及修复

例如检索缓存写后不失效、超大日志压缩后丢验收条件、未匹配 Tool Call 的恢复、取消后线程还想提交文件，任选真正理解的一项。准备触发条件、旧行为、原因、最小修复和独立检查。

你应该能解释：为什么这不是靠再写一句 prompt 就能稳定解决的问题；修复是否改变其他路径；有没有新的误拦或成本。面试官往往从这部分判断你是否能维护复杂系统。

### 证据三：一个公平的消融实验

选两组配置，只改变一个组件，如 artifact 开关、压缩策略、重排开关或 query cache 策略。固定模型、数据、工作区初态、预算与评分方式；多次运行并保留失败。至少同时报告任务正确性和资源消耗。

不要把“成功样本平均 token 更少”当成整个策略更好，因为新策略可能让困难题直接失败，留下的都是简单题。可以分别报告全样本完成率、所有尝试成本、成功条件下成本，并解释各自用途。

### 证据四：一个仍未解决的边界

准备一段诚实说明：现在的完整性保证是什么，尚未保证什么，若只有一周最先补哪一项。比如本地 outbox 降低重复投递但不保证远端 exactly-once，下一步先做稳定下游幂等键和状态对账；或 Goal 验证未绑定所有 bash 副作用，下一步将验收绑定文件版本。

这不是主动暴露缺点，而是证明你能区分可观察结果和推断。对于实习岗位，清楚识别问题并提出可验证改进，通常比虚构成熟生产规模更有价值。

### 将证据对应到面试回答

| 面试官追问 | 应当立即提供的东西 | 不足以支撑答案的东西 |
|---|---|---|
| “你的提升从哪来？” | 配对配置、样本量、指标分母、原始结果和坏例 | 只有一张不含配置的百分比图 |
| “为什么是这个参数？” | 参数扫描或消融，以及没选其他值的代价 | “大家都这样设” |
| “并发会不会出错？” | 不变量、锁/事务范围、复现竞态的时序 | “用了 asyncio，所以没有问题” |
| “安全吗？” | 威胁范围、授权与隔离位置、漏拦/误拦场景 | “提示词写了不要做危险操作” |
| “如何恢复？” | 持久状态、歧义窗口、检查实际副作用的流程 | “重启再运行一次就行” |
| “你做了哪些？” | 自己实际实现/维护/验证的范围与代码定位 | 把引用框架或现有源码全部算作个人成果 |

<a id="exp-section-09"></a>

## 本轮研究结论应当怎样使用

本轮新增题适合用来反向检查项目理解：先看问题独立回答，再对照当前源码，最后补上自己的真实实验经历。原问与招聘条件只是选题证据；回答中的扩展设计是为 MiniClaw 推导的方案，不是招聘公司的内部实现或标准答案。

关于来源，官方招聘、第三方职位页、候选人自述和页面访问限制分别标注。对正文中的年份、刷新时间、岗位性质和工作年限，不做超出原页面的推断；同岗位转载和同作者复盘不当成独立统计样本。

关于项目，本轮没有把 MCP、多 Agent、分布式队列、完整前端控制台或模型训练写成已经交付的能力。新增题的价值在于帮助你讲清现状、找到边界并提出具体方案。

关于文件，新增内容在原 Markdown 末尾追加，原有正文按字节保留；原文备份、公开网页、来源登记、草稿和合并核验记录均保留在本地研究目录。本轮没有因整理而删除原来的章节或研究材料。
