# AI 自主测试模块深度优化整改计划

## 📊 问题诊断总结

基于对代码库的全面审查和用户反馈，识别出以下核心问题：

### 问题 1：执行时出现黑色区域
**根因分析**：
- 屏幕捕获时序问题：浏览器启动后立即捕获，此时窗口可能尚未完成渲染（黑屏/过渡帧）
- `capture_for_observation()` 优先截取前台窗口，若 Testory 自身窗口抢占焦点则截到错误区域
- 缺少画面有效性校验：截到黑屏后未重试，直接送入 OCR 导致全为乱码

**涉及文件**：
- `screen_tools.py` — 屏幕捕获核心
- `windows_desktop_tools.py` — `capture_for_observation()` 调用链
- `ai_chat_tool_loop.py` — 工具循环中的截图触发时机

### 问题 2：元素识别能力差
**根因分析**：
- UIA（UI Automation）覆盖不足：许多应用（Electron、Qt、Chrome）的控件 UIA 树不完整
- OCR 识别质量不稳定：依赖 PaddleOCR/Tesseract 引擎，对混合中英文/小号字/艺术字识别率低
- 缺少视觉大模型（VLM）辅助：当前没有利用视觉 grounding 能力来"看"屏幕
- 单一策略 fallback 不够智能：OCR 失败后退化为坐标点击，但没有视觉验证
- 元素描述匹配过于依赖精确文本匹配，缺少语义理解

**涉及文件**：
- `desktop_ocr.py` — OCR 引擎核心
- `desktop_uia_core.py` — UIA 元素查找
- `desktop_hybrid_locator.py` — 混合定位
- `desktop_visual_engine.py` — 视觉引擎
- `windows_desktop_tools.py` — `windows_click_element()` 主入口

### 问题 3：共享屏幕功能形同虚设
**根因分析**：
- 后端仅设置 `_screen_share_active = True`，无任何消费代码
- `ScreenObserver` 已废弃，`should_capture()` 恒返回 False
- 工具循环中未注入屏幕观察逻辑
- 无周期性截图+分析机制
- 前端 toggle 存在但不影响实际执行

**涉及文件**：
- `ai_screen_observer.py` — 已废弃的观察者
- `ai_external_browser_bridge.py` — 屏幕共享状态
- `ai_chat_tool_loop.py` — 工具循环参数 `allow_screen_tools`
- `app.py` — `/api/ai/screen-share/toggle`

---

## 🎯 优化目标

参考业界领先产品（Playwright、Appium、Microsoft Power Automate Desktop、OpenDevin、SWE-Agent、Google ADK），实现：

1. **零黑屏启动**：浏览器/应用启动后画面捕获正确率 ≥ 99%
2. **多模态元素定位**：UIA + OCR + VLM 视觉 grounding + DOM 四模融合，元素识别成功率 ≥ 90%
3. **真正可用的共享屏幕**：实时视觉反馈，AI 能"看到"屏幕内容
4. **智能 Agent 工作流**：自动选择最佳识别策略，具备自恢复能力

---

## 🏗️ 优化方案详细设计

### 模块 1：屏幕捕获可靠性修复（解决黑屏问题）

#### 1.1 新增画面有效性校验层

**文件**：`screen_tools.py`

**修改内容**：
```python
def _is_valid_frame(png_bytes: bytes, min_edge_diff: float = 5.0) -> bool:
    """检测截图是否为有效画面（非黑屏/纯色）。"""
    # 转换为 numpy 数组，计算边缘差异
    # 如果边缘差异低于阈值，判定为无效帧
    ...

def capture_for_observation(...):
    # 原有捕获逻辑后增加：
    for attempt in range(3):
        png = ...  # 原捕获
        if png and _is_valid_frame(png):
            return png, meta
        time.sleep(0.5)  # 等待画面稳定
    # 3次都失败则返回最后一帧，但标记为低置信度
    return png, {**meta, "low_confidence": True}
```

