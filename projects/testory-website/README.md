# Testory 产品官网（独立项目）

默认端口 **5200**。通过 HTTP 调用创始人控制面，不直接读后台数据库或安装包目录。

## 启动

```powershell
cd projects/testory-website
# 推荐在 monorepo 根目录配置 .env（PLATFORM_ADMIN_URL、WEBSITE_URL 等）
python app.py
```

或从仓库根目录：

```powershell
python -m website
```

## 环境变量

| 变量 | 说明 |
|------|------|
| `WEBSITE_URL` | 对外官网地址（生产 `https://www.example.com`） |
| `PLATFORM_ADMIN_URL` | **服务端**访问控制面的地址（生产用内网 IP/域名，非 127.0.0.1） |
| `PLATFORM_ADMIN_SECRET` | 与主平台、控制面一致的密钥 |

## 下载

`/download/latest` 仅 **302 重定向** 到控制面 `/api/public/download/latest` 或 releases 中的 CDN URL，不代理大文件。

## 导出为独立 Git 仓库

复制本目录 + `packages/testory_common/` + 根目录 `.env.example` 即可；详见 `docs/PROJECT_SPLIT.md`。
