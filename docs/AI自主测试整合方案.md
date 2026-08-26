# AI 自主测试模块整合方案（v2 修正版）

> 基于代码审计编写。v2 修正：画布已去除（前端中栏已是对话面板），不建"外部浏览器桥接"模块，改为直接启动有头浏览器 + CDP 热同步。

---

## 0. 与 v1 的关键差异

| v1 问题 | v2 修正 |
|---------|---------|
| 讨论了"移除画布" | **画布已去除**，前端中栏已是 `ai-chat-container` 对话面板（消息气泡+输入框+打字指示器），不再讨论 |
| 新建 `ai_external_browser_bridge.py`（250行模块） | **不建模块**，直接在执行路径调 `sync_start_browser(headless=False)` + 获取 CDP ws URL + `sync_hermes_cdp_endpoint()`，约 30 行改动 |
| 新建 `ai_action_recorder.py`（200行模块） | **轻量化**，动作提取做成 inline 函数（~60行），不独立成模块 |
| 前端"替换中栏 iframe" | **中栏已是对话面板**，前端只需补 action_record/vision_result 事件处理 + 右栏卡片 |
| 总计新增 450 行 | 修正为新增 **~100 行**（CDP 获取 ~15行 + 动作提取 ~60行 + 视觉触发 ~25行） |

---

## 1. 当前真实状态（画布已去除后）

### 1.1 前端现状

| 区域 | 现有内容 | 状态 |
|------|---------|------|
| 顶栏 | 返回按钮 + 标题 + 模型芯片 + 设置按钮 | ✅ 已完成 |
| 左栏 | URL + platform + scope + screen-share toggle | ✅ 已完成 |
| **中栏** | `ai-chat-container` 对话面板（消息气泡、输入框、Ctrl+Enter、打字指示器） | ✅ **画布已去除** |
| 右栏 | 步骤预览 | ⚠️ 需扩展为多卡片 |
| 底栏 | 状态栏 | ⚠️ 需补 用例数/视觉状态 |

### 1.2 后端现状

| 组件 | 现状 | 问题 |
|------|------|------|
| `hermes_execute_allowed()` | Hermes Gateway 健康 → 允许执行 | ✅ 无需改 |
| `HERMES_BROWSER_MODE` | `cdp_attach`（Hermes 不启动浏览器，期望外部提供 CDP） | ⚠️ 当前无人提供 CDP |
| `sync_hermes_cdp_endpoint(ws)` | 已实现热更新（`POST /v1/config/cdp`） | ✅ 可直接复用 |
| `sync_start_browser(headless)` | 已实现，可 `headless=False` | ⚠️ **不暴露 CDP ws URL** |
| `hermes_explore` 路径 | 直接调 Hermes，不同步浏览器 | ❌ Hermes 无浏览器可操作 |
| `execute_plan` 路径 | 用平台 Playwright 直接执行，Hermes 不参与 | ❌ 两条路径割裂 |
| `/api/ai/vision/capture` | stub 空壳 | ❌ 需实现 |
| `/api/ai/vision/snapshot` | stub 空壳 | ❌ 需实现 |
| `/api/ai/screen-share/toggle` | stub 空壳 | ❌ 需实现 |
| `/api/ai/actions/to-case` | naive 字段映射 | ❌ 需接入 normalization 管线 |

### 1.3 核心缺口（仅 3 个）

```
缺口 1: 启动有头浏览器后不暴露 CDP → Hermes 无浏览器可操作
缺口 2: 5 个 stub/naive 端点未真实实现
缺口 3: 执行结果未结构化记录 → 无法"动作转用例"
```

---

## 2. 修正后的 Agent 工作链

### 2.1 完整流程

