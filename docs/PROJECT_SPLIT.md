# 三项目拆分说明

本 monorepo 含三个可独立部署的应用：

| 项目 | 路径 | 端口 | 说明 |
|------|------|------|------|
| **主平台 Testory** | 仓库根目录 `app.py` | 5000 / Tauri 动态 | 桌面 + 团队服务器 + 自动化 |
| **产品官网** | `projects/testory-website/` | 5200 | 营销站、官网支付 BFF |
| **创始人控制面** | `projects/testory-platform-admin/` | 5100 | License、安装包、订单 |

共享库：`packages/testory_common/`（品牌、pay_token、HTTP 客户端）。

## 环境变量（.env）归属

**代码已拆分，配置在 monorepo 里仍可按两种方式使用：**

| 方式 | 说明 |
|------|------|
| **分项目配置（推荐）** | 各项目目录下复制 `.env.example` → `.env`，只放本项目需要的变量 |
| **根目录 `.env`（兼容）** | 官网/控制面启动时会**先读根目录 `.env`，再用本项目 `.env` 覆盖** |

| 项目 | 配置文件位置 | 主要变量 |
|------|----------------|----------|
| 主平台 | 仓库根 `.env` | `FLASK_*`、`UAT_DATA_DIR`、自动化/AI 等 |
| 官网 | `projects/testory-website/.env` | `WEBSITE_*`、`PLATFORM_ADMIN_URL`（调用控制面） |
| 控制面 | `projects/testory-platform-admin/.env` | `PLATFORM_ADMIN_USER/PASSWORD/SECRET`、`WEBSITE_URL` |

**管理后台登录账号**：看控制面 `.env` 的 `PLATFORM_ADMIN_USER` / `PLATFORM_ADMIN_PASSWORD`（未设置密码时看启动终端随机打印）。  
导出为三个 Git 仓库后，**各自只有自己的 `.env`**，不再共享根目录文件。

## 启动

```powershell
# 主平台
python app.py

# 官网
python projects/testory-website/app.py
# 或 python -m website

# 控制面
python projects/testory-platform-admin/app.py
# 或 python -m platform_admin
```

## 生产环境 URL

- `127.0.0.1` 仅用于本机联调。
- 官网/主平台的 `PLATFORM_ADMIN_URL` 必须是**服务器间可访问**的内网或管理域地址。
- 控制面进程默认 `host=0.0.0.0`；是否公网暴露由 Nginx/防火墙决定。

## 下载链路

- 官网 `/download/latest` → **302** → 控制面 `/api/public/download/latest` 或 CDN `download_url`。
- 禁止官网 Flask 代理整包文件流。

## 怎么拆成三个独立文件夹

### 方式 A：继续用 monorepo（代码已拆好，不必再动目录）

三个应用已在不同路径，共用一份根 `.env` 即可本地联调：

| 应用 | 目录 |
|------|------|
| 主平台 | 仓库根 `app.py` |
| 官网 | `projects/testory-website/` |
| 控制面 | `projects/testory-platform-admin/` |

若希望配置也分开：各项目复制 `.env.example` → `.env`，从根 `.env` 抄对应段落即可。

### 方式 B：导出到三个 sibling 文件夹（推荐上生产 / 三个 Git 仓库）

在 monorepo 根目录执行：

```powershell
cd D:\mkst_baixiang\Python_Code\NewUITestPlatform\NewUITestPlatform
.\scripts\export_split_projects.ps1 -Destination D:\TestoryRepos
```

可选 `-InitGit` 在每个导出目录执行 `git init`：

```powershell
.\scripts\export_split_projects.ps1 -Destination D:\TestoryRepos -InitGit
```

会生成：

| 文件夹 | 内容 |
|--------|------|
| `testory/` | 主平台（不含 `projects/`、`website/`、`platform_admin/`） |
| `testory-website/` | 官网 + 内嵌 `testory_common/` + 自动从 `.env.example` 生成 `.env` |
| `testory-platform-admin/` | 控制面 + `testory_common/` + `.env` |

**导出后必做：**

1. **三个 `.env` 里 `PLATFORM_ADMIN_SECRET` 必须相同**（官网、控制面、主平台 `platform_sync` 共用）。
2. 主平台 `.env` 需从 monorepo 根 `.env` 手动迁移（导出脚本**不复制**根 `.env`，避免把官网/控制面变量打进安装包）。
3. 各目录 `pip install -r requirements.txt`，再分别启动。

```powershell
# 终端 1 — 控制面
cd D:\TestoryRepos\testory-platform-admin
python app.py                    # :5100

# 终端 2 — 官网
cd D:\TestoryRepos\testory-website
python app.py                    # :5200

# 终端 3 — 主平台
cd D:\TestoryRepos\testory
python app.py                    # :5000
```

### 方式 C：手动搬目录（不推荐）

若不用脚本，需自行：

- 复制 `projects/testory-website` → 新仓库，并复制 `packages/testory_common` 到项目根 `testory_common/`
- 复制 `projects/testory-platform-admin` 同上，并保留 `admin_database.py`、`license_manager.py`
- 主平台复制 monorepo 根，**删除** `projects/`、`website/`、`platform_admin/`，保留 `packages/testory_common`

`testory_common` 变更后需同步到三个仓库（或日后抽成私有 pip 包）。

## 安装包

PyInstaller / Tauri 打包**不包含** `projects/testory-website`、`projects/testory-platform-admin`。

主平台通过 `platform_sync.py`（HTTP）可选连接控制面；未配置 URL 时本地功能不受影响。
