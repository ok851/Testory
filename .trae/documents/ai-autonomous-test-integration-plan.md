# AI 自主测试模块整合方案 — 剩余阶段执行计划

> 基于全量代码审计编写。本计划不是"新建清单"，而是"整合清单"——明确区分复用、修改、新增，每一步对应到具体文件、行号和函数。
> 已获用户确认：①完全替换 `[INIT]` 路径为 `ensure_browser`；②全量执行 Phase 1.2-5。

---

## 摘要

Phase 0（8 项核心 Bug 修复）和 Phase 1.1（`ai_external_browser_bridge.py` 222 行）已落地。本计划覆盖剩余 5 个阶段，总改动约 900 行（新增 ~400 行、修改 ~500 行），复用现有 6000+ 行 AI 模块。

**核心思路**：移除 `[INIT]` 伪启动路径 → 接入 ExternalBrowserBridge 真实有头浏览器 → 新增 ActionRecorder 观测 Hermes 执行 → 动作转用例走 837 行规范化管线 → 视觉 stub 接入 ai_vision_local → 前端补齐 action_record/vision_result 事件。

---

## 当前状态分析（审计结论）

| 模块/路由 | 状态 | 证据 |
|---|---|---|
| `ai_external_browser_bridge.py` | ✅ 已存在（222 行） | ensure_browser/get_page_snapshot/get_probe_registry/get_dom_context_pack/capture_screenshot/cleanup 齐全；CDP 走 DevTools `/json/version`；已调用 sync_hermes_cdp_endpoint + _COLLECT_INTERACTIVE_JS_FLAT + _format_summary_lines |
| `/api/ai/task/execute` SSE | ⚠️ Phase 0 已修，Phase 1.2 未接 | 第 5997-6011 行仍走 `hermes_client.execute_user_instruction("[INIT]...")`；ChatToolLoopParams 第 6060-6064 行传空 snapshot/registry/dom_pack |
| `ai_action_recorder.py` | ❌ 不存在 | Phase 2 完全未落地 |
| `ai_chat_tool_loop.ChatToolLoopParams` | ⚠️ 有 abort_event，无 recorder | 第 346-363 行 |
| `/api/ai/actions/to-case` | ❌ naive 映射 | 第 6303-6309 行简单 action_type→action 映射，未接入 normalization |
| 3 个 vision/screen-share stub | ❌ 仍是 stub | 第 6363-6383 行返回"开发中" |
| `ai_test.html` 右栏 | ✅ 4 卡片已存在 | 思考流程/执行动作/实时用例/执行报告（第 841-905 行） |
| `ai_test.html` SSE 处理 | ⚠️ 缺 action_record/vision_result | 第 1401-1463 行已处理 think/action/case_step/done/error |

### 关键函数签名（实测，与用户提供计划有出入）

| 函数 | 文件:行 | 实测签名 |
|---|---|---|
| `normalize_ai_step` | ai_step_normalization.py:245 | `normalize_ai_step(step: dict) -> dict` — **无 platform 参数**，从 `step["automation_layer"]` 读取平台 |
| `repair_raw_ai_steps_for_platform` | ai_step_normalization.py:220 | `repair_raw_ai_steps_for_platform(steps: Any) -> List[str]` — **无 platform 参数**，就地修改，返回告警列表 |
| `dedupe_and_validate_ai_steps` | ai_step_normalization.py:558 | `dedupe_and_validate_ai_steps(steps: list, *, platform: str = "web") -> Tuple[List[dict], List[str]]` |
| `apply_step_normalization_to_plan` | ai_step_normalization.py:662 | `apply_step_normalization_to_plan(plan) -> Tuple[Optional[Dict], List[str]]` |
| `sync_hermes_cdp_endpoint` | hermes_config.py:225 | `sync_hermes_cdp_endpoint(cdp_ws_url, *, restart_gateway=True) -> bool` |
| `ocr_region_png` | ai_vision_local.py:92 | `ocr_region_png(image_bytes: bytes) -> str` — **接受 bytes 不是路径** |
| `vision_describe_cloud` | ai_vision_local.py:126 | `vision_describe_cloud(image_bytes, instruction, *, provider, api_key, base_url, model, timeout) -> str` |

