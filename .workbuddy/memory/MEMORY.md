# Testory 项目长期记忆

## 项目架构
- 桌面框架：pywebview >= 5.0 (Windows 使用 WebView2/EdgeChromium)
- 后端：Flask (app.py ~14000行)，监听 127.0.0.1:5000
- 前端：Jinja2 SSR + Tailwind CSS + 自定义 CSS，无 SPA 框架
- 无边框窗口模式 (TESTORY_FRAMELESS_SHELL=1)
- 启动链：Testory.exe → testory_exe_launcher.py → uat_desktop.py → Flask + pywebview
- **WSGI 服务器铁律**：桌面端常驻 Flask **严禁用 werkzeug 开发服务器**（`app.run(debug=...)`）。werkzeug 3.0.1 在 `serving.py:347` 执行 `self.rfile.read(10_000_000)`，长期运行内存紧张时抛 `MemoryError`，且崩溃在 URL 解析前，请求被静默丢弃（表现为"对方没发请求"）。`app.py` `__main__` 已改为三级容错：waitress（优先，需联网装）→ wsgiref(ThreadingWSGIServer)（标准库零安装，默认）→ werkzeug（兜底）。并套 `_AccessLogMiddleware`（已抽到 `modules/core/wsgi_middleware.py`），请求一到达即打印访问日志，且**剥除应用下发的 hop-by-hop 响应头**（Connection/Keep-Alive/Transfer-Encoding 等）——WSGI 规范禁止应用返回这类头，werkzeug 容忍但 wsgiref/waitress 会 assert 崩溃；曾导致所有带 `'Connection': 'keep-alive'` 头的 SSE 路由（/api/ai/task/execute 等 4 处）一律 HTTP 500（agent 一收到命令就失败，发生在调模型之前）。改动启动逻辑时务必保留 wsgiref 分支与 hop-by-hop 剥除；新增 SSE 路由时响应头不要再手写 Connection/Keep-Alive。

## 仓库结构（2026-08-26 大重组后）
- 根目录仅 12 个 .py：app.py、database.py + 10 个 CLI 工具（build_exe/verify_installation/generate_license/cross_end_cli/android_sdk_manager/assertion_engine/element_locator_steward/enhanced_text_extractor/example_intelligent_usage/env_example_sync）
- `modules/` 新顶级包（9 个域子包）：ai(36) / desktop(38) / mobile(23) / hermes(10) / web(17) / execution(14) / integration(13) / auth(5) / core(17)
- 全库 import 已重写为 `modules.<域>.<模块>` 绝对导入，**无根目录 shim**
- modules/ 下文件用 `Path(__file__).resolve().parents[2]` 锚定仓库根（license.key/data/.env/.venv/static）
- 勿用 `platform` 作包名（遮蔽标准库，pywebview/numpy 会炸）
- PyInstaller：`packaging/pyinstaller/_spec_common.py` 的 `collect_submodules` 已含 "modules"，硬编码 hiddenimports 已改点路径
- `modules/desktop/desktop_picker.py` 子进程依赖显式 PYTHONPATH 注入（源码布局 sys.path[0] 不再是仓库根）

## 关键文件
- `packaging/desktop_shell.py` — pywebview 窗口创建、DesktopWindowApi (minimize/toggle_maximize/close)
- `templates/base.html` — 主基础模板（标题栏+导航+CSS/JS引入）
- `templates/auth_shell.html` — 认证页基础模板
- `static/css/testory-desktop-chrome.css` — 无边框标题栏样式
- `static/js/testory-desktop-chrome.js` — 窗口控制按钮事件
- `static/css/testory-desktop-layout.css` — 桌面端 UI 尺寸适配
- `static/css/testory-desktop-shell.css` — 废弃侧栏壳覆盖（需注意 !important 与 frameless 冲突）
- `static/desktop/shell_boot.html` — 启动加载页
- `modules/core/deployment_config.py` — is_client/is_tauri 判断逻辑

