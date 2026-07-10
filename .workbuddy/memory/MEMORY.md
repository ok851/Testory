# Testory 项目长期记忆

## 项目架构
- 桌面框架：pywebview >= 5.0 (Windows 使用 WebView2/EdgeChromium)
- 后端：Flask (app.py ~14000行)，监听 127.0.0.1:5000
- 前端：Jinja2 SSR + Tailwind CSS + 自定义 CSS，无 SPA 框架
- 无边框窗口模式 (TESTORY_FRAMELESS_SHELL=1)
- 启动链：Testory.exe → testory_exe_launcher.py → uat_desktop.py → Flask + pywebview

## 关键文件
- `packaging/desktop_shell.py` — pywebview 窗口创建、DesktopWindowApi (minimize/toggle_maximize/close)
- `templates/base.html` — 主基础模板（标题栏+导航+CSS/JS引入）
- `templates/auth_shell.html` — 认证页基础模板
- `static/css/testory-desktop-chrome.css` — 无边框标题栏样式
- `static/js/testory-desktop-chrome.js` — 窗口控制按钮事件
- `static/css/testory-desktop-layout.css` — 桌面端 UI 尺寸适配
- `static/css/testory-desktop-shell.css` — 废弃侧栏壳覆盖（需注意 !important 与 frameless 冲突）
- `static/desktop/shell_boot.html` — 启动加载页
- `deployment_config.py` — is_client/is_tauri 判断逻辑

## 重要注意事项
- `testory-desktop-shell.css` 的 `!important` 会覆盖 frameless shell 的 flex 布局，必须用 `:not(.testory-frameless-shell)` 限定
- body 在桌面模式下同时有 `testory-desktop-client` 和 `testory-frameless-shell` 两个类
- CSS 版本号用于缓存控制，修改 CSS/JS 后必须更新版本号
- 多个页面使用内嵌 `<style>` 块，需用 `!important` 在全局 CSS 中覆盖