---

## 待执行变更

### Phase 1.2：桥接层集成到 SSE（核心）

**目标**：移除 `/api/ai/task/execute` 中第 5997-6011 行的 `[INIT]` 伪启动路径，改用 `ai_external_browser_bridge.ensure_browser()`；ChatToolLoopParams 使用 bridge 提供的 snapshot/registry/dom_pack。

**修改文件**：`app.py`

**修改点 A — 替换浏览器启动逻辑（第 5994-6013 行）**

将：
```python
if hermes_available:
    yield send('think', text='Hermes 智能体已就绪', status='done')
    try:
        from hermes_config import hermes_cdp_attached
        if not hermes_cdp_attached():
            yield send('think', text='正在启动浏览器...', status='running')
            init_result = hermes_client.execute_user_instruction("[INIT] ...", ...)
            if init_result and '"ok": false' in init_result.lower():
                yield send('think', text='浏览器启动失败: ...', status='warning')
            else:
                yield send('think', text='浏览器已启动', status='done')
    except Exception as e:
        ...
else:
    yield send('think', text='Hermes 智能体不可用，将使用内置浏览器模式', status='warning')
```

替换为：
```python
browser_ready = False
if hermes_available:
    yield send('think', text='Hermes 智能体已就绪', status='done')
    # 使用 ExternalBrowserBridge 启动有头浏览器（替代 [INIT] 伪启动）
    try:
        from ai_external_browser_bridge import ensure_browser
        from hermes_config import hermes_cdp_attached
        if not hermes_cdp_attached():
            yield send('think', text='正在启动有头浏览器...', status='running')
            browser_ready = ensure_browser(headless=False, url=url or "")
            if browser_ready:
                yield send('think', text='浏览器已启动，CDP 已同步到 Hermes', status='done')
            else:
                yield send('think', text='浏览器启动失败，Hermes 将使用独立浏览器（无快照模式）', status='warning')
        else:
            browser_ready = True
            if url:
                from ai_external_browser_bridge import get_page
                _pg = get_page()
                if _pg and not _pg.is_closed():
                    try: _pg.goto(url, wait_until="domcontentloaded", timeout=15000)
                    except Exception: pass
            yield send('think', text='CDP 已连接，复用现有浏览器', status='done')
    except ImportError:
        yield send('think', text='Playwright 未安装，Hermes 将使用独立浏览器', status='warning')
    except Exception as e:
        uat_logger.warning("ExternalBrowserBridge 启动失败: %s", e)
        yield send('think', text='浏览器桥接失败: ' + str(e)[:200] + '（降级为无快照模式）', status='warning')
else:
    yield send('think', text='Hermes 智能体不可用且内置浏览器模式未集成。请先启动 Hermes Gateway 或连接浏览器后重试。', status='warning')
```

**修改点 B — ChatToolLoopParams 传参（第 6053-6070 行）**

将 `page_snapshot=""`、`probe_registry=None`、`dom_context_pack=""` 改为从 bridge 获取：
```python
_page_snapshot = ""
_probe_registry = None
_dom_context_pack = ""
if browser_ready:
    try:
        from ai_external_browser_bridge import get_page_snapshot, get_probe_registry, get_dom_context_pack
        _page_snapshot = get_page_snapshot()
        _probe_registry = get_probe_registry() or None
        _dom_context_pack = get_dom_context_pack()
    except Exception:
        pass

params = ChatToolLoopParams(
    message=task,
    project_name=project_name,
    current_plan=current_plan,
    history=[],
    profile=profile,
    legacy_model="",
    page_snapshot=_page_snapshot,
    probe_registry=_probe_registry,
    probe_url=url or "",
    memory_context="",
    dom_context_pack=_dom_context_pack,
    interaction_context={"url": url, "platform": platform, "enable_vision": enable_vision} if url else None,
    test_scope=task,
    embedded_session_id="",
    platform_type=platform,
    abort_event=abort_event,
    recorder=recorder,  # Phase 2 新增
)
```

