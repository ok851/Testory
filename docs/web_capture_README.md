# 网页元素捕获与自动化

## 模式

| 模式 | 说明 |
|------|------|
| **cdp**（默认） | 平台启动带 `--remote-debugging-port` 的 Chrome/Edge，注入拾取脚本，无需商店扩展 |
| **extension** | 加载 `browser_extension/chrome`，经 WebSocket `127.0.0.1:19222` 与平台通信 |
| **legacy_inject** | 旧版工作区代理 + 书签（不推荐作为主流程） |

## 快速开始（CDP）

1. 启动平台 `python app.py`
2. 用例步骤页点击 **网页捕获**
3. 在弹出的调试浏览器中打开待测 URL
4. 点击页面左上角 **UAT 网页捕获 → 开始捕获**，悬停后单击元素
5. 在 **元素编辑器** 中校验、精准定位，点 **完成** 写入步骤

## 环境变量

```env
WEB_CAPTURE_CDP_PORT=9222
WEB_CAPTURE_EXT_WS_PORT=19222
WEB_CAPTURE_EXEC_MODE=cdp
```

`WEB_CAPTURE_EXEC_MODE=cdp` 时，用例执行会优先连接调试浏览器执行 Web 步骤。

## 扩展安装

```bash
python tools/install_web_extension.py --prepare
```

然后在 `edge://extensions` 开启开发者模式 → **加载已解压的扩展程序** → 选择输出目录。

## API（节选）

- `POST /api/element-picker/start` — `capture_channel: web`, `web_capture_mode: cdp`
- `POST /api/web-capture/locator/test` — 精准定位 / 唯一性
- `POST /api/web-capture/verify` — 存在 / 可见 / 启用

## 演示脚本

```bash
python examples/web_capture_baidu_demo.py --dry-run
```
