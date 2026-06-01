# Testory 产品官网

独立营销站点，与 UAT 主应用、创始人控制面分离部署。

## 启动

```powershell
cd 项目根目录
python -m website.app
```

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
- 安装包文件存储于 `data/release_files/`

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
