# Testory 创始人控制面（独立项目）

License 签发、安装包发布、订单与用户同步。默认端口 **5100**，监听 `0.0.0.0`。

## 启动

```powershell
cd projects/testory-platform-admin
python app.py
```

或：

```powershell
python -m platform_admin
```

## 数据目录

| 路径 | 内容 |
|------|------|
| `data/platform_admin.db` | 控制面 SQLite |
| `data/release_files/` | 上传的安装包 |

## 环境变量

| 变量 | 说明 |
|------|------|
| `PLATFORM_ADMIN_URL` | 本服务对外/内网基址（供 API 返回下载链接） |
| `PLATFORM_ADMIN_PUBLIC_URL` | 可选，浏览器直链下载用公网/内网 URL |
| `WEBSITE_URL` | 官网根地址（releases 页展示） |
| `PLATFORM_ADMIN_SECRET` | Session、pay_token、用户同步密钥 |

## 导出为独立 Git 仓库

复制本目录 + `packages/testory_common/` + 本目录内 `license_manager.py`；详见 `docs/PROJECT_SPLIT.md`。
