# CDP Attach 模式参考（Testory）

## 环境变量

```env
HERMES_BROWSER_MODE=cdp_attach
HERMES_CDP_ENDPOINT=ws://127.0.0.1:xxxxx/devtools/browser/...
```

平台通过 `web_capture.cdp_browser.launch_debug_browser` 启动**用户本机 Edge/Chrome**（`--remote-debugging-port`），再由 `sync_hermes_cdp_endpoint()` 写入 `HERMES_HOME/.env` 供 Hermes attach。

内嵌画布 Chromium / Browser Runtime 已废弃，请勿再依赖。

## WebSocket 403 修复

Chromium/Edge 启动需 `--remote-allow-origins=*`；Python 连接时：

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

## 与本机浏览器的关系

- 平台用 `cdp_browser` 拉起本机 Edge/Chrome
- Hermes 以 `cdp_attach` 连接**同一**调试端口
- AI 执行前校验浏览器存活；失败则提示启动本机浏览器，禁止另起独立后台 Chromium（除非显式 `AI_ALLOW_PLAYWRIGHT_CHROMIUM_FALLBACK=1`）

## 端口说明

调试端口由 `launch_debug_browser` 分配（常见 9222+）。以返回的 `debug_port` / `cdp_ws` 为准，不要写死。
