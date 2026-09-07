(function () {
  "use strict";

  const SVG_WIDTH = 3200;
  const SVG_HEIGHT = 2400;
  const stage = document.getElementById("image-stage");
  const image = document.getElementById("architecture-image");
  const zoomRange = document.getElementById("zoom-range");
  const zoomLabel = document.getElementById("zoom-label");
  const positionStatus = document.getElementById("position-status");
  const regionTitle = document.getElementById("region-title");
  const regionDescription = document.getElementById("region-description");
  const minimap = document.getElementById("minimap");
  const minimapWindow = document.getElementById("minimap-window");
  const stageHint = document.getElementById("stage-hint");

  const regions = {
    overview: {
      x: 0, y: 0, width: 3200, height: 2400,
      title: "全局总览",
      description: "完整系统、控制流、数据流、安全边界与持久化关系"
    },
    platforms: {
      x: 55, y: 205, width: 590, height: 830,
      title: "入口与平台",
      description: "CLI、飞书长连接、审批回复、去重、队列、会话路由与响应更新"
    },
    assistant: {
      x: 645, y: 205, width: 690, height: 1030,
      title: "Assistant 组装",
      description: "CodingAssistant 组合模型、审批、工具、记忆、Goal、Trace 与 Runtime"
    },
    "agent-llm": {
      x: 1325, y: 205, width: 835, height: 1030,
      title: "Agent 与 LLM",
      description: "原生 SSE 增量、Tool Call 拼接、四类超时、Cancellation Token、重试与显式降级"
    },
    "tools-runtime": {
      x: 2140, y: 205, width: 1010, height: 1400,
      title: "Tools 与 Runtime",
      description: "危险操作审批、五工具、取消竞速、原子文件提交、Host 进程树与 Docker 容器回收"
    },
    memory: {
      x: 55, y: 1190, width: 1060, height: 1130,
      title: "四层记忆",
      description: "Working、Episodic、Semantic、Procedural 与混合检索、压缩"
    },
    goal: {
      x: 1090, y: 1190, width: 760, height: 1130,
      title: "Goal 监督",
      description: "持久化状态机、外层尝试循环、cancelled 终态、验证证据与可取消 Judge"
    },
    "trace-eval": {
      x: 1815, y: 1540, width: 1335, height: 780,
      title: "Trace → Eval",
      description: "统一 Trace、模型传输尝试、失败 Eval Case、边界重放、聚类与回归告警"
    },
    storage: {
      x: 55, y: 2050, width: 3095, height: 300,
      title: "持久化结构",
      description: ".aster 下的会话、记忆、Goal、Trace、Artifact 与 Sandbox 文件"
    }
  };

  let scale = 1;
  let translateX = 0;
  let translateY = 0;
  let dragging = false;
  let dragStartX = 0;
  let dragStartY = 0;
  let dragOriginX = 0;
  let dragOriginY = 0;
  let activeRegion = "overview";

  function clampScale(value) {
    return Math.min(2.4, Math.max(0.1, value));
  }

  function applyTransform() {
    image.style.transform = `translate(${translateX}px, ${translateY}px) scale(${scale})`;
    const percent = Math.round(scale * 100);
    zoomRange.value = String(Math.min(240, Math.max(10, percent)));
    zoomLabel.textContent = `${percent}%`;
    positionStatus.textContent = `x ${Math.round(-translateX / scale)} · y ${Math.round(-translateY / scale)}`;
    updateMinimap();
  }

  function fitRegion(name, padding) {
    const region = regions[name];
    if (!region) return;
    activeRegion = name;
    const inset = padding === undefined ? 34 : padding;
    const availableWidth = Math.max(120, stage.clientWidth - inset * 2);
    const availableHeight = Math.max(120, stage.clientHeight - inset * 2);
    scale = clampScale(Math.min(availableWidth / region.width, availableHeight / region.height));
    translateX = (stage.clientWidth - region.width * scale) / 2 - region.x * scale;
    translateY = (stage.clientHeight - region.height * scale) / 2 - region.y * scale;
    updateRegionUi(name);
    applyTransform();
  }

  function updateRegionUi(name) {
    const region = regions[name];
    regionTitle.textContent = region.title;
    regionDescription.textContent = region.description;
    document.querySelectorAll("#module-navigation button").forEach((button) => {
      button.classList.toggle("active", button.dataset.region === name);
    });
    if (history.replaceState) {
      history.replaceState(null, "", name === "overview" ? "#overview" : `#${name}`);
    }
  }

  function zoomAt(nextScale, centerX, centerY) {
    const bounded = clampScale(nextScale);
    const localX = (centerX - translateX) / scale;
    const localY = (centerY - translateY) / scale;
    scale = bounded;
    translateX = centerX - localX * scale;
    translateY = centerY - localY * scale;
    activeRegion = "overview";
    updateRegionUi("overview");
    applyTransform();
  }

  function updateMinimap() {
    const mapWidth = minimap.clientWidth;
    const mapHeight = minimap.clientHeight;
    if (!mapWidth || !mapHeight || !stage.clientWidth || !stage.clientHeight) return;
    const visibleX = Math.max(0, -translateX / scale);
    const visibleY = Math.max(0, -translateY / scale);
    const visibleWidth = Math.min(SVG_WIDTH, stage.clientWidth / scale);
    const visibleHeight = Math.min(SVG_HEIGHT, stage.clientHeight / scale);
    minimapWindow.style.left = `${(visibleX / SVG_WIDTH) * mapWidth}px`;
    minimapWindow.style.top = `${(visibleY / SVG_HEIGHT) * mapHeight}px`;
    minimapWindow.style.width = `${Math.max(5, (visibleWidth / SVG_WIDTH) * mapWidth)}px`;
    minimapWindow.style.height = `${Math.max(5, (visibleHeight / SVG_HEIGHT) * mapHeight)}px`;
  }

  function moveBy(deltaX, deltaY) {
    translateX += deltaX;
    translateY += deltaY;
    applyTransform();
  }

  document.getElementById("zoom-in").addEventListener("click", () => {
    zoomAt(scale * 1.18, stage.clientWidth / 2, stage.clientHeight / 2);
  });

  document.getElementById("zoom-out").addEventListener("click", () => {
    zoomAt(scale / 1.18, stage.clientWidth / 2, stage.clientHeight / 2);
  });

  document.getElementById("fit-view").addEventListener("click", () => fitRegion("overview", 20));
  document.getElementById("actual-size").addEventListener("click", () => {
    scale = 1;
    translateX = 24;
    translateY = 24;
    updateRegionUi("overview");
    applyTransform();
  });

  document.getElementById("fullscreen").addEventListener("click", async () => {
    if (!document.fullscreenElement) {
      await document.documentElement.requestFullscreen();
    } else {
      await document.exitFullscreen();
    }
    window.setTimeout(() => fitRegion(activeRegion, 30), 120);
  });

  zoomRange.addEventListener("input", () => {
    zoomAt(Number(zoomRange.value) / 100, stage.clientWidth / 2, stage.clientHeight / 2);
  });

  document.getElementById("module-navigation").addEventListener("click", (event) => {
    const button = event.target.closest("button[data-region]");
    if (button) fitRegion(button.dataset.region, 38);
  });

  stage.addEventListener("wheel", (event) => {
    event.preventDefault();
    const rect = stage.getBoundingClientRect();
    const factor = event.deltaY < 0 ? 1.12 : 1 / 1.12;
    zoomAt(scale * factor, event.clientX - rect.left, event.clientY - rect.top);
    stageHint.classList.add("hidden");
  }, { passive: false });

  stage.addEventListener("pointerdown", (event) => {
    if (event.button !== 0) return;
    dragging = true;
    dragStartX = event.clientX;
    dragStartY = event.clientY;
    dragOriginX = translateX;
    dragOriginY = translateY;
    stage.classList.add("dragging");
    stage.setPointerCapture(event.pointerId);
    stageHint.classList.add("hidden");
  });

  stage.addEventListener("pointermove", (event) => {
    if (!dragging) return;
    translateX = dragOriginX + event.clientX - dragStartX;
    translateY = dragOriginY + event.clientY - dragStartY;
    applyTransform();
  });

  function stopDragging(event) {
    if (!dragging) return;
    dragging = false;
    stage.classList.remove("dragging");
    if (event.pointerId !== undefined && stage.hasPointerCapture(event.pointerId)) {
      stage.releasePointerCapture(event.pointerId);
    }
  }

  stage.addEventListener("pointerup", stopDragging);
  stage.addEventListener("pointercancel", stopDragging);

  minimap.addEventListener("click", (event) => {
    const rect = minimap.getBoundingClientRect();
    const targetX = ((event.clientX - rect.left) / rect.width) * SVG_WIDTH;
    const targetY = ((event.clientY - rect.top) / rect.height) * SVG_HEIGHT;
    translateX = stage.clientWidth / 2 - targetX * scale;
    translateY = stage.clientHeight / 2 - targetY * scale;
    applyTransform();
  });
  minimap.addEventListener("pointerdown", (event) => event.stopPropagation());

  window.addEventListener("keydown", (event) => {
    const target = event.target;
    if (target instanceof HTMLInputElement) return;
    if (event.key === "+" || event.key === "=") {
      zoomAt(scale * 1.18, stage.clientWidth / 2, stage.clientHeight / 2);
    } else if (event.key === "-") {
      zoomAt(scale / 1.18, stage.clientWidth / 2, stage.clientHeight / 2);
    } else if (event.key === "0") {
      fitRegion("overview", 20);
    } else if (event.key === "1") {
      scale = 1;
      translateX = 24;
      translateY = 24;
      updateRegionUi("overview");
      applyTransform();
    } else if (event.key === "ArrowLeft") {
      moveBy(48, 0);
    } else if (event.key === "ArrowRight") {
      moveBy(-48, 0);
    } else if (event.key === "ArrowUp") {
      moveBy(0, 48);
    } else if (event.key === "ArrowDown") {
      moveBy(0, -48);
    } else {
      return;
    }
    event.preventDefault();
  });

  const resizeObserver = new ResizeObserver(() => {
    if (activeRegion) fitRegion(activeRegion, 30);
  });
  resizeObserver.observe(stage);

  image.addEventListener("load", () => {
    const hashRegion = window.location.hash.replace("#", "");
    fitRegion(regions[hashRegion] ? hashRegion : "overview", 20);
  });
  if (image.complete) {
    window.requestAnimationFrame(() => {
      const hashRegion = window.location.hash.replace("#", "");
      fitRegion(regions[hashRegion] ? hashRegion : "overview", 20);
    });
  }
})();