## 移动端 Agent（跨端）关键资产
- `modules/mobile/mobile_cross_end_tools.py` — 跨端 Agent 手机侧工具：`dispatch_cross_end_tool` 分发器、`cross_end_tool_schemas` 工具面、`MOBILE_TOOL_NAMES` 门禁集合（工具挂 schema 但未入此集合 = 死工具 bug）
- `modules/mobile/mobile_ui_probe.py` — 移动端 UI 树结构化感知（RPC → agent_page_source → ADB uiautomator 三级获取，归一化文本对齐桌面 UIA 树）
- `modules/ai/ai_chat_tool_loop.py` — 决策循环：`_browser_obs_cap_check` 上限即跳过（snapshot 2/console 1，epoch 由操作工具重置）、`_sig_dedup` SSE 去重（8s）
- `modules/hermes/patch_venv_hermes_tools.py` — .venv 依赖包幂等补丁（browser_tool.py 只读缓存 + tool_guardrails hard_stop_enabled）。**pip 升级 .venv 后必须重跑**：`.venv/Scripts/python.exe modules/hermes/patch_venv_hermes_tools.py`
- `static/js/mobile_scrcpy_mirror.js` — ScrcpyMirrorPlayer（WebCodecs H.264 播放器，从 git aee8ecc 恢复）。帧格式 `[meta:u8][len:u32大端][payload]`，meta 0=P/1=config(SPS/PPS)/2=keyframe
- `templates/mobile_h264_player.html` — 手机实时画面观察台，路由 `/mobile-h264-player?serial=xxx`
- `templates/cross_end.html` — 跨端编排页，含「双端观察」overlay（左 PC /api/screenshot 轮询 + 右手机 iframe 播放器）
- scrcpy bridge：`ws://{host}:8767/scrcpy?serial=xxx`（无鉴权，端口可配 MOBILE_SCRCPY_BRIDGE_PORT）；上行控制 `{"type":"tap"|"swipe","x","y","screen_width","screen_height"}`
- 执行位置适配：`_mobile_execute_action` 自动切换 scrcpy 注入 → ADB → APK job（中文输入必走 APK 剪贴板）
- `modules/mobile/mobile_device_lock.py` — 设备级互斥锁（按 serial hash 分文件 `data/.uat_mobile_dev_<sha256[:16]>.lock`，线程重入+跨进程互斥+stale 1h）；`mobile_device_guard(serial, owner, timeout_sec, required)` 上下文管理器；已接入 `_mobile_execute_action` 各动作分支。注意：APK job 是用户级队列（device_id 强制置空），PC 侧无法预知领取设备，`mobile_run_steps` 不加 serial 锁
- `ai_modules/execute/multi_device_scheduler.py` — 含跨端并行编排：`is_cross_end_parallel_stage`（cross_end_parallel:true / parallel:{pc,mobile} 简写 / branches 列表）、`execute_cross_end_parallel_stage`（ThreadPool 并行 PC 桌面分支 + 手机 APK 分支）、`cross_end_parallel_summary`；orchestrator stage 分发最前已注册

## 重要注意事项
- `testory-desktop-shell.css` 的 `!important` 会覆盖 frameless shell 的 flex 布局，必须用 `:not(.testory-frameless-shell)` 限定
- body 在桌面模式下同时有 `testory-desktop-client` 和 `testory-frameless-shell` 两个类
- CSS 版本号用于缓存控制，修改 CSS/JS 后必须更新版本号
- 多个页面使用内嵌 `<style>` 块，需用 `!important` 在全局 CSS 中覆盖
- Windows 上 `time.time()` 连续调用可能返回相同值（精度问题），TTL/超时判断注意边界（用 `>=` 而非 `>`）
- 新增业务模块应放入 `modules/<对应域>/`，根目录不再堆放实现文件
- **Hermes 家目录解析**：`hermes_home_dir()`（`modules/hermes/hermes_config.py`）当 `HERMES_HOME` 含字面量 `%UAT_DATA_DIR%` 且 `UAT_DATA_DIR` 为空时，必须回退 `LOCALAPPDATA/Testory/hermes`，**禁止**回退到仓库根下字面量 `%UAT_DATA_DIR%/hermes` 毒目录（写入 WinError 5）。`UAT_DATA_DIR` 由 `packaging/uat_desktop.py` 设为 `AppData/Local/Testory`，缺失会导致 Hermes 等多处数据目录解析到错误路径。
- Hermes 网关是**独立 `hermes_agent` 包**（.venv 内 0.16.0），与项目 `modules/` 重组无关；智能体"启动失败"常见真因：网关 8642 未监听+状态文件陈旧假运行、平台↔网关 `API_SERVER_KEY` 不匹配（`resolve_hermes_api_server_key()` 有自愈合）、上游 LLM 403 额度耗尽、CDP 9222 未开。
