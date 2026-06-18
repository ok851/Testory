# 官网 + 创始人控制面 · 腾讯云轻量部署指南

适用于：**Ubuntu 22.04 LTS**，同机部署 `testory-website`（5200）与 `testory-platform-admin`（5100），前面加 Nginx + HTTPS。

主平台（桌面 Testory）**不部署在本机**；用户在本机安装后，可选配置 `WEBSITE_URL` / `PLATFORM_ADMIN_URL` 连接本服务。

---

## 0. 你需要提前准备

| 项目 | 示例 |
|------|------|
| 服务器 | 已 SSH 登录（root 或 ubuntu） |
| 域名 | `www.yourdomain.com`（官网）、`admin.yourdomain.com`（控制面，建议限制访问） |
| DNS | 两条 A 记录指向服务器公网 IP |
| 本地代码 | 已运行过 `.\scripts\export_split_projects.ps1 -Destination D:\TestoryRepos` |

---

## 1. 服务器基础环境

SSH 登录后执行：

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3 python3-pip python3-venv nginx certbot python3-certbot-nginx git rsync ufw
```

防火墙（先允许 SSH，再开 Web）：

```bash
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'
sudo ufw enable
sudo ufw status
```

创建部署用户（可选但推荐）：

```bash
sudo adduser --disabled-password --gecos "" testory
sudo usermod -aG sudo testory
# 将你的 SSH 公钥写入 /home/testory/.ssh/authorized_keys 后用 testory 登录
```

---

## 2. 上传代码到服务器

### 方式 A：从 Windows 导出目录 rsync/scp（推荐）

在 **Windows PowerShell**（替换 IP 与路径）：

```powershell
# 先在本地导出
cd D:\mkst_baixiang\Python_Code\NewUITestPlatform\NewUITestPlatform
.\scripts\export_split_projects.ps1 -Destination D:\TestoryRepos

# 上传到服务器（需安装 OpenSSH 客户端）
scp -r D:\TestoryRepos\testory-website D:\TestoryRepos\testory-platform-admin root@你的公网IP:/opt/
```

### 方式 B：Git（若代码已在远程仓库）

```bash
sudo mkdir -p /opt/testory
sudo chown $USER:$USER /opt/testory
cd /opt/testory
git clone <你的仓库地址> .
# 使用 projects/testory-website 与 projects/testory-platform-admin，并复制 packages/testory_common
```

上传后服务器目录应为：

```
/opt/testory-website/
  app.py  templates/  static/  testory_common/  requirements.txt
/opt/testory-platform-admin/
  app.py  admin_database.py  license_manager.py  templates/  testory_common/  data/
```

---

## 3. Python 虚拟环境与依赖

```bash
# 官网
cd /opt/testory-website
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
pip install waitress gunicorn
deactivate

# 控制面
cd /opt/testory-platform-admin
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
pip install waitress gunicorn
deactivate

# 数据目录
mkdir -p /opt/testory-platform-admin/data/release_files
mkdir -p /opt/testory-website/data
```

---

## 4. 配置 .env（生产）

生成随机密钥：

```bash
openssl rand -hex 32
# 记下输出，下面三处 PLATFORM_ADMIN_SECRET 必须相同
```

### `/opt/testory-platform-admin/.env`

```env
PLATFORM_ADMIN_PORT=5100
PLATFORM_ADMIN_USER=founder
PLATFORM_ADMIN_PASSWORD=请改为强密码
PLATFORM_ADMIN_SECRET=上一步生成的64位hex

WEBSITE_URL=https://www.yourdomain.com

# 若浏览器直链下载走 admin 域名（未用 CDN 时）
PLATFORM_ADMIN_PUBLIC_URL=https://admin.yourdomain.com
```

### `/opt/testory-website/.env`

```env
WEBSITE_PORT=5200
WEBSITE_URL=https://www.yourdomain.com
WEBSITE_SECRET=

# 同机部署：服务端调用控制面用回环地址
PLATFORM_ADMIN_URL=http://127.0.0.1:5100
PLATFORM_ADMIN_SECRET=与控制面完全相同的密钥