**验证**：Hermes 在有头浏览器中执行，页面快照和 DOM 探测正常工作，AI 能看到真实页面控件。

---

### Phase 2：动作记录器

**目标**：新建 `ai_action_recorder.py`，从 Hermes 返回文本中提取结构化动作，按需触发视觉分析，通过 SSE `action_record` 事件推送到前端。

**新增文件**：`ai_action_recorder.py`（约 180 行）

**关键设计**：
- `ActionRecord` dataclass：action_id, action_type, target, locator, input_data, result, status, timestamp, screenshot, vision_info, raw_text
- `ActionRecorder` 类：
  - `capture_from_hermes_result(result_text: str) -> List[ActionRecord]`：用正则匹配 navigate/click/input/assert 四类动作
  - `_trigger_vision_for_records(records)`：按需触发（仅当 vision_enabled 且 records 非空），复用 `ai_vision_local.ocr_region_png`（接受 bytes）+ `_VisionCircuitBreaker` 熔断器
  - `to_case_steps() -> List[Dict]`：转换为原始步骤格式供 normalization 处理
- 视觉触发策略：**不是每个动作都做**，只在 vision_enabled 且最新动作可能需要时触发；失败静默跳过（复用熔断器）

**修改文件 1**：`ai_chat_tool_loop.py`

- **ChatToolLoopParams**（第 346-363 行）：新增字段 `recorder: Optional[Any] = None`
- **tool_call_result 事件**（第 783-786 行）：在 yield 之后，若 `params.recorder` 且 `name == "hermes_execute"`，调用 `params.recorder.capture_from_hermes_result(result_text)`，将新记录通过新事件类型 `action_records` 传出：
  ```python
  yield ("tool_call_result", {...})  # 现有
  # 新增：动作记录
  if params.recorder and name in ("hermes_execute", "openclaw_execute"):
      try:
          new_recs = params.recorder.capture_from_hermes_result(result_text)
          if new_recs:
              yield ("action_records", [{"action_type": r.action_type, "target": r.target,
                  "status": r.status, "result": r.result[:100], "has_vision": bool(r.vision_info)} for r in new_recs])
      except Exception:
          pass
  ```

**修改文件 2**：`app.py`（SSE 事件转发，第 6090-6096 行附近）

在 `tool_call_result` 处理后新增 `action_records` 事件转发：
```python
elif evt_type == "action_records":
    for rec in evt_data:  # evt_data 是 list
        yield send('action_record', **rec)
```

并在 SSE 创建 recorder（第 6053 行 params 之前）：
```python
from ai_action_recorder import ActionRecorder
recorder = ActionRecorder(vision_enabled=bool(enable_vision), platform=platform)
```

**验证**：Hermes 执行后，前端右栏"执行动作"卡片实时显示结构化动作列表。

---

### Phase 3：动作转用例管线接入

**目标**：重写 `/api/ai/actions/to-case`（app.py 第 6291-6360 行），将 naive 映射替换为 `ai_step_normalization` 837 行全管线。

**修改文件**：`app.py` 第 6291-6360 行

