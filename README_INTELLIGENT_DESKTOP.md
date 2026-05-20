# 桌面自动化智能升级文档

## 概述

本次升级借鉴 OpenClaw 的设计理念，实现了三大核心改进：

1. **动态感知** - 运行时窗口状态快照 + 智能控件匹配
2. **原子化工具层** - 技能框架 + 自然语言路由
3. **产品封装** - 零配置启动 + 一键打包

## 新增模块

### 1. 运行时动态感知层

#### `desktop_runtime_snapshot.py`
实时捕获窗口控件树，支持智能控件匹配。

**核心功能：**
- `capture_window_snapshot()` - 捕获窗口快照
- `find_similar_control()` - 查找相似控件
- `find_control_by_fuzzy_match()` - 自然语言模糊查找
- `get_snapshot_summary()` - 快照摘要

**使用示例：**
```python
from desktop_runtime_snapshot import capture_window_snapshot, find_control_by_fuzzy_match

# 捕获当前窗口
snapshot = capture_window_snapshot(window)

# 自然语言查找控件
matches = find_control_by_fuzzy_match(snapshot, "确定按钮")
if matches:
    best_match, score = matches[0]
    print(f"找到控件: {best_match.name}, 匹配度: {score}")
```

---

### 2. 模糊搜索与语义理解

#### `desktop_fuzzy_search.py`
自然语言到应用的智能匹配。

**核心功能：**
- `FuzzyAppMatcher` - 应用模糊匹配器
- `SemanticIntentParser` - 语义意图解析器
- `find_apps_by_query()` - 基于查询查找应用
- `parse_user_intent()` - 解析用户意图

**使用示例：**
```python
from desktop_fuzzy_search import parse_user_intent, find_apps_by_query
from desktop_app_catalog import list_catalog_apps

# 解析用户意图
intent = parse_user_intent("打开记事本输入Hello World")
print(intent)
# 输出: {"action": "launch", "target_app": "记事本", "target_control": {...}, ...}

# 模糊搜索应用
apps = list_catalog_apps()
matches = find_apps_by_query("编辑器", apps, top_k=3)
for app, score in matches:
    print(f"{app['display_name']} - 匹配度: {score:.2f}")
```

---

### 3. 技能框架

#### `desktop_skill_framework.py`
所有技能的统一抽象基类。

**核心概念：**
- `DesktopSkill` - 技能基类
- `SkillContext` - 执行上下文
- `SkillResult` - 执行结果
- `SkillRegistry` - 技能注册表

**创建自定义技能：**
```python
from desktop_skill_framework import DesktopSkill, SkillContext, SkillResult, register_skill

class MyCustomSkill(DesktopSkill):
    skill_id = "my_skill"
    skill_name = "我的技能"
    skill_description = "这是一个示例技能"
    intent_patterns = ["执行我的操作", "运行我的任务"]

    def can_handle(self, context: SkillContext) -> bool:
        return "我的" in context.user_input

    def execute(self, context: SkillContext) -> SkillResult:
        # 实现技能逻辑
        return SkillResult.success("任务完成")

# 注册技能
register_skill(MyCustomSkill)
```

---

### 4. 内置技能集

#### `desktop_builtin_skills.py`
预置的常用自动化技能。

**可用技能：**
| 技能ID | 名称 | 功能 | 示例指令 |
|--------|------|------|----------|
| launch_app | 启动应用 | 启动应用程序 | "打开记事本" |
| attach_window | 附着窗口 | 连接到已有窗口 | "附着到计算器" |
| click_control | 点击控件 | 点击指定控件 | "点击确定按钮" |
| type_text | 输入文本 | 输入文字内容 | "输入Hello World" |
| organize_files | 整理文件 | 自动整理文件夹 | "整理下载文件夹" |
| manage_window | 窗口管理 | 最大化/最小化等 | "最大化窗口" |
| wait | 等待 | 等待指定时间 | "等待3秒" |

---

### 5. 零配置初始化

#### `desktop_auto_setup.py`
首次启动自动完成所有配置。

**功能：**
- 自动检测操作系统和依赖
- 扫描常用应用生成别名
- 构建应用目录索引
- 生成推荐配置

**使用：**
```python
from desktop_auto_setup import ensure_initialized, get_quick_start_guide

# 自动初始化（首次运行）
result = ensure_initialized()

# 获取快速入门指南
print(get_quick_start_guide())
```

---

### 6. 智能API统一入口

#### `desktop_intelligent_api.py`
简洁的高层API，整合所有功能。

**核心类：**
- `DesktopAgent` - 智能代理类

**使用示例：**
```python
from desktop_intelligent_api import DesktopAgent, run

# 方法1: 使用代理类
agent = DesktopAgent()
agent.initialize()

# 自然语言执行
agent.execute("打开记事本")
agent.execute("点击新建")
agent.type_text("Hello World")

# 方法2: 快速执行（推荐）
run("打开记事本")
run("点击格式菜单")
run("输入Hello World")
```