```
用户在 ai_test.html 对话面板输入任务
  │
  ▼
POST /api/ai/task/execute (SSE)                          [修改: 加浏览器启动 + action 事件]
  │
  ├─ Step 1: 直接启动有头浏览器（非桥接模块，3 个函数调用串联）
  │   ├─ sync_start_browser(headless=False)                [复用 playwright_automation.py:12869]
  │   │    → Chromium 窗口在用户屏幕可见
  │   ├─ get_browser_cdp_ws_url()                          [新增 ~15行: 查 http://127.0.0.1:PORT/json/version]
  │   │    → 返回 ws://127.0.0.1:PORT/devtools/browser/xxx
  │   └─ sync_hermes_cdp_endpoint(cdp_ws)                  [复用 hermes_config.py:225]
  │        → POST /v1/config/cdp 热更新到 Hermes（无需重启）
  │        → Hermes 以 cdp_attach 模式接管同一可见浏览器
  │
  ├─ Step 2: AI 执行循环（复用 787 行 tool loop）
  │   └─ ai_chat_tool_loop.run_ai_chat_with_tools_stream() [复用]
  │        ├─ _build_system_prompt()
  │        │    ← page_snapshot  (从 Playwright page 获取)  [复用 ai_page_probe]
  │        │    ← dom_context_pack (从 Playwright page 获取)  [复用 ai_page_probe]
  │        │    ← memory_context (向量检索)                   [复用 ai_memory_store]
  │        ├─ 多轮 LLM 决策
  │        │    ├─ hermes_execute:
  │        │    │    → HermesGatewayClient.execute_user_instruction()  [复用]
  │        │    │    → Hermes 通过 CDP attach 操作用户可见浏览器
  │        │    │    → 用户实时看到 AI 在浏览器中的操作
  │        │    │    → 返回文本结果（URL、操作、选择器、检查点）
  │        │    │    → extract_actions_from_result(result_text)       [新增 ~60行 inline]
  │        │    │       → 解析 Hermes 文本提取结构化动作
  │        │    │       → 推送 action_record SSE 事件到前端
  │        │    │    → 按需截图 (page.screenshot) + 视觉分析            [复用 ai_vision_local]
  │        │    │       → 仅在失败/歧义/用户开启时触发
  │        │    │       → 推送 vision_result SSE 事件到前端
  │        │    └─ refine_test_plan:
  │        │         → local_ai_service.refine_case_and_steps()      [复用]
  │        │         → ai_step_normalization 规范化                  [复用 837行]
  │        └─ 最终输出: JSON plan + action_records[]
  │
  ├─ Step 3: 用户点击"动作转用例"
  │   └─ POST /api/ai/actions/to-case                       [修改: 接入规范化管线]
  │        ├─ normalize_ai_step() × N                        [复用 ai_step_normalization]
  │        ├─ repair_raw_ai_steps_for_platform()             [复用: 三平台修复]
  │        ├─ dedupe_and_validate_ai_steps()                [复用: 去重验证]
  │        ├─ ai_locator_resolution.resolve()                [复用: 如有 probe_registry]
  │        ├─ ai_selector_recovery 4层降级                   [复用]
  │        └─ 保存到 test_cases 表
  │
  └─ Step 4: Skill 沉淀
      └─ record_execution_success()                         [复用]
```

### 2.2 工作链核心特征

| 特征 | 说明 |
|------|------|
| **浏览器可见** | `headless=False` 启动，用户直接看到 Chromium 窗口，AI 操作实时可见 |
| **Hermes 接管同一浏览器** | CDP 热同步后，Hermes 在同一浏览器中 navigate/click/input |
| **Testory 也能操作同一浏览器** | Playwright page 对象仍在，可直接截图、探测 DOM |
| **视觉按需触发** | 不是每个动作都做 OCR+VLM，仅失败/歧义/用户开启时触发 |
| **动作转用例走全管线** | 复用 837 行 normalization + 4 层选择器恢复，不另造数据模型 |

---

## 3. 有头浏览器启动方案（核心：非桥接，直接调用）

### 3.1 为什么不能让 Hermes 自己启动浏览器

| 原因 | 证据 |
|------|------|
| Hermes 配置为 `cdp_attach` 模式 | `hermes_config.py:84` `"HERMES_BROWSER_MODE=cdp_attach"` |
| Skill 明确禁止 Agent 自带浏览器 | `testory-web-browser/SKILL.md:18` "禁止 Agent 自带 headless 浏览器或临时拉起其他 Chromium/Playwright 实例" |
| Testory 需要直接访问浏览器 | 截图、DOM 探测、page_snapshot 都需要 Playwright page 对象 |

**结论**：必须由 Testory 后端启动有头浏览器，暴露 CDP，同步给 Hermes。但这是 3 个函数调用串联，不是独立模块。

### 3.2 实现方案