**新实现流程**（注意实测函数签名）：
```python
@app.route('/api/ai/actions/to-case', methods=['POST'])
@login_required
@api_error_handler
def api_ai_actions_to_case():
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
    instruction = data.get('instruction', '')
    case_url = data.get('url', '')
    platform = data.get('platform', 'web')

    # Step 1: 原始动作 → 步骤格式，写入 automation_layer 供 normalize_ai_step 读取平台
    raw_steps = []
    for action in actions:
        raw_steps.append({
            'action': action.get('action_type', action.get('type', '操作')),
            'target': action.get('target', action.get('content', '')),
            'input_value': action.get('input_data', action.get('input_value', '')),
            'description': action.get('result', ''),
            'automation_layer': platform,  # normalize_ai_step 从此字段读平台
        })

    # Step 2: 逐条规范化（复用 837 行管线）
    normalized_steps = [normalize_ai_step(s) for s in raw_steps]

    # Step 3: 平台修复（实测：无 platform 参数，就地修改，返回告警）
    warnings1 = repair_raw_ai_steps_for_platform(normalized_steps)

    # Step 4: 去重 + 验证（实测：platform 为关键字参数）
    clean_steps, warnings2 = dedupe_and_validate_ai_steps(normalized_steps, platform=platform)

    # Step 5: 构造 plan 并应用规范化（实测：无 platform 参数）
    plan = {'case_name': case_name, 'case_url': case_url or '', 'steps': clean_steps}
    plan, warnings3 = apply_step_normalization_to_plan(plan)

    all_warnings = (warnings1 or []) + (warnings2 or []) + (warnings3 or [])

    # Step 6: 选择器恢复（复用 4 层降级，如有 probe_registry）
    try:
        from ai_external_browser_bridge import get_probe_registry
        registry = get_probe_registry()
        if registry and clean_steps:
            from ai_locator_resolution import resolve_plan_steps_locators_with_snapshot
            plan = resolve_plan_steps_locators_with_snapshot(plan, registry)
    except Exception:
        pass

    # Step 7: 保存到数据库（保留现有逻辑）
    saved_to_db = False
    case_id = None
    if project_id:
        try:
            from database import get_db_connection
            conn = get_db_connection()
            cursor = conn.cursor()
            import json as _json
            cursor.execute(
                "INSERT INTO test_cases (project_id, name, steps, created_by, created_at) "
                "VALUES (?, ?, ?, ?, datetime('now'))",
                (int(project_id), case_name,
                 _json.dumps(clean_steps, ensure_ascii=False), current_user.id)
            )
            conn.commit()
            case_id = cursor.lastrowid
            conn.close()
            saved_to_db = True
        except Exception:
            pass

    # Step 8: Skill 沉淀（保留现有逻辑）
    skill_exported = False
    try:
        from hermes_skill_loop import record_execution_success
        record_execution_success(plan, case_url=case_url,
                                 instruction=instruction or case_name, outcome='ok')
        skill_exported = True
    except Exception:
        pass

    return jsonify({
        'success': True,
        'message': f'动作转用例成功（规范化 {len(clean_steps)} 步）'
                   + ('，已沉淀为 Skill' if skill_exported else ''),
        'case': {'name': case_name, 'project_id': project_id, 'steps': clean_steps},
        'case_id': case_id,
        'saved_to_db': saved_to_db,
        'skill_exported': skill_exported,
        'warnings': all_warnings,
    })
```

**关键修正**：用户提供计划中 `normalize_ai_step(step, platform_type=platform)` 和 `repair_raw_ai_steps_for_platform(steps, platform)` 的签名是**错误的**。实测 normalize_ai_step 无 platform 参数，repair_raw_ai_steps_for_platform 也无 platform 参数。本计划已按实测签名修正。

**验证**：动作转用例后，步骤格式与平台 runner 兼容（三平台 action 映射正确，选择器经 4 层降级）。

---

### Phase 4：视觉/截屏 Stub 实现

**目标**：实现 3 个 stub 接口，复用 `ai_vision_local` 和 `ai_external_browser_bridge`。

**修改文件**：`app.py` 第 6363-6383 行

