# MiniClaw Architecture

## Goal

MiniClaw is an independent Python implementation of a small coding-agent architecture. It follows three useful ideas seen in pi:

1. normalize model providers behind one message and event contract;
2. keep the agent loop independent from the product using it;
3. build the coding experience by composing tools and session services around that loop.

The implementation and package names are Python-native. Five mutation/search contracts follow pi closely, with a sixth shell-free `search` extension for safe file discovery.

## Dependency direction

```text
platforms / cli / benchmark
          ↓
coding_agent  ≈ pi-coding-agent
  ├─ assistant (composition root)
  ├─ tools / memory / goal
  ├─ instructions / approval / runtime
  ├──────────────→ agent  ≈ pi-agent-core
  └──────────────→ llm    ≈ pi-ai
                         ↑
agent ──────────────────┘

trace observes model and product boundaries without becoming agent policy
```

The three-package spine follows pi's real dependency direction: the provider
layer is reusable on its own, the generic agent core builds on it, and the
coding product builds on both. Tools are not a fourth peer layer; like pi's
`packages/coding-agent/src/core/tools`, they are concrete product components
inside `coding_agent`.

In particular:

- `llm` must not import `agent` or `coding_agent`;
- `agent` must not import `coding_agent`, concrete tools, or session storage;
- `agent` sees only the structural `AgentToolExecutor`/`AgentToolResult` protocol;
- `coding_agent.tools` must not decide whether a user task is complete;
- `goal` supervises complete product runs, never individual AgentLoop turns;
- `trace` records sanitized observations and must not change execution decisions;
- `approval` classifies dangerous operations and decides whether execution may cross the tool boundary;
- `runtime` owns workspace mapping and command execution but knows nothing about models or goals;
- `coding_agent.assistant` is the composition root where concrete product services are assembled and injected into `AgentLoop`.

## Repository layout

The current physical tree is documented in [docs/PROJECT_TREE.md](docs/PROJECT_TREE.md). The dependency graph above describes package boundaries rather than file placement.

- `src/MiniClaw/`: `llm`, `agent`, `coding_agent`, `platforms`, `trace`, `evaluation`, and `benchmark`.
- `docs/notes`, `docs/interview`, `docs/plans`: technical notes, interview material, and proposed work; see the [document index](docs/README.md).
- `scripts/launch`, `scripts/development`, `scripts/benchmarks`: operational helpers; see [script usage](scripts/README.md).
- `tests/` and `evals/`: regression tests and evaluation definitions, fixtures, baselines, and fixed splits.
- `frontend/architecture/`, `docker/runtime/`, and `external/benchmarks/`: visualization assets, runtime image, and third-party benchmark resources.
- `.aster/`, `benchmark-results/`, and `.codex-research/`: local runtime, experiment, and research artifacts.

## Packages

### `llm`

Owns provider-neutral messages, model metadata, normalized usage, tool calls, and model events. `OpenAICompatibleClient` is the first concrete model client.

`OpenAICompatibleClient` uses native SSE. Text deltas are emitted immediately while Tool Call names, IDs, and JSON arguments are assembled by call index. The transport has separate connect, first-token, idle, and total deadlines; a cooperative cancellation token closes active reads. Retryable pre-output failures use bounded exponential backoff, then explicit provider/model fallback routes. Once visible output is emitted, the request fails rather than replaying duplicate deltas.

### `agent`

Owns `AgentLoop` and `AgentEvent`. A run is:

```text
append user message
→ request model response
→ append assistant message
→ execute every requested tool
→ append tool results in source order
→ request the model again
→ finish when the model returns no tool calls
```

The agent loop has a turn limit, a context-transform hook, and a dynamic system-prompt provider, but it contains no built-in memory, compaction, project-instruction, goal, or platform policy.

### `coding_agent.tools`

Owns role-aware tool registration and the execution boundary through `ToolManager`:

```text
lookup
→ argument preparation
→ recursive JSON-schema validation
→ project-instruction and approval preflights
→ optional executor timeout
→ exception normalization
→ final safety-net output limit
```

`WorkspaceGuard` prevents file tools from resolving paths outside one repository, follows existing path components to stop symlink escape, and applies dynamic `read/write/search/execute` policy on every access. Newly created `.env`, credential and internal `.aster` paths are protected without restarting the Runtime; Artifact outputs are exposed read-only, while `.git` writes remain Bash-only. Docker recomputes sensitive mask mounts for every command. Each tool owns its pi-style, purpose-specific output policy: `read` keeps the head, `bash` keeps the tail and saves complete oversized output, and `grep` caps matches, bytes, and individual line length. `bash` delegates command execution to the session Runtime rather than choosing host or Docker itself.