#### 改动 1: `playwright_automation.py` — 启动时加 CDP 调试端口

**文件**: `playwright_automation.py`，`start_browser()` 方法内（~1234行 args 构造处）

```python
# 现有代码（line 1234-1242）:
args = [
    '--start-maximized',
    '--no-default-browser-check',
    '--no-first-run',
    '--no-sandbox',
    '--disable-dev-shm-usage',
    '--disable-blink-features=AutomationControlled',
]

# 修改为: 有头模式时追加 CDP 暴露参数
if not headless:
    cdp_port = int(os.environ.get("TESTORY_CDP_PORT", "9223"))
    args.extend([
        f'--remote-debugging-port={cdp_port}',
        '--remote-allow-origins=*',
    ])
```

**改动量**: +5 行

#### 改动 2: 新增 `get_browser_cdp_ws_url()` 工具函数

**文件**: `playwright_automation.py`（文件末尾 sync_ 函数区，~13406行附近）

```python
def get_browser_cdp_ws_url() -> str:
    """获取当前已启动浏览器的 CDP WebSocket URL，供 sync_hermes_cdp_endpoint 使用。"""
    import urllib.request
    port = int(os.environ.get("TESTORY_CDP_PORT", "9223"))
    try:
        resp = urllib.request.urlopen(
            f"http://127.0.0.1:{port}/json/version", timeout=3
        )
        data = json.loads(resp.read())
        return (data.get("webSocketDebuggerUrl") or "").strip()
    except Exception:
        return ""


def sync_get_browser_cdp_ws_url() -> str:
    """sync 包装（与 sync_start_browser 风格一致）。"""
    return get_browser_cdp_ws_url()
```

**改动量**: +15 行

#### 改动 3: 在执行路径中串联（`/api/ai/task/execute` SSE 内）

**文件**: `app.py`，`/api/ai/task/execute` SSE 生成器内（tool loop 之前）

```python
# === 启动有头浏览器 + CDP 热同步到 Hermes ===
from playwright_automation import sync_start_browser, sync_get_browser_cdp_ws_url
from hermes_config import sync_hermes_cdp_endpoint

# 1. 启动有头浏览器（用户可见）
yield _sse({"t": "think", "text": "正在启动有头浏览器..."})
sync_start_browser(headless=False)

# 2. 获取 CDP ws URL
cdp_ws = sync_get_browser_cdp_ws_url()
if cdp_ws:
    # 3. 热同步到 Hermes（无需重启）
    if sync_hermes_cdp_endpoint(cdp_ws, restart_gateway=False):
        yield _sse({"t": "think", "text": "浏览器已启动，CDP 已同步到 Hermes"})
    else:
        yield _sse({"t": "think", "text": "浏览器已启动，CDP 同步失败（Hermes 可能无法操作浏览器）", "level": "warning"})
else:
    yield _sse({"t": "think", "text": "浏览器已启动，但未获取到 CDP 端点", "level": "warning"})
```

**改动量**: +20 行

#### 改动 4: `gateway-stream` 路径同样补浏览器启动

**文件**: `app.py`，`/api/ai/agent/gateway-stream` 的 `hermes_explore` 分支（~5518行）

当前 `hermes_explore` 分支直接调 Hermes，不启动浏览器。需在调用前补 Step 1 的 3 行串联逻辑。

**改动量**: +15 行

### 3.3 端口冲突处理

| 场景 | 处理 |
|------|------|
| 端口 9223 被占用 | 启动前检测，被占用则跳过（复用已有浏览器）；或自动递增到 9224 |
| 平台已有无头浏览器在跑 | 有头浏览器是独立实例，CDP 端口不同，不冲突 |
| 用户手动关闭浏览器窗口 | Playwright `disconnected` 事件已处理（`_handle_browser_disconnect_sync`），下次执行自动重启 |

---

## 4. 模块整合清单

### 4.1 复用模块（不修改，直接调用）