**`/api/ai/vision/capture`（POST）**：
```python
@app.route('/api/ai/vision/capture', methods=['POST'])
@login_required
@api_error_handler
def api_ai_vision_capture():
    data = request.get_json(silent=True) or {}
    source = data.get('source', 'browser')  # browser / screen
    if source == 'browser':
        from ai_external_browser_bridge import capture_screenshot
        png = capture_screenshot()
    else:
        import mss
        from mss.tools import to_png
        with mss.mss() as sct:
            monitor = sct.monitors[1]
            shot = sct.grab(monitor)
            png = to_png(shot.rgb, shot.size)
    if not png:
        return jsonify({'success': False, 'error': '截图失败'}), 500
    import tempfile, os, uuid
    filename = f"vision_{uuid.uuid4().hex[:8]}.png"
    filepath = os.path.join(tempfile.gettempdir(), filename)
    with open(filepath, 'wb') as f:
        f.write(png)
    return jsonify({'success': True, 'screenshot_path': filepath, 'size': len(png)})
```

**`/api/ai/vision/snapshot`（GET）**：
```python
@app.route('/api/ai/vision/snapshot', methods=['GET'])
@login_required
@api_error_handler
def api_ai_vision_snapshot():
    from ai_external_browser_bridge import capture_screenshot
    png = capture_screenshot()
    if not png:
        return jsonify({'success': False, 'error': '无可用截图'}), 404
    ocr_text = ""
    try:
        from ai_vision_local import ocr_region_png
        ocr_text = ocr_region_png(png)  # 实测接受 bytes
    except Exception:
        pass
    return jsonify({'success': True, 'ocr_text': ocr_text[:2000], 'screenshot_size': len(png)})
```

**`/api/ai/screen-share/toggle`（POST）**：
```python
@app.route('/api/ai/screen-share/toggle', methods=['POST'])
@login_required
@api_error_handler
def api_ai_screen_share_toggle():
    data = request.get_json(silent=True) or {}
    enabled = data.get('enabled', False)
    # 共享屏幕状态记录在 bridge 模块全局变量
    import ai_external_browser_bridge as bridge
    bridge._screen_share_active = bool(enabled)
    bridge._screen_share_interval = int(data.get('interval', 3))
    return jsonify({
        'success': True,
        'enabled': enabled,
        'message': '共享屏幕已开启' if enabled else '共享屏幕已关闭',
    })
```

**配套修改**：`ai_external_browser_bridge.py` 顶部新增两个模块级变量：
```python
_screen_share_active = False
_screen_share_interval = 3
```

**隐私控制**：开启共享屏幕时前端已有确认（现有 `showScreenShareOverlay`），截图不持久化到磁盘（仅 /capture 接口按需存临时文件）。

**验证**：视觉分析按需触发，不阻塞主执行流；OCR 返回中文+英文文本。

---

### Phase 5：前端事件处理补齐

**目标**：前端右栏 4 卡片和 think/action/case_step/done/error 事件已存在，仅需补齐 `action_record` 和 `vision_result` 事件处理。

**修改文件**：`templates/ai_test.html` 第 1439-1446 行附近（handleStreamEvent 函数内）

在 `case_step` 处理之后新增：
```javascript
} else if (ev.type === 'action_record') {
    // 细粒度动作记录（来自 ActionRecorder）
    AI_TEST_STATE.actions.push({
        type: ev.action_type || 'action',
        content: ev.target || '未知',
        status: ev.status || 'success',
        has_vision: ev.has_vision || false
    });
    updateActionCard();
    updateReportCard();
} else if (ev.type === 'vision_result') {
    // 视觉分析结果
    var ocrPreview = (ev.ocr_text || '').slice(0, 100);
    if (ocrPreview) {
        addChatMessage('🔍 视觉识别: ' + ocrPreview, false);
        saveChatHistory();
    }
}
```

**桌面壳约束处理**：
- 现有 CSS 已使用 `html.dark` 限定深色模式，与 frameless shell 兼容
- 修改后更新 CSS 版本号：`ai_hub.css?v=1` → `?v=2`（第 4 行）
- 新增样式（若有）放在页面 `<style>` 块中，遵循现有 `rgba()` + `backdrop-filter` 玻璃态风格

