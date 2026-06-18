# Testory 产品官网

独立营销站点，与 UAT 主应用、创始人控制面分离部署。

## 启动

```powershell
cd projects/testory-website
python app.py
```

或从 monorepo 根目录：`python -m website`

默认访问：**http://127.0.0.1:5200**

## 环境变量（写入 `.env`）

| 变量 | 说明 | 默认 |
|------|------|------|
| `WEBSITE_URL` | 对外官网根地址（客户端跳转支付） | `http://127.0.0.1:5200` |
| `WEBSITE_PORT` | 官网端口 | `5200` |
| `WEBSITE_CONTACT_EMAIL` | 联系邮箱 | `contact@hufirst.com` |
| `WEBSITE_CONTACT_PHONE` | 联系电话 | 空 |
| `PLATFORM_ADMIN_URL` | 创始人后台地址 | `http://127.0.0.1:5100` |
| `PLATFORM_ADMIN_SECRET` | 控制面密钥（pay_token、用户同步） | 随机 |

## 功能说明

### 一键下载

- 用户在官网点击「下载」→ 访问 `/download/latest` → 浏览器直接保存 `.exe` 安装包
- 管理员在创始人控制面 **安装包** 页上传文件，或填写外部 CDN 地址
- 安装包文件存储于 `projects/testory-platform-admin/data/release_files/`

### 官网支付（须软件内登录）

1. 用户在 Testory 客户端/团队服务器 **登录账号**
2. 用户菜单或「授权管理」→ **升级订阅（官网）**
3. 软件签发短时 `pay_token` 并打开 `WEBSITE_URL/pricing?pay_token=...`
4. 用户在官网选择方案并完成支付（演示环境为模拟支付，生产对接微信/支付宝）
5. 支付成功后获得 License Key，回到软件「授权管理」激活

### 创始人控制面 · 用户列表

- 路径：`/users`（产品用户）
- 客户端/服务器登录后通过 `POST /api/platform/users/sync` 自动同步

## 页面结构

- Hero · 产品能力 · 解决方案 · 定价 · **一键下载** · FAQ · 联系我们
- `/pricing` — 订阅与支付（需从软件携带 pay_token 进入）
- `/download/latest` — 最新版安装包直链
- 留言表单写入 `website/data/inquiries.jsonl`

## 生产部署

建议使用 gunicorn / waitress 或 Nginx 反代：

```bash
pip install waitress
waitress-serve --port=5200 website.app:app
```

绑定域名示例：`www.hufirst.com` → 5200 端口。

## 三项目生产部署（拆分后必读）

### ① URL 与监听地址（不要写死 127.0.0.1）

| 环境 | 变量 | 示例 |
|------|------|------|
| 本地开发 | `WEBSITE_URL` | `http://127.0.0.1:5200` |
| 本地开发 | `PLATFORM_ADMIN_URL`（主平台/官网服务端调用） | `http://127.0.0.1:5100` |
| 生产 · 公网 | `WEBSITE_URL` | `https://www.your.com` |
| 生产 · 公网 | 主平台对外地址（支付跳转、文档） | `https://app.your.com` 或桌面客户端无需公网 |
| 生产 · 内网/专线 | `PLATFORM_ADMIN_URL`（**仅服务端** urllib/requests 调用） | `http://10.0.0.12:5100` 或 `https://admin-internal.your.com` |

要点：

- `127.0.0.1` 只适用于本机联调；生产必须把 `PLATFORM_ADMIN_URL` 配成**官网/主平台服务器能访问到的地址**（内网 IP、内网域名或带 mTLS 的管理域）。
- 创始人后台 `platform_admin` 启动已默认 `host=0.0.0.0`（见 `platform_admin/app.py`），容器/VM 内可监听；**是否暴露公网**由 Nginx/安全组决定，不建议把管理面直接裸奔到互联网。
- 主平台桌面安装包**不需要**把官网/后台打进包内；用户机器上的 `.env` 可留空 `PLATFORM_ADMIN_URL`，仅在你启用「登录同步 / 官网支付 / License 上报」时才配置公网 `WEBSITE_URL`。

### ② 下载链路：禁止「双跳代理」，优先 302 / CDN

**错误做法（性能陷阱）**：官网 Flask 用 `requests.get(admin下载)` 把整包读进内存再 `send_file` 给用户 —— 同步阻塞、占双倍带宽。

**当前代码已避开的部分**：`website/app.py` 在无本地文件时会 **`redirect` 到** `{PLATFORM_ADMIN_URL}/api/public/download/latest`，浏览器直连后台，不是官网中转流。

**拆分后推荐顺序**：

1. **最佳**：控制面 releases 填 `download_url` 为 OSS/S3/CDN 直链（或预签名 URL）；`/api/public/latest-release` 返回该 URL；官网 `/download/latest` **302 到 CDN**，业务服务器不碰大文件。
2. **次优**：官网 302 到 `https://admin.your.com/api/public/download/latest`；Nginx 对后台 `location /api/public/download/` 配 **`X-Accel-Redirect`** / `alias` 指向 `release_files`，Flask 只返回头、不读 body。
3. **仅开发**：官网与后台同机共享 `data/release_files/` 直读磁盘（拆仓后应废弃）。

后台 `_serve_release_download` 已支持：本地文件 → `send_file`；否则 → `redirect(download_url)`。

### ③ CORS / Cookie：推荐「服务端 BFF」，少做浏览器跨域

| 调用方 | 目标 | 方式 | 是否要 CORS |
|--------|------|------|-------------|
| 浏览器 | 官网 `www` | 同源 | 否 |
| 官网 Flask | 后台 API（下单、校验 token） | **服务端** `platform_api_json` | 否 |
| 主平台 Flask | 后台 `/api/platform/users/sync` | **服务端** `platform_sync` | 否 |
| 浏览器 JS | 后台 `admin` API | 跨域 fetch | **要**（不推荐作为主路径） |

现状：

- 官网支付：`pay_token` 在 URL 带入，校验在官网服务端或后台 `/api/platform/pay-token/verify`；**不依赖** `.your.com` 跨子域 Cookie。
- 创始人后台 Session：仅 `admin` 域下 HTML 登录，与 `www` 无关。
- 主平台 `app.py` 已有 `FLASK_CORS_ORIGINS`；**后台 `platform_admin` 尚未加 CORS** —— 若未来官网前端直连后台 JSON API，需在后台加 `flask-cors` 且 `origins=[WEBSITE_URL]`；更推荐保持 **官网做 BFF**，浏览器只调 `www.your.com/api/checkout/*`。

若必须用 Cookie 跨子域：设 `SESSION_COOKIE_DOMAIN=.your.com` 且 HTTPS + `SameSite=None; Secure`；支付链路仍建议继续用 **HMAC pay_token / JWT Header**，与现有 `platform_pay_token.py` 一致。