| 模块 | 文件 | 在工作链中的角色 |
|------|------|-----------------|
| AI 执行循环 | `ai_chat_tool_loop.py` (787行) | 多轮 tool calling 核心 |
| 多模型调度 | `ai_multi_provider.py` (909行) | 18 家提供商路由 + 熔断器 |
| 步骤规范化 | `ai_step_normalization.py` (837行) | 三平台 action 映射 + 智能修复 |
| DOM 探测 | `ai_page_probe.py` (2000行) | 50+ UI 框架识别 + probe 注册表 |
| 选择器恢复 | `ai_selector_recovery.py` | 4 层降级: 缓存→VLM→LLM→Hermes |
| 定位器解析 | `ai_locator_resolution.py` | 过宽选择器→具体 CSS/XPath |
| 视觉基础设施 | `ai_vision_local.py` | OCR + VLM + 云端回退 + 熔断器 |
| VLM 元素定位 | `ai_vision_grounding.py` | 截图→像素坐标定位 |
| 视觉断言 | `ai_vision_insight.py` | 自然语言断言 |
| 断言引擎 | `assertion_engine.py` | 15 种断言类型 |
| 记忆存储 | `ai_memory_store.py` | 向量记忆 embedding + cosine Top-K |
| 任务管理 | `ai_job_store.py` | SQLite WAL + 异步取消/状态 |
| Skill 管理 | `ai_hermes_skills.py` | Skill 生命周期 + 版本管理 |
| Hermes 配置 | `hermes_config.py` (275行) | CDP 端点热同步 |
| Agent 网关 | `hermes_gateway_client.py` | execute_user_instruction HTTP 客户端 |
| 本地推理 | `ai_local_inference.py` | Ollama + JSON 解析 |
| 结构化场景 | `ai_structured_scenarios.py` | 需求→半结构化测试场景 |

### 4.2 修改模块

| 模块/路由 | 文件:位置 | 修改内容 | 改动量 |
|-----------|---------|---------|--------|
| `start_browser()` | `playwright_automation.py:~1234` | 有头模式追加 `--remote-debugging-port` + `--remote-allow-origins=*` | +5 行 |
| `get_browser_cdp_ws_url()` | `playwright_automation.py:~13406` | 新增工具函数：查 `http://127.0.0.1:PORT/json/version` | +15 行 |
| `/api/ai/task/execute` SSE | `app.py:5936` | tool loop 前加浏览器启动+CDP同步；tool_call_result 后加 action 提取 | +80 行 |
| `/api/ai/agent/gateway-stream` | `app.py:5470` | hermes_explore 分支补浏览器启动 | +15 行 |
| `/api/ai/actions/to-case` | `app.py:6320` | 替换 naive 映射为 normalization 全管线 | +80 行 |
| `/api/ai/vision/capture` | `app.py:6392` | stub→真实: Playwright page 截图 + `ai_vision_local` | +40 行 |
| `/api/ai/vision/snapshot` | `app.py:6399` | stub→真实: 返回最新截图 + OCR | +25 行 |
| `/api/ai/screen-share/toggle` | `app.py:6406` | stub→真实: mss 截屏 + 隐私控制 | +40 行 |
| `ai_test.html` 右栏 | `templates/ai_test.html` | 扩展为多卡片（思考/动作/用例/快照）+ action_record 事件处理 | +120 行 |

### 4.3 新增模块

**无新增独立模块。** 动作提取逻辑作为 inline 函数放在 `app.py` 或 `ai_chat_tool_loop.py` 中。

```python
# 放在 ai_chat_tool_loop.py 或 app.py 中（不独立成文件）
def extract_actions_from_hermes_result(result_text: str) -> list:
    """
    从 Hermes 返回的文本中提取结构化动作。
    Hermes 输出包含：访问的 URL、操作描述、发现的选择器、检查点结论。
    不拦截执行——只观测和记录。
    """
    import re
    records = []
    for line in result_text.split("\n"):
        line = line.strip()
        if not line:
            continue
        # 检测 URL 导航
        url_match = re.search(r'https?://[^\s<>"\']+', line)
        if url_match and any(kw in line.lower() for kw in ["访问","导航","打开","navigate","visited","opened"]):
            records.append({"action_type":"navigate","target":url_match.group(),"result":line[:200]})
        # 检测点击
        elif any(kw in line.lower() for kw in ["点击","click","press","tap"]):
            records.append({"action_type":"click","target":_extract_target(line),"result":line[:200]})
        # 检测输入
        elif any(kw in line.lower() for kw in ["输入","input","type","填写","enter"]):
            records.append({"action_type":"input","target":_extract_target(line),"input_data":_extract_value(line),"result":line[:200]})
        # 检测断言
        elif any(kw in line.lower() for kw in ["验证","assert","检查","verify","确认"]):
            records.append({"action_type":"assert","target":line[:120],"result":line[:200]})
    return records
```

