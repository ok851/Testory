# AI Agent 工作流 Bug 修复计划

## 问题分析

### 1. 核心错误：`ai_service` 未定义（导致 "name 'ai_service' is not defined"）

**位置**: `app.py` 第 6055 行

**根因**: 在 `api_ai_task_execute` 函数中，调用 `run_ai_chat_with_tools_stream` 时使用了 `ai_service`，但该变量从未导入或定义。

**对比正确用法**:
- 同一文件第 187 行：`from ai_local_inference import local_ai_service`
- 第 5836 行（另一个相似函数）：正确导入并使用 `local_ai_service`

### 2. 冗余代码：重复获取 profile（第 5999-6014 行）

**问题**: 先调用 `get_active_llm_profile()` 获取 profile，如果为空又重复写了一遍相同逻辑。这是之前函数不存在时的临时代码，现在可以简化。

### 3. 未使用的导入：`ai_chat_tools_enabled`（第 5995 行）

**问题**: 从 `ai_chat_tool_loop` 导入了 `ai_chat_tools_enabled`，但在该函数中从未使用。

## 修复方案

### 文件修改

#### 文件: `app.py`

**修改点 1**: 添加 `local_ai_service` 导入（第 5997 行附近）

```python
# 在现有导入后添加
from ai_multi_provider import get_active_llm_profile
from ai_local_inference import local_ai_service  # 新增
```

**修改点 2**: 将 `ai_service` 改为 `local_ai_service`（第 6055 行）

```python
# 修改前
local_ai_service=ai_service,
# 修改后
local_ai_service=local_ai_service,
```

**修改点 3**: 简化 profile 获取逻辑（第 5999-6014 行）

```python
# 修改前
profile = get_active_llm_profile() if callable(get_active_llm_profile) else None
if not profile:
    try:
        from ai_config_paths import ai_model_registry_path
        reg_path = ai_model_registry_path()
        if reg_path.is_file():
            reg = _json.loads(reg_path.read_text(encoding="utf-8"))
            aid = (reg.get("active_profile_id") or "").strip()
            for p in (reg.get("profiles") or []):
                if isinstance(p, dict) and p.get("id") == aid:
                    profile = p
                    break
            if not profile and reg.get("profiles"):
                profile = reg["profiles"][0]
    except Exception:
        pass

# 修改后（简化为单行）
profile = get_active_llm_profile()
```

**修改点 4**: 移除未使用的 `ai_chat_tools_enabled` 导入（第 5995 行）

```python
# 修改前
from ai_chat_tool_loop import (
    run_ai_chat_with_tools_stream,
    ChatToolLoopParams,
    ai_chat_tools_enabled,  # 未使用
)

# 修改后
from ai_chat_tool_loop import (
    run_ai_chat_with_tools_stream,
    ChatToolLoopParams,
)
```

## 风险评估

| 风险 | 等级 | 说明 | 缓解措施 |
|------|------|------|----------|
| `get_active_llm_profile()` 异常 | 低 | 函数内部已有 try-except | 保持原有的异常处理逻辑 |
| 导入冲突 | 低 | `local_ai_service` 在文件顶部已导入 | 局部导入不会影响全局 |
| 功能回归 | 低 | 修改仅影响变量命名和代码简化 | 验证测试通过即可 |

## 验证步骤

1. 运行语法检查：`python -m py_compile app.py`
2. 启动应用并测试 AI 自主测试功能
3. 验证错误提示是否消失

## 预期结果

- `name 'ai_service' is not defined` 错误消失
- AI Agent 工作流正常执行
- 代码更简洁，减少冗余逻辑
