# MiniClaw Architecture Viewer

这是一个不依赖前端构建工具的静态 SVG 查看器。

## 启动

在项目根目录运行：

```powershell
python -m MiniClaw.frontend
```

打开：

```text
http://127.0.0.1:8765/
```

## 功能

- 鼠标滚轮缩放；
- 拖拽平移；
- 模块索引定位；
- 缩略图快速导航；
- 适应窗口与 100% 原始尺寸；
- 浏览器全屏；
- 下载独立 SVG；
- 键盘 `+`、`-`、`0`、`1` 和方向键控制。

## 本地烟雾测试

```powershell
pwsh -File scripts/development/test-architecture-frontend.ps1
```

核心图片是 `miniclaw-architecture.svg`，可以独立放进文档、幻灯片或面试材料。