**总计：新增约 100 行代码（含 CDP 获取 15行 + 动作提取 60行 + 视觉触发 25行），修改约 400 行，复用超过 6000 行现有代码。**

---

## 5. API 修改详细方案

### 5.1 `/api/ai/task/execute` — 加浏览器启动 + action 事件

**文件**: `app.py:5936`

**修改位置**: SSE 生成器内部，tool loop 执行之前 + tool_call_result 事件处理中

```python
# ===== 修改点 1: tool loop 之前，启动有头浏览器 =====
from playwright_automation import sync_start_browser, sync_get_browser_cdp_ws_url, sync_navigate_to
from hermes_config import sync_hermes_cdp_endpoint

url = (data.get('url') or data.get('target_page_url') or '').strip()

yield send('think', text='正在启动有头浏览器...')
try:
    sync_start_browser(headless=False)
    if url:
        sync_navigate_to(url, ai_probe=True)
except Exception as e:
    yield send('think', text=f'浏览器启动失败: {e}', level='warning')

cdp_ws = sync_get_browser_cdp_ws_url()
if cdp_ws:
    if sync_hermes_cdp_endpoint(cdp_ws, restart_gateway=False):
        yield send('think', text='浏览器已启动，CDP 已同步到 Hermes')
    else:
        yield send('think', text='CDP 同步失败，Hermes 可能无法操作浏览器', level='warning')
else:
    yield send('think', text='未获取到 CDP 端点', level='warning')

# ===== 修改点 2: tool_call_result 事件中，提取动作 =====
# 在现有 for evt_type, evt_data in run_ai_chat_with_tools_stream(...) 循环中:

elif evt_type == "tool_call_result":
    tool_name = evt_data.get('tool', '')
    result_preview = evt_data.get('result_preview', '')

    if tool_name == "hermes_execute":
        # 新增：从 Hermes 文本结果提取动作
        new_actions = extract_actions_from_hermes_result(result_preview)
        for act in new_actions:
            yield send('action_record',
                action_type=act['action_type'],
                target=act.get('target',''),
                result=act.get('result','')[:100],
            )

        # 按需视觉分析（仅失败/歧义时）
        if _should_trigger_vision(new_actions):
            try:
                from playwright_automation import sync_screenshot  # 已有截图能力
                from ai_vision_local import ocr_region_png
                # 截图 + OCR（复用现有视觉管线）
                png_path = sync_screenshot()
                if png_path:
                    ocr_text = ocr_region_png(png_path)
                    yield send('vision_result', ocr_text=ocr_text[:500])
            except Exception:
                pass  # 视觉失败不影响主流程

        yield send('action', action_type='hermes_execute', status='success',
                   result=result_preview[:200])
```

**新增 SSE 事件类型**:

| 事件类型 | 字段 | 说明 |
|---------|------|------|
| `action_record` | action_type, target, result | 单个结构化动作 |
| `vision_result` | ocr_text | 按需视觉分析结果 |

### 5.2 `/api/ai/actions/to-case` — 接入规范化管线

**文件**: `app.py:6320`

**当前问题**: naive 映射 `action_type→action, target→target`，完全忽略 `ai_step_normalization.py` 的 837 行管线。