#### 1.2 窗口捕获智能选择

**文件**：`screen_tools.py`

**修改内容**：`capture_foreground_window_png()` 增加窗口过滤逻辑
- 排除 Testory 自身窗口
- 排除最小化/隐藏窗口
- 优先选择用户实际操作的窗口（通过进程名/标题关键词匹配）

#### 1.3 启动后画面稳定等待

**文件**：`windows_desktop_tools.py`

**修改内容**：`_windows_launch_app_impl()` 在等待窗口出现后增加画面稳定检查
- 连续 2 次截图哈希不同才认为画面已稳定
- 或者等待至少 0.5 秒的无变化期

---

### 模块 2：多模态元素识别引擎（核心改造）

#### 2.1 新增统一元素定位器 `UnifiedElementLocator`

**新文件**：`unified_element_locator.py`

**设计思路**：借鉴 Playwright 的 Locator 设计 + Appium 的多策略查找 + SWE-Agent 的视觉 grounding

```python
class UnifiedElementLocator:
    """统一元素定位器：四模融合 + 置信度评估。
    
    查找顺序（优先级从高到低）：
    1. Web DOM 选择器（id/css/xpath/text） — 仅 Web 平台
    2. UIA 树遍历（accessibility name/class name） — 桌面平台
    3. OCR 文本匹配（PaddleOCR/Tesseract） — 全平台
    4. VLM 视觉 grounding（截图+多模态理解） — 全平台兜底
    """
    
    def find_element(self, description: str, context: ElementContext) -> ElementResult:
        """
        Args:
            description: 元素描述（如"登录按钮"、"搜索框"）
            context: 上下文（当前平台、截图、已找到的元素等）
        
        Returns:
            ElementResult: 包含坐标、置信度、来源策略等信息
        """
        strategies = self._get_strategies(context.platform)
        
        for strategy in strategies:
            result = strategy.find(description, context)
            if result.confidence >= strategy.threshold:
                return result
        
        # 所有策略都失败时，组合多策略候选
        return self._ensemble_results(description, context)
```

#### 2.2 四大定位策略实现

**策略 1：Web DOM 定位器**（复用现有 Playwright 能力）
- CSS 选择器查找
- XPath 查找
- Text content 匹配
- Role + accessibility name 查找
- 自动 iframe 穿透

**策略 2：UIA 树遍历增强**（改造 `desktop_uia_core.py`）
- 增加深度递归搜索（当前限制过严）
- 支持模糊匹配 accessibility name
- 增加 ControlType + Name 组合查找
- 支持 XPath-like 的 UIA 树路径

**策略 3：OCR 智能匹配**（改造 `desktop_ocr.py`）
- 增加关键词提取和语义扩展
  - "登录按钮" → 扩展为 ["登录", "确定", "Login", "Sign in", "Submit"]
  - "搜索框" → 扩展为 ["搜索", "Search", "查找", "Find"]
- 增加位置先验（搜索框通常在左上、登录按钮通常在右下）
- 支持部分匹配和近义词匹配

**策略 4：VLM 视觉 Grounding**（新增）
- 截图后发送给 VLM（如 qwen-vl、llava、gpt-4o）
- 提示词："在这个界面中找到「登录按钮」，返回它的大致位置"
- VLM 返回坐标后，映射回屏幕坐标
- 作为兜底策略，只在其他策略失败时调用

#### 2.3 元素置信度评估与自恢复

**文件**：新 `element_confidence.py`