`ToolManager` keeps global and role-scoped tools in one session registry. Product entry points may inject additional tools for a named role, apply allow/deny policies, disable tools dynamically, or unregister them. Only tools visible to the active role are included in model definitions and the generated system prompt.

### `coding_agent.runtime`

Owns the effective task workspace and command execution backend:

```text
source workspace
   ├─ direct   → effective workspace is the source
   └─ snapshot → safe one-time seed under the session sandbox directory

effective workspace
   ├─ file tools → guarded host-side operations
   └─ bash       → host shell or one ephemeral Docker container
```

The default backend is Docker with a Direct workspace. Host remains an explicit development fallback. Docker requires an available image, mounts only the effective workspace, dynamically masks sensitive paths in Direct mode, passes a fixed non-secret environment, and applies read-only-root, capability, network, CPU, memory, PID, tmpfs, file-descriptor, timeout, and cleanup limits. Runtime configuration and selected workspace metadata are included in unified Trace runs.

### `coding_agent.instructions`

Owns layered, scoped repository guidance without moving coding policy into `AgentLoop`:

```text
MiniClaw user-global files (`~/.miniclaw`)
→ effective workspace root
→ activated ancestor directories
→ nearest target directory
→ current user request (kept as a user message)
```

Each directory contributes at most one first-match file from `AGENTS.md`, `AGENTS.MD`, `CLAUDE.md`, or `CLAUDE.MD`. Sources retain their path, scope, level, depth, content hash, file signature, and estimated Token usage. Budget allocation runs from the most local source backward, while rendering preserves low-to-high priority order. A stat-keyed cache reloads changed files. The loader always reads from `runtime.host_workspace`, so Direct and Snapshot tasks obey the instructions that exist in the workspace they can actually modify.

`read` and `grep` activate target scopes for the following model request. `write` and `edit` have a fail-before-mutation preflight: when a relevant source is new or changed since the current system prompt was produced, the Tool Call returns `instructions_refresh_required`; the next turn receives the refreshed prompt and may retry. `instructions.injected` Trace events record metadata and budget decisions without duplicating full instruction contents.

### `coding_agent.assistant`

Assembles the coding product:

- standard coding tools;
- coding system prompt;
- model profile;
- four-layer memory services;
- recovered and compacted conversation state.
- persistent Goal state, Goal tools, and the outer supervision loop.
- unified Trace recording around model, tool, Goal, and Compaction boundaries.
- one shared approval gate for CLI, Feishu, and future product entry points.
- scoped project-instruction resolution and dynamic prompt refresh.

### `coding_agent.approval`

Owns the dangerous-operation boundary without adding policy to `AgentLoop`:

```text
validated ToolInvocation
  → classify shell/file risk
  → project allowlist
  → allow / ask / deny policy
  → CLI or Feishu decision with timeout
  → execute tool or return a structured denial
```

The default policy is `ask`. Read-only operations, normal source edits, and memory operations do not pause. Destructive filesystem/Git/database operations, network or external writes, system changes, sensitive files, and full overwrite of an existing file are classified. Memory mutations are constrained by the semantic store's secret rejection, deduplication, and conflict-resolution rules rather than human approval. Approval requests and decisions are sanitized before entering Trace; the later `tool.call` event records whether execution actually succeeded.

### `coding_agent.goal`

Owns the long-running task state machine without changing `AgentLoop`:

```text
active → verifying → complete
   ├──────────────→ waiting_for_user → active
   ├──────────────→ failed
   └──────────────→ cancelled
```

One natural model stop is one Goal attempt. The outer `GoalSupervisor` starts another attempt while the durable state remains `active` or `verifying`. `goal_complete` is accepted only after a fresh successful `bash(goal_verification=true)` result and exact evidence for every acceptance criterion. Successful `edit` or `write` calls clear old verification evidence. An optional independent LLM judge fails closed and records decisions in `goal-judge.jsonl`.

### `coding_agent.memory`

Owns four lifecycles without adding product policy to `AgentLoop`:

- Working Context: append-only JSONL, artifacts, archive checkpoints, and model-summary fallback;
- Episodic Memory: one Markdown checkpoint per session;
- Semantic Memory: explicit stable facts with secret/transient/conflict rejection;
- Procedural Memory: skill catalog in the prompt, full resources loaded only on demand.

`memory(action="overview")` reports these four modules with counts and bounded samples;
`memory(action="inspect", module=...)` inspects one module. `preference`, `project`,
`environment`, and `fact` are Semantic categories, selected by `memory(action="read",
category=..., limit=...)`. Archive indexes, evidence ledgers, and conflict logs are
supporting records rather than extra core modules. An empty conflict log means no
registered pending conflicts, not a full consistency audit.

