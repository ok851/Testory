# Docker Compose 部署说明

## 常用命令

| 场景 | 命令 | 说明 |
|------|------|------|
| **默认 Web 仅** | `docker compose up -d uat-platform` | 不启动 Browser Runtime / 宿主机 Hermes |
| **+ Browser Runtime** | `docker compose --profile with-embedded-browser up -d` | AI 测试画布 / run-steps |
| **全栈** | `docker compose --profile full-stack up -d` | 平台 + Browser Runtime |
| **+ Nginx** | `docker compose --profile with-nginx up -d` | 反向代理 |
| **+ PostgreSQL** | `docker compose --profile with-postgres up -d` | 外置数据库 |

环境变量模板：`docker-compose.full-stack.env.example`

## 组件对照

| 组件 | Docker | 本机 Windows 离线包 |
|------|--------|---------------------|
| Flask 平台 | `uat-platform` | `TestoryBackend.exe` |
| Browser Runtime | profile `with-embedded-browser` | `TestoryBrowserRuntime.exe`（自动 bootstrap） |
| Testory AI (Hermes) | 指向 `host.docker.internal:8642` | `TestoryHermesGw.exe`（自动 bootstrap） |
| OpenClaw | **已移除** | **已移除** |

Hermes 在 Docker 场景下通常由**宿主机**离线安装包提供 API Server；容器内设置 `HERMES_GATEWAY_URL=http://host.docker.internal:8642`。

详见架构图 [architecture_local_16x9.png](assets/architecture_local_16x9.png)。
