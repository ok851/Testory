# 轻量安装包优化方案（v2 修正版）

## 重要事实更正

经过代码审查，**按需下载组件的完整链路已经实现**，用户**不需要接触任何代码**。具体如下：

| 层级 | 文件 | 状态 |
|------|------|------|
| 后端模块 | `components_manager.py` | ✅ 已存在 |
| API 路由 | `app.py` 的 `/api/components/*` | ✅ 已存在（3 个端点） |
| 前端 UI | `templates/settings.html` 的「可选组件」面板 | ✅ 已存在 |
| 启动集成 | `desktop_shell.py` 启动后自动检测 | ✅ 已完成 |

---

## 已完成的优化

### 1. 核心包瘦身（打包脚本）
- ✅ `Ensure-PortablePython.ps1`：默认跳过 OpenCV/torch/ultralytics/paddleocr，新增 `-WithOpenCV`
- ✅ `prepare_offline_release.ps1`：默认不打 Chromium，新增清理逻辑在步骤 [7c/8]
- ✅ `build_desktop_installer.ps1`：新增 `-Full/-WithChromium/-WithOpenCV/-WithMobile`

### 2. PyInstaller spec 瘦身
- ✅ `_spec_common.py`：新增 `lite_gateway_analysis_bundle()`（不含 playwright 76MB）
- ✅ `testory_desktop_gw.spec`：改用 lite + excludes 含 numpy/cv2/PIL/mss/pandas
- ✅ `testory_embedded_gw.spec`：改用 lite（但 excludes 未更新）
- ✅ `testory_mobile_gw.spec`：改用 lite（但 excludes 未更新）

### 3. 图标与窗口修复
- ✅ `generate_brand_icons.py`：回退方案改为紫青色渐变
- ✅ `desktop_shell.py`：`easy_drag=frameless` 解决拖动问题

### 4. 官网优化
- ✅ `templates/index.html`：下载页改为双卡片（核心壳 + 完整包）
- ✅ `templates/help_components.html`：新增组件安装说明页
- ✅ `projects/testory-website/app.py`：新增 `/help/components` 路由

---

## 待修复：无用大包清理

### 问题 1：numpy.libs 被拉入所有 onedir（36 MB × 6）

**现状**：`numpy.libs`（36 MB）在 **6 个 onedir** 中都有副本，但只有 testory_backend 需要 numpy。

**原因**：PyInstaller 的 `Analysis` 自动追踪依赖链，把 numpy 拉进了所有 onedir。gateway spec 文件缺少 `excludes`。

**需要修改的文件**：

#### 1. `packaging/pyinstaller/testory_embedded_gw.spec`（第 28 行）
```python
# 当前
excludes=["tests", "pytest"],
# 改为
excludes=["tests", "pytest", "numpy", "cv2", "PIL", "mss", "pandas", "scipy", "openpyxl", "reportlab", "docx"],
```

#### 2. `packaging/pyinstaller/testory_mobile_gw.spec`（第 28 行）
```python
# 当前
excludes=["tests", "pytest"],
# 改为
excludes=["tests", "pytest", "numpy", "cv2", "PIL", "mss", "pandas", "scipy", "openpyxl", "reportlab", "docx"],
```

#### 3. `packaging/pyinstaller/testory_browser_runtime.spec`（第 28 行）
```python
# 当前
excludes=["tests", "pytest"],
# 改为
excludes=["tests", "pytest", "numpy", "cv2", "PIL", "mss", "pandas", "scipy", "openpyxl", "reportlab", "docx"],
```
注：BrowserRuntime 仍用 `gateway_analysis_bundle`（需要 playwright），但不需要 numpy。

#### 4. `packaging/pyinstaller/testory_hermes_gw.spec`（第 32 行）
```python
# 当前
excludes=["tests", "pytest"],
# 改为
excludes=["tests", "pytest", "numpy", "cv2", "PIL", "mss", "pandas", "scipy", "openpyxl", "reportlab", "docx"],
```

---

### 问题 2：.venv 中的无用大包（~175 MB 未压缩）

**现状**：`Ensure-PortablePython.ps1` 把 build venv 中的包复制到 `.venv`，但桌面壳运行时根本不需要很多大包。

**关键事实**：
- 桌面壳（`desktop_shell.py`）只 import `webview`
- `.venv` 中的 `playwright/driver`（76 MB）完全没用——gateway 用自己的 onedir
- `.venv` 中的 `pandas`（40 MB）没用——只有后端用
- `.venv` 中的 `numpy`（21 MB）和 `numpy.libs`（36 MB）没用——只有后端用

**问题根源**：`Ensure-PortablePython.ps1` 第 179 行验证了 `import numpy`，导致无法跳过 numpy：
```powershell
& $runtimePy -c "import flask, requests, numpy; print('core imports ok')"
```