New preference writes require an exact explicit user quote and preserve that quote
instead of an assistant paraphrase. Automatic consolidation rejects assistant-only
assertions, memory/skill output echoes, and file-existence or completion snapshots;
those remain dated episode history. It compares against existing semantic memory,
normalizes presentation for conservative deduplication, and registers detected
conflicts without automatically replacing the existing value. Arbitrary natural
language contradictions and cross-language equivalence are not guaranteed to be
detected. Reviewed cleanup retains originals and a per-entry audit outside active
Semantic memory.

Each source retrieves independently, then cross-source RRF and final reranking combine lexical,
dense, exact-symbol, status, confidence, and query-sensitive freshness signals. Archive JSONL parsing,
BM25 state, embeddings, and FAISS HNSW indexes are persistent. Once an ANN scope is synchronized,
steady-state searches encode only the new query and search the on-disk index; the caller's document
scope remains a hard result boundary.

The ranked candidate list is not copied into the prompt wholesale. A diversity-aware allocator selects
at most twelve excerpts, gives each a minimum share, applies source-specific caps, and redistributes the
remaining 15k Token budget. Trace distinguishes pre-budget `ranked_count` from the exact model-visible
`rendered_count`, including per-excerpt truncation metadata. Retrieved history is wrapped as explicitly
untrusted user-level evidence; only product rules, project instructions, and Goal state occupy the system
message.

The evaluation profile uses an 80k soft trigger, 100k hard trigger, 30k post-compaction target, 20k recent-message budget, and 4.5k deterministic semantic budget. Reserve is derived from the model output limit and context window; smaller windows scale all watermarks down safely.

### `trace`

`TracingModelClient` wraps the provider-neutral model client, so ordinary Agent requests, Goal Judge requests, and model-based Compaction summaries use the same event path. `CodingAssistant` associates tool calls, Goal snapshots, Compaction outcomes, and run completion with the active run ID. Transport attempts, retries, cancellations, and fallback selections are separate `model.transport` events; only the final successful reply contributes usage and cost to the single logical `model.request`.

### End-to-end cancellation

One attempt-scoped `CancellationToken` is shared by AgentLoop, LLM requests, ToolManager, approval waits, Goal Judge, Compaction, and Host/Docker command execution. Host commands run in their own process group and cancellation kills the process tree. Docker cancellation removes the named container before reaping the local Docker CLI process. AgentLoop synthesizes cancelled Tool results for the active and all remaining calls, so no later call is executed and the persisted message history remains protocol-valid. Built-in write/edit use staged atomic replacement to prevent a cancelled background write from committing after the user has already stopped the task.

Trace records `run.cancelled`, cancelled model transport/request events, `tool.call status=cancelled`, a `cancelled` Goal terminal state, and `run.completed status=cancelled`. Dashboard failure/error rates exclude deliberate user cancellations.

Each session writes one append-only `trace.jsonl`. The trace stores sanitized replay context, normalized usage, configured cost estimates, latency, tool outcomes, and state snapshots. Analysis code can migrate legacy `tool-calls.jsonl`/`model-requests.jsonl`, create an Eval Case from a production failure, replay model boundaries without executing tools, cluster deterministic failure signatures, and generate JSON/Markdown/HTML dashboards.

The session directory also stores the latest `run-state.json`. Model waits, active Tool Calls, completed Tool Call IDs, finalization, cancellation, and failure are atomically persisted. On restart, a state owned by a dead process is marked `interrupted`; unmatched assistant Tool Calls receive synthetic interrupted results so the next model request remains protocol-valid and must inspect workspace state before retrying a side effect.

Feishu delivery passes through a persistent append-only outbox. Each progress, final, approval, cancellation, chunk, and artifact notification has an idempotency key and bounded exponential retry. Successful final text remains unchanged; generated artifacts are delivered as a separate user-visible notice.

## Current tool vocabulary

The public names and main argument contracts follow pi:

| MiniClaw | Responsibility |
|---|---|
| `read` | Read by `path`, `offset`, and `limit`; keep at most 2000 lines or 50KB from the head |
| `bash` | Run a workspace command through the selected host/Docker Runtime; keep the output tail and persist oversized captured output |
| `edit` | Apply one or more unique, non-overlapping `oldText`/`newText` replacements |
| `write` | Create or replace a UTF-8 file and create parent directories |
| `grep` | Search through ripgrep with regex/literal, glob, case, context, and match-limit options |
| `search` | List matching files/directories without granting shell execution; protected paths are excluded |

The coding product also registers memory, skill, `goal`, and `goal_complete` tools. Role policy may expose only the subset appropriate for the current assistant role.

## Deferred features

The following are intentionally not part of the first framework version:

- parallel tool execution;
- Slack adapter;
- full workspace integration replay and benchmark orchestration.

They should be introduced one at a time with an isolated regression test. This keeps the initial design small enough to understand and prevents product policy from leaking into the agent loop.