```python
@app.route('/api/ai/actions/to-case', methods=['POST'])
@login_required
@api_error_handler
def api_ai_actions_to_case():
    """将执行动作转换为测试用例，走完整规范化管线。"""
    from ai_step_normalization import (
        normalize_ai_step,
        repair_raw_ai_steps_for_platform,
        dedupe_and_validate_ai_steps,
        apply_step_normalization_to_plan,
    )

    data = request.get_json(silent=True) or {}
    actions = data.get('actions', [])
    project_id = data.get('project_id')
    case_name = data.get('case_name', 'AI 生成用例')
    case_url = data.get('url', '')
    platform = data.get('platform', 'web')

    # Step 1: 原始动作 → 步骤格式
    raw_steps = [{
        'action': a.get('action_type', a.get('type', '操作')),
        'target': a.get('target', ''),
        'input_value': a.get('input_data', ''),
        'description': a.get('result', ''),
    } for a in actions]

    # Step 2: 逐条规范化（复用 ai_step_normalization 837行管线）
    normalized = [normalize_ai_step(s, platform_type=platform) for s in raw_steps]

    # Step 3: 平台修复（URL断言修正、toast改regex、桌面navigate改launch_app）
    normalized = repair_raw_ai_steps_for_platform(normalized, platform)

    # Step 4: 去重 + 验证
    clean_steps, warnings = dedupe_and_validate_ai_steps(normalized)

    # Step 5: 构造 plan + 全量规范化
    plan = {'case_name': case_name, 'case_url': case_url, 'steps': clean_steps}
    plan = apply_step_normalization_to_plan(plan, platform_type=platform)

    # Step 6: 选择器恢复（4层降级，如有 probe_registry）
    try:
        from ai_page_probe import get_current_probe_registry
        registry = get_current_probe_registry()
        if registry and clean_steps:
            from ai_locator_resolution import resolve_plan_steps_locators_with_snapshot
            plan = resolve_plan_steps_locators_with_snapshot(plan, registry)
    except Exception:
        pass

    # Step 7: 保存到数据库
    case_id = None
    if project_id:
        try:
            from database import get_db_connection
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO test_cases (project_id, name, steps, created_by, created_at) "
                "VALUES (?, ?, ?, ?, datetime('now'))",
                (int(project_id), case_name,
                 json.dumps(clean_steps, ensure_ascii=False), current_user.id)
            )
            conn.commit()
            case_id = cursor.lastrowid
            conn.close()
        except Exception:
            pass

    # Step 8: Skill 沉淀
    try:
        from hermes_skill_loop import record_execution_success
        record_execution_success(plan, case_url=case_url,
                                 instruction=case_name, outcome='ok')
    except Exception:
        pass

    return jsonify({
        'success': True,
        'message': f'动作转用例成功（规范化 {len(clean_steps)} 步）',
        'case': {'name': case_name, 'project_id': project_id, 'steps': clean_steps},
        'case_id': case_id,
        'warnings': warnings,
    })
```

### 5.3 视觉/截屏 stub → 真实实现

**`/api/ai/vision/capture`** (`app.py:6392`):
```python
@app.route('/api/ai/vision/capture', methods=['POST'])
@login_required
@api_error_handler
def api_ai_vision_capture():
    """捕获当前浏览器截图。"""
    from playwright_automation import sync_screenshot
    png_path = sync_screenshot()
    if not png_path:
        return jsonify({'success': False, 'error': '截图失败，浏览器可能未启动'}), 500
    return jsonify({'success': True, 'screenshot_path': png_path})
```

**`/api/ai/vision/snapshot`** (`app.py:6399`):
```python
@app.route('/api/ai/vision/snapshot', methods=['GET'])
@login_required
@api_error_handler
def api_ai_vision_snapshot():
    """获取最新截图 + OCR 文本（复用 ai_vision_local）。"""
    from playwright_automation import sync_screenshot
    png_path = sync_screenshot()
    if not png_path:
        return jsonify({'success': False, 'error': '无可用截图'}), 404
    ocr_text = ""
    try:
        from ai_vision_local import ocr_region_png
        ocr_text = ocr_region_png(png_path)
    except Exception:
        pass
    return jsonify({'success': True, 'ocr_text': ocr_text[:2000]})
```

**`/api/ai/screen-share/toggle`** (`app.py:6406`):
```python
@app.route('/api/ai/screen-share/toggle', methods=['POST'])
@login_required
@api_error_handler
def api_ai_screen_share_toggle():
    """开启/关闭共享屏幕（截全屏供 AI 视觉分析）。"""
    data = request.get_json(silent=True) or {}
    enabled = data.get('enabled', False)
    # 状态存储在进程内变量（简单方案）
    import app as app_module
    app_module._screen_share_active = bool(enabled)
    return jsonify({
        'success': True,
        'enabled': bool(enabled),
        'message': '共享屏幕已开启' if enabled else '共享屏幕已关闭',
    })
```

---

## 6. 前端修改方案

### 6.1 中栏：已是对话面板，无需改动