**需要修改的文件**：

#### 5. `packaging/bundle/Ensure-PortablePython.ps1`

**5a. 修改第 179 行验证**（移除 numpy 依赖）：
```powershell
# 当前
& $runtimePy -c "import flask, requests, numpy; print('core imports ok')"
# 改为
& $runtimePy -c "import flask, requests; print('core imports ok')"
```

**5b. 在 `$skipPkgs` 列表中增加大包**（第 125-146 行之后）：
```powershell
# 在已有的 if ($Lite -or -not $WithOpenCV) 块之后，增加：
$skipPkgs += @(
    "pandas", "pandas-*",
    "scipy", "scipy-*",
    "playwright", "playwright-*",   # .venv 不需要 playwright（gateway 用 onedir）
    "numpy", "numpy-*",             # .venv 不需要 numpy（backend 用 onedir）
    "cv2", "opencv*",               # 已在 OpenCV 块中，此处确保兜底
    "torch", "torch-*",
    "torchvision", "torchvision-*",
    "ultralytics", "ultralytics-*",
    "paddle*", "paddlex*",
    "reportlab", "reportlab-*",     # .venv 不需要报告库（backend 用 onedir）
    "openpyxl", "openpyxl-*"        # .venv 不需要 Excel 库（backend 用 onedir）
)
```

---

## 预期效果汇总

| 优化项 | 未压缩节省 | 压缩后节省（约 40%） |
|-------|----------|-----------------|
| numpy.libs 从 5 个 gateway 移除 | 36 × 5 = 180 MB | ~72 MB |
| playwright 从 3 个 gateway 移除 | 76 × 3 = 228 MB | ~91 MB（已完成） |
| .venv 中的 playwright/driver | 76 MB | ~30 MB |
| .venv 中的 pandas | 40 MB | ~16 MB |
| .venv 中的 numpy + numpy.libs | 58 MB | ~23 MB |
| **总计** | **~582 MB** | **~232 MB** |

**预期安装包大小：593 - 232 ≈ ~360 MB**（核心壳）

---

## 其他发现的优化点

### `runtime/testory_app/_internal` 中的 cv2（约 30 MB）
PyInstaller 的 `project_analysis_bundle` 会把 cv2 打进 testory_backend 的 onedir。如果后端不需要 cv2（由 components_manager 按需安装），可在 `testory_backend.spec` 的 excludes 中增加 cv2。但这会影响后端的 OpenCV 功能，需谨慎。

### `.venv` 中可能还有其他大包
打包后需要再次运行磁盘分析命令检查：
```powershell
Get-ChildItem -Path "dist\uat_release\.venv\Lib\site-packages" -Directory | ForEach-Object {
    $size = (Get-ChildItem -Path $_.FullName -Recurse -File -ErrorAction SilentlyContinue | Measure-Object -Property Length -Sum).Sum
    [PSCustomObject]@{ Name = $_.Name; SizeMB = [math]::Round($size / 1MB, 1) }
} | Where-Object { $_.SizeMB -gt 5 } | Sort-Object SizeMB -Descending | Format-Table -AutoSize
```

---

## 验证步骤

1. 修改完所有 spec 文件后，清理并重新打包：
```powershell
Remove-Item -Recurse -Force dist\uat_release, dist\testory_app, dist\Testory*, dist\_pyi_work -ErrorAction SilentlyContinue
.\packaging\build_desktop_installer.ps1
```

2. 检查产物大小：
```powershell
(Get-Item dist\testory_setup.exe).Length / 1MB
```
预期：~350-400 MB

3. 检查 onedir 中是否还有 numpy：
```powershell
Get-ChildItem dist\uat_release\runtime -Filter "numpy*" -Recurse -Directory | Select-Object FullName
```
预期：只在 testory_app 中有 numpy

4. 检查 .venv 中是否还有大包：
```powershell
Get-ChildItem -Path "dist\uat_release\.venv\Lib\site-packages" -Directory | ForEach-Object {
    $size = (Get-ChildItem -Path $_.FullName -Recurse -File -ErrorAction SilentlyContinue | Measure-Object -Property Length -Sum).Sum
    [PSCustomObject]@{ Name = $_.Name; SizeMB = [math]::Round($size / 1MB, 1) }
} | Where-Object { $_.SizeMB -gt 5 } | Sort-Object SizeMB -Descending | Format-Table -AutoSize
```
预期：没有超过 10 MB 的包

5. 安装测试：
- 安装核心壳 → 启动 → 正常运行
- 设置 → 可选组件 → 看到 Chromium/OpenCV 未安装
- 点击安装 Chromium → 下载完成 → 重启后 Web 自动化可用
