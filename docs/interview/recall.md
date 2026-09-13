```mermaid
flowchart TD
    A[任务进入 CodingAssistant]
    A --> B[MemoryManager.prompt_context]
    B --> C[QueryTracker 新任务重置]
    C --> D[同步外部存储与 memory_version]
    D --> E[rewrite_query 规范化并截断查询]
    E --> F[retrieve 进入统一召回入口]

    G[模型显式 memory(action='search')]
    G --> E2[rewrite_query 规范化查询]
    E2 --> F

    H[工具结果出现高信号错误 / 路径 / 符号]
    H --> I[maybe_refresh_prompt_context]
    I --> J[提取最多3段新证据]
    J --> K[查询证据去重并限制约2400字符]
    K --> E3[root query + workspace evidence]
    E3 --> F

    L[压缩提交完成]
    L --> L1[重新建立 memory context]
    L1 --> E4[沿用 root query，不重置 QueryTracker]
    E4 --> F

    E --> N[构造 cache key]
    E2 --> N
    N --> N1[user scope + channel scope]
    N1 --> N2[mode + normalized query]
    N2 --> N3[active context signature + limit]
    N3 --> N4[memory_version]
    N4 --> O{检索缓存命中?}
    F --> N

    O -- 是 --> P[返回缓存候选的副本]
    O -- 否 --> Q[按 query intent 分配各源候选数]

    Q --> S1[SemanticMemoryStore]
    S1 --> S2[exact symbols：路径 / 错误码 / 标识符]
    S2 --> S3[BM25 词法召回]
    S3 --> S4[vector / ANN 语义召回]
    S4 --> S5[源内 RRF 融合]
    S5 --> S6[exact 与词法优先排序]

    Q --> EPI1[EpisodicMemoryStore]
    EPI1 --> EPI2[BM25 词法召回]
    EPI2 --> EPI3[vector / ANN 语义召回]
    EPI3 --> EPI4[源内 RRF 融合]
    EPI4 --> EPI5[自动召回排除当前 session]

    Q --> ARC1[ArchiveMemoryIndex]
    ARC1 --> ARC2[子 chunk exact + BM25 + vector 召回]
    ARC2 --> ARC3[按最佳 anchor 找父级历史]
    ARC3 --> ARC4[扩展相邻 chunk：neighbor radius=1]
    ARC4 --> ARC5[自动召回可沿 Episode 链追到旧 session]

    S6 --> CAND[三源候选池]
    EPI5 --> CAND
    ARC5 --> CAND

    CAND --> V1[统一可见性过滤]
    V1 --> V2[active / status 检查]
    V2 --> V3[valid_from / valid_until 时间有效性]
    V3 --> V4[workspace / user / channel scope]
    V4 --> V5[confidence ≥ 0.15]
    V5 --> V6[superseded / superseded_by 冲突排除]
    V6 --> V7[自动召回排除已在当前上下文的内容]

    V7 --> M[跨源合并与初始打分]
    M --> M1[语义 1.15 · 归档 1.0 · 情景 0.75]
    M1 --> M2[叠加 exact / BM25 / authority / revision / recency]
    M2 --> M3[同一 source + record_id 合并来源信息]

    M3 --> RR[一次跨源最终重排]
    RR --> RR1[deterministic_rerank_score]
    RR1 --> RR2[覆盖率 / 短语 / 精确符号 / 状态 / 置信度]
    RR2 --> RR3{显式搜索且需要 Cross-Encoder?}
    RR3 -- 否：自动召回或无需重排 --> RR4[保留确定性排序]
    RR3 -- 是：候选接近或存在冲突 --> RR5[仅对候选池调用 Cross-Encoder]
    RR4 --> FD[按最终分数排序]
    RR5 --> FD

    FD --> DD[最终去重]
    DD --> DD1[equivalence_key 合并等价正文]
    DD1 --> DD2[不同版本不误删]
    DD2 --> DD3[合并 provenance 来源证据]
    DD3 --> CAP[来源多样性限制与 limit 截断]
    CAP --> CACHE[写入检索缓存]
    CACHE --> R0[返回 RetrievedMemoryItem]
    P --> R0

    R0 --> R1[_render_and_record]
    R1 --> R2[展示层再次合并完全重复正文]
    R2 --> R3[最多12条，按来源设置正文上限]
    R3 --> R4{是否超过渲染 Token 预算?}
    R4 -- 否 --> R5[保留完整候选正文]
    R4 -- 是 --> R6[按条目截断，保留来源与 hash / metadata]
    R5 --> R7[必要时附加最多3条 FACT_CONFLICTS]
    R6 --> R7
    R7 --> R8[生成 untrusted retrieved_memory 证据块]
    R8 --> R9[自动约4500 Token；显式搜索约15000 Token]
    R9 --> Z[以一次 user-role evidence block 注入模型]
    Z --> ZA[模型将其作为历史事实参考，不当作指令]

    SB[SKILL.md 独立旁路]
    SB --> SB1[system_skill_context]
    SB1 --> SB2[匹配 skill catalog]
    SB2 --> SB3[只读取选中的 SKILL.md]
    SB3 --> SB4[作为系统级 workflow instruction 注入]
    SB4 -.不进入 Memory 融合.-> ZA

    MUT[remember / replace / forget]
    MUT --> MUT1[更新 Semantic / Evidence 视图]
    MUT1 --> MUT2[memory_version 递增]
    MUT2 --> MUT3[失效旧 retrieval cache]
    MUT3 --> F

    classDef main fill:#123047,stroke:#42c8df,color:#eef7ff;
    classDef decision fill:#3a2a16,stroke:#f3ac52,color:#fff0d0;
    classDef security fill:#12362d,stroke:#63d69c,color:#effff5;
    classDef exec fill:#302248,stroke:#ae91ff,color:#f7f0ff;
    classDef error fill:#421f2b,stroke:#ff737d,color:#fff0f2;
    classDef result fill:#17352d,stroke:#70d6a2,color:#effff5;

    class A,B,C,D,E,E2,E3,E4,F,N,N1,N2,N3,N4,Q,CAND,M,M1,M2,M3,RR,RR1,RR2,FD,CACHE,R0,R1,R2,R3,R5,R6,R7,R8,R9,Z,ZA main;
    class O,R4,RR3 decision;
    class V1,V2,V3,V4,V5,V6,V7,DD,DD1,DD2,DD3,CAP security;
    class S1,S2,S3,S4,S5,S6,EPI1,EPI2,EPI3,EPI4,EPI5,ARC1,ARC2,ARC3,ARC4,ARC5,RR4,RR5,SB1,SB2,SB3,SB4,MUT1,MUT2,MUT3 exec;
    class G,H,I,J,K,L,L1,SB,MUT main;
    class P result;
```