当前 `ai-chat-container` 已有：
- 消息气泡（user/ai 区分）
- 输入框（Ctrl+Enter 发送）
- 打字指示器
- 停止/清空按钮

**无需修改中栏结构**，只需在 SSE 处理中新增 `action_record` 和 `vision_result` 事件处理。

### 6.2 右栏：扩展为多卡片

现有右栏 320px，改为可折叠卡片组：

```html
<div class="ai-right-panel">
    <!-- 卡片1: 思考流程 -->
    <div class="ai-result-card" id="cardThinking">
        <div class="ai-result-card__header">思考流程</div>
        <div class="ai-result-card__body" id="thinkingLog"></div>
    </div>
    <!-- 卡片2: 执行动作 -->
    <div class="ai-result-card" id="cardActions">
        <div class="ai-result-card__header">
            执行动作 <span class="ai-badge" id="actionCount">0</span>
        </div>
        <div class="ai-result-card__body" id="actionLog"></div>
    </div>
    <!-- 卡片3: 实时用例 -->
    <div class="ai-result-card" id="cardCase">
        <div class="ai-result-card__header">
            实时用例 <span class="ai-badge" id="stepCount">0</span>
        </div>
        <div class="ai-result-card__body" id="casePreview"></div>
    </div>
    <!-- 卡片4: 屏幕快照 -->
    <div class="ai-result-card" id="cardSnapshot">
        <div class="ai-result-card__header">屏幕快照</div>
        <div class="ai-result-card__body" id="snapshotArea">
            <img id="snapshotImg" style="max-width:100%;border-radius:8px;" />
        </div>
    </div>
</div>
```

### 6.3 SSE 事件处理新增

```javascript
// 在现有 SSE 处理中新增
if (data.type === 'action_record') {
    appendActionRecord(data.action_type, data.target, data.result);
}
if (data.type === 'vision_result') {
    appendVisionResult(data.ocr_text);
}
```

### 6.4 桌面壳约束处理

| 约束 | 处理方式 |
|------|---------|
| `testory-desktop-shell.css` 的 `!important` | 新增 CSS 用 `:not(.testory-frameless-shell)` 限定 |
| CSS/JS 版本号缓存 | 修改后更新 `ai_hub.css?v=1` → `?v=2` |
| 浅色主题 | 保留现有渐变 `--primary-gradient`，不改深色 |
| 内嵌 `<style>` 块 | 新增样式放页面 `<style>`，用 `!important` 覆盖全局 |

---

## 7. 实施阶段

### 阶段一：有头浏览器 + CDP 热同步（核心，~1天）

| 步骤 | 文件 | 说明 | 改动量 |
|------|------|------|--------|
| 1 | `playwright_automation.py:~1234` | `start_browser()` 有头模式追加 `--remote-debugging-port` + `--remote-allow-origins=*` | +5 行 |
| 2 | `playwright_automation.py:~13406` | 新增 `get_browser_cdp_ws_url()` 工具函数 | +15 行 |
| 3 | `app.py:5936` | `/api/ai/task/execute` SSE 加浏览器启动 + CDP 同步 | +20 行 |
| 4 | `app.py:5470` | `/api/ai/agent/gateway-stream` hermes_explore 分支补浏览器启动 | +15 行 |

**验收**: 执行 AI 任务时，有头 Chromium 窗口在用户屏幕可见，Hermes 通过 CDP 操作同一浏览器，用户实时看到 AI 的操作。

### 阶段二：动作提取 + SSE 事件（~1天）

| 步骤 | 文件 | 说明 | 改动量 |
|------|------|------|--------|
| 1 | `ai_chat_tool_loop.py` 或 `app.py` | `extract_actions_from_hermes_result()` inline 函数 | +60 行 |
| 2 | `app.py:5936` | SSE tool_call_result 事件加 action 提取 | +20 行 |
| 3 | `templates/ai_test.html` | 右栏扩展卡片 + action_record 事件处理 | +120 行 |

**验收**: Hermes 执行后，右栏动作列表实时显示结构化动作。

### 阶段三：动作转用例管线接入（~1.5天）

