# Eval 路径误报修复

**问题：**旧检查直接在 Windows 上解析 `/workspace/output.json`，把 Docker 中的合法输出误当成工作区外文件。

**修改：**新增通用 `tool_write_paths` 检查，使用 Runtime 的 WorkspaceGuard 统一映射。每次审计保留模型原始路径、Docker 执行路径、宿主机路径和工作区相对路径，按规范化后的目标判断，缺失 Trace 或成功写入缺少路径时不判通过。

**验证：**相对路径、Docker 路径、合法宿主绝对路径均通过，包括 Windows 大小写变体；越界、兄弟目录、额外文件均拒绝。用真实 Runtime 产生写入 Trace，并实际通过 Docker 挂载读取 Windows 文件验证；额外文件写入后再删除，仍被 Trace 检查识别。相关自动测试共 43 项，42 项通过，1 项因 Windows 无符号链接创建权限跳过；Docker 实测另外通过，无模型 API 调用。

**成绩保留：**原始 7/30 不动；仅替换无效路径检查，修订为 11/30。原始成绩、复核标签、修订成绩分为三个字段，原报告及冻结输入前后哈希一致。4 条纯误报隔离，真实额外文件写入与对账遗漏仍失败。新版本不是新的模型测试成绩。

- 新主入口：`evals/active-eval-portfolio-v4.json`。
- 修订结果：`evals/evidence/path-oracle-v2.1/regrade.json`；前一版修订及 oracle 源码保留在 `path-oracle-v2/`。
- 修订失败集：`evals/hard-observed-failures-v2.json`，19 条。
- Windows + Docker 证据：`.aster/evals/path-oracle-v2/docker-validation.json`。
- 回归命令：`python -m unittest tests.test_eval_path_audit tests.test_judge_calibration tests.test_evaluation tests.test_code_compression_benchmark tests.test_trace -q`。
- Docker 验证：`python scripts/verify_eval_paths_docker.py`。
- 评分复核：`python scripts/regrade_hard_paths_v2.py`，相同证据可重复验证，不允许覆盖不同内容的同版修订。

本次修的是阅卷程序的路径判定。成功 edit/write 的目标可以追溯，任意 bash 的瞬时副作用和“只读→授权写入”的任务权限策略不在这项修复内。
