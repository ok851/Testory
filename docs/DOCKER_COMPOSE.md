# Docker Compose 部署说明

## 设计原则

| 场景 | 命令 | 说明 |
|------|------|------|
| **默认 Web 仅** | `docker compose up -d uat-platform` | 不启动 OpenClaw / embedded 网关 |
| **+ 内嵌浏览器网关** | `docker compose --profile with-embedded-browser up -d` | AI 测试 CDP / 画布 |
| **+ OpenClaw** | `docker compose --profile with-openclaw up -d` | AI 优化 `openclaw_execute` |
| **全栈 AI** | `docker compose --profile full-stack up -d` | 平台 + 两个网关 |
| **+ Nginx** | 追加 `--profile with-nginx` | 反向代理 |
| **+ PostgreSQL** | 追加 `--profile with-postgres` | 替代 SQLite |

**桌面混排用例**（pywinauto）不能在 Linux 容器内执行，请使用 Windows 本机套件（`packaging/run_uat_local.ps1`）。

## 环境变量

### 仅 Web（默认）

`uat-platform` 中 `EMBEDDED_BROWSER_GATEWAY_URL` 与 `OPENCLAW_GATEWAY_URL` 默认为空。未启用对应 profile 时，平台不会连接这些服务。

### 启用网关后

复制 `docker-compose.full-stack.env.example` 中的键到根目录 `.env`，或执行：

```bash
docker compose --profile full-stack --env-file docker-compose.full-stack.env.example up -d
```

宿主机浏览器访问画布 WebSocket 时，`EMBEDDED_BROWSER_PUBLIC_WS_BASE` 通常仍为 `ws://127.0.0.1:8765`（端口已映射）。

## 与本地版对照

| 能力 | Docker `uat-platform` | Windows 本地版 |
|------|----------------------|----------------|
| Web 步骤 | ✅ 无头 Chromium | ✅ 可有界面 |
| 桌面步骤 | ❌ | ✅ inprocess |
| OpenClaw | 可选 profile | 本机进程 / 可选容器 |
| embedded_browser | 可选 profile | `python -m embedded_browser_gateway` |

架构图：`docs/assets/architecture_local_16x9.svg` / `.png`