| 步骤 | 文件 | 说明 | 改动量 |
|------|------|------|--------|
| 1 | `app.py:6320` | 重写 `/api/ai/actions/to-case`，接入 `ai_step_normalization` 全管线 | +80 行 |
| 2 | 对接 `ai_locator_resolution` | 用 probe_registry 解析选择器 | 复用 |
| 3 | 对接 `ai_selector_recovery` | 4 层降级处理失败选择器 | 复用 |
| 4 | 前端"动作转用例"按钮 | 调用升级后的 API | +20 行 |

**验收**: 动作转用例后，步骤格式与平台 runner 兼容（三平台 action 映射正确）。

### 阶段四：视觉 stub 实现（~1天）

| 步骤 | 文件 | 说明 | 改动量 |
|------|------|------|--------|
| 1 | `app.py:6392` | `/api/ai/vision/capture` stub→真实 | +10 行 |
| 2 | `app.py:6399` | `/api/ai/vision/snapshot` stub→真实 | +25 行 |
| 3 | `app.py:6406` | `/api/ai/screen-share/toggle` stub→真实 | +15 行 |
| 4 | 动作提取中按需视觉触发 | 失败/歧义时才触发，复用熔断器 | +25 行 |

**验收**: 视觉分析按需触发，不阻塞主执行流。

### 阶段五：联调与优化（~1天）

| 步骤 | 说明 |
|------|------|
| 1 | 端到端测试: Web 平台全流程（输入→浏览器启动→AI操作→动作记录→转用例） |
| 2 | 端到端测试: Desktop 平台（无浏览器，纯 Hermes OS 操作） |
| 3 | 视觉触发策略优化（调整触发阈值） |
| 4 | token 成本控制（限制视觉调用频率） |
| 5 | CSS 版本号更新 + 缓存验证 |

---

## 8. 风险与缓解

| 风险 | 等级 | 缓解措施 |
|------|------|---------|
| CDP 端口 9223 被占用 | 中 | 启动前检测端口，被占用时自动递增；最终降级到无 CDP 模式（Hermes 独立探索） |
| `--remote-debugging-port` 影响现有测试执行 | 低 | 仅 `headless=False` 时追加，无头模式不受影响 |
| 动作提取准确率 | 中 | 多模式匹配 + 原始文本保留；用户可手动编辑动作列表后再转用例 |
| 视觉分析阻塞主流程 | 中 | 所有视觉调用异步执行，失败静默跳过（复用 `_VisionCircuitBreaker` 熔断器） |
| 共享屏幕隐私风险 | 高 | 开启前显示确认对话框；截图不持久化；明确告知截屏范围 |
| 桌面壳 CSS 冲突 | 中 | 新增 CSS 用 `:not(.testory-frameless-shell)` 限定 |
| Playwright `--remote-debugging-port` 与内部 CDP 冲突 | 低 | Chromium 支持多个 CDP 客户端连接同一实例；Playwright 内部 CDP 不受影响 |
| Hermes Gateway 未启动 | 低 | 现有 `bootstrap_hermes_services()` 已处理 |

---

## 9. 与原计划（v0）的关键差异总结

| 原计划 v0 | 本方案 v2 |
|--------|--------|
| 声称新增 10 个 API | 真实新增 0 个 API，修改 5 个现有 stub/naive 实现 |
| 声称完全重写前端 | 前端中栏已是对话面板，只扩展右栏卡片 + SSE 事件 |
| 声称新建"外部浏览器桥接"模块 | 不建模块，直接 3 个函数调用串联（`sync_start_browser` → `get_cdp_ws` → `sync_hermes_cdp_endpoint`） |
| 声称新建动作记录器模块 | inline 函数 ~60 行，不独立成文件 |
| 声称新增视觉识别 | 复用 `ai_vision_local.py`（OCR+VLM+熔断器），只实现 stub |
| 声称新增 AI 执行循环 | 复用 `ai_chat_tool_loop.py` 787 行 |
| 声称新增动作转用例 | 复用 `ai_step_normalization.py` 837 行管线 |
| 未提及 CDP 依赖 | 明确用 `sync_hermes_cdp_endpoint` 热同步解决 |
| 未提及选择器恢复 | 复用 `ai_selector_recovery.py` 4 层降级 |
| 未提及 DOM 探测 | 复用 `ai_page_probe.py` 2000 行 |
| 新增约 450 行 | **修正为新增约 100 行 + 修改 400 行，复用 6000+ 行** |
