# Testory Tauri Desktop Shell (Phase 0)

Flask SSR + Tauri frameless WebView. Pywebview launcher (`packaging/uat_desktop.py`) remains unchanged.

## Prerequisites

1. **Rust** — https://rustup.rs/  
   - 安装后**重启终端**，或确保 `%USERPROFILE%\.cargo\bin` 在 PATH 中（否则会出现 `cargo metadata ... program not found`）。
2. **Visual Studio C++ 构建工具**（Windows 必需）— 编译 Tauri 需要 `link.exe`：  
   - 安装 [Build Tools for Visual Studio 2022](https://visualstudio.microsoft.com/visual-cpp-build-tools/)  
   - 勾选 **「使用 C++ 的桌面开发」** 或 **MSVC v143 + Windows SDK**  
   - 若报错 `linker link.exe not found`，即缺少此项。
3. **Node.js** — `npm install --cache .npm-cache`
4. **Python `.venv`** — 项目根目录，含 Flask 依赖

## First-time setup

```powershell
cd D:\...\NewUITestPlatform
npm install --cache .npm-cache
npm run build:tauri-api
```

## Development

Ensure `%USERPROFILE%\.cargo\bin` is on PATH (restart terminal after installing Rust).

```powershell
npm install --cache .npm-cache
npm run build:tauri-api
npm run tauri dev
```

```powershell
npm install --cache .npm-cache
npm run build:tauri-api
npm run tauri dev
```

## Production bundle

```powershell
.\scripts\prepare_tauri_bundle.ps1
```

## Coexistence with pywebview

| Launcher | `TESTORY_TAURI_MODE` | Port |
|----------|----------------------|------|
| `pythonw packaging\uat_desktop.py` | unset | 5000 |
| `npm run tauri dev` | `1` (Rust) | OS-assigned |

## Phase 3 (optional perf / desktop UX)

- **Invoke proxy**: `flask_fetch` Rust command → localhost Flask (forwards `document.cookie`). `tauri-polyfill.js` patches `uatFetchCurrentRunsPack` to use it.
- **Runtime logs**: `/runtime-logs` page + SSE `/stream/logs?source=platform|backend` with `virtual-scroll.js` (10k line buffer).
- **Native file dialogs**: Tauri mode intercepts `<input type="file">` clicks → `pick_native_files` + `read_native_file_base64` (50MB cap). Add `data-testory-skip-native` to opt out.