```python
class ElementConfidence:
    """元素置信度评估与自恢复策略。"""
    
    CONFIDENCE_THRESHOLDS = {
        "dom_exact": 1.0,      # DOM 精确匹配
        "uia_exact": 0.95,     # UIA 精确匹配
        "dom_fuzzy": 0.8,      # DOM 模糊匹配
        "uia_fuzzy": 0.75,     # UIA 模糊匹配
        "ocr_exact": 0.7,      # OCR 精确匹配
        "ocr_fuzzy": 0.55,     # OCR 模糊匹配
        "vlm_grounding": 0.5,  # VLM 视觉定位
    }
    
    def should_retry(self, result: ElementResult) -> bool:
        """判断是否需要重试（换策略或换描述）。"""
        if result.confidence < 0.5:
            return True
        if result.source == "ocr" and result.confidence < 0.6:
            return True  # OCR 低置信度时尝试 VLM
        return False
    
    def generate_retry_description(self, original: str, failed_result: ElementResult) -> str:
        """基于失败结果生成重试用的改进描述。"""
        # 从 OCR 候选中提取实际文本作为新描述
        # 或从 UIA 树中提取实际控件名
        ...
```

---

### 模块 3：真正可用的共享屏幕系统

#### 3.1 重构屏幕观察者

**新文件**：`screen_observer_v2.py`（替代已废弃的 `ai_screen_observer.py`）

```python
class ScreenObserverV2:
    """实时屏幕观察者：周期性截图 + 按需视觉分析。
    
    工作模式：
    1. 被动模式（默认）：仅在 Agent 调用 get_screen_text/get_screen_description 时触发
    2. 主动模式（共享屏幕开启时）：每 N 秒自动截图 + 轻量分析
    3. 事件驱动模式：在工具调用失败时立即截图分析
    """
    
    def __init__(self, interval_sec: float = 3.0, enable_vlm: bool = False):
        self._interval = interval_sec
        self._enable_vlm = enable_vlm
        self._running = False
        self._thread = None
        self._last_frame = None
        self._last_analysis = None
        self._frame_count = 0
    
    def start(self):
        """启动后台屏幕观察线程。"""
        ...
    
    def stop(self):
        """停止后台屏幕观察。"""
        ...
    
    def get_latest_frame(self) -> Optional[bytes]:
        """获取最新截图。"""
        ...
    
    def get_latest_analysis(self) -> Optional[Dict]:
        """获取最新分析结果（OCR + 可选 VLM）。"""
        ...
    
    def on_tool_failure(self, tool_name: str, error: str) -> Dict:
        """工具失败时立即触发屏幕分析，返回给 Agent 作为上下文。"""
        ...
```

#### 3.2 工具循环中集成屏幕观察

**文件**：`ai_chat_tool_loop.py`

**修改内容**：
- 在 `ChatToolLoopParams` 中增加 `screen_observer` 参数
- 工具调用失败时自动触发 `screen_observer.on_tool_failure()`
- 将屏幕分析结果注入下一轮推理的 messages 中
- Agent 可以在 reasoning 中"看到"当前屏幕状态

#### 3.3 屏幕共享 API 真正实现

**文件**：`app.py`

**修改**：`/api/ai/screen-share/toggle` 
- 开启时创建 `ScreenObserverV2` 实例
- 关闭时停止观察线程
- 增加状态查询端点 `/api/ai/screen-share/status`
- 增加获取最新截图端点 `/api/ai/screen-share/latest-frame`

---

### 模块 4：Agent 推理与执行流程优化

#### 4.1 元素获取智能路由

**文件**：`ai_chat_tool_loop.py`

**修改内容**：在 `windows_click_element` 调用前增加智能路由
```python
def _prepare_element_context(params, name, args):
    """为元素操作准备多模态上下文。"""
    if name in ("windows_click_element", "windows_type_text"):
        # 1. 检查是否有缓存的屏幕观察结果
        obs = params.screen_observer.get_latest_analysis()
        
        # 2. 检查 OCR 缓存是否仍然有效
        if obs and obs.get("frame_hash") == params.last_frame_hash:
            # 使用缓存的 OCR 结果作为 _ocr_hints
            args["_ocr_hints"] = obs.get("texts", [])
            args["_ocr_blocks"] = obs.get("blocks", [])
        
        # 3. 如果缓存过期或为空，主动观察一次
        if not args.get("_ocr_hints"):
            from screen_tools import get_screen_text
            screen_result = get_screen_text()
            args["_ocr_hints"] = screen_result.get("texts", [])
            args["_ocr_blocks"] = screen_result.get("blocks", [])
        
        return args
```