---

### 7. 打包工具

#### `build_exe.py`
一键打包为独立可执行文件。

**使用：**
```bash
python build_exe.py
```

**输出：**
- `dist/NewUITestPlatform.exe` - 单文件可执行程序
- `dist/启动平台.bat` - 一键启动脚本
- `dist/README.md` - 用户说明文档

**分发：**
用户只需解压zip文件，双击 `启动平台.bat` 即可使用，无需Python环境。

---

## 架构总览

```
用户输入层
    │
    ├──→ 自然语言指令 ("打开记事本")
    ├──→ 程序名/路径 ("notepad.exe")
    └──→ 聊天消息 (通过适配器)

意图解析层 (desktop_fuzzy_search.py)
    │
    ├──→ SemanticIntentParser 解析意图
    └──→ FuzzyAppMatcher 匹配应用

技能路由层 (desktop_skill_framework.py)
    │
    ├──→ 意图匹配 → 找到对应技能
    └──→ 上下文匹配 → 自动路由

核心自动化层 (现有模块)
    │
    ├──→ desktop_discovery - 应用发现
    ├──→ desktop_locator - 控件定位
    ├──→ desktop_app_catalog - 应用目录
    └──→ desktop_runtime_snapshot - 运行时快照

执行层
    │
    └──→ pywinauto / Win32 API
```

---

## 迁移指南

### 从旧API迁移到新API

**旧方式（需要精确指定）：**
```python
from desktop_locator import attach_application
from desktop_env_config import prepare_desktop_step

# 需要预先知道确切路径
spec = {"path": "C:\\Windows\\notepad.exe"}
app, window = attach_application(spec)
```

**新方式（零配置）：**
```python
from desktop_intelligent_api import run

# 自然语言即可
run("打开记事本")
run("点击格式")
run("输入Hello World")
```

---

## 配置说明

### 环境变量（可选）

```ini
# .env

# 快照缓存时间（秒）
DESKTOP_SNAPSHOT_TTL_SEC=30

# 用户习惯历史文件
DESKTOP_USER_HISTORY_FILE=data/desktop_user_history.json

# 深度搜索开关
DESKTOP_DEEP_SEARCH=0

# 应用别名（会被自动生成的配置合并）
DESKTOP_APP_ALIASES={"erp":"C:\\ERP\\client.exe"}
```

---

## 性能优化

| 优化项 | 说明 | 默认 |
|--------|------|------|
| 应用目录缓存 | 扫描结果持久化到JSON | 启用 |
| 窗口快照TTL | 避免重复捕获 | 30秒 |
| 用户习惯学习 | 记录选择偏好 | 启用 |
| 后台目录构建 | 异步初始化 | 启用 |

---

## 扩展开发

### 1. 创建自定义技能

```python
from desktop_skill_framework import DesktopSkill, SkillContext, SkillResult, register_skill

class EmailTriageSkill(DesktopSkill):
    skill_id = "email_triage"
    skill_name = "邮件分类"
    intent_patterns = ["整理邮件", "分类邮件", "处理收件箱"]

    def execute(self, context: SkillContext) -> SkillResult:
        # 实现邮件分类逻辑
        return SkillResult.success("已分类50封邮件")

register_skill(EmailTriageSkill)
```

### 2. 集成到现有系统

```python
from desktop_intelligent_api import DesktopAgent

# 在Flask中集成
from flask import Flask, request, jsonify
app = Flask(__name__)
agent = DesktopAgent()

@app.route('/api/execute', methods=['POST'])
def execute_command():
    command = request.json.get('command')
    result = agent.execute(command)
    return jsonify(result.to_dict())
```

---

## 测试建议

运行示例脚本测试所有功能：

```bash
python example_intelligent_usage.py
```

---

## 后续规划

### 短期（已完成）
- [x] 运行时窗口快照
- [x] 模糊搜索匹配
- [x] 技能框架
- [x] 内置技能集
- [x] 零配置初始化

### 中期
- [ ] 更多内置技能（邮件、浏览器自动化）
- [ ] 技能市场/仓库机制
- [ ] 多语言支持
- [ ] 企业微信/钉钉集成

### 长期
- [ ] AI意图理解增强（LLM集成）
- [ ] 跨平台支持（macOS/Linux）
- [ ] 云端技能同步

---

## 参考

- OpenClaw: https://openclawdesktop.com/
- pywinauto: https://pywinauto.readthedocs.io/

---

## 问题反馈

如有问题，请查看：
- 日志文件：`logs/uat_platform_*.log`
- 错误日志：`logs/errors_*.log`
- 应用目录：`data/desktop_app_catalog.json`
