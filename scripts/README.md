# MiniClaw 辅助脚本

以下命令从项目根目录执行。Python 脚本运行前先执行 `python -m pip install -e .`。运行真实模型需要本地配置。

| 分类 | 脚本 | 用途 |
|---|---|---|
| 启动 | [launch/start-miniclaw-feishu.ps1](launch/start-miniclaw-feishu.ps1) | 启动飞书入口 |
| 开发检查 | [development/test-architecture-frontend.ps1](development/test-architecture-frontend.ps1) | 启动本地架构页面服务并检查静态资源 |

```powershell
pwsh -File scripts/launch/start-miniclaw-feishu.ps1
pwsh -File scripts/development/test-architecture-frontend.ps1
```

脚本依据自身位置定位项目根目录。分类后原来的 `scripts/<文件名>` 路径已迁移到上表位置；输出目录约定保持不变。