#### 4.2 失败自恢复循环

**文件**：`ai_chat_tool_loop.py`

**修改内容**：增加失败恢复逻辑
```python
# 在工具执行失败时：
if not _desktop_tool_succeeded(result_text):
    # 1. 触发屏幕观察，获取失败时的画面
    failure_context = params.screen_observer.on_tool_failure(name, result_text)
    
    # 2. 尝试从失败画面中提取新的候选描述
    if failure_context.get("ocr_texts"):
        # 生成新的候选描述
        new_desc = _generate_retry_description(args["description"], failure_context)
        if new_desc != args["description"]:
            # 使用新描述重试一次
            args["description"] = new_desc
            result = _dispatch_desktop_or_screen_tool(name, args)
```

#### 4.3 增强工具描述注入（Prompt Engineering）

**文件**：`agent_tool_registry.py` 和 `ai_chat_tool_loop.py`

**修改内容**：更新工具描述，引导 Agent 正确使用
```
"windows_click_element": {
    "description": """点击桌面上的元素。系统会自动通过 UIA 树、OCR 文本、视觉模型等多模态方式定位元素。
    
    最佳实践：
    1. description 只写控件上的实际文字（如「登录」「确定」「搜索」），不要写完整句子
    2. 如果第一次点击失败，系统会自动从屏幕中识别可能的替代元素并重试
    3. 对于搜索框/输入框，优先使用 windows_type_text 让系统自动定位
    """,
    "best_practices": [
        "✅ 好: windows_click_element('登录')",
        "✅ 好: windows_click_element('确定')",
        "✅ 好: windows_click_element('搜索')",
        "❌ 差: windows_click_element('点击页面上的登录按钮')",
        "❌ 差: windows_click_element('那个蓝色的按钮')",
    ]
}
```

---

### 模块 5：视觉基础设施增强

#### 5.1 截图质量提升

**文件**：`screen_tools.py`

**修改内容**：
- 增加 DPI 感知（高 DPI 屏幕截图模糊问题）
- 增加截图预放大（小字识别率提升）
- 增加多显示器支持
- 增加截图区域智能裁剪（排除 Testory 自身窗口覆盖区域）

#### 5.2 OCR 引擎优化

**文件**：`desktop_ocr.py`

**修改内容**：
- 增加 PaddleOCR 模型热加载（首次调用慢的问题）
- 增加文本行合并策略优化
- 增加置信度校准
- 增加中文/英文混排场景的特殊处理

#### 5.3 VLM 集成接口

**新文件**：`vlm_grounding.py`

```python
class VLMGrounding:
    """视觉语言模型元素定位接口。
    
    支持的后端：
    - Ollama 本地模型（qwen2.5-vl、llava）
    - 云端 API（qwen-vl、gpt-4o）
    - 预留其他 VLM 后端扩展
    """
    
    def find_element(self, screenshot: bytes, description: str) -> Optional[Dict]:
        """
        提示词模板：
        "这是一个桌面应用的截图。请找到描述为「{description}」的元素，
         返回它在图片中的大致位置（左上角和右下角的相对坐标，用 0-100 的百分比表示）。
         如果找不到，返回 null。"
        """
        ...
    
    def should_use_vlm(self, confidence: float, platform: str) -> bool:
        """判断是否应该使用 VLM（成本较高，仅在必要时使用）。"""
        return confidence < 0.6 and platform in ("desktop", "mobile")
```

---

## 📝 实施步骤

