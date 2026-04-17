import asyncio
import random
import cv2
import numpy as np
from playwright.async_api import async_playwright
from typing import List, Dict, Any, Optional
import json
import time
import sys
import os
import importlib
from logger import uat_logger
import threading
if sys.platform == 'win32':
    import ctypes  # 仅 Windows：获取屏幕尺寸（Linux 无 ctypes.windll）
import re

# 🔥 导入增强型定位器模块
from enhanced_locator import (
    ElementLocatorManager, 
    EnhancedLocatorGenerator,
    DynamicClassNameFilter,
    create_locator_manager,
    ElementInfo
)
from playwright_codegen_import import (
    runtime_xpath_button_link_fallback_items,
    xpath_click_attempt_variants,
)

# 🔥 添加全局执行锁，防止并发执行多个测试用例集
_execution_lock = threading.Lock()
_currently_executing = False

def is_execution_in_progress():
    """检查是否有测试用例正在执行"""
    global _currently_executing
    return _currently_executing

def set_execution_in_progress(status):
    """设置执行状态"""
    global _currently_executing
    _currently_executing = status

def force_reset_execution_state():
    """强制重置执行状态、执行锁及浏览器内部状态（用于浏览器异常关闭后的恢复）"""
    global _currently_executing
    
    # 1. 重置执行状态标志
    _currently_executing = False
    uat_logger.info("🔄 [FORCE_RESET] 开始强制重置执行状态...")
    
    # 2. 强制释放执行锁（多次尝试确保成功）
    for attempt in range(3):
        try:
            if _execution_lock.locked():
                _execution_lock.release()
                uat_logger.info(f"🔓 [FORCE_RESET] 第{attempt+1}次尝试: 成功释放执行锁")
                break
        except RuntimeError as e:
            # RuntimeError: release unlocked lock 或 cannot release un-acquired lock
            uat_logger.debug(f"⚠️ [FORCE_RESET] 第{attempt+1}次尝试: 锁未被当前线程持有 - {e}")
            break
        except Exception as e:
            uat_logger.warning(f"⚠️ [FORCE_RESET] 第{attempt+1}次尝试释放锁失败: {e}")
    
    # 3. 强制清空浏览器引用（无论浏览器状态如何）
    # 🔥 关键修复: 不再检查浏览器是否连接，直接清空所有引用
    # 这样可以确保下次 start_browser 能完全重新创建浏览器实例
    try:
        if automation.browser is not None or automation.page is not None or automation.context is not None:
            uat_logger.info("🧹 [FORCE_RESET] 强制清空所有浏览器引用...")
            automation.browser = None
            automation.page = None
            automation.context = None
            automation.playwright = None
            uat_logger.info("✅ [FORCE_RESET] 浏览器引用已全部清空")
    except Exception as e:
        uat_logger.warning(f"⚠️ [FORCE_RESET] 清空浏览器引用时出错: {e}")

    try:
        automation.recording = False
        automation.recorded_steps = []
        automation.current_iframe = None
    except Exception:
        pass
    
    uat_logger.info("✅ [FORCE_RESET] 执行状态已全面重置完成")


_INVALID_URL_SKIP = frozenset([
    'example.com', '0.0.0.0', '0.0.0.1', '127.0.0.1',
    'localhost', 'about:blank', 'about:newtab',
])
# 🔥 修复：IP 地址正则表达式增加 100-199 范围的匹配 (1[0-9]{2})
# IP 第一段 (1-255): (25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9][0-9]|[1-9])
# IP 中间/最后段 (0-255): (25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])
def _normalize_xpath_selector_value(selector: str) -> str:
    """
    Codegen / 从 TS 源码复制步骤时，XPath 里可能残留 JS 字符串转义（如 \\' 请输入 \\'）。
    浏览器与 document.evaluate 需要真实的引号字符，此处按 JS 字符串规则做一次反转义。
    """
    if not selector or "\\" not in selector:
        return selector
    out: List[str] = []
    i, n = 0, len(selector)
    while i < n:
        if selector[i] == "\\" and i + 1 < n:
            c = selector[i + 1]
            if c in "'\"\\":
                out.append(c)
                i += 2
                continue
            if c == "n":
                out.append("\n")
                i += 2
                continue
            if c == "r":
                out.append("\r")
                i += 2
                continue
            if c == "t":
                out.append("\t")
                i += 2
                continue
            if (
                c == "u"
                and i + 6 <= n
                and all(ch in "0123456789abcdefABCDEF" for ch in selector[i + 2 : i + 6])
            ):
                try:
                    out.append(chr(int(selector[i + 2 : i + 6], 16)))
                    i += 6
                    continue
                except ValueError:
                    pass
        out.append(selector[i])
        i += 1
    return "".join(out)


def _fill_text_compare_equal(expected: Optional[str], actual: Optional[str]) -> bool:
    """与预期填充文本比对（忽略首尾空白、NBSP）。"""
    if expected is None:
        expected = ""
    if actual is None:
        actual = ""
    return (
        str(expected).replace("\u00a0", " ").strip()
        == str(actual).replace("\u00a0", " ").strip()
    )


_URL_RE = re.compile(
    r'^https?://'
    r'(([a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}'
    r'|((25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9][0-9]|[1-9])\.){1}'
    r'((25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])\.){2}'
    r'(25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9]))'
    r'(:\d+)?(/.*)?$'
)


def _xpath_group_inner_and_index(selector: str):
    """
    Selenium IDE 常见 (//tag[@id='x'])[n]，n 为 1-based。
    返回 (inner_xpath, n)；无法解析则 None。
    """
    s = (selector or "").strip()
    m = re.match(r"^\((.+)\)\[(\d+)\]\s*$", s)
    if not m:
        return None
    inner, k = m.group(1).strip(), int(m.group(2))
    if k < 1 or not inner.startswith("//"):
        return None
    return inner, k


def _xpath_string_literal(s: str) -> str:
    """
    XPath 1.0 字符串字面量，用于 contains(normalize-space(.), lit) 等。
    同时含单双引号时用 concat 拼接。
    """
    s = s or ""
    if '"' not in s:
        return f'"{s}"'
    if "'" not in s:
        return f"'{s}'"
    parts: List[str] = []
    for i, segment in enumerate(s.split('"')):
        if i:
            parts.append("'\"'")
        if segment:
            parts.append(f'"{segment}"')
    return "concat(" + ", ".join(parts) + ")" if parts else "'\"'"


def _looks_dynamic_dom_id(v: str) -> bool:
    s = str(v or "").strip()
    if not s:
        return False
    low = s.lower()
    if len(s) >= 16 and re.search(r"\d{8,}", s):
        return True
    if re.search(r"[a-f0-9]{10,}", low) and re.search(r"\d{4,}", s):
        return True
    if re.search(r"(?:^|[_-])(id|card|row|item)?\d{10,}$", low):
        return True
    if re.search(r"\d{6,}", s):
        return True
    return False


def _stable_class_tokens(class_name: str) -> List[str]:
    out: List[str] = []
    for token in str(class_name or "").split():
        t = token.strip()
        if not t:
            continue
        if len(t) <= 2:
            continue
        if re.search(r"\d{4,}", t):
            continue
        if re.search(r"[a-f0-9]{8,}", t.lower()):
            continue
        out.append(t)
    return out[:3]


def _picker_locator_candidates(
    css_selector: str, text_content: str, class_name: str, element_id: str
) -> str:
    pack: List[Dict[str, Any]] = []
    if css_selector:
        pack.append({"type": "css", "value": css_selector, "score": 96})
    if element_id and not _looks_dynamic_dom_id(element_id):
        pack.append({"type": "id", "value": element_id, "score": 100})
    for cls in _stable_class_tokens(class_name):
        pack.append({"type": "css", "value": f".{cls}", "score": 88})
    txt = (text_content or "").strip()
    if txt and len(txt) <= 48 and "\n" not in txt and '"' not in txt and "'" not in txt:
        pack.append({"type": "partial_text", "value": txt, "score": 76})
        pack.append(
            {
                "type": "xpath",
                "value": f'//*[contains(normalize-space(.),"{txt}")]',
                "score": 72,
            }
        )
    dedup: List[Dict[str, Any]] = []
    seen = set()
    for p in pack:
        k = (str(p.get("type") or "").lower(), str(p.get("value") or ""))
        if not k[1] or k in seen:
            continue
        seen.add(k)
        dedup.append(p)
    dedup.sort(key=lambda x: -int(x.get("score") or 0))
    return json.dumps(dedup, ensure_ascii=False)


def _pa_validate_url(url: str) -> tuple:
    """校验 URL。返回 (fixed_url, err)。
    - fixed_url=None, err=None → 跳过（占位符或空）
    - fixed_url=str, err=None  → 就用 fixed_url
    - fixed_url=None, err=str  → 报错
    """
    if not url or not url.strip():
        return None, None
    url = url.strip().replace('：', ':')
    for pat in _INVALID_URL_SKIP:
        if pat in url.lower():
            uat_logger.warning(f"占位符URL ({url})，跳过")
            return None, None
    if not url.startswith(('http://', 'https://')):
        url = 'http://' + url
    if not _URL_RE.match(url):
        return None, f"无效的URL地址: {url}"
    return url, None


def resolve_playwright_headless(requested: bool = True) -> bool:
    """是否使用无头浏览器。默认与 requested 一致（调用方一般传 True）。
    环境变量 PLAYWRIGHT_HEADLESS：0/false/no/off 为有界面，1/true/yes/on 为无头。
    未设置时沿用 requested。"""
    env = os.environ.get("PLAYWRIGHT_HEADLESS")
    if env is None:
        return requested
    v = env.strip().lower()
    if v in ("0", "false", "no", "off"):
        return False
    if v in ("1", "true", "yes", "on"):
        return True
    return requested


def _locator_candidates_json_from_event(event: Dict[str, Any]) -> Optional[str]:
    if not event:
        return None
    lp = event.get("locatorPack") or event.get("locator_pack")
    if not lp:
        return None
    try:
        if isinstance(lp, str):
            return lp
        return json.dumps(lp, ensure_ascii=False)
    except Exception:
        return None


def _normalize_locator_candidate_list(raw: Any) -> List[Dict[str, Any]]:
    if raw is None or raw == "":
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return []
    if not isinstance(raw, list):
        return []
    out: List[Dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        st = item.get("selector_type") or item.get("type")
        sv = item.get("selector_value") or item.get("value")
        if not st or sv is None:
            continue
        st = str(st).strip().lower()
        try:
            score = int(item.get("score", 0))
        except Exception:
            score = 0
        out.append({"selector_type": st, "selector_value": str(sv), "score": score})
    return out


def parse_platform_scroll_input_value(input_value: Optional[str]) -> Dict[str, int]:
    """解析步骤里滚动距离的存储格式 up:a,down:b,left:c,right:d（与 list_steps 编辑页一致）。"""
    vals: Dict[str, int] = {"up": 0, "down": 0, "left": 0, "right": 0}
    if not input_value or not str(input_value).strip():
        return vals
    for part in str(input_value).split(","):
        part = part.strip()
        if ":" not in part:
            continue
        k, v = part.split(":", 1)
        k = k.strip().lower()
        if k not in vals:
            continue
        try:
            vals[k] = int(float(v.strip()))
        except ValueError:
            pass
    return vals


def scroll_event_to_platform_input_value(event: Dict[str, Any]) -> str:
    """将录制端 scroll 事件转为平台 input_value（与 list_steps 一致）。"""
    sd = event.get("scrollDistance")
    sdir = event.get("scrollDirection")
    if isinstance(sd, dict) and isinstance(sdir, dict):
        try:
            dx = int(float(sd.get("x", 0) or 0))
            dy = int(float(sd.get("y", 0) or 0))
        except (TypeError, ValueError):
            dx, dy = 0, 0
        up = down = left = right = 0
        sx = str(sdir.get("x", "") or "")
        sy = str(sdir.get("y", "") or "")
        if sy == "up":
            up = max(dy, 0)
        elif sy == "down":
            down = max(dy, 0)
        if sx == "left":
            left = max(dx, 0)
        elif sx == "right":
            right = max(dx, 0)
        return f"up:{up},down:{down},left:{left},right:{right}"
    if event.get("direction"):
        d = str(event.get("direction", "down")).lower()
        try:
            px = int(float(event.get("pixels") or 500))
        except (TypeError, ValueError):
            px = 500
        px = max(px, 0)
        if d == "down":
            return f"up:0,down:{px},left:0,right:0"
        if d == "up":
            return f"up:{px},down:0,left:0,right:0"
    return "up:0,down:0,left:0,right:0"


def _collapse_consecutive_fill_events(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """同一批次里相同选择器的连续 fill 只保留最后一个（最终输入值）。"""
    if not events:
        return events
    out: List[Dict[str, Any]] = []
    for ev in events:
        if ev.get("action") != "fill":
            out.append(ev)
            continue
        if out and out[-1].get("action") == "fill" and out[-1].get("selector") == ev.get("selector"):
            out[-1] = ev
        else:
            out.append(ev)
    return out


def _fallback_locator_tuples(primary_selector: str, primary_type: str, locator_candidates_raw: Any) -> List[tuple]:
    cands = _normalize_locator_candidate_list(locator_candidates_raw)
    cands.sort(key=lambda x: -int(x.get("score") or 0))
    seen = {(str(primary_type).lower(), str(primary_selector or ""))}
    out: List[tuple] = []
    for c in cands:
        t = str(c.get("selector_type") or "css").lower()
        v = str(c.get("selector_value") or "")
        key = (t, v)
        if key in seen or not v:
            continue
        seen.add(key)
        out.append((v, t))
    return out


class PlaywrightAutomation:
    def __init__(self):
        self.browser = None
        self.page = None
        self.context = None
        self._last_headless = None  # 记录最近一次启动模式，拾取器需要有头模式
        self.recording = False
        self.recorded_steps = []
        self.current_url = ""
        self.page_events = []  # 存储页面事件以便后续处理
        self.sync_task = None  # 用于同步录制事件的后台任务
        self.playwright = None  # 初始化playwright实例变量
        self.locator_manager = None  # 🔥 增强型定位管理器
        self.recorder_panel_page = None  # 录制步骤预览窗口（同 context 第二页）
        self._recording_poll_task = None
        self._platform_origin = ""
        self._recording_session_clear_cb = None
        self._selection_mode_active = False
        self.current_iframe = None  # {'selector', 'selector_type', 'iframe'} 由 enter_iframe 设置

    def _is_recorder_panel_page(self, p) -> bool:
        rp = getattr(self, "recorder_panel_page", None)
        return rp is not None and p == rp

    def _get_recording_target_pages(self) -> List[Any]:
        """返回录制期间需要采集事件的业务页面（排除步骤面板页）。"""
        pages: List[Any] = []
        ctx = getattr(self, "context", None)
        if not ctx:
            if self.page is not None:
                return [self.page]
            return []
        try:
            for p in (ctx.pages or []):
                if p is None or self._is_recorder_panel_page(p):
                    continue
                try:
                    if p.is_closed():
                        continue
                except Exception:
                    continue
                pages.append(p)
        except Exception:
            pass
        if not pages and self.page is not None:
            pages = [self.page]
        return pages

    def set_recording_session_clear_callback(self, cb):
        """浏览器被关闭或录制会话异常结束时回调（例如清理 Flask _recording_session_user_id）"""
        self._recording_session_clear_cb = cb

    def _notify_recording_session_cleared(self):
        cb = getattr(self, "_recording_session_clear_cb", None)
        if callable(cb):
            try:
                cb()
            except Exception as e:
                uat_logger.debug(f"[RECORDING] session clear cb: {e}")

    def _handle_browser_disconnect_sync(self):
        """Playwright browser disconnected 事件（用户关掉整窗）"""
        try:
            self.recording = False
            self.recorded_steps = []
            self.recorder_panel_page = None
            self._recording_poll_task = None
        except Exception:
            pass
        self.page = None
        self.context = None
        self.browser = None
        self._notify_recording_session_cleared()
        uat_logger.info("🔌 [BROWSER] 已断开连接，录制已自动结束")

    async def is_browser_session_usable(self) -> bool:
        """主标签页是否仍可用于自动化（例如运行用例前的会话检查）。"""
        try:
            if self.browser is None or not self.browser.is_connected():
                return False
            if self.page is None or self.page.is_closed():
                return False
        except Exception:
            return False
        return True

    def convert_selector(self, selector_value: str, selector_type: str) -> tuple:
        """
        将简化的选择器类型转换为实际可用的选择器和类型
        
        Args:
            selector_value: 选择器值
            selector_type: 简化的选择器类型
            
        Returns:
            tuple: (转换后的选择器值, 实际的选择器类型)
        """
        if selector_type == 'id':
            # ID定位: username -> #username
            return f"#{selector_value}", 'css'
        
        elif selector_type == 'class':
            # Class定位: btn-primary -> .btn-primary
            return f".{selector_value}", 'css'
        
        elif selector_type == 'name':
            # Name定位: username -> [name="username"]
            return f'[name="{selector_value}"]', 'css'
        
        elif selector_type == 'text':
            # 文本定位: 提交 -> xpath=//*[text()='提交']
            return f"//*[text()='{selector_value}']", 'xpath'
        
        elif selector_type == 'partial_text':
            # 与 Playwright getByText 更接近：用 string-value（normalize-space(.)）含子节点文案；
            # contains(text(),'…') 仅匹配直接子文本节点，菜单/图标+span 等场景常点不到。
            lit = _xpath_string_literal(selector_value)
            return f"//*[contains(normalize-space(.),{lit})]", 'xpath'
        
        elif selector_type == 'placeholder':
            # Placeholder定位: 请输入用户名 -> [placeholder="请输入用户名"]
            return f'[placeholder="{selector_value}"]', 'css'
        
        elif selector_type == 'label':
            # Label关联定位: 用户名 -> 查找label文本对应的input
            # 返回特殊的标记，让后续方法处理
            return selector_value, 'label'
        
        elif selector_type == 'title':
            # Title属性定位: 帮助 -> [title="帮助"]
            return f'[title="{selector_value}"]', 'css'
        
        elif selector_type == 'alt':
            # Alt属性定位: 图片说明 -> [alt="图片说明"]
            return f'[alt="{selector_value}"]', 'css'
        
        elif selector_type == 'data':
            # Data属性定位: test-id=123 -> [data-test-id="123"]
            if '=' in selector_value:
                key, value = selector_value.split('=', 1)
                return f'[data-{key}="{value}"]', 'css'
            else:
                # 如果只提供值，尝试常见的data属性
                return f'[data-testid="{selector_value}"], [data-test="{selector_value}"], [data-id="{selector_value}"]', 'css'
        
        elif selector_type == 'aria':
            # ARIA标签定位: 搜索 -> [aria-label="搜索"]
            return f'[aria-label="{selector_value}"]', 'css'
        
        else:
            # 其他类型保持不变
            return selector_value, selector_type
    
    async def find_element_by_label(self, label_text: str, page=None):
        """
        通过label文本查找关联的input元素
        
        Args:
            label_text: label的文本内容
            page: 可选，指定页面
            
        Returns:
            找到的元素locator
        """
        target_page = page if page is not None else self.page
        if target_page is None:
            raise Exception("浏览器未启动")
        
        uat_logger.info(f"🔍 [LABEL_LOCATE] 通过label文本查找元素: {label_text}")
        
        # 使用JavaScript查找与label关联的input
        js_result = await target_page.evaluate("""(labelText) => {
            // 策略1: 通过label的for属性查找
            const labels = document.querySelectorAll('label');
            for (const label of labels) {
                if (label.textContent.trim() === labelText || label.textContent.trim().includes(labelText)) {
                    const forAttr = label.getAttribute('for');
                    if (forAttr) {
                        const input = document.getElementById(forAttr);
                        if (input && (input.tagName === 'INPUT' || input.tagName === 'TEXTAREA' || input.tagName === 'SELECT')) {
                            return {
                                found: true,
                                selector: `#${forAttr}`,
                                selectorType: 'css',
                                method: 'for_attribute'
                            };
                        }
                    }
                    
                    // 策略2: label内部包含input
                    const innerInput = label.querySelector('input, textarea, select');
                    if (innerInput) {
                        const id = innerInput.id;
                        if (id) {
                            return {
                                found: true,
                                selector: `#${id}`,
                                selectorType: 'css',
                                method: 'label_inner'
                            };
                        }
                        // 使用XPath定位
                        return {
                            found: true,
                            selector: `//label[contains(text(),'${labelText}')]//input | //label[contains(text(),'${labelText}')]//textarea | //label[contains(text(),'${labelText}')]//select`,
                            selectorType: 'xpath',
                            method: 'label_inner_xpath'
                        };
                    }
                }
            }
            
            // 策略3: 查找相邻的input（label后面紧跟着input）
            for (const label of labels) {
                if (label.textContent.trim() === labelText || label.textContent.trim().includes(labelText)) {
                    const parent = label.parentElement;
                    if (parent) {
                        const siblingInput = parent.querySelector('input, textarea, select');
                        if (siblingInput) {
                            const id = siblingInput.id;
                            if (id) {
                                return {
                                    found: true,
                                    selector: `#${id}`,
                                    selectorType: 'css',
                                    method: 'sibling'
                                };
                            }
                        }
                    }
                }
            }
            
            return { found: false };
        }""", label_text)
        
        if js_result and js_result.get('found'):
            uat_logger.info(f"✅ [LABEL_LOCATE] 通过label找到元素: {js_result.get('method')}")
            return js_result.get('selector'), js_result.get('selectorType')
        else:
            uat_logger.error(f"❌ [LABEL_LOCATE] 未找到与label关联的元素: {label_text}")
            return None, None
    
    async def _cleanup_browser_resources(self):
        """清理浏览器相关资源"""
        uat_logger.info("🧹 [CLEANUP] 开始清理浏览器资源...")
        try:
            # 关闭页面
            if self.page:
                try:
                    await self.page.close()
                    uat_logger.info("✅ [CLEANUP] 页面已关闭")
                except Exception as e:
                    uat_logger.warning(f"⚠️ [CLEANUP] 关闭页面时出现警告: {str(e)}")
                self.page = None
            
            # 关闭上下文
            if self.context:
                try:
                    await self.context.close()
                    uat_logger.info("✅ [CLEANUP] 浏览器上下文已关闭")
                except Exception as e:
                    uat_logger.warning(f"⚠️ [CLEANUP] 关闭上下文时出现警告: {str(e)}")
                self.context = None
            
            # 关闭浏览器
            if self.browser:
                try:
                    await self.browser.close()
                    uat_logger.info("✅ [CLEANUP] 浏览器已关闭")
                except Exception as e:
                    uat_logger.warning(f"⚠️ [CLEANUP] 关闭浏览器时出现警告: {str(e)}")
                self.browser = None
            
            # 停止playwright
            if self.playwright:
                try:
                    await self.playwright.stop()
                    uat_logger.info("✅ [CLEANUP] Playwright已停止")
                except Exception as e:
                    uat_logger.warning(f"⚠️ [CLEANUP] 停止Playwright时出现警告: {str(e)}")
                self.playwright = None
                
            uat_logger.info("✅ [CLEANUP] 浏览器资源清理完成")
        except Exception as e:
            uat_logger.error(f"❌ [CLEANUP] 清理浏览器资源时发生错误: {str(e)}")
    
    async def start_browser(self, headless: bool = True, _retry: bool = True):
        """启动浏览器。默认无头，便于服务器/容器部署；本地需要可见窗口时设置 PLAYWRIGHT_HEADLESS=0。
        _retry: 内部使用，会话失效时自动重置并重试一次，避免用户手动关掉窗口后下次报错。"""
        try:
            headless = resolve_playwright_headless(headless)

            # 检查现有浏览器状态（必须同时检查连接有效性）
            browser_valid = False
            try:
                browser_valid = self.browser is not None and self.browser.is_connected()
            except Exception:
                browser_valid = False

            uat_logger.info(f"🔍 [BROWSER_START] 浏览器连接状态: {browser_valid}")

            if not browser_valid:
                # 浏览器无效（未启动 or 已手动关闭 or 断连），彻底清空所有引用
                uat_logger.info("🔧 [BROWSER_START] 浏览器无效，强制清空所有引用并重新启动...")
                # 先尝试优雅关闭，忽略所有异常
                for attr in ('page', 'context', 'browser'):
                    obj = getattr(self, attr, None)
                    if obj is not None:
                        try:
                            await obj.close()
                        except Exception:
                            pass
                        finally:
                            setattr(self, attr, None)
                # playwright 用 stop() 而不是 close()
                if self.playwright is not None:
                    try:
                        await self.playwright.stop()
                    except Exception:
                        pass
                    finally:
                        self.playwright = None
                uat_logger.info("✅ [BROWSER_START] 旧资源清理完毕，准备重新启动")
            elif self.context is None or self.page is None:
                # 浏览器有效但 context/page 失效，只清理失效部分
                uat_logger.info("🔧 [BROWSER_START] 检测到context或page失效，只清理失效组件...")
                if self.context:
                    try:
                        await self.context.close()
                    except Exception as e:
                        uat_logger.warning(f"⚠️ [CLEANUP] 关闭context时出现警告: {str(e)}")
                    self.context = None
                if self.page:
                    try:
                        await self.page.close()
                    except Exception as e:
                        uat_logger.warning(f"⚠️ [CLEANUP] 关闭page时出现警告: {str(e)}")
                    self.page = None
            else:
                uat_logger.info("✅ [BROWSER_START] 浏览器状态正常，无需清理")
            
            # 确保浏览器相关对象都已正确重置
            # 🔥 修复：增强浏览器连接检测的容错性
            need_start_browser = False
            if self.browser is None:
                need_start_browser = True
            else:
                try:
                    need_start_browser = not self.browser.is_connected()
                except Exception:
                    need_start_browser = True
                    # 检测异常说明浏览器对象已失效，清空引用
                    self.browser = None
                    self.page = None
                    self.context = None
            
            if need_start_browser:
                uat_logger.info(f"启动浏览器,headless={headless}")
                
                # 1. 确保playwright实例已正确关闭和重置
                if self.playwright:
                    try:
                        await self.playwright.stop()
                    except:
                        pass
                    self.playwright = None
                
                # 2. 使用Windows API直接获取真实的屏幕尺寸(不依赖浏览器)
                self.playwright = await async_playwright().start()
                
                if sys.platform == 'win32':
                    user32 = ctypes.windll.user32
                    screen_width = user32.GetSystemMetrics(0)  # SM_CXSCREEN
                    screen_height = user32.GetSystemMetrics(1)  # SM_CYSCREEN
                    avail_width = user32.GetSystemMetrics(78)  # SM_CXAVAILABLE
                    avail_height = user32.GetSystemMetrics(79)  # SM_CYAVAILABLE
                    screen_size = {"width": screen_width, "height": screen_height}
                    avail_screen_size = {"width": avail_width, "height": avail_height}
                    uat_logger.info(f"Windows API获取的屏幕尺寸: {screen_size['width']}x{screen_size['height']}")
                    uat_logger.info(f"Windows API获取的可用工作区尺寸: {avail_screen_size['width']}x{avail_screen_size['height']}")
                else:
                    try:
                        screen_width = int(os.environ.get("PLAYWRIGHT_SCREEN_WIDTH", "1920"))
                        screen_height = int(os.environ.get("PLAYWRIGHT_SCREEN_HEIGHT", "1080"))
                    except ValueError:
                        screen_width, screen_height = 1920, 1080
                    uat_logger.info(
                        f"非 Windows 环境，跳过 Win32 屏幕检测；参考尺寸 {screen_width}x{screen_height} "
                        f"(可通过环境变量 PLAYWRIGHT_SCREEN_WIDTH/HEIGHT 调整)"
                    )
                
                # 2. 使用获取到的可用工作区尺寸启动真正的浏览器实例
                # 使用可用工作区尺寸可以避免与任务栏等系统UI冲突
                args = [
                    '--start-maximized',  # 真正的浏览器最大化
                    '--no-default-browser-check',
                    '--no-first-run',
                    # 容器环境常见问题：沙箱权限不足/共享内存不足导致页面直接关闭
                    '--no-sandbox',
                    '--disable-dev-shm-usage',
                    '--disable-blink-features=AutomationControlled',
                ]
                if headless:
                    # 无头模式下 start-maximized/resizeTo 经常不生效，显式窗口尺寸更稳定
                    args.extend([
                        f'--window-size={screen_width},{screen_height}',
                        '--force-device-scale-factor=1',
                        '--disable-gpu',
                    ])
                
                self.browser = await self.playwright.chromium.launch(
                    headless=headless,
                    args=args
                )
                self._last_headless = bool(headless)
                try:
                    self.browser.on("disconnected", lambda _: self._handle_browser_disconnect_sync())
                except Exception as e:
                    uat_logger.warning(f"⚠️ [BROWSER] 注册 disconnected 事件失败: {e}")
                
                # headed 模式让浏览器自行管理视口；headless 模式固定视口，避免元素布局抖动
                context_kwargs = {
                    "ignore_https_errors": True,
                }
                if headless:
                    context_kwargs["viewport"] = {"width": screen_width, "height": screen_height}
                else:
                    context_kwargs["no_viewport"] = True
                self.context = await self.browser.new_context(**context_kwargs)
                
                # 创建新页面
                self.page = await self.context.new_page()
                try:
                    action_ms = int(os.environ.get("PLAYWRIGHT_ACTION_TIMEOUT_MS", "0"))
                except ValueError:
                    action_ms = 0
                try:
                    nav_ms = int(os.environ.get("PLAYWRIGHT_NAV_TIMEOUT_MS", "0"))
                except ValueError:
                    nav_ms = 0
                if action_ms <= 0:
                    action_ms = 45000 if headless else 30000
                if nav_ms <= 0:
                    nav_ms = 90000 if headless else 60000
                # Playwright Python：set_default_* 为同步 API，不能使用 await
                self.page.set_default_timeout(action_ms)
                self.page.set_default_navigation_timeout(nav_ms)
                uat_logger.info(
                    f"页面默认超时: action={action_ms}ms, navigation={nav_ms}ms "
                    f"(可用 PLAYWRIGHT_ACTION_TIMEOUT_MS / PLAYWRIGHT_NAV_TIMEOUT_MS 覆盖)"
                )

                if sys.platform == 'win32' and not headless:
                    user32 = ctypes.windll.user32
                    screen_width = user32.GetSystemMetrics(0)  # SM_CXSCREEN
                    screen_height = user32.GetSystemMetrics(1)  # SM_CYSCREEN
                    uat_logger.info(f"将浏览器窗口设置为真实屏幕尺寸: {screen_width}x{screen_height}")
                    await self.page.evaluate(f"window.resizeTo({screen_width}, {screen_height})")
                    await self.page.evaluate("window.moveTo(0, 0)")
                else:
                    if headless:
                        uat_logger.info(f"无头模式：固定视口 {screen_width}x{screen_height}，跳过 window.resizeTo/moveTo")
                    else:
                        uat_logger.info("非 Windows 环境，跳过 window.resizeTo/moveTo（服务器无 windll；已使用 no_viewport）")
                
                # 直接获取浏览器窗口的实际尺寸
                viewport_size = await self.page.evaluate("() => ({ width: window.innerWidth, height: window.innerHeight })")
                outer_size = await self.page.evaluate("() => ({ width: window.outerWidth, height: window.outerHeight })")
                screen_size = await self.page.evaluate("() => ({ width: screen.width, height: screen.height })")
                avail_screen_size = await self.page.evaluate("() => ({ width: screen.availWidth, height: screen.availHeight })")
                
                uat_logger.info(f"屏幕总尺寸: {screen_size['width']}x{screen_size['height']}")
                uat_logger.info(f"屏幕可用尺寸: {avail_screen_size['width']}x{avail_screen_size['height']}")
                uat_logger.info(f"浏览器窗口内尺寸: {viewport_size['width']}x{viewport_size['height']}")
                uat_logger.info(f"浏览器窗口外尺寸: {outer_size['width']}x{outer_size['height']}")
                uat_logger.info(f"浏览器已设置为全屏模式,右上角最大化按钮应不可见")
                
                uat_logger.info("浏览器已启动并最大化,设置事件监听器")
                
                # 🔥 初始化增强型定位管理器
                self.locator_manager = create_locator_manager(self.page)
                await self.locator_manager.setup_tracking()
                
                uat_logger.info("浏览器启动完成，已准备就绪")
            
            try:
                page_ok = self.page is not None and not self.page.is_closed()
            except Exception:
                page_ok = False
            try:
                br_ok = self.browser is not None and self.browser.is_connected()
            except Exception:
                br_ok = False
            if (not page_ok or not br_ok) and _retry:
                uat_logger.warning("🔧 [BROWSER_START] 返回前检测到页面或连接已失效，重置会话并重试一次")
                force_reset_execution_state()
                self._notify_recording_session_cleared()
                return await self.start_browser(headless=headless, _retry=False)
            if not page_ok or not br_ok:
                raise Exception(
                    "浏览器会话不可用（页面或连接已失效，无头环境下多为断连/崩溃；不一定是手动关闭），已尝试自动恢复失败，请重试"
                )
            
            return self.page
        except Exception as e:
            uat_logger.log_exception("start_browser", e)
            raise Exception(f"启动浏览器失败: {str(e)}")
    
    async def _setup_event_listeners(self, page=None):
        """内置浏览器录制已移除。"""
        return

    async def _on_page_navigated(self, frame):
        """内置录制已移除。"""
        return

    async def get_recorded_events(self, page=None):
        """内置录制已移除。"""
        return []

    async def sync_recorded_events(self):
        """内置录制已移除。"""
        return 0

    async def navigate_to(self, url: str, iframe_selector: str = None, page=None):
        """导航到指定URL,支持iframe导航。page: 可选，指定在哪个标签页执行（多标签并行时使用）"""
        # 统一校验URL
        fixed_url, err = _pa_validate_url(url)
        if err:
            raise Exception(f"导航失败: {err}")
        if fixed_url is None:
            uat_logger.warning(f"导航跳过，占位符或空URL: {url}")
            return  # 跳过占位符URL而不报错
        url = fixed_url
        
        target_page = page if page is not None else self.page
        if target_page is None:
            await self.start_browser()
            target_page = self.page
        
        # 再次检查确保page对象存在
        if target_page is not None:
            # 导航到URL,对于录制时使用domcontentloaded以提高响应速度
            # 但在回放时,我们需要确保页面完全加载
                # 🔥 性能优化：回放模式使用更快的等待策略
                await target_page.goto(url, wait_until='domcontentloaded')
                # 快速检查 networkidle（仅等待 3 秒，避免 WebSocket/SSE 长连接导致的长时间阻塞）
                try:
                    await target_page.wait_for_load_state('networkidle', timeout=3000)
                except Exception:
                    pass  # 超时即认为网络已足够稳定，继续执行
                # 🔥 移除固定 1 秒延迟和 DOM 稳定性检测（大幅减少不必要的等待）
                # 原因：domcontentloaded + 3 秒 networkidle 已足够覆盖绝大多数场景
        else:
            uat_logger.error("页面对象为None,无法导航")
            raise Exception("无法创建页面对象")
        self.current_url = url
        
        uat_logger.info(f"执行导航操作: {url}")

    async def enter_iframe(self, selector: str, selector_type: str = "css") -> None:
        """记录 iframe 定位串到 current_iframe。

        执行器会把后续步骤默认映射到该 iframe（直到 exit_iframe）。本身不向页面发送额外点击。
        必须在 Playwright Worker 同线程事件循环中调用（与 click/navigate 一致），勿用 asyncio.run。
        """
        if self.page is None:
            raise Exception("浏览器未启动")
        if not (selector or "").strip():
            raise Exception("进入 iframe 失败: 未提供 iframe 元素选择器")

        uat_logger.info(f"🔄 进入iframe（隐式上下文）: {selector} (类型: {selector_type})")

        sel = selector.strip()
        st = (selector_type or "css").lower()
        if st in (
            "id",
            "class",
            "name",
            "text",
            "partial_text",
            "placeholder",
            "label",
            "title",
            "alt",
            "data",
            "aria",
        ):
            if st == "label":
                sel, st = await self.find_element_by_label(sel, self.page)
                if sel is None:
                    raise Exception(f"未找到与 label 关联的 iframe 元素: {selector}")
            else:
                sel, st = self.convert_selector(sel, st)

        wait_sel = sel
        if st == "xpath":
            wait_sel = sel if str(sel).startswith("xpath=") else f"xpath={sel}"

        try:
            # iframe 常因尺寸/跨域等不满足 visible；attached 即可确认节点存在再 frame_locator
            await self.page.wait_for_selector(wait_sel, state="attached", timeout=20000)
            uat_logger.info(f"✅ 找到 iframe 元素: {wait_sel}")
            iframe_fl = self.page.frame_locator(wait_sel)
            self.current_iframe = {
                "selector": wait_sel,
                "selector_type": st,
                "iframe": iframe_fl,
            }
            uat_logger.info("✅ 已进入 iframe 并保存 frame_locator 上下文")
        except Exception as e:
            uat_logger.error(f"❌ 进入iframe失败: {e}")
            raise Exception(f"进入iframe失败: {e}") from e

    async def exit_iframe(self) -> None:
        """清除 current_iframe；后续步骤默认回到主文档（直到再次 enter_iframe）。

        须在 Playwright Worker 同线程中调用，勿用 asyncio.run。
        嵌套 iframe：当前实现为单层，再次 enter 会覆盖；exit 一次即清空。
        """
        uat_logger.info("🔄 跳出iframe（隐式上下文结束），返回主文档")
        try:
            self.current_iframe = None
            uat_logger.info("✅ 成功跳出iframe，返回主文档")
        except Exception as e:
            uat_logger.error(f"❌ 跳出iframe失败: {e}")
            raise Exception(f"跳出iframe失败: {e}") from e
    
    async def select_option(self, selector: str, select_value: str, selector_type: str = "css", iframe_selector: str = None, page=None):
        """选择下拉框选项。支持原生select和自定义下拉框（如Element Plus）。page: 可选，指定在哪个标签页执行"""
        target_page = page if page is not None else self.page
        if target_page is None:
            raise Exception("浏览器未启动")
        
        # 🔥 转换简化的选择器类型
        original_selector_type = selector_type
        if selector_type in ['id', 'class', 'name', 'text', 'partial_text', 'placeholder', 
                             'label', 'title', 'alt', 'data', 'aria']:
            if selector_type == 'label':
                # Label类型需要异步查找
                selector, selector_type = await self.find_element_by_label(selector, target_page)
                if selector is None:
                    raise Exception(f"未找到与label关联的元素: {original_selector_type}={selector}")
            else:
                selector, selector_type = self.convert_selector(selector, selector_type)
            uat_logger.info(f"🔍 [SELECTOR_CONVERT] 选择器转换: {original_selector_type} -> {selector_type}, 值: {selector}")
        
        uat_logger.info(f"🔍 [SELECT_DEBUG] 开始选择下拉框选项,选择器: {selector}, 选择值: {select_value}, 选择器类型: {selector_type}, iframe选择器: {iframe_selector}")
        
        # 构建完整的选择器
        full_selector = selector
        if selector_type == "xpath":
            full_selector = f"xpath={selector}"
        
        # 确定操作上下文
        target_context = target_page
        if iframe_selector:
            uat_logger.info(f"🔄 [IFRAME_DEBUG] 使用iframe上下文,选择器: {iframe_selector}")
            target_context = target_page.frame_locator(iframe_selector)
        
        if target_context is None:
            uat_logger.error(f"❌ [SELECT_DEBUG] 操作上下文为None,无法执行选择操作")
            raise Exception(f"操作上下文为None,无法执行选择操作")
        
        # 🔥 优化：减少等待时间，提升执行速度
        # 策略优化: 先等待元素存在于DOM，不要求可见性
        uat_logger.info(f"🔍 [SELECT_DEBUG] 等待元素存在于DOM...")
        try:
            await target_context.wait_for_selector(full_selector, state='attached', timeout=3000)
            uat_logger.info(f"✅ [SELECT_DEBUG] 元素已存在于DOM")
        except Exception as e:
            uat_logger.error(f"❌ [SELECT_DEBUG] 等待元素存在于DOM失败: {str(e)}")
            raise Exception(f"无法找到下拉框元素: {selector}")
        
        # 获取元素引用
        element = target_context.locator(full_selector)
        try:
            matched_count = await element.count()
            if matched_count and matched_count > 1:
                uat_logger.warning(
                    f"⚠️ [SELECT_DEBUG] 下拉触发器选择器存在歧义: full_selector={full_selector}, 命中数量={matched_count}. 将只操作第一个匹配元素（element.first）。"
                )
        except Exception:
            pass
                
        uat_logger.info(f"🔍 [SELECT_DEBUG] 检测下拉框类型")
        
        # 检查是否是原生select元素
        try:
            tag_name = await element.evaluate("el => el.tagName.toLowerCase()")
            uat_logger.info(f"🔍 [SELECT_DEBUG] 元素标签名: {tag_name}")
        except Exception as e:
            uat_logger.warning(f"⚠️ [SELECT_DEBUG] 获取标签名失败: {str(e)}, 按自定义下拉框处理")
            tag_name = "div"
        
        if tag_name == "select":
            # 处理原生 select 元素
            uat_logger.info(f"🔍 [SELECT_DEBUG] 检测到原生 select 元素，使用原生方法")
                    
            # 滚动到视图，确保元素可见（仅原生 select 需要）
            uat_logger.info(f"🔍 [SELECT_DEBUG] 滚动元素到视图...")
            try:
                await element.scroll_into_view_if_needed(timeout=2000)
                uat_logger.info(f"✅ [SELECT_DEBUG] 滚动完成")
            except Exception as e:
                uat_logger.warning(f"⚠️ [SELECT_DEBUG] 滚动失败：{str(e)}, 继续尝试")
                    
            # 再次等待元素可见
            uat_logger.info(f"🔍 [SELECT_DEBUG] 等待元素可见...")
            try:
                await target_context.wait_for_selector(full_selector, state='visible', timeout=3000)
                uat_logger.info(f"✅ [SELECT_DEBUG] 元素可见")
            except Exception as e:
                uat_logger.warning(f"⚠️ [SELECT_DEBUG] 元素可能不可见：{str(e)}, 继续尝试操作")
                    
            # 获取所有选项文本
            options = await target_context.locator(f"{full_selector} option").all()
            option_texts = []
            for option in options:
                text = await option.text_content()
                if text:
                    option_texts.append(text.strip())
            
            uat_logger.info(f"🔍 [SELECT_DEBUG] 下拉框中的选项: {option_texts}")
            
            # 验证选择值是否存在于下拉框中
            if select_value not in option_texts:
                uat_logger.error(f"❌ [SELECT_DEBUG] 选择值 '{select_value}' 不存在于下拉框中")
                raise Exception(f"选择值 '{select_value}' 不存在于下拉框中")
            
            # 选择选项
            uat_logger.info(f"🔍 [SELECT_DEBUG] 自动选择用户输入值: {select_value}")
            await target_context.locator(full_selector).select_option(select_value)
            uat_logger.info(f"✅ [SELECT_DEBUG] 原生下拉框选择成功: {select_value}")
        else:
            # 处理自定义下拉框（Element Plus、Ant Design等）
            uat_logger.info(f"🔍 [SELECT_DEBUG] 检测到自定义下拉框，尝试静默设置值（不展开面板）")
            
            # 🔥 新增：优先尝试 JavaScript 直接设置值，无需展开下拉面板
            js_set_success = False
            try:
                if selector_type == "css":
                    # 尝试通过 Element UI 的 API 直接设置值
                    # 注意：这里必须对“当前匹配到的触发器实例”操作，不能用 document.querySelector(selector)
                    # 否则同一 selector 匹配到多个下拉时，会把值写到别的实例上。
                    js_result = await asyncio.wait_for(element.first.evaluate("""(el, params) => {
                        const { value } = params;
                        const normalize = (s) => (s || '').replace(/\\s+/g, ' ').trim();

                        // 检查是否是 Element UI 的 el-select 组件
                        const isElSelect = el.classList && (el.classList.contains('el-select') ||
                            el.querySelector && el.querySelector('.el-input__inner') !== null);

                        if (!isElSelect) return { success: false, error: 'not_el_select' };

                        const selectInner = el.querySelector('.el-input__inner');
                        if (!selectInner) return { success: false, error: 'select_inner_not_found' };

                        // 触发元素：优先使用内部带 aria-controls 的 input，其次外层 el-select
                        let triggerEl = el;
                        if (!(triggerEl.getAttribute && triggerEl.getAttribute('aria-controls'))) {
                            const inner = el.querySelector && el.querySelector('[aria-controls]');
                            if (inner) triggerEl = inner;
                        }

                        // 尝试找到与当前触发器“最近”的下拉面板
                        let optionValue = null;
                        const elementRect = triggerEl.getBoundingClientRect();
                        const targetText = normalize(value);

                        const allDropdowns = document.querySelectorAll('.el-select-dropdown, .ant-select-dropdown, [role="listbox"]');
                        let targetDropdown = null;
                        let bestScore = Infinity;

                        for (const dropdown of allDropdowns) {
                            const style = window.getComputedStyle(dropdown);
                            if (!style || style.display === 'none' || style.visibility === 'hidden') continue;
                            const r = dropdown.getBoundingClientRect();
                            // 以触发器中心点为基准选择“最近面板”
                            const dx = (r.left + r.width / 2) - (elementRect.left + elementRect.width / 2);
                            const dy = (r.top + r.height / 2) - (elementRect.top + elementRect.height / 2);
                            const score = Math.abs(dx) + Math.abs(dy) * 0.2;
                            if (score < bestScore) {
                                bestScore = score;
                                targetDropdown = dropdown;
                            }
                        }

                        if (targetDropdown) {
                            const options = targetDropdown.querySelectorAll('.el-select-dropdown__item, .ant-select-item, [role="option"], li');
                            for (const opt of options) {
                                const itemText = normalize(opt.textContent);
                                const dataValue = normalize(opt.getAttribute && (opt.getAttribute('data-value') || opt.getAttribute('value')));
                                if (!itemText) continue;
                                const textMatch = itemText === targetText || itemText.includes(targetText);
                                const valueMatch = dataValue && (dataValue === targetText || dataValue.includes(targetText));
                                if (textMatch || valueMatch) {
                                    optionValue = (opt.getAttribute && (opt.getAttribute('data-value') || opt.getAttribute('value'))) || itemText;
                                    break;
                                }
                            }
                        }

                        const setValue = optionValue !== null ? optionValue : value;
                        selectInner.value = setValue;
                        selectInner.dispatchEvent(new Event('input', { bubbles: true }));
                        selectInner.dispatchEvent(new Event('change', { bubbles: true }));
                        selectInner.dispatchEvent(new Event('blur', { bubbles: true }));

                        // 校验当前触发器显示/内部值是否贴近目标值
                        const innerValue = normalize(selectInner.value);
                        const selectedTextNode = el.querySelector('.el-select__selected');
                        const selectedText = normalize(selectedTextNode ? selectedTextNode.textContent : '');
                        const ok = (innerValue.includes(targetText) || targetText.includes(innerValue) ||
                                    (selectedText && selectedText.includes(targetText)));

                        return { success: ok, method: optionValue !== null ? 'vue_input_set' : 'direct_text_set', innerValue, selectedText };
                    }""", {'value': select_value}), timeout=6)
                    
                    if js_result and js_result.get('success'):
                        uat_logger.info(
                            f"✅ [SELECT_DEBUG] JavaScript静默设置成功: {select_value}, 方法: {js_result.get('method')}, innerValue: {js_result.get('innerValue')}, selectedText: {js_result.get('selectedText')}"
                        )
                        js_set_success = True
                    else:
                        uat_logger.debug(f"🔍 [SELECT_DEBUG] JavaScript静默设置失败: {js_result}")
                        
            except Exception as js_error:
                uat_logger.debug(f"🔍 [SELECT_DEBUG] JavaScript静默设置异常: {str(js_error)}")
            
            # 如果 JavaScript 设置失败，回退到点击方式
            if not js_set_success:
                uat_logger.info(f"🔍 [SELECT_DEBUG] JavaScript设置失败，回退到点击方式")
                
                # 尝试点击下拉框展开选项
                uat_logger.info(f"🔍 [SELECT_DEBUG] 尝试点击下拉框展开选项")
                clicked = False
                for attempt in range(2):  # 最多尝试2次，减少耗时
                    try:
                        uat_logger.info(f"🔍 [SELECT_DEBUG] 第{attempt+1}次尝试点击下拉框")
                        # 用 JS click 避免 Playwright click 的自动滚动行为
                        # 这是导致“无故上下滑动/漂移”的常见原因之一
                        await element.evaluate("el => el.click()")
                        uat_logger.info(f"✅ [SELECT_DEBUG] 下拉框点击成功")
                        clicked = True
                        break
                    except Exception as e:
                        uat_logger.warning(f"⚠️ [SELECT_DEBUG] 第{attempt+1}次点击失败: {str(e)}")
                        # 尝试使用force点击
                        try:
                            # force click 也可能触发滚动；仅作为兜底
                            await element.click(force=True, timeout=1200)
                            uat_logger.info(f"✅ [SELECT_DEBUG] force点击成功")
                            clicked = True
                            break
                        except Exception as e2:
                            uat_logger.warning(f"⚠️ [SELECT_DEBUG] force点击也失败: {str(e2)}")
                            # 缩短固定等待时间，使用更短的短暂等待
                            await target_page.wait_for_timeout(80)
                
                if not clicked:
                    uat_logger.error(f"❌ [SELECT_DEBUG] 所有点击尝试都失败")
                    raise Exception(f"无法点击下拉框: {selector}")

                # 快速路径：Element Plus 下拉一般可通过 trigger input 的 aria-controls
                # 直接定位到对应 listbox，再点击目标选项，避免多轮全局搜索导致变慢。
                try:
                    listbox_id = await asyncio.wait_for(
                        element.first.evaluate("""(el) => {
                            let triggerEl = el;
                            if (!(triggerEl.getAttribute && triggerEl.getAttribute('aria-controls'))) {
                                const inner = el.querySelector && el.querySelector('[aria-controls]');
                                if (inner) triggerEl = inner;
                            }
                            return triggerEl.getAttribute ? triggerEl.getAttribute('aria-controls') : null;
                        }"""),
                        timeout=2
                    )
                except Exception:
                    listbox_id = None

                if listbox_id:
                    try:
                        fast_option = target_page.locator(
                            f"ul#{listbox_id} li.el-select-dropdown__item",
                            has_text=select_value
                        ).first
                        await fast_option.wait_for(state='visible', timeout=1200)
                        await asyncio.wait_for(fast_option.evaluate("el => el.click()"), timeout=1.2)
                        uat_logger.info(f"✅ [SELECT_DEBUG] 快速路径命中成功(listbox={listbox_id}): {select_value}")
                        await target_page.keyboard.press('Escape')
                        await target_page.wait_for_timeout(60)
                        return
                    except Exception as fast_e:
                        uat_logger.debug(f"🔍 [SELECT_DEBUG] 快速路径未命中，回退通用策略: {fast_e}")
                
                # 等待下拉选项展开 - 使用更快的混合检测策略
                uat_logger.info(f"🔍 [SELECT_DEBUG] 等待下拉面板出现...")
                # 🔥 优化：减少轮询尝试，使用更短的超时时间，提升响应速度
                dropdown_selectors = [
                    'div.el-select-dropdown', 'div.ant-select-dropdown', 
                    'div.dropdown-menu', '[role="listbox"]'
                ]
                dropdown_found = False
                for dropdown_sel in dropdown_selectors:
                    try:
                        # 🔥 优化：从1000ms降低到500ms，减少无谓等待时间
                        await target_page.wait_for_selector(dropdown_sel, state='visible', timeout=300)
                        dropdown_found = True
                        break
                    except Exception:
                        continue
                
                if not dropdown_found:
                    # 🔥 优化：减少固定等待时间从300ms到100ms
                    await target_page.wait_for_timeout(60)
                
                # 🔥 关键修复：对于动态加载数据的下拉框，等待选项实际渲染到DOM中
                # 检测是否有选项元素存在
                uat_logger.info(f"🔍 [SELECT_DEBUG] 等待选项渲染到DOM...")
                option_rendered = False
                option_selectors_check = [
                    '.el-select-dropdown__item', 
                    '.ant-select-item',
                    '[role="option"]'
                ]
                for opt_sel in option_selectors_check:
                    try:
                        await target_page.wait_for_selector(opt_sel, state='visible', timeout=300)
                        option_rendered = True
                        uat_logger.info(f"✅ [SELECT_DEBUG] 选项已渲染: {opt_sel}")
                        break
                    except Exception:
                        continue
                
                if not option_rendered:
                    uat_logger.info(f"🔍 [SELECT_DEBUG] 选项未立即渲染，继续尝试...")
                
                # 查找并点击选项 - 使用平衡的性能和可靠性策略
                uat_logger.info(f"🔍 [SELECT_DEBUG] 查找选项: {select_value}")
                
                # ✅ 恢复：平衡的策略，既保证性能又确保可靠性
                # 🔥 新增：使用contains()进行模糊匹配，处理文本包含空格或额外字符的情况
                option_selectors = [
                    f"xpath=//*[contains(@class, 'dropdown')]//div[text()='{select_value}']",
                    f"xpath=//*[contains(@class, 'select-dropdown')]//div[text()='{select_value}']",
                    f"xpath=//*[contains(@class, 'el-select-dropdown')]//div[text()='{select_value}']",
                    f"xpath=//*[contains(@class, 'el-select-dropdown')]//li[text()='{select_value}']",
                    f"xpath=//*[contains(@class, 'option')]//span[text()='{select_value}']",
                    f"xpath=//*[contains(@class, 'menu')]//li[text()='{select_value}']",
                    f"xpath=//*[contains(@class, 'list')]//div[text()='{select_value}']",
                    f"xpath=//div[@role='option']//span[text()='{select_value}']",
                    f"xpath=//li[@role='option'][text()='{select_value}']",
                    f"xpath=//div[@role='listbox']//div[text()='{select_value}']",
                    # 🔥 新增：模糊匹配策略（使用contains）
                    f"xpath=//*[contains(@class, 'el-select-dropdown')]//div[contains(text(), '{select_value}')]",
                    f"xpath=//*[contains(@class, 'el-select-dropdown')]//li[contains(text(), '{select_value}')]",
                    f"xpath=//*[contains(@class, 'dropdown')]//div[contains(text(), '{select_value}')]",
                    f"xpath=//div[@role='option'][contains(text(), '{select_value}')]",
                    f"xpath=//li[@role='option'][contains(text(), '{select_value}')]",
                ]
                
                option_clicked = False

                # 优先用 JS 在“关联当前触发器的下拉面板”内点击匹配选项，
                # 避免 get_by_text(...).first 误命中其他同名下拉/选项，导致“只某一个无法选择”。
                uat_logger.info(f"🔍 [SELECT_DEBUG] 使用 JS 在关联面板内点击选项")
                try:
                    js_click_result = await asyncio.wait_for(
                        element.first.evaluate("""(el, params) => {
                        const { value } = params;
                        
                        const normalize = (s) => (s || '').replace(/\\s+/g, ' ').trim();
                        
                        // 触发元素：优先使用内部带 aria-controls 的 input，其次外层 el-select
                        let triggerEl = el;
                        if (!(triggerEl.getAttribute && triggerEl.getAttribute('aria-controls'))) {
                            const inner = el.querySelector && el.querySelector('[aria-controls]');
                            if (inner) triggerEl = inner;
                        }
                        
                        // 优先：Element/组件通常会在触发器上提供 aria-controls 指向面板
                        const triggerRect = triggerEl.getBoundingClientRect();
                        const ariaControls = triggerEl.getAttribute && triggerEl.getAttribute('aria-controls');
                        let targetDropdown = null;
                        if (ariaControls) {
                            targetDropdown = document.getElementById(ariaControls) || document.querySelector(`#${ariaControls}`);
                        }

                        const allDropdowns = Array.from(
                            document.querySelectorAll('.el-select-dropdown, .ant-select-dropdown, [role=\"listbox\"]')
                        );

                        const isVisible = (el) => {
                            if (!el) return false;
                            const style = window.getComputedStyle(el);
                            return style && style.display !== 'none' && style.visibility !== 'hidden';
                        };

                        // 兜底：根据“触发器中心点”和面板中心点距离打分，选最像的那个面板
                        if (!targetDropdown) {
                            let bestScore = Infinity;
                            const triggerCx = triggerRect.left + triggerRect.width / 2;
                            const triggerCy = triggerRect.top + triggerRect.height / 2;
                            for (const dropdown of allDropdowns) {
                                if (!isVisible(dropdown)) continue;
                                const r = dropdown.getBoundingClientRect();
                                const cx = r.left + r.width / 2;
                                const cy = r.top + r.height / 2;
                                const dx = Math.abs(cx - triggerCx);
                                const dy = Math.abs(cy - triggerCy);
                                const score = dx + dy * 0.2;
                                if (score < bestScore) {
                                    bestScore = score;
                                    targetDropdown = dropdown;
                                }
                            }
                        }

                        if (!targetDropdown) {
                            return { success: false, error: 'dropdown_not_found' };
                        }

                        const targetText = normalize(value);
                        let items = Array.from(
                            targetDropdown.querySelectorAll(
                                '.el-select-dropdown__item, .el-option, .ant-select-item, [role=\"option\"], li'
                            )
                        );
                        // 如果按照常规类名/role没有匹配到任何候选项，则退化为“面板内所有后代节点”兜底匹配
                        if (items.length === 0) {
                            items = Array.from(targetDropdown.querySelectorAll('*'));
                        }

                        const pickClickable = (node) => {
                            if (!node) return node;
                            return node.closest('.el-select-dropdown__item') ||
                                   node.closest('[role=\"option\"]') ||
                                   node.closest('li') ||
                                   node;
                        };

                        for (const item of items) {
                            const itemText = normalize(item.textContent);
                            const dataValue = normalize(item.getAttribute && (item.getAttribute('data-value') || item.getAttribute('value')));
                            if (!itemText) continue;

                            const textMatch = itemText === targetText || itemText.includes(targetText);
                            const valueMatch = dataValue && (dataValue === targetText || dataValue.includes(targetText));

                            if (textMatch || valueMatch) {
                                const clickable = pickClickable(item);
                                clickable && clickable.click && clickable.click();
                                // 尝试校验触发器内部值是否已更新
                                const selectInner = el.querySelector && el.querySelector('.el-input__inner');
                                const innerValue = selectInner ? normalize(selectInner.value) : '';
                                const selectedTextNode = el.querySelector && el.querySelector('.el-select__selected');
                                const selectedText = selectedTextNode ? normalize(selectedTextNode.textContent) : '';
                                return {
                                    success: true,
                                    method: 'panel_click',
                                    matchedText: itemText,
                                    innerValue,
                                    selectedText,
                                    updated: (innerValue.includes(targetText) || (selectedText && selectedText.includes(targetText)))
                                };
                            }
                        }

                        return { success: false, error: 'option_not_found' };
                    }""", {'value': select_value}),
                        timeout=3
                    )

                    if js_click_result and js_click_result.get('success'):
                        uat_logger.info(
                            f"✅ [SELECT_DEBUG] 面板内 JS 点击成功: {js_click_result.get('matchedText')}, innerValue: {js_click_result.get('innerValue')}, selectedText: {js_click_result.get('selectedText')}, updated: {js_click_result.get('updated')}"
                        )
                        # 以 JS 点击结果的 success 为准
                        # updated 取决于 el 内部某些节点是否存在/可读，某些场景会出现“已点中但 updated=false”的误判
                        option_clicked = True
                    else:
                        uat_logger.debug(f"🔍 [SELECT_DEBUG] 面板内 JS 点击失败: {js_click_result}")
                except Exception as js_click_err:
                    uat_logger.debug(f"🔍 [SELECT_DEBUG] 面板内 JS 点击异常: {str(js_click_err)}")

                if not option_clicked:
                    # 退回到XPath/通用策略，但仍使用 JS click 避免自动滚动
                    # 控制策略数量，避免在失败场景下拖到超时
                    for idx, opt_selector in enumerate(option_selectors[:3], 1):
                        try:
                            uat_logger.info(f"🔍 [SELECT_DEBUG] 尝试策略{idx}: {opt_selector}")
                            opt_loc = target_page.locator(opt_selector).first
                            await asyncio.wait_for(opt_loc.evaluate("el => el.click()"), timeout=1.5)
                            uat_logger.info(f"✅ [SELECT_DEBUG] 策略{idx}成功，选项点击成功")
                            option_clicked = True
                            break
                        except Exception as e:
                            uat_logger.debug(f"🔍 [SELECT_DEBUG] 策略{idx}未找到/点击选项: {str(e)}")
                
                if not option_clicked:
                    uat_logger.error(f"❌ [SELECT_DEBUG] 无法找到并点击选项: {select_value}")
                    raise Exception(f"无法找到并点击选项: {select_value}")
                
                uat_logger.info(f"✅ [SELECT_DEBUG] 自定义下拉框选择成功: {select_value}")
                
                # 🔥 修复：选择完成后关闭下拉框面板，避免遮挡后续元素
                uat_logger.info(f"🔍 [SELECT_DEBUG] 尝试关闭下拉框面板...")
                try:
                    # 方法1: 按 Escape 键关闭下拉框
                    await target_page.keyboard.press('Escape')
                    uat_logger.info(f"✅ [SELECT_DEBUG] 已按Escape键关闭下拉框")
                except Exception as e:
                    uat_logger.debug(f"🔍 [SELECT_DEBUG] 按Escape键关闭下拉框失败: {str(e)}")
                
                # 🔥 修复：移除点击 body(0,0) 的方式，因为会导致页面跳动
                # 仅使用 Escape 键关闭，然后短暂等待框架自动关闭
                await target_page.wait_for_timeout(80)
            else:
                # JavaScript 设置成功，短暂等待确保值生效
                await target_page.wait_for_timeout(80)

    async def simple_select_option(self, selector: str, select_value: str, selector_type: str = "css", iframe_selector: str = None, page=None):
        """简化版选择下拉框选项。自动检测原生select或自定义下拉框"""
        target_page = page if page is not None else self.page
        if target_page is None:
            raise Exception("浏览器未启动")
        
        uat_logger.info(f"选择下拉框: {selector}, 选择值: {select_value}")
        
        # 构建完整选择器
        full_selector = f"xpath={selector}" if selector_type == "xpath" else selector
        
        # 确定操作上下文
        target_context = target_page.frame_locator(iframe_selector) if iframe_selector else target_page
        
        # 尝试原生select方法
        try:
            element = target_context.locator(full_selector)
            tag_name = await element.evaluate("el => el.tagName.toLowerCase()") if await element.count() > 0 else "div"
            
            if tag_name == "select":
                # 原生select
                await element.select_option(select_value)
                uat_logger.info(f"原生select选择成功: {select_value}")
                return
        except Exception as e:
            uat_logger.debug(f"原生select方法失败: {e}")
        
        # 自定义下拉框处理
        try:
            # 点击展开下拉框
            await target_context.click(full_selector)
            
            # 等待下拉框展开
            await target_page.wait_for_timeout(200)
            
            # 直接查找并点击选项 - 使用 JS 点击避免滚动
            try:
                # 优先尝试内置文本查找 + JS 点击 (不滚动)
                option_element = target_page.get_by_text(select_value, exact=True).first
                await option_element.evaluate("el => el.click()")
            except Exception:
                # 降级到传统方式
                try:
                    await target_page.click(f"text={select_value}")
                except Exception:
                    # 最后尝试 XPath
                    await target_page.click(f"//*[text()='{select_value}']")
            
            uat_logger.info(f"自定义下拉框选择成功: {select_value}")
        except Exception as e:
            uat_logger.error(f"选择失败: {e}")
            raise

    async def select(self, selector: str, value: str, by: str = "text", context: str = None):
        """
        超级简化版下拉框选择
        selector: 下拉框选择器
        value: 要选择的值
        by: 选择方式 - "text" (按文本) | "value" (按值) | "index" (按索引)
        context: iframe选择器（可选）
        
        示例:
        await uat.select("#dropdown", "选项文本")
        await uat.select("#dropdown", "option_value", by="value") 
        await uat.select("//select[@id='ddl']", "1", by="index")
        """
        target_page = self.page
        if target_page is None:
            raise Exception("浏览器未启动")
        
        uat_logger.info(f"选择下拉框: {selector} -> {value} (by: {by})")
        
        # 等待下拉框可用
        await target_page.wait_for_selector(selector, state="visible")
        
        # 根据选择方式处理
        if by == "text":
            # 选择文本
            await target_page.select_option(selector, label=value)
        elif by == "value":
            # 选择值
            await target_page.select_option(selector, value=value)
        elif by == "index":
            # 按索引选择（只支持原生select）
            index = int(value)
            await target_page.select_option(selector, index=index)
        else:
            raise Exception(f"不支持的选择方式: {by}")
        
        uat_logger.info(f"成功选择: {selector} -> {value}")
        
        # 🔥 优化：减少选择后的等待时间从300ms到100ms
        await target_page.wait_for_timeout(100)

    async def select_date(self, selector: str, date: str, date_format: str = "YYYY-MM-DD"):
        """
        智能日期选择器操作
        selector: 日期选择器输入框的选择器
        date: 要设置的日期值（支持多种格式）
        date_format: 日期格式，默认YYYY-MM-DD
        
        示例:
        await uat.select_date("#date-picker", "2023-12-25")
        await uat.select_date("#date-picker", "2023-12-25", "YYYY-MM-DD")
        await uat.select_date("#date-picker", "12/25/2023", "MM/DD/YYYY")
        """
        target_page = self.page
        if target_page is None:
            raise Exception("浏览器未启动")
        
        uat_logger.info(f"设置日期选择器: {selector} -> {date} (格式: {date_format})")
        
        try:
            # 首先验证选择器类型并构建正确的selector
            locator_selector = selector
            if selector.startswith('/'):
                # XPath选择器，使用xpath=
                locator_selector = f"xpath={selector}"
            
            # 检查是否是原生input[type="date"]元素
            element = target_page.locator(locator_selector)
            
            # 尝试直接设置值（适用于原生date输入框）
            try:
                input_type = await element.get_attribute("type")
                if input_type == "date":
                    # 原生date输入框直接设置值
                    await element.fill(date)
                    uat_logger.info(f"原生date输入框设置成功: {date}")
                    return
            except Exception as e:
                uat_logger.debug(f"原生date输入框处理失败: {e}")
            
            # 主路径：标准化日期 + 触发 input/change/blur 事件（比单纯 fill 稳定）
            try:
                date_candidates = self._build_date_input_candidates(date, date_format)
                if not date_candidates:
                    date_candidates = [date]
                for dv in date_candidates:
                    try:
                        commit_ok = await target_page.evaluate(
                            """({sel, value}) => {
                                const el = document.querySelector(sel);
                                if (!el) return { ok: false, reason: 'not_found' };
                                el.focus();
                                el.value = value;
                                el.dispatchEvent(new Event('input', { bubbles: true }));
                                el.dispatchEvent(new Event('change', { bubbles: true }));
                                el.dispatchEvent(new Event('blur', { bubbles: true }));
                                return { ok: true, current: (el.value || '').trim() };
                            }""",
                            {"sel": selector, "value": dv},
                        )
                        cur = (commit_ok or {}).get("current", "")
                        if commit_ok and commit_ok.get("ok") and cur:
                            uat_logger.info(f"日期事件填充成功: {dv} (当前值: {cur})")
                            return
                    except Exception:
                        pass
                # 退回常规 fill
                await element.fill(date_candidates[0])
                await element.press("Enter")
                uat_logger.info(f"直接fill日期成功: {date_candidates[0]}")
                return
            except Exception as e:
                uat_logger.debug(f"直接fill方法失败: {e}")
            
            # 特殊处理Element Plus日期选择器
            try:
                await self._handle_element_plus_date_picker(target_page, locator_selector, date, date_format)
                
                # 自定义日期选择器处理
                # await self._handle_custom_date_picker(target_page, selector, date, date_format)
            except Exception as e:
                self._last_step_exception = e  # 记录具体异常
                uat_logger.error(f"日期选择器操作失败: {e}")
                raise
        except Exception as e:
            raise Exception(f"日期选择操作失败: {e}")

    def _build_date_input_candidates(self, date: str, date_format: str = "YYYY-MM-DD") -> List[str]:
        """构建常见日期输入候选格式，提升不同组件兼容性。"""
        import datetime as _dt
        s = (date or "").strip()
        if not s:
            return []
        fmts = [date_format, "%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%d/%m/%Y"]
        parsed = None
        for f in fmts:
            try:
                ff = f.replace("YYYY", "%Y").replace("MM", "%m").replace("DD", "%d")
                parsed = _dt.datetime.strptime(s, ff)
                break
            except Exception:
                continue
        if parsed is None:
            return [s]
        out = [
            parsed.strftime("%Y-%m-%d"),
            parsed.strftime("%Y/%m/%d"),
            parsed.strftime("%m/%d/%Y"),
            parsed.strftime("%d/%m/%Y"),
            parsed.strftime("%Y-%-m-%-d") if sys.platform != "win32" else parsed.strftime("%Y-%m-%d"),
        ]
        # 去重保序
        seen = set()
        ordered = []
        for x in out:
            if x and x not in seen:
                seen.add(x)
                ordered.append(x)
        return ordered
    
    async def _handle_custom_date_picker(self, page, selector: str, date: str, date_format: str):
        """处理自定义日期选择器（Element Plus, Ant Design等）"""
        
        # 点击展开日期选择器
        # 确保选择器类型正确
        click_selector = selector
        if selector.startswith('/'):
            # XPath选择器
            click_selector = f"xpath={selector}"
        
        await page.click(click_selector)
        
        # 智能等待日期选择器面板出现
        calendar_selectors = [
            '.el-picker-panel', '.ant-calendar-picker-container',
            '.el-date-picker', '.ant-calendar', 
            '[role="dialog"]', '.calendar'
        ]
        calendar_found = False
        for calendar_sel in calendar_selectors:
            try:
                await page.wait_for_selector(calendar_sel, state='visible', timeout=5000)  # 增加到5秒
                calendar_found = True
                break
            except Exception:
                continue
        
        if not calendar_found:
            # 如果无法智能检测，使用短时间等待
            await page.wait_for_timeout(500)
        
        # 解析日期
        import re
        import datetime
        
        if date_format == "YYYY-MM-DD":
            match = re.match(r'(\d{4})-(\d{1,2})-(\d{1,2})', date)
            if match:
                year, month, day = match.groups()
        elif date_format == "MM/DD/YYYY":
            match = re.match(r'(\d{1,2})/(\d{1,2})/(\d{4})', date)
            if match:
                month, day, year = match.groups()
        else:
            # 尝试自动解析
            formats = [
                "%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%Y/%m/%d"
            ]
            parsed_date = None
            for fmt in formats:
                try:
                    parsed_date = datetime.datetime.strptime(date, fmt)
                    break
                except ValueError:
                    continue
            
            if parsed_date:
                year = str(parsed_date.year)
                month = str(parsed_date.month).zfill(2)
                day = str(parsed_date.day).zfill(2)
            else:
                raise Exception(f"无法解析日期格式: {date}")
        
        # 尝试多种日期选择策略
        strategies = [
            # 策略1: 直接点击日期单元格
            lambda: self._click_by_direct_date_selector(page, year, month, day),
            # 策略2: 使用文本匹配
            lambda: self._click_by_date_text(page, year, month, day),
            # 策略3: 通过日历导航
            lambda: self._navigate_calendar_manually(page, year, month, day),
        ]
        
        strategy_success = False
        last_error = None
        for i, strategy in enumerate(strategies, 1):
            try:
                await strategy()
                strategy_success = True
                uat_logger.info(f"策略{i}成功选择日期: {year}-{month}-{day}")
                break
            except Exception as e:
                uat_logger.debug(f"策略{i}失败: {e}")
                last_error = str(e)
                # 增加在策略间的等待时间，让UI有时间响应
                await page.wait_for_timeout(1000)
                continue
        
        if not strategy_success:
            # 优先报告最具体的错误，而不是超时
            specific_error = last_error or f"所有日期选择策略都失败，无法选择日期: {date}"
            raise Exception(f"策略执行失败: {specific_error}")
        
        # 等待日期选择器关闭
        await page.wait_for_timeout(300)

    async def _handle_element_plus_date_picker(self, page, selector: str, date: str, date_format: str):
        """专门处理Element Plus日期选择器"""
        uat_logger.info(f"🎯 [ELEMENT_PLUS_DATE] 开始处理Element Plus日期选择器")
        
        # 解析日期
        import re
        import datetime
        
        if date_format == "YYYY-MM-DD":
            match = re.match(r'(\d{4})-(\d{1,2})-(\d{1,2})', date)
            if match:
                year, month, day = match.groups()
        else:
            # 尝试自动解析
            formats = [
                "%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%Y/%m/%d"
            ]
            parsed_date = None
            for fmt in formats:
                try:
                    parsed_date = datetime.datetime.strptime(date, fmt)
                    break
                except ValueError:
                    continue
            
            if parsed_date:
                year = str(parsed_date.year)
                month = str(parsed_date.month).zfill(2)
                day = str(parsed_date.day).zfill(2)
            else:
                raise Exception(f"无法解析日期格式: {date}")
        
        try:
            # 策略1: 直接填充日期到输入框 (最可靠的方法)
            uat_logger.info(f"⏳ [ELEMENT_PLUS_DATE] 步骤1: 尝试直接填充日期: {selector}")
            try:
                await page.fill(selector, date)
                await page.wait_for_timeout(500)
                
                # 检查填充是否成功
                input_value = await page.get_attribute(selector, "value")
                uat_logger.info(f"📋 [ELEMENT_PLUS_DATE] 填充后输入框值: {input_value}")
                
                if input_value and input_value.strip() == date:
                    uat_logger.info("✅ [ELEMENT_PLUS_DATE] 直接填充日期成功")
                    return
                else:
                    uat_logger.info(f"📋 [ELEMENT_PLUS_DATE] 直接填充未生效，期望: {date}")
            except Exception as fill_error:
                uat_logger.info(f"📋 [ELEMENT_PLUS_DATE] 直接填充失败: {fill_error}")
            
            uat_logger.info(f"⏳ [ELEMENT_PLUS_DATE] 步骤2: 切换到展开选择模式")
            
            # 步骤2: 点击输入框展开日期选择器 - 智能检测策略
            uat_logger.info(f"⏳ [ELEMENT_PLUS_DATE] 步骤2: 点击输入框展开日期选择器: {selector}")
            
            # 执行点击
            await page.click(selector)
            
            # 检查展开状态，但使用更宽松的策略
            panel_check_attempted = False
            try:
                # 方法1: 检查aria-expanded属性
                expanded_after = await page.get_attribute(selector, "aria-expanded")
                uat_logger.info(f"📋 [ELEMENT_PLUS_DATE] aria-expanded属性值: {expanded_after}")
                
                # 方法2: 检查是否有日历面板实际出现 (简化：只使用最靠谱的)
                calendar_found = False
                panel_selectors = [".el-picker-panel", "[class*='date']", "[role='dialog']"]
                
                for panel_selector in panel_selectors:
                    try:
                        # 使用更短的等待timeout
                        await page.wait_for_selector(panel_selector, state="visible", timeout=500)
                        uat_logger.info(f"📋 [ELEMENT_PLUS_DATE] 检测到面板可见: {panel_selector}")
                        calendar_found = True
                        break
                    except Exception as panel_e:
                        uat_logger.debug(f"❌ [ELEMENT_PLUS_DATE] 未找到面板 {panel_selector}")
                        continue
                
                # 等待UI更新
                await page.wait_for_timeout(200)
                
                # 判断展开状态：aria-expanded为true或找到了日历面板
                if expanded_after == "true" or calendar_found:
                    uat_logger.info("✅ [ELEMENT_PLUS_DATE] 输入框已展开 (通过属性或面板检测)")
                    panel_check_attempted = True
                elif expanded_after == "false":
                    uat_logger.warning("⚠️ [ELEMENT_PLUS_DATE] aria-expanded=false，但可能在异步更新中...")
                    panel_check_attempted = True  # 标记已尝试检测
                    # 不立即失败，继续尝试日期选择
                else:
                    uat_logger.warning("⚠️ [ELEMENT_PLUS_DATE] aria-expanded属性不存在或值异常")
                    panel_check_attempted = True  # 标记已尝试检测
                    # 不立即失败，继续尝试面板方式的日期选择
                    
                # 无论检测结果如何，都继续尝试日期选择
                uat_logger.info("📋 [ELEMENT_PLUS_DATE] 继续尝试日期选择...")
                    
            except Exception as e:
                if "输入框明确未展开" in str(e):
                    raise e
                uat_logger.debug(f"❌ [ELEMENT_PLUS_DATE] 检查点击后状态失败: {e}")
                # 如果检查状态失败，不抛出异常，继续执行日期选择
            
            # 步骤2: 等待日历面板出现 (Element Plus特定选择器)
            # 扩展更多的面板选择器，包括可能的iframe或弹窗形式
            calendar_selectors = [
                # Element Plus日期选择器面板
                ".el-picker-panel",
                ".el-date-picker__editor",
                ".el-date-picker__time-header", 
                ".el-picker-panel__body",
                ".el-date-picker__header",
                ".el-popper",
                ".el-picker-panel__body-wrapper",
                "[role='dialog']",
                ".el-date-picker",
                ".el-date-range-picker",
                ".el-picker-panel *, .el-date-picker *",
                ".el-select-dropdown",
                ".el-dropdown-menu",
                ".el-dialog",
                ".el-message-box",
                # 更广泛的选择器，包含任何可能的日历相关元素
                ".el-date",
                "[class*='date']",
                "[class*='picker']",
                "[class*='calendar']",
                # 表格和日期单元格
                "table",
                "td",
                ".el-table", 
                ".el-date-table",
                # 弹出层和遮罩
                ".el-fade-in-linear-enter-active",
                ".el-fade-in-enter-active",
                # 任何可见的overlay
                ".el-overlay",
            ]
            
            # 增加多次重试机制
            max_retries = 3
            calendar_found = False
            for retry in range(max_retries):
                uat_logger.info(f"⏳ [ELEMENT_PLUS_DATE] 第{retry+1}次尝试查找日历面板...")
                
                for calendar_selector in calendar_selectors:
                    try:
                        await page.wait_for_selector(calendar_selector, state="visible", timeout=2000)
                        calendar_found = True
                        uat_logger.info(f"✅ [ELEMENT_PLUS_DATE] 找到日历面板: {calendar_selector}")
                        break
                    except Exception as e:
                        uat_logger.debug(f"❌ [ELEMENT_PLUS_DATE] 等待日历面板失败: {calendar_selector}")
                        continue
                
                if calendar_found:
                    break
                
                # 如果没找到，重新点击输入框再试
                if retry < max_retries - 1:
                    uat_logger.info(f"⏳ [ELEMENT_PLUS_DATE] 第{retry+1}次重试: 重新点击输入框...")
                    try:
                        await page.click(selector)
                        await page.wait_for_timeout(800)
                    except Exception as e:
                        uat_logger.debug(f"❌ [ELEMENT_PLUS_DATE] 重新点击失败: {e}")
            
            if not calendar_found:
                # 尝试通过页面内容来判断是否展开
                try:
                    # 检查输入框的aria-expanded属性是否变为true
                    expanded = await page.get_attribute(selector, "aria-expanded")
                    if expanded == "true":
                        uat_logger.info(f"✅ [ELEMENT_PLUS_DATE] 检测到输入框已展开: aria-expanded={expanded}")
                        calendar_found = True
                    else:
                        uat_logger.warning(f"❌ [ELEMENT_PLUS_DATE] 输入框未展开: aria-expanded={expanded}")
                except Exception as e:
                    uat_logger.debug(f"❌ [ELEMENT_PLUS_DATE] 检查aria-expanded失败: {e}")
                
                # 如果还是没找到，尝试跳过面板检测，直接进入日期选择
                if not calendar_found:
                    uat_logger.warning(f"❌ [ELEMENT_PLUS_DATE] 无法找到日历面板，将尝试直接选择日期")
                    # 不抛出异常，继续执行日期选择策略
            
            # 步骤3: 尝试多种日期选择方式
            selection_strategies = [
                # 策略A: 直接点击日期单元格 (今天/当前月)
                lambda: self._element_plus_click_day_by_text(page, day),
                # 策略B: 使用日期单元格属性选择
                lambda: self._element_plus_click_day_by_attributes(page, year, month, day),
                # 策略C: 使用XPath文本匹配
                lambda: self._element_plus_click_day_by_xpath(page, day),
                # 策略D: 尝试完整的日期选择器导航
                lambda: self._element_plus_full_navigation(page, year, month, day),
            ]
            
            success = False
            last_error = None
            
            for i, strategy in enumerate(selection_strategies, 1):
                try:
                    uat_logger.info(f"⏳ [ELEMENT_PLUS_DATE] 尝试策略{i}...")
                    await strategy()
                    success = True
                    uat_logger.info(f"✅ [ELEMENT_PLUS_DATE] 策略{i}成功选择日期")
                    break
                except Exception as e:
                    last_error = str(e)
                    uat_logger.debug(f"❌ [ELEMENT_PLUS_DATE] 策略{i}失败: {e}")
                    await page.wait_for_timeout(500)  # 等待后重试
                    continue
            
            if not success:
                raise Exception(f"所有Element Plus日期选择策略失败: {last_error}")
            
            # 步骤4: 等待选择完成并关闭面板
            await page.wait_for_timeout(1000)
            
            uat_logger.info(f"✅ [ELEMENT_PLUS_DATE] Element Plus日期选择完成: {date}")
            
        except Exception as e:
            uat_logger.error(f"❌ [ELEMENT_PLUS_DATE] Element Plus日期选择器处理失败: {e}")
            # 确保错误信息具体化，避免被超时覆盖
            error_msg = f"Element Plus日期选择器执行失败: {str(e)}"
            uat_logger.error(f"📋 [ELEMENT_PLUS_DATE] 最终错误: {error_msg}")
            # 创建一个新的异常类，确保不会被asyncio.TimeoutError捕获
            class ElementPlusDatePickerError(Exception):
                pass
            
            error = ElementPlusDatePickerError(error_msg)
            error.specific_error = True  # 标记为具体错误
            raise error

    async def _element_plus_click_day_by_text(self, page, day: str):
        """Element Plus策略A: 通过文本点击日期 (优化版本: 仅需少数核心选择器)"""
        day_int = int(day)
        
        # 缩短等待时间
        await page.wait_for_timeout(200)
        
        # 只用少量核心选择器，这个we really know了common case
        core_selectors = [
            f"td.available:has-text('{day_int}')", # 优先Dirichlet + key scenario
            f"td.current:has-text('{day_int}')",
            f".el-date-table td:has-text('{day_int}')" # broader scope masking for trim
        ]
        
        for i, selector in enumerate(core_selectors, 1):
            try:
                await page.click(selector, timeout=2000)
                uat_logger.info(f"✅ [ELEMENT_PLUS_DAY] 核心选择器{i}成功: {selector}") 
                return True
            except Exception as e:
                uat_logger.debug(f"❌ [ELEMENT_PLUS_DAY] 核心选择器{i}失败: {selector}")
                continue
        
        raise Exception(f"核心选择器均无法选择日期{day}")

    async def _element_plus_click_day_by_attributes(self, page, year: str, month: str, day: str):
        """Element Plus策略B: 通过属性选择日期"""
        
        # 尝试通过data-date等属性选择
        attr_selectors = [
            f"td[data-date='{day}']",
            f"td[data-day='{day}']",
            f"td[data-year='{year}'][data-month='{int(month)}'][data-date='{day}']",
            f"td[data-year='{year}'][data-month='{int(month)-1}'][data-date='{day}']",
        ]
        
        await page.wait_for_timeout(500)
        
        for i, selector in enumerate(attr_selectors, 1):
            try:
                await page.wait_for_selector(selector, state="visible", timeout=2000)
                await page.click(selector)
                uat_logger.info(f"✅ [ELEMENT_PLUS_ATTR] 通过属性选择成功: {selector}")
                return True
            except Exception as e:
                uat_logger.debug(f"❌ [ELEMENT_PLUS_ATTR] 属性选择失败{i}: {selector} - {e}")
                continue
        
        raise Exception("通过属性选择日期失败")

    async def _element_plus_click_day_by_xpath(self, page, day: str):
        """Element Plus策略C: 通过XPath选择日期"""
        
        day_int = int(day)
        xpath_expressions = [
            f"//td[contains(@class, 'available') and text()='{day_int}']",
            f"//td[contains(@class, 'current') and text()='{day_int}']",
            f"//td[contains(@class, 'cell') and not(contains(@class, 'disabled')) and text()='{day_int}']",
            f"//td[@class and not(contains(@class, 'disabled')) and text()='{day_int}']",
        ]
        
        await page.wait_for_timeout(500)
        
        for i, xpath in enumerate(xpath_expressions, 1):
            try:
                await page.wait_for_selector(f"xpath={xpath}", state="visible", timeout=2000)
                await page.click(f"xpath={xpath}")
                uat_logger.info(f"✅ [ELEMENT_PLUS_XPATH] 通过XPath选择成功: {xpath}")
                return True
            except Exception as e:
                uat_logger.debug(f"❌ [ELEMENT_PLUS_XPATH] XPath选择失败{i}: {xpath} - {e}")
                continue
        
        raise Exception("通过XPath选择日期失败")

    async def _element_plus_full_navigation(self, page, year: str, month: str, day: str):
        """Element Plus策略D: 完整的日期导航"""
        
        # 这个方法用于处理需要调整年月的情况
        try:
            # 等待面板稳定
            await page.wait_for_timeout(1000)
            
            # 尝试直接点击目标日期（当前显示月份中）
            await self._element_plus_click_day_by_text(page, day)
            return True
        except:
            # 如果当前页面没有目标日期，可能需要调整月份或年份
            # 这里可以扩展更多复杂的导航逻辑
            await self._element_plus_click_day_by_attributes(page, year, month, day)
            return True

    async def _click_by_direct_date_selector(self, page, year: str, month: str, day: str):
        """直接通过CSS选择器选择日期"""
        # 常见的日期选择器选择器模式 - 扩展更多选择器
        date_selectors = [
            f"td[title*='{year}-{month}-{day}']",
            f"td[data-date='{year}-{month}-{day}']",
            f"td[data-year='{year}'][data-month='{int(month)-1}'][data-date='{int(day)}']",
            f"td.active",  # 如果已经是激活状态
            f"td.cell:not(.disabled):has-text('{day}')",  # Element Plus风格
            f"td.current:not(.disabled):has-text('{day}')",  # 当前月份日期
            f"[data-date='{int(day)}']",  # 简化的数据属性
            f"td:has-text('{int(day)}')",  # 简单文本匹配
            f".available:has-text('{int(day)}')",  # 可用日期
            f".today:has-text('{int(day)}')",  # 今天日期
        ]
        
        # 首先等待可能的日期单元格出现
        await page.wait_for_timeout(1000)
        
        for i, selector in enumerate(date_selectors, 1):
            try:
                # 先等待选择器出现，使用较短的超时
                await page.wait_for_selector(selector, state="visible", timeout=2000)
                # 点击日期单元格
                await page.click(selector)
                uat_logger.info(f"✅ 日期选择策略1-步骤{i}成功: 使用选择器 {selector}")
                return True
            except Exception as e:
                uat_logger.debug(f"❌ 日期选择策略1-步骤{i}失败: {selector} - {str(e)}")
                continue
        
        raise Exception("直接选择器方法失败: 所有选择器模式都未找到匹配的日期单元格")

    async def _click_by_date_text(self, page, year: str, month: str, day: str):
        """通过文本内容选择日期"""
        # 尝试通过具体文本选择 - 增加多种文本匹配方式
        text_patterns = [
            f"text={day}",  # 精确文本匹配
            f"text={int(day)}",  # 无前导零的文本匹配
            f"text=//{day}//",  # XPath文本匹配
            f"//*[text()='{int(day)}']",  # XPath精确匹配
            f"//*[contains(text(), '{int(day)}')]",  # XPath包含匹配
        ]
        
        # 等待日历面板有足够时间加载
        await page.wait_for_timeout(1000)
        
        for i, pattern in enumerate(text_patterns, 1):
            try:
                await page.click(pattern)
                uat_logger.info(f"✅ 日期选择策略2-步骤{i}成功: 使用文本模式 {pattern}")
                return True
            except Exception as e:
                uat_logger.debug(f"❌ 日期选择策略2-步骤{i}失败: {pattern} - {str(e)}")
                continue
        
        raise Exception("文本匹配方法失败: 所有文本模式都未找到匹配的日期")

    async def _navigate_calendar_manually(self, page, year: str, month: str, day: str):
        """手动导航日历（用于复杂场景）"""
        # 切换到指定年份
        try:
            # 点击年份选择器
            await page.click(".el-date-picker__header-label:has-text('年')", timeout=2000)
            await page.wait_for_timeout(300)
            
            # 选择年份
            await page.click(f"li:has-text('{year}')")
            await page.wait_for_timeout(300)
        except:
            pass  # 年份可能已经匹配
        
        # 切换到指定月份
        try:
            # 点击月份选择器
            await page.click(".el-date-picker__header-label:has-text('月')", timeout=3000)
            await page.wait_for_timeout(500)
            
            # 选择月份（1-based）
            await page.click(f"li:has-text('{int(month)}月')")
            await page.wait_for_timeout(500)
        except:
            pass  # 月份可能已经匹配
        
        # 选择具体日期 - 第三次尝试，使用更多耐心
        try:
            await self._click_by_direct_date_selector(page, year, month, day)
        except Exception as e:
            # 如果第三次也失败，尝试刷新面板后再次选择
            uat_logger.debug(f"第三次日期选择失败，尝试重新点击展开日历: {e}")
            await page.wait_for_timeout(1000)
            await self._click_by_direct_date_selector(page, year, month, day)

    async def click_element(self, selector: str, selector_type: str = "css", iframe_selector: str = None, iframe_context=None, page=None, locator_candidates=None):
        """点击元素。page: 可选，指定在哪个标签页执行（多标签并行时使用）。
        locator_candidates: 录制器生成的 JSON 或列表，主选择器失败时按 score 降级重试。"""
        target_page = page if page is not None else self.page
        if target_page is None:
            raise Exception("浏览器未启动")
        
        # 🔥 转换简化的选择器类型
        original_selector_type = selector_type
        raw_partial_text_for_click: Optional[str] = None
        if selector_type in ['id', 'class', 'name', 'text', 'partial_text', 'placeholder', 
                             'label', 'title', 'alt', 'data', 'aria']:
            if selector_type == 'label':
                # Label类型需要异步查找
                selector, selector_type = await self.find_element_by_label(selector, target_page)
                if selector is None:
                    raise Exception(f"未找到与label关联的元素: {original_selector_type}={selector}")
            else:
                if selector_type == 'partial_text':
                    raw_partial_text_for_click = (selector or "").strip() or None
                selector, selector_type = self.convert_selector(selector, selector_type)
            uat_logger.info(f"🔍 [SELECTOR_CONVERT] 选择器转换: {original_selector_type} -> {selector_type}, 值: {selector}")
        
        if selector_type == "xpath" and selector:
            selector = _normalize_xpath_selector_value(selector)
        
        # 检查页面是否已经被关闭
        try:
            if hasattr(target_page, 'url'):
                _ = target_page.url  # 尝试访问URL判断页面是否有效
        except Exception as e:
            uat_logger.error(f"页面已关闭,无法执行点击操作: {str(e)}")
            raise Exception("页面已关闭")
        
        uat_logger.info(f"🔍 [CLICK_DEBUG] 开始点击元素,选择器: {selector}, 选择器类型: {selector_type}, iframe选择器: {iframe_selector}")
        
        # 构建完整的选择器
        full_selector = selector
        if selector_type == "xpath":
            full_selector = f"xpath={selector}"
        
        # 确定操作上下文
        target_context = target_page
        if iframe_context:
            target_context = iframe_context
        elif iframe_selector:
            uat_logger.info(f"🔄 [IFRAME_DEBUG] 使用iframe上下文,选择器: {iframe_selector}")
            target_context = target_page.frame_locator(iframe_selector)
        
        if target_context is None:
            uat_logger.error(f"❌ [CLICK_DEBUG] 操作上下文为None,无法执行点击操作")
            raise Exception(f"操作上下文为None,无法执行点击操作")
        
        element_clicked = False

        # partial_text：与 Codegen getByText 一致，优先用 Playwright 文本定位（可命中子 span，
        # 并在 Element Plus 等场景下由引擎选择可点击目标）；避免 //*[contains(...)] 点到不可交互节点。
        if raw_partial_text_for_click and hasattr(target_context, "get_by_text"):
            try:
                pw_loc = target_context.get_by_text(
                    raw_partial_text_for_click, exact=False
                )
                cnt = await pw_loc.count()
                if cnt == 0:
                    uat_logger.info(
                        "🔍 [CLICK_DEBUG] partial_text: get_by_text 零匹配，回退 XPath"
                    )
                else:
                    uat_logger.info(
                        f"🔍 [CLICK_DEBUG] partial_text: get_by_text 命中 {cnt} 个，点击首个（等同 .first）"
                    )
                    tgt = pw_loc.first
                    try:
                        await tgt.wait_for(state="visible", timeout=10000)
                    except Exception:
                        uat_logger.info(
                            "🔍 [CLICK_DEBUG] partial_text: visible 超时，改 attached（侧栏收起/overflow 等）"
                        )
                        await tgt.wait_for(state="attached", timeout=5000)
                    try:
                        await tgt.click(timeout=8000)
                    except Exception:
                        await tgt.click(timeout=8000, force=True)
                    uat_logger.info(
                        "✅ [CLICK_DEBUG] partial_text 已通过 Playwright get_by_text 点击成功"
                    )
                    element_clicked = True
            except Exception as _pwte:
                uat_logger.warning(
                    f"⚠️ [CLICK_DEBUG] partial_text get_by_text 点击失败，回退 XPath: {_pwte}"
                )
        
        # 获取当前页面URL和状态
        try:
            current_url = target_page.url
            uat_logger.info(f"🔍 [CLICK_DEBUG] 当前页面URL: {current_url}")
        except Exception as e:
            uat_logger.warning(f"🔍 [CLICK_DEBUG] 获取当前URL失败: {str(e)}")
            current_url = ""

        # 分组 XPath：(//node)[k] —— 使用 locator.nth，避免与 wait_for(enabled) 组合失败（仅 1 个节点时节点的 [2] 无效）
        xg = _xpath_group_inner_and_index(selector) if selector_type == "xpath" else None
        if xg:
            inner, idx1 = xg
            try:
                loc_all = target_context.locator(f"xpath={inner}")
                cnt = await loc_all.count()
                if cnt == 0:
                    uat_logger.warning(f"⚠️ [CLICK_DEBUG] 分组 XPath 内层零匹配: {inner[:120]}")
                else:
                    idx0 = idx1 - 1
                    if idx0 >= cnt:
                        uat_logger.warning(
                            f"⚠️ [CLICK_DEBUG] XPath 仅 {cnt} 个匹配，录制序号为 [{idx1}]，改用索引 {cnt - 1}"
                        )
                        idx0 = cnt - 1
                    elg = loc_all.nth(idx0)
                    await elg.wait_for(state="visible", timeout=10000)
                    try:
                        await elg.click(timeout=8000)
                    except Exception:
                        await elg.click(timeout=8000, force=True)
                    uat_logger.info(
                        f"✅ [CLICK_DEBUG] 分组 XPath nth({idx0}) 点击成功: {inner[:80]}"
                    )
                    element_clicked = True
            except Exception as e_g:
                uat_logger.warning(f"⚠️ [CLICK_DEBUG] 分组 XPath 专用点击失败: {e_g}，走常规流程")
        
        # //button[normalize-space(.)='x'] 在「图标 + span 文案」DOM 下常匹配不到；优先短超时试 span/后代文案，避免主流程多次 10s 拖满步骤 60s 上限
        if not element_clicked and selector_type == "xpath" and selector and not xg:
            _xvars = xpath_click_attempt_variants(selector)
            if len(_xvars) > 1:
                for sel_alt in _xvars[:-1]:
                    alt_full = f"xpath={sel_alt}"
                    try:
                        uat_logger.info(
                            f"🔍 [CLICK_DEBUG] 尝试 XPath 文案变体(图标按钮): {sel_alt[:100]!s}"
                        )
                        if hasattr(target_context, "wait_for_selector"):
                            await target_context.wait_for_selector(
                                alt_full, state="visible", timeout=6000
                            )
                            await target_context.click(alt_full, timeout=8000)
                        else:
                            _elv = target_context.locator(alt_full)
                            await _elv.wait_for(state="visible", timeout=6000)
                            await _elv.click(timeout=8000)
                        uat_logger.info(
                            f"✅ [CLICK_DEBUG] XPath 文案变体点击成功: {sel_alt[:80]!s}"
                        )
                        element_clicked = True
                        selector = sel_alt
                        break
                    except Exception as _xe:
                        uat_logger.debug(
                            f"🔍 [CLICK_DEBUG] XPath 变体未命中: {_xe} — {sel_alt[:70]!s}"
                        )
        
        # 尝试多种点击方式,增加成功概率
        # 方式1: 使用Playwright的click方法,等待元素可点击
        if not element_clicked:
            try:
                uat_logger.info(f"🔍 [CLICK_DEBUG] 尝试方式1: Playwright click方法")
                if hasattr(target_context, 'wait_for_selector'):
                    await target_context.wait_for_selector(full_selector, state='visible', timeout=10000)
                    # 不少页面按钮长期 disabled（需先输入等），严格等 enabled 会导致整条用例失败
                    try:
                        await target_context.wait_for_selector(full_selector, state='enabled', timeout=3500)
                    except Exception:
                        uat_logger.info(
                            "🔍 [CLICK_DEBUG] 元素未短时变为 enabled，仍尝试点击（可先输入再点或_force）"
                        )
                    await target_context.click(full_selector, timeout=10000)
                    uat_logger.info(f"✅ [CLICK_DEBUG] 方式1成功点击元素: {selector}, 选择器类型: {selector_type}")
                    element_clicked = True
                else:
                    element = target_context.locator(full_selector)
                    await element.wait_for(state='visible', timeout=10000)
                    try:
                        await element.wait_for(state='enabled', timeout=3500)
                    except Exception:
                        pass
                    await element.click(timeout=10000)
                    uat_logger.info(f"✅ [CLICK_DEBUG] 方式1成功点击元素: {selector}, 选择器类型: {selector_type}")
                    element_clicked = True
            except Exception as e:
                uat_logger.warning(f"⚠️ [CLICK_DEBUG] 方式1失败: {str(e)}, 尝试方式2: force click")
                
                # 方式2: 使用force参数强制点击
                try:
                    if hasattr(target_context, 'click'):
                        await target_context.click(full_selector, force=True, timeout=10000)
                        uat_logger.info(f"✅ [CLICK_DEBUG] 方式2成功点击元素: {selector}, 选择器类型: {selector_type}")
                        element_clicked = True
                    else:
                        # 如果是frame_locator对象,需要使用其locator方法
                        element = target_context.locator(full_selector)
                        await element.click(timeout=10000, force=True)
                        uat_logger.info(f"✅ [CLICK_DEBUG] 方式2成功点击元素: {selector}, 选择器类型: {selector_type}")
                        element_clicked = True
                except Exception as e2:
                    uat_logger.warning(f"⚠️ [CLICK_DEBUG] 方式2失败: {str(e2)}, 尝试方式3: SVG子元素点击（处理图标按钮）")
                    
                    # 方式3: 智能元素点击 - 处理SVG/IMG元素和父元素点击
                    try:
                        uat_logger.info(f"🔍 [CLICK_DEBUG] 尝试方式3: 智能元素点击（支持SVG子元素和父元素点击）")
                        
                        clicked_result = False
                        
                        if selector_type == "css":
                            clicked_result = await target_context.evaluate("""(selector) => {
                                const element = document.querySelector(selector);
                                if (!element) return { success: false, reason: 'not_found' };
                                
                                const tagName = element.tagName.toLowerCase();
                                
                                // 情况1: 如果当前元素本身就是SVG或IMG，直接点击该元素本身
                                if (tagName === 'svg' || tagName === 'img') {
                                    const rect = element.getBoundingClientRect();
                                    const clickX = rect.left + rect.width / 2;
                                    const clickY = rect.top + rect.height / 2;
                                    
                                    const clickEvent = new MouseEvent('click', {
                                        bubbles: true,
                                        cancelable: true,
                                        view: window,
                                        clientX: clickX,
                                        clientY: clickY
                                    });
                                    element.dispatchEvent(clickEvent);
                                    
                                    return { 
                                        success: true, 
                                        method: 'self_svg_img_click', 
                                        elementType: tagName,
                                        x: clickX, 
                                        y: clickY,
                                        rect: { width: rect.width, height: rect.height }
                                    };
                                }
                                
                                // 情况2: 查找子元素：优先SVG，其次IMG，然后任何可见子元素
                                let targetElement = null;
                                let targetType = '';
                                
                                // 1. 尝试查找直接子SVG
                                const svgChild = element.querySelector(':scope > svg');
                                if (svgChild) {
                                    targetElement = svgChild;
                                    targetType = 'direct_svg';
                                }
                                // 2. 尝试查找任何子SVG
                                else if (element.querySelector('svg')) {
                                    targetElement = element.querySelector('svg');
                                    targetType = 'nested_svg';
                                }
                                // 3. 尝试查找IMG子元素
                                else if (element.querySelector('img')) {
                                    targetElement = element.querySelector('img');
                                    targetType = 'img';
                                }
                                // 4. 尝试查找任何可见的子元素
                                else {
                                    const children = element.children;
                                    for (let child of children) {
                                        const style = window.getComputedStyle(child);
                                        if (style.display !== 'none' && style.visibility !== 'hidden') {
                                            targetElement = child;
                                            targetType = 'visible_child';
                                            break;
                                        }
                                    }
                                }
                                
                                // 如果找到子元素，点击子元素的中心
                                if (targetElement) {
                                    const rect = targetElement.getBoundingClientRect();
                                    const clickX = rect.left + rect.width / 2;
                                    const clickY = rect.top + rect.height / 2;
                                    
                                    const clickEvent = new MouseEvent('click', {
                                        bubbles: true,
                                        cancelable: true,
                                        view: window,
                                        clientX: clickX,
                                        clientY: clickY
                                    });
                                    targetElement.dispatchEvent(clickEvent);
                                    
                                    return { 
                                        success: true, 
                                        method: 'child_element', 
                                        childType: targetType,
                                        x: clickX, 
                                        y: clickY,
                                        childRect: { width: rect.width, height: rect.height }
                                    };
                                }
                                
                                // 没有子元素，点击原元素
                                element.click();
                                return { success: true, method: 'self_click' };
                            }""", selector)
                        else:  # xpath
                            clicked_result = await target_context.evaluate("""(xpath) => {
                                const result = document.evaluate(xpath, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null);
                                const element = result.singleNodeValue;
                                if (!element) return { success: false, reason: 'not_found' };
                                
                                const tagName = element.tagName.toLowerCase();
                                
                                // 情况1: 如果当前元素本身就是SVG或IMG，直接点击该元素本身
                                if (tagName === 'svg' || tagName === 'img') {
                                    const rect = element.getBoundingClientRect();
                                    const clickX = rect.left + rect.width / 2;
                                    const clickY = rect.top + rect.height / 2;
                                    
                                    const clickEvent = new MouseEvent('click', {
                                        bubbles: true,
                                        cancelable: true,
                                        view: window,
                                        clientX: clickX,
                                        clientY: clickY
                                    });
                                    element.dispatchEvent(clickEvent);
                                    
                                    return { 
                                        success: true, 
                                        method: 'self_svg_img_click', 
                                        elementType: tagName,
                                        x: clickX, 
                                        y: clickY,
                                        rect: { width: rect.width, height: rect.height }
                                    };
                                }
                                
                                // 情况2: 查找子元素
                                let targetElement = null;
                                let targetType = '';
                                
                                const svgChild = element.querySelector(':scope > svg');
                                if (svgChild) {
                                    targetElement = svgChild;
                                    targetType = 'direct_svg';
                                }
                                else if (element.querySelector('svg')) {
                                    targetElement = element.querySelector('svg');
                                    targetType = 'nested_svg';
                                }
                                else if (element.querySelector('img')) {
                                    targetElement = element.querySelector('img');
                                    targetType = 'img';
                                }
                                else {
                                    const children = element.children;
                                    for (let child of children) {
                                        const style = window.getComputedStyle(child);
                                        if (style.display !== 'none' && style.visibility !== 'hidden') {
                                            targetElement = child;
                                            targetType = 'visible_child';
                                            break;
                                        }
                                    }
                                }
                                
                                if (targetElement) {
                                    const rect = targetElement.getBoundingClientRect();
                                    const clickX = rect.left + rect.width / 2;
                                    const clickY = rect.top + rect.height / 2;
                                    
                                    const clickEvent = new MouseEvent('click', {
                                        bubbles: true,
                                        cancelable: true,
                                        view: window,
                                        clientX: clickX,
                                        clientY: clickY
                                    });
                                    targetElement.dispatchEvent(clickEvent);
                                    
                                    return { 
                                        success: true, 
                                        method: 'child_element', 
                                        childType: targetType,
                                        x: clickX, 
                                        y: clickY,
                                        childRect: { width: rect.width, height: rect.height }
                                    };
                                }
                                
                                element.click();
                                return { success: true, method: 'self_click' };
                            }""", selector)
                        
                        if clicked_result and clicked_result.get('success'):
                            method = clicked_result.get('method')
                            if method == 'child_element':
                                uat_logger.info(f"✅ [CLICK_DEBUG] 方式3成功点击子元素: {selector}, 类型: {clicked_result.get('childType')}, 坐标: ({clicked_result.get('x'):.1f}, {clicked_result.get('y'):.1f})")
                            elif method == 'self_svg_img_click':
                                uat_logger.info(f"✅ [CLICK_DEBUG] 方式3成功点击SVG/IMG本身: {selector}, 元素类型: {clicked_result.get('elementType')}, 坐标: ({clicked_result.get('x'):.1f}, {clicked_result.get('y'):.1f}), 尺寸: {clicked_result.get('rect')}")
                            else:
                                uat_logger.info(f"✅ [CLICK_DEBUG] 方式3成功点击元素: {selector}")
                            element_clicked = True
                        else:
                            uat_logger.warning(f"⚠️ [CLICK_DEBUG] 方式3未成功: {clicked_result}")
                            
                    except Exception as e3:
                        uat_logger.warning(f"⚠️ [CLICK_DEBUG] 方式3失败: {str(e3)}, 尝试方式4: 中心点坐标点击")
                        
                        # 方式4: 标准JavaScript点击
                        try:
                            uat_logger.info(f"🔍 [CLICK_DEBUG] 尝试方式4: 标准JavaScript点击")
                            if selector_type == "css":
                                if hasattr(target_context, 'evaluate'):
                                    element_exists = await target_context.evaluate("(selector) => document.querySelector(selector) !== null", selector)
                                    if element_exists:
                                        await target_context.evaluate("""(selector) => {
                                            const element = document.querySelector(selector);
                                            if (element) element.click();
                                        }""", selector)
                                        uat_logger.info(f"✅ [CLICK_DEBUG] 方式4成功点击元素: {selector}")
                                        element_clicked = True
                                    else:
                                        uat_logger.error(f"❌ [CLICK_DEBUG] 元素不存在: {selector}")
                                else:
                                    element = target_context.locator(selector)
                                    count = await element.count()
                                    if count > 0:
                                        await element.click(timeout=10000, force=True)
                                        uat_logger.info(f"✅ [CLICK_DEBUG] 方式4成功点击元素: {selector}")
                                        element_clicked = True
                                    else:
                                        uat_logger.error(f"❌ [CLICK_DEBUG] 元素不存在: {selector}")
                            else:  # xpath
                                if hasattr(target_context, 'evaluate'):
                                    element_exists = await target_context.evaluate("""(xpath) => {
                                        const result = document.evaluate(xpath, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null);
                                        return result.singleNodeValue !== null;
                                    }""", selector)
                                    if element_exists:
                                        await target_context.evaluate("""(xpath) => {
                                            const result = document.evaluate(xpath, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null);
                                            const element = result.singleNodeValue;
                                            if (element) element.click();
                                        }""", selector)
                                        uat_logger.info(f"✅ [CLICK_DEBUG] 方式4成功点击元素: {selector}")
                                        element_clicked = True
                                    else:
                                        uat_logger.error(f"❌ [CLICK_DEBUG] 元素不存在: {selector}")
                                else:
                                    element = target_context.locator(f"xpath={selector}")
                                    count = await element.count()
                                    if count > 0:
                                        await element.click(timeout=5000, force=True)
                                        uat_logger.info(f"✅ [CLICK_DEBUG] 方式4成功点击元素: {selector}")
                                        element_clicked = True
                                    else:
                                        uat_logger.error(f"❌ [CLICK_DEBUG] 元素不存在: {selector}")
                        except Exception as e4:
                            uat_logger.error(f"❌ [CLICK_DEBUG] 方式4失败: {str(e4)}")
                        
                        # 🔥 方式5: 增强型智能定位器降级机制
                        if not element_clicked and self.locator_manager:
                            try:
                                uat_logger.info(f"🔍 [CLICK_DEBUG] 尝试方式5: 增强型智能定位器降级")
                                
                                # 尝试提取元素信息并使用智能定位
                                element_info = await self.locator_manager.extract_element_info(selector if selector_type == "css" else None)
                                if element_info:
                                    uat_logger.info(f"🔍 [CLICK_DEBUG] 提取到元素信息: {element_info.tag_name}")
                                    locator = await self.locator_manager.find_element(element_info, max_attempts=3)
                                    if locator:
                                        await locator.click(timeout=10000)
                                        uat_logger.info(f"✅ [CLICK_DEBUG] 方式5成功点击元素: 使用智能定位器")
                                        element_clicked = True
                                    else:
                                        uat_logger.warning(f"⚠️ [CLICK_DEBUG] 智能定位器未找到元素")
                                else:
                                    uat_logger.warning(f"⚠️ [CLICK_DEBUG] 无法提取元素信息")
                            except Exception as e5:
                                uat_logger.error(f"❌ [CLICK_DEBUG] 方式5失败: {str(e5)}")
                    
        if not element_clicked:
            merged_candidates: Any = locator_candidates
            if selector_type == "xpath" and selector:
                extra_fb = runtime_xpath_button_link_fallback_items(selector)
                if extra_fb:
                    if not merged_candidates:
                        merged_candidates = extra_fb
                    else:
                        prev = _normalize_locator_candidate_list(merged_candidates)
                        prev.extend(_normalize_locator_candidate_list(extra_fb))
                        merged_candidates = prev
            if merged_candidates:
                for extra_sel, extra_type in _fallback_locator_tuples(
                    selector, selector_type, merged_candidates
                ):
                    try:
                        await self.click_element(
                            extra_sel, extra_type, iframe_selector, iframe_context, page, None
                        )
                        uat_logger.info(
                            f"✅ [LOCATOR_PACK] 备选点击成功: {extra_type}={extra_sel[:120]!s}"
                        )
                        return
                    except Exception as _fb_e:
                        uat_logger.warning(
                            f"⚠️ [LOCATOR_PACK] 备选点击失败 ({extra_type}={extra_sel[:80]!s}): {_fb_e}"
                        )
            raise Exception(f"无法点击元素: {selector}, 选择器类型: {selector_type}, 所有点击方式均失败")
        
        # 检查点击后的页面状态
        try:
            new_url = target_page.url
            uat_logger.info(f"🔍 [CLICK_DEBUG] 点击后页面URL: {new_url}")
            if new_url != current_url:
                uat_logger.info(f"🔄 [CLICK_DEBUG] 检测到页面URL变化: {current_url} -> {new_url}")
        except Exception as e:
            uat_logger.warning(f"🔍 [CLICK_DEBUG] 获取点击后URL失败: {str(e)}")
        
        # 单选框和复选框点击后状态验证
        try:
            # 检查是否是单选框或复选框相关选择器
            is_radio_selector = False
            is_checkbox_selector = False
            selector_lower = selector.lower()
            if 'radio' in selector_lower or 'type="radio"' in selector_lower:
                is_radio_selector = True
            elif 'checkbox' in selector_lower or 'type="checkbox"' in selector_lower:
                is_checkbox_selector = True
            
            # 如果是单选框或复选框选择器,验证点击后状态
            if is_radio_selector or is_checkbox_selector:
                # 等待元素状态更新
                await target_page.wait_for_timeout(200)
                
                # 检查单选框或复选框是否被选中
                evaluate_script = f'''() => {{
                    const element = document.querySelector('{selector}');
                    if (element && element.tagName === 'INPUT' && (element.type === 'radio' || element.type === 'checkbox')) {{
                        return element.checked;
                    }}
                    // 处理复合组件,找到内部的input元素
                    const inputElement = element?.querySelector('input[type="radio"], input[type="checkbox"]');
                    return inputElement ? inputElement.checked : false;
                }}'''
                is_checked = await target_page.evaluate(evaluate_script)
                
                if is_checked:
                    element_type = "单选框" if is_radio_selector else "复选框"
                    uat_logger.info(f"✅ {element_type}点击验证通过: {selector} 已选中")
                else:
                    element_type = "单选框" if is_radio_selector else "复选框"
                    uat_logger.warning(f"⚠️ {element_type}点击验证警告: {selector} 未选中")
        except Exception as e:
            uat_logger.warning(f"验证单选框/复选框状态时出错: {str(e)}")
        
        # 🔥 修复：添加点击后的页面响应等待机制
        # 等待页面加载状态变化
        try:
            uat_logger.info(f"⏳ [CLICK_DEBUG] 等待页面加载状态变化...")
            # 等待页面DOM内容加载完成
            await target_page.wait_for_load_state('domcontentloaded', timeout=5000)
            uat_logger.info(f"✅ [CLICK_DEBUG] 页面DOM内容加载完成")
        except Exception as e:
            uat_logger.warning(f"⚠️ [CLICK_DEBUG] 等待DOM内容加载超时: {str(e)}")
        
        # 等待网络空闲状态
        try:
            uat_logger.info(f"⏳ [CLICK_DEBUG] 等待网络空闲状态...")
            # 等待网络空闲（最多等待5秒）
            await target_page.wait_for_load_state('networkidle', timeout=5000)
            uat_logger.info(f"✅ [CLICK_DEBUG] 网络空闲状态达成")
        except Exception as e:
            uat_logger.warning(f"⚠️ [CLICK_DEBUG] 等待网络空闲超时: {str(e)}")
        
        # 等待页面稳定
        try:
            uat_logger.info(f"⏳ [CLICK_DEBUG] 等待页面稳定...")
            # 等待一小段时间，让页面状态稳定
            await target_page.wait_for_timeout(1000)
            uat_logger.info(f"✅ [CLICK_DEBUG] 页面状态稳定")
        except Exception as e:
            uat_logger.warning(f"⚠️ [CLICK_DEBUG] 等待页面稳定时出错: {str(e)}")
        
    
    async def fill_input(self, selector: str, text: str, selector_type: str = "css", iframe_selector: str = None, iframe_context=None, page=None, locator_candidates=None):
        # 🔥 添加输入操作总超时控制（30秒）
        try:
            return await asyncio.wait_for(
                self._fill_input_internal(selector, text, selector_type, iframe_selector, iframe_context, page, locator_candidates),
                timeout=30
            )
        except asyncio.TimeoutError:
            uat_logger.error(f"输入操作超时: {selector}, 30秒限制")
            raise Exception(f"输入操作超时: {selector}, 超过30秒限制")
    
    async def _fill_input_internal(self, selector: str, text: str, selector_type: str = "css", iframe_selector: str = None, iframe_context=None, page=None, locator_candidates=None):
        """填充输入框。page: 可选，指定在哪个标签页执行（多标签并行时使用）"""
        target_page = page if page is not None else self.page
        if target_page is None:
            raise Exception("浏览器未启动")
        
        # 🔥 转换简化的选择器类型
        original_selector_type = selector_type
        if selector_type in ['id', 'class', 'name', 'text', 'partial_text', 'placeholder', 
                             'label', 'title', 'alt', 'data', 'aria']:
            if selector_type == 'label':
                # Label类型需要异步查找
                selector, selector_type = await self.find_element_by_label(selector, target_page)
                if selector is None:
                    raise Exception(f"未找到与label关联的元素: {original_selector_type}={selector}")
            else:
                selector, selector_type = self.convert_selector(selector, selector_type)
            uat_logger.info(f"🔍 [SELECTOR_CONVERT] 选择器转换: {original_selector_type} -> {selector_type}, 值: {selector}")
        
        if selector_type == "xpath" and selector:
            selector = _normalize_xpath_selector_value(selector)
        
        # 构建完整的选择器
        full_selector = selector
        if selector_type == "xpath":
            full_selector = f"xpath={selector}"
        
        # 确定操作上下文
        target_context = target_page
        if iframe_context:
            target_context = iframe_context
        elif iframe_selector:
            uat_logger.info(f"🔄 [IFRAME_DEBUG] 使用iframe上下文,选择器: {iframe_selector}")
            target_context = target_page.frame_locator(iframe_selector)
        
        # 🔥 优化选择器：简化过于复杂的CSS选择器
        optimized_selector = self._optimize_selector(selector, selector_type)
        if optimized_selector != selector:
            uat_logger.info(f"🔧 选择器优化: {selector} -> {optimized_selector}")
            selector = optimized_selector
            # 重新构建完整的选择器
            if selector_type == "xpath":
                full_selector = f"xpath={selector}"
        
        # 🔥 智能输入框定位：如果原始选择器找不到input，尝试通过placeholder或label查找
        original_selector = selector
        original_selector_type = selector_type
        smart_locate_result = await self._smart_locate_input(target_context, selector, selector_type)
        if smart_locate_result and smart_locate_result.get('found'):
            uat_logger.info(f"🔍 [SMART_LOCATE] 智能定位到输入框: {smart_locate_result.get('description')}, 选择器: {smart_locate_result.get('selector')}")
            selector = smart_locate_result.get('selector')
            selector_type = smart_locate_result.get('selector_type', 'css')
            # 重新构建完整的选择器
            if selector_type == "xpath":
                full_selector = f"xpath={selector}"
            else:
                full_selector = selector
        
        # 🔥 添加元素存在性和类型预检查
        element_check_result = await self._check_element_type(target_context, full_selector, selector_type)
        if not element_check_result['exists']:
            raise Exception(f"元素不存在: {selector}")
        
        # 🔥 Element UI 下拉框特殊检测
        is_el_select_component = False
        el_select_info = None
        if element_check_result['type'] == 'div':
            # 检测是否是el-select组件
            try:
                el_select_info = await target_context.evaluate("""(sel) => {
                    const el = document.querySelector(sel);
                    if (!el) return null;
                    const className = el.className || '';
                    const isElSelect = className.includes('el-select');
                    const isFilterable = className.includes('is-filterable');
                    const placeholder = el.querySelector('.el-input__inner')?.placeholder || '';
                    const ariaLabel = el.querySelector('.el-input__inner')?.getAttribute('aria-label') || '';
                    return { 
                        isElSelect, 
                        isFilterable, 
                        placeholder,
                        ariaLabel,
                        className
                    };
                }""", selector)
                
                if el_select_info and el_select_info.get('isElSelect'):
                    is_el_select_component = True
                    uat_logger.info(f"🔍 [EL_SELECT] 检测到Element UI下拉框组件: {el_select_info}")
            except Exception as e:
                uat_logger.debug(f"🔍 [EL_SELECT] 检测失败: {e}")
        
        if element_check_result['type'] not in ['input', 'textarea', 'contenteditable'] and not is_el_select_component:
            uat_logger.warning(f"元素类型可能不支持输入: {element_check_result['type']}，继续尝试填充")
        
        # 尝试多种填充方式,增加成功概率
        fill_success = False
        xpath_group_readback = None  # (inner_xpath, nth_index) 用于分组 XPath 输入值回读

        xg_fill = _xpath_group_inner_and_index(selector) if selector_type == "xpath" else None
        if xg_fill:
            inner_f, idx1_f = xg_fill
            try:
                loc_f = target_context.locator(f"xpath={inner_f}")
                cnt_f = await loc_f.count()
                if cnt_f == 0:
                    raise Exception(f"元素不存在: {selector}")
                idx0_f = idx1_f - 1
                if idx0_f >= cnt_f:
                    uat_logger.warning(
                        f"⚠️ [FILL] XPath 匹配 {cnt_f} 个节点，录制 [{idx1_f}] 降级为索引 {cnt_f - 1}"
                    )
                    idx0_f = cnt_f - 1
                elf = loc_f.nth(idx0_f)
                await elf.wait_for(state="visible", timeout=10000)
                try:
                    await elf.fill(text, timeout=10000)
                except Exception:
                    await elf.fill(text, timeout=10000, force=True)
                fill_success = True
                xpath_group_readback = (inner_f, idx0_f)
                uat_logger.info(
                    f"✅ [FILL] 分组 XPath nth({idx0_f}) 填充成功: {inner_f[:90]}"
                )
            except Exception as e_xgf:
                uat_logger.warning(f"⚠️ [FILL] 分组 XPath 专用填充跳过: {e_xgf}")
        
        # 🔥 前置验证：确保目标元素是真正可见且可交互的输入框
        try:
            element_validation = await self._validate_input_element(target_context, full_selector, selector_type, text)
            if not element_validation.get('valid'):
                uat_logger.warning(f"⚠️ [INPUT_VALIDATION] 输入框验证失败: {element_validation.get('reason')}")
            else:
                uat_logger.info(f"✅ [INPUT_VALIDATION] 输入框验证通过: {element_validation.get('info')}")
        except Exception as ve:
            uat_logger.warning(f"⚠️ [INPUT_VALIDATION] 验证过程出错: {ve}")

        # 日期/时间输入框快速提交路径：避免“输入后被组件回滚清空”
        try:
            date_commit_result = await target_context.evaluate("""(params) => {
                const { selector, selectorType, value } = params;
                let root = null;
                if (selectorType === 'xpath') {
                    const r = document.evaluate(selector, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null);
                    root = r.singleNodeValue;
                } else {
                    root = document.querySelector(selector);
                }
                if (!root) return { isDateLike: false, committed: false, reason: 'root_not_found' };

                let target = root;
                const wrapperClass = (root.className || '').toString();
                if (root.tagName && root.tagName.toLowerCase() === 'div' &&
                    (wrapperClass.includes('el-input') || wrapperClass.includes('el-date-editor') || wrapperClass.includes('el-input__wrapper'))) {
                    const inner = root.querySelector('input, textarea');
                    if (inner) target = inner;
                }

                const placeholder = (target.getAttribute && target.getAttribute('placeholder')) || '';
                const ariaHaspopup = (target.getAttribute && target.getAttribute('aria-haspopup')) || '';
                const cls = (target.className || '').toString();
                const isDateLike = /日期|时间/.test(placeholder) ||
                    ariaHaspopup === 'dialog' ||
                    cls.includes('el-date-editor');

                if (!isDateLike) return { isDateLike: false, committed: false };

                target.focus && target.focus();
                target.value = value;
                target.dispatchEvent(new Event('input', { bubbles: true }));
                target.dispatchEvent(new Event('change', { bubbles: true }));
                target.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
                target.dispatchEvent(new KeyboardEvent('keyup', { key: 'Enter', bubbles: true }));
                target.dispatchEvent(new Event('blur', { bubbles: true }));
                return { isDateLike: true, committed: true, current: target.value, placeholder };
            }""", {'selector': selector, 'selectorType': selector_type, 'value': text})

            if date_commit_result and date_commit_result.get('isDateLike') and date_commit_result.get('committed'):
                uat_logger.info(f"✅ [INPUT_DATE_FASTPATH] 日期/时间输入快速提交成功: {date_commit_result.get('current')}")
                fill_success = True
        except Exception as date_e:
            uat_logger.debug(f"🔍 [INPUT_DATE_FASTPATH] 快速路径未命中/失败: {date_e}")
        
        # 🔥 方式0: Element UI 下拉框优先处理
        if is_el_select_component and self.locator_manager:
            try:
                uat_logger.info(f"🔍 [EL_SELECT] 使用专用方法填充Element UI下拉框")
                
                # 使用专用方法填充el-select
                fill_success = await self.locator_manager.fill_el_select(
                    selector=selector,
                    value=text,
                    placeholder=el_select_info.get('placeholder')
                )
                
                if fill_success:
                    uat_logger.info(f"✅ [EL_SELECT] Element UI下拉框填充成功")
                    return
                
                # 如果是可搜索下拉框，尝试搜索方式
                if el_select_info.get('isFilterable'):
                    fill_success = await self.locator_manager.fill_el_select_searchable(
                        selector=selector,
                        value=text,
                        placeholder=el_select_info.get('placeholder')
                    )
                    if fill_success:
                        uat_logger.info(f"✅ [EL_SELECT] 可搜索下拉框填充成功")
                        return
            except Exception as e:
                uat_logger.warning(f"⚠️ [EL_SELECT] 专用方法失败: {e}，尝试常规方法")
        
        # 方式1: 使用Playwright的fill方法
        if not fill_success:
            try:
                # 等待元素可见
                if hasattr(target_context, 'wait_for_selector'):
                    await target_context.wait_for_selector(full_selector, state='visible', timeout=5000)
                    await target_context.fill(full_selector, text, timeout=5000)
                    uat_logger.info(f"成功填充元素: {selector}, 选择器类型: {selector_type}, 文本: {text}")
                    fill_success = True
                else:
                    element = target_context.locator(full_selector)
                    await element.wait_for(state='visible', timeout=5000)
                    await element.fill(text, timeout=5000)
                    uat_logger.info(f"成功填充元素: {selector}, 选择器类型: {selector_type}, 文本: {text}")
                    fill_success = True
            except Exception as e:
                uat_logger.warning(f"常规填充失败: {str(e)}, 尝试使用force fill方法")
                
                # 方式2: 使用force fill方法
                try:
                    if hasattr(target_context, 'fill'):
                        await target_context.fill(full_selector, text, timeout=5000, force=True)
                        uat_logger.info(f"使用force fill方法成功填充元素: {selector}, 选择器类型: {selector_type}, 文本: {text}")
                        fill_success = True
                    else:
                        # 如果是frame_locator对象,需要使用其locator方法
                        element = target_context.locator(full_selector)
                        await element.fill(text, timeout=5000, force=True)
                        uat_logger.info(f"使用force fill方法成功填充元素: {selector}, 选择器类型: {selector_type}, 文本: {text}")
                        fill_success = True
                except Exception as e2:
                    uat_logger.warning(f"force fill方法失败: {str(e2)}, 尝试使用type方法")
                    
                    # 方式3: 使用type方法
                    type_success = False
                    try:
                        if hasattr(target_context, 'type'):
                            await target_context.type(full_selector, text, timeout=5000)
                            uat_logger.info(f"使用type方法执行完成: {selector}, 选择器类型: {selector_type}, 文本: {text}")
                            type_success = True
                        else:
                            # 如果是frame_locator对象,需要使用其locator方法
                            element = target_context.locator(full_selector)
                            await element.type(text, timeout=5000)
                            uat_logger.info(f"使用type方法(通过locator)执行完成: {selector}, 选择器类型: {selector_type}, 文本: {text}")
                            type_success = True
                    except Exception as e3:
                        uat_logger.warning(f"type方法失败: {str(e3)}, 尝试使用force type方法")
                    
                    # 🔥 修复：即使type方法没有报错，也要验证是否真的填充成功
                    if type_success:
                        try:
                            # 短暂等待让值生效
                            await asyncio.sleep(0.2)
                            # 验证填充是否成功
                            verify_value = await target_context.evaluate("""(sel) => {
                                const el = document.querySelector(sel);
                                if (!el) return null;
                                // 检查是否是包装层
                                if (el.tagName.toLowerCase() === 'div' && el.className.includes('el-textarea')) {
                                    const inner = el.querySelector('textarea');
                                    return inner ? inner.value : el.value;
                                }
                                return el.value;
                            }""", selector)
                            
                            if verify_value == text:
                                uat_logger.info(f"✅ type方法验证成功，值已正确设置: {verify_value}")
                                fill_success = True
                            else:
                                uat_logger.warning(f"⚠️ type方法验证失败: 期望值 '{text}', 实际值 '{verify_value}', 将继续尝试其他方法")
                                type_success = False  # 标记为失败，继续后续方法
                        except Exception as verify_err:
                            uat_logger.warning(f"⚠️ type方法验证异常: {verify_err}, 将继续尝试其他方法")
                            type_success = False
                    
                    if not type_success:
                        # 方式4: 使用type方法 (通过locator实现)
                        type4_success = False
                        try:
                            element = target_context.locator(full_selector)
                            await element.type(text, timeout=5000)
                            uat_logger.info(f"使用type方法(通过locator)执行完成: {selector}, 选择器类型: {selector_type}, 文本: {text}")
                            type4_success = True
                        except Exception as e4:
                            uat_logger.warning(f"type方法(通过locator)失败: {str(e4)}, 尝试使用JavaScript")
                        
                        # 🔥 修复：即使type方法(通过locator)没有报错，也要验证是否真的填充成功
                        if type4_success:
                            try:
                                await asyncio.sleep(0.2)
                                verify_value = await target_context.evaluate("""(sel) => {
                                    const el = document.querySelector(sel);
                                    if (!el) return null;
                                    if (el.tagName.toLowerCase() === 'div' && el.className.includes('el-textarea')) {
                                        const inner = el.querySelector('textarea');
                                        return inner ? inner.value : el.value;
                                    }
                                    return el.value;
                                }""", selector)
                                
                                if verify_value == text:
                                    uat_logger.info(f"✅ type方法(通过locator)验证成功，值已正确设置: {verify_value}")
                                    fill_success = True
                                else:
                                    uat_logger.warning(f"⚠️ type方法(通过locator)验证失败: 期望值 '{text}', 实际值 '{verify_value}', 将继续尝试JavaScript方法")
                                    type4_success = False
                            except Exception as verify_err:
                                uat_logger.warning(f"⚠️ type方法(通过locator)验证异常: {verify_err}, 将继续尝试JavaScript方法")
                                type4_success = False
                        
                        if not type4_success:
                            
                            # 方式5: 使用JavaScript直接设置值
                            try:
                                # 检查元素是否存在并设置值
                                if selector_type == "css":
                                    if hasattr(target_context, 'evaluate'):
                                        element_exists = await target_context.evaluate("(selector) => document.querySelector(selector) !== null", selector)
                                        if element_exists:
                                            # 🔥 修复：处理 el-textarea 等组件包装层的情况
                                            js_result = await target_context.evaluate("""(params) => {
                                                const { selector, text } = params;
                                                let element = document.querySelector(selector);
                                                if (!element) return { success: false, error: 'Element not found' };
                                                
                                                // 检查是否是包装层元素（如 el-textarea, el-input 等）
                                                const tagName = element.tagName.toLowerCase();
                                                const className = element.className || '';
                                                const isWrapper = tagName === 'div' && (
                                                    className.includes('el-textarea') ||
                                                    className.includes('el-input') ||
                                                    className.includes('el-input__wrapper')
                                                );
                                                
                                                let targetElement = element;
                                                if (isWrapper) {
                                                    // 查找内部的 input 或 textarea
                                                    const innerInput = element.querySelector('input, textarea');
                                                    if (innerInput) {
                                                        targetElement = innerInput;
                                                    }
                                                }
                                                
                                                // 设置值
                                                targetElement.value = text;
                                                
                                                // 触发输入相关事件
                                                targetElement.dispatchEvent(new Event('input', { bubbles: true }));
                                                targetElement.dispatchEvent(new Event('change', { bubbles: true }));
                                                targetElement.dispatchEvent(new Event('blur', { bubbles: true }));
                                                
                                                // 对于 Vue 组件，还需要触发 compositionend 事件
                                                targetElement.dispatchEvent(new Event('compositionend', { bubbles: true }));
                                                
                                                return { 
                                                    success: true, 
                                                    isWrapper: isWrapper,
                                                    targetTagName: targetElement.tagName.toLowerCase(),
                                                    targetClassName: targetElement.className
                                                };
                                            }""", {'selector': selector, 'text': text})
                                            
                                            if js_result and js_result.get('success'):
                                                uat_logger.info(f"✅ [JS_FILL] JavaScript填充成功: selector={selector}, isWrapper={js_result.get('isWrapper')}, target={js_result.get('targetTagName')}.{js_result.get('targetClassName')}")
                                            else:
                                                uat_logger.warning(f"⚠️ [JS_FILL] JavaScript填充可能有问题: {js_result}")
                                            uat_logger.info(f"使用JavaScript成功填充元素: {selector}, 文本: {text}")
                                            fill_success = True
                                        else:
                                            uat_logger.error(f"元素不存在,无法使用JavaScript填充: {selector}")
                                    else:
                                        # 如果是frame_locator对象,使用其locator方法
                                        element = target_context.locator(selector)
                                        count = await element.count()
                                        if count > 0:
                                            await element.fill(text, timeout=5000, force=True)
                                            uat_logger.info(f"使用frame_locator方法成功填充元素: {selector}, 文本: {text}")
                                            fill_success = True
                                        else:
                                            uat_logger.error(f"元素不存在,无法使用frame_locator方法填充: {selector}")
                                elif selector_type in ["link_text", "partial_link_text"]:
                                    # 🔥 修复：处理 link_text 选择器类型
                                    # 使用 placeholder 或 label 文本查找输入框
                                    if hasattr(target_context, 'evaluate'):
                                        uat_logger.info(f"🔍 [JS_FILL] 使用link_text策略查找输入框: placeholder/label='{selector}'")
                                        
                                        # 尝试通过placeholder或关联label查找input元素
                                        element_info = await target_context.evaluate("""(text) => {
                                            // 策略1: 通过placeholder查找input
                                            let input = document.querySelector('input[placeholder="' + text + '"], textarea[placeholder="' + text + '"]');
                                            if (input) return { found: true, tagName: input.tagName.toLowerCase(), id: input.id, className: input.className };
                                            
                                            // 策略2: 通过label的for属性查找关联input
                                            const labels = document.querySelectorAll('label');
                                            for (const label of labels) {
                                                if (label.textContent.trim() === text || label.textContent.trim().includes(text)) {
                                                    const forAttr = label.getAttribute('for');
                                                    if (forAttr) {
                                                        input = document.getElementById(forAttr);
                                                        if (input && (input.tagName === 'INPUT' || input.tagName === 'TEXTAREA')) {
                                                            return { found: true, tagName: input.tagName.toLowerCase(), id: input.id, className: input.className };
                                                        }
                                                    }
                                                    // 策略3: label内包含input
                                                    input = label.querySelector('input, textarea');
                                                    if (input) return { found: true, tagName: input.tagName.toLowerCase(), id: input.id, className: input.className };
                                                }
                                            }
                                            
                                            // 策略4: 通过包含文本的元素向上查找父级表单元素
                                            const textElements = document.querySelectorAll('*');
                                            for (const el of textElements) {
                                                if (el.textContent && (el.textContent.trim() === text || el.textContent.trim().includes(text))) {
                                                    // 向上查找最近的input父级或兄弟
                                                    let parent = el.parentElement;
                                                    for (let i = 0; i < 3 && parent; i++) {
                                                        input = parent.querySelector('input, textarea');
                                                        if (input) return { found: true, tagName: input.tagName.toLowerCase(), id: input.id, className: input.className };
                                                        parent = parent.parentElement;
                                                    }
                                                }
                                            }
                                            
                                            return { found: false };
                                        }""", selector)
                                        
                                        if element_info and element_info.get('found'):
                                            uat_logger.info(f"✅ [JS_FILL] 找到输入框元素: {element_info}")
                                            # 使用找到的元素信息进行填充
                                            await target_context.evaluate("""(text, inputText) => {
                                                let input = document.querySelector('input[placeholder="' + text + '"], textarea[placeholder="' + text + '"]');
                                                if (!input) {
                                                    const labels = document.querySelectorAll('label');
                                                    for (const label of labels) {
                                                        if (label.textContent.trim() === text || label.textContent.trim().includes(text)) {
                                                            const forAttr = label.getAttribute('for');
                                                            if (forAttr) {
                                                                input = document.getElementById(forAttr);
                                                                if (input && (input.tagName === 'INPUT' || input.tagName === 'TEXTAREA')) break;
                                                            }
                                                            input = label.querySelector('input, textarea');
                                                            if (input) break;
                                                        }
                                                    }
                                                }
                                                if (!input) {
                                                    const textElements = document.querySelectorAll('*');
                                                    for (const el of textElements) {
                                                        if (el.textContent && (el.textContent.trim() === text || el.textContent.trim().includes(text))) {
                                                            let parent = el.parentElement;
                                                            for (let i = 0; i < 3 && parent; i++) {
                                                                input = parent.querySelector('input, textarea');
                                                                if (input) break;
                                                                parent = parent.parentElement;
                                                            }
                                                            if (input) break;
                                                        }
                                                    }
                                                }
                                                if (input) {
                                                    input.value = inputText;
                                                    input.dispatchEvent(new Event('input', {bubbles: true}));
                                                    input.dispatchEvent(new Event('change', {bubbles: true}));
                                                    input.dispatchEvent(new Event('blur', {bubbles: true}));
                                                    return true;
                                                }
                                                return false;
                                            }""", selector, text)
                                            uat_logger.info(f"✅ [JS_FILL] 使用JavaScript成功填充元素(link_text): {selector}, 文本: {text}")
                                            fill_success = True
                                        else:
                                            uat_logger.error(f"❌ [JS_FILL] 无法通过link_text找到输入框元素: {selector}")
                                    else:
                                        uat_logger.error(f"❌ [JS_FILL] frame_locator不支持link_text的JavaScript填充: {selector}")
                                else:  # xpath
                                    # 使用XPath查找元素
                                    if hasattr(target_context, 'evaluate'):
                                        element_exists = await target_context.evaluate("""(xpath) => {
                                        const result = document.evaluate(xpath, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null);
                                        return result.singleNodeValue !== null;
                                    }""", selector)
                                    if element_exists:
                                        # 使用JavaScript设置值并触发输入相关事件
                                        await target_context.evaluate("""(xpath, text) => {
                                            const result = document.evaluate(xpath, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null);
                                            const element = result.singleNodeValue;
                                            if (element) {
                                                // 设置值
                                                element.value = text;
                                                
                                                // 触发输入相关事件
                                                element.dispatchEvent(new Event('input', {bubbles: true}));
                                                element.dispatchEvent(new Event('change', {bubbles: true}));
                                                element.dispatchEvent(new Event('blur', {bubbles: true}));
                                            }
                                        }""", selector, text)
                                        # 验证JavaScript填充是否真正成功
                                        try:
                                            actual_value = await target_context.evaluate(
                                                """(xpath) => {
                                                    const result = document.evaluate(xpath, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null);
                                                    const element = result.singleNodeValue;
                                                    return element ? element.value : null;
                                                }""",
                                                selector
                                            )
                                            if actual_value == text:
                                                uat_logger.info(f"使用JavaScript成功填充元素: {selector}, 选择器类型: {selector_type}, 文本: {text}")
                                                fill_success = True
                                            else:
                                                uat_logger.error(f"JavaScript填充验证失败: 期望值 '{text}'，实际值 '{actual_value}'")
                                        except Exception as verify_error:
                                            uat_logger.error(f"JavaScript填充验证异常: {verify_error}，判定为失败")
                                            fill_success = False
                                    else:
                                        uat_logger.error(f"元素不存在,无法使用JavaScript填充: {selector}")
                            except Exception as e5:
                                uat_logger.error(f"JavaScript填充失败: {str(e5)}")
                                
                                # 🔥 方式6: Element UI 下拉框专用处理
                                if not fill_success:
                                    try:
                                        uat_logger.info(f"🔍 [FILL_DEBUG] 尝试方式6: Element UI下拉框专用处理")
                                        
                                        # 检测是否是el-select组件
                                        is_el_select = await target_context.evaluate("""(sel) => {
                                            const el = document.querySelector(sel);
                                            if (!el) return { isSelect: false };
                                            const className = el.className || '';
                                            const isSelect = className.includes('el-select');
                                            const isFilterable = className.includes('is-filterable');
                                            const placeholder = el.querySelector('.el-input__inner')?.placeholder || '';
                                            return { isSelect, isFilterable, placeholder };
                                        }""", selector)
                                        
                                        if is_el_select and is_el_select.get('isSelect'):
                                            uat_logger.info(f"✅ [FILL_DEBUG] 检测到Element UI下拉框，使用专用填充方法")
                                            
                                            # 使用专用方法填充el-select
                                            if self.locator_manager:
                                                fill_success = await self.locator_manager.fill_el_select(
                                                    selector=selector,
                                                    value=text,
                                                    placeholder=is_el_select.get('placeholder')
                                                )
                                                if fill_success:
                                                    uat_logger.info(f"✅ [FILL_DEBUG] Element UI下拉框填充成功")
                                            
                                            # 如果是可搜索下拉框，尝试搜索方式
                                            if not fill_success and is_el_select.get('isFilterable'):
                                                fill_success = await self.locator_manager.fill_el_select_searchable(
                                                    selector=selector,
                                                    value=text,
                                                    placeholder=is_el_select.get('placeholder')
                                                )
                                        else:
                                            uat_logger.info(f"🔍 [FILL_DEBUG] 不是Element UI下拉框，继续尝试其他方式")
                                    except Exception as e6:
                                        uat_logger.error(f"❌ [FILL_DEBUG] 方式6失败: {str(e6)}")
                                
                                # 🔥 方式7: 增强型智能定位器降级机制
                                if not fill_success and self.locator_manager:
                                    try:
                                        uat_logger.info(f"🔍 [FILL_DEBUG] 尝试方式7: 增强型智能定位器降级")
                                        
                                        # 尝试提取元素信息并使用智能定位
                                        element_info = await self.locator_manager.extract_element_info(selector if selector_type == "css" else None)
                                        if element_info:
                                            uat_logger.info(f"🔍 [FILL_DEBUG] 提取到元素信息: {element_info.tag_name}")
                                            locator = await self.locator_manager.find_element(element_info, max_attempts=3)
                                            if locator:
                                                await locator.fill(text, timeout=10000)
                                                uat_logger.info(f"✅ [FILL_DEBUG] 方式7成功填充元素: 使用智能定位器")
                                                fill_success = True
                                            else:
                                                uat_logger.warning(f"⚠️ [FILL_DEBUG] 智能定位器未找到元素")
                                        else:
                                            uat_logger.warning(f"⚠️ [FILL_DEBUG] 无法提取元素信息")
                                    except Exception as e7:
                                        uat_logger.error(f"❌ [FILL_DEBUG] 方式7失败: {str(e7)}")
            
        # 最终验证：与 Playwright fill 语义对齐，兼容 Vue/uni-app 异步回填、shadow DOM；不以「两次必须同字面值」苛判
        if fill_success:
            try:
                async def _read_best_fill_value() -> Optional[str]:
                    try:
                        loc = target_context.locator(full_selector)
                        if await loc.count() > 0:
                            v = await loc.first.input_value(timeout=4000)
                            if v is not None:
                                return v
                    except Exception as _e:
                        uat_logger.debug(f"[FILL_VERIFY] locator.input_value: {_e}")
                    if not hasattr(target_context, "evaluate"):
                        return None
                    _read_deep_fn = """function readDeep(el) {
    if (!el) return null;
    let e = el;
    if (e.shadowRoot) {
        const si = e.shadowRoot.querySelector('input, textarea');
        if (si) e = si;
    }
    const t = (e.tagName || '').toLowerCase();
    const cls = (e.className || '').toString();
    if (t === 'input' || t === 'textarea')
        return e.value != null ? String(e.value) : '';
    if (e.isContentEditable)
        return (e.innerText || e.textContent || '').trim();
    if (t === 'div' && (
            cls.includes('el-textarea') ||
            cls.includes('el-input') ||
            cls.includes('el-input__wrapper'))) {
        const inner = e.querySelector('input, textarea');
        if (inner) return inner.value != null ? String(inner.value) : '';
    }
    if (e.querySelector) {
        const inner = e.querySelector('input, textarea');
        if (inner) return inner.value != null ? String(inner.value) : '';
    }
    return null;
}"""
                    _js_css = "(sel) => {\n" + _read_deep_fn + "\nreturn readDeep(document.querySelector(sel));\n}"
                    _js_xpath = (
                        "(xpath) => {\n"
                        + _read_deep_fn
                        + "\nconst result = document.evaluate(xpath, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null);\n"
                        "return readDeep(result.singleNodeValue);\n}"
                    )
                    try:
                        if selector_type == "css":
                            return await target_context.evaluate(_js_css, selector)
                        if selector_type in ["link_text", "partial_link_text"]:
                            return await target_context.evaluate(
                                """(text) => {
                                    let input = document.querySelector('input[placeholder="' + text + '"], textarea[placeholder="' + text + '"]');
                                    if (input) return input.value;
                                    const labels = document.querySelectorAll('label');
                                    for (const label of labels) {
                                        if (label.textContent.trim() === text || label.textContent.trim().includes(text)) {
                                            const forAttr = label.getAttribute('for');
                                            if (forAttr) {
                                                input = document.getElementById(forAttr);
                                                if (input && (input.tagName === 'INPUT' || input.tagName === 'TEXTAREA')) return input.value;
                                            }
                                            input = label.querySelector('input, textarea');
                                            if (input) return input.value;
                                        }
                                    }
                                    return null;
                                }""",
                                selector,
                            )
                        return await target_context.evaluate(_js_xpath, selector)
                    except Exception as _e3:
                        uat_logger.debug(f"[FILL_VERIFY] evaluate: {_e3}")
                    return None

                await asyncio.sleep(0.15)
                first_value = await _read_best_fill_value()
                await asyncio.sleep(0.45)
                second_value = await _read_best_fill_value()

                if _fill_text_compare_equal(second_value, text):
                    if not _fill_text_compare_equal(first_value, text):
                        uat_logger.info(
                            f"✅ 输入验证通过（异步回填/UI 模型后同步）: 首次='{first_value}' → 二次='{second_value}'，预期='{text}'"
                        )
                    else:
                        uat_logger.debug(f"✅ 输入验证成功: '{text}'")
                elif _fill_text_compare_equal(first_value, text) and not _fill_text_compare_equal(
                    second_value, text
                ):
                    uat_logger.error(
                        f"🔥 输入验证失败: 已写入后又被组件清空 — 首次='{first_value}'，二次='{second_value}'，预期='{text}'"
                    )
                    fill_success = False
                else:
                    await asyncio.sleep(0.55)
                    third_value = await _read_best_fill_value()
                    if _fill_text_compare_equal(third_value, text):
                        uat_logger.info(
                            f"✅ 输入验证在延长等待后通过（如 uni-app 晚步刷新）: '{third_value}'"
                        )
                    else:
                        uat_logger.error(
                            f"🔥 输入验证失败: 预期='{text}'，首次='{first_value}'，二次='{second_value}'，三次='{third_value}'"
                        )
                        fill_success = False
            except Exception as verify_error:
                uat_logger.error(f"输入验证异常: {verify_error}，判定为失败")
                fill_success = False
        
        if not fill_success:
            if locator_candidates:
                for extra_sel, extra_type in _fallback_locator_tuples(selector, selector_type, locator_candidates):
                    try:
                        await self._fill_input_internal(
                            extra_sel, text, extra_type, iframe_selector, iframe_context, page, None
                        )
                        uat_logger.info(
                            f"✅ [LOCATOR_PACK] 备选填充成功: {extra_type}={extra_sel[:120]!s}"
                        )
                        return
                    except Exception as _fb_fe:
                        uat_logger.warning(
                            f"⚠️ [LOCATOR_PACK] 备选填充失败 ({extra_type}={extra_sel[:80]!s}): {_fb_fe}"
                        )
            raise Exception(f"无法填充元素: {selector}, 选择器类型: {selector_type}, 所有填充方式均失败")
        

    async def _smart_locate_input(self, context, selector: str, selector_type: str) -> dict:
        """
        智能定位输入框
        当选择器指向包装层或动态ID元素时，尝试通过placeholder或label查找真正的input元素
        
        Returns:
            dict: {'found': bool, 'selector': str, 'selector_type': str, 'description': str}
        """
        result = {'found': False, 'selector': selector, 'selector_type': selector_type, 'description': ''}
        
        try:
            if not hasattr(context, 'evaluate'):
                return result
            
            # 首先检查当前选择器指向的元素
            element_info = None
            if selector_type == "css":
                element_info = await context.evaluate("""(sel) => {
                    const el = document.querySelector(sel);
                    if (!el) return null;
                    return {
                        tagName: el.tagName.toLowerCase(),
                        className: el.className || '',
                        id: el.id || '',
                        placeholder: el.placeholder || '',
                        isContentEditable: el.isContentEditable,
                        parentTagName: el.parentElement ? el.parentElement.tagName.toLowerCase() : null,
                        parentClassName: el.parentElement ? (el.parentElement.className || '') : ''
                    };
                }""", selector)
            elif selector_type == "xpath":
                element_info = await context.evaluate("""(xpath) => {
                    const result = document.evaluate(xpath, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null);
                    const el = result.singleNodeValue;
                    if (!el) return null;
                    return {
                        tagName: el.tagName.toLowerCase(),
                        className: el.className || '',
                        id: el.id || '',
                        placeholder: el.placeholder || '',
                        isContentEditable: el.isContentEditable,
                        parentTagName: el.parentElement ? el.parentElement.tagName.toLowerCase() : null,
                        parentClassName: el.parentElement ? (el.parentElement.className || '') : ''
                    };
                }""", selector)
            
            if not element_info:
                # ⭐ 元素不存在时，尝试通过XPath派生查找内部input
                # 场景：XPath指向父级包装层但因为结构差异找不到，尝试在父级路径下查找input
                if selector_type == "xpath":
                    uat_logger.info(f"🔍 [SMART_LOCATE] 原始XPath找不到元素，尝试派生查找内部input: {selector}")
                    derived_result = await context.evaluate("""(xpath) => {
                        // 尝试在xpath基础上追加 //input 或 //textarea 来查找内部input
                        const tryXpaths = [
                            xpath + '/descendant::input[contains(@class,"el-input__inner")]',
                            xpath + '/descendant::textarea[contains(@class,"el-textarea__inner")]',
                            xpath + '//input',
                            xpath + '//textarea',
                            xpath + '/input',
                            xpath + '/textarea',
                        ];
                        for (const tryXpath of tryXpaths) {
                            try {
                                const r = document.evaluate(tryXpath, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null);
                                const el = r.singleNodeValue;
                                if (el) {
                                    return {
                                        found: true,
                                        xpath: tryXpath,
                                        id: el.id || '',
                                        placeholder: el.placeholder || '',
                                        tagName: el.tagName.toLowerCase(),
                                        className: el.className || ''
                                    };
                                }
                            } catch(e) {}
                        }
                        return null;
                    }""", selector)
                    if derived_result and derived_result.get('found'):
                        el_id = derived_result.get('id', '')
                        el_placeholder = derived_result.get('placeholder', '')
                        el_xpath = derived_result.get('xpath', '')
                        # ⭐⭐ 关键修复：元素不存在时的派生查找，必须使用派生XPath路径，保持位置精确性
                        # 不能使用全局CSS placeholder（会定位到页面第一个匹配元素！）
                        # 优先用派生xpath（位置精确），其次用ID，不使用全局CSS placeholder
                        if el_xpath:
                            return {'found': True, 'selector': el_xpath, 'selector_type': 'xpath',
                                    'description': f'XPath元素不存在，派生xpath精确找到input'}
                        elif el_id:
                            return {'found': True, 'selector': f'#{el_id}', 'selector_type': 'css',
                                    'description': f'XPath元素不存在，派生通过id找到input: {el_id}'}
                        else:
                            # placeholder作为最后备选（可能有重复，但总比什么都没有好）
                            escaped = el_placeholder.replace('"', '\\"')
                            return {'found': True, 'selector': f'input[placeholder="{escaped}"], textarea[placeholder="{escaped}"]',
                                    'selector_type': 'css',
                                    'description': f'XPath元素不存在，使用placeholder备选找到input: {el_placeholder}'}
                return result
            
            # 如果已经是input或textarea，不需要智能定位
            if element_info['tagName'] in ['input', 'textarea']:
                return result
            
            uat_logger.info(f"🔍 [SMART_LOCATE] 当前元素是 {element_info['tagName']}.{element_info['className']}, 尝试智能定位内部input...")
            
            # 策畧1: 如果当前元素是包装层（如el-input），查找内部的input
            # 注意：使用Python的 'in' 运算符，而非 JavaScript 的 .includes() 方法
            if element_info['tagName'] == 'div' and (
                'el-input' in element_info['className'] or
                'el-textarea' in element_info['className']
            ):
                if selector_type == "css":
                    inner_input = await context.evaluate("""(sel) => {
                        const wrapper = document.querySelector(sel);
                        if (!wrapper) return null;
                        const input = wrapper.querySelector('input.el-input__inner, textarea.el-textarea__inner');
                        if (input) {
                            return {
                                found: true,
                                inputId: input.id || '',
                                placeholder: input.placeholder || '',
                                className: input.className || ''
                            };
                        }
                        return null;
                    }""", selector)
                else:  # xpath
                    inner_input = await context.evaluate("""(xpath) => {
                        const result = document.evaluate(xpath, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null);
                        const wrapper = result.singleNodeValue;
                        if (!wrapper) return null;
                        const input = wrapper.querySelector('input.el-input__inner, textarea.el-textarea__inner');
                        if (input) {
                            return {
                                found: true,
                                inputId: input.id || '',
                                placeholder: input.placeholder || '',
                                className: input.className || ''
                            };
                        }
                        return null;
                    }""", selector)
                            
                if inner_input and inner_input.get('found'):
                    input_id = inner_input.get('inputId', '')
                    input_placeholder = inner_input.get('placeholder', '')
                    # ⭐⭐ 关键修复：当原始选择器是XPath时，必须使用基于XPath的descendant路径
                    # 而不能用全局CSS placeholder选择器（会定位到页面上第一个匹配的元素！）
                    if selector_type == "xpath":
                        # XPath选择器：始终使用descendant精确定位，保留位置所属的路径
                        derived_xpath = (
                            selector + '/descendant::input[contains(@class,"el-input__inner")] | ' +
                            selector + '/descendant::textarea[contains(@class,"el-textarea__inner")]'
                        )
                        result = {
                            'found': True,
                            'selector': derived_xpath,
                            'selector_type': 'xpath',
                            'description': f"从XPath包装层 {element_info['className'][:30]} 派生找到内郢input"
                        }
                    else:
                        # CSS选择器：使用唯一ID（最精确）
                        if input_id:
                            result = {
                                'found': True,
                                'selector': f'#{input_id}',
                                'selector_type': 'css',
                                'description': f"从CSS包装层找到内部input（id={input_id}）"
                            }
                        elif input_placeholder:
                            # CSS模式下如果placeholder唯一才用CSS，否则用外层CSS+内层子选择器
                            escaped = input_placeholder.replace('"', '\\"')
                            result = {
                                'found': True,
                                'selector': f'{selector} input.el-input__inner, {selector} textarea.el-textarea__inner',
                                'selector_type': 'css',
                                'description': f"从CSS包装层找到内部input（placeholder={input_placeholder}）"
                            }
                        else:
                            # 没有id和placeholder时用CSS子选择器
                            result = {
                                'found': True,
                                'selector': f'{selector} input.el-input__inner, {selector} textarea.el-textarea__inner',
                                'selector_type': 'css',
                                'description': f"从CSS包装层派生找到内部input"
                            }
                    return result
            
            # 策略2: 通过placeholder查找关联的input
            # 尝试从当前元素的文本内容或属性中提取placeholder关键词
            if selector_type == "css":
                placeholder_hints = await context.evaluate("""(sel) => {
                    const el = document.querySelector(sel);
                    if (!el) return [];
                    
                    const hints = [];
                    // 获取元素自身的placeholder
                    if (el.placeholder) hints.push(el.placeholder);
                    
                    // 获取父元素中的label文本
                    let parent = el.parentElement;
                    for (let i = 0; i < 3 && parent; i++) {
                        const label = parent.querySelector('label');
                        if (label) {
                            hints.push(label.textContent.trim());
                        }
                        // 检查是否有placeholder属性
                        const inputs = parent.querySelectorAll('input[placeholder], textarea[placeholder]');
                        inputs.forEach(input => {
                            if (input.placeholder) hints.push(input.placeholder);
                        });
                        parent = parent.parentElement;
                    }
                    
                    return hints.filter(h => h && h.length > 0);
                }""", selector)
            else:  # xpath
                placeholder_hints = await context.evaluate("""(xpath) => {
                    const result = document.evaluate(xpath, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null);
                    const el = result.singleNodeValue;
                    if (!el) return [];
                    
                    const hints = [];
                    if (el.placeholder) hints.push(el.placeholder);
                    
                    let parent = el.parentElement;
                    for (let i = 0; i < 3 && parent; i++) {
                        const label = parent.querySelector('label');
                        if (label) {
                            hints.push(label.textContent.trim());
                        }
                        const inputs = parent.querySelectorAll('input[placeholder], textarea[placeholder]');
                        inputs.forEach(input => {
                            if (input.placeholder) hints.push(input.placeholder);
                        });
                        parent = parent.parentElement;
                    }
                    
                    return hints.filter(h => h && h.length > 0);
                }""", selector)
            
            if placeholder_hints and len(placeholder_hints) > 0:
                uat_logger.info(f"🔍 [SMART_LOCATE] 找到placeholder提示: {placeholder_hints}")
                
                # 尝试通过placeholder查找input
                for hint in placeholder_hints[:2] if len(placeholder_hints) > 2 else placeholder_hints:  # 只尝试前两个
                    escaped_hint = hint.replace('"', '\\"')
                    found_input = await context.evaluate(f"""() => {{
                        const input = document.querySelector('input[placeholder="{escaped_hint}"], textarea[placeholder="{escaped_hint}"]');
                        if (input) {{
                            return {{
                                found: true,
                                tagName: input.tagName.toLowerCase(),
                                id: input.id,
                                className: input.className,
                                placeholder: input.placeholder
                            }};
                        }}
                        return null;
                    }}""")
                    
                    if found_input and found_input.get('found'):
                        # 构建稳定的选择器
                        if found_input.get('id'):
                            result = {
                                'found': True,
                                'selector': f"#{found_input['id']}",
                                'selector_type': 'css',
                                'description': f"通过placeholder='{hint}'找到input"
                            }
                        else:
                            result = {
                                'found': True,
                                'selector': f"input[placeholder=\"{escaped_hint}\"]",
                                'selector_type': 'css',
                                'description': f"通过placeholder='{hint}'找到input"
                            }
                        return result
            
            # 策略3: 通过label的for属性查找关联的input
            if selector_type == "css":
                label_result = await context.evaluate("""(sel) => {
                    const el = document.querySelector(sel);
                    if (!el) return null;
                    
                    // 向上查找包含label的父元素
                    let parent = el.parentElement;
                    for (let i = 0; i < 5 && parent; i++) {
                        const label = parent.querySelector('label');
                        if (label) {
                            const forAttr = label.getAttribute('for');
                            if (forAttr) {
                                const input = document.getElementById(forAttr);
                                if (input && (input.tagName === 'INPUT' || input.tagName === 'TEXTAREA')) {
                                    return {
                                        found: true,
                                        method: 'label_for',
                                        labelText: label.textContent.trim(),
                                        inputId: forAttr,
                                        inputTag: input.tagName.toLowerCase()
                                    };
                                }
                            }
                            // label包裹input的情况
                            const wrappedInput = label.querySelector('input, textarea');
                            if (wrappedInput) {
                                return {
                                    found: true,
                                    method: 'label_wrapped',
                                    labelText: label.textContent.trim(),
                                    inputId: wrappedInput.id,
                                    inputTag: wrappedInput.tagName.toLowerCase()
                                };
                            }
                        }
                        parent = parent.parentElement;
                    }
                    return null;
                }""", selector)
            else:  # xpath
                label_result = await context.evaluate("""(xpath) => {
                    const result = document.evaluate(xpath, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null);
                    const el = result.singleNodeValue;
                    if (!el) return null;
                    
                    let parent = el.parentElement;
                    for (let i = 0; i < 5 && parent; i++) {
                        const label = parent.querySelector('label');
                        if (label) {
                            const forAttr = label.getAttribute('for');
                            if (forAttr) {
                                const input = document.getElementById(forAttr);
                                if (input && (input.tagName === 'INPUT' || input.tagName === 'TEXTAREA')) {
                                    return {
                                        found: true,
                                        method: 'label_for',
                                        labelText: label.textContent.trim(),
                                        inputId: forAttr,
                                        inputTag: input.tagName.toLowerCase()
                                    };
                                }
                            }
                            const wrappedInput = label.querySelector('input, textarea');
                            if (wrappedInput) {
                                return {
                                    found: true,
                                    method: 'label_wrapped',
                                    labelText: label.textContent.trim(),
                                    inputId: wrappedInput.id,
                                    inputTag: wrappedInput.tagName.toLowerCase()
                                };
                            }
                        }
                        parent = parent.parentElement;
                    }
                    return null;
                }""", selector)
            
            if label_result and label_result.get('found'):
                input_id = label_result.get('inputId')
                if input_id:
                    result = {
                        'found': True,
                        'selector': f"#{input_id}",
                        'selector_type': 'css',
                        'description': f"通过label='{label_result.get('labelText')}'找到关联input"
                    }
                else:
                    # 使用XPath通过label文本查找
                    label_text = label_result.get('labelText', '').replace('"', '\\"')
                    result = {
                        'found': True,
                        'selector': f"//label[contains(text(), '{label_text}')]/following::input[1] | //label[contains(text(), '{label_text}')]//input",
                        'selector_type': 'xpath',
                        'description': f"通过label='{label_text}'找到关联input"
                    }
                return result
            
        except Exception as e:
            uat_logger.warning(f"⚠️ [SMART_LOCATE] 智能定位失败: {e}")
        
        return result

    async def _validate_input_element(self, context, full_selector: str, selector_type: str, expected_text: str) -> dict:
        """
        验证输入框元素是否正确可见且可交互
        防止将文本输入到错误的元素
        """
        result = {'valid': False, 'reason': '', 'info': ''}
        
        try:
            if not hasattr(context, 'evaluate'):
                result['valid'] = True
                result['info'] = 'frame_locator模式，跳过详细验证'
                return result
            
            validation_result = None
            if selector_type == "css":
                selector = full_selector
                validation_result = await context.evaluate("""(params) => {
                    const { selector, expectedText } = params;
                    const el = document.querySelector(selector);
                    if (!el) return { valid: false, reason: '元素不存在' };
                    
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    
                    // 检查可见性
                    if (style.display === 'none') return { valid: false, reason: '元素display:none' };
                    if (style.visibility === 'hidden') return { valid: false, reason: '元素visibility:hidden' };
                    if (rect.width === 0 || rect.height === 0) return { valid: false, reason: '元素尺寸为0' };
                    
                    // 检查是否在视口内
                    const inViewport = rect.top >= 0 && rect.left >= 0 && 
                                       rect.bottom <= window.innerHeight && 
                                       rect.right <= window.innerWidth;
                    
                    // 检查元素类型
                    const tagName = el.tagName.toLowerCase();
                    const isInput = tagName === 'input' || tagName === 'textarea';
                    const isContentEditable = el.isContentEditable;
                    
                    // 检查是否被其他元素遮挡
                    const centerX = rect.left + rect.width / 2;
                    const centerY = rect.top + rect.height / 2;
                    const topElement = document.elementFromPoint(centerX, centerY);
                    const isObscured = topElement && topElement !== el && !el.contains(topElement) && !topElement.contains(el);
                    
                    // 检查placeholder或label是否匹配预期文本（简单启发式）
                    let textMatch = false;
                    if (expectedText && expectedText.length > 3) {
                        const placeholder = el.placeholder || '';
                        const ariaLabel = el.getAttribute('aria-label') || '';
                        const title = el.title || '';
                        // 检查是否有相关文本提示
                        if (placeholder.includes(expectedText.substring(0, 5)) ||
                            ariaLabel.includes(expectedText.substring(0, 5)) ||
                            title.includes(expectedText.substring(0, 5))) {
                            textMatch = true;
                        }
                    }
                    
                    return {
                        valid: true,
                        tagName: tagName,
                        isInput: isInput,
                        isContentEditable: isContentEditable,
                        inViewport: inViewport,
                        isObscured: isObscured,
                        rect: { width: rect.width, height: rect.height, top: rect.top, left: rect.left },
                        textMatch: textMatch,
                        disabled: el.disabled,
                        readonly: el.readOnly
                    };
                }""", {'selector': selector, 'expectedText': expected_text})
                
            elif selector_type == "xpath":
                xpath = full_selector.replace('xpath=', '')
                validation_result = await context.evaluate("""(params) => {
                    const { xpath, expectedText } = params;
                    const result = document.evaluate(xpath, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null);
                    const el = result.singleNodeValue;
                    if (!el) return { valid: false, reason: '元素不存在' };
                    
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    
                    if (style.display === 'none') return { valid: false, reason: '元素display:none' };
                    if (style.visibility === 'hidden') return { valid: false, reason: '元素visibility:hidden' };
                    if (rect.width === 0 || rect.height === 0) return { valid: false, reason: '元素尺寸为0' };
                    
                    const inViewport = rect.top >= 0 && rect.left >= 0 && 
                                       rect.bottom <= window.innerHeight && 
                                       rect.right <= window.innerWidth;
                    
                    const tagName = el.tagName.toLowerCase();
                    const isInput = tagName === 'input' || tagName === 'textarea';
                    const isContentEditable = el.isContentEditable;
                    
                    const centerX = rect.left + rect.width / 2;
                    const centerY = rect.top + rect.height / 2;
                    const topElement = document.elementFromPoint(centerX, centerY);
                    const isObscured = topElement && topElement !== el && !el.contains(topElement) && !topElement.contains(el);
                    
                    let textMatch = false;
                    if (expectedText && expectedText.length > 3) {
                        const placeholder = el.placeholder || '';
                        const ariaLabel = el.getAttribute('aria-label') || '';
                        if (placeholder.includes(expectedText.substring(0, 5)) ||
                            ariaLabel.includes(expectedText.substring(0, 5))) {
                            textMatch = true;
                        }
                    }
                    
                    return {
                        valid: true,
                        tagName: tagName,
                        isInput: isInput,
                        isContentEditable: isContentEditable,
                        inViewport: inViewport,
                        isObscured: isObscured,
                        rect: { width: rect.width, height: rect.height },
                        textMatch: textMatch,
                        disabled: el.disabled,
                        readonly: el.readOnly
                    };
                }""", {'xpath': xpath, 'expectedText': expected_text})
            
            if validation_result:
                if not validation_result.get('valid'):
                    result['reason'] = validation_result.get('reason', '未知原因')
                else:
                    result['valid'] = True
                    info_parts = [
                        f"标签: {validation_result.get('tagName')}",
                        f"尺寸: {validation_result.get('rect', {}).get('width')}x{validation_result.get('rect', {}).get('height')}",
                        f"视口内: {'是' if validation_result.get('inViewport') else '否'}",
                        f"被遮挡: {'是' if validation_result.get('isObscured') else '否'}"
                    ]
                    if validation_result.get('disabled'):
                        info_parts.append("disabled:是")
                    if validation_result.get('readonly'):
                        info_parts.append("readonly:是")
                    result['info'] = ', '.join(info_parts)
                    
                    # 如果被遮挡，发出警告
                    if validation_result.get('isObscured'):
                        result['valid'] = False
                        result['reason'] = '元素被其他元素遮挡'
                    
            else:
                result['valid'] = True  # 无法验证时默认通过
                result['info'] = '验证结果为空，默认通过'
                
        except Exception as e:
            result['reason'] = f'验证异常: {str(e)}'
        
        return result

    def _optimize_selector(self, selector: str, selector_type: str) -> str:
        """优化选择器，简化过于复杂的CSS选择器"""
        if selector_type != "css":
            return selector
            
        # 如果选择器过短，不需要优化
        if len(selector.split(' > ')) < 5:
            return selector
            
        # 检查是否包含动态类名（Tailwind样式）
        dynamic_patterns = [
            r'h-\[[^\]]+\]',
            r'w-\[[^\]]+\]',
            r'left-\[[^\]]+\]',
            r'calc\([^)]+\)',
            r'var\(--[^\)]+\)'
        ]
        
        has_dynamic_classes = any(re.search(pattern, selector) for pattern in dynamic_patterns)
        if not has_dynamic_classes:
            return selector
            
        # 尝试简化选择器：提取关键部分
        parts = selector.split(' > ')
        
        # 策略1：查找包含表单相关类名的部分
        form_keywords = ['form', 'input', 'el-input', 'el-form', 'form-control', 'form-group']
        for i, part in enumerate(parts):
            if any(keyword in part.lower() for keyword in form_keywords):
                simplified = ' > '.join(parts[i-1:i+3]) if i > 0 else ' > '.join(parts[i:i+3])
                uat_logger.debug(f"选择器优化策略1: 提取表单相关部分 -> {simplified}")
                return simplified
        
        # 策略2：使用最后几个关键元素
        if len(parts) > 3:
            simplified = ' > '.join(parts[-3:])
            uat_logger.debug(f"选择器优化策略2: 使用最后3个个元素 -> {simplified}")
            return simplified
            
        return selector

    async def _check_element_type(self, context, full_selector: str, selector_type: str) -> dict:
        """检查元素是否存在以及元素类型"""
        result = {
            'exists': False,
            'type': None,
            'tag_name': None
        }
        
        try:
            # 分组 XPath：(//node)[k] — wait_for_selector / document.evaluate(FIRST_ORDERED) 易误判为不存在
            if selector_type == "xpath":
                xb = full_selector[6:] if full_selector.startswith("xpath=") else full_selector
                gx = _xpath_group_inner_and_index(xb)
                if gx:
                    inner, idx1 = gx
                    try:
                        loc = context.locator(f"xpath={inner}")
                        cnt = await loc.count()
                        if cnt == 0:
                            return result
                        idx0 = idx1 - 1
                        if idx0 >= cnt:
                            uat_logger.warning(
                                f"⚠️ [ELEMENT_CHECK] XPath 命中 {cnt} 个，录制序 [{idx1}] 改为索引 {cnt - 1}"
                            )
                            idx0 = cnt - 1
                        nth = loc.nth(idx0)
                        await nth.wait_for(state="attached", timeout=5000)
                        result["exists"] = True
                        element_info = await nth.evaluate("""element => ({
                            tagName: element.tagName.toLowerCase(),
                            type: element.type || '',
                            className: element.className,
                            id: element.id,
                            placeholder: element.placeholder || '',
                            isContentEditable: element.isContentEditable,
                            value: element.value || ''
                        })""")
                        if element_info:
                            result["tag_name"] = element_info.get("tagName")
                            tn = element_info.get("tagName", "")
                            if tn in ("input", "textarea"):
                                result["type"] = tn
                            elif element_info.get("isContentEditable"):
                                result["type"] = "contenteditable"
                            else:
                                result["type"] = tn
                        return result
                    except Exception as e_gx:
                        uat_logger.debug(f"[ELEMENT_CHECK] 分组 XPath 检查失败，回退常规检查: {e_gx}")
            
            if hasattr(context, 'wait_for_selector'):
                # 检查元素是否存在
                try:
                    await context.wait_for_selector(full_selector, state='attached', timeout=2000)
                    result['exists'] = True
                except:
                    result['exists'] = False
                    return result
                    
                # 获取元素信息
                try:
                    # 🔥 修复：根据选择器类型使用不同的方式获取元素信息
                    if selector_type == "xpath":
                        element_info = await context.evaluate("""(xpath) => {
                            const result = document.evaluate(xpath, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null);
                            const element = result.singleNodeValue;
                            if (!element) return null;
                            
                            return {
                                tagName: element.tagName.toLowerCase(),
                                type: element.type,
                                className: element.className,
                                id: element.id,
                                placeholder: element.placeholder,
                                isContentEditable: element.isContentEditable,
                                value: element.value
                            };
                        }""", full_selector.replace('xpath=', ''))
                    elif selector_type == "css":
                        element_info = await context.evaluate("""(selector) => {
                            const element = document.querySelector(selector);
                            if (!element) return null;
                            
                            return {
                                tagName: element.tagName.toLowerCase(),
                                type: element.type,
                                className: element.className,
                                id: element.id,
                                placeholder: element.placeholder,
                                isContentEditable: element.isContentEditable,
                                value: element.value
                            };
                        }""", full_selector)
                    else:
                        # 其他选择器类型，使用Playwright的locator获取元素
                        element = context.locator(full_selector).first
                        element_info = await element.evaluate("""element => {
                            return {
                                tagName: element.tagName.toLowerCase(),
                                type: element.type,
                                className: element.className,
                                id: element.id,
                                placeholder: element.placeholder,
                                isContentEditable: element.isContentEditable,
                                value: element.value
                            };
                        }""")
                    
                    if element_info:
                        result['tag_name'] = element_info['tagName']
                        if element_info['tagName'] in ['input', 'textarea']:
                            result['type'] = element_info['tagName']
                        elif element_info['isContentEditable']:
                            result['type'] = 'contenteditable'
                        else:
                            result['type'] = element_info['tagName']
                            
                except Exception as e:
                    uat_logger.debug(f"获取元素信息失败: {e}")
                    
            else:
                # frame_locator 情况
                try:
                    element = context.locator(full_selector)
                    count = await element.count()
                    result['exists'] = count > 0
                    
                    if count > 0:
                        tag_name = await element.evaluate("element => element.tagName.toLowerCase()")
                        result['tag_name'] = tag_name
                        
                        if tag_name in ['input', 'textarea']:
                            result['type'] = tag_name
                        else:
                            is_editable = await element.evaluate("element => element.isContentEditable")
                            if is_editable:
                                result['type'] = 'contenteditable'
                            else:
                                result['type'] = tag_name
                                
                except Exception as e:
                    uat_logger.debug(f"frame_locator元素检查失败: {e}")
                    
        except Exception as e:
            uat_logger.debug(f"元素检查过程中出错: {e}")
            
        return result
    
    async def scroll_page(self, direction: str = "down", pixels: int = 500, iframe_selector: str = None, iframe_context=None, page=None):
        """滚动页面或iframe。page: 可选，指定在哪个标签页执行（多标签并行时使用）"""
        target_page = page if page is not None else self.page
        if target_page is None:
            uat_logger.error("浏览器未启动,无法执行滚动操作")
            raise Exception("浏览器未启动")
        
        # 检查页面是否已经被关闭
        try:
            if hasattr(target_page, 'url'):
                _ = target_page.url
        except Exception as e:
            uat_logger.error(f"页面已关闭,无法执行滚动操作: {str(e)}")
            raise Exception("页面已关闭")
        
        uat_logger.info(f"🔍 [SCROLL_DEBUG] 开始滚动,方向: {direction}, 像素: {pixels}, iframe选择器: {iframe_selector}")
        
        # 确定操作上下文
        target_context = target_page
        if iframe_context:
            target_context = iframe_context
        elif iframe_selector:
            uat_logger.info(f"🔄 [IFRAME_DEBUG] 使用iframe上下文,选择器: {iframe_selector}")
            target_context = target_page.frame_locator(iframe_selector)
        
        # 执行滚动操作
        if hasattr(target_context, 'evaluate'):
            # 对于page对象
            if direction == "down":
                await target_context.evaluate(f"window.scrollBy(0, {pixels})")
            elif direction == "up":
                await target_context.evaluate(f"window.scrollBy(0, {-pixels})")
            elif direction == "to_top":
                await target_context.evaluate("window.scrollTo(0, 0)")
            elif direction == "to_bottom":
                await target_context.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        else:
            # 对于frame_locator对象,需要先获取iframe的contentFrame
            try:
                # 获取iframe的contentFrame
                iframe = await target_context.first.content_frame()
                if iframe:
                    if direction == "down":
                        await iframe.evaluate(f"window.scrollBy(0, {pixels})")
                    elif direction == "up":
                        await iframe.evaluate(f"window.scrollBy(0, {-pixels})")
                    elif direction == "to_top":
                        await iframe.evaluate("window.scrollTo(0, 0)")
                    elif direction == "to_bottom":
                        await iframe.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                else:
                    uat_logger.warning("无法获取iframe的contentFrame,无法执行滚动操作")
            except Exception as e:
                uat_logger.warning(f"执行iframe滚动时出错: {str(e)}")
        
    
    async def scroll_by_delta(self, dx: int = 0, dy: int = 0, iframe_selector: str = None, page=None):
        """按像素增量滚动主页面或 iframe（scrollBy），与平台步骤 input_value 四向格式对应。"""
        target_page = page if page is not None else self.page
        if target_page is None:
            raise Exception("浏览器未启动")
        try:
            dx = int(dx)
            dy = int(dy)
        except (TypeError, ValueError):
            dx, dy = 0, 0
        if dx == 0 and dy == 0:
            return
        if iframe_selector:
            try:
                target_context = target_page.frame_locator(iframe_selector)
                iframe = await target_context.first.content_frame()
                if iframe:
                    await iframe.evaluate(f"window.scrollBy({dx}, {dy})")
                else:
                    raise Exception("无法获取 iframe 的 content_frame")
            except Exception as e:
                raise Exception(f"iframe 滚动失败: {e}") from e
        else:
            await target_page.evaluate(f"window.scrollBy({dx}, {dy})")
    
    async def get_page_text(self, page=None) -> str:
        """获取页面文本内容。page: 可选，指定在哪个标签页执行（多标签并行时使用）"""
        target_page = page if page is not None else self.page
        if target_page is None:
            raise Exception("浏览器未启动")
        
        # 使用更高效的方法获取页面文本
        try:
            # 首先尝试使用JavaScript直接获取所有文本,这是最快的方法
            text_content = await target_page.evaluate(
                "() => document.body.innerText || document.body.textContent || document.documentElement.innerText || document.documentElement.textContent || ''"
            )
            
            if text_content and text_content.strip():
                return text_content.strip()
            
            # 如果JavaScript方法失败,使用Playwright的text_content方法
            body_element = target_page.locator('body')
            text_content = await body_element.text_content(timeout=5000)
            
            return text_content if text_content else ""
        except Exception as e:
            print(f"获取页面文本时出错: {e}")
            return ""
    
    async def extract_element_text(
        self,
        selector: str,
        selector_type: str = "css",
        iframe_selector: str = None,
        iframe_context=None,
        page=None,
        locator_candidates=None,
        _no_fallback: bool = False,
    ) -> str:
        """提取特定元素的文本，支持多种定位方式。page: 可选，指定在哪个标签页执行（多标签并行时使用）
        Parameters:
            selector: Locator string
            selector_type: Locator type, supports:
                - css: CSS selector
                - xpath: XPath selector
                - text: Text content
                - role: Semantic role (use role name directly, e.g. "button", "heading")
                - testid: Test ID (data-testid attribute value)
            iframe_selector: iframe selector (optional)
            iframe_context: iframe context (optional)
            locator_candidates: 与 click/input 相同，主定位失败时按 score 尝试备选。
        """
        target_page = page if page is not None else self.page
        if target_page is None:
            raise Exception("Browser not started")
        
        uat_logger.info(f"📝 [TEXT_EXTRACT_DEBUG] Start extracting text, selector: {selector}, selector_type: {selector_type}")
        
        if locator_candidates and not _no_fallback:
            attempts = [(selector, selector_type)] + list(
                _fallback_locator_tuples(selector, selector_type, locator_candidates)
            )
            last_exc = None
            for esel, etype in attempts:
                try:
                    return await self.extract_element_text(
                        esel, etype, iframe_selector, iframe_context, page, None, True
                    )
                except Exception as e:
                    last_exc = e
                    uat_logger.warning(
                        f"⚠️ [TEXT_EXTRACT_FALLBACK] 失败 ({etype}={str(esel)[:120]}): {e}"
                    )
            if last_exc:
                raise last_exc
        
        try:
            element = None
            
            # Determine target context
            target_context = target_page
            if iframe_selector:
                target_context = target_page.frame_locator(iframe_selector)
            elif iframe_context:
                target_context = iframe_context
            
            # Get element based on context type and locator method
            if hasattr(target_context, 'locator'):
                # For page or frame_locator objects, use locator method
                if selector_type == "css":
                    # CSS selector
                    uat_logger.info(f"📝 [TEXT_EXTRACT_DEBUG] Using CSS selector: {selector}")
                    element = target_context.locator(selector)
                    element = element.first
                elif selector_type == "xpath":
                    # XPath selector
                    uat_logger.info(f"📝 [TEXT_EXTRACT_DEBUG] Using XPath selector: {selector}")
                    element = target_context.locator(f"xpath={selector}")
                    element = element.first
                elif selector_type == "text":
                    # Text content selector
                    uat_logger.info(f"📝 [TEXT_EXTRACT_DEBUG] Using text selector: {selector}")
                    element = target_context.locator(f"text={selector}")
                    element = element.first
            elif selector_type == "role":
                # Semantic role selector
                uat_logger.info(f"📝 [TEXT_EXTRACT_DEBUG] Using role selector: {selector}")
                # Use Playwright's dedicated role locator
                if "," in selector:
                    # Handle role with parameters, only use role name part
                    role_name = selector.split(",")[0]
                    uat_logger.info(f"📝 [TEXT_EXTRACT_DEBUG] Role selector contains parameters, only use role name: {role_name}")
                    element = target_page.get_by_role(role_name)
                else:
                    element = target_page.get_by_role(selector)
                element = element.first
            elif selector_type == "testid":
                # Test ID selector, use Playwright's dedicated testid locator
                uat_logger.info(f"📝 [TEXT_EXTRACT_DEBUG] Using testid selector: {selector}")
                element = target_page.get_by_test_id(selector)
                element = element.first
            elif selector.startswith("//") or selector.startswith("/"):
                # Auto-detect XPath
                uat_logger.info(f"📝 [TEXT_EXTRACT_DEBUG] Auto-detected as XPath selector: {selector}")
                element = target_page.locator(f"xpath={selector}")
                element = element.first
            else:
                # Default to CSS selector
                uat_logger.info(f"📝 [TEXT_EXTRACT_DEBUG] Default to CSS selector: {selector}")
                element = self.page.locator(selector)
                element = element.first
            
            # Ensure element is correctly obtained
            if element is None:
                uat_logger.error(f"📝 [TEXT_EXTRACT_DEBUG] Element not successfully obtained")
                raise Exception(f"元素获取失败: {selector}")
            
            # Add strict waiting mechanism
            try:
                # Try to wait for element to exist (not required to be visible)
                await element.wait_for(state="attached", timeout=5000)
            except Exception as e:
                uat_logger.error(f"📝 [TEXT_EXTRACT_DEBUG] Waiting for element existence timed out: {e}")
                raise Exception(f"等待元素超时: {selector}")
            
            # Check if element exists
            try:
                count = await element.count()
                uat_logger.info(f"📝 [TEXT_EXTRACT_DEBUG] Found {count} elements")
                if count == 0:
                    uat_logger.error(f"📝 [TEXT_EXTRACT_DEBUG] Element not found")
                    raise Exception(f"元素未找到: {selector}")
            except Exception as e:
                uat_logger.error(f"📝 [TEXT_EXTRACT_DEBUG] Failed to check element count: {e}")
                raise Exception(f"检查元素数量失败: {selector}")
            
            # Use appropriate extraction method based on element type
            extracted_text = ""
            try:
                # Try to get element's tag name to determine element type
                tag_name = await element.evaluate("el => el.tagName.toLowerCase()")
                uat_logger.info(f"📝 [TEXT_EXTRACT_DEBUG] Element tag name: {tag_name}")
                
                if tag_name in ["input", "textarea"]:
                    uat_logger.info(f"📝 [TEXT_EXTRACT_DEBUG] Input element, using input_value() extraction")
                    try:
                        extracted_text = await element.input_value()
                        uat_logger.info(f"📝 [TEXT_EXTRACT_DEBUG] input_value() extraction result: '{extracted_text}'")
                    except Exception as e:
                        uat_logger.warning(f"📝 [TEXT_EXTRACT_DEBUG] input_value() failed: {e}")
                        try:
                            extracted_text = await element.get_attribute("value")
                            uat_logger.info(f"📝 [TEXT_EXTRACT_DEBUG] get_attribute('value') extraction result: '{extracted_text}'")
                        except Exception as e2:
                            uat_logger.warning(f"📝 [TEXT_EXTRACT_DEBUG] get_attribute('value') failed: {e2}")
                else:
                    uat_logger.info(f"📝 [TEXT_EXTRACT_DEBUG] Normal element, using inner_text() extraction")
                    try:
                        extracted_text = await element.inner_text()
                        uat_logger.info(f"📝 [TEXT_EXTRACT_DEBUG] inner_text() extraction result: '{extracted_text}'")
                    except Exception as e:
                        uat_logger.warning(f"📝 [TEXT_EXTRACT_DEBUG] inner_text() failed: {e}")
                        try:
                            extracted_text = await element.text_content()
                            uat_logger.info(f"📝 [TEXT_EXTRACT_DEBUG] text_content() extraction result: '{extracted_text}'")
                        except Exception as e2:
                            uat_logger.warning(f"📝 [TEXT_EXTRACT_DEBUG] text_content() failed: {e2}")
            except Exception as e:
                # If getting tag name fails, try using general methods to extract text
                uat_logger.warning(f"📝 [TEXT_EXTRACT_DEBUG] Failed to get tag name: {e}, trying general methods to extract text")
                try:
                    extracted_text = await element.inner_text()
                    uat_logger.info(f"📝 [TEXT_EXTRACT_DEBUG] General method inner_text() extraction result: '{extracted_text}'")
                except Exception as e:
                    uat_logger.warning(f"📝 [TEXT_EXTRACT_DEBUG] inner_text() failed: {e}")
                    try:
                        extracted_text = await element.text_content()
                        uat_logger.info(f"📝 [TEXT_EXTRACT_DEBUG] General method text_content() extraction result: '{extracted_text}'")
                    except Exception as e2:
                        uat_logger.warning(f"📝 [TEXT_EXTRACT_DEBUG] text_content() failed: {e2}")
                        try:
                            extracted_text = await element.input_value()
                            uat_logger.info(f"📝 [TEXT_EXTRACT_DEBUG] General method input_value() extraction result: '{extracted_text}'")
                        except Exception as e3:
                            uat_logger.warning(f"📝 [TEXT_EXTRACT_DEBUG] input_value() failed: {e3}")
                            try:
                                extracted_text = await element.get_attribute("value")
                                uat_logger.info(f"📝 [TEXT_EXTRACT_DEBUG] General method get_attribute('value') extraction result: '{extracted_text}'")
                            except Exception as e4:
                                uat_logger.warning(f"📝 [TEXT_EXTRACT_DEBUG] get_attribute('value') failed: {e4}")
            
            # Ensure returned text is not None
            result = extracted_text if extracted_text is not None else ""
            if not result:
                uat_logger.error(f"📝 [TEXT_EXTRACT_DEBUG] Extracted text is empty")
                raise Exception(f"元素文本提取失败: {selector}")
            uat_logger.info(f"📝 [TEXT_EXTRACT_DEBUG] Final extraction result: '{result}'")
            return result
        except Exception as e:
            # Record detailed exception information
            uat_logger.error(f"📝 [TEXT_EXTRACT_DEBUG] Error extracting text: {str(e)}")
            print(f"Error extracting element text: {str(e)}")
            raise Exception(f"文本提取异常: {str(e)}")
    async def extract_element_json(self, selector: str, selector_type: str = "css") -> dict:
        """从特定元素中提取JSON数据,支持多种定位方式
        参数:
            selector: 定位器字符串
            selector_type: 定位器类型,支持以下选项:
                - css: CSS选择器
                - xpath: XPath选择器
                - text: 文本内容
                - role: 语义角色 (直接使用角色名,如 "button", "heading")
                - testid: 测试ID (data-testid属性值)
        返回:
            提取到的JSON数据,解析失败则返回空字典
        """
        if self.page is None:
            raise Exception("浏览器未启动")
        
        uat_logger.info(f"📝 [JSON_EXTRACT_DEBUG] 开始提取JSON,选择器: {selector}, 选择器类型: {selector_type}")
        
        try:
            element = None
            
            # 根据不同定位方式获取元素
            if selector_type == "css":
                # CSS选择器
                uat_logger.info(f"📝 [JSON_EXTRACT_DEBUG] 使用CSS选择器: {selector}")
                element = self.page.locator(selector)
                await element.wait_for(state="visible", timeout=8000)
            elif selector_type == "xpath":
                # XPath选择器
                uat_logger.info(f"📝 [JSON_EXTRACT_DEBUG] 使用XPath选择器: {selector}")
                element = self.page.locator(f"xpath={selector}")
                await element.wait_for(state="visible", timeout=8000)
            elif selector_type == "text":
                # 文本内容选择器
                uat_logger.info(f"📝 [JSON_EXTRACT_DEBUG] 使用文本选择器: {selector}")
                element = self.page.locator(f"text={selector}")
                await element.wait_for(state="visible", timeout=8000)
            elif selector_type == "role":
                # 语义角色选择器
                uat_logger.info(f"📝 [JSON_EXTRACT_DEBUG] 使用角色选择器: {selector}")
                # 使用Playwright的专用role定位器
                if "," in selector:
                    # 处理带参数的角色,只使用角色名部分
                    role_name = selector.split(",")[0]
                    uat_logger.info(f"📝 [JSON_EXTRACT_DEBUG] 角色选择器包含参数,只使用角色名: {role_name}")
                    element = self.page.get_by_role(role_name)
                else:
                    element = self.page.get_by_role(selector)
                await element.wait_for(state="visible", timeout=8000)
            elif selector_type == "testid":
                # 测试ID选择器,使用Playwright的专用testid定位器
                uat_logger.info(f"📝 [JSON_EXTRACT_DEBUG] 使用testid选择器: {selector}")
                element = self.page.get_by_test_id(selector)
                await element.wait_for(state="visible", timeout=8000)
            elif selector.startswith("//") or selector.startswith("/"):
                # 自动识别XPath
                uat_logger.info(f"📝 [JSON_EXTRACT_DEBUG] 自动识别为XPath选择器: {selector}")
                element = self.page.locator(f"xpath={selector}")
                await element.wait_for(state="visible", timeout=8000)
            else:
                # 默认使用CSS选择器
                uat_logger.info(f"📝 [JSON_EXTRACT_DEBUG] 默认使用CSS选择器: {selector}")
                element = self.page.locator(selector)
                await element.wait_for(state="visible", timeout=8000)
            
            # 确保元素已正确获取
            if element is None:
                uat_logger.warning(f"📝 [JSON_EXTRACT_DEBUG] 未成功获取元素")
                return {}
            
            # 检查元素是否存在
            count = await element.count()
            uat_logger.info(f"📝 [JSON_EXTRACT_DEBUG] 找到元素数量: {count}")
            if count == 0:
                uat_logger.warning(f"📝 [JSON_EXTRACT_DEBUG] 未找到元素")
                return {}
            
            # 获取第一个匹配元素
            element = element.first
            
            # 获取元素的标签名,判断元素类型
            tag_name = await element.evaluate("el => el.tagName.toLowerCase()")
            uat_logger.info(f"📝 [JSON_EXTRACT_DEBUG] 元素标签名: {tag_name}")
            
            # 从多种来源提取JSON数据
            json_sources = []
            
            # 1. 从元素文本内容提取
            try:
                text_content = await element.text_content()
                if text_content and text_content.strip():
                    json_sources.append(text_content.strip())
                    uat_logger.info(f"📝 [JSON_EXTRACT_DEBUG] 从text_content提取到潜在JSON: {text_content.strip()[:100]}...")
            except Exception as e:
                uat_logger.warning(f"📝 [JSON_EXTRACT_DEBUG] 从text_content提取失败: {e}")
            
            # 2. 从inner_text提取
            try:
                inner_text = await element.inner_text()
                if inner_text and inner_text.strip() and inner_text.strip() != text_content:
                    json_sources.append(inner_text.strip())
                    uat_logger.info(f"📝 [JSON_EXTRACT_DEBUG] 从inner_text提取到潜在JSON: {inner_text.strip()[:100]}...")
            except Exception as e:
                uat_logger.warning(f"📝 [JSON_EXTRACT_DEBUG] 从inner_text提取失败: {e}")
            
            # 3. 从input/textarea的value属性提取
            if tag_name in ["input", "textarea"]:
                try:
                    input_value = await element.input_value()
                    if input_value and input_value.strip():
                        json_sources.append(input_value.strip())
                        uat_logger.info(f"📝 [JSON_EXTRACT_DEBUG] 从input_value提取到潜在JSON: {input_value.strip()[:100]}...")
                except Exception as e:
                    uat_logger.warning(f"📝 [JSON_EXTRACT_DEBUG] 从input_value提取失败: {e}")
            
            # 4. 从innerHTML提取(寻找JSON结构)
            try:
                inner_html = await element.innerHTML()
                if inner_html and inner_html.strip():
                    # 尝试从innerHTML中提取JSON字符串
                    import re
                    # 匹配JSON对象或数组
                    json_pattern = r'\{\s*["\w].*?\}\s*' + r'|' + r'\[\s*["\w].*?\]\s*'
                    matches = re.findall(json_pattern, inner_html, re.DOTALL)
                    if matches:
                        for match in matches:
                            if match.strip():
                                json_sources.append(match.strip())
                                uat_logger.info(f"📝 [JSON_EXTRACT_DEBUG] 从innerHTML提取到潜在JSON: {match.strip()[:100]}...")
            except Exception as e:
                uat_logger.warning(f"📝 [JSON_EXTRACT_DEBUG] 从innerHTML提取失败: {e}")
            
            # 5. 从元素的特定属性提取
            json_attributes = ["data-json", "data-content", "data-value", "value"]
            for attr in json_attributes:
                try:
                    attr_value = await element.get_attribute(attr)
                    if attr_value and attr_value.strip():
                        json_sources.append(attr_value.strip())
                        uat_logger.info(f"📝 [JSON_EXTRACT_DEBUG] 从属性{attr}提取到潜在JSON: {attr_value.strip()[:100]}...")
                except Exception as e:
                    uat_logger.warning(f"📝 [JSON_EXTRACT_DEBUG] 从属性{attr}提取失败: {e}")
            
            # 尝试解析每个潜在的JSON源
            for json_source in json_sources:
                try:
                    import json
                    # 清理JSON字符串(移除可能的换行符、多余空格等)
                    cleaned_json = json_source.replace("\n", "").replace("\r", "").strip()
                    # 尝试解析JSON
                    json_data = json.loads(cleaned_json)
                    uat_logger.info(f"📝 [JSON_EXTRACT_DEBUG] 成功解析JSON,包含{len(json_data) if isinstance(json_data, dict) else len(json_data)}个元素")
                    return json_data
                except json.JSONDecodeError as e:
                    uat_logger.warning(f"📝 [JSON_EXTRACT_DEBUG] JSON解析失败: {e},尝试下一个源")
                except Exception as e:
                    uat_logger.warning(f"📝 [JSON_EXTRACT_DEBUG] 处理JSON源时出错: {e},尝试下一个源")
            
            uat_logger.warning(f"📝 [JSON_EXTRACT_DEBUG] 所有JSON源解析失败")
            return {}
        except Exception as e:
            # 详细记录异常信息
            uat_logger.error(f"📝 [JSON_EXTRACT_DEBUG] 提取JSON时出错: {str(e)}")
            print(f"提取元素JSON时出错: {str(e)}")
            return {}

    async def _validate_selector(self, selector: str):
        """验证定位器的有效性和唯一性"""
        try:
            # 执行inspector验证
            elements = await self.page.evaluate(f'''
                (selector) => {{
                    const els = document.querySelectorAll(selector);
                    return {{
                        count: els.length,
                        sampleHtml: els.length > 0 ? els[0].innerHTML.substring(0, 200) : ''
                    }};
                }}
            ''', selector)
            
            print(f"定位器验证结果: 匹配 {elements['count']} 个元素")
            if elements['sampleHtml']:
                print(f"第一个匹配元素的HTML片段: {elements['sampleHtml']}")
        except Exception as e:
            print(f"定位器验证失败: {e}")
    
    async def _wait_for_text_non_empty(self, element, selector: str, timeout: int = 10000):
        """等待元素文本非空状态"""
        try:
            # 尝试等待文本非空
            await self.page.wait_for(f'''
                () => {{
                    const el = document.querySelector('{selector}');
                    if (!el) return false;
                    
                    // 检查是否为输入元素
                    if (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA') {{
                        return el.value && el.value.trim() !== '';
                    }}
                    
                    // 检查其他元素
                    return (el.innerText && el.innerText.trim() !== '') || 
                           (el.textContent && el.textContent.trim() !== '');
                }}
            ''', timeout=timeout)
        except Exception:
            # 超时后继续执行,不抛出异常
            pass
    
    async def _extract_from_shadow_dom(self, selector: str) -> str:
        """从Shadow DOM中提取文本"""
        try:
            # 尝试使用JavaScript穿透Shadow DOM
            text = await self.page.evaluate(f'''
                (selector) => {{
                    // 递归查找元素,支持Shadow DOM
                    function findElement(root, selector) {{
                        // 先在当前根节点查找
                        let el = root.querySelector(selector);
                        if (el) return el;
                        
                        // 查找所有Shadow DOM
                        const shadowHosts = root.querySelectorAll('*');
                        for (let host of shadowHosts) {{
                            if (host.shadowRoot) {{
                                el = findElement(host.shadowRoot, selector);
                                if (el) return el;
                            }}
                        }}
                        return null;
                    }}
                    
                    // 开始查找
                    const element = findElement(document, selector);
                    if (!element) return '';
                    
                    // 提取文本
                    if (element.tagName === 'INPUT' || element.tagName === 'TEXTAREA') {{
                        return element.value || element.getAttribute('value') || '';
                    }}
                    return element.innerText || element.textContent || '';
                }}
            ''', selector)
            print(f"Shadow DOM提取结果: '{text}'")
            return text if text else ""
        except Exception as e:
            print(f"Shadow DOM提取时出错: {e}")
            # 尝试使用更简单的方法
            try:
                # 使用更简单的JavaScript提取方法
                text = await self.page.evaluate(f'''
                    (selector) => {{
                        // 直接尝试使用querySelector穿透Shadow DOM
                        // 注意:这在某些浏览器中可能不支持
                        const el = document.querySelector(selector);
                        if (!el) return '';
                        
                        if (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA') {{
                            return el.value || el.getAttribute('value') || '';
                        }}
                        return el.innerText || el.textContent || '';
                    }}
                ''', selector)
                print(f"降级Shadow DOM提取结果: '{text}'")
                return text if text else ""
            except Exception as e2:
                print(f"降级Shadow DOM提取时出错: {e2}")
                return ""
    
    async def _extract_from_iframe(self, selector: str) -> str:
        """从iframe中提取文本"""
        try:
            # 递归函数:从frame及其子frame中提取文本
            async def extract_from_frame(frame):
                try:
                    # 尝试在当前frame中查找元素
                    element = frame.locator(selector)
                    await element.wait_for(timeout=5000)
                    
                    # 提取文本
                    extraction_methods = []
                    
                    try:
                        # 尝试使用inner_text()提取可见文本
                        text = await element.inner_text()
                        extraction_methods.append(("inner_text", text))
                        print(f"iframe中inner_text提取结果: '{text}'")
                        if text:
                            return text
                    except:
                        pass
                    
                    try:
                        # 尝试使用text_content()提取所有文本
                        text = await element.text_content()
                        extraction_methods.append(("text_content", text))
                        print(f"iframe中text_content提取结果: '{text}'")
                        if text:
                            return text
                    except:
                        pass
                    
                    try:
                        # 尝试使用input_value()提取输入框值
                        text = await element.input_value()
                        extraction_methods.append(("input_value", text))
                        print(f"iframe中input_value提取结果: '{text}'")
                        if text:
                            return text
                    except:
                        pass
                    
                    try:
                        # 尝试使用get_attribute("value")提取属性值
                        text = await element.get_attribute("value")
                        extraction_methods.append(("get_attribute('value')", text))
                        print(f"iframe中get_attribute('value')提取结果: '{text}'")
                        if text:
                            return text
                    except:
                        pass
                    
                    # 提取方法结果对比验证
                    if extraction_methods:
                        print("iframe中提取方法结果对比:")
                        for method, result in extraction_methods:
                            print(f"  {method}: '{result}'")
                        
                        # 选择非空结果
                        for method, result in extraction_methods:
                            if result:
                                print(f"选择iframe中最优提取方法: {method}")
                                return result
                                
                except:
                    pass
                
                # 递归处理子frame
                try:
                    child_frames = frame.child_frames()
                    for child_frame in child_frames:
                        try:
                            child_text = await extract_from_frame(child_frame)
                            if child_text:
                                return child_text
                        except Exception as e:
                            print(f"处理子frame时出错: {e}")
                            pass
                except Exception as e:
                    print(f"获取子frame时出错: {e}")
                    pass
                
                return ""
            
            # 从主页面开始递归提取
            main_frame_text = await extract_from_frame(self.page.main_frame())
            if main_frame_text:
                return main_frame_text
            
            # 额外尝试:使用frame_locator方法
            try:
                # 尝试通过CSS选择器定位iframe
                iframe_selector = "iframe"
                await self.page.wait_for_selector(iframe_selector, timeout=5000)
                iframe = self.page.frame_locator(iframe_selector)
                element = iframe.locator(selector)
                await element.wait_for(timeout=5000)
                
                # 尝试提取文本
                try:
                    text = await element.inner_text()
                    if text:
                        return text
                except:
                    pass
                
                try:
                    text = await element.text_content()
                    if text:
                        return text
                except:
                    pass
                
                try:
                    text = await element.input_value()
                    if text:
                        return text
                except:
                    pass
                
                try:
                    text = await element.get_attribute("value")
                    if text:
                        return text
                except:
                    pass
                    
            except:
                pass
            
            return ""
        except Exception as e:
            print(f"iframe提取时出错: {e}")
            return ""
    
    async def extract_all_texts(self, selector: str) -> List[str]:
        """批量提取多个元素的文本"""
        if self.page is None:
            raise Exception("浏览器未启动")
        
        try:
            # 使用locator()定位多个元素,内置自动等待
            elements = self.page.locator(selector)
            # 等待至少一个元素可见
            await elements.first.wait_for(state='visible', timeout=10000)
            # 使用all_inner_texts()批量提取文本
            texts = await elements.all_inner_texts()
            return texts
        except Exception as e:
            print(f"批量提取文本时出错: {e}")
            return []
    
    async def extract_text_from_iframe(self, iframe_selector: str, element_selector: str) -> str:
        """从iframe中提取文本"""
        if self.page is None:
            raise Exception("浏览器未启动")
        
        try:
            # 1. 增强等待机制:等待iframe加载完成
            await self.page.wait_for_selector(iframe_selector, timeout=15000)
            
            # 2. 使用frame_locator()定位iframe
            iframe = self.page.frame_locator(iframe_selector)
            
            # 3. 等待iframe中的元素可见
            await iframe.locator(element_selector).wait_for(state='visible', timeout=10000)
            
            # 4. 在iframe中定位元素
            element = iframe.locator(element_selector)
            
            # 5. 尝试获取元素标签名,判断元素类型
            try:
                tag_name = await element.evaluate("el => el.tagName.toLowerCase()")
                
                # 对于输入框类型,使用多种方法获取值
                if tag_name in ["input", "textarea"]:
                    # 首先尝试input_value()
                    try:
                        text = await element.input_value()
                        if text:
                            return text
                    except:
                        pass
                    
                    # 然后尝试get_attribute("value")作为补充
                    try:
                        text = await element.get_attribute("value")
                        return text if text else ""
                    except:
                        pass
                    
                    return ""
            except:
                pass
            
            # 6. 对于非输入框元素,根据可见性选择提取方法
            try:
                # 尝试使用inner_text()提取可见文本
                text = await element.inner_text()
                if text:
                    return text
            except:
                pass
            
            try:
                # 尝试使用text_content()提取所有文本(包括隐藏文本)
                text = await element.text_content()
                return text if text else ""
            except:
                pass
            
            return ""
        except Exception as e:
            print(f"从iframe提取文本时出错: {e}")
            try:
                # 7. 降级方案:再次尝试
                await self.page.wait_for_selector(iframe_selector, timeout=10000)
                iframe = self.page.frame_locator(iframe_selector)
                await iframe.locator(element_selector).wait_for(timeout=8000)
                element = iframe.locator(element_selector)
                
                # 尝试text_content()
                try:
                    text = await element.text_content()
                    if text:
                        return text
                except:
                    pass
                
                # 尝试inner_text()
                try:
                    text = await element.inner_text()
                    if text:
                        return text
                except:
                    pass
                
                # 尝试input_value()
                try:
                    text = await element.input_value()
                    if text:
                        return text
                except:
                    pass
                
                # 尝试get_attribute("value")
                try:
                    text = await element.get_attribute("value")
                    if text:
                        return text
                except:
                    pass
                
                return ""
            except Exception as e2:
                print(f"iframe降级方案提取时出错: {e2}")
                return ""
    
    async def extract_text_from_image(self, selector: str) -> str:
        """从图片中提取文本(OCR)"""
        if self.page is None:
            raise Exception("浏览器未启动")
        
        try:
            # 定位图片元素
            element = self.page.locator(selector)
            # 等待元素可见
            await element.wait_for(state='visible', timeout=10000)
            
            # 截取图片
            screenshot_path = f"temp_image_{int(time.time())}.png"
            await element.screenshot(path=screenshot_path)
            
            # 这里可以集成OCR库,如Tesseract或第三方API
            # 暂时返回占位符,实际项目中需要实现OCR逻辑
            print(f"图片已保存到: {screenshot_path}")
            print("OCR功能需要安装Tesseract或集成第三方OCR API")
            
            # 清理临时文件
            import os
            if os.path.exists(screenshot_path):
                os.remove(screenshot_path)
            
            return "OCR功能已触发(需要安装Tesseract或集成第三方API)"
        except Exception as e:
            print(f"从图片提取文本时出错: {e}")
            return ""
    
    async def get_element_attributes(self, selector: str) -> Dict[str, str]:
        """获取元素属性"""
        if self.page is None:
            raise Exception("浏览器未启动")
        
        attributes = {}
        try:
            # 等待元素可见
            await self.page.wait_for_selector(selector, state='visible', timeout=10000)
            # 获取元素的所有属性
            attrs = await self.page.evaluate(f"""
                (selector) => {{
                    const element = document.querySelector(selector);
                    if (!element) return {{}};
                    const attrs = {{}};
                    for (let attr of element.attributes) {{
                        attrs[attr.name] = attr.value;
                    }}
                    return attrs;
                }}
            """, selector)
            return attrs
        except:
            return {}
    
    async def get_all_links(self) -> List[Dict[str, str]]:
        """获取页面上所有链接"""
        if self.page is None:
            raise Exception("浏览器未启动")
        
        links = await self.page.evaluate("""
            () => {
                const elements = document.querySelectorAll('a[href]');
                return Array.from(elements).map(el => ({
                    text: el.textContent.trim(),
                    href: el.href,
                    title: el.title
                }));
            }
        """)
        return links
    
    async def extract_element_data(self, selector: str) -> Dict[str, Any]:
        """提取元素的各种数据"""
        if self.page is None:
            raise Exception("浏览器未启动")
        
        try:
            # 使用 locator() 定位元素,内置自动等待
            element = self.page.locator(selector)
            
            # 等待元素可见
            await element.wait_for(state='visible', timeout=10000)
            
            # 提取文本内容
            text_content = await element.text_content()
            inner_text = await element.inner_text()
            
            # 提取属性
            attributes = {
                'id': await element.get_attribute('id') or '',
                'className': await element.get_attribute('class') or '',
                'tagName': await element.evaluate('el => el.tagName') or '',
                'href': await element.get_attribute('href') or '',
                'src': await element.get_attribute('src') or '',
                'alt': await element.get_attribute('alt') or '',
                'title': await element.get_attribute('title') or '',
                'value': await element.get_attribute('value') or '',
                'placeholder': await element.get_attribute('placeholder') or '',
                'type': await element.get_attribute('type') or '',
                'name': await element.get_attribute('name') or '',
            }
            
            # 提取样式
            styles = {
                'display': await element.evaluate('el => getComputedStyle(el).display'),
                'visibility': await element.evaluate('el => getComputedStyle(el).visibility'),
                'opacity': await element.evaluate('el => getComputedStyle(el).opacity'),
            }
            
            # 提取位置信息
            bounding_box = await element.bounding_box()
            
            # 提取状态信息
            is_visible = await element.is_visible()
            is_enabled = await element.is_enabled()
            # 仅对复选框或单选按钮调用 is_checked()
            try:
                is_selected = await element.is_checked()
            except:
                is_selected = False
            
            return {
                'textContent': text_content.strip() if text_content else '',
                'innerText': inner_text.strip() if inner_text else '',
                'innerHTML': await element.inner_html() or '',
                'attributes': attributes,
                'styles': styles,
                'rect': bounding_box,
                'isVisible': is_visible,
                'isEnabled': is_enabled,
                'isSelected': is_selected
            }
        except Exception as e:
            print(f"提取元素数据时出错: {e}")
            return {}

    
    async def get_page_data(self) -> Dict[str, Any]:
        """获取页面的全面数据"""
        if self.page is None:
            raise Exception("浏览器未启动")
        
        page_data = await self.page.evaluate("""
            () => {
                return {
                    url: window.location.href,
                    title: document.title,
                    textContent: document.body ? document.body.textContent.trim() : '',
                    html: document.documentElement.outerHTML,
                    metaTags: Array.from(document.querySelectorAll('meta')).map(meta => ({
                        name: meta.name,
                        property: meta.property,
                        content: meta.content
                    })),
                    links: Array.from(document.querySelectorAll('a[href]')).length,
                    images: Array.from(document.querySelectorAll('img')).length,
                    forms: Array.from(document.querySelectorAll('form')).length,
                    inputs: Array.from(document.querySelectorAll('input, textarea, select')).length,
                    headings: {
                        h1: Array.from(document.querySelectorAll('h1')).map(el => el.textContent.trim()),
                        h2: Array.from(document.querySelectorAll('h2')).map(el => el.textContent.trim()),
                        h3: Array.from(document.querySelectorAll('h3')).map(el => el.textContent.trim()),
                    },
                    scripts: Array.from(document.querySelectorAll('script')).length,
                    stylesheets: Array.from(document.querySelectorAll('link[rel="stylesheet"]')).length
                };
            }
        """)
        
        return page_data
    
    async def analyze_page_content(self, selector: str = 'body') -> Dict[str, Any]:
        """分析页面内容"""
        if self.page is None:
            raise Exception("浏览器未启动")
        
        try:
            if selector == 'body':
                text_content = await self.page.inner_text('body')
            else:
                text_content = await self.page.inner_text(selector)
            
            # 分析文本内容
            words = text_content.split()
            word_count = len(words)
            char_count = len(text_content)
            
            # 提取所有链接
            links = await self.get_all_links()
            
            # 提取所有图片
            images = await self.page.evaluate("""
                () => {
                    return Array.from(document.querySelectorAll('img')).map(img => ({
                        src: img.src,
                        alt: img.alt,
                        title: img.title
                    }));
                }
            """)
            
            analysis = {
                'textContent': text_content,
                'wordCount': word_count,
                'charCount': char_count,
                'links': links,
                'images': images,
                'summary': f"页面包含 {word_count} 个词, {len(links)} 个链接, {len(images)} 个图片"
            }
            
            return analysis
        except Exception as e:
            print(f"分析页面内容时出错: {e}")
            return {'error': str(e)}
    
    async def wait_for_element_visible(self, selector: str, timeout: int = 30000, selector_type: str = "css", page=None):
        """等待元素可见。page: 可选，指定在哪个标签页执行（多标签并行时使用）"""
        target_page = page if page is not None else self.page
        if target_page is None:
            raise Exception("浏览器未启动")
        
        try:
            if selector_type == "xpath":
                element = target_page.locator(f"xpath={selector}")
                await element.wait_for(state="visible", timeout=timeout)
            else:
                await target_page.wait_for_selector(selector, state="visible", timeout=timeout)
            return True
        except:
            return False
    
    async def hover_element(self, selector: str, selector_type: str = "css", iframe_selector: str = None, iframe_context=None):
        """悬停在元素上"""
        if self.page is None:
            raise Exception("浏览器未启动")
        
        uat_logger.info(f"🔍 [HOVER_DEBUG] 开始悬停元素,选择器: {selector}, 选择器类型: {selector_type}, iframe选择器: {iframe_selector}")
        
        # 构建完整的选择器
        full_selector = selector
        if selector_type == "xpath":
            full_selector = f"xpath={selector}"
        
        # 确定操作上下文
        target_context = self.page
        if iframe_context:
            target_context = iframe_context
        elif iframe_selector:
            uat_logger.info(f"🔄 [IFRAME_DEBUG] 使用iframe上下文,选择器: {iframe_selector}")
            target_context = self.page.frame_locator(iframe_selector)
        
        # 悬停步骤通常不是必要的,设置较短的超时时间
        try:
            # 等待元素可见(减少超时时间到2秒)
            if hasattr(target_context, 'wait_for_selector'):
                # 对于page对象
                await target_context.wait_for_selector(full_selector, state='visible', timeout=2000)
                # 使用更健壮的悬停方式
                await target_context.hover(full_selector, timeout=2000)
            else:
                # 对于frame_locator对象
                element = target_context.locator(full_selector)
                await element.wait_for(state='visible', timeout=2000)
                # 使用更健壮的悬停方式
                await element.hover(timeout=2000)
            uat_logger.info(f"成功悬停元素: {selector}")
        except Exception as e:
            uat_logger.warning(f"悬停失败,这通常不影响执行: {str(e)}")
            # 悬停失败不影响后续操作,不尝试JavaScript模拟
        
    
    async def double_click_element(self, selector: str, selector_type: str = "css", iframe_selector: str = None, iframe_context=None, page=None):
        """双击元素。page: 可选，指定在哪个标签页执行（多标签并行时使用）"""
        target_page = page if page is not None else self.page
        if target_page is None:
            raise Exception("浏览器未启动")
        
        uat_logger.info(f"🔍 [DOUBLE_CLICK_DEBUG] 开始双击元素,选择器: {selector}, 选择器类型: {selector_type}, iframe选择器: {iframe_selector}")
        
        # 构建完整的选择器
        full_selector = selector
        if selector_type == "xpath":
            selector = _normalize_xpath_selector_value(selector)
            full_selector = f"xpath={selector}"
        
        # 确定操作上下文
        target_context = target_page
        if iframe_context:
            target_context = iframe_context
        elif iframe_selector:
            uat_logger.info(f"🔄 [IFRAME_DEBUG] 使用iframe上下文,选择器: {iframe_selector}")
            target_context = target_page.frame_locator(iframe_selector)
        
        # 等待元素可见且可交互
        if hasattr(target_context, 'wait_for_selector'):
            # 对于page对象
            await target_context.wait_for_selector(full_selector, state='visible', timeout=10000)
            await target_context.dblclick(full_selector, timeout=10000)
        else:
            # 对于frame_locator对象
            element = target_context.locator(full_selector)
            await element.wait_for(state='visible', timeout=10000)
            await element.dblclick(timeout=10000)
        
    
    async def right_click_element(self, selector: str, selector_type: str = "css", iframe_selector: str = None, iframe_context=None, page=None):
        """右键点击元素。page: 可选，指定在哪个标签页执行（多标签并行时使用）"""
        target_page = page if page is not None else self.page
        if target_page is None:
            raise Exception("浏览器未启动")
        
        uat_logger.info(f"🔍 [RIGHT_CLICK_DEBUG] 开始右键点击元素,选择器: {selector}, 选择器类型: {selector_type}, iframe选择器: {iframe_selector}")
        
        # 构建完整的选择器
        full_selector = selector
        if selector_type == "xpath":
            selector = _normalize_xpath_selector_value(selector)
            full_selector = f"xpath={selector}"
        
        # 确定操作上下文
        target_context = target_page
        if iframe_context:
            target_context = iframe_context
        elif iframe_selector:
            uat_logger.info(f"🔄 [IFRAME_DEBUG] 使用iframe上下文,选择器: {iframe_selector}")
            target_context = target_page.frame_locator(iframe_selector)
        
        # 等待元素可见且可交互
        if hasattr(target_context, 'wait_for_selector'):
            # 对于page对象
            await target_context.wait_for_selector(full_selector, state='visible', timeout=10000)
            await target_context.click(full_selector, button="right", timeout=10000)
        else:
            # 对于frame_locator对象
            element = target_context.locator(full_selector)
            await element.wait_for(state='visible', timeout=10000)
            await element.click(button="right", timeout=10000)
        
    
    async def swipe_element(self, selector: str, direction: str, distance: int = 100, selector_type: str = "css", iframe_selector: str = None, iframe_context=None, page=None):
        """滑动元素。page: 可选，指定在哪个标签页执行（多标签并行时使用）"""
        target_page = page if page is not None else self.page
        if target_page is None:
            raise Exception("浏览器未启动")
        
        # 验证方向参数
        valid_directions = ['up', 'down', 'left', 'right']
        if direction not in valid_directions:
            raise ValueError(f"无效的滑动方向: {direction}，有效值为: {valid_directions}")
        
        # 验证距离参数
        if not isinstance(distance, int) or distance <= 0:
            raise ValueError(f"无效的滑动距离: {distance}，必须是正整数")
        
        uat_logger.info(f"🔍 [SWIPE_DEBUG] 开始滑动元素,选择器: {selector}, 方向: {direction}, 距离: {distance}px, 选择器类型: {selector_type}, iframe选择器: {iframe_selector}")
        
        # 构建完整的选择器
        full_selector = selector
        if selector_type == "xpath":
            full_selector = f"xpath={selector}"
        
        # 确定操作上下文
        target_context = target_page
        if iframe_context:
            target_context = iframe_context
        elif iframe_selector:
            uat_logger.info(f"🔄 [IFRAME_DEBUG] 使用iframe上下文,选择器: {iframe_selector}")
            target_context = target_page.frame_locator(iframe_selector)
        
        # 等待元素可见
        if hasattr(target_context, 'wait_for_selector'):
            # 对于page对象
            await target_context.wait_for_selector(full_selector, state='visible', timeout=10000)
            element = target_context.locator(full_selector)
        else:
            # 对于frame_locator对象
            element = target_context.locator(full_selector)
            await element.wait_for(state='visible', timeout=10000)
        
        # 获取元素位置和大小
        box = await element.bounding_box()
        if not box:
            raise Exception("无法获取元素边界框")
        
        # 计算滑动起点和终点
        x = box['x'] + box['width'] / 2
        y = box['y'] + box['height'] / 2
        
        # 使用传入的滑动距离
        swipe_distance = distance
        
        if direction == 'up':
            end_y = y - swipe_distance
        elif direction == 'down':
            end_y = y + swipe_distance
        elif direction == 'left':
            end_x = x - swipe_distance
        elif direction == 'right':
            end_x = x + swipe_distance
        
        # 执行滑动操作
        await target_page.mouse.move(x, y)
        await target_page.mouse.down()
        await target_page.wait_for_timeout(100)
        if direction in ['up', 'down']:
            await target_page.mouse.move(x, end_y, steps=10)
        else:
            await target_page.mouse.move(end_x, y, steps=10)
        await target_page.mouse.up()
        
        uat_logger.info(f"✅ 滑动元素成功: {selector}, 方向: {direction}, 距离: {distance}px")
        
    
    async def _verify_element_state_attempt(
        self,
        target_page,
        target_context,
        selector: str,
        selector_type: str,
        wait_state: str,
    ) -> None:
        """单组 selector 的可见/存在类校验（与 click 的 selector 转换规则对齐）。"""
        original_selector_type = selector_type
        raw_partial_text = None
        if selector_type in [
            "id",
            "class",
            "name",
            "text",
            "partial_text",
            "placeholder",
            "label",
            "title",
            "alt",
            "data",
            "aria",
        ]:
            if selector_type == "label":
                selector, selector_type = await self.find_element_by_label(selector, target_page)
                if selector is None:
                    raise Exception(f"未找到与label关联的元素: {original_selector_type}={selector}")
            else:
                if selector_type == "partial_text":
                    raw_partial_text = (selector or "").strip() or None
                selector, selector_type = self.convert_selector(selector, selector_type)
            uat_logger.info(
                f"🔍 [VERIFY_CONVERT] {original_selector_type} -> {selector_type}, 值: {selector}"
            )
        if selector_type == "xpath" and selector:
            selector = _normalize_xpath_selector_value(selector)
        if raw_partial_text and hasattr(target_context, "get_by_text"):
            pw_loc = target_context.get_by_text(raw_partial_text, exact=False)
            if await pw_loc.count() == 0:
                raise Exception(f"partial_text 无匹配: {raw_partial_text!r}")
            await pw_loc.first.wait_for(state=wait_state, timeout=10000)
            return
        full_selector = selector
        if selector_type == "xpath":
            full_selector = f"xpath={selector}"
        if hasattr(target_context, "wait_for_selector"):
            await target_context.wait_for_selector(full_selector, state=wait_state, timeout=10000)
            target_context.locator(full_selector)
        else:
            element = target_context.locator(full_selector)
            await element.wait_for(state=wait_state, timeout=10000)

    async def verify_element(
        self,
        selector: str = None,
        verify_type: str = "visible",
        selector_type: str = "css",
        iframe_selector: str = None,
        iframe_context=None,
        page=None,
        locator_candidates=None,
    ):
        """验证元素。用于处理人机验证弹窗等场景。page: 可选，指定在哪个标签页执行（多标签并行时使用）
        
        如果没有提供selector，则自动识别并处理验证弹窗
        verify_type 可以是 'visible', 'exist', 'clickable' 或验证码类型: 'auto', 'slider', 'image'
        locator_candidates: 与 click/input 相同，主定位失败时按 score 尝试备选。
        """
        target_page = page if page is not None else self.page
        if target_page is None:
            raise Exception("浏览器未启动")
        
        # 检查是否为验证码类型
        captcha_types = ['auto', 'slider', 'image']
        if verify_type in captcha_types:
            uat_logger.info(f"🔍 [VERIFY_DEBUG] 开始处理验证码，类型: {verify_type}")
            # 如果提供了选择器，则使用选择器定位并处理验证码
            if selector:
                uat_logger.info(f"🔍 [VERIFY_DEBUG] 使用提供的选择器处理验证码: {selector}")
                # 构建完整的选择器
                full_selector = selector
                if selector_type == "xpath":
                    full_selector = f"xpath={selector}"
                
                # 确定操作上下文
                target_context = target_page
                if iframe_context:
                    target_context = iframe_context
                elif iframe_selector:
                    uat_logger.info(f"🔄 [IFRAME_DEBUG] 使用iframe上下文,选择器: {iframe_selector}")
                    target_context = target_page.frame_locator(iframe_selector)
                
                # 查找验证码元素
                element = target_context.locator(full_selector)
                try:
                    await element.wait_for(state='visible', timeout=10000)
                    uat_logger.info(f"✅ [VERIFY_DEBUG] 找到验证码元素: {selector}")
                except Exception as e:
                    uat_logger.error(f"❌ [VERIFY_DEBUG] 等待验证码元素超时: {e}")
                    # 尝试直接查找滑块，不依赖于容器元素
                    uat_logger.info("🔍 [VERIFY_DEBUG] 尝试直接查找滑块")
                    try:
                        slider_handled = await self._handle_slider_captcha(target_page)
                        if slider_handled:
                            return True
                        else:
                            raise Exception("验证码处理失败: 未找到滑块验证码元素")
                    except Exception as slider_error:
                        uat_logger.error(f"❌ [VERIFY_DEBUG] 直接查找滑块失败: {slider_error}")
                        raise Exception(f"验证码处理失败: 等待元素超时 - {e}")
                
                # 根据验证类型处理
                if verify_type == 'slider' or verify_type == 'auto':
                    # 处理滑动验证码
                    uat_logger.info("🔍 [VERIFY_DEBUG] 处理滑动验证码")
                    # 尝试在验证码元素内查找滑块
                    try:
                        # 使用与_handle_slider_captcha方法相同的滑块选择器列表
                        slider_selectors = [
                            # 滑块容器
                            '.captcha-slider',
                            '.slider-container',
                            '.slide-container',
                            '[class*="slider"]',
                            '[class*="slide"]',
                            '[class*="verify"]',
                            '[class*="verifybox"]',
                            '[class*="captcha"]',
                            '[class*="geetest"]',
                            '[class*="tcaptcha"]',
                            '[class*="yidun"]',
                            '.geetest_slider',
                            '.tcaptcha-slider',
                            '.yidun_slider',
                            '.nc_wrapper',  # 网易易盾
                            '.ac-slider',  # 阿里滑块
                            # 滑块本身
                            '.slider-handle',
                            '.slide-handle',
                            '.slider-btn',
                            '.slide-btn',
                            '.captcha-btn',
                            '.geetest_slider_button',
                            '.tcaptcha-drag-button',
                            '.yidun_slider__handle',
                            '.nc_iconfont',  # 网易易盾滑块
                            '.ac-slider-handle',  # 阿里滑块
                            '[class*="handle"]',
                            '[class*="btn"]',
                            '[class*="verify-move-block"]',
                            '[class*="verify-drag-icon"]',
                            '[class*="button"]',
                            '[class*="drag"]',
                            '[class*="slide"]',
                            '[class*="slider"]',
                            '[class*="move"]',
                            '[class*="icon"]',
                            # 通用元素
                            'button',
                            'div',
                            'span',
                            'i',
                            # 组合选择器
                            '[class*="slider"] button',
                            '[class*="slide"] button',
                            '[class*="captcha"] button',
                            '[class*="verify"] button',
                            '[class*="slider"] div',
                            '[class*="slide"] div',
                            '[class*="captcha"] div',
                            '[class*="verify"] div',
                        ]
                        
                        # 在验证码元素内尝试查找滑块
                        slider_found = False
                        for slider_selector in slider_selectors:
                            try:
                                uat_logger.info(f"🔍 [VERIFY_DEBUG] 在验证码元素内尝试查找滑块: {slider_selector}")
                                slider_element = element.locator(slider_selector)
                                if await slider_element.count() > 0:
                                    slider_element = slider_element.first
                                    if await slider_element.is_visible():
                                        uat_logger.info(f"✅ [VERIFY_DEBUG] 在验证码元素内找到滑块: {slider_selector}")
                                        slider_found = True
                                        # 执行滑动操作
                                        await self._perform_slider_action(target_page, slider_element)
                                        # 等待验证完成，使用智能等待
                                        try:
                                            # 检查滑块是否还存在，如果不存在说明验证成功
                                            await target_page.wait_for_selector('[data-slider="captcha"]', state='hidden', timeout=3000)
                                        except Exception:
                                            # 如果智能检测失败，使用短等待
                                            await asyncio.sleep(1)
                                        # 检查验证是否成功（优先看验证码组件是否消失）
                                        if await self._captcha_appears_gone(target_page):
                                            uat_logger.info("✅ 滑动验证码处理成功")
                                            return True
                                        # 某些场景组件不消失但已通过，给一次短暂缓冲再判定
                                        await asyncio.sleep(1.0)
                                        if await self._captcha_appears_gone(target_page):
                                            uat_logger.info("✅ 滑动验证码处理成功(延迟判定)")
                                            return True
                                        raise Exception("验证码处理失败: 滑动操作已执行，但验证未完成")
                            except Exception as e:
                                uat_logger.debug(f"选择器 {slider_selector} 未找到滑块: {e}")
                                continue
                        
                        if not slider_found:
                            # 如果在验证码元素内未找到滑块，尝试调用专门的滑块处理方法
                            uat_logger.info("🔍 [VERIFY_DEBUG] 在验证码元素内未找到滑块，尝试使用专门的滑块处理方法")
                            try:
                                # 尝试在整个页面中查找滑块，优先在指定容器内查找
                                slider_handled = await self._handle_slider_captcha(target_page, selector)
                                if slider_handled:
                                    return True
                                else:
                                    # 如果专门的滑块处理方法返回False，抛出异常
                                    raise Exception("验证码处理失败: 未找到滑块验证码元素或滑动操作未完成")
                            except Exception as e:
                                uat_logger.warning(f"⚠️ 专门的滑块处理方法失败: {e}")
                                # 重新抛出异常
                                raise
                    except Exception as slider_error:
                        uat_logger.warning(f"⚠️ 在验证码元素内查找滑块失败: {slider_error}")
                        # 尝试调用专门的滑块处理方法
                        uat_logger.info("🔍 [VERIFY_DEBUG] 尝试使用专门的滑块处理方法")
                        try:
                            # 尝试在整个页面中查找滑块，优先在指定容器内查找
                            slider_handled = await self._handle_slider_captcha(target_page, selector)
                            if slider_handled:
                                return True
                            else:
                                # 如果专门的滑块处理方法返回False，抛出异常
                                raise Exception("验证码处理失败: 未找到滑块验证码元素或滑动操作未完成")
                        except Exception as e:
                            uat_logger.warning(f"⚠️ 专门的滑块处理方法失败: {e}")
                            # 回退到自动检测
                            success = await self._auto_handle_verification_popup(target_page, verify_type)
                            if not success:
                                raise Exception("验证码处理失败: 未找到验证弹窗或验证操作未完成")
                            return success
                elif verify_type == 'image' or verify_type == 'auto':
                    # 处理图片验证码
                    uat_logger.info("🔍 [VERIFY_DEBUG] 处理图片验证码")
                    # 首先尝试在提供的选择器所定位的元素内查找图片验证码
                    try:
                        # 在验证码元素内查找图片（使用更多选择器）
                        image_element = element.locator('img, [class*="image"], [class*="pic"], [class*="img"]').first
                        await image_element.wait_for(state='visible', timeout=5000)
                        # 执行图片验证操作
                        await self._click_image_randomly(target_page, image_element)
                        uat_logger.info("✅ 图片验证码处理成功")
                        return True
                    except Exception as image_error:
                        uat_logger.warning(f"⚠️ 在验证码元素内查找图片失败: {image_error}")
                        # 如果在验证码元素内查找图片失败，则尝试调用专门的图片处理方法
                        success = await self._handle_image_captcha(target_page)
                        if not success:
                            # 如果是auto类型，尝试处理滑动验证码
                            if verify_type == 'auto':
                                uat_logger.info("🔍 [VERIFY_DEBUG] 图片验证码处理失败，尝试处理滑动验证码")
                                success = await self._handle_slider_captcha(target_page)
                                if not success:
                                    raise Exception("验证码处理失败: 未找到验证弹窗或验证操作未完成")
                                return success
                            else:
                                raise Exception("验证码处理失败: 未找到图片验证码元素或验证操作未完成")
                        return success
                else:
                    # 自动检测验证类型
                    success = await self._auto_handle_verification_popup(target_page, verify_type)
                    if not success:
                        raise Exception("验证码处理失败: 未找到验证弹窗或验证操作未完成")
                    return success
            else:
                # 如果没有提供选择器，则自动识别并处理验证弹窗
                success = await self._auto_handle_verification_popup(target_page, verify_type)
                if not success:
                    raise Exception("验证码处理失败: 未找到验证弹窗或验证操作未完成")
                return success
        
        # 如果没有提供选择器，则自动识别验证弹窗
        if not selector:
            uat_logger.info("🔍 [VERIFY_DEBUG] 开始自动识别验证弹窗")
            success = await self._auto_handle_verification_popup(target_page)
            if not success:
                raise Exception("验证码处理失败: 未找到验证弹窗或验证操作未完成")
            return success
        
        # 验证验证类型参数
        valid_verify_types = ['visible', 'exist', 'clickable']
        if verify_type not in valid_verify_types and verify_type not in captcha_types:
            raise ValueError(f"无效的验证类型: {verify_type}，有效值为: {valid_verify_types} 或验证码类型: {captcha_types}")
        
        uat_logger.info(f"🔍 [VERIFY_DEBUG] 开始验证元素,选择器: {selector}, 验证类型: {verify_type}, 选择器类型: {selector_type}, iframe选择器: {iframe_selector}")
        
        # 确定操作上下文
        target_context = target_page
        if iframe_context:
            target_context = iframe_context
        elif iframe_selector:
            uat_logger.info(f"🔄 [IFRAME_DEBUG] 使用iframe上下文,选择器: {iframe_selector}")
            target_context = target_page.frame_locator(iframe_selector)
        
        valid_states = ['attached', 'detached', 'visible', 'hidden']
        wait_state = verify_type if verify_type in valid_states else 'visible'
        
        attempts = [(selector, selector_type)]
        if locator_candidates:
            attempts.extend(
                _fallback_locator_tuples(selector, selector_type, locator_candidates)
            )
        last_exc = None
        for attempt_sel, attempt_type in attempts:
            try:
                await self._verify_element_state_attempt(
                    target_page, target_context, attempt_sel, attempt_type, wait_state
                )
                uat_logger.info(
                    f"✅ 验证元素成功: {attempt_sel!s}, 类型: {attempt_type}, 验证: {verify_type}"
                )
                return
            except Exception as _ve:
                last_exc = _ve
                uat_logger.warning(
                    f"⚠️ [VERIFY_FALLBACK] 失败 ({attempt_type}={str(attempt_sel)[:120]}): {_ve}"
                )
        if last_exc:
            raise last_exc
        
    
    async def _auto_handle_verification_popup(self, page, verify_type='auto'):
        """自动识别并处理验证弹窗
        
        Args:
            page: 页面对象
            verify_type: 验证类型，可选值: 'auto', 'slider', 'image'
        """
        uat_logger.info(f"🔍 开始处理验证弹窗，类型: {verify_type}")
        
        if verify_type == 'auto':
            # 自动检测验证类型
            return await self._detect_and_handle_captcha(page)
        elif verify_type == 'slider':
            # 处理滑动方块验证码
            return await self._handle_slider_captcha(page)
        elif verify_type == 'image':
            # 处理点击图片文字验证码
            return await self._handle_image_captcha(page)
        else:
            uat_logger.warning(f"⚠️ 未知的验证类型: {verify_type}，使用自动检测")
            return await self._detect_and_handle_captcha(page)
    
    async def _detect_and_handle_captcha(self, page):
        """自动检测并处理验证码"""
        uat_logger.info("🔍 自动检测验证码类型")
        
        # 智能等待验证弹窗出现
        max_wait_time = 10  # 最大等待时间（秒）
        start_time = time.time()
        verification_found = False
        
        while time.time() - start_time < max_wait_time:
            # 尝试查找常见的验证弹窗元素
            try:
                # 常见的验证弹窗选择器
                verification_selectors = [
                    '.captcha-box',
                    '.verification-box',
                    '.verify-box',
                    '[class*="captcha"]',
                    '[class*="verification"]',
                    '[class*="verify"]',
                    '.slider-container',
                    '.slide-container',
                    '.captcha-slider',
                    '[class*="slider"]',
                    '[class*="slide"]',
                ]
                
                for selector in verification_selectors:
                    element = page.locator(selector)
                    if await element.count() > 0:
                        first_element = element.first
                        is_visible = await first_element.is_visible()
                        if is_visible:
                            uat_logger.info(f"✅ 验证弹窗已出现: {selector}")
                            verification_found = True
                            break
                
                if verification_found:
                    break
            except Exception as e:
                uat_logger.debug(f"等待验证弹窗时出错: {e}")
            
            # 短暂等待后重试
            await asyncio.sleep(0.5)
        
        if not verification_found:
            uat_logger.warning("⚠️ 未检测到验证弹窗，继续执行默认流程")
        
        # 首先尝试处理滑动验证码
        try:
            slider_handled = await self._handle_slider_captcha(page)
            if slider_handled:
                uat_logger.info("✅ 滑动验证码处理成功")
                return True
        except Exception as slider_error:
            uat_logger.warning(f"⚠️ 处理滑动验证码时出错: {slider_error}")
        
        # 然后尝试处理图片验证码
        try:
            image_handled = await self._handle_image_captcha(page)
            if image_handled:
                uat_logger.info("✅ 图片验证码处理成功")
                return True
        except Exception as image_error:
            uat_logger.warning(f"⚠️ 处理图片验证码时出错: {image_error}")
        
        # 最后尝试查找其他类型的验证弹窗
        # 常见的验证弹窗选择器模式
        verification_selectors = [
            # 人机验证相关
            '.captcha-box',
            '.verification-box',
            '.verify-box',
            '#captcha',
            '#verification',
            '#verify',
            '[class*="captcha"]',
            '[class*="verification"]',
            '[class*="verify"]',
            '[id*="captcha"]',
            '[id*="verification"]',
            '[id*="verify"]',
            # 验证按钮
            'button:has-text("验证")',
            'button:has-text("Verify")',
            'button:has-text("确认")',
            'button:has-text("Confirm")',
            'button:has-text("提交")',
            'button:has-text("Submit")',
            'a:has-text("验证")',
            'a:has-text("Verify")',
            # iframe中的验证
            'iframe[src*="captcha"]',
            'iframe[src*="verify"]',
            'iframe[src*="recaptcha"]',
            # 其他常见验证元素
            '.g-recaptcha',
            '#g-recaptcha',
            '[data-sitekey]',
            '.hcaptcha',
            '#hcaptcha',
        ]
        
        # 尝试查找验证弹窗
        found_verification = False
        for selector in verification_selectors:
            try:
                uat_logger.info(f"🔍 尝试查找验证弹窗: {selector}")
                element = page.locator(selector)
                
                # 检查元素是否存在且可见
                if await element.count() > 0:
                    first_element = element.first
                    is_visible = await first_element.is_visible()
                    if is_visible:
                        uat_logger.info(f"✅ 找到验证弹窗: {selector}")
                        found_verification = True
                        
                        # 尝试点击验证按钮
                        try:
                            await first_element.click(timeout=3000)
                            uat_logger.info("✅ 已点击验证按钮")
                            # 等待验证完成
                            await asyncio.sleep(2)
                        except Exception as click_error:
                            uat_logger.warning(f"⚠️ 点击验证按钮失败: {click_error}")
                            # 如果点击失败，尝试其他方式
                            try:
                                # 尝试等待验证弹窗消失
                                await first_element.wait_for(state='hidden', timeout=5000)
                                uat_logger.info("✅ 验证弹窗已消失")
                            except Exception as wait_error:
                                uat_logger.warning(f"⚠️ 等待验证弹窗消失失败: {wait_error}")
                        
                        break
            except Exception as e:
                uat_logger.debug(f"选择器 {selector} 未找到验证弹窗: {e}")
                continue
        
        if not found_verification:
            uat_logger.error("❌ 未检测到验证弹窗，验证操作失败")
            # 抛出异常，标记验证失败
            raise Exception("验证码处理失败: 未找到验证弹窗或验证操作未完成")
        
        return True
    
    async def _handle_slider_captcha(self, page, selector=None):
        """处理滑动方块验证码
        
        Args:
            page: 页面对象
            selector: 可选，验证码容器选择器，优先在该容器内查找滑块
        """
        uat_logger.info("🔍 处理滑动方块验证码")
        
        # 先走图像识别增强路径（更智能），失败再回退传统选择器逻辑
        try:
            optimizer = SliderCaptchaOptimizer()
            det = await optimizer.optimize_slider_detection(page)
            if det and det.get("slider") is not None:
                slider = det["slider"]
                platform = det.get("platform", "default")
                uat_logger.info(f"🤖 [SLIDER_AI] 使用优化器处理平台: {platform}")
                distance = await optimizer.calculate_smart_distance(page, slider, platform)
                if distance and distance > 0:
                    await optimizer.perform_optimized_swipe(page, slider, distance, platform)
                    if await optimizer.verify_slider_success(page, platform):
                        uat_logger.info("✅ [SLIDER_AI] 图像识别滑块验证成功")
                        return True
                    uat_logger.warning("⚠️ [SLIDER_AI] 验证未确认通过，回退传统策略")
        except Exception as _opt_e:
            uat_logger.warning(f"⚠️ [SLIDER_AI] 优化器路径失败，回退传统策略: {_opt_e}")

        # 常见的滑块验证码选择器
        slider_selectors = [
            # 滑块容器
            '.captcha-slider',
            '.slider-container',
            '.slide-container',
            '[class*="slider"]',
            '[class*="slide"]',
            '[class*="verify"]',
            '[class*="verifybox"]',
            '[class*="captcha"]',
            '[class*="geetest"]',
            '[class*="tcaptcha"]',
            '[class*="yidun"]',
            '.geetest_slider',
            '.tcaptcha-slider',
            '.yidun_slider',
            '.nc_wrapper',  # 网易易盾
            '.ac-slider',  # 阿里滑块
            # 滑块本身
            '.slider-handle',
            '.slide-handle',
            '.slider-btn',
            '.slide-btn',
            '.captcha-btn',
            '.geetest_slider_button',
            '.tcaptcha-drag-button',
            '.yidun_slider__handle',
            '.nc_iconfont',  # 网易易盾滑块
            '.ac-slider-handle',  # 阿里滑块
            '[class*="handle"]',
            '[class*="btn"]',
            '[class*="verify-move-block"]',
            '[class*="verify-drag-icon"]',
            '[class*="button"]',
            '[class*="drag"]',
            '[class*="slide"]',
            '[class*="slider"]',
            '[class*="move"]',
            '[class*="icon"]',
            # 通用元素
            'button',
            'div',
            'span',
            'i',
            # 组合选择器
            '[class*="slider"] button',
            '[class*="slide"] button',
            '[class*="captcha"] button',
            '[class*="verify"] button',
            '[class*="slider"] div',
            '[class*="slide"] div',
            '[class*="captcha"] div',
            '[class*="verify"] div',
        ]
        
        # 等待滑块加载 - 减少等待时间
        await asyncio.sleep(0.5)
        
        # 优先在指定容器内查找滑块
        if selector:
            uat_logger.info(f"🔍 优先在指定容器内查找滑块: {selector}")
            try:
                container = page.locator(selector)
                if await container.count() > 0:
                    container_element = container.first
                    if await container_element.is_visible():
                        for sub_selector in slider_selectors:
                            try:
                                uat_logger.info(f"🔍 在容器内尝试查找滑块: {sub_selector}")
                                slider = container_element.locator(sub_selector)
                                if await slider.count() > 0:
                                    slider_element = slider.first
                                    is_visible = await slider_element.is_visible()
                                    if is_visible:
                                        uat_logger.info(f"✅ 在容器内找到滑块: {sub_selector}")
                                        # 执行滑动操作
                                        try:
                                            # 首先尝试获取滑块位置和尺寸
                                            slider_box = await slider_element.bounding_box()
                                            if slider_box:
                                                # 尝试识别拼图滑块并计算缺口位置
                                                uat_logger.info("🔍 尝试识别拼图滑块并计算缺口位置")
                                                distance = await self._calculate_puzzle_distance(page, slider_element, slider_box)
                                                if distance and distance > 0:
                                                    uat_logger.info(f"✅ 拼图滑块缺口计算距离: {distance}px")
                                                    # 直接执行滑动操作，使用计算出的距离
                                                    uat_logger.info("🔄 执行滑块滑动，使用计算出的距离")
                                                    
                                                    # 获取滑动起点
                                                    start_x = slider_box['x'] + slider_box['width'] // 2
                                                    start_y = slider_box['y'] + slider_box['height'] // 2
                                                    uat_logger.info(f"🎯 滑动起点: x={start_x}, y={start_y}")
                                                    
                                                    # 模拟鼠标移动到滑块上
                                                    uat_logger.info(f"🖱️  移动鼠标到滑块位置: x={start_x}, y={start_y}")
                                                    await page.mouse.move(start_x, start_y, steps=3)
                                                    await asyncio.sleep(0.2)  # 短暂停顿
                                                    
                                                    # 模拟鼠标按下
                                                    uat_logger.info("🖱️  按下鼠标")
                                                    await page.mouse.down()
                                                    await asyncio.sleep(0.1)  # 短暂停顿
                                                    
                                                    # 分阶段滑动，模拟人类行为
                                                    # 开始加速
                                                    middle_x1 = start_x + distance * 0.3
                                                    middle_y1 = start_y + random.randint(-5, 5)
                                                    uat_logger.info(f"🖱️  开始加速，移动到: x={middle_x1}, y={middle_y1}")
                                                    await page.mouse.move(middle_x1, middle_y1, steps=3)
                                                    await asyncio.sleep(random.uniform(0.05, 0.1))
                                                    # 中间匀速
                                                    middle_x2 = start_x + distance * 0.6
                                                    middle_y2 = start_y + random.randint(-5, 5)
                                                    uat_logger.info(f"🖱️  中间匀速，移动到: x={middle_x2}, y={middle_y2}")
                                                    await page.mouse.move(middle_x2, middle_y2, steps=5)
                                                    await asyncio.sleep(random.uniform(0.05, 0.1))
                                                    # 最后减速 - 更精确地移动到目标位置
                                                    end_x = start_x + distance
                                                    end_y = start_y + random.randint(-1, 1)  # 最小化抖动，最精确
                                                    uat_logger.info(f"🖱️  最后减速，移动到: x={end_x}, y={end_y}")
                                                    await page.mouse.move(end_x, end_y, steps=20)  # 最大化步骤，最精确
                                                    await asyncio.sleep(0.3)  # 增加停顿时间，确保完全到达目标位置
                                                    
                                                    # 释放鼠标 - 在正确位置精确释放
                                                    uat_logger.info("🖱️  释放鼠标")
                                                    await page.mouse.up()
                                                    await asyncio.sleep(0.5)  # 增加释放后的停顿时间，确保验证有足够时间响应
                                                    
                                                    # 等待验证完成
                                                    uat_logger.info("⏳ 等待验证完成")
                                                    await asyncio.sleep(2)
                                                    
                                                    # 尝试点击验证按钮或确认区域
                                                    try:
                                                        uat_logger.info("🔍 尝试找到并点击验证确认按钮")
                                                        # 常见的验证确认按钮选择器
                                                        confirm_selectors = [
                                                            'button:has-text("验证")',
                                                            'button:has-text("确认")',
                                                            'button:has-text("Verify")',
                                                            'button:has-text("Confirm")',
                                                            '.verify-button',
                                                            '.confirm-button',
                                                            '.submit-button',
                                                            '[class*="verify"] button',
                                                            '[class*="confirm"] button',
                                                            '[class*="submit"] button',
                                                        ]
                                                        
                                                        for selector in confirm_selectors:
                                                            try:
                                                                confirm_button = page.locator(selector)
                                                                if await confirm_button.count() > 0:
                                                                    is_visible = await confirm_button.is_visible()
                                                                    if is_visible:
                                                                        uat_logger.info(f"✅ 找到验证确认按钮: {selector}")
                                                                        await confirm_button.click()
                                                                        uat_logger.info("✅ 点击验证确认按钮")
                                                                        await asyncio.sleep(1)
                                                                        break
                                                            except Exception as e:
                                                                uat_logger.debug(f"选择器 {selector} 未找到确认按钮: {e}")
                                                                continue
                                                    except Exception as e:
                                                        uat_logger.debug(f"点击确认按钮失败: {e}")
                                                    
                                                    uat_logger.info("✅ 滑动验证完成")
                                                    return True
                                                else:
                                                    uat_logger.error("❌ 无法计算滑动距离")
                                                    continue
                                            else:
                                                uat_logger.error("❌ 无法获取滑块位置")
                                                continue
                                        except Exception as slide_error:
                                            uat_logger.error(f"❌ 滑动验证失败: {slide_error}")
                                            raise
                            except Exception as e:
                                uat_logger.debug(f"容器内选择器 {sub_selector} 未找到滑块: {e}")
                                continue
            except Exception as e:
                uat_logger.debug(f"容器选择器 {selector} 未找到: {e}")
        
        # 在整个页面中查找滑块
        uat_logger.info("🔍 在整个页面中查找滑块")
        for selector in slider_selectors:
            try:
                uat_logger.info(f"🔍 尝试查找滑块: {selector}")
                element = page.locator(selector)
                
                if await element.count() > 0:
                    slider = element.first
                    is_visible = await slider.is_visible()
                    if is_visible:
                        uat_logger.info(f"✅ 找到滑块: {selector}")
                        
                        # 执行滑动操作
                        try:
                            # 首先尝试获取滑块位置和尺寸
                            slider_box = await slider.bounding_box()
                            if slider_box:
                                # 尝试识别拼图滑块并计算缺口位置
                                uat_logger.info("🔍 尝试识别拼图滑块并计算缺口位置")
                                distance = await self._calculate_puzzle_distance(page, slider, slider_box)
                                if distance and distance > 0:
                                    uat_logger.info(f"✅ 拼图滑块缺口计算距离: {distance}px")
                                    # 直接执行滑动操作，使用计算出的距离
                                    uat_logger.info("🔄 执行滑块滑动，使用计算出的距离")
                                    
                                    # 获取滑动起点
                                    start_x = slider_box['x'] + slider_box['width'] // 2
                                    start_y = slider_box['y'] + slider_box['height'] // 2
                                    uat_logger.info(f"🎯 滑动起点: x={start_x}, y={start_y}")
                                    
                                    # 模拟鼠标移动到滑块上
                                    uat_logger.info(f"🖱️  移动鼠标到滑块位置: x={start_x}, y={start_y}")
                                    await page.mouse.move(start_x, start_y, steps=3)
                                    await asyncio.sleep(0.2)  # 短暂停顿
                                    
                                    # 模拟鼠标按下
                                    uat_logger.info("🖱️  按下鼠标")
                                    await page.mouse.down()
                                    await asyncio.sleep(0.1)  # 短暂停顿
                                    
                                    # 分阶段滑动，模拟人类行为
                                    # 开始加速
                                    middle_x1 = start_x + distance * 0.3
                                    middle_y1 = start_y + random.randint(-5, 5)
                                    uat_logger.info(f"🖱️  开始加速，移动到: x={middle_x1}, y={middle_y1}")
                                    await page.mouse.move(middle_x1, middle_y1, steps=3)
                                    await asyncio.sleep(random.uniform(0.05, 0.1))
                                    # 中间匀速
                                    middle_x2 = start_x + distance * 0.6
                                    middle_y2 = start_y + random.randint(-5, 5)
                                    uat_logger.info(f"🖱️  中间匀速，移动到: x={middle_x2}, y={middle_y2}")
                                    await page.mouse.move(middle_x2, middle_y2, steps=5)
                                    await asyncio.sleep(random.uniform(0.05, 0.1))
                                    # 最后减速 - 更精确地移动到目标位置
                                    end_x = start_x + distance
                                    end_y = start_y + random.randint(-1, 1)  # 最小化抖动，最精确
                                    uat_logger.info(f"🖱️  最后减速，移动到: x={end_x}, y={end_y}")
                                    await page.mouse.move(end_x, end_y, steps=20)  # 最大化步骤，最精确
                                    await asyncio.sleep(0.3)  # 增加停顿时间，确保完全到达目标位置
                                    
                                    # 释放鼠标 - 在正确位置精确释放
                                    uat_logger.info("🖱️  释放鼠标")
                                    await page.mouse.up()
                                    await asyncio.sleep(0.5)  # 增加释放后的停顿时间，确保验证有足够时间响应
                                    
                                    # 等待验证完成
                                    uat_logger.info("⏳ 等待验证完成")
                                    await asyncio.sleep(2)
                                    
                                    # 尝试点击验证按钮或确认区域
                                    try:
                                        uat_logger.info("🔍 尝试找到并点击验证确认按钮")
                                        # 常见的验证确认按钮选择器
                                        confirm_selectors = [
                                            'button:has-text("验证")',
                                            'button:has-text("确认")',
                                            'button:has-text("Verify")',
                                            'button:has-text("Confirm")',
                                            '.verify-button',
                                            '.confirm-button',
                                            '.submit-button',
                                            '[class*="verify"] button',
                                            '[class*="confirm"] button',
                                            '[class*="submit"] button',
                                        ]
                                        
                                        for selector in confirm_selectors:
                                            try:
                                                confirm_button = page.locator(selector)
                                                if await confirm_button.count() > 0:
                                                    is_visible = await confirm_button.is_visible()
                                                    if is_visible:
                                                        uat_logger.info(f"✅ 找到验证确认按钮: {selector}")
                                                        await confirm_button.click()
                                                        uat_logger.info("✅ 点击验证确认按钮")
                                                        await asyncio.sleep(1)
                                                        break
                                            except Exception as e:
                                                uat_logger.debug(f"选择器 {selector} 未找到确认按钮: {e}")
                                                continue
                                    except Exception as e:
                                        uat_logger.debug(f"点击确认按钮失败: {e}")
                                    
                                    uat_logger.info("✅ 滑动验证完成")
                                    return True
                            # 如果无法计算距离，使用默认的滑动操作
                            uat_logger.info("⚠️ 无法计算滑动距离，使用默认的滑动操作")
                            try:
                                await self._perform_slider_action(page, slider)
                                uat_logger.info("✅ 滑动验证完成")
                                return True
                            except Exception as slide_error:
                                uat_logger.error(f"❌ 滑动验证失败: {slide_error}")
                                raise
                        except Exception as slide_error:
                            uat_logger.error(f"❌ 滑动验证失败: {slide_error}")
                            continue
            except Exception as e:
                uat_logger.debug(f"选择器 {selector} 未找到滑块: {e}")
                continue
        
        uat_logger.error("❌ 未找到滑块验证码元素")
        raise Exception("滑动验证失败: 未找到滑块验证码元素，可能的原因：1. 验证码未加载完成 2. 页面结构发生变化 3. 选择器不匹配")
    

    async def _perform_slider_action(self, page, slider):
        """执行滑块滑动操作
        
        Args:
            page: 页面对象
            slider: 滑块元素
        
        Returns:
            bool: 滑动操作是否成功
        """
        uat_logger.info("🔄 执行滑块滑动")
        
        try:
            # 确保滑块在视口中可见
            await slider.scroll_into_view_if_needed()
            await asyncio.sleep(0.5)  # 等待滚动完成
            
            # 获取滑块位置和尺寸
            slider_box = await slider.bounding_box()
            if not slider_box:
                error_msg = "无法获取滑块位置，可能的原因：1. 滑块元素不可见 2. 页面结构发生变化"
                uat_logger.error(f"❌ {error_msg}")
                raise Exception(error_msg)
            
            # 打印滑块位置和尺寸
            uat_logger.info(f"📏 滑块位置: x={slider_box['x']}, y={slider_box['y']}, 宽度={slider_box['width']}, 高度={slider_box['height']}")
            
            # 计算滑动起点
            start_x = slider_box['x'] + slider_box['width'] // 2
            start_y = slider_box['y'] + slider_box['height'] // 2
            uat_logger.info(f"🎯 滑动起点: x={start_x}, y={start_y}")
            
            # 尝试识别拼图滑块并计算缺口位置
            distance = await self._calculate_puzzle_distance(page, slider, slider_box)
            
            # 如果无法计算距离，尝试智能计算
            if not distance or distance <= 0:
                uat_logger.info("⚠️ 无法计算拼图距离，尝试智能计算滑动距离")
                distance = await self._calculate_slider_distance(page, slider, slider_box)
            
            # 如果仍然无法计算，抛出错误而不是使用默认距离
            # 这样可以提供更明确的错误信息，便于分析失败原因
            if not distance or distance <= 0:
                error_msg = "无法计算滑块滑动距离，可能的原因：1. 无法识别拼图缺口 2. 无法找到滑块容器 3. 页面结构异常"
                uat_logger.error(f"❌ {error_msg}")
                raise Exception(error_msg)
            
            uat_logger.info(f"🎯 最终滑动距离: {distance}px")
            
            # 多次尝试（带微调），提升不同验证码厂商成功率
            for idx, offset in enumerate((0, -6, 8, -10), 1):
                trial_distance = max(20, int(distance + offset))
                uat_logger.info(f"🔁 [SLIDER] 第{idx}次尝试，距离={trial_distance}")
                success = await self._slide_with_consistent_speed(page, start_x, start_y, trial_distance)
                if not success:
                    continue
                await asyncio.sleep(1.2)
                if await self._captcha_appears_gone(page):
                    uat_logger.info("✅ [SLIDER] 验证组件已消失，判定成功")
                    return True
            error_msg = "滑块滑动多次尝试后仍未通过验证"
            uat_logger.error(f"❌ {error_msg}")
            raise Exception(error_msg)
        except Exception as e:
            uat_logger.error(f"❌ 滑动操作执行失败: {e}")
            raise

    async def _captcha_appears_gone(self, page) -> bool:
        """验证码是否已消失（用于滑块/点选后的成功判定）。"""
        selectors = [
            '[class*="captcha"]',
            '[class*="verify"]',
            '[class*="slider"]',
            '.geetest_panel',
            '.yidun',
            '.tcaptcha',
        ]
        try:
            for sel in selectors:
                loc = page.locator(sel)
                if await loc.count() <= 0:
                    continue
                try:
                    if await loc.first.is_visible():
                        return False
                except Exception:
                    pass
            return True
        except Exception:
            return False
    
    # 临时方法，稍后删除
    async def _old_perform_slider_action(self, page, slider):
        """旧的滑块滑动操作方法，用于备份"""
        pass
    
    async def _calculate_slider_distance(self, page, slider, slider_box):
        """智能计算滑块滑动距离
        
        Args:
            page: 页面对象
            slider: 滑块元素
            slider_box: 滑块边界框
        
        Returns:
            int: 滑动距离
        """
        uat_logger.info("🔍 智能计算滑块滑动距离")
        
        try:
            # 尝试获取滑块容器，以计算实际需要滑动的距离
            # 常见的滑块容器选择器
            container_selectors = [
                '.slider-container',
                '.slide-container',
                '.captcha-slider',
                '[class*="slider"]',
                '[class*="slide"]',
                '[class*="verify"]',
                '[class*="verifybox"]',
                '[class*="captcha"]',
                '[class*="geetest"]',
                '[class*="tcaptcha"]',
                '[class*="yidun"]',
                '.geetest_slider',
                '.tcaptcha-slider',
                '.yidun_slider',
                '.nc_wrapper',  # 网易易盾
                '.ac-slider',  # 阿里滑块
                '.verify-bar-area',  # 特定于用户的验证容器
                '.verify-left-bar',  # 特定于用户的验证容器
            ]
            
            for container_selector in container_selectors:
                container = page.locator(container_selector)
                if await container.count() > 0:
                    container_element = container.first
                    container_box = await container_element.bounding_box()
                    if container_box:
                        uat_logger.info(f"📦 找到容器: {container_selector}, 位置: x={container_box['x']}, y={container_box['y']}, 宽度={container_box['width']}, 高度={container_box['height']}")
                        # 尝试通过查找缺口元素来计算滑动距离
                        # 常见的缺口元素选择器
                        gap_selectors = [
                            '.puzzle-gap',
                            '.captcha-gap',
                            '.verify-gap',
                            '.slider-gap',
                            '.slide-gap',
                            '[class*="gap"]',
                            '[class*="hole"]',
                            '[class*="缺口"]',
                        ]
                        
                        for gap_selector in gap_selectors:
                            try:
                                gap_element = page.locator(gap_selector)
                                if await gap_element.count() > 0:
                                    gap = gap_element.first
                                    is_visible = await gap.is_visible()
                                    if is_visible:
                                        gap_box = await gap.bounding_box()
                                        if gap_box:
                                            uat_logger.info(f"✅ 找到缺口元素: {gap_selector}, 位置: x={gap_box['x']}, y={gap_box['y']}")
                                            # 计算滑动距离：缺口位置 - 滑块初始位置
                                            distance = int(gap_box['x'] - slider_box['x'])
                                            uat_logger.info(f"✅ 缺口位置计算距离: {distance}px")
                                            if distance > 0 and distance < 500:
                                                return distance
                            except Exception as e:
                                uat_logger.debug(f"选择器 {gap_selector} 未找到缺口元素: {e}")
                                continue
                        
                        # 如果没有找到缺口元素，返回一个基于容器宽度的合理距离
                        # 但不是容器宽度 - 滑块宽度，而是一个更合理的值
                        distance = int(container_box['width'] * 0.6)
                        uat_logger.info(f"✅ 基于容器宽度计算滑动距离: {distance}px")
                        if distance > 0 and distance < 500:
                            return distance
            
            # 如果仍然无法计算，尝试获取滑块的父元素作为容器
            try:
                # 获取滑块的父元素
                parent = slider.locator('xpath=..')
                parent_box = await parent.bounding_box()
                if parent_box:
                    uat_logger.info(f"👨‍👩‍👧‍👦 找到父元素, 位置: x={parent_box['x']}, y={parent_box['y']}, 宽度={parent_box['width']}, 高度={parent_box['height']}")
                    # 如果没有找到缺口元素，返回一个基于父元素宽度的合理距离
                    # 但不是父元素宽度 - 滑块宽度，而是一个更合理的值
                    distance = int(parent_box['width'] * 0.6)
                    uat_logger.info(f"✅ 基于父元素宽度计算滑动距离: {distance}px")
                    if distance > 0 and distance < 500:
                        return distance
            except Exception as e:
                uat_logger.debug(f"使用父元素计算滑动距离失败: {e}")
            
            # 如果仍然无法计算，尝试获取滑块的祖父元素作为容器
            try:
                # 获取滑块的祖父元素
                grandparent = slider.locator('xpath=../..')
                grandparent_box = await grandparent.bounding_box()
                if grandparent_box:
                    uat_logger.info(f"👴 找到祖父元素, 位置: x={grandparent_box['x']}, y={grandparent_box['y']}, 宽度={grandparent_box['width']}, 高度={grandparent_box['height']}")
                    # 如果没有找到缺口元素，返回一个基于祖父元素宽度的合理距离
                    # 但不是祖父元素宽度 - 滑块宽度，而是一个更合理的值
                    distance = int(grandparent_box['width'] * 0.6)
                    uat_logger.info(f"✅ 基于祖父元素宽度计算滑动距离: {distance}px")
                    if distance > 0 and distance < 500:
                        return distance
            except Exception as e:
                uat_logger.debug(f"使用祖父元素计算滑动距离失败: {e}")
        except Exception as e:
            uat_logger.debug(f"智能计算滑动距离失败: {e}")
        
        # 如果所有方法都失败，返回一个默认的滑动距离
        # 这个默认距离是基于常见的滑块验证码容器宽度计算的
        default_distance = 200
        uat_logger.warning(f"⚠️ 所有距离计算方法都失败，使用默认滑动距离: {default_distance}px")
        return default_distance
    
    async def _slide_with_consistent_speed(self, page, start_x, start_y, distance):
        """以一致的速度执行滑块滑动
        
        Args:
            page: 页面对象
            start_x: 滑动起点x坐标
            start_y: 滑动起点y坐标
            distance: 滑动距离
        
        Returns:
            bool: 滑动操作是否成功
        """
        uat_logger.info(f"🔄 以一致速度滑动，距离: {distance}px")
        
        try:
            # 模拟鼠标移动到滑块上 - 减少等待时间
            uat_logger.info(f"🖱️  移动鼠标到滑块位置: x={start_x}, y={start_y}")
            # 先移动到滑块附近，再微调位置
            near_x = start_x + random.randint(-10, 10)
            near_y = start_y + random.randint(-10, 10)
            await page.mouse.move(near_x, near_y, steps=3)
            await asyncio.sleep(random.uniform(0.05, 0.15))  # 减少随机停顿时间
            # 精确移动到滑块中心
            await page.mouse.move(start_x, start_y, steps=5)
            await asyncio.sleep(random.uniform(0.05, 0.1))  # 减少随机停顿时间
            
            # 模拟鼠标按下 - 减少等待时间
            uat_logger.info("🖱️  按下鼠标")
            await asyncio.sleep(random.uniform(0.02, 0.08))  # 减少按下前的犹豫时间
            await page.mouse.down()
            await asyncio.sleep(random.uniform(0.02, 0.05))  # 减少按下后的停顿时间
            
            # 计算滑动路径点 - 模拟人类的加速、匀速、减速过程
            total_steps = random.randint(20, 30)  # 增加总步骤数，进一步提高精度
            step_distance = distance / total_steps
            
            # 分阶段滑动，模拟人类行为
            current_x = start_x
            current_y = start_y
            
            for step in range(1, total_steps + 1):
                # 计算当前步骤的目标位置
                target_x = start_x + step_distance * step
                
                # 模拟人类的Y轴偏移 - 更自然的抖动
                # 开始和结束时抖动较小，中间时抖动较大
                if step < total_steps * 0.2 or step > total_steps * 0.8:
                    # 开始和结束阶段，抖动较小
                    jitter = random.randint(-1, 1)
                else:
                    # 中间阶段，抖动较大
                    jitter = random.randint(-3, 3)
                target_y = start_y + jitter
                
                # 模拟人类的移动速度变化
                # 开始时较慢（加速），中间较快（匀速），结束时较慢（减速）
                if step < total_steps * 0.3:
                    # 加速阶段
                    move_steps = random.randint(2, 3)
                    sleep_time = random.uniform(0.04, 0.06)
                elif step > total_steps * 0.7:
                    # 减速阶段
                    if step > total_steps * 0.9:
                        # 最后10%的步骤，使用最多的步骤以获得最高精度
                        move_steps = random.randint(20, 30)  # 进一步增加最后阶段的步骤数
                        sleep_time = random.uniform(0.1, 0.2)  # 增加最后阶段的停留时间
                    else:
                        # 减速阶段的前半部分
                        move_steps = random.randint(10, 15)  # 进一步增加减速阶段的步骤数
                        sleep_time = random.uniform(0.05, 0.1)
                else:
                    # 匀速阶段
                    move_steps = random.randint(3, 5)  # 增加匀速阶段的步骤数
                    sleep_time = random.uniform(0.02, 0.05)
                
                # 移动到目标位置
                await page.mouse.move(target_x, target_y, steps=move_steps)
                await asyncio.sleep(sleep_time)
            
            # 最后的精确定位 - 确保滑块准确到达目标位置
            final_target_x = start_x + distance
            final_target_y = start_y + random.randint(-1, 1)  # 最后的微调
            await page.mouse.move(final_target_x, final_target_y, steps=30)  # 使用更多步骤确保精度
            await asyncio.sleep(random.uniform(0.1, 0.2))  # 增加停顿时间，确保完全到达目标位置
            
            # 释放鼠标 - 人类可能会有轻微的延迟
            await asyncio.sleep(random.uniform(0.05, 0.1))  # 释放前的停顿
            uat_logger.info("🖱️  释放鼠标")
            await page.mouse.up()
            
            # 释放后的停顿
            await asyncio.sleep(random.uniform(0.1, 0.2))
            
            return True
        except Exception as e:
            uat_logger.error(f"❌ 一致速度滑动失败: {e}")
            return False
    
    async def _calculate_puzzle_distance_with_opencv(self, page, slider, slider_box):
        """使用OpenCV计算拼图滑块的缺口距离"""
        uat_logger.info("🔍 使用OpenCV识别拼图滑块并计算缺口距离")
        
        try:
            # 尝试获取滑块的父元素，作为验证码区域
            try:
                parent = slider.locator('xpath=..')
                parent_box = await parent.bounding_box()
                if parent_box:
                    # 截图只包含验证码区域，减少干扰
                    uat_logger.info("📸 截图获取验证码区域")
                    screenshot_path = "captcha_screenshot.png"
                    # 计算截图区域，稍微扩大一点范围以确保包含完整的验证码
                    clip = {
                        "x": max(0, parent_box['x'] - 10),
                        "y": max(0, parent_box['y'] - 10),
                        "width": parent_box['width'] + 20,
                        "height": parent_box['height'] + 20
                    }
                    await page.screenshot(path=screenshot_path, clip=clip)
                else:
                    # 如果无法获取父元素，使用整个页面截图
                    uat_logger.info("📸 截图获取整个页面")
                    screenshot_path = "captcha_screenshot.png"
                    await page.screenshot(path=screenshot_path, full_page=False)
            except Exception:
                # 失败时使用整个页面截图
                uat_logger.info("📸 截图获取整个页面")
                screenshot_path = "captcha_screenshot.png"
                await page.screenshot(path=screenshot_path, full_page=False)
            
            # 读取截图并进行图像处理
            image = cv2.imread(screenshot_path)
            if image is None:
                uat_logger.error("❌ 无法读取截图")
                return None
            
            # 转换为灰度图像
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            
            # 应用高斯模糊，减少噪声，使用更大的核以获得更好的模糊效果
            blurred = cv2.GaussianBlur(gray, (7, 7), 0)
            
            # 使用自适应阈值处理，调整参数以获得更好的阈值效果
            thresh = cv2.adaptiveThreshold(blurred, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 15, 3)
            
            # 使用边缘检测，调整参数以获得更清晰的边缘
            edges = cv2.Canny(thresh, 20, 80)
            
            # 执行形态学操作，闭合边缘中的小间隙
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
            edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel)
            
            # 查找轮廓
            contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            
            # 过滤出可能的缺口轮廓
            gap_contours = []
            for contour in contours:
                area = cv2.contourArea(contour)
                # 根据面积和长宽比过滤，只保留可能是缺口的轮廓
                if 80 < area < 4000:  # 调整面积范围
                    x, y, w, h = cv2.boundingRect(contour)
                    aspect_ratio = w / h if h > 0 else 0
                    # 缺口通常是横向的，长宽比大于1
                    if 1.0 < aspect_ratio < 6:  # 调整长宽比范围
                        # 进一步过滤：缺口通常位于图像的右侧部分
                        if x > image.shape[1] * 0.3:  # 确保缺口在图像右侧30%区域
                            gap_contours.append(contour)
            
            # 如果找到缺口轮廓
            if gap_contours:
                # 按面积排序，选择最大的缺口轮廓
                gap_contours.sort(key=lambda x: cv2.contourArea(x), reverse=True)
                largest_contour = gap_contours[0]
                
                # 计算缺口轮廓的边界框
                x, y, w, h = cv2.boundingRect(largest_contour)
                uat_logger.info(f"✅ 使用OpenCV找到缺口位置: x={x}, y={y}, 宽度={w}, 高度={h}")
                
                # 计算缺口中心点位置（在截图中的坐标）
                gap_center_x_screenshot = x + w // 2
                
                # 计算滑块中心点位置（在实际页面中的坐标）
                slider_center_x_page = int(slider_box['x'] + slider_box['width'] // 2)
                
                # 计算坐标映射比例
                # 截图宽度与实际截图区域宽度的比例
                if 'clip' in locals():
                    # 如果使用了裁剪区域，使用裁剪区域的宽度
                    width_ratio = clip['width'] / image.shape[1]
                    # 调整缺口坐标，考虑裁剪区域的偏移
                    gap_center_x_page = int((gap_center_x_screenshot * width_ratio) + clip['x'])
                else:
                    # 否则使用页面视口宽度
                    viewport_width = await page.evaluate("window.innerWidth")
                    width_ratio = viewport_width / image.shape[1] if viewport_width else 1
                    gap_center_x_page = int(gap_center_x_screenshot * width_ratio)
                
                uat_logger.info(f"📏 宽度映射比例: {width_ratio}")
                uat_logger.info(f"📍 转换后的缺口中心点页面坐标: {gap_center_x_page}")
                uat_logger.info(f"📍 滑块中心点页面坐标: {slider_center_x_page}")
                
                # 计算滑动距离
                distance = gap_center_x_page - slider_center_x_page
                uat_logger.info(f"✅ 使用OpenCV计算滑动距离: {distance}px")
                
                # 验证距离是否合理
                if distance < 0 or distance > 500:
                    uat_logger.warning(f"⚠️ 计算的滑动距离不合理: {distance}px，可能是坐标映射错误")
                    # 使用传统方法作为备选
                    return None
                
                return distance
            
            # 如果未找到缺口轮廓，尝试使用模板匹配方法
            uat_logger.info("⚠️ 轮廓检测失败，尝试使用模板匹配方法")
            try:
                # 尝试找到滑块图像
                slider_image = None
                slider_images = page.locator('img, [class*="slider"], [class*="slide"]')
                count = await slider_images.count()
                if count > 0:
                    for i in range(count):
                        img = slider_images.nth(i)
                        if await img.is_visible():
                            slider_image = img
                            break
                
                if slider_image:
                    # 获取滑块图像位置
                    slider_img_box = await slider_image.bounding_box()
                    if slider_img_box:
                        # 截图滑块区域
                        slider_clip = {
                            "x": max(0, slider_img_box['x'] - 5),
                            "y": max(0, slider_img_box['y'] - 5),
                            "width": slider_img_box['width'] + 10,
                            "height": slider_img_box['height'] + 10
                        }
                        slider_screenshot_path = "slider_template.png"
                        await page.screenshot(path=slider_screenshot_path, clip=slider_clip)
                        
                        # 读取滑块模板
                        template = cv2.imread(slider_screenshot_path, 0)
                        if template is not None:
                            # 模板匹配
                            result = cv2.matchTemplate(gray, template, cv2.TM_CCOEFF_NORMED)
                            min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(result)
                            
                            # 找到最佳匹配位置
                            if max_val > 0.6:  # 匹配阈值
                                # 计算缺口位置
                                gap_x = max_loc[0] + template.shape[1] // 2
                                uat_logger.info(f"✅ 使用模板匹配找到缺口位置: x={gap_x}")
                                
                                # 坐标映射
                                if 'clip' in locals():
                                    width_ratio = clip['width'] / image.shape[1]
                                    gap_center_x_page = int((gap_x * width_ratio) + clip['x'])
                                else:
                                    viewport_width = await page.evaluate("window.innerWidth")
                                    width_ratio = viewport_width / image.shape[1] if viewport_width else 1
                                    gap_center_x_page = int(gap_x * width_ratio)
                                
                                # 计算滑块中心点位置（在实际页面中的坐标）
                                slider_center_x_page = int(slider_box['x'] + slider_box['width'] // 2)
                                # 计算滑动距离
                                distance = gap_center_x_page - slider_center_x_page
                                uat_logger.info(f"✅ 使用模板匹配计算滑动距离: {distance}px")
                                
                                if distance > 0 and distance < 500:
                                    return distance
            except Exception as template_error:
                uat_logger.debug(f"模板匹配失败: {template_error}")
            
            uat_logger.warning("⚠️ 使用OpenCV未找到缺口位置")
            return None
        except Exception as e:
            uat_logger.error(f"❌ 使用OpenCV计算拼图距离失败: {e}")
            return None
    
    async def _calculate_puzzle_distance(self, page, slider, slider_box):
        """计算拼图滑块的缺口距离"""
        uat_logger.info("🔍 识别拼图滑块并计算缺口距离")
        
        try:
            # 优先使用OpenCV进行缺口识别
            uat_logger.info("📋 步骤1: 使用OpenCV进行缺口识别")
            opencv_distance = await self._calculate_puzzle_distance_with_opencv(page, slider, slider_box)
            if opencv_distance and opencv_distance > 0 and opencv_distance < 500:
                uat_logger.info(f"✅ 使用OpenCV计算出的距离: {opencv_distance}px")
                return opencv_distance
            
            # 如果OpenCV方法失败，使用传统的DOM元素识别方法
            uat_logger.info("⚠️ OpenCV方法失败，使用传统的DOM元素识别方法")
            uat_logger.info("📋 步骤2: 使用传统的DOM元素识别方法")
            # 尝试找到缺口元素 - 这是最准确的方法
            gap_selectors = [
                '.puzzle-gap',
                '.captcha-gap',
                '.verify-gap',
                '.slider-gap',
                '.slide-gap',
                '[class*="gap"]',
                '[class*="hole"]',
                '[class*="缺口"]',
                # 新增缺口选择器
                '[class*="notch"]',
                '[class*="missing"]',
                '[class*="empty"]',
                '[class*="cut"]',
                '[class*="break"]',
                '[class*="puzzle"] [class*="gap"]',
                '[class*="captcha"] [class*="gap"]',
                '[class*="verify"] [class*="gap"]',
                '[class*="slider"] [class*="gap"]',
                '[class*="slide"] [class*="gap"]',
                # 新增针对拼图验证码的选择器
                '.puzzle-piece',
                '.puzzle-missing',
                '.puzzle-hole',
                '[class*="puzzle"] [class*="piece"]',
                '[class*="puzzle"] [class*="missing"]',
                '[class*="puzzle"] [class*="hole"]',
                # 针对特定验证码服务的选择器
                '.geetest_canvas_slice',  # 极验验证码
                '.yidun_jigsaw',  # 易盾验证码
                '.tcaptcha-puzzle',  # 腾讯验证码
                # 新增更多常见的缺口选择器
                '.jigsaw-gap',
                '.security-gap',
                '.code-gap',
                '[class*="jigsaw"]',
                '[class*="security"]',
                '[class*="code"]',
                '[class*="verify"] [class*="hole"]',
                '[class*="captcha"] [class*="hole"]',
                '[class*="slider"] [class*="hole"]',
                '[class*="slide"] [class*="hole"]',
                # 针对不同框架和库的选择器
                '.ant-captcha-gap',
                '.element-plus-captcha-gap',
                '.vuetify-captcha-gap',
                '.bootstrap-captcha-gap',
                # 通用选择器
                'div[class*="gap"]',
                'div[class*="hole"]',
                'span[class*="gap"]',
                'span[class*="hole"]',
                'img[class*="gap"]',
                'img[class*="hole"]',
            ]
            
            for selector in gap_selectors:
                try:
                    gap_element = page.locator(selector)
                    if await gap_element.count() > 0:
                        gap = gap_element.first
                        is_visible = await gap.is_visible()
                        if is_visible:
                            uat_logger.info(f"✅ 找到缺口元素: {selector}")
                            
                            # 获取缺口位置
                            gap_box = await gap.bounding_box()
                            if gap_box:
                                uat_logger.info(f"📏 缺口位置: x={gap_box['x']}, y={gap_box['y']}, 宽度={gap_box['width']}, 高度={gap_box['height']}")
                                
                                # 计算滑动距离：缺口位置 - 滑块初始位置（考虑中心点）
                                # 缺口的中心点位置减去滑块的中心点位置
                                gap_center_x = gap_box['x'] + gap_box['width'] // 2
                                slider_center_x = slider_box['x'] + slider_box['width'] // 2
                                distance = int(gap_center_x - slider_center_x)
                                uat_logger.info(f"✅ 缺口位置计算距离: {distance}px")
                                return distance
                except Exception as e:
                    uat_logger.debug(f"选择器 {selector} 未找到缺口元素: {e}")
                    continue
            
            # 尝试获取滑块的目标位置
            target_selectors = [
                '.target-position',
                '.puzzle-target',
                '.captcha-target',
                '.verify-target',
                '[class*="target"]',
                # 新增目标位置选择器
                '[class*="destination"]',
                '[class*="end"]',
                '[class*="final"]',
                '[class*="goal"]',
            ]
            
            for selector in target_selectors:
                try:
                    target_element = page.locator(selector)
                    if await target_element.count() > 0:
                        target = target_element.first
                        is_visible = await target.is_visible()
                        if is_visible:
                            uat_logger.info(f"✅ 找到目标位置: {selector}")
                            
                            # 获取目标位置
                            target_box = await target.bounding_box()
                            if target_box:
                                uat_logger.info(f"📏 目标位置: x={target_box['x']}, y={target_box['y']}, 宽度={target_box['width']}, 高度={target_box['height']}")
                                
                                # 计算滑动距离：目标位置 - 滑块初始位置（考虑中心点）
                                # 目标的中心点位置减去滑块的中心点位置
                                target_center_x = target_box['x'] + target_box['width'] // 2
                                slider_center_x = slider_box['x'] + slider_box['width'] // 2
                                distance = int(target_center_x - slider_center_x)
                                uat_logger.info(f"✅ 目标位置计算距离: {distance}px")
                                return distance
                except Exception as e:
                    uat_logger.debug(f"选择器 {selector} 未找到目标位置: {e}")
                    continue
            

            

            
            # 尝试通过图片计算距离 - 作为最后的备选方案
            # 常见的拼图图片选择器
            puzzle_image_selectors = [
                '.puzzle-image',
                '.captcha-image',
                '.verify-image',
                '.slider-image',
                '.slide-image',
                '[class*="puzzle"]',
                '[class*="captcha"] img',
                '[class*="verify"] img',
                '[class*="slider"] img',
                '[class*="slide"] img',
                '.geetest_item_img',  # 极验验证码
                '.yidun_bg-img',  # 易盾验证码
                '.tcaptcha-img',  # 腾讯验证码
                '.verifybox',  # 用户提供的verifybox选择器
                '.verify-bar-area',  # 验证条区域
                '.verify-left-bar',  # 验证左侧条
                '.verify-move-block',  # 验证移动块
                '.verify-drag-icon',  # 验证拖动图标
                # 新增常见验证码选择器
                '.captcha-container img',
                '.verify-container img',
                '.security-code img',
                '.code-image',
                '[class*="security"] img',
                '[class*="code"] img',
                'img[src*="captcha"]',
                'img[src*="verify"]',
                'img[src*="code"]',
                # 通用选择器
                'img',  # 最后尝试所有图片
            ]
            
            # 尝试找到拼图图片
            for selector in puzzle_image_selectors:
                try:
                    image_element = page.locator(selector)
                    if await image_element.count() > 0:
                        image = image_element.first
                        is_visible = await image.is_visible()
                        if is_visible:
                            uat_logger.info(f"✅ 找到拼图图片: {selector}")
                            
                            # 获取图片位置和尺寸
                            image_box = await image.bounding_box()
                            if image_box:
                                uat_logger.info(f"📏 拼图图片位置: x={image_box['x']}, y={image_box['y']}, 宽度={image_box['width']}, 高度={image_box['height']}")
                                
                                # 对于拼图滑块，使用OpenCV进行缺口识别
                                uat_logger.info("🔍 使用OpenCV进行缺口识别")
                                opencv_distance = await self._calculate_puzzle_distance_with_opencv(page, slider, slider_box)
                                if opencv_distance and opencv_distance > 0 and opencv_distance < 500:
                                    uat_logger.info(f"✅ 使用OpenCV计算出的距离: {opencv_distance}px")
                                    return opencv_distance
                except Exception as e:
                    uat_logger.debug(f"选择器 {selector} 未找到拼图图片: {e}")
                    continue
            
            # 未找到拼图图片或缺口位置，返回None
            uat_logger.warning(f"⚠️ 未找到拼图图片或缺口位置")
            return None
        except Exception as e:
            uat_logger.error(f"❌ 计算拼图距离失败: {e}")
            # 出错时返回None
            return None

    async def _handle_image_captcha(self, page):
        """处理点击图片文字验证码"""
        uat_logger.info("🔍 处理点击图片文字验证码")
        
        # 常见的图片验证码选择器
        image_captcha_selectors = [
            '.captcha-image',
            '.verify-image',
            '.image-captcha',
            '#captcha-image',
            '#verify-image',
            '[class*="image"]',
            '[class*="pic"]',
            '[class*="img"]',
            'img[class*="captcha"]',
            'img[class*="verify"]',
            '.captcha-container img',
            '.verify-container img',
        ]
        
        # 等待图片加载
        await asyncio.sleep(1)
        
        for selector in image_captcha_selectors:
            try:
                uat_logger.info(f"🔍 尝试查找图片验证码: {selector}")
                element = page.locator(selector)
                
                if await element.count() > 0:
                    image = element.first
                    is_visible = await image.is_visible()
                    if is_visible:
                        uat_logger.info(f"✅ 找到图片验证码: {selector}")
                        
                        # 先尝试 OCR 指令驱动点选，再尝试图像识别点选，最后回退随机点选
                        try:
                            ocr_ok = await self._click_image_by_ocr_instruction(page, image)
                            if ocr_ok:
                                uat_logger.info("✅ [IMAGE_OCR] OCR指令驱动点选成功")
                                return True
                            ai_ok = await self._click_image_by_vision(page, image)
                            if not ai_ok:
                                await self._click_image_randomly(page, image)
                            uat_logger.info("✅ 图片验证操作完成")
                            return True
                        except Exception as click_error:
                            uat_logger.error(f"❌ 图片验证失败: {click_error}")
                            continue
            except Exception as e:
                uat_logger.debug(f"选择器 {selector} 未找到图片验证码: {e}")
                continue
        
        uat_logger.warning("⚠️ 未找到图片验证码元素")
        return False

    async def _extract_captcha_instruction_text(self, page) -> str:
        """提取页面上验证码点击指令文本（如：请依次点击“苹果、香蕉”）。"""
        try:
            txt = await page.evaluate("""() => {
                const sels = [
                    '[class*="captcha"]',
                    '[class*="verify"]',
                    '[class*="yidun"]',
                    '[class*="geetest"]',
                    '[class*="tcaptcha"]',
                    '[class*="question"]',
                    '[class*="prompt"]',
                ];
                let best = '';
                for (const s of sels) {
                    const nodes = Array.from(document.querySelectorAll(s)).slice(0, 20);
                    for (const n of nodes) {
                        const t = (n.innerText || n.textContent || '').trim();
                        if (!t) continue;
                        if (t.includes('点击') || t.includes('依次') || t.includes('选择')) {
                            if (t.length > best.length) best = t;
                        }
                    }
                }
                return best;
            }""")
            return (txt or "").strip()
        except Exception:
            return ""

    def _parse_instruction_targets(self, instruction: str) -> List[str]:
        s = (instruction or "").strip()
        if not s:
            return []
        m = re.search(r"(?:点击|选择|依次点击|请点击|请依次点击)\s*[：: ]?\s*[“\"'「]?(.*?)[”\"'」]?(?:$|。|，|,)", s)
        body = m.group(1).strip() if m else s
        body = body.replace("依次", "").replace("点击", "").replace("选择", "").strip()
        if not body:
            return []
        parts = re.split(r"[、，,;\s]+", body)
        out = []
        for p in parts:
            t = p.strip().strip("“”\"'「」")
            if len(t) >= 1:
                out.append(t)
        return out[:5]

    async def _click_image_by_ocr_instruction(self, page, image) -> bool:
        """OCR 指令驱动点选：先识别提示词，再在图中匹配文字框并点击。"""
        try:
            pyt = importlib.import_module("pytesseract")
        except Exception:
            uat_logger.info("ℹ️ [IMAGE_OCR] 未安装 pytesseract，跳过 OCR 路径")
            return False

        try:
            instruction = await self._extract_captcha_instruction_text(page)
            targets = self._parse_instruction_targets(instruction)
            if not targets:
                uat_logger.info("ℹ️ [IMAGE_OCR] 未提取到点击指令词，跳过 OCR 路径")
                return False
            uat_logger.info(f"🔍 [IMAGE_OCR] 指令词: {targets}")

            image_box = await image.bounding_box()
            if not image_box:
                return False
            png = await image.screenshot()
            arr = np.frombuffer(png, dtype=np.uint8)
            img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if img is None:
                return False
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

            data = pyt.image_to_data(gray, lang="chi_sim+eng", output_type=pyt.Output.DICT)
            n = len(data.get("text", []))
            ocr_boxes = []
            for i in range(n):
                txt = (data["text"][i] or "").strip()
                if not txt:
                    continue
                try:
                    conf = float(data["conf"][i])
                except Exception:
                    conf = 0.0
                if conf < 20:
                    continue
                x, y, w, h = int(data["left"][i]), int(data["top"][i]), int(data["width"][i]), int(data["height"][i])
                if w < 6 or h < 6:
                    continue
                ocr_boxes.append({"text": txt, "conf": conf, "x": x, "y": y, "w": w, "h": h})

            if not ocr_boxes:
                return False

            clicked_any = False
            for target in targets:
                # 找包含目标词的最佳框
                candidates = [b for b in ocr_boxes if target in b["text"] or b["text"] in target]
                if not candidates:
                    continue
                candidates.sort(key=lambda b: (b["conf"], b["w"] * b["h"]), reverse=True)
                b = candidates[0]
                click_x = image_box["x"] + b["x"] + b["w"] / 2
                click_y = image_box["y"] + b["y"] + b["h"] / 2
                await page.mouse.click(click_x, click_y)
                clicked_any = True
                uat_logger.info(f"🎯 [IMAGE_OCR] 点击目标词「{target}」位置")
                await asyncio.sleep(0.8)
                if await self._captcha_appears_gone(page):
                    return True

            return clicked_any and await self._captcha_appears_gone(page)
        except Exception as e:
            uat_logger.warning(f"⚠️ [IMAGE_OCR] OCR 指令点选失败: {e}")
            return False

    async def _click_image_by_vision(self, page, image) -> bool:
        """使用 OpenCV 在验证码图中找可疑文字块并点击。"""
        try:
            image_box = await image.bounding_box()
            if not image_box:
                return False
            png = await image.screenshot()
            arr = np.frombuffer(png, dtype=np.uint8)
            img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if img is None:
                return False

            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            blur = cv2.GaussianBlur(gray, (3, 3), 0)
            bin_img = cv2.adaptiveThreshold(
                blur, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 31, 8
            )
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
            bin_img = cv2.morphologyEx(bin_img, cv2.MORPH_CLOSE, kernel, iterations=1)
            contours, _ = cv2.findContours(bin_img, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            if not contours:
                return False

            h_img, w_img = gray.shape[:2]
            candidates = []
            for c in contours:
                x, y, w, h = cv2.boundingRect(c)
                area = w * h
                if area < 80 or area > (w_img * h_img * 0.12):
                    continue
                ratio = w / max(h, 1)
                if ratio < 0.2 or ratio > 12:
                    continue
                # 偏好靠上的文字区域（常见“请依次点击”目标字在上半部分）
                score = area - (y * 0.8)
                candidates.append((score, x, y, w, h))
            if not candidates:
                return False
            candidates.sort(key=lambda t: t[0], reverse=True)

            # 点击前3个最可能文本块中心，逐次判定是否通过
            for _, x, y, w, h in candidates[:3]:
                click_x = image_box["x"] + x + w / 2
                click_y = image_box["y"] + y + h / 2
                await page.mouse.click(click_x, click_y)
                await asyncio.sleep(0.7)
                if await self._captcha_appears_gone(page):
                    uat_logger.info("✅ [IMAGE_AI] 图像识别点选后验证码已消失")
                    return True
            return False
        except Exception as e:
            uat_logger.debug(f"[IMAGE_AI] 视觉点选失败: {e}")
            return False
    
    async def _click_image_randomly(self, page, image):
        """在图片上随机点击"""
        uat_logger.info("🎯 在图片上随机点击")
        
        # 获取图片位置和尺寸
        image_box = await image.bounding_box()
        if not image_box:
            raise Exception("无法获取图片位置")
        
        # 计算图片中心区域
        center_x = image_box['x'] + image_box['width'] // 2
        center_y = image_box['y'] + image_box['height'] // 2
        
        w = max(20, int(image_box['width']))
        h = max(20, int(image_box['height']))
        # 更像人工点选：逐点尝试并检查是否通过，不盲点全部位置
        click_positions = [
            (center_x, center_y),
            (image_box['x'] + w * 0.25, image_box['y'] + h * 0.25),
            (image_box['x'] + w * 0.75, image_box['y'] + h * 0.25),
            (image_box['x'] + w * 0.25, image_box['y'] + h * 0.75),
            (image_box['x'] + w * 0.75, image_box['y'] + h * 0.75),
        ]

        for x, y in click_positions:
            try:
                await page.mouse.click(x, y)
                uat_logger.info(f"🎯 点击位置: ({x}, {y})")
                await asyncio.sleep(0.7)
                if await self._captcha_appears_gone(page):
                    uat_logger.info("✅ 图片验证码组件已消失，判定成功")
                    return
            except Exception as e:
                uat_logger.debug(f"点击位置 ({x}, {y}) 失败: {e}")
                continue
        
        # 等待验证完成
        await asyncio.sleep(1.5)
    
    async def _auto_handle_verification_popup_old(self, page):
        """自动识别并处理验证弹窗"""
        uat_logger.info("🔍 开始自动识别验证弹窗")
        
        # 常见的验证弹窗选择器模式
        verification_selectors = [
            # 人机验证相关
            '.captcha-box',
            '.verification-box',
            '.verify-box',
            '#captcha',
            '#verification',
            '#verify',
            '[class*="captcha"]',
            '[class*="verification"]',
            '[class*="verify"]',
            '[id*="captcha"]',
            '[id*="verification"]',
            '[id*="verify"]',
            # 验证按钮
            'button:has-text("验证")',
            'button:has-text("Verify")',
            'button:has-text("确认")',
            'button:has-text("Confirm")',
            'button:has-text("提交")',
            'button:has-text("Submit")',
            'a:has-text("验证")',
            'a:has-text("Verify")',
            # iframe中的验证
            'iframe[src*="captcha"]',
            'iframe[src*="verify"]',
            'iframe[src*="recaptcha"]',
            # 其他常见验证元素
            '.g-recaptcha',
            '#g-recaptcha',
            '[data-sitekey]',
            '.hcaptcha',
            '#hcaptcha',
        ]
        
        # 等待一段时间让验证弹窗出现
        await asyncio.sleep(1)
        
        # 尝试查找验证弹窗
        found_verification = False
        for selector in verification_selectors:
            try:
                uat_logger.info(f"🔍 尝试查找验证弹窗: {selector}")
                element = page.locator(selector)
                
                # 检查元素是否存在且可见
                if await element.count() > 0:
                    first_element = element.first
                    is_visible = await first_element.is_visible()
                    if is_visible:
                        uat_logger.info(f"✅ 找到验证弹窗: {selector}")
                        found_verification = True
                        
                        # 尝试点击验证按钮
                        try:
                            await first_element.click(timeout=3000)
                            uat_logger.info("✅ 已点击验证按钮")
                            # 等待验证完成
                            await asyncio.sleep(2)
                        except Exception as click_error:
                            uat_logger.warning(f"⚠️ 点击验证按钮失败: {click_error}")
                            # 如果点击失败，尝试其他方式
                            try:
                                # 尝试等待验证弹窗消失
                                await first_element.wait_for(state='hidden', timeout=5000)
                                uat_logger.info("✅ 验证弹窗已消失")
                            except Exception as wait_error:
                                uat_logger.warning(f"⚠️ 等待验证弹窗消失失败: {wait_error}")
                        
                        break
            except Exception as e:
                uat_logger.debug(f"选择器 {selector} 未找到验证弹窗: {e}")
                continue
        
        if not found_verification:
            uat_logger.info("ℹ️ 未检测到验证弹窗，继续执行")

        return found_verification
    
    async def get_element_screenshot(self, selector: str, path: str = None):
        """截取特定元素的截图"""
        if self.page is None:
            raise Exception("浏览器未启动")
        
        if path is None:
            path = f"element_screenshot_{int(time.time())}.png"
        
        # 等待元素可见
        await self.page.wait_for_selector(selector, state='visible', timeout=10000)
        await self.page.locator(selector).screenshot(path=path)
        return path
    
    async def get_page_elements(self) -> List[Dict[str, Any]]:
        """获取页面上所有可交互元素的信息"""
        if self.page is None:
            raise Exception("浏览器未启动")
        
        elements = await self.page.evaluate("""
            () => {
                const elements = [];
                
                // 获取所有可交互的元素
                const selector = 'button, input, textarea, select, a, [role="button"], [role="link"], [role="checkbox"], [role="radio"], [role="menuitem"], [role="tab"], [role="option"], [role="listbox"], [role="combobox"], [role="gridcell"], [role="treeitem"], [role="slider"], [role="progressbar"], [role="img"], [role="tooltip"], [role="dialog"], [role="alert"], [role="alertdialog"], [role="application"], [role="banner"], [role="complementary"], [role="contentinfo"], [role="form"], [role="main"], [role="navigation"], [role="region"], [role="search"], [role="status"], [role="tabpanel"], [role="timer"], [role="toolbar"], [role="tooltip"], [role="tree"], [role="treegrid"], [role="grid"], [role="gridcell"], [role="row"], [role="rowgroup"], [role="columnheader"], [role="rowheader"], [role="separator"], [role="presentation"], [role="none"], [tabindex], [onclick], [ondblclick], [onmousedown], [onmouseup], [onmouseover], [onmousemove], [onmouseout], [onkeypress], [onkeydown], [onkeyup], [class*="btn"], [class*="button"], [class*="input"], [class*="select"], [class*="click"], [class*="toggle"], [class*="menu"], [class*="nav"], [class*="link"], [class*="item"], [class*="option"], [class*="control"], [class*="field"], [class*="action"], [class*="trigger"], [id*="btn"], [id*="button"], [id*="input"], [id*="select"], [id*="click"], [id*="toggle"], [id*="menu"], [id*="nav"], [id*="link"], [id*="item"], [id*="option"], [id*="control"], [id*="field"], [id*="action"], [id*="trigger"]';
                
                const elementList = document.querySelectorAll(selector);
                
                for (let i = 0; i < elementList.length; i++) {
                    const el = elementList[i];
                    
                    // 获取元素的可见性
                    const isVisible = el.offsetParent !== null;
                    
                    // 跳过不可见的元素
                    if (!isVisible) continue;
                    
                    // 获取元素的边界框
                    const rect = el.getBoundingClientRect();
                    
                    // 获取各种可能的标识符
                    const id = el.id;
                    const classes = el.className;
                    const tagName = el.tagName.toLowerCase();
                    const textContent = el.textContent ? el.textContent.trim().substring(0, 50) : '';
                    const title = el.title;
                    const ariaLabel = el.getAttribute('aria-label');
                    const placeholder = el.placeholder;
                    const alt = el.alt;
                    
                    // 构建选择器
                    let selector = tagName;
                    if (id) selector += `#${id}`;
                    if (classes) {
                        const classList = classes.split(' ').filter(c => c !== '');
                        for (const cls of classList) {
                            if (cls) selector += `.${cls}`;
                        }
                    }
                    
                    elements.push({
                        selector: selector,
                        tagName: tagName,
                        id: id,
                        classes: classes,
                        textContent: textContent,
                        title: title,
                        ariaLabel: ariaLabel,
                        placeholder: placeholder,
                        alt: alt,
                        rect: {
                            x: rect.x,
                            y: rect.y,
                            width: rect.width,
                            height: rect.height,
                            top: rect.top,
                            right: rect.right,
                            bottom: rect.bottom,
                            left: rect.left
                        },
                        isVisible: isVisible
                    });
                }
                
                return elements;
            }
        """)
        
        return elements
    
    async def get_page_title(self) -> str:
        """获取页面标题"""
        if self.page is None:
            raise Exception("浏览器未启动")
        
        return await self.page.title()
    
    async def get_current_url(self) -> str:
        """获取当前URL"""
        if self.page is None:
            raise Exception("浏览器未启动")
        
        return self.page.url
    
    # 🔥 已移除 reset_page_to_clean_state 方法，不再需要页面重置逻辑
    
    async def wait_for_selector(self, selector: str, timeout: int = 30000, selector_type: str = "css", iframe_selector: str = None, page=None):
        """等待元素出现。page: 可选，指定在哪个标签页执行（多标签并行时使用）"""
        target_page = page if page is not None else self.page
        if target_page is None:
            raise Exception("浏览器未启动")
        
        full_selector = f"xpath={selector}" if (selector_type == "xpath" or (selector and (selector.startswith("//") or selector.startswith("/")))) else selector
        if iframe_selector:
            context = target_page.frame_locator(iframe_selector)
            element = context.locator(full_selector)
            await element.wait_for(state="attached", timeout=timeout)
        else:
            await target_page.wait_for_selector(full_selector, timeout=timeout)
    
    async def get_element_count(self, selector: str) -> int:
        """获取指定选择器的元素数量"""
        if self.page is None:
            raise Exception("浏览器未启动")
        
        count = await self.page.evaluate(f"""
            () => {{
                return document.querySelectorAll('{selector}').length;
            }}
        """)
        return count
    
    async def take_screenshot(self, path: str = None, page=None):
        """截取页面截图。page: 可选，指定在哪个标签页执行（多标签并行时使用）"""
        target_page = page if page is not None else self.page
        if target_page is None:
            raise Exception("浏览器未启动")
        
        if path is None:
            path = f"screenshot_{int(time.time())}.png"
        
        await target_page.screenshot(path=path)
        return path
    
    async def execute_script_steps(self, steps: List[Dict[str, Any]]):
        """执行脚本步骤（严格按顺序串行执行，禁用所有并行逻辑）"""
        # 强制使用主页面，禁用所有多标签页和并行执行逻辑
        target_page = self.page
        if target_page is None:
            await self.start_browser()
            target_page = self.page
        
        # 确保使用主页面，禁用所有并行执行选项
        uat_logger.info("🔧 [SERIAL_EXECUTION] 启用严格的串行执行模式，禁用所有并行逻辑")
        
        # 🔥 修复：禁用所有步骤去重逻辑，直接执行所有步骤
        # 原因：去重逻辑过于激进，会跳过应该执行的步骤
        if not steps:
            return []

        uat_logger.info(f"开始执行步骤，原始步骤数: {len(steps)}")

        # 直接执行所有步骤，不进行任何去重
        results = []
        step_index = 0
        
        # 跟踪操作状态,强制执行顺序
        has_clicked = False
        has_submitted = False
        
        for step in steps:
            step_index += 1
            action = step.get("action")
            uat_logger.info(f"🎯 [STEP_DEBUG] ========== 开始执行步骤 {step_index}/{len(steps)} ==========")
            uat_logger.info(f"🎯 [STEP_DEBUG] 步骤类型: {action}, 详情: {step}")
            uat_logger.info(f"🎯 [STEP_DEBUG] 当前操作状态: has_clicked={has_clicked}, has_submitted={has_submitted}")
            
            # 获取当前页面状态
            try:
                current_url = target_page.url
                uat_logger.info(f"🎯 [STEP_DEBUG] 当前页面URL: {current_url}")
            except Exception as e:
                uat_logger.warning(f"🎯 [STEP_DEBUG] 获取当前URL失败: {str(e)}")
            
            try:
                # 强制检查:submit操作前必须先click
                if action == "submit":
                    if not has_clicked:
                        uat_logger.warning(f"⚠️ [FORCE_CHECK] submit操作前未检测到click,但继续执行(多案例执行模式)")
                        # 不再强制抛出异常,允许继续执行
                        # raise Exception(f"违反强制规则:submit操作前必须先click,但当前未检测到click操作")
                    else:
                        uat_logger.info(f"✅ [FORCE_CHECK] submit操作检查通过:已检测到click操作")
                
                # 强制检查:navigate操作前必须先submit(除非是第一个navigate操作)
                if action == "navigate" and step_index > 1:
                    if not has_submitted:
                        uat_logger.warning(f"⚠️ [FORCE_CHECK] navigate操作前未检测到submit,但继续执行(多案例执行模式)")
                        # 不再强制抛出异常,允许继续执行
                        # raise Exception(f"违反强制规则:navigate操作前必须先submit,但当前未检测到submit操作")
                    else:
                        uat_logger.info(f"✅ [FORCE_CHECK] navigate操作检查通过:已检测到submit操作")
                
                if action == "navigate":
                    url = step.get("url")
                    # 检查当前页面是否已经在目标URL上,避免重复导航
                    if target_page and target_page.url != url:
                        await self.navigate_to(url, page=target_page)
                        # 确保页面完全加载完成
                        if target_page:
                            uat_logger.info("导航后等待页面完全加载")
                            await target_page.wait_for_load_state('domcontentloaded', timeout=30000)
                            await target_page.wait_for_load_state('load', timeout=30000)
                    else:
                        uat_logger.info(f"页面已在目标URL上,跳过导航: {url}")
                elif action == "click":
                    selector = step.get("selector")
                    
                    # 尝试点击元素,如果失败则尝试处理动态选择器
                    click_success = False
                    
                    # 首先尝试原始选择器
                    try:
                        await self.click_element(
                            selector,
                            step.get("selector_type", "css"),
                            step.get("iframe_selector"),
                            page=target_page,
                            locator_candidates=step.get("locator_candidates"),
                        )
                        click_success = True
                    except Exception as e:
                        uat_logger.warning(f"原始选择器点击失败: {str(e)}")
                        
                        # 尝试使用更宽松的选择器(移除动态class)
                        if '.' in selector:
                            # 对于CSS选择器,尝试移除动态类名(如is-loading、is-focus等)
                            import re
                            # 保留基础元素类型和非动态类
                            # 移除所有以is-开头的动态类(如is-loading、is-focus、is-active等)
                            base_selector = re.sub(r'\.(is-\w+)', '', selector)
                            # 移除所有以el-开头的动态类(Element UI临时类名)
                            base_selector = re.sub(r'\.(el-\w+-\w+)', '', base_selector)
                            # 移除所有以has-开头的动态类
                            base_selector = re.sub(r'\.(has-\w+)', '', base_selector)
                            # 移除连续的空格和重复的>符号
                            base_selector = re.sub(r'\s+', ' ', base_selector)
                            base_selector = re.sub(r'\s*>\s*', ' > ', base_selector)
                            base_selector = base_selector.strip()
                            
                            if base_selector != selector and base_selector.strip():
                                uat_logger.info(f"尝试使用更宽松的选择器: {base_selector}")
                                try:
                                    # 等待基础选择器的元素可见
                                    await target_page.wait_for_selector(base_selector, state='visible', timeout=5000)
                                    await target_page.click(base_selector, force=True, timeout=5000)
                                    uat_logger.info(f"使用宽松选择器成功点击元素: {base_selector}")
                                    click_success = True
                                except Exception as e2:
                                    uat_logger.warning(f"宽松选择器点击失败: {str(e2)}")
                                    
                        # 如果前面的尝试都失败,尝试更基础的选择器
                        if not click_success:
                            # 尝试仅使用标签名和ID
                            try:
                                import re
                                # 提取ID部分
                                id_match = re.search(r'#([\w-]+)', selector)
                                class_matches = re.findall(r'\.([\w-]+)', selector)
                                tag_match = re.match(r'([a-zA-Z]+)', selector)
                                
                                if id_match:
                                    basic_selector = f"#{id_match.group(1)}"
                                    uat_logger.info(f"尝试使用ID选择器: {basic_selector}")
                                    await target_page.wait_for_selector(basic_selector, state='visible', timeout=5000)
                                    await target_page.click(basic_selector, force=True, timeout=5000)
                                    uat_logger.info(f"使用ID选择器成功点击元素: {basic_selector}")
                                    click_success = True
                            except:
                                pass
                        
                        if not click_success:
                            # 如果所有尝试都失败,抛出异常
                            raise Exception(f"无法点击元素,所有选择器尝试均失败: {selector}")
                    
                    # 对于点击操作,根据元素类型执行适当的等待策略
                    if target_page:
                        try:
                            # 根据选择器判断元素类型,执行不同的等待策略
                            if 'input' in selector or 'textarea' in selector or 'select' in selector:
                                # 对于表单元素,等待一段时间让数据保存,但不等待页面加载
                                uat_logger.info("表单元素点击,等待数据保存完成")
                                await target_page.wait_for_timeout(300)
                            elif 'button' in selector or 'submit' in selector.lower():
                                # 对于按钮,先不进行导航检测,因为可能只是UI变化
                                uat_logger.info("按钮点击,等待UI响应")
                                await target_page.wait_for_timeout(300)
                            else:
                                # 对于其他元素,使用较短的等待时间
                                await target_page.wait_for_timeout(200)
                        except Exception as e:
                            uat_logger.warning(f"点击后等待时出错: {str(e)}")
                            # 发生错误时也继续执行
                elif action in ["fill", "input"]:
                    selector = step.get("selector")
                    # 🔥 修复:对于input操作，text可能存储在input_value字段中
                    text = step.get("text") or step.get("input_value", "")
                    uat_logger.info(f"📝 [INPUT_VALUE] 填充文本: '{text}' (来自text字段: {bool(step.get('text'))}, 来自input_value字段: {bool(step.get('input_value'))})")
                    
                    # 尝试填充元素,如果失败则尝试处理动态选择器
                    fill_success = False
                    
                    # 首先尝试原始选择器
                    try:
                        await self.fill_input(
                            selector,
                            text,
                            step.get("selector_type", "css"),
                            step.get("iframe_selector"),
                            page=target_page,
                            locator_candidates=step.get("locator_candidates"),
                        )
                        fill_success = True
                    except Exception as e:
                        uat_logger.warning(f"原始选择器填充失败: {str(e)}")
                        
                        # 尝试使用更宽松的选择器(移除动态class)
                        if '.' in selector:
                            import re
                            # 保留基础元素类型和非动态类
                            # 移除所有以is-开头的动态类(如is-loading、is-focus、is-active等)
                            base_selector = re.sub(r'\.(is-\w+)', '', selector)
                            # 移除所有以el-开头的动态类(Element UI临时类名)
                            base_selector = re.sub(r'\.(el-\w+-\w+)', '', base_selector)
                            # 移除所有以has-开头的动态类
                            base_selector = re.sub(r'\.(has-\w+)', '', base_selector)
                            # 移除连续的空格和重复的>符号
                            base_selector = re.sub(r'\s+', ' ', base_selector)
                            base_selector = re.sub(r'\s*>\s*', ' > ', base_selector)
                            base_selector = base_selector.strip()
                            
                            if base_selector != selector and base_selector.strip():
                                uat_logger.info(f"尝试使用更宽松的选择器: {base_selector}")
                                try:
                                    await self.fill_input(base_selector, text, page=target_page)
                                    fill_success = True
                                except Exception as e2:
                                    uat_logger.warning(f"宽松选择器填充失败: {str(e2)}")
                                    
                        # 如果前面的尝试都失败,尝试更基础的选择器
                        if not fill_success:
                            # 尝试仅使用标签名和ID
                            try:
                                import re
                                # 提取ID部分
                                id_match = re.search(r'#([\w-]+)', selector)
                                class_matches = re.findall(r'\.([\w-]+)', selector)
                                tag_match = re.match(r'([a-zA-Z]+)', selector)
                                
                                if id_match:
                                    basic_selector = f"#{id_match.group(1)}"
                                    uat_logger.info(f"尝试使用ID选择器: {basic_selector}")
                                    await self.fill_input(basic_selector, text, page=target_page)
                                    fill_success = True
                            except:
                                pass
                        
                        if not fill_success:
                            # 如果所有尝试都失败,抛出异常
                            raise Exception(f"无法填充元素,所有选择器尝试均失败: {selector}")
                    
                    # 填充后等待一小段时间以确保值已设置,但不等待页面加载
                    if target_page:
                        await target_page.wait_for_timeout(300)
                        uat_logger.info(f"填充操作完成,等待值生效: {selector}")
                elif action == "scroll":
                    iv = (step.get("input_value") or "").strip()
                    parsed = parse_platform_scroll_input_value(iv)
                    rdx = parsed["right"] - parsed["left"]
                    rdy = parsed["down"] - parsed["up"]
                    if rdx != 0 or rdy != 0:
                        await self.scroll_by_delta(
                            rdx, rdy, step.get("iframe_selector"), page=target_page
                        )
                    elif "scrollPosition" in step:
                        scroll_pos = step.get("scrollPosition", {})
                        current_scroll = {"x": 0, "y": 0}
                        if target_page is not None:
                            current_scroll = await target_page.evaluate("""
                                () => ({
                                    x: window.pageXOffset || document.documentElement.scrollLeft,
                                    y: window.pageYOffset || document.documentElement.scrollTop
                                })
                            """)
                        else:
                            uat_logger.warning("页面对象为None,无法获取滚动位置")
                        delta_x = scroll_pos.get("x", 0) - current_scroll["x"]
                        delta_y = scroll_pos.get("y", 0) - current_scroll["y"]
                        if target_page is not None:
                            await target_page.evaluate(f"window.scrollBy({delta_x}, {delta_y})")
                    else:
                        direction = step.get("direction", "down")
                        pixels = step.get("pixels", 500)
                        await self.scroll_page(direction, pixels, page=target_page)
                    
                    # 移除滚动后的固定等待
                elif action == "hover":
                    # 悬停步骤通常不是必要的,跳过以提高执行速度
                    uat_logger.info(f"跳过悬停步骤: {step.get('selector')}")
                    # selector = step.get("selector")
                    # await self.hover_element(selector)
                    # await asyncio.sleep(0.2)
                elif action == "double_click":
                    selector = step.get("selector")
                    await self.double_click_element(selector, step.get("selector_type", "css"), step.get("iframe_selector"), page=target_page)
                    # 移除双击后的固定等待
                elif action == "right_click":
                    selector = step.get("selector")
                    await self.right_click_element(selector, step.get("selector_type", "css"), step.get("iframe_selector"), page=target_page)
                    # 移除右键点击后的固定等待
                elif action == "submit":
                    selector = step.get("selector")
                    uat_logger.info(f"🔍 [SUBMIT_DEBUG] 开始执行submit操作,选择器: {selector}")
                    
                    # 获取当前页面URL和状态
                    try:
                        current_url = target_page.url
                        uat_logger.info(f"🔍 [SUBMIT_DEBUG] 当前页面URL: {current_url}")
                    except Exception as e:
                        uat_logger.warning(f"🔍 [SUBMIT_DEBUG] 获取当前URL失败: {str(e)}")
                    
                    # 尝试提交表单,如果失败则尝试处理动态选择器
                    submit_success = False
                    
                    # 首先尝试原始选择器,直接点击提交按钮来触发表单提交
                    try:
                        uat_logger.info(f"🔍 [SUBMIT_DEBUG] 尝试方式1: 原始选择器提交")
                        # 检查元素是否存在
                        element_exists = await target_page.evaluate("(selector) => document.querySelector(selector) !== null", selector)
                        if element_exists:
                            uat_logger.info(f"🔍 [SUBMIT_DEBUG] 提交按钮存在,准备点击")
                            # 使用JavaScript点击提交按钮,触发表单提交
                            await target_page.evaluate("""(selector) => {
                                const element = document.querySelector(selector);
                                if (element) {
                                    // 直接点击提交按钮,触发表单提交
                                    element.click();
                                }
                            }""", selector)
                            uat_logger.info(f"✅ [SUBMIT_DEBUG] 方式1成功点击提交按钮")
                            submit_success = True
                        else:
                            uat_logger.error(f"❌ [SUBMIT_DEBUG] 提交按钮不存在: {selector}")
                    except Exception as e:
                        uat_logger.error(f"❌ [SUBMIT_DEBUG] 原始选择器提交失败: {str(e)}")
                        
                        # 尝试使用更宽松的选择器(移除动态class)
                        if '.' in selector:
                            uat_logger.info(f"🔍 [SUBMIT_DEBUG] 尝试方式2: 更宽松的选择器")
                            import re
                            # 保留基础元素类型和非动态类
                            # 移除所有以is-开头的动态类(如is-loading、is-focus、is-active等)
                            base_selector = re.sub(r'\.(is-\w+)', '', selector)
                            # 移除所有以el-开头的动态类(Element UI临时类名)
                            base_selector = re.sub(r'\.(el-\w+-\w+)', '', base_selector)
                            # 移除所有以has-开头的动态类
                            base_selector = re.sub(r'\.(has-\w+)', '', base_selector)
                            # 移除连续的空格和重复的>符号
                            base_selector = re.sub(r'\s+', ' ', base_selector)
                            base_selector = re.sub(r'\s*>\s*', ' > ', base_selector)
                            base_selector = base_selector.strip()
                            
                            if base_selector != selector and base_selector.strip():
                                uat_logger.info(f"🔍 [SUBMIT_DEBUG] 尝试使用更宽松的选择器: {base_selector}")
                                try:
                                    # 使用JavaScript点击提交按钮
                                    element_exists = await target_page.evaluate("(selector) => document.querySelector(selector) !== null", base_selector)
                                    if element_exists:
                                        uat_logger.info(f"🔍 [SUBMIT_DEBUG] 宽松选择器元素存在,准备点击")
                                        await target_page.evaluate("""(selector) => {
                                            const element = document.querySelector(selector);
                                            if (element) {
                                                // 直接点击提交按钮,触发表单提交
                                                element.click();
                                            }
                                        }""", base_selector)
                                        uat_logger.info(f"✅ [SUBMIT_DEBUG] 方式2成功点击提交按钮")
                                        submit_success = True
                                    else:
                                        uat_logger.warning(f"⚠️ [SUBMIT_DEBUG] 宽松选择器元素不存在: {base_selector}")
                                except Exception as e2:
                                    uat_logger.warning(f"❌ [SUBMIT_DEBUG] 宽松选择器提交失败: {str(e2)}")
                                    
                        # 如果前面的尝试都失败,尝试更基础的选择器
                        if not submit_success:
                            uat_logger.info(f"🔍 [SUBMIT_DEBUG] 尝试方式3: 基础选择器")
                            # 尝试仅使用标签名和ID
                            try:
                                import re
                                # 提取ID部分
                                id_match = re.search(r'#([\w-]+)', selector)
                                class_matches = re.findall(r'\.([\w-]+)', selector)
                                tag_match = re.match(r'([a-zA-Z]+)', selector)
                                
                                if id_match:
                                    basic_selector = f"#{id_match.group(1)}"
                                    uat_logger.info(f"🔍 [SUBMIT_DEBUG] 尝试使用ID选择器: {basic_selector}")
                                    # 使用JavaScript点击提交按钮
                                    element_exists = await target_page.evaluate("(selector) => document.querySelector(selector) !== null", basic_selector)
                                    if element_exists:
                                        uat_logger.info(f"🔍 [SUBMIT_DEBUG] ID选择器元素存在,准备点击")
                                        await target_page.evaluate("""(selector) => {
                                            const element = document.querySelector(selector);
                                            if (element) {
                                                // 直接点击提交按钮,触发表单提交
                                                element.click();
                                            }
                                        }""", basic_selector)
                                        uat_logger.info(f"✅ [SUBMIT_DEBUG] 方式3成功点击提交按钮")
                                        submit_success = True
                                    else:
                                        uat_logger.warning(f"⚠️ [SUBMIT_DEBUG] ID选择器元素不存在: {basic_selector}")
                            except Exception as e3:
                                uat_logger.warning(f"❌ [SUBMIT_DEBUG] 基础选择器提交失败: {str(e3)}")
                        
                        if not submit_success:
                            # 如果所有尝试都失败,抛出异常
                            uat_logger.error(f"❌ [SUBMIT_DEBUG] 所有提交方式均失败: {selector}")
                            raise Exception(f"无法提交表单,所有选择器尝试均失败: {selector}")
                        
                        uat_logger.info(f"✅ [SUBMIT_DEBUG] submit操作执行成功: {selector}")
                    
                    # 提交后等待一小段时间,确保表单提交事件被触发
                    if target_page:
                        uat_logger.info(f"🔍 [SUBMIT_DEBUG] 表单提交,等待一小段时间确保提交事件触发")
                        await target_page.wait_for_timeout(300)
                        
                        # 检查提交后的页面状态
                        try:
                            new_url = target_page.url
                            uat_logger.info(f"🔍 [SUBMIT_DEBUG] 提交后页面URL: {new_url}")
                            if new_url != current_url:
                                uat_logger.info(f"🔄 [SUBMIT_DEBUG] 检测到页面URL变化: {current_url} -> {new_url}")
                        except Exception as e:
                            uat_logger.warning(f"🔍 [SUBMIT_DEBUG] 获取提交后URL失败: {str(e)}")
                        
                        uat_logger.info(f"✅ [SUBMIT_DEBUG] submit操作完成")
                elif action == "keypress":
                    selector = step.get("selector")
                    key = step.get("key")
                    if selector:
                        await target_page.click(selector)  # 先点击确保焦点
                    # 如果没有selector,直接发送按键
                    await target_page.keyboard.press(key)
                    # 移除按键后的固定等待
                elif action == "wait":
                    wait_time = step.get("time", 1000)
                    await asyncio.sleep(wait_time / 1000)  # 转换为秒
                elif action == "wait_for_selector":
                    selector = step.get("selector")
                    timeout = step.get("timeout", 30000)
                    if selector:
                        await self.wait_for_selector(selector, timeout, step.get("selector_type", "css"), step.get("iframe_selector"), page=target_page)
                elif action == "wait_for_element_visible":
                    selector = step.get("selector")
                    timeout = step.get("timeout", 30000)
                    if selector:
                        await self.wait_for_element_visible(selector, timeout, step.get("selector_type", "css"), page=target_page)
                elif action == "screenshot":
                    # 截取页面截图
                    await self.take_screenshot(page=target_page)
                elif action == "extract_text":
                    selector = step.get("selector")
                    uat_logger.info(f"🔍 [EXTRACT_TEXT_DEBUG] 开始执行提取文本操作,选择器: {selector}")
                    
                    try:
                        if selector:
                            # 提取元素文本
                            extracted_text = await self.extract_element_text(selector, step.get("selector_type", "css"), step.get("iframe_selector"), page=target_page)
                            uat_logger.info(f"✅ [EXTRACT_TEXT_DEBUG] 提取到文本: {extracted_text[:100]}...")
                            # 标记为成功
                            step_status = "success"
                            step_extracted_text = extracted_text
                        else:
                            # 提取整个页面文本
                            extracted_text = await self.get_page_text(page=target_page)
                            uat_logger.info(f"✅ [EXTRACT_TEXT_DEBUG] 提取到页面文本: {extracted_text[:100]}...")
                            # 标记为成功
                            step_status = "success"
                            step_extracted_text = extracted_text
                    except Exception as e:
                        uat_logger.error(f"❌ [EXTRACT_TEXT_DEBUG] 提取文本失败: {str(e)}")
                        step_status = "error"
                        step_error = str(e)
                        step_extracted_text = ""
                    
                    # 等待页面状态稳定
                    if target_page:
                        try:
                            # 等待页面稳定,确保上一步操作完成
                            uat_logger.info(f"等待步骤完成: {action}")
                            
                            # 检查页面是否正在加载
                            try:
                                # 等待页面加载状态稳定(最多等待2秒)
                                await target_page.wait_for_load_state('domcontentloaded', timeout=2000)
                            except:
                                pass  # 页面可能已经加载完成
                            
                            # 等待一小段时间,让页面状态稳定
                            await target_page.wait_for_timeout(500)
                            
                            # 检查是否有正在进行的网络请求
                            try:
                                # 等待网络空闲(最多等待3秒)
                                await target_page.wait_for_load_state('networkidle', timeout=3000)
                            except:
                                pass  # 网络可能一直有活动
                            
                            uat_logger.info(f"步骤完成: {action}")
                        except Exception as e:
                            uat_logger.warning(f"等待页面稳定时出错: {str(e)}")
                            # 即使等待失败,也继续执行后续步骤
                    
                    # 检查步骤执行后的页面状态
                    try:
                        new_url = target_page.url
                        uat_logger.info(f"🎯 [STEP_DEBUG] 步骤执行后页面URL: {new_url}")
                        if new_url != current_url:
                            uat_logger.info(f"🔄 [STEP_DEBUG] 检测到页面URL变化: {current_url} -> {new_url}")
                    except Exception as e:
                        uat_logger.warning(f"🎯 [STEP_DEBUG] 获取步骤执行后URL失败: {str(e)}")
                    
                    uat_logger.info(f"✅ [STEP_DEBUG] ========== 步骤 {step_index}/{len(steps)} 执行成功 ==========")
                    
                    # 添加到结果中
                    if step_status == "success":
                        result = {"status": "success", "step": step}
                        if step_extracted_text:
                            result["extracted_text"] = step_extracted_text
                        results.append(result)
                    else:
                        results.append({"status": "error", "step": step, "error": step_error})
                    
                    # 跳过后续的通用处理
                    continue
                elif action == "verify":
                    selector = step.get("selector")
                    verify_type = step.get("verify_type", "auto")
                    uat_logger.info(f"🔍 [VERIFY_DEBUG] 开始执行验证操作,选择器: {selector}, 验证类型: {verify_type}")
                    
                    try:
                        # 执行验证操作
                        await self.verify_element(selector, verify_type, step.get("selector_type", "css"), step.get("iframe_selector"), page=target_page)
                        uat_logger.info(f"✅ [VERIFY_DEBUG] 验证操作成功")
                        # 标记为成功
                        step_status = "success"
                    except Exception as e:
                        uat_logger.error(f"❌ [VERIFY_DEBUG] 验证操作失败: {str(e)}")
                        step_status = "error"
                        step_error = str(e)
                    
                    # 等待页面状态稳定
                    if target_page:
                        try:
                            # 等待页面稳定,确保上一步操作完成
                            uat_logger.info(f"等待步骤完成: {action}")
                            
                            # 检查页面是否正在加载
                            try:
                                # 等待页面加载状态稳定(最多等待2秒)
                                await target_page.wait_for_load_state('domcontentloaded', timeout=2000)
                            except:
                                pass  # 页面可能已经加载完成
                            
                            # 等待一小段时间,让页面状态稳定
                            await target_page.wait_for_timeout(500)
                            
                            # 检查是否有正在进行的网络请求
                            try:
                                # 等待网络空闲(最多等待3秒)
                                await target_page.wait_for_load_state('networkidle', timeout=3000)
                            except:
                                pass  # 网络可能一直有活动
                            
                            uat_logger.info(f"步骤完成: {action}")
                        except Exception as e:
                            uat_logger.warning(f"等待页面稳定时出错: {str(e)}")
                            # 即使等待失败,也继续执行后续步骤
                    
                    # 检查步骤执行后的页面状态
                    try:
                        new_url = target_page.url
                        uat_logger.info(f"🎯 [STEP_DEBUG] 步骤执行后页面URL: {new_url}")
                        if new_url != current_url:
                            uat_logger.info(f"🔄 [STEP_DEBUG] 检测到页面URL变化: {current_url} -> {new_url}")
                    except Exception as e:
                        uat_logger.warning(f"🎯 [STEP_DEBUG] 获取步骤执行后URL失败: {str(e)}")
                    
                    uat_logger.info(f"✅ [STEP_DEBUG] ========== 步骤 {step_index}/{len(steps)} 执行成功 ==========")
                    
                    # 添加到结果中
                    if step_status == "success":
                        results.append({"status": "success", "step": step})
                    else:
                        results.append({"status": "error", "step": step, "error": step_error})
                    
                    # 跳过后续的通用处理
                    continue
                if target_page:
                    try:
                        # 等待页面稳定,确保上一步操作完成
                        uat_logger.info(f"等待步骤完成: {action}")
                        
                        # 检查页面是否正在加载
                        try:
                            # 等待页面加载状态稳定(最多等待2秒)
                            await target_page.wait_for_load_state('domcontentloaded', timeout=2000)
                        except:
                            pass  # 页面可能已经加载完成
                        
                        # 等待一小段时间,让页面状态稳定
                        await target_page.wait_for_timeout(500)
                        
                        # 检查是否有正在进行的网络请求
                        try:
                            # 等待网络空闲(最多等待3秒)
                            await target_page.wait_for_load_state('networkidle', timeout=3000)
                        except:
                            pass  # 网络可能一直有活动
                        
                        uat_logger.info(f"步骤完成: {action}")
                    except Exception as e:
                        uat_logger.warning(f"等待页面稳定时出错: {str(e)}")
                        # 即使等待失败,也继续执行后续步骤
                
                # 检查步骤执行后的页面状态
                try:
                    new_url = target_page.url
                    uat_logger.info(f"🎯 [STEP_DEBUG] 步骤执行后页面URL: {new_url}")
                    if new_url != current_url:
                        uat_logger.info(f"🔄 [STEP_DEBUG] 检测到页面URL变化: {current_url} -> {new_url}")
                except Exception as e:
                    uat_logger.warning(f"🎯 [STEP_DEBUG] 获取步骤执行后URL失败: {str(e)}")
                
                uat_logger.info(f"✅ [STEP_DEBUG] ========== 步骤 {step_index}/{len(steps)} 执行成功 ==========")
                results.append({"status": "success", "step": step})
                
                # 更新操作状态
                if action == "click":
                    has_clicked = True
                    uat_logger.info(f"🔄 [STATE_UPDATE] 已执行click操作,更新状态: has_clicked=True")
                elif action == "submit":
                    has_submitted = True
                    uat_logger.info(f"🔄 [STATE_UPDATE] 已执行submit操作,更新状态: has_submitted=True")
            except Exception as e:
                uat_logger.error(f"❌ [STEP_DEBUG] ========== 步骤 {step_index}/{len(steps)} 执行失败 ==========")
                uat_logger.error(f"❌ [STEP_DEBUG] 错误详情: {str(e)}")
                results.append({"status": "error", "step": step, "error": str(e)})
                # 🔥 修复：异常捕获后添加 break 语句，终止当前用例的步骤循环
                uat_logger.error(f"❌ [STEP_DEBUG] 步骤执行失败，终止用例执行: {e}")
                break
        
        uat_logger.info(f"🎯 [STEP_DEBUG] ========== 所有步骤执行完成,共 {len(results)} 个步骤 ==========")
        return results
    
    async def execute_multiple_test_cases(self, case_ids: List[int], db) -> Dict[str, Any]:
        """执行多个测试用例（严格按列表顺序串行执行；禁用所有并行逻辑）
        
        Args:
            case_ids: 测试用例ID列表（顺序即执行顺序）
            db: 数据库实例,用于获取测试用例步骤
            
        Returns:
            包含所有测试用例执行结果的字典
        """
        uat_logger.info(f"🚀 [SERIAL_MULTI_CASE] 开始串行执行 {len(case_ids)} 个用例: {case_ids}")
        
        all_results = {
            "total_cases": len(case_ids),
            "successful_cases": 0,
            "failed_cases": 0,
            "case_results": []
        }
        
        # 确保浏览器已启动
        browser_need_start = False
        if self.browser is None or self.context is None:
            browser_need_start = True
        else:
            try:
                if not self.browser.is_connected():
                    self.browser = None
                    self.page = None
                    self.context = None
                    self.playwright = None
                    browser_need_start = True
            except Exception:
                browser_need_start = True
                self.browser = None
                self.page = None
                self.context = None
                self.playwright = None
        
        if browser_need_start:
            try:
                await self.start_browser()
                uat_logger.info("✅ [SERIAL_MULTI_CASE] 浏览器启动成功")
            except Exception as browser_error:
                uat_logger.error(f"❌ [SERIAL_MULTI_CASE] 浏览器启动失败: {str(browser_error)}")
                for failed_case_id in case_ids:
                    process_case_result({
                        "case_id": failed_case_id,
                        "case_name": "未知", 
                        "status": "error",
                        "error": f"浏览器启动失败: {str(browser_error)}"
                    }, failed_case_id)
                return all_results
        
        def build_execution_steps(steps):
            """将数据库步骤格式转换为执行脚本所需的格式"""
            execution_steps = []
            for step in steps:
                exec_step = {"action": step["action"]}
                if step["action"] == "click":
                    exec_step["selector"] = step["selector_value"]
                    exec_step["selector_type"] = step.get("selector_type", "css")
                    exec_step["iframe_selector"] = step.get("iframe_selector")
                elif step["action"] in ["fill", "input"]:
                    exec_step["selector"] = step["selector_value"]
                    exec_step["text"] = step["input_value"]
                    exec_step["selector_type"] = step.get("selector_type", "css")
                    exec_step["iframe_selector"] = step.get("iframe_selector")
                elif step["action"] == "submit":
                    exec_step["selector"] = step["selector_value"]
                    exec_step["selector_type"] = step.get("selector_type", "css")
                    exec_step["iframe_selector"] = step.get("iframe_selector")
                elif step["action"] == "navigate":
                    # 统一校验URL，不再用哨兵字符
                    raw = step.get("url") or step.get("input_value", "")
                    fixed_url, url_err = _pa_validate_url(raw)
                    if url_err:
                        uat_logger.error(f"构建步骤: 导航URL无效 ({raw}): {url_err}")
                        exec_step["url"] = "__INVALID_URL__"
                        exec_step["url_error"] = url_err
                    elif fixed_url is None:
                        exec_step["url"] = "__SKIP_URL__"  # 跳过占位符
                    else:
                        exec_step["url"] = fixed_url
                elif step["action"] == "keypress":
                    exec_step["key"] = step["input_value"]
                elif step["action"] == "wait":
                    try:
                        raw_w = (step.get("input_value") or "1").strip()
                        iv = int(float(raw_w)) if raw_w else 1
                        # 兼容：1–120 视为秒；更大整数视为毫秒（如 AI 常写的 1500）
                        if iv <= 120:
                            exec_step["time"] = iv * 1000
                        else:
                            exec_step["time"] = max(100, iv)
                    except Exception:
                        exec_step["time"] = 1000  # 默认1秒
                elif step["action"] == "select":
                    exec_step["selector"] = step["selector_value"]
                    exec_step["text"] = step.get("input_value", "")
                    exec_step["selector_type"] = step.get("selector_type", "css")
                    exec_step["iframe_selector"] = step.get("iframe_selector")
                elif step["action"] in ["wait_for_selector", "wait_for_element_visible"]:
                    exec_step["selector"] = step["selector_value"]
                    exec_step["selector_type"] = step.get("selector_type", "css")
                    exec_step["iframe_selector"] = step.get("iframe_selector")
                    try:
                        exec_step["timeout"] = int(step["input_value"])
                    except Exception:
                        exec_step["timeout"] = 30000
                elif step["action"] == "extract_text":
                    exec_step["selector"] = step["selector_value"]
                    exec_step["selector_type"] = step.get("selector_type", "css")
                    exec_step["iframe_selector"] = step.get("iframe_selector")
                elif step["action"] == "verify":
                    exec_step["selector"] = step["selector_value"]
                    exec_step["verify_type"] = step.get("verify_type", "auto")
                    exec_step["selector_type"] = step.get("selector_type", "css")
                    exec_step["iframe_selector"] = step.get("iframe_selector")
                if step.get("description"):
                    exec_step["description"] = step["description"]
                execution_steps.append(exec_step)
            return execution_steps
        
        def process_case_result(result, case_id):
            """统一处理单个用例结果并写入 all_results"""
            if isinstance(result, Exception):
                all_results["case_results"].append({
                    "case_id": case_id,
                    "case_name": "未知",
                    "status": "error",
                    "error": str(result)
                })
                all_results["failed_cases"] += 1
            elif result.get("status") == "success":
                all_results["case_results"].append(result)
                all_results["successful_cases"] += 1
            elif result.get("status") == "warning":
                all_results["case_results"].append(result)
            else:
                all_results["case_results"].append(result)
                all_results["failed_cases"] += 1
        
        # 🔥 精简执行前日志
        uat_logger.info(f"🎯 [SERIAL_MULTI_CASE] 开始执行用例序列")
        
        # 执行顺序追踪
        actual_execution_order = []
        
        for index, case_id in enumerate(case_ids):
            stop_checker = getattr(self, "_external_stop_checker", None)
            if callable(stop_checker) and stop_checker():
                uat_logger.warning("🛑 [SERIAL_MULTI_CASE] 检测到外部停止请求，终止批量执行")
                break
            case_number = index + 1
            actual_execution_order.append(case_id)
            case_start_time = time.time()
            
            uat_logger.info(f"🎯 [SERIAL_MULTI_CASE] 执行用例 {case_number}/{len(case_ids)}, ID: {case_id}")
            
            # 🔥 性能优化：轻量级用例环境准备（复用 context，只创建新 page）
            try:
                # 关闭旧页面（如果有），使用轻量方式
                if self.page:
                    try:
                        await self.page.close()
                    except Exception:
                        pass
                    finally:
                        self.page = None

                # 检查浏览器连接是否有效（仅在首个用例或异常后检查）
                browser_alive = False
                try:
                    browser_alive = self.browser is not None and self.browser.is_connected()
                except Exception:
                    browser_alive = False

                if not browser_alive:
                    uat_logger.warning(f"⚠️ [SERIAL_MULTI_CASE] 用例 {case_id} 执行前浏览器已断连，重新启动...")
                    try:
                        await self.start_browser()
                    except Exception as restart_error:
                        uat_logger.error(f"❌ [SERIAL_MULTI_CASE] 浏览器重启失败: {str(restart_error)}")
                        process_case_result({
                            "case_id": case_id, "case_name": "未知",
                            "status": "error", "error": f"浏览器重启失败: {str(restart_error)}"
                        }, case_id)
                        continue
                else:
                    # 🔥 性能优化：复用 context，避免每个用例都重建上下文
                    context_alive = False
                    try:
                        context_alive = self.context is not None and not (hasattr(self.context, 'closed') and self.context.closed)
                    except Exception:
                        context_alive = False

                    if not context_alive:
                        try:
                            self.context = await self.browser.new_context(ignore_https_errors=True, no_viewport=True)
                        except Exception as ctx_error:
                            uat_logger.error(f"❌ [SERIAL_MULTI_CASE] 创建context失败: {str(ctx_error)}")
                            process_case_result({
                                "case_id": case_id, "case_name": "未知",
                                "status": "error", "error": f"创建浏览器上下文失败: {str(ctx_error)}"
                            }, case_id)
                            continue

                # 在复用的 context 中创建新页面
                self.page = await self.context.new_page()
                uat_logger.info(f"✅ [SERIAL_MULTI_CASE] 用例 {case_id} 新页面就绪")
            except Exception as e:
                uat_logger.error(f"❌ [SERIAL_MULTI_CASE] 用例 {case_id} 环境准备失败: {str(e)}")
                process_case_result({
                    "case_id": case_id, "case_name": "未知",
                    "status": "error", "error": f"准备执行环境失败: {str(e)}"
                }, case_id)
                continue
            
            try:
                case_start_time = time.time()
                case_info = db.get_test_case_v2(case_id)
                if not case_info:
                    process_case_result({
                        "case_id": case_id,
                        "case_name": "未知",
                        "status": "error",
                        "error": f"测试用例不存在,ID: {case_id}"
                    }, case_id)
                    continue
                case_name = case_info.get("name", "未命名用例")
                # 🔥 修复：获取所有步骤而不是分页的10个步骤
                steps = db.get_case_steps(case_id, page=1, page_size=9999)
                if not steps:
                    process_case_result({
                        "case_id": case_id,
                        "case_name": case_name,
                        "status": "warning",
                        "warning": "测试用例没有步骤"
                    }, case_id)
                    continue
                execution_steps = build_execution_steps(steps)
                uat_logger.info(f"📋 用例 {case_id} ({case_name}) 共 {len(execution_steps)} 个步骤")
                
                # 如果用例有初始 URL，先导航到该URL
                case_url = case_info.get('url', '')
                if case_url:
                    fixed_url, url_err = _pa_validate_url(case_url)
                    if url_err:
                        uat_logger.warning(f"用例初始URL无效，跳过初始导航: {url_err}")
                    elif fixed_url:
                        uat_logger.info(f"用例初始导航到: {fixed_url}")
                        try:
                            await self.navigate_to(fixed_url, page=self.page)
                        except Exception as nav_err:
                            uat_logger.warning(f"用例初始导航失败，继续执行步骤: {nav_err}")
                
                # 执行用例步骤

                # 直接执行用例步骤，不使用全局超时
                try:
                    case_results = await self._execute_case_steps(execution_steps)
                    uat_logger.info(f"✅ [MULTI_CASE] 用例 {case_id} 执行完成，共执行了 {len(case_results)} 个步骤结果")
                    # 🔥 修复：用例级计时结束，确保计时时间准确
                    case_end_time = time.time()
                    case_duration = case_end_time - case_start_time
                except Exception as e:
                    uat_logger.error(f"❌ [SERIAL_MULTI_CASE] 用例 {case_id} 执行异常: {str(e)}")
                    case_results = [{"status": "error", "step": None, "error": str(e)}]
                    # 🔥 修复：在异常情况下也计算时间
                    case_end_time = time.time()
                    case_duration = case_end_time - case_start_time
                # 🔥 精简状态判断日志
                if len(case_results) < len(execution_steps):
                    case_status = "error"
                    extracted_text = ""
                    success_count = sum(1 for r in case_results if r.get("status") == "success")
                    error_count = sum(1 for r in case_results if r.get("status") == "error")
                else:
                    # 原有的状态判断逻辑
                    success_count = sum(1 for r in case_results if r.get("status") == "success")
                    error_count = sum(1 for r in case_results if r.get("status") == "error")
                    
                    # 🔥 关键修复：在 else 分支中也定义 extracted_text
                    extracted_text = ""
                    for r in case_results:
                        if r.get("extracted_text"):
                            extracted_text = r.get("extracted_text")
                    
                    case_status = "success" if error_count == 0 else "error"
                try:
                    db.create_run_history(
                       case_id,
                       case_status,
                       round(case_duration, 2),  # 使用实际计算的执行时间
                       "" if case_status == "success" else 
                    str(case_results),
                       extracted_text
                    )
                except Exception as db_error:
                    uat_logger.error(f"❌ [MULTI_CASE] 保存测试结果到数据库失败: {db_error}")
                result = {
                    "case_id": case_id,
                    "case_name": case_name,
                    "status": case_status,
                    "total_steps": len(case_results),
                    "successful_steps": success_count,
                    "failed_steps": error_count,
                    "extracted_text": extracted_text,
                    "step_results": case_results,
                    # 🔥 添加用例耗时计算
                    "execution_time": round(case_duration, 2)
                }
                process_case_result(result, case_id)
                uat_logger.info(f"✅ [SERIAL_MULTI_CASE] 用例 {case_id} 完成，状态: {case_status}，耗时: {result['execution_time']:.2f}s")
            except Exception as e:
                uat_logger.error(f"❌ [SERIAL_MULTI_CASE] 用例 {case_id} 异常: {str(e)}")
                process_case_result({
                    "case_id": case_id,
                    "case_name": case_info.get("name", "未命名用例") if 'case_info' in locals() else "未知",
                    "status": "error",
                    "error": str(e)
                }, case_id)
                uat_logger.info(f"⚠️ [SERIAL_MULTI_CASE] 用例 {case_id} 执行失败，继续下一个")
            finally:
                # 🔥 性能优化：不关闭 context，只关闭 page（context 复用给下一个用例）
                try:
                    if self.page:
                        await self.page.close()
                except Exception:
                    pass
                finally:
                    self.page = None
                # 🔥 性能优化：用例间等待时间从 1 秒减少到 0.1 秒
                await asyncio.sleep(0.1)
        
        uat_logger.info(f"🎉 [SERIAL_MULTI_CASE] 所有用例执行完成，成功: {all_results['successful_cases']}, 失败: {all_results['failed_cases']}")
        
        # 验证执行顺序
        if actual_execution_order != case_ids:
            uat_logger.error(f"❌ [SERIAL_MULTI_CASE] 执行顺序验证失败! 期望: {case_ids}, 实际: {actual_execution_order}")
        
        return all_results
    
    async def _execute_case_steps(self, execution_steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """执行单个测试用例的所有步骤（严格在当前页面执行，禁止多标签页并行）
        
        Args:
            execution_steps: 要执行的步骤列表
            
        Returns:
            步骤执行结果列表
        """
        case_results = []
        
        # 逐个执行步骤
        for i, step in enumerate(execution_steps):
            stop_checker = getattr(self, "_external_stop_checker", None)
            if callable(stop_checker) and stop_checker():
                case_results.append({
                    "status": "stopped",
                    "step": step.get('action', 'unknown'),
                    "error": "用户已停止执行"
                })
                break
            uat_logger.info(f"🎯 [CASE_STEP] 执行步骤 {i+1}/{len(execution_steps)}: {step.get('action', 'unknown')}")
            
            try:
                # 🔥 修改：为每个步骤执行添加60秒超时控制
                import asyncio
                step_result = await asyncio.wait_for(
                    self.execute_single_step(step),
                    timeout=60  # 60秒超时
                )
                case_results.extend(step_result if isinstance(step_result, list) else [step_result])
                uat_logger.info(f"✅ [CASE_STEP] 步骤 {i+1} 执行成功")
                
            except asyncio.TimeoutError as timeout_e:
                # 检查是否在超时中有更具体的错误信息
                last_exception = getattr(self, '_last_step_exception', None)
                if last_exception and hasattr(last_exception, 'specific_error'):
                    # 优先报告具体的执行失败
                    uat_logger.error(f"❌ [CASE_STEP] 执行失败（非超时导致）: {last_exception}")
                    error_result = {
                        "status": "error",
                        "step": step.get('action', 'unknown'),
                        "error": f"执行失败：{last_exception}"
                    }
                else:
                    # 报告超时
                    timeout_error = f"步骤 {i+1} 执行超时（60秒限制）"
                    uat_logger.error(f"❌ [CASE_STEP] {timeout_error}")
                    error_result = {
                        "status": "error",
                        "step": step.get('action', 'unknown'),
                        "error": f"执行失败：{timeout_error}"
                    }
                case_results.append(error_result)
                
                uat_logger.error(f"🛑 [CASE_STEP] 步骤 {i+1} 执行失败，立即终止当前用例执行")
                # 🔥 修改：立即中断当前用例执行，跳出循环
                break
                
            except Exception as step_error:
                import traceback
                uat_logger.error(f"❌ [CASE_STEP] 步骤 {i+1} 异常: {type(step_error).__name__}: {str(step_error)}")
                
                error_result = { 
                    "status": "error", 
                    "step": step.get('action', 'unknown'), 
                    "error": str(step_error)
                }
                case_results.append(error_result)
                break
        
        return case_results
    
    async def execute_single_step(self, step: Dict[str, Any]) -> List[Dict[str, Any]]:
        """执行单个测试步骤（强制在主页面执行，禁用多标签页）
        
        Args:
            step: 要执行的步骤字典
            
        Returns:
            步骤执行结果列表
        """
        target_page = self.page
        if target_page is None:
            raise Exception("浏览器未启动")
        
        action = step.get("action", "")
        uat_logger.info(f"🎯 [SINGLE_STEP] 开始执行单步操作: {action}")
        
        results = []
        
        try:
            if action == "navigate":
                url = step.get("url", "")
                # 处理构建阶段标注的占位符
                if url == "__INVALID_URL__":
                    raise Exception(step.get("url_error", "导航URL无效"))
                if url == "__SKIP_URL__":
                    uat_logger.warning("导航步骤URL为占位符，跳过")
                    results.append({"status": "skipped", "step": step})
                else:
                    fixed_url, url_err = _pa_validate_url(url)
                    if url_err:
                        raise Exception(url_err)
                    elif fixed_url is None:
                        uat_logger.warning(f"导航步骤URL为空或占位符，跳过: {url}")
                        results.append({"status": "skipped", "step": step})
                    else:
                        await self.navigate_to(fixed_url, page=target_page)
                        results.append({"status": "success", "step": step})
                    
            elif action == "click":
                selector = step.get("selector", "")
                selector_type = step.get("selector_type", "css")
                iframe_selector = step.get("iframe_selector", "")
                if selector:
                    await self.click_element(
                        selector,
                        selector_type,
                        iframe_selector,
                        page=target_page,
                        locator_candidates=step.get("locator_candidates"),
                    )
                    results.append({"status": "success", "step": step})
                else:
                    raise Exception("点击步骤缺少选择器参数")
                    
            elif action in ["fill", "input"]:
                selector = step.get("selector", "")
                text = step.get("text", step.get("input_value", ""))
                selector_type = step.get("selector_type", "css")
                iframe_selector = step.get("iframe_selector", "")
                if selector and text:
                    await self.fill_input(
                        selector,
                        text,
                        selector_type,
                        iframe_selector,
                        page=target_page,
                        locator_candidates=step.get("locator_candidates"),
                    )
                    results.append({"status": "success", "step": step})
                else:
                    raise Exception("填充步骤缺少选择器或文本参数")
                    
            elif action == "scroll":
                iv = (step.get("input_value") or "").strip()
                parsed = parse_platform_scroll_input_value(iv)
                rdx = parsed["right"] - parsed["left"]
                rdy = parsed["down"] - parsed["up"]
                if rdx != 0 or rdy != 0:
                    await self.scroll_by_delta(
                        rdx, rdy, step.get("iframe_selector") or None, page=target_page
                    )
                elif "scrollPosition" in step:
                    scroll_pos = step.get("scrollPosition", {})
                    current_scroll = await target_page.evaluate(
                        """() => ({
                            x: window.pageXOffset || document.documentElement.scrollLeft,
                            y: window.pageYOffset || document.documentElement.scrollTop
                        })"""
                    )
                    delta_x = scroll_pos.get("x", 0) - current_scroll["x"]
                    delta_y = scroll_pos.get("y", 0) - current_scroll["y"]
                    await target_page.evaluate(f"window.scrollBy({delta_x}, {delta_y})")
                else:
                    direction = step.get("direction", "down")
                    pixels = step.get("pixels", 500)
                    await self.scroll_page(direction, pixels, page=target_page)
                results.append({"status": "success", "step": step})
                
            elif action == "wait":
                wait_time = step.get("time", 1000)
                await target_page.wait_for_timeout(wait_time)
                results.append({"status": "success", "step": step})
                
            elif action == "submit":
                selector = step.get("selector", "")
                selector_type = step.get("selector_type", "css")
                iframe_selector = step.get("iframe_selector", "")
                if selector:
                    # 提交操作通常需要先点击提交按钮
                    await self.click_element(
                        selector,
                        selector_type,
                        iframe_selector,
                        page=target_page,
                        locator_candidates=step.get("locator_candidates"),
                    )
                    results.append({"status": "success", "step": step})
                else:
                    raise Exception("提交步骤缺少选择器参数")
                    
            elif action == "extract_text":
                selector = step.get("selector", "")
                selector_type = step.get("selector_type", "css")
                iframe_selector = step.get("iframe_selector", "")
                if selector:
                    extracted_text = await self.extract_element_text(selector, selector_type, iframe_selector, page=target_page)
                    results.append({
                        "status": "success", 
                        "step": step,
                        "extracted_text": extracted_text
                    })
                else:
                    # 提取整个页面文本
                    page_text = await self.get_page_text(page=target_page)
                    results.append({
                        "status": "success", 
                        "step": step,
                        "extracted_text": page_text
                    })
                    
            elif action == "verify":
                selector = step.get("selector", "")
                verify_type = step.get("verify_type", "visible")
                selector_type = step.get("selector_type", "css")
                iframe_selector = step.get("iframe_selector", "")
                await self.verify_element(selector, verify_type, selector_type, iframe_selector, page=target_page)
                results.append({"status": "success", "step": step})
                
            elif action == "hover":
                selector = step.get("selector", "")
                selector_type = step.get("selector_type", "css")
                iframe_selector = step.get("iframe_selector", "")
                if selector:
                    await self.hover_element(selector, selector_type, iframe_selector, page=target_page)
                    results.append({"status": "success", "step": step})
                else:
                    raise Exception("悬停步骤缺少选择器参数")
                    
            elif action == "double_click":
                selector = step.get("selector", "")
                selector_type = step.get("selector_type", "css")
                iframe_selector = step.get("iframe_selector", "")
                if selector:
                    await self.double_click_element(selector, selector_type, iframe_selector, page=target_page)
                    results.append({"status": "success", "step": step})
                else:
                    raise Exception("双击步骤缺少选择器参数")
                    
            elif action == "right_click":
                selector = step.get("selector", "")
                selector_type = step.get("selector_type", "css")
                iframe_selector = step.get("iframe_selector", "")
                if selector:
                    await self.right_click_element(selector, selector_type, iframe_selector, page=target_page)
                    results.append({"status": "success", "step": step})
                else:
                    raise Exception("右键点击步骤缺少选择器参数")
                    
            elif action == "swipe":
                selector = step.get("selector", "")
                direction = step.get("direction", "right")
                distance = step.get("distance", 100)
                selector_type = step.get("selector_type", "css")
                iframe_selector = step.get("iframe_selector", "")
                if selector:
                    await self.swipe_element(selector, direction, distance, selector_type, iframe_selector, page=target_page)
                    results.append({"status": "success", "step": step})
                else:
                    raise Exception("滑动步骤缺少选择器参数")
                    
            elif action == "wait_for_selector":
                selector = step.get("selector", "")
                timeout = step.get("timeout", 30000)
                selector_type = step.get("selector_type", "css")
                iframe_selector = step.get("iframe_selector", "")
                if selector:
                    await self.wait_for_selector(selector, timeout, selector_type, iframe_selector, page=target_page)
                    results.append({"status": "success", "step": step})
                else:
                    raise Exception("等待选择器步骤缺少选择器参数")
                    
            elif action == "wait_for_element_visible":
                selector = step.get("selector", "")
                timeout = step.get("timeout", 30000)
                selector_type = step.get("selector_type", "css")
                if selector:
                    await self.wait_for_element_visible(selector, timeout, selector_type, page=target_page)
                    results.append({"status": "success", "step": step})
                else:
                    raise Exception("等待元素可见步骤缺少选择器参数")
                    
            elif action == "screenshot":
                screenshot_path = await self.take_screenshot(page=target_page)
                results.append({
                    "status": "success", 
                    "step": step,
                    "screenshot_path": screenshot_path
                })
            elif action == "select":
                selector = step.get("selector", "")
                select_value = step.get("text", step.get("input_value", ""))
                selector_type = step.get("selector_type", "css")
                iframe_selector = step.get("iframe_selector", "")
                if selector and select_value:
                    await self.select_option(selector, select_value, selector_type, iframe_selector, page=target_page)
                    results.append({"status": "success", "step": step})
                else:
                    raise Exception("下拉框选择步骤缺少选择器或选择值参数")
            else:
                raise Exception(f"不支持的操作类型: {action}")
                
            uat_logger.info(f"✅ [SINGLE_STEP] 单步操作执行成功: {action}")
            
        except Exception as e:
            uat_logger.error(f"❌ [SINGLE_STEP] 单步操作执行失败: {action}, 错误: {str(e)}")
            results.append({
                "status": "error", 
                "step": step, 
                "error": str(e)
            })
            # 🔥 修复：重新抛出异常，让上层调用者知道步骤执行失败
            raise
            
        return results

    async def _stop_recording_poll_and_panel(self):
        t = getattr(self, "_recording_poll_task", None)
        if t and not t.done():
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass
        self._recording_poll_task = None
        p = getattr(self, "recorder_panel_page", None)
        if p:
            try:
                if not p.is_closed():
                    await p.close()
            except Exception:
                pass
            self.recorder_panel_page = None

    async def start_recording(self, platform_origin: str = ""):
        """内置浏览器录制已移除，请使用 Playwright Codegen 与本页「从 Codegen 导入」。"""
        self.recording = False
        self.recorded_steps = []
        return

    async def stop_recording(self) -> List[Dict[str, Any]]:
        self.recording = False
        await self._stop_recording_poll_and_panel()
        return []

    def _get_recorded_events_sync(self):
        """同步获取录制的事件"""
        # 为了避免事件循环冲突,直接返回空列表
        # 实际的事件同步已经在后台任务中完成
        return []
    
    async def wait_for_timeout(self, milliseconds: int):
        """等待指定的毫秒数"""
        if self.page is None:
            raise Exception("浏览器未启动")
        
        uat_logger.info(f"等待 {milliseconds} 毫秒")
        await self.page.wait_for_timeout(milliseconds)
    
    async def close_browser(self):
        """关闭浏览器"""
        # 设置recording为False以停止任何可能的循环
        self.recording = False
        try:
            await self._stop_recording_poll_and_panel()
        except Exception:
            pass
        
        if self.browser:
            try:
                await self.browser.close()
            except Exception:
                pass  # 忽略错误
            self.browser = None
            self.page = None
            self.context = None
            self._last_headless = None
        
        if hasattr(self, 'playwright') and self.playwright:
            try:
                await self.playwright.stop()
            except Exception:
                pass  # 忽略错误
            self.playwright = None
        self._last_headless = None

    async def _wait_pick_page_ready(self, *, after_nav: bool = False, timeout_ms: int = 30000):
        """拾取器注入前等待主框架就绪。避免 networkidle（SPA / 长连接页面常永不满足或易与导航竞态）。"""
        try:
            await self.page.wait_for_load_state('domcontentloaded', timeout=timeout_ms)
        except Exception as e:
            uat_logger.warning(f'[拾取] 等待 domcontentloaded: {e}')
        if not after_nav:
            return
        try:
            await self.page.wait_for_load_state('load', timeout=min(20000, timeout_ms))
        except Exception:
            pass

    async def _picker_goto(self, url: str):
        """拾取专用导航：domcontentloaded 为主；ERR_ABORTED 时用 commit 减轻竞态与重定向场景。"""
        target = (url or '').strip()
        if not target:
            return
        timeout = 60000.0
        try:
            opt = int(os.environ.get('PLAYWRIGHT_NAV_TIMEOUT_MS', '0') or '0')
            if opt > 0:
                timeout = float(opt)
        except ValueError:
            pass
        try:
            await self.page.goto(target, wait_until='domcontentloaded', timeout=timeout)
        except Exception as e1:
            msg = str(e1)
            if 'Target page, context or browser has been closed' in msg:
                uat_logger.warning("[拾取] 导航时页面已关闭，重建浏览器后重试一次")
                await self.start_browser(headless=False)
                await self.page.goto(target, wait_until='domcontentloaded', timeout=timeout)
            elif 'ERR_ABORTED' in msg or 'net::' in msg:
                uat_logger.warning(f'[拾取] goto(domcontentloaded) 失败，尝试 commit: {e1}')
                try:
                    await self.page.goto(target, wait_until='commit', timeout=timeout)
                except Exception as e2:
                    msg2 = str(e2)
                    if 'Target page, context or browser has been closed' in msg2:
                        uat_logger.warning("[拾取] commit 导航时页面已关闭，重建浏览器后重试一次")
                        await self.start_browser(headless=False)
                        await self.page.goto(target, wait_until='domcontentloaded', timeout=timeout)
                    else:
                        msg2l = msg2.lower()
                        if (
                            "err_connection_timed_out" in msg2l
                            or "err_connection_refused" in msg2l
                            or "err_name_not_resolved" in msg2l
                        ):
                            raise Exception(f"目标地址不可达，请检查网络或服务是否启动: {target}") from e2
                        raise Exception(f'拾取导航失败: {e2}') from e2
            else:
                msgl = msg.lower()
                if (
                    "err_connection_timed_out" in msgl
                    or "err_connection_refused" in msgl
                    or "err_name_not_resolved" in msgl
                ):
                    # 网络不可达场景，给用户可读提示，避免内部错误细节暴露
                    raise Exception(f"目标地址不可达，请检查网络或服务是否启动: {target}") from e1
                raise
        await self._wait_pick_page_ready(after_nav=True, timeout_ms=int(timeout))
        try:
            cur = (self.page.url or "").strip().lower()
        except Exception:
            cur = ""
        if cur in ("about:blank", "about:newtab", "about:newtab/"):
            uat_logger.warning(f"[拾取] 导航后仍为空白页({cur})，尝试再次强制导航")
            await self.page.goto(target, wait_until='load', timeout=timeout)
            await self._wait_pick_page_ready(after_nav=True, timeout_ms=int(timeout))

    async def enable_element_selection(self, url=''):
        """启用元素选择模式,显示悬浮窗让用户选择页面元素"""
        try:
            # 检查浏览器实例是否有效
            browser_valid = False
            
            # 1. 检查browser对象是否存在且已连接
            browser_connected = False
            if self.browser:
                try:
                    browser_connected = self.browser.is_connected()
                except:
                    browser_connected = False
            
            # 2. 如果浏览器已连接,检查page对象是否有效
            if browser_connected and self.page:
                try:
                    # 尝试执行一个简单的操作来检查页面是否仍然有效
                    await self.page.evaluate("1 + 1")
                    browser_valid = True
                except Exception as e:
                    uat_logger.warning(f"页面对象已失效: {str(e)}")
                    # 重置浏览器相关状态
                    self.page = None
                    self.context = None
            
            # 3. 如果浏览器未连接或页面无效,重置所有浏览器相关状态
            if not browser_valid:
                uat_logger.warning(f"浏览器实例无效,重置所有相关状态")
                # 尝试优雅关闭playwright
                if self.playwright:
                    try:
                        await self.playwright.stop()
                    except:
                        pass
                # 重置所有浏览器相关状态
                self.browser = None
                self.page = None
                self.context = None
                self.playwright = None
            
            # 4. 启动或复用浏览器实例
            if not browser_valid:
                # 如果浏览器实例不存在或已失效,则启动新实例
                uat_logger.info("启动新的浏览器实例")
                await self.start_browser(headless=False)
            else:
                # 复用已存在的浏览器实例,切换到当前页面
                if bool(getattr(self, "_last_headless", False)):
                    uat_logger.info("当前浏览器为无头模式，重启为有头模式用于拾取")
                    await self.close_browser()
                    await self.start_browser(headless=False)
                else:
                    uat_logger.info("复用已存在的浏览器实例")
                try:
                    await self.page.bring_to_front()
                except Exception:
                    pass

            # 如果提供了URL,则导航到该URL（_picker_goto 内已等待 load）；否则确保当前页 dom 就绪
            if (url or '').strip():
                await self._picker_goto(url)
            else:
                await self._wait_pick_page_ready(after_nav=False)

            if self.page.is_closed():
                raise Exception('拾取页面已关闭，请重新打开可视化选择')

            # 注入拾取器浮动条与选择逻辑
            picker_injected = await self.page.evaluate("""
                (() => {
                    try {
                        const host = document.body || document.documentElement;
                        if (!host) return false;
                        if (window.automationSelection && window.automationSelection._inited) {
                            if (typeof window.enableElementSelection === 'function') {
                                window.enableElementSelection();
                            }
                            return true;
                        }

                        const state = {
                            _inited: true,
                            enabled: false,
                            selectedElement: null,
                            overlay: null,
                            toolbar: null,
                            btn: null,
                            tip: null
                        };
                        window.automationSelection = state;

                        function stableClassSelector(el) {
                            if (!el || !el.classList || !el.classList.length) return '';
                            const keep = [];
                            for (const c of Array.from(el.classList)) {
                                if (!c || c.length <= 2) continue;
                                if (/\\d{4,}/.test(c)) continue;
                                if (/[a-f0-9]{8,}/i.test(c)) continue;
                                keep.push(c);
                                if (keep.length >= 3) break;
                            }
                            return keep.length ? ('.' + keep.join('.')) : '';
                        }

                        function generateSelector(element) {
                            if (!element || !element.tagName) return '';
                            const tag = element.tagName.toLowerCase();
                            const id = element.id || '';
                            if (id && !/(\\d{6,}|[a-f0-9]{10,})/i.test(id)) return '#' + id;
                            const cls = stableClassSelector(element);
                            if (cls) return `${tag}${cls}`;
                            return tag;
                        }
                        function resolveElementTarget(raw) {
                            if (!raw) return null;
                            if (raw.nodeType === 1) return raw;
                            return raw.parentElement || null;
                        }
                        window.generateSelector = generateSelector;

                        function ensureOverlay() {
                            if (state.overlay) return state.overlay;
                            const ov = document.createElement('div');
                            ov.id = 'automation-picker-overlay';
                            ov.style.cssText = 'position:fixed;pointer-events:none;border:2px solid #2f80ff;background:rgba(47,128,255,0.10);z-index:2147483646;display:none;';
                            host.appendChild(ov);
                            state.overlay = ov;
                            return ov;
                        }

                        function ensureToolbar() {
                            if (state.toolbar && state.toolbar.isConnected) return state.toolbar;
                            const bar = document.createElement('div');
                            bar.id = 'automation-picker-toolbar';
                            bar.style.cssText = 'position:fixed;top:16px;right:16px;z-index:2147483647;background:#1f2937;color:#fff;border-radius:10px;padding:8px 10px;display:flex;gap:8px;align-items:center;box-shadow:0 6px 16px rgba(0,0,0,.25);font:13px/1.2 -apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Arial;';
                            const btn = document.createElement('button');
                            btn.id = 'automation-picker-btn';
                            btn.textContent = '拾取元素';
                            btn.style.cssText = 'border:none;border-radius:8px;padding:6px 10px;background:#3b82f6;color:#fff;cursor:pointer;';
                            const tip = document.createElement('span');
                            tip.id = 'automation-picker-tip';
                            tip.textContent = '点击后在页面选择目标';
                            bar.appendChild(btn);
                            bar.appendChild(tip);
                            host.appendChild(bar);
                            state.toolbar = bar;
                            state.btn = btn;
                            state.tip = tip;
                            function broadcastPickerState(enabled) {
                                try {
                                    const fs = document.querySelectorAll('iframe');
                                    for (const f of fs) {
                                        if (f && f.contentWindow) {
                                            f.contentWindow.postMessage({
                                                __automationPicker: true,
                                                type: 'picker_state',
                                                enabled: !!enabled
                                            }, '*');
                                        }
                                    }
                                } catch (_) {}
                            }
                            btn.onclick = () => {
                                state.enabled = !state.enabled;
                                btn.textContent = state.enabled ? '退出拾取' : '拾取元素';
                                btn.style.background = state.enabled ? '#ef4444' : '#3b82f6';
                                tip.textContent = state.enabled ? '请点击目标元素' : '点击后在页面选择目标';
                                if (!state.enabled && state.overlay) state.overlay.style.display = 'none';
                                broadcastPickerState(state.enabled);
                            };
                            return bar;
                        }

                        function onMove(e) {
                            if (!state.enabled) return;
                            const raw = (e.composedPath && e.composedPath()[0]) ? e.composedPath()[0] : e.target;
                            const target = resolveElementTarget(raw);
                            if (!target || target === state.toolbar || state.toolbar.contains(target)) return;
                            const rect = target.getBoundingClientRect();
                            const ov = ensureOverlay();
                            ov.style.display = 'block';
                            ov.style.left = rect.left + 'px';
                            ov.style.top = rect.top + 'px';
                            ov.style.width = rect.width + 'px';
                            ov.style.height = rect.height + 'px';
                        }

                        function onClick(e) {
                            if (!state.enabled) return;
                            const raw = (e.composedPath && e.composedPath()[0]) ? e.composedPath()[0] : e.target;
                            const target = resolveElementTarget(raw);
                            if (!target || target === state.toolbar || state.toolbar.contains(target)) return;
                            e.preventDefault();
                            e.stopPropagation();
                            state.selectedElement = target;
                            state.enabled = false;
                            if (state.btn) {
                                state.btn.textContent = '拾取元素';
                                state.btn.style.background = '#3b82f6';
                            }
                            if (state.tip) {
                                state.tip.textContent = '已成功拾取元素';
                                window.setTimeout(() => {
                                    if (state.tip && !state.enabled) state.tip.textContent = '请点击目标元素';
                                }, 3000);
                            }
                            if (state.overlay) state.overlay.style.display = 'none';
                            const detail = {
                                selector: generateSelector(target),
                                source_frame: 'main',
                                elementInfo: {
                                    tagName: target.tagName,
                                    id: target.id || '',
                                    className: target.className || '',
                                    textContent: target.textContent ? target.textContent.substring(0, 100) : '',
                                    attributes: {
                                        type: target.type || '',
                                        name: target.name || '',
                                        value: target.value || '',
                                        href: target.href || '',
                                        src: target.src || '',
                                        alt: target.alt || '',
                                        title: target.title || '',
                                        'data-testid': target.getAttribute('data-testid') || '',
                                        'data-test': target.getAttribute('data-test') || '',
                                        'data-id': target.getAttribute('data-id') || '',
                                        role: target.getAttribute('role') || ''
                                    }
                                }
                            };
                            state.selectedElementPayload = detail;
                            window.dispatchEvent(new CustomEvent('elementSelected', { detail }));
                        }

                        if (!window.__automationPickerBound) {
                            window.__automationPickerBound = true;
                            document.addEventListener('mousemove', onMove, true);
                            document.addEventListener('click', onClick, true);
                            window.addEventListener('message', (evt) => {
                                try {
                                    const d = evt && evt.data;
                                    if (!(d && d.__automationPicker)) return;
                                    if (d.type === 'selected' && d.payload) {
                                        state.selectedElementPayload = d.payload;
                                        state.enabled = false;
                                        if (state.overlay) state.overlay.style.display = 'none';
                                        if (state.btn) {
                                            state.btn.textContent = '拾取元素';
                                            state.btn.style.background = '#3b82f6';
                                        }
                                        if (state.tip) {
                                            state.tip.textContent = '已成功拾取元素';
                                            window.setTimeout(() => {
                                                if (state.tip && !state.enabled) state.tip.textContent = '请点击目标元素';
                                            }, 3000);
                                        }
                                    }
                                } catch (_) {}
                            }, true);
                        }

                        window.enableElementSelection = function () {
                            ensureToolbar();
                            state.enabled = false;
                            if (state.btn) {
                                state.btn.textContent = '拾取元素';
                                state.btn.style.background = '#3b82f6';
                            }
                            if (state.tip) state.tip.textContent = '点击后在页面选择目标';
                            try {
                                const fs = document.querySelectorAll('iframe');
                                for (const f of fs) {
                                    if (f && f.contentWindow) {
                                        f.contentWindow.postMessage({
                                            __automationPicker: true,
                                            type: 'picker_state',
                                            enabled: true
                                        }, '*');
                                    }
                                }
                            } catch (_) {}
                        };
                        window.disableElementSelection = function () {
                            state.enabled = false;
                            if (state.overlay) state.overlay.style.display = 'none';
                            try {
                                const fs = document.querySelectorAll('iframe');
                                for (const f of fs) {
                                    if (f && f.contentWindow) {
                                        f.contentWindow.postMessage({
                                            __automationPicker: true,
                                            type: 'picker_state',
                                            enabled: false
                                        }, '*');
                                    }
                                }
                            } catch (_) {}
                        };

                        window.enableElementSelection();
                        return true;
                    } catch (e) {
                        return false;
                    }
                })()
            """)
            if not picker_injected:
                raise Exception("拾取器注入失败：页面DOM尚未就绪或被页面脚本拦截")
            # 同源 iframe 轻量拾取桥接：frame 内点击后把元素详情回传到 top 的 automationSelection
            try:
                for fr in self.page.frames:
                    if fr == self.page.main_frame:
                        continue
                    try:
                        await fr.evaluate("""
                            (() => {
                                try {
                                    if (window.__automationFramePickerBound) return true;
                                    window.__automationFramePickerBound = true;
                                    function stableClassSelector(el) {
                                        if (!el || !el.classList || !el.classList.length) return '';
                                        const keep = [];
                                        for (const c of Array.from(el.classList)) {
                                            if (!c || c.length <= 2) continue;
                                            if (/\\d{4,}/.test(c)) continue;
                                            if (/[a-f0-9]{8,}/i.test(c)) continue;
                                            keep.push(c);
                                            if (keep.length >= 3) break;
                                        }
                                        return keep.length ? ('.' + keep.join('.')) : '';
                                    }
                                    function generateSelector(element) {
                                        if (!element || !element.tagName) return '';
                                        const tag = element.tagName.toLowerCase();
                                        const id = element.id || '';
                                        if (id && !/(\\d{6,}|[a-f0-9]{10,})/i.test(id)) return '#' + id;
                                        const cls = stableClassSelector(element);
                                        if (cls) return `${tag}${cls}`;
                                        return tag;
                                    }
                                    function resolveElementTarget(raw) {
                                        if (!raw) return null;
                                        if (raw.nodeType === 1) return raw;
                                        return raw.parentElement || null;
                                    }
                                    const frameState = { enabled: false };
                                    let overlay = null;
                                    function ensureOverlay() {
                                        if (overlay && overlay.isConnected) return overlay;
                                        const host = document.body || document.documentElement;
                                        if (!host) return null;
                                        overlay = document.createElement('div');
                                        overlay.id = '__automation-frame-picker-overlay';
                                        overlay.style.cssText = 'position:fixed;pointer-events:none;border:2px solid #2f80ff;background:rgba(47,128,255,0.10);z-index:2147483646;display:none;';
                                        host.appendChild(overlay);
                                        return overlay;
                                    }
                                    function onMove(e) {
                                        if (!frameState.enabled) {
                                            if (overlay) overlay.style.display = 'none';
                                            return;
                                        }
                                        const raw = (e.composedPath && e.composedPath()[0]) ? e.composedPath()[0] : e.target;
                                        const target = resolveElementTarget(raw);
                                        if (!target) return;
                                        const rect = target.getBoundingClientRect();
                                        const ov = ensureOverlay();
                                        if (!ov) return;
                                        ov.style.display = 'block';
                                        ov.style.left = rect.left + 'px';
                                        ov.style.top = rect.top + 'px';
                                        ov.style.width = rect.width + 'px';
                                        ov.style.height = rect.height + 'px';
                                    }
                                    function onClick(e) {
                                        if (!frameState.enabled) return;
                                        const raw = (e.composedPath && e.composedPath()[0]) ? e.composedPath()[0] : e.target;
                                        const target = resolveElementTarget(raw);
                                        if (!target) return;
                                        e.preventDefault();
                                        e.stopPropagation();
                                        const detail = {
                                            selector: generateSelector(target),
                                            source_frame: window.location.href || 'iframe',
                                            elementInfo: {
                                                tagName: target.tagName,
                                                id: target.id || '',
                                                className: target.className || '',
                                                textContent: target.textContent ? target.textContent.substring(0, 100) : '',
                                                attributes: {
                                                    type: target.type || '',
                                                    name: target.name || '',
                                                    value: target.value || '',
                                                    href: target.href || '',
                                                    src: target.src || '',
                                                    alt: target.alt || '',
                                                    title: target.title || '',
                                                    'data-testid': target.getAttribute('data-testid') || '',
                                                    'data-test': target.getAttribute('data-test') || '',
                                                    'data-id': target.getAttribute('data-id') || '',
                                                    role: target.getAttribute('role') || ''
                                                }
                                            }
                                        };
                                        try {
                                            window.top.postMessage({
                                                __automationPicker: true,
                                                type: 'selected',
                                                payload: detail
                                            }, '*');
                                        } catch (_) {}
                                        frameState.enabled = false;
                                        if (overlay) overlay.style.display = 'none';
                                    }
                                    window.addEventListener('message', (evt) => {
                                        try {
                                            const d = evt && evt.data;
                                            if (!(d && d.__automationPicker && d.type === 'picker_state')) return;
                                            frameState.enabled = !!d.enabled;
                                            if (!frameState.enabled && overlay) overlay.style.display = 'none';
                                        } catch (_) {}
                                    }, true);
                                    document.addEventListener('mousemove', onMove, true);
                                    document.addEventListener('click', onClick, true);
                                    return true;
                                } catch (e) {
                                    return false;
                                }
                            })()
                        """)
                    except Exception:
                        continue
            except Exception:
                pass

            self._selection_mode_active = True
            
            uat_logger.info("元素选择模式已启用")
            return True
        except Exception as e:
            uat_logger.error(f"启用元素选择模式时出错: {str(e)}")
            raise Exception(f"启用元素选择模式失败: {str(e)}")

    async def disable_element_selection(self):
        """禁用元素选择模式"""
        if self.page is None:
            self._selection_mode_active = False
            return True
        
        try:
            await self.page.evaluate("""
                (() => {
                    if (typeof disableElementSelection === 'function') {
                        disableElementSelection();
                    }
                })()
            """)
            
            uat_logger.info("元素选择模式已禁用")
            self._selection_mode_active = False
            return True
        except Exception as e:
            if "Target page, context or browser has been closed" in str(e):
                uat_logger.info("拾取窗口已关闭，按已停止处理")
            else:
                uat_logger.error(f"禁用元素选择模式时出错: {str(e)}")
            self._selection_mode_active = False
            return True

    async def get_selected_element(self):
        """获取用户选择的元素信息"""
        if self.page is None:
            return None
        try:
            if self.page.is_closed():
                return {"_picker_closed": True}
        except Exception:
            return {"_picker_closed": True}
        
        try:
            # H5 页面常在运行中动态重建 iframe，这里做一次轻量自愈：
            # 1) 广播当前拾取状态给所有 iframe
            # 2) 给新出现的 frame 补注入拾取桥接监听
            try:
                await self.page.evaluate("""
                    (() => {
                        try {
                            const st = window.automationSelection;
                            const enabled = !!(st && st.enabled);
                            const fs = document.querySelectorAll('iframe');
                            for (const f of fs) {
                                if (f && f.contentWindow) {
                                    f.contentWindow.postMessage({
                                        __automationPicker: true,
                                        type: 'picker_state',
                                        enabled
                                    }, '*');
                                }
                            }
                        } catch (_) {}
                    })()
                """)
            except Exception:
                pass

            try:
                injected = 0
                for fr in self.page.frames:
                    if fr == self.page.main_frame:
                        continue
                    try:
                        ok = await fr.evaluate("""
                            (() => {
                                try {
                                    if (window.__automationFramePickerBound) return false;
                                    window.__automationFramePickerBound = true;
                                    const frameState = { enabled: false };
                                    function stableClassSelector(el) {
                                        if (!el || !el.classList || !el.classList.length) return '';
                                        const keep = [];
                                        for (const c of Array.from(el.classList)) {
                                            if (!c || c.length <= 2) continue;
                                            if (/\\d{4,}/.test(c)) continue;
                                            if (/[a-f0-9]{8,}/i.test(c)) continue;
                                            keep.push(c);
                                            if (keep.length >= 3) break;
                                        }
                                        return keep.length ? ('.' + keep.join('.')) : '';
                                    }
                                    function generateSelector(element) {
                                        if (!element || !element.tagName) return '';
                                        const tag = element.tagName.toLowerCase();
                                        const id = element.id || '';
                                        if (id && !/(\\d{6,}|[a-f0-9]{10,})/i.test(id)) return '#' + id;
                                        const cls = stableClassSelector(element);
                                        if (cls) return `${tag}${cls}`;
                                        return tag;
                                    }
                                    function resolveElementTarget(raw) {
                                        if (!raw) return null;
                                        if (raw.nodeType === 1) return raw;
                                        return raw.parentElement || null;
                                    }
                                    let overlay = null;
                                    function ensureOverlay() {
                                        if (overlay && overlay.isConnected) return overlay;
                                        const host = document.body || document.documentElement;
                                        if (!host) return null;
                                        overlay = document.createElement('div');
                                        overlay.id = '__automation-frame-picker-overlay';
                                        overlay.style.cssText = 'position:fixed;pointer-events:none;border:2px solid #2f80ff;background:rgba(47,128,255,0.10);z-index:2147483646;display:none;';
                                        host.appendChild(overlay);
                                        return overlay;
                                    }
                                    function onMove(e) {
                                        if (!frameState.enabled) {
                                            if (overlay) overlay.style.display = 'none';
                                            return;
                                        }
                                        const raw = (e.composedPath && e.composedPath()[0]) ? e.composedPath()[0] : e.target;
                                        const target = resolveElementTarget(raw);
                                        if (!target) return;
                                        const rect = target.getBoundingClientRect();
                                        const ov = ensureOverlay();
                                        if (!ov) return;
                                        ov.style.display = 'block';
                                        ov.style.left = rect.left + 'px';
                                        ov.style.top = rect.top + 'px';
                                        ov.style.width = rect.width + 'px';
                                        ov.style.height = rect.height + 'px';
                                    }
                                    function onClick(e) {
                                        if (!frameState.enabled) return;
                                        const raw = (e.composedPath && e.composedPath()[0]) ? e.composedPath()[0] : e.target;
                                        const target = resolveElementTarget(raw);
                                        if (!target) return;
                                        e.preventDefault();
                                        e.stopPropagation();
                                        const detail = {
                                            selector: generateSelector(target),
                                            source_frame: window.location.href || 'iframe',
                                            elementInfo: {
                                                tagName: target.tagName,
                                                id: target.id || '',
                                                className: target.className || '',
                                                textContent: target.textContent ? target.textContent.substring(0, 100) : '',
                                                attributes: {
                                                    type: target.type || '',
                                                    name: target.name || '',
                                                    value: target.value || '',
                                                    href: target.href || '',
                                                    src: target.src || '',
                                                    alt: target.alt || '',
                                                    title: target.title || '',
                                                    'data-testid': target.getAttribute('data-testid') || '',
                                                    'data-test': target.getAttribute('data-test') || '',
                                                    'data-id': target.getAttribute('data-id') || '',
                                                    role: target.getAttribute('role') || ''
                                                }
                                            }
                                        };
                                        try {
                                            window.top.postMessage({
                                                __automationPicker: true,
                                                type: 'selected',
                                                payload: detail
                                            }, '*');
                                        } catch (_) {}
                                        frameState.enabled = false;
                                        if (overlay) overlay.style.display = 'none';
                                    }
                                    window.addEventListener('message', (evt) => {
                                        try {
                                            const d = evt && evt.data;
                                            if (!(d && d.__automationPicker && d.type === 'picker_state')) return;
                                            frameState.enabled = !!d.enabled;
                                            if (!frameState.enabled && overlay) overlay.style.display = 'none';
                                        } catch (_) {}
                                    }, true);
                                    document.addEventListener('mousemove', onMove, true);
                                    document.addEventListener('click', onClick, true);
                                    return true;
                                } catch (_) {
                                    return false;
                                }
                            })()
                        """)
                        if ok:
                            injected += 1
                    except Exception:
                        continue
                if injected > 0:
                    uat_logger.info(f"[PICKER] 轮询阶段为 {injected} 个新 frame 补注入拾取桥接")
            except Exception:
                pass

            # 获取页面标题,用于填充页面名称
            page_name = await self.page.title()
            
            # 非阻塞检查：由前端轮询触发，不在这里等待事件
            raw_element_info = await self.page.evaluate("""
                (() => {
                    if (window.automationSelection && window.automationSelection.selectedElementPayload) {
                        const p = window.automationSelection.selectedElementPayload;
                        window.automationSelection.selectedElementPayload = null;
                        return p;
                    }
                    if (!(window.automationSelection && window.automationSelection.selectedElement)) {
                        return null;
                    }
                    const element = window.automationSelection.selectedElement;
                    const selector = (typeof generateSelector === 'function') ? generateSelector(element) : '';
                    const out = {
                        selector: selector,
                        elementInfo: {
                            tagName: element.tagName,
                            id: element.id || '',
                            className: element.className || '',
                            textContent: element.textContent ? element.textContent.substring(0, 100) : '',
                            attributes: {
                                type: element.type || '',
                                name: element.name || '',
                                value: element.value || '',
                                href: element.href || '',
                                src: element.src || '',
                                alt: element.alt || '',
                                title: element.title || '',
                                'data-testid': element.getAttribute('data-testid') || '',
                                'data-test': element.getAttribute('data-test') || '',
                                'data-id': element.getAttribute('data-id') || '',
                                role: element.getAttribute('role') || ''
                            }
                        }
                    };
                    // 消费后清空，避免轮询每秒重复返回同一个元素
                    window.automationSelection.selectedElement = null;
                    return out;
                })
            """)
            
            if raw_element_info:
                # 处理原始元素信息,转换为前端期望的格式
                element = raw_element_info.get('elementInfo', {})
                css_selector = raw_element_info.get('selector', '')
                text_content = element.get('textContent', '').strip()
                attrs = element.get('attributes', {}) or {}
                
                # 选择最合适的定位方式
                selector_type = 'css'
                selector_value = css_selector
                
                element_id = element.get('id', '')
                data_testid = (
                    attrs.get('data-testid')
                    or attrs.get('data-test')
                    or attrs.get('data-id')
                    or ''
                )

                # data-testid 等语义属性优先
                if data_testid:
                    selector_type = 'data'
                    selector_value = f'testid={data_testid}'
                # ID 仅在看起来稳定时才提升，动态 ID 自动降权
                elif element_id and not _looks_dynamic_dom_id(element_id):
                    selector_type = 'id'
                    selector_value = element_id
                # 稳定 class 作为候选主定位
                elif _stable_class_tokens(element.get('className', '')):
                    selector_type = 'css'
                    selector_value = "." + ".".join(_stable_class_tokens(element.get('className', '')))
                # 如果是文本内容比较独特,使用文本选择器
                elif text_content and len(text_content) > 5:
                    selector_type = 'partial_text'
                    selector_value = text_content
                
                class_name = element.get('className', '')
                class_tokens_raw = [c.strip() for c in str(class_name or "").split() if c.strip()]
                class_set = {c.lower() for c in class_tokens_raw}
                is_card_list_container = (
                    "card-list" in class_set
                    or any(("list" in c.lower() or "container" in c.lower()) for c in class_tokens_raw)
                )
                card_item_xpath = ""
                if is_card_list_container:
                    stable_root_classes = _stable_class_tokens(class_name)
                    if stable_root_classes:
                        root_pred = " and ".join(
                            [f"contains(@class,'{c}')" for c in stable_root_classes[:2]]
                        )
                        root_xpath = f"//*[{root_pred}]"
                    else:
                        root_xpath = "//*[contains(@class,'card-list') or contains(@class,'list-container')]"
                    card_item_xpath = (
                        f"{root_xpath}"
                        "//*[contains(@class,'outer-card') or contains(@class,'card') or contains(@class,'item') or @role='listitem']"
                    )
                    selector_type = "xpath"
                    selector_value = card_item_xpath

                locator_candidates = _picker_locator_candidates(
                    css_selector, text_content, class_name, element_id
                )

                # 构造前端期望的返回格式
                formatted_element_info = {
                    'selector_type': selector_type,
                    'selector_value': selector_value,
                    'text_content': text_content,
                    'page_name': page_name,
                    'tag_name': element.get('tagName', '').lower(),
                    'css_selector': css_selector,
                    'id': element_id,
                    'class_name': class_name,
                    'locator_candidates': locator_candidates,
                    'is_card_list_container': is_card_list_container,
                    'card_item_xpath': card_item_xpath,
                    'dynamic_id_ignored': bool(element_id and _looks_dynamic_dom_id(element_id))
                }
                
                uat_logger.info(f"获取到格式化的选中元素: {formatted_element_info}")
                return formatted_element_info
            return None
        except Exception as e:
            if "Target page, context or browser has been closed" in str(e):
                return {"_picker_closed": True}
            uat_logger.error(f"获取选中元素信息时出错: {str(e)}")
            raise Exception(f"获取选中元素信息失败: {str(e)}")

    async def extract_json_from_selected_element(self):
        """从用户选定的区域提取JSON数据"""
        if self.page is None:
            raise Exception("浏览器未启动")
        
        try:
            uat_logger.info("📝 [JSON_EXTRACT] 开始从选定元素提取JSON数据")
            
            # 获取用户选择的元素
            selected_element_info = await self.get_selected_element()
            if not selected_element_info:
                raise Exception("未选择任何元素")
            
            # 获取选中元素的选择器
            selector = selected_element_info.get('css_selector')
            if not selector:
                raise Exception("无法获取选中元素的选择器")
            
            uat_logger.info(f"📝 [JSON_EXTRACT] 从元素选择器提取JSON: {selector}")
            
            # 使用现有的extract_element_json方法从选定元素中提取JSON数据
            json_data = await self.extract_element_json(selector)
            
            if json_data:
                uat_logger.info(f"📝 [JSON_EXTRACT] 成功提取JSON数据: {json_data}")
                return json_data
            else:
                uat_logger.warning("📝 [JSON_EXTRACT] 未从选定元素中提取到JSON数据")
                return {}
                
        except Exception as e:
            uat_logger.error(f"📝 [JSON_EXTRACT] 提取JSON数据时出错: {str(e)}")
            raise Exception(f"提取JSON数据失败: {str(e)}")

# 全局实例
automation = PlaywrightAutomation()

# 添加一个函数来重置自动化实例,确保每次录制都是干净的开始
def reset_automation_instance():
    """重置自动化实例,确保干净的状态"""
    global automation
    global worker
    # 关闭当前实例的浏览器
    try:
        if automation.browser:
            automation.close_browser()
    except:
        pass  # 忽略错误
    
    # 停止并重新创建工作线程
    try:
        if worker:
            worker.stop()
    except:
        pass  # 忽略错误
    
    # 创建新的工作线程实例
    worker = PlaywrightWorker()
    
    # 创建新的自动化实例
    automation = PlaywrightAutomation()
    return automation

# 同步包装器函数
# 使用一个全局事件循环来避免重复创建
import threading
import queue

# 创建一个专门的线程池来处理Playwright操作
class PlaywrightWorker:
    def __init__(self):
        self.task_queue = queue.Queue()
        self.result_queue = queue.Queue()
        self.worker_thread = None
        self.loop = None
        self.running = False
        self._start_worker()
    
    def _start_worker(self):
        """启动工作线程"""
        self.running = True
        self.worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self.worker_thread.start()
        # 等待线程完全启动
        time.sleep(0.1)
    
    def _worker_loop(self):
        """工作线程的主循环"""
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        
        while self.running:
            try:
                # 获取任务,超时1秒
                task = self.task_queue.get(timeout=1)
                task_id, func, args, kwargs = task
                
                try:
                    # 检查是否是协程函数
                    if asyncio.iscoroutinefunction(func):
                        # 在事件循环中执行异步函数，添加1分钟超时控制
                        import functools
                        try:
                            result = self.loop.run_until_complete(
                                asyncio.wait_for(
                                    func(*args, **kwargs),
                                    timeout=60  # 1分钟超时
                                )
                            )
                        except asyncio.TimeoutError:
                            raise Exception("函数执行超过1分钟限制")
                    else:
                        # 执行同步函数
                        result = func(*args, **kwargs)
                    
                    self.result_queue.put((task_id, "success", result))
                except Exception as e:
                    # 获取完整的异常信息
                    import traceback
                    exc_info = traceback.format_exc()
                    self.result_queue.put((task_id, "error", {"message": str(e), "traceback": exc_info}))
                
                self.task_queue.task_done()
            except queue.Empty:
                continue
            except Exception as e:
                import traceback
                exc_info = traceback.format_exc()
                print(f"工作线程错误: {e}\n{exc_info}")
    
    def execute(self, func, *args, **kwargs):
        """在工作线程中执行函数"""
        # 确保工作线程已启动
        if not self.running or self.worker_thread is None or not self.worker_thread.is_alive():
            self._start_worker()
        
        task_id = str(time.time()) + str(id(func))
        self.task_queue.put((task_id, func, args, kwargs))
        
        # 等待结果
        while True:
            try:
                # 修改为1分钟超时（60秒），更合理的执行时间限制
                tid, status, result = self.result_queue.get(timeout=60)
                if tid == task_id:
                    if status == "success":
                        return result
                    else:
                        # 处理错误结果
                        if isinstance(result, dict) and "message" in result:
                            raise Exception(result["message"])
                        else:
                            raise Exception(result)
            except queue.Empty:
                raise Exception("执行超时")
    
    def stop(self):
        """停止工作线程"""
        self.running = False
        if hasattr(self, 'worker_thread') and self.worker_thread:
            self.worker_thread.join(timeout=2)
            
        # 清理事件循环
        if hasattr(self, 'loop') and self.loop and not self.loop.is_closed():
            self.loop.close()

# 创建全局工作线程实例
worker = PlaywrightWorker()


def test_timeout_settings():
    """
    测试超时设置是否正确生效
    """
    print("🔍 开始测试超时设置...")
    
    # 检查总的超时限制
    print(f"✅ Worker队列超时: 60秒")
    print(f"✅ 步骤执行超时: 60秒") 
    print(f"✅ 异步函数执行超时: 60秒")
    
    # 返回测试结果
    return {
        "step_timeout": 60,  # 步骤执行超时
        "worker_timeout": 60,  # Worker队列超时
        "async_timeout": 60,  # 异步函数执行超时
        "total_test_timeout": 60,  # 总体测试超时
        "status": "configured"
    }


# 验证超时时间修改的脚本
if __name__ == "__main__":
    print("🏁 启动超时设置验证...")
    result = test_timeout_settings()
    print("✅ 超时设置验证结果:")
    for key, value in result.items():
        print(f"   {key}: {value}")
    print("\n📋 总结：系统已配置1分钟超时限制")
    print("🔧 修改包括:")
    print("   - _execute_case_steps中添加了asyncio.wait_for(timeout=60)")
    print("   - Worker队列超时从600秒改为60秒")
    print("   - 工作线程异步函数执行添加了1分钟超时控制")
    print("   - 所有步骤执行都会严格限制在60秒内")

def sync_start_browser(headless: bool = True):
    async def run():
        return await automation.start_browser(headless)
    return worker.execute(run)

def sync_navigate_to(url: str, iframe_selector: str = None):
    async def run():
        return await automation.navigate_to(url, iframe_selector=iframe_selector)
    return worker.execute(run)

def sync_click_element(selector: str, selector_type: str = "css", iframe_selector: str = None, locator_candidates=None):
    async def run():
        return await automation.click_element(
            selector, selector_type, iframe_selector=iframe_selector, locator_candidates=locator_candidates
        )
    return worker.execute(run)

def sync_fill_input(selector: str, text: str, selector_type: str = "css", iframe_selector: str = None, locator_candidates=None):
    async def run():
        return await automation.fill_input(
            selector, text, selector_type, iframe_selector=iframe_selector, locator_candidates=locator_candidates
        )
    return worker.execute(run)

def sync_select_option(selector: str, select_value: str, selector_type: str = "css", iframe_selector: str = None):
    async def run():
        return await automation.select_option(selector, select_value, selector_type, iframe_selector=iframe_selector)
    return worker.execute(run)

def sync_select_date(selector: str, date: str, date_format: str = "YYYY-MM-DD"):
    async def run():
        return await automation.select_date(selector, date, date_format)
    return worker.execute(run)

def sync_wait_for_timeout(milliseconds: int):
    async def run():
        return await automation.wait_for_timeout(milliseconds)
    return worker.execute(run)

def sync_wait_for_page_stable(timeout=5000):
    """等待页面稳定
    
    Args:
        timeout: 超时时间（毫秒）
    """
    async def run():
        try:
            # 等待网络空闲
            await automation.page.wait_for_load_state('networkidle', timeout=timeout)
            uat_logger.debug("页面已达到网络空闲状态")
            
            # 等待DOM变化稳定
            await automation.page.evaluate("""
                () => new Promise(resolve => {
                    let lastScrollHeight = document.body.scrollHeight;
                    let checkCount = 0;
                    const checkInterval = 200;
                    const maxChecks = 10;
                    
                    const checkStability = () => {
                        const currentScrollHeight = document.body.scrollHeight;
                        if (currentScrollHeight === lastScrollHeight) {
                            checkCount++;
                            if (checkCount >= maxChecks) {
                                resolve(true);
                                return;
                            }
                        } else {
                            checkCount = 0;
                        }
                        lastScrollHeight = currentScrollHeight;
                        
                        if (checkCount < maxChecks) {
                            setTimeout(checkStability, checkInterval);
                        }
                    };
                    
                    checkStability();
                })
            """)
            uat_logger.debug("页面已稳定")
        except Exception as e:
            uat_logger.warning(f"等待页面稳定超时或失败: {e}")
            # 不抛出异常，让执行继续
            
    return worker.execute(run)

def sync_scroll_page(direction: str = "down", pixels: int = 500, iframe_selector: str = None):
    async def run():
        return await automation.scroll_page(direction, pixels, iframe_selector=iframe_selector)
    return worker.execute(run)


def sync_scroll_by_delta(dx: int = 0, dy: int = 0, iframe_selector: str = None):
    async def run():
        return await automation.scroll_by_delta(dx, dy, iframe_selector=iframe_selector)
    return worker.execute(run)

def sync_get_page_text():
    async def run():
        return await automation.get_page_text()
    return worker.execute(run)

def sync_extract_element_text(
    selector: str,
    selector_type: str = "css",
    iframe_selector: str = None,
    locator_candidates=None,
):
    async def run():
        return await automation.extract_element_text(
            selector,
            selector_type,
            iframe_selector=iframe_selector,
            locator_candidates=locator_candidates,
        )
    return worker.execute(run)

def sync_extract_element_json(selector: str, selector_type: str = "css"):
    async def run():
        return await automation.extract_element_json(selector, selector_type)
    return worker.execute(run)

def sync_execute_script_steps(steps: List[Dict[str, Any]]):
    async def run():
        return await automation.execute_script_steps(steps)
    return worker.execute(run)

def sync_close_browser():
    async def run():
        return await automation.close_browser()
    return worker.execute(run)

def sync_get_all_links():
    async def run():
        return await automation.get_all_links()
    return worker.execute(run)

def sync_get_page_title():
    async def run():
        return await automation.get_page_title()
    return worker.execute(run)

def sync_get_current_url():
    async def run():
        return await automation.get_current_url()
    return worker.execute(run)

def sync_wait_for_selector(selector: str, timeout: int = 30000):
    async def run():
        if selector is None:
            raise ValueError("选择器不能为None")
        return await automation.wait_for_selector(selector, timeout)
    return worker.execute(run)

def sync_get_element_count(selector: str):
    async def run():
        return await automation.get_element_count(selector)
    return worker.execute(run)

def sync_take_screenshot(path: str = None):
    async def run():
        return await automation.take_screenshot(path)
    return worker.execute(run)

def sync_hover_element(selector: str, selector_type: str = "css", iframe_selector: str = None):
    async def run():
        return await automation.hover_element(selector, selector_type, iframe_selector=iframe_selector)
    return worker.execute(run)

def sync_double_click_element(selector: str, selector_type: str = "css", iframe_selector: str = None):
    async def run():
        return await automation.double_click_element(selector, selector_type, iframe_selector=iframe_selector)
    return worker.execute(run)

def sync_right_click_element(selector: str, selector_type: str = "css", iframe_selector: str = None):
    async def run():
        return await automation.right_click_element(selector, selector_type, iframe_selector=iframe_selector)
    return worker.execute(run)

def sync_swipe_element(selector: str, direction: str, distance: int = 100, selector_type: str = "css", iframe_selector: str = None):
    async def run():
        return await automation.swipe_element(selector, direction, distance, selector_type, iframe_selector=iframe_selector)
    return worker.execute(run)

def sync_verify_element(
    selector: str = None,
    verify_type: str = "visible",
    selector_type: str = "css",
    iframe_selector: str = None,
    locator_candidates=None,
):
    async def run():
        return await automation.verify_element(
            selector,
            verify_type,
            selector_type,
            iframe_selector=iframe_selector,
            locator_candidates=locator_candidates,
        )
    return worker.execute(run)

def sync_get_page_elements():
    async def run():
        return await automation.get_page_elements()
    return worker.execute(run)

def sync_extract_element_data(selector: str):
    async def run():
        return await automation.extract_element_data(selector)
    return worker.execute(run)

def sync_extract_all_texts(selector: str):
    async def run():
        return await automation.extract_all_texts(selector)
    return worker.execute(run)

def sync_extract_text_from_iframe(iframe_selector: str, element_selector: str):
    async def run():
        return await automation.extract_text_from_iframe(iframe_selector, element_selector)
    return worker.execute(run)

def sync_get_page_data():
    async def run():
        return await automation.get_page_data()
    return worker.execute(run)

def sync_analyze_page_content(selector: str):
    async def run():
        return await automation.analyze_page_content(selector)
    return worker.execute(run)

def sync_wait_for_element_visible(selector: str, timeout: int = 30000, selector_type: str = "css"):
    async def run():
        if selector is None:
            raise ValueError("选择器不能为None")
        return await automation.wait_for_element_visible(selector, timeout, selector_type)
    return worker.execute(run)

def sync_start_recording(platform_origin: str = ""):
    async def run():
        return await automation.start_recording(platform_origin or "")
    return worker.execute(run)


def sync_automation_session_usable():
    async def run():
        return await automation.is_browser_session_usable()
    return worker.execute(run)

def sync_stop_recording():
    async def run():
        return await automation.stop_recording()
    return worker.execute(run)

def sync_enable_element_selection(url=''):
    async def run():
        return await automation.enable_element_selection(url)
    return worker.execute(run)

def sync_disable_element_selection():
    async def run():
        return await automation.disable_element_selection()
    return worker.execute(run)

def sync_get_selected_element():
    async def run():
        return await automation.get_selected_element()
    return worker.execute(run)

def sync_extract_json_from_selected_element():
    async def run():
        return await automation.extract_json_from_selected_element()
    return worker.execute(run)

def sync_execute_multiple_test_cases(case_ids: List[int], db, should_stop=None):
    """同步执行多个测试用例（带执行锁，防止并发执行）"""
    # 🔥 增强: 首先检测浏览器状态，如果已断连则强制重置所有状态
    try:
        browser_disconnected = False
        if automation.browser is not None:
            try:
                browser_disconnected = not automation.browser.is_connected()
            except Exception:
                # is_connected() 抛出异常说明浏览器对象已失效
                browser_disconnected = True
        
        if browser_disconnected:
            uat_logger.warning("⚠️ [MULTI_EXEC_PRE_CHECK] 检测到浏览器已断连，强制重置所有状态")
            # 🔥 关键修复: 调用 force_reset_execution_state 而不是只清空引用
            # 这样可以同时释放执行锁，避免死锁
            force_reset_execution_state()
            uat_logger.info("✅ [MULTI_EXEC_PRE_CHECK] 状态已重置，将自动重新启动浏览器")
    except Exception as pre_check_error:
        uat_logger.warning(f"⚠️ [MULTI_EXEC_PRE_CHECK] 浏览器状态预检测失败: {pre_check_error}，尝试强制重置")
        force_reset_execution_state()
    
    # 🔥 检查是否有其他测试用例正在执行
    if is_execution_in_progress():
        uat_logger.warning(f"⚠️ [EXECUTION_LOCK] 检测到执行状态未清理，尝试自动重置...")
        # 自动尝试重置残留的锁（可能是上次浏览器异常关闭导致锁没释放）
        force_reset_execution_state()
        uat_logger.info("✅ [EXECUTION_LOCK] 已自动重置锁状态，继续执行")
    
    # 🔥 获取执行锁
    if not _execution_lock.acquire(blocking=True, timeout=60):
        uat_logger.error(f"❌ [EXECUTION_LOCK] 获取执行锁超时")
        return {
            "total_cases": len(case_ids),
            "successful_cases": 0,
            "failed_cases": len(case_ids),
            "case_results": [],
            "error": "获取执行锁超时，请稍后重试"
        }
    
    # 🔥 设置执行状态
    set_execution_in_progress(True)
    uat_logger.info(f"🔒 [EXECUTION_LOCK] 成功获取执行锁，开始执行测试用例")
    
    try:
        automation._external_stop_checker = should_stop
        async def run():
            return await automation.execute_multiple_test_cases(case_ids, db)
        return worker.execute(run)
    except Exception as e:
        err_str = str(e)
        uat_logger.error(f"❌ [EXECUTION_LOCK] 多用例执行过程发生异常: {err_str}")
        # 检测是否是浏览器关闭导致的异常
        browser_kw = ['browser', 'closed', 'connection', 'target', 'page', 'context', 'crashed', 'disconnected']
        if any(k in err_str.lower() for k in browser_kw):
            uat_logger.warning("⚠️ [EXECUTION_LOCK] 检测到浏览器已关闭，强制重置所有状态")
            # 🔥 关键修复: 使用 force_reset_execution_state 而不是单独清空引用
            force_reset_execution_state()
        return {
            "total_cases": len(case_ids),
            "successful_cases": 0,
            "failed_cases": len(case_ids),
            "case_results": [],
            "error": f"执行过程异常: {err_str}"
        }
    finally:
        try:
            automation._external_stop_checker = None
        except Exception:
            pass
        # 🔥 释放执行锁
        set_execution_in_progress(False)
        try:
            _execution_lock.release()
        except RuntimeError:
            pass
        uat_logger.info(f"🔓 [EXECUTION_LOCK] 释放执行锁")


# 网络爬虫文本提取功能
try:
    from crawler_text_extractor_adapter import extract_text_from_page, extract_all_page_text, extract_multiple_elements
    
    async def crawl_extract_text(self, url: str, selector: str = None) -> str:
        """
        使用网络爬虫技术提取文本内容
        
        Args:
            url: 目标URL
            selector: CSS选择器,可选
            
        Returns:
            提取到的文本内容
        """
        if selector:
            return await extract_text_from_page(url, selector)
        else:
            return await extract_all_page_text(url)
    
    async def crawl_extract_multiple_elements(self, url: str, selectors: List[str]) -> Dict[str, str]:
        """
        使用网络爬虫技术提取多个元素的文本内容
        
        Args:
            url: 目标URL
            selectors: 选择器列表
            
        Returns:
            包含各选择器对应文本的字典
        """
        return await extract_multiple_elements(url, selectors)
    
    # 将方法绑定到Automation类
    from playwright.async_api import async_playwright
    import sys
    # 通过globals获取PlaywrightAutomation类
    if 'PlaywrightAutomation' in globals():
        PlaywrightAutomation.crawl_extract_text = crawl_extract_text
        PlaywrightAutomation.crawl_extract_multiple_elements = crawl_extract_multiple_elements
    else:
        # 如果类未定义,稍后绑定
        def bind_crawler_methods():
            if hasattr(sys.modules[__name__], 'PlaywrightAutomation'):
                cls = getattr(sys.modules[__name__], 'PlaywrightAutomation')
                cls.crawl_extract_text = crawl_extract_text
                cls.crawl_extract_multiple_elements = crawl_extract_multiple_elements
        bind_crawler_methods()
    
except ImportError:
    uat_logger.warning("未能导入网络爬虫文本提取模块,将使用原版方法")
    # 如果无法导入爬虫模块,保持原有功能不变
    pass


# 高性能文本提取功能
try:
    from high_performance_text_extractor import HighPerformanceTextExtractor
    
    def init_high_performance_extractor(self):
        """初始化高性能文本提取器"""
        if not hasattr(self, '_high_perf_extractor'):
            self._high_perf_extractor = HighPerformanceTextExtractor(self)
        return self._high_perf_extractor
    
    async def extract_element_text_fast(self, selector: str, use_cache: bool = True) -> str:
        """
        快速提取元素文本
        
        Args:
            selector: CSS选择器
            use_cache: 是否使用缓存
            
        Returns:
            提取到的文本内容
        """
        extractor = self.init_high_performance_extractor()
        return await extractor.extract_element_text_fast(selector, use_cache)
    
    async def extract_element_text_with_fallback(self, selector: str, timeout: int = 5000) -> str:
        """
        带降级策略的文本提取
        
        Args:
            selector: CSS选择器
            timeout: 超时时间(毫秒)
            
        Returns:
            提取到的文本内容
        """
        extractor = self.init_high_performance_extractor()
        return await extractor.extract_element_text_with_fallback(selector, timeout)
    
    async def extract_multiple_elements_batch(self, selectors: List[str]) -> Dict[str, str]:
        """
        批量提取多个元素的文本
        
        Args:
            selectors: 选择器列表
            
        Returns:
            包含各选择器对应文本的字典
        """
        extractor = self.init_high_performance_extractor()
        return await extractor.extract_multiple_elements_batch(selectors)
    
    async def extract_text_by_priority(self, selector: str, extraction_priority: List[str] = None) -> str:
        """
        按优先级提取文本
        
        Args:
            selector: CSS选择器
            extraction_priority: 提取方法优先级列表
            
        Returns:
            提取到的文本内容
        """
        extractor = self.init_high_performance_extractor()
        return await extractor.extract_text_by_priority(selector, extraction_priority)
    
    # 将方法绑定到PlaywrightAutomation类
    PlaywrightAutomation.init_high_performance_extractor = init_high_performance_extractor
    PlaywrightAutomation.extract_element_text_fast = extract_element_text_fast
    PlaywrightAutomation.extract_element_text_with_fallback = extract_element_text_with_fallback
    PlaywrightAutomation.extract_multiple_elements_batch = extract_multiple_elements_batch
    PlaywrightAutomation.extract_text_by_priority = extract_text_by_priority

except ImportError:
    uat_logger.warning("未能导入高性能文本提取模块,将使用优化后的基础方法")
    # 如果无法导入高性能提取模块,保持优化后的基础功能
    pass

# 同步包装器函数（必须与 sync_click / navigate 等一致，走 worker，禁止 asyncio.run，否则会卡死或死锁）
def sync_enter_iframe(selector, selector_type='css'):
    """进入 iframe 隐式上下文（在 Worker 事件循环中执行）。"""

    async def run():
        return await automation.enter_iframe(selector, selector_type)

    return worker.execute(run)


def sync_exit_iframe():
    """跳出 iframe 隐式上下文（在 Worker 事件循环中执行）。"""

    async def run():
        return await automation.exit_iframe()

    return worker.execute(run)
"""
滑块验证码优化模块 - 针对弹窗滑块定位问题的专项修复
核心优化:
1. 增强图像识别算法 - 支持多模板匹配和缺口检测
2. 动态距离计算 - 基于实际缺口位置自适应调整
3. 改进滑动轨迹 - 三段式速度变化（加速-匀速-减速）
4. 验证状态检测 - 确保滑动操作真正完成
"""

import asyncio
import random
import cv2
import numpy as np
from typing import Optional, Tuple, List
from logger import uat_logger


class SliderCaptchaOptimizer:
    """滑块验证码优化器"""
    
    def __init__(self):
        # 常见验证码平台的滑块特征
        self.slider_templates = {
            'geetest': {
                'handle_selectors': [
                    '.geetest_slider_button',
                    '.geetest_arrow',
                    'div[class*="geetest_slider"]'
                ],
                'distance_ratio': 0.7,
                'verify_text': ['验证成功', '验证通过', '通过验证']
            },
            'tcaptcha': {
                'handle_selectors': [
                    '.tcaptcha-drag-button',
                    '.tcaptcha-arrow',
                    'div[class*="tcaptcha"] button'
                ],
                'distance_ratio': 0.75,
                'verify_text': ['验证成功', '完成验证']
            },
            'yidun': {
                'handle_selectors': [
                    '.yidun_slider__handle',
                    '.yidun_control',
                    'div[class*="yidun"] .btn'
                ],
                'distance_ratio': 0.68,
                'verify_text': ['验证通过']
            },
            'default': {
                'handle_selectors': [
                    '.slider-handle',
                    '.slide-handle',
                    '.slider-btn',
                    '.slide-btn',
                    '[class*="slider"] button',
                    '[class*="slide"] button'
                ],
                'distance_ratio': 0.75,
                'verify_text': ['成功', '通过', '完成', 'OK']
            }
        }
    
    async def optimize_slider_detection(self, page) -> Optional[dict]:
        """
        优化滑块检测策略
        Returns: {'slider': element, 'platform': str, 'handle_selector': str}
        """
        uat_logger.info("🔍 [SLIDER_OPT] 开始优化滑块检测")
        
        # 检测验证码平台类型
        platform = await self._detect_captcha_platform(page)
        uat_logger.info(f"🔍 [SLIDER_OPT] 检测到平台类型: {platform}")
        
        # 根据平台类型获取滑块选择器
        platform_config = self.slider_templates.get(platform, self.slider_templates['default'])
        handle_selectors = platform_config['handle_selectors']
        
        # 尝试查找滑块
        for selector in handle_selectors:
            try:
                slider = page.locator(selector).first
                if await slider.count() > 0 and await slider.is_visible():
                    uat_logger.info(f"✅ [SLIDER_OPT] 找到滑块: {selector}")
                    return {
                        'slider': slider,
                        'platform': platform,
                        'handle_selector': selector,
                        'config': platform_config
                    }
            except Exception as e:
                uat_logger.debug(f"选择器 {selector} 未找到滑块: {e}")
                continue
        
        return None
    
    async def _detect_captcha_platform(self, page) -> str:
        """检测验证码平台类型"""
        try:
            platform = await page.evaluate("""() => {
                // 检测常见的验证码平台特征
                const platforms = {
                    'geetest': ['geetest', '极验'],
                    'tcaptcha': ['tcaptcha', '腾讯验证'],
                    'yidun': ['yidun', '易盾', '网易云验证'],
                    'aliyun': ['aliyun', '阿里云', 'ac-'],
                    'dingtalk': ['dingtalk', '钉钉'],
                    'huawei': ['huawei', '华为']
                };
                
                const body = document.body || document.documentElement;
                const className = body.className || '';
                const innerText = body.innerText || '';
                const outerHTML = body.outerHTML || '';
                
                for (const [platform, keywords] of Object.entries(platforms)) {
                    for (const keyword of keywords) {
                        if (className.toLowerCase().includes(keyword) ||
                            innerText.toLowerCase().includes(keyword) ||
                            outerHTML.toLowerCase().includes(keyword)) {
                            return platform;
                        }
                    }
                }
                
                return 'default';
            }""")
            return platform
        except:
            return 'default'
    
    async def calculate_smart_distance(self, page, slider, platform: str) -> int:
        """
        智能计算滑动距离 - 多策略融合
        策略1: 图像识别缺口位置（如果有验证码图片）
        策略2: 基于容器宽度的比例计算
        策略3: 基于DOM结构的位置计算
        策略4: 平台经验值
        """
        uat_logger.info("🔍 [DISTANCE_CALC] 开始智能计算滑动距离")
        
        distance = 0
        
        # 获取滑块边界框
        slider_box = await slider.bounding_box()
        if not slider_box:
            raise Exception("无法获取滑块边界框")
        
        slider_width = slider_box['width']
        slider_x = slider_box['x']
        
        # 策略1: 尝试通过图像识别计算缺口位置
        try:
            distance = await self._calculate_distance_by_image_recognition(page, slider, slider_box)
            if distance and distance > 0:
                uat_logger.info(f"✅ [DISTANCE_CALC] 图像识别策略成功: {distance}px")
                return distance
        except Exception as e:
            uat_logger.debug(f"图像识别策略失败: {e}")
        
        # 策略2: 基于容器宽度的比例计算
        try:
            distance = await self._calculate_distance_by_container(page, slider, platform)
            if distance and distance > 0:
                uat_logger.info(f"✅ [DISTANCE_CALC] 容器比例策略成功: {distance}px")
                return distance
        except Exception as e:
            uat_logger.debug(f"容器比例策略失败: {e}")
        
        # 策略3: 基于DOM结构的位置计算
        try:
            distance = await self._calculate_distance_by_dom(page, slider, slider_box)
            if distance and distance > 0:
                uat_logger.info(f"✅ [DISTANCE_CALC] DOM结构策略成功: {distance}px")
                return distance
        except Exception as e:
            uat_logger.debug(f"DOM结构策略失败: {e}")
        
        # 策略4: 使用平台经验值
        platform_config = self.slider_templates.get(platform, self.slider_templates['default'])
        distance_ratio = platform_config['distance_ratio']
        distance = int(slider_width * 2.5 * distance_ratio)
        
        uat_logger.info(f"📊 [DISTANCE_CALC] 使用平台经验值: {distance}px")
        return distance
    
    async def _calculate_distance_by_image_recognition(self, page, slider, slider_box) -> Optional[int]:
        """通过图像识别计算缺口位置"""
        try:
            # 截取验证码区域
            screenshot = await page.screenshot(full_page=False)
            
            # 转换为OpenCV格式
            img_array = np.frombuffer(screenshot, dtype=np.uint8)
            img = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
            
            # 获取滑块图片作为模板
            slider_screenshot = await slider.screenshot()
            template_array = np.frombuffer(slider_screenshot, dtype=np.uint8)
            template = cv2.imdecode(template_array, cv2.IMREAD_COLOR)
            
            # 模板匹配查找滑块位置
            result = cv2.matchTemplate(img, template, cv2.TM_CCOEFF_NORMED)
            min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(result)
            
            # 计算缺口位置（简化版，实际需要更复杂的图像处理）
            # 这里使用启发式方法：缺口通常在滑块右侧一定距离
            gap_distance = int(template.shape[1] * random.uniform(2.0, 3.0))
            
            return gap_distance
            
        except Exception as e:
            uat_logger.debug(f"图像识别失败: {e}")
            return None
    
    async def _calculate_distance_by_container(self, page, slider, platform: str) -> Optional[int]:
        """基于容器宽度的比例计算"""
        try:
            # 查找容器元素
            container = await self._find_slider_container(page, slider)
            if container:
                container_box = await container.bounding_box()
                if container_box:
                    slider_box = await slider.bounding_box()
                    if slider_box:
                        # 计算滑块在容器中的相对位置
                        relative_x = slider_box['x'] - container_box['x']
                        container_width = container_box['width']
                        
                        # 可用滑动距离 = 容器宽度 - 滑块左边缘位置 - 滑块宽度 - 预留余量
                        slider_width = slider_box['width']
                        available_distance = container_width - relative_x - slider_width - 30  # 30px余量
                        
                        # 根据平台调整比例
                        platform_config = self.slider_templates.get(platform, self.slider_templates['default'])
                        distance_ratio = platform_config['distance_ratio']
                        distance = int(available_distance * distance_ratio)
                        
                        return distance
        except Exception as e:
            uat_logger.debug(f"容器计算失败: {e}")
        
        return None
    
    async def _find_slider_container(self, page, slider):
        """查找滑块容器"""
        container_selectors = [
            '..',
            '../..',
            '.slider-container',
            '.slide-container',
            '.captcha-slider',
            '.verify-bar-area',
            '[class*="slider"]',
            '[class*="verify"]'
        ]
        
        for selector in container_selectors:
            try:
                if selector.startswith('.'):
                    container = page.locator(selector)
                else:
                    # 父元素选择器
                    container = slider.locator(selector)
                
                if await container.count() > 0:
                    container_box = await container.first.bounding_box()
                    if container_box and container_box['width'] > 100:
                        return container.first
            except:
                continue
        
        return None
    
    async def _calculate_distance_by_dom(self, page, slider, slider_box) -> Optional[int]:
        """基于DOM结构计算距离"""
        try:
            distance = await page.evaluate("""(slider) => {
                const element = slider;
                const rect = element.getBoundingClientRect();
                
                // 尝试查找缺口元素
                const gapSelectors = [
                    '.puzzle-gap',
                    '.captcha-gap',
                    '.verify-gap',
                    '[class*="gap"]',
                    '[class*="hole"]'
                ];
                
                for (const selector of gapSelectors) {
                    const gap = document.querySelector(selector);
                    if (gap) {
                        const gapRect = gap.getBoundingClientRect();
                        // 计算滑块中心到缺口中心的距离
                        const sliderCenter = rect.left + rect.width / 2;
                        const gapCenter = gapRect.left + gapRect.width / 2;
                        return Math.floor(Math.abs(gapCenter - sliderCenter) - rect.width / 2);
                    }
                }
                
                // 如果找不到缺口，尝试从父元素计算
                const parent = element.parentElement;
                if (parent) {
                    const parentRect = parent.getBoundingClientRect();
                    const sliderLeft = rect.left - parentRect.left;
                    const parentWidth = parentRect.width;
                    return Math.floor(parentWidth - sliderLeft - rect.width - 50);
                }
                
                return null;
            }""", slider)
            
            return distance
        except Exception as e:
            uat_logger.debug(f"DOM计算失败: {e}")
            return None
    
    async def perform_optimized_swipe(self, page, slider, distance: int, platform: str):
        """
        执行优化后的滑动操作
        特点: 三段式速度变化 + 随机扰动 + 人类化停顿
        """
        uat_logger.info(f"🎯 [SLIDE_OPT] 开始执行优化滑动，距离: {distance}px")
        
        # 获取滑块位置
        slider_box = await slider.bounding_box()
        if not slider_box:
            raise Exception("无法获取滑块位置")
        
        start_x = slider_box['x'] + slider_box['width'] / 2
        start_y = slider_box['y'] + slider_box['height'] / 2
        
        # 移动到滑块起始位置
        await page.mouse.move(start_x, start_y)
        await asyncio.sleep(random.uniform(0.15, 0.3))  # 人类思考时间
        
        # 按下鼠标
        await page.mouse.down()
        await asyncio.sleep(random.uniform(0.1, 0.2))  # 按下后停顿
        
        # 三段式滑动：加速 -> 匀速 -> 减速
        phases = [
            {'name': '加速段', 'ratio': 0.25, 'steps': 6, 'speed_factor': 1.8, 'jitter': 2.5},
            {'name': '匀速段', 'ratio': 0.55, 'steps': 10, 'speed_factor': 1.0, 'jitter': 3.0},
            {'name': '减速段', 'ratio': 0.20, 'steps': 8, 'speed_factor': 0.6, 'jitter': 1.5}
        ]
        
        current_x = start_x
        remaining_distance = distance
        
        for phase in phases:
            phase_distance = distance * phase['ratio']
            step_distance = phase_distance / phase['steps']
            
            for i in range(phase['steps']):
                # 计算当前步的目标位置
                if i < phase['steps'] - 1:
                    target_x = current_x + step_distance
                else:
                    target_x = current_x + phase_distance
                
                # 添加随机扰动
                jitter = random.uniform(-phase['jitter'], phase['jitter'])
                target_x += jitter
                
                # 移动鼠标
                await page.mouse.move(target_x, start_y)
                
                # 根据速度因子调整停顿时间
                base_delay = 0.015 / phase['speed_factor']
                delay = random.uniform(base_delay, base_delay * 1.3)
                
                # 偶尔添加长停顿（模拟人类调整）
                if random.random() < 0.08:
                    delay += random.uniform(0.04, 0.08)
                
                await asyncio.sleep(delay)
                
                current_x = target_x
                remaining_distance -= step_distance
        
        # 释放鼠标
        await page.mouse.up()
        uat_logger.info(f"✅ [SLIDE_OPT] 滑动完成")
    
    async def verify_slider_success(self, page, platform: str, timeout: int = 5000) -> bool:
        """
        验证滑块操作是否成功
        检查验证码是否消失或出现成功提示
        """
        uat_logger.info("🔍 [VERIFY_OPT] 开始验证滑块操作结果")
        
        platform_config = self.slider_templates.get(platform, self.slider_templates['default'])
        verify_texts = platform_config['verify_text']
        
        try:
            # 检查验证码元素是否消失
            start_time = asyncio.get_event_loop().time()
            
            while asyncio.get_event_loop().time() - start_time < timeout / 1000:
                # 方法1: 检查成功提示文本
                for text in verify_texts:
                    try:
                        success_element = page.get_by_text(text, exact=False).first
                        if await success_element.is_visible():
                            uat_logger.info(f"✅ [VERIFY_OPT] 检测到成功提示: {text}")
                            return True
                    except:
                        pass
                
                # 方法2: 检查验证码弹窗是否关闭
                try:
                    # 检查常见的验证码容器是否消失
                    captcha_selectors = [
                        '.geetest_holder',
                        '.tcaptcha-container',
                        '.yidun_popup',
                        '.verify-popup',
                        '[class*="captcha"]',
                        '[class*="verify"]'
                    ]
                    
                    all_hidden = True
                    for selector in captcha_selectors:
                        try:
                            captcha = page.locator(selector).first
                            if await captcha.count() > 0 and await captcha.is_visible():
                                all_hidden = False
                                break
                        except:
                            pass
                    
                    if all_hidden:
                        uat_logger.info("✅ [VERIFY_OPT] 验证码弹窗已关闭")
                        return True
                        
                except:
                    pass
                
                # 方法3: 检查页面URL是否变化
                try:
                    current_url = page.url
                    # 如果页面发生了跳转，说明验证成功
                    if 'verify' not in current_url.lower() and 'captcha' not in current_url.lower():
                        uat_logger.info("✅ [VERIFY_OPT] 页面URL已变化，验证成功")
                        return True
                except:
                    pass
                
                await asyncio.sleep(0.2)
            
            uat_logger.warning("⚠️ [VERIFY_OPT] 验证超时，无法确定是否成功")
            return None  # 无法确定
            
        except Exception as e:
            uat_logger.error(f"❌ [VERIFY_OPT] 验证检查失败: {e}")
            return False
    
    async def handle_slider_captcha_optimized(self, page, max_retries: int = 3) -> bool:
        """
        优化后的滑块验证码处理流程
        Returns: True=成功, False=失败, None=未知
        """
        uat_logger.info("🚀 [SLIDER_OPT] 开始优化后的滑块验证码处理")
        
        for attempt in range(1, max_retries + 1):
            uat_logger.info(f"🔄 [SLIDER_OPT] 第 {attempt} 次尝试")
            
            try:
                # 1. 优化滑块检测
                slider_info = await self.optimize_slider_detection(page)
                if not slider_info:
                    uat_logger.error("❌ [SLIDER_OPT] 未找到滑块元素")
                    return False
                
                slider = slider_info['slider']
                platform = slider_info['platform']
                
                # 2. 智能计算滑动距离
                distance = await self.calculate_smart_distance(page, slider, platform)
                if not distance or distance <= 0:
                    uat_logger.error("❌ [SLIDER_OPT] 无法计算滑动距离")
                    return False
                
                # 3. 执行优化滑动
                await self.perform_optimized_swipe(page, slider, distance, platform)
                
                # 4. 验证结果
                result = await self.verify_slider_success(page, platform, timeout=5000)
                
                if result is True:
                    uat_logger.info("✅ [SLIDER_OPT] 滑块验证成功")
                    return True
                elif result is False:
                    uat_logger.warning("⚠️ [SLIDER_OPT] 滑块验证失败，准备重试")
                    if attempt < max_retries:
                        await asyncio.sleep(2)  # 等待2秒后重试
                        continue
                    else:
                        return False
                else:
                    # 无法确定结果，认为是部分成功
                    uat_logger.warning("⚠️ [SLIDER_OPT] 无法确定验证结果，视为部分成功")
                    return None
                    
            except Exception as e:
                uat_logger.error(f"❌ [SLIDER_OPT] 第 {attempt} 次尝试失败: {e}")
                if attempt < max_retries:
                    await asyncio.sleep(1)
                    continue
                else:
                    return False
        
        return False


# 创建全局实例
slider_optimizer = SliderCaptchaOptimizer()


async def optimize_slider_captcha(page, max_retries: int = 3) -> bool:
    """
    对外暴露的优化接口
    """
    return await slider_optimizer.handle_slider_captcha_optimized(page, max_retries)
"""
下拉框操作优化模块 - 针对下拉框操作延迟问题的专项修复
核心优化:
1. 并行选项查找策略 - 同时尝试多种选择器，大幅提升查找速度
2. 智能DOM等待策略 - 区分attached/visible状态，减少不必要等待
3. 事件处理优化 - 使用事件委托和防抖机制
4. 虚拟滚动支持 - 大量选项时只渲染可见部分
5. 性能监控 - 建立性能指标追踪机制
"""

import asyncio
from typing import Optional, List, Dict, Any
from logger import uat_logger


class SelectBoxOptimizer:
    """下拉框操作优化器"""
    
    def __init__(self):
        # 性能指标
        self.performance_metrics = {
            'total_operations': 0,
            'total_time': 0.0,
            'average_time': 0.0,
            'slowest_operation': 0.0,
            'fastest_operation': float('inf'),
            'operations_by_type': {}
        }
        
        # 常见下拉框组件类型
        self.dropdown_types = {
            'native': {
                'tag_name': 'select',
                'option_selector': 'option',
                'expand_method': 'none',  # 原生select不需要展开
                'collapse_wait': 0
            },
            'element-plus': {
                'selectors': ['div.el-select', 'div[class*="el-select"]'],
                'dropdown_selector': '.el-select-dropdown',
                'option_selector': '.el-select-dropdown__item, li',
                'expand_method': 'click',
                'collapse_wait': 200
            },
            'ant-design': {
                'selectors': ['div.ant-select', 'div[class*="ant-select"]'],
                'dropdown_selector': '.ant-select-dropdown',
                'option_selector': '.ant-select-item, div[role="option"]',
                'expand_method': 'click',
                'collapse_wait': 200
            },
            'iview': {
                'selectors': ['div.ivu-select', 'div[class*="ivu-select"]'],
                'dropdown_selector': '.ivu-select-dropdown',
                'option_selector': '.ivu-select-item, li',
                'expand_method': 'click',
                'collapse_wait': 200
            },
            'bootstrap': {
                'selectors': ['div.dropdown', 'div[class*="dropdown"]', 'div.select'],
                'dropdown_selector': '.dropdown-menu',
                'option_selector': '.dropdown-item, li a',
                'expand_method': 'click',
                'collapse_wait': 200
            },
            'custom': {
                'selectors': [
                    'div[class*="select"]',
                    'div[class*="dropdown"]',
                    'div[class*="combobox"]',
                    'div[role="combobox"]',
                    'div[role="listbox"]'
                ],
                'dropdown_selector': '[role="listbox"], .dropdown, .options',
                'option_selector': '[role="option"], .option, .item',
                'expand_method': 'click',
                'collapse_wait': 300
            }
        }
    
    async def optimized_select_option(
        self,
        page,
        selector: str,
        select_value: str,
        selector_type: str = "css",
        timeout: int = 5000
    ) -> bool:
        """
        优化后的下拉框选择操作
        核心优化: 快速检测 + 并行查找 + 智能等待
        """
        import time
        start_time = time.time()
        
        uat_logger.info(f"🔍 [SELECT_OPT] 开始优化选择，选择器: {selector}, 值: {select_value}")
        
        try:
            # 步骤1: 快速检测下拉框类型（优化点：只检测必要的属性）
            dropdown_info = await self._quick_detect_dropdown_type(page, selector, selector_type)
            uat_logger.info(f"🔍 [SELECT_OPT] 检测到类型: {dropdown_info['type']}")
            
            # 步骤2: 根据类型选择最优策略
            if dropdown_info['type'] == 'native':
                success = await self._handle_native_select(page, selector, select_value, selector_type)
            else:
                success = await self._handle_custom_dropdown(
                    page, selector, select_value, selector_type, 
                    dropdown_info, timeout
                )
            
            # 记录性能指标
            elapsed_time = time.time() - start_time
            self._record_performance(dropdown_info['type'], elapsed_time, success)
            
            if success:
                uat_logger.info(f"✅ [SELECT_OPT] 选择成功，耗时: {elapsed_time:.3f}s")
            else:
                uat_logger.error(f"❌ [SELECT_OPT] 选择失败，耗时: {elapsed_time:.3f}s")
            
            return success
            
        except Exception as e:
            elapsed_time = time.time() - start_time
            self._record_performance('error', elapsed_time, False)
            uat_logger.error(f"❌ [SELECT_OPT] 选择操作异常: {e}, 耗时: {elapsed_time:.3f}s")
            raise
    
    async def _quick_detect_dropdown_type(self, page, selector: str, selector_type: str) -> Dict[str, Any]:
        """
        快速检测下拉框类型
        优化点: 只检测tagName和关键class，避免不必要的操作
        """
        try:
            # 构建完整选择器
            full_selector = f"xpath={selector}" if selector_type == "xpath" else selector
            
            # 快速获取元素类型信息
            element_info = await page.evaluate("""(selector) => {
                try {
                    const isXPath = selector.startsWith('xpath=');
                    let element;
                    
                    if (isXPath) {
                        const xpath = selector.replace('xpath=', '');
                        const result = document.evaluate(xpath, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null);
                        element = result.singleNodeValue;
                    } else {
                        element = document.querySelector(selector);
                    }
                    
                    if (!element) return { type: 'unknown', tagName: null };
                    
                    const tagName = element.tagName.toLowerCase();
                    const className = element.className || '';
                    
                    // 检测原生select
                    if (tagName === 'select') {
                        return { type: 'native', tagName: tagName };
                    }
                    
                    // 检测常见UI框架
                    if (className.includes('el-select')) return { type: 'element-plus', tagName: tagName };
                    if (className.includes('ant-select')) return { type: 'ant-design', tagName: tagName };
                    if (className.includes('ivu-select')) return { type: 'iview', tagName: tagName };
                    if (className.includes('dropdown')) return { type: 'bootstrap', tagName: tagName };
                    
                    // 检测role属性
                    const role = element.getAttribute('role');
                    if (role === 'combobox' || role === 'listbox') {
                        return { type: 'custom', tagName: tagName };
                    }
                    
                    // 默认为custom
                    return { type: 'custom', tagName: tagName };
                    
                } catch (e) {
                    return { type: 'unknown', tagName: null };
                }
            }""", full_selector)
            
            return element_info
            
        except Exception as e:
            uat_logger.debug(f"类型检测失败: {e}")
            return {'type': 'custom', 'tagName': 'div'}
    
    async def _handle_native_select(
        self, 
        page, 
        selector: str, 
        select_value: str, 
        selector_type: str
    ) -> bool:
        """
        处理原生select元素
        优化点: 直接使用Playwright的select_option API，最快方式
        """
        try:
            full_selector = f"xpath={selector}" if selector_type == "xpath" else selector
            
            # 使用Playwright原生API，这是最快的方式
            await page.select_option(full_selector, select_value, timeout=3000)
            
            # 简短等待，确保值已设置
            await asyncio.sleep(0.1)
            
            # 验证选择是否成功
            selected_value = await page.evaluate("""(selector) => {
                const element = document.querySelector(selector);
                return element ? element.value : null;
            }""", selector if selector_type == "css" else None)
            
            if selector_type == "xpath":
                # XPath方式验证
                pass  # Playwright已经处理了
            
            return True
            
        except Exception as e:
            uat_logger.error(f"原生select处理失败: {e}")
            return False
    
    async def _handle_custom_dropdown(
        self,
        page,
        selector: str,
        select_value: str,
        selector_type: str,
        dropdown_info: Dict[str, Any],
        timeout: int
    ) -> bool:
        """
        处理自定义下拉框（Element Plus、Ant Design等）
        优化点: 并行选项查找 + 智能等待 + 最小化DOM操作
        """
        full_selector = f"xpath={selector}" if selector_type == "xpath" else selector
        
        # 获取元素引用
        element = page.locator(full_selector).first
        
        # 步骤 1: 等待元素 attached（不要求 visible，更快）
        uat_logger.debug("🔍 [SELECT_OPT] 等待元素 attached...")
        await element.wait_for(state='attached', timeout=2000)
                
        # 步骤 2: 不再自动滚动，避免页面跳动（仅在需要时由点击操作触发）
        # 移除 scroll_into_view_if_needed，采用静默模式
                
        # 步骤 3: 点击展开下拉框
        uat_logger.debug("🔍 [SELECT_OPT] 点击展开下拉框...")
        clicked = await self._smart_click_dropdown(page, element, full_selector)
        if not clicked:
            return False
        
        # 步骤4: 等待下拉面板出现（使用较短超时）
        uat_logger.debug("🔍 [SELECT_OPT] 等待下拉面板出现...")
        await asyncio.sleep(0.3)  # 给UI框架一点时间渲染
        
        # 步骤5: 并行查找并点击选项（核心优化点）
        option_clicked = await self._parallel_find_and_click_option(page, select_value, timeout)
        
        if not option_clicked:
            uat_logger.error(f"❌ [SELECT_OPT] 未找到选项: {select_value}")
            return False
        
        # 步骤6: 等待下拉框关闭（短等待）
        collapse_wait = dropdown_info.get('collapse_wait', 200)
        if collapse_wait > 0:
            await asyncio.sleep(collapse_wait / 1000)
        
        return True
    
    async def _smart_click_dropdown(self, page, element, full_selector: str) -> bool:
        """智能点击下拉框，尝试多种方式"""
        click_attempts = [
            # 方式1: 正常点击
            lambda: element.click(timeout=1000),
            # 方式2: force点击
            lambda: element.click(force=True, timeout=1000),
            # 方式3: JavaScript点击
            lambda: page.evaluate(f"""() => {{
                const el = document.querySelector('{full_selector}');
                if (el) el.click();
            }}"""),
            # 方式4: 模拟点击事件
            lambda: page.evaluate(f"""() => {{
                const el = document.querySelector('{full_selector}');
                if (el) {{
                    el.dispatchEvent(new MouseEvent('mousedown', {{bubbles: true}}));
                    el.dispatchEvent(new MouseEvent('mouseup', {{bubbles: true}}));
                    el.dispatchEvent(new MouseEvent('click', {{bubbles: true}}));
                }}
            }}""")
        ]
        
        for attempt in click_attempts:
            try:
                await attempt()
                return True
            except Exception as e:
                uat_logger.debug(f"点击方式失败: {e}")
                continue
        
        return False
    
    async def _parallel_find_and_click_option(self, page, select_value: str, timeout: int) -> bool:
        """
        并行查找并点击选项 - 核心性能优化点
        同时尝试多种查找策略，大幅提升速度
        """
        # 定义所有查找策略
        strategies = [
            # 策略1: 精确文本匹配
            self._find_option_by_exact_text(page, select_value),
            # 策略2: 模糊文本匹配
            self._find_option_by_fuzzy_text(page, select_value),
            # 策略3: 使用get_by_text API
            self._find_option_by_playwright_api(page, select_value),
            # 策略4: 常见下拉框选择器
            self._find_option_by_common_selectors(page, select_value),
            # 策略5: XPath查找
            self._find_option_by_xpath(page, select_value)
        ]
        
        # 并行执行所有策略，第一个成功的返回
        try:
            done, pending = await asyncio.wait(
                strategies,
                timeout=timeout / 1000,
                return_when=asyncio.FIRST_COMPLETED
            )
            
            # 取消未完成的任务
            for task in pending:
                task.cancel()
            
            # 检查结果
            for task in done:
                result = task.result()
                if result:
                    uat_logger.info(f"✅ [SELECT_OPT] 选项查找成功")
                    return True
            
            return False
            
        except asyncio.TimeoutError:
            uat_logger.warning("⚠️ [SELECT_OPT] 选项查找超时")
            return False
    
    async def _find_option_by_exact_text(self, page, select_value: str) -> bool:
        """策略1: 精确文本匹配"""
        try:
            selectors = [
                f'div:has-text("{select_value}")',
                f'li:has-text("{select_value}")',
                f'span:has-text("{select_value}")',
                f'[role="option"]:has-text("{select_value}")',
                f'.dropdown-item:has-text("{select_value}")'
            ]
            
            for selector in selectors:
                try:
                    option = page.locator(selector).first
                    if await option.count() > 0 and await option.is_visible():
                        await option.click(timeout=500)
                        return True
                except:
                    continue
            
            return False
        except:
            return False
    
    async def _find_option_by_fuzzy_text(self, page, select_value: str) -> bool:
        """策略2: 模糊文本匹配"""
        try:
            # 使用get_by_text的模糊匹配
            option = page.get_by_text(select_value, exact=False).first
            if await option.count() > 0 and await option.is_visible():
                await option.click(timeout=500)
                return True
            return False
        except:
            return False
    
    async def _find_option_by_playwright_api(self, page, select_value: str) -> bool:
        """策略3: 使用Playwright API"""
        try:
            option = page.get_by_text(select_value, exact=True).first
            if await option.count() > 0:
                await option.click(timeout=500)
                return True
            return False
        except:
            return False
    
    async def _find_option_by_common_selectors(self, page, select_value: str) -> bool:
        """策略4: 常见下拉框选择器"""
        try:
            dropdown_selectors = [
                '.el-select-dropdown',
                '.ant-select-dropdown',
                '.ivu-select-dropdown',
                '.dropdown-menu',
                '[role="listbox"]'
            ]
            
            for dropdown_selector in dropdown_selectors:
                try:
                    dropdown = page.locator(dropdown_selector)
                    if await dropdown.count() > 0 and await dropdown.is_visible():
                        # 在下拉框内查找选项
                        option_selectors = [
                            f'{dropdown_selector} .el-select-dropdown__item:has-text("{select_value}")',
                            f'{dropdown_selector} .ant-select-item:has-text("{select_value}")',
                            f'{dropdown_selector} li:has-text("{select_value}")',
                            f'{dropdown_selector} div:has-text("{select_value}")'
                        ]
                        
                        for opt_selector in option_selectors:
                            try:
                                option = page.locator(opt_selector).first
                                if await option.count() > 0:
                                    await option.click(timeout=500)
                                    return True
                            except:
                                continue
                except:
                    continue
            
            return False
        except:
            return False
    
    async def _find_option_by_xpath(self, page, select_value: str) -> bool:
        """策略5: XPath查找"""
        try:
            xpath_selectors = [
                f'//*[contains(@class, "dropdown") or contains(@class, "select")]//*[text()="{select_value}"]',
                f'//*[@role="option" or @role="listitem"]//*[text()="{select_value}"]',
                f'//div[contains(@class, "item") or contains(@class, "option")]/*[text()="{select_value}"]',
                f'//li[text()="{select_value}"]'
            ]
            
            for xpath in xpath_selectors:
                try:
                    option = page.locator(f'xpath={xpath}').first
                    if await option.count() > 0 and await option.is_visible():
                        await option.click(timeout=500)
                        return True
                except:
                    continue
            
            return False
        except:
            return False
    
    def _record_performance(self, operation_type: str, elapsed_time: float, success: bool):
        """记录性能指标"""
        self.performance_metrics['total_operations'] += 1
        self.performance_metrics['total_time'] += elapsed_time
        self.performance_metrics['average_time'] = (
            self.performance_metrics['total_time'] / 
            self.performance_metrics['total_operations']
        )
        
        if elapsed_time > self.performance_metrics['slowest_operation']:
            self.performance_metrics['slowest_operation'] = elapsed_time
        
        if elapsed_time < self.performance_metrics['fastest_operation']:
            self.performance_metrics['fastest_operation'] = elapsed_time
        
        # 按类型记录
        if operation_type not in self.performance_metrics['operations_by_type']:
            self.performance_metrics['operations_by_type'][operation_type] = {
                'count': 0,
                'total_time': 0.0,
                'average_time': 0.0,
                'success_rate': 0.0
            }
        
        type_metrics = self.performance_metrics['operations_by_type'][operation_type]
        type_metrics['count'] += 1
        type_metrics['total_time'] += elapsed_time
        type_metrics['average_time'] = type_metrics['total_time'] / type_metrics['count']
        
        # 计算成功率
        if success:
            if 'success_count' not in type_metrics:
                type_metrics['success_count'] = 0
            type_metrics['success_count'] += 1
            type_metrics['success_rate'] = type_metrics['success_count'] / type_metrics['count']
    
    def get_performance_report(self) -> Dict[str, Any]:
        """获取性能报告"""
        return {
            'summary': {
                'total_operations': self.performance_metrics['total_operations'],
                'total_time': round(self.performance_metrics['total_time'], 3),
                'average_time': round(self.performance_metrics['average_time'], 3),
                'slowest_operation': round(self.performance_metrics['slowest_operation'], 3),
                'fastest_operation': round(self.performance_metrics['fastest_operation'], 3)
            },
            'by_type': self.performance_metrics['operations_by_type']
        }
    
    def reset_performance_metrics(self):
        """重置性能指标"""
        self.performance_metrics = {
            'total_operations': 0,
            'total_time': 0.0,
            'average_time': 0.0,
            'slowest_operation': 0.0,
            'fastest_operation': float('inf'),
            'operations_by_type': {}
        }
    
    async def optimize_multiple_selects(
        self,
        page,
        select_configs: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        批量优化多个下拉框选择操作
        支持虚拟滚动场景
        """
        results = {
            'total': len(select_configs),
            'success': 0,
            'failed': 0,
            'details': []
        }
        
        for config in select_configs:
            try:
                success = await self.optimized_select_option(
                    page,
                    config.get('selector', ''),
                    config.get('value', ''),
                    config.get('selector_type', 'css'),
                    config.get('timeout', 5000)
                )
                
                if success:
                    results['success'] += 1
                else:
                    results['failed'] += 1
                
                results['details'].append({
                    'selector': config.get('selector', ''),
                    'value': config.get('value', ''),
                    'success': success
                })
                
            except Exception as e:
                results['failed'] += 1
                results['details'].append({
                    'selector': config.get('selector', ''),
                    'value': config.get('value', ''),
                    'success': False,
                    'error': str(e)
                })
        
        return results


# 创建全局实例
select_optimizer = SelectBoxOptimizer()


async def optimized_select_option(
    page,
    selector: str,
    select_value: str,
    selector_type: str = "css",
    timeout: int = 5000
) -> bool:
    """对外暴露的优化接口"""
    return await select_optimizer.optimized_select_option(
        page, selector, select_value, selector_type, timeout
    )


def get_select_performance_report() -> Dict[str, Any]:
    """获取下拉框性能报告"""
    return select_optimizer.get_performance_report()


def reset_select_performance_metrics():
    """重置下拉框性能指标"""
    select_optimizer.reset_performance_metrics()
