# CDP Attach 模式参考（Testory）

## 环境变量

```env
HERMES_BROWSER_MODE=cdp_attach
HERMES_CDP_ENDPOINT=ws://127.0.0.1:xxxxx/devtools/browser/...
```

平台在 AI 测试画布激活时由 `sync_hermes_cdp_endpoint()` 自动写入并重启 Gateway。

## WebSocket 403 修复

Chromium 启动需 `--remote-allow-origins=*`；Python 连接时：

```python
header=['Origin: http://127.0.0.1:PORT']
```

## 常用 CDP 调用序列

```python
call('Page.enable')
call('Runtime.enable')
call('Page.navigate', {'url': 'https://example.com'})
# 等待加载
call('Runtime.evaluate', {
    'expression': '({title: document.title, url: location.href})',
    'returnByValue': True,
})
call('Page.captureScreenshot', {'format': 'png'})
```

## 与 Playwright 画布的关系

- 画布 gateway 用 Playwright 管理 BrowserContext
- Hermes 通过 CDP attach 到**同一** Chromium 实例
- `run-steps` API 与 Hermes terminal/browser 工具不得同时争抢 run_lock

## 端口说明

Testory embedded browser 使用动态 CDP 端口，**不要**硬编码 9222/9223；始终读取 `HERMES_CDP_ENDPOINT` 或 session API 返回的 `cdp_browser_ws`。