### Phase 1：黑屏修复（优先级：最高）
1. 在 `screen_tools.py` 中实现 `_is_valid_frame()` 画面有效性检测
2. 修改 `capture_for_observation()` 增加重试和有效性校验
3. 在 `windows_desktop_tools.py` 的启动流程中增加画面稳定等待
4. **验证**：启动浏览器后截图不再出现黑屏

### Phase 2：多模态元素定位器（优先级：高）
1. 创建 `unified_element_locator.py`，实现四模融合框架
2. 增强 `desktop_uia_core.py` 的 UIA 树遍历能力
3. 增强 `desktop_ocr.py` 的 OCR 语义扩展和位置先验
4. 实现 `element_confidence.py` 置信度评估
5. **验证**：元素识别成功率显著提升

### Phase 3：共享屏幕系统（优先级：高）
1. 创建 `screen_observer_v2.py` 替代废弃的 `ScreenObserver`
2. 修改 `ai_chat_tool_loop.py` 集成屏幕观察
3. 实现 `vlm_grounding.py` VLM 视觉 grounding 接口
4. 修改 `app.py` 实现真正的屏幕共享 API
5. **验证**：开启共享屏幕后 AI 能实时感知屏幕变化

### Phase 4：Agent 流程优化（优先级：中）
1. 实现 `_prepare_element_context()` 智能路由
2. 实现失败自恢复循环
3. 更新工具描述和 Prompt Engineering
4. **验证**：Agent 自主操作成功率提升

### Phase 5：基础设施增强（优先级：中）
1. 高 DPI 支持和截图质量提升
2. OCR 引擎优化
3. 多显示器支持
4. **验证**：各分辨率显示器下截图质量一致

---

## ⚠️ 风险与注意事项

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| VLM 调用延迟 | 每次调用增加 2-5 秒 | 仅在其他策略失败时调用；增加本地缓存 |
| PaddleOCR 首次加载慢 | 首次 OCR 调用 5-10 秒 | 预热机制；后台预加载 |
| 多显示器坐标映射 | 截图与实际点击位置偏移 | 统一使用物理坐标；增加坐标校准 |
| 隐私合规 | 共享屏幕可能捕获敏感信息 | 增加隐私过滤层；本地处理优先 |
| 内存占用增加 | 多模态同时运行占用大 | 懒加载；按需初始化 |
| 兼容性 | 不同 Windows 版本 UIA 支持不同 | 增加降级路径；优先 OCR/VLM |

---

## 📚 参考的业界方案

| 产品/项目 | 借鉴点 |
|-----------|--------|
| **Playwright** | Locator 链式调用、自动等待、iframe 穿透、元素状态检查 |
| **Appium** | 多平台统一元素查找、Appium Inspector 的元素树可视化 |
| **Microsoft Power Automate Desktop** | UIA + OCR + 图像识别的多策略融合 |
| **OpenDevin/SWE-Agent** | VLM 驱动的视觉 grounding、截图→分析→操作循环 |
| **Google ADK** | Agent 工具动态路由、上下文感知的工具选择 |
| **Browser-use** | AI 驱动的浏览器操作、LLM 直接理解页面结构 |
| **CrewAI/AutoGen** | 多 Agent 协作、反思与自恢复机制 |

---

## ✅ 验收标准

1. **黑屏问题**：连续 10 次浏览器启动，截图有效率 100%
2. **元素识别**：常见桌面应用元素识别成功率 ≥ 90%（UIA 可访问的 ≥ 95%，纯视觉的 ≥ 80%）
3. **共享屏幕**：开启后 AI 能在 3 秒内感知屏幕变化并作出反应
4. **自恢复**：单次元素操作失败后，自动重试成功率 ≥ 70%
5. **响应速度**：单次元素定位（不含 VLM）耗时 ≤ 500ms
6. **跨平台**：Web/Desktop/Mobile 三端元素定位 API 统一