WEBSITE_CONTACT_EMAIL=contact@yourdomain.com
WEBSITE_CONTACT_PHONE=
```

权限：

```bash
chmod 600 /opt/testory-platform-admin/.env /opt/testory-website/.env
```

---

## 5. systemd 常驻服务

创建官网服务 `/etc/systemd/system/testory-website.service`：

```ini
[Unit]
Description=Testory Website
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/testory-website
EnvironmentFile=/opt/testory-website/.env
ExecStart=/opt/testory-website/.venv/bin/waitress-serve --listen=127.0.0.1:5200 app:app
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

创建控制面服务 `/etc/systemd/system/testory-admin.service`：

```ini
[Unit]
Description=Testory Platform Admin
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/testory-platform-admin
EnvironmentFile=/opt/testory-platform-admin/.env
ExecStart=/opt/testory-platform-admin/.venv/bin/waitress-serve --listen=127.0.0.1:5100 app:app
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

启用：

```bash
sudo systemctl daemon-reload
sudo systemctl enable testory-website testory-admin
sudo systemctl start testory-website testory-admin
sudo systemctl status testory-website testory-admin
```

本机自检：

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:5200/
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:5100/login
# 期望均为 200
```

---

## 6. Nginx 反向代理 + HTTPS

创建 `/etc/nginx/sites-available/testory`（替换域名）：

```nginx
# 官网
server {
    listen 80;
    server_name www.yourdomain.com yourdomain.com;

    location / {
        proxy_pass http://127.0.0.1:5200;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}

# 创始人控制面（建议仅自己 IP 可访问，见下方 allow/deny）
server {
    listen 80;
    server_name admin.yourdomain.com;

    # 取消注释并改成你的办公/家庭公网 IP
    # allow 1.2.3.4;
    # deny all;

    location / {
        proxy_pass http://127.0.0.1:5100;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        client_max_body_size 500m;
    }
}
```

启用站点：

```bash
sudo ln -sf /etc/nginx/sites-available/testory /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
```

申请证书（DNS 已生效后）：

```bash
sudo certbot --nginx -d www.yourdomain.com -d yourdomain.com -d admin.yourdomain.com
```

证书会自动改 Nginx 为 443；之后把 `.env` 里的 URL 全部改为 `https://`。

---

## 7. 安装包与下载

1. 浏览器访问 `https://admin.yourdomain.com`，用 `.env` 中的 founder 账号登录。
2. 进入 **安装包** 页，上传 `.exe` 或填写 **CDN/OSS 直链**（推荐）。
3. 官网「下载」→ `/download/latest` → 302 到控制面或 CDN。

**推荐**：腾讯云 COS + CDN，`download_url` 填 CDN 地址，轻量机不承担大文件流量。

---

## 8. 桌面客户端连接（用户侧）

用户 `%LOCALAPPDATA%\Testory\.env` 或主平台 `.env` 可选配置：

```env
WEBSITE_URL=https://www.yourdomain.com
PLATFORM_ADMIN_URL=https://admin.yourdomain.com
PLATFORM_ADMIN_SECRET=与服务器相同的密钥
```

用于：官网支付跳转、License 激活上报、登录用户同步到控制面。

---

## 9. 验收清单

| 步骤 | 命令/操作 | 期望 |
|------|-----------|------|
| 服务 | `systemctl status testory-website testory-admin` | active |
| 官网 | 浏览器打开 `https://www.yourdomain.com` | 首页正常 |
| 控制面 | `https://admin.yourdomain.com/login` | 登录页 |
| 下载 | 官网点下载 | 开始下载或 302 到 CDN |
| License | 控制面签发 → 桌面激活 | 付费功能生效 |
| 密钥 | 三处 `PLATFORM_ADMIN_SECRET` 一致 | 支付/同步不报 unauthorized |

---

## 10. 更新发布

```bash
# 本机重新导出并 scp 覆盖后：
sudo systemctl restart testory-website testory-admin
```

数据库与上传文件在 `/opt/testory-platform-admin/data/`，更新代码时不要删此目录。

---

## 11. 常见问题

| 现象 | 处理 |
|------|------|
| 502 Bad Gateway | `systemctl status testory-*` 看是否启动；`ss -tlnp \| grep 5200` |
| 官网支付失败 / unauthorized | 检查两边 `PLATFORM_ADMIN_SECRET` 是否一致 |
| 控制面看不到激活记录 | 客户端 `PLATFORM_ADMIN_URL` 是否可达；防火墙是否拦 443 |
| certbot 失败 | DNS 是否已指向本机；80 端口是否开放 |

模板文件见 `scripts/cloud/`。