**验证**：三列布局在 frameless 模式下正常显示；action_record 事件实时更新右栏执行动作卡片。

---

## 假设与决策

1. **完全替换 [INIT]**（用户确认）：移除 `hermes_client.execute_user_instruction("[INIT]...")`，统一用 `ensure_browser()`。失败时降级为无快照模式（Hermes 独立浏览器），不阻断任务执行。

2. **normalize_ai_step 签名修正**：用户提供计划中 `normalize_ai_step(step, platform_type=platform)` 是错误的。实测 `normalize_ai_step(step: dict)` 无 platform 参数，从 `step["automation_layer"]` 读取平台。本计划通过在 raw_steps 中写入 `automation_layer` 字段解决。

3. **ocr_region_png 接受 bytes**：实测 `ocr_region_png(image_bytes: bytes)`，不是文件路径。Phase 4 直接传 PNG bytes，无需写临时文件。

4. **recorder 通过 ChatToolLoopParams 传递**：recorder 在 app.py SSE 中创建，通过 params.recorder 传入工具循环，在 tool_call_result 后调用。不独立成模块级单例，每次任务独立。

5. **视觉按需触发**：ActionRecorder 只在 `vision_enabled=True` 时触发 OCR，且复用 `_VisionCircuitBreaker` 熔断器（threshold=3, recovery=60s），失败静默跳过，不阻塞主流程。

6. **abort 中断限制**：`execute_user_instruction` 仍同步阻塞，abort 只能在工具调用轮次之间生效（Phase 0.4 已知限制，本计划不扩展）。

7. **不重复造轮子**：所有视觉、规范化、选择器恢复、DOM 探测、记忆、Skill 沉淀均复用现有模块，仅新增 bridge（已存在）、recorder 两个文件。

---

## 验证步骤

### 语法检查
- `python -m py_compile app.py`
- `python -m py_compile ai_action_recorder.py`
- `python -m py_compile ai_external_browser_bridge.py`
- `python -m py_compile ai_chat_tool_loop.py`

### 功能验证
1. **Phase 1.2**：访问 `/api/ai/task/execute`，SSE 应推送 `think` 事件含"正在启动有头浏览器"→"浏览器已启动，CDP 已同步到 Hermes"；有头 Chromium 窗口可见。
2. **Phase 2**：执行浏览器任务后，右栏"执行动作"卡片显示 navigate/click/input 等结构化动作。
3. **Phase 3**：点击"动作转用例"按钮，返回的 steps 包含 `automation_layer` 字段，action 为三平台规范值（如 web 的 navigate/click/input），非原始 action_type 字符串。
4. **Phase 4**：`GET /api/ai/vision/snapshot` 返回 `ocr_text` 非空；`POST /api/ai/vision/capture` 返回 `screenshot_path` 指向真实 PNG 文件。
5. **Phase 5**：前端右栏实时更新；`action_record` 事件触发 `updateActionCard()`；无 CSS 版本冲突。

### 回归验证
- 停止按钮仍生效（Phase 0 已修，recorder 不影响 abort 链路）
- 乱码已修复（Phase 0 已修，recorder 不经过 hermes_gateway_client）
- Hermes 不可用时返回明确错误（Phase 0 已修）

---

## 实施顺序与依赖

```
Phase 1.2（桥接集成）  ← 独立，先做
    ↓
Phase 2（动作记录器）   ← 依赖 Phase 1.2 的 browser_ready（视觉截图需要 bridge）
    ↓
Phase 3（动作转用例）   ← 依赖 Phase 2 的 recorder.records（动作来源）
    ↓
Phase 4（视觉 stub）    ← 依赖 Phase 1.2 的 bridge（capture_screenshot）
    ↓
Phase 5（前端事件）     ← 依赖 Phase 2/4 的新事件类型
```

每个 Phase 完成后立即语法检查，全部完成后端到端验证。
