# -*- coding: utf-8 -*-
"""
验证码求解引擎：OpenCV 为主力（零额外体积）；ddddocr / VLM 均为可选增强。

环境变量：
  CAPTCHA_DDDDOCR_ENABLE=0   默认关闭；安装 ddddocr 后设为 1 启用（约 200MB+，不随主安装包分发）
  CAPTCHA_VISION_FALLBACK=1  L1 失败后调用 VLM
  CAPTCHA_VISION_TIMEOUT     VLM 单次超时秒数（默认 25）
  CAPTCHA_MAX_RETRY=0        刷新后再开的轮数（需 CAPTCHA_AUTO_REFRESH=1；默认 0 不刷新）
  CAPTCHA_SOLVE_RETRY=3      同一张验证码上重复求解次数（不刷新，默认 3）
  CAPTCHA_SOLVE_RETRY_DELAY  同题重试间隔秒数（默认 0.35）
  CAPTCHA_REFRESH_ROUNDS     显式刷新轮数（默认同 MAX_RETRY；AUTO_REFRESH=0 时忽略）
  CAPTCHA_CURVE_CONF_MIN=0.70 曲线算法置信度阈值，低于则优先 VLM
  CAPTCHA_DESKTOP_SCALE=0.5  Desktop 截图识别缩放比
  CAPTCHA_DRAG_STEPS         拖动轨迹步数（默认 24）
  CAPTCHA_OVERSHOOT_PX       轻微 overshoot 像素（默认 3）
  CAPTCHA_WORKER_TIMEOUT     verify 步骤 worker 超时秒数（默认 180）
  CAPTCHA_AUTO_REFRESH=0     失败后是否点击验证码内刷新（默认关，避免换题浪费）
  CAPTCHA_ALLOW_HEURISTIC_SLIDE  缺口失败时是否启发式滑到底（默认 0）
"""
from __future__ import annotations

import importlib
import json
import os
import random
import re
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

from modules.core.logger import uat_logger
from modules.core.optional_cv2 import cv2, require_cv2

# ---------------------------------------------------------------------------
# 状态回调（供 UI 显示「正在识别…」「正在 AI 分析…」）
# ---------------------------------------------------------------------------

_status_lock = threading.Lock()
_status_callback: Optional[Callable[[str], None]] = None
_ddddocr_installed_cache: Optional[bool] = None


def set_captcha_status_callback(fn: Optional[Callable[[str], None]]) -> None:
    global _status_callback
    with _status_lock:
        _status_callback = fn


def emit_captcha_status(message: str) -> None:
    msg = (message or "").strip()
    if not msg:
        return
    uat_logger.info("[CAPTCHA_STATUS] %s", msg)
    with _status_lock:
        cb = _status_callback
    if cb:
        try:
            cb(msg)
        except Exception as e:
            uat_logger.debug("captcha status callback error: %s", e)


def captcha_auto_refresh_enabled() -> bool:
    """是否在全部同题重试失败后点击验证码组件内的「刷新」换题（默认关闭）。"""
    return _env_bool("CAPTCHA_AUTO_REFRESH", False)


def captcha_solve_attempts() -> int:
    """同一张验证码图片上重复求解次数（不刷新），默认 3。"""
    raw = (os.environ.get("CAPTCHA_SOLVE_RETRY") or os.environ.get("CAPTCHA_SOLVE_ATTEMPTS") or "3").strip()
    try:
        return max(1, min(int(raw), 8))
    except ValueError:
        return 3


def resolve_captcha_solve_attempts(step_max: Optional[int] = None) -> int:
    """步骤级最大验证次数优先，否则使用环境变量 CAPTCHA_SOLVE_RETRY。"""
    if step_max is not None:
        try:
            n = int(step_max)
            if n >= 1:
                return max(1, min(n, 20))
        except (TypeError, ValueError):
            pass
    return captcha_solve_attempts()


def captcha_requires_user_scope() -> bool:
    """verify 步骤是否必须拾取验证码区域（默认是）。"""
    return _env_bool("CAPTCHA_REQUIRE_USER_SCOPE", True)


def captcha_solve_retry_delay() -> float:
    raw = (os.environ.get("CAPTCHA_SOLVE_RETRY_DELAY") or "0.35").strip()
    try:
        return max(0.1, min(float(raw), 3.0))
    except ValueError:
        return 0.35


_solve_attempt_index = 1


def set_captcha_solve_attempt_index(attempt: int) -> None:
    """由 recovery 状态机在每次同题重试前设置（用于距离微调）。"""
    global _solve_attempt_index
    _solve_attempt_index = max(1, int(attempt))


def captcha_distance_retry_offset() -> int:
    """同题第 N 次求解时对拖动距离的微调（像素）。"""
    offsets = (0, -5, 6, -8, 10, -10, 8)
    return offsets[(_solve_attempt_index - 1) % len(offsets)]


def captcha_refresh_rounds() -> int:
    """求解全部失败后，刷新换题再开的轮数（需 CAPTCHA_AUTO_REFRESH=1）。"""
    if not captcha_auto_refresh_enabled():
        return 0
    raw = (os.environ.get("CAPTCHA_REFRESH_ROUNDS") or os.environ.get("CAPTCHA_MAX_RETRY") or "0").strip()
    try:
        return max(0, min(int(raw), 2))
    except ValueError:
        return 0


def captcha_total_solve_slots() -> int:
    """用于 UI 展示的总求解次数 = 每轮求解次数 × (1 + 刷新轮数)。"""
    return captcha_solve_attempts() * (1 + captcha_refresh_rounds())


def captcha_worker_timeout() -> int:
    """Playwright worker 执行 verify 步骤的超时（秒），验证码含 VLM/重试需更长。"""
    raw = (os.environ.get("CAPTCHA_WORKER_TIMEOUT") or "180").strip()
    try:
        return max(60, min(int(raw), 600))
    except ValueError:
        return 180


def captcha_allow_heuristic_slide() -> bool:
    """是否允许在缺口识别失败时用容器比例等启发式距离（默认关，易滑到底）。"""
    return _env_bool("CAPTCHA_ALLOW_HEURISTIC_SLIDE", False)


def clamp_slider_distance(distance: int, track_width: int, slider_width: int = 0) -> int:
    """将拖动距离限制在轨道可用范围内。"""
    if distance <= 0 or track_width <= 0:
        return 0
    margin = max(8, int(slider_width * 0.5) if slider_width else 10)
    max_dist = max(margin, int(track_width - slider_width - margin))
    return max(margin, min(int(distance), max_dist))


def captcha_max_retry() -> int:
    """兼容旧配置：等同 captcha_refresh_rounds（刷新换题轮数，非同题重试次数）。"""
    return captcha_refresh_rounds()


def captcha_vision_timeout() -> int:
    raw = (os.environ.get("CAPTCHA_VISION_TIMEOUT") or "").strip()
    if raw.isdigit():
        return max(5, min(int(raw), 120))
    return 25


def captcha_curve_confidence_min() -> float:
    raw = (os.environ.get("CAPTCHA_CURVE_CONF_MIN") or "0.70").strip()
    try:
        return max(0.0, min(float(raw), 1.0))
    except ValueError:
        return 0.70


def desktop_recognition_scale() -> float:
    raw = (os.environ.get("CAPTCHA_DESKTOP_SCALE") or "0.5").strip()
    try:
        s = float(raw)
        return s if 0.25 <= s <= 1.0 else 0.5
    except ValueError:
        return 0.5

# ---------------------------------------------------------------------------
# 环境 / 可选依赖
# ---------------------------------------------------------------------------

_TIANAI_SELECTORS = (
    "#captcha-box",
    "#tianai-captcha",
    '[id*="tianai"]',
    "#slider-move-btn",
    ".slider-move-btn",
    ".slider-move-track",
    ".slider-img-div",
    'canvas[class*="captcha"]',
    '[class*="tianai"]',
)

_CAPTCHA_CONTAINER_SELECTORS = (
    ".captcha-box",
    ".verification-box",
    ".verify-box",
    "#captcha",
    "#verification",
    '[class*="captcha"]',
    '[class*="verification"]',
    '[class*="verify"]',
    ".slider-container",
    ".slide-container",
    ".captcha-slider",
    '[class*="slider"]',
    '[class*="slide"]',
) + _TIANAI_SELECTORS


def _env_bool(name: str, default: bool) -> bool:
    v = (os.environ.get(name) or "").strip().lower()
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off"):
        return False
    return default


def ddddocr_enabled() -> bool:
    """仅当用户显式开启且已安装 ddddocr 时启用（默认关闭，不随主包分发）。"""
    if not _env_bool("CAPTCHA_DDDDOCR_ENABLE", False):
        return False
    return is_ddddocr_installed()


def is_ddddocr_installed() -> bool:
    global _ddddocr_installed_cache
    if _ddddocr_installed_cache is not None:
        return _ddddocr_installed_cache
    try:
        importlib.import_module("ddddocr")
        _ddddocr_installed_cache = True
    except ImportError:
        _ddddocr_installed_cache = False
    return _ddddocr_installed_cache


def invalidate_ddddocr_cache() -> None:
    global _ddddocr_installed_cache
    _ddddocr_installed_cache = None


def get_ddddocr_install_info() -> Dict[str, Any]:
    """供 API / UI 展示可选组件状态与安装指引。"""
    import sys

    installed = is_ddddocr_installed()
    enabled = ddddocr_enabled()
    return {
        "component": "ddddocr",
        "installed": installed,
        "enabled": enabled,
        "bundled_in_main_installer": False,
        "estimated_size_mb": "200+",
        "pip_command": f'"{sys.executable}" -m pip install ddddocr',
        "hint_zh": (
            "ddddocr 为可选高级组件（约 200MB+，含 onnxruntime），未随主安装包分发。"
            "滑块缺口识别更准，需单独安装。安装后设置 CAPTCHA_DDDDOCR_ENABLE=1 并重启平台。"
            if not installed
            else "ddddocr 已安装。设置 CAPTCHA_DDDDOCR_ENABLE=1 并重启后可启用滑块增强识别。"
        ),
        "enable_env": "CAPTCHA_DDDDOCR_ENABLE=1",
    }


def install_ddddocr_subprocess() -> Dict[str, Any]:
    """一键安装 ddddocr（子进程 pip，不阻塞打包体积）。"""
    import subprocess
    import sys

    if is_ddddocr_installed():
        return {"success": True, "message": "ddddocr 已安装", "installed": True}
    emit_captcha_status("正在安装 ddddocr 可选组件（约 200MB，请稍候）…")
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pip", "install", "ddddocr"],
            capture_output=True,
            text=True,
            timeout=600,
        )
        invalidate_ddddocr_cache()
        ok = proc.returncode == 0 and is_ddddocr_installed()
        return {
            "success": ok,
            "installed": is_ddddocr_installed(),
            "stdout": (proc.stdout or "")[-2000:],
            "stderr": (proc.stderr or "")[-2000:],
            "message": "ddddocr 安装成功，请设置 CAPTCHA_DDDDOCR_ENABLE=1 并重启"
            if ok
            else f"安装失败（exit {proc.returncode}）",
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "message": "安装超时（>10 分钟）", "installed": False}
    except Exception as e:
        return {"success": False, "message": str(e), "installed": False}


def vision_fallback_enabled() -> bool:
    return _env_bool("CAPTCHA_VISION_FALLBACK", True)


def captcha_container_selectors() -> Tuple[str, ...]:
    return _CAPTCHA_CONTAINER_SELECTORS


def tianai_selectors() -> Tuple[str, ...]:
    return _TIANAI_SELECTORS


def _get_ddddocr_slide():
    if not ddddocr_enabled():
        return None
    try:
        ddddocr = importlib.import_module("ddddocr")
        return ddddocr.DdddOcr(det=False, ocr=False, show_ad=False)
    except Exception as e:
        uat_logger.debug("ddddocr slide unavailable: %s", e)
        return None


def _get_ddddocr_det():
    if not ddddocr_enabled():
        return None
    try:
        ddddocr = importlib.import_module("ddddocr")
        return ddddocr.DdddOcr(det=True, ocr=False, show_ad=False)
    except Exception as e:
        uat_logger.debug("ddddocr det unavailable: %s", e)
        return None


def _png_to_bgr(png_bytes: bytes) -> Optional[np.ndarray]:
    if not png_bytes or cv2 is None:
        return None
    arr = np.frombuffer(png_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    return img


def _png_to_gray(png_bytes: bytes) -> Optional[np.ndarray]:
    img = _png_to_bgr(png_bytes)
    if img is None or cv2 is None:
        return None
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


def prepare_recognition_png(
    png_bytes: bytes,
    *,
    scale: Optional[float] = None,
    for_desktop: bool = False,
) -> Tuple[bytes, float]:
    """
    识别前缩放 PNG，降低 Desktop 截图传输/推理耗时。
    返回 (png_for_recognition, coord_multiplier)，坐标/距离需乘以 multiplier 映射回原始尺寸。
    """
    if not png_bytes:
        return png_bytes, 1.0
    s = scale
    if s is None:
        s = desktop_recognition_scale() if for_desktop else 1.0
    if s >= 0.99:
        return png_bytes, 1.0
    img = _png_to_bgr(png_bytes)
    if img is None:
        return png_bytes, 1.0
    h, w = img.shape[:2]
    nw, nh = max(8, int(w * s)), max(8, int(h * s))
    resized = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".png", resized)
    if not ok:
        return png_bytes, 1.0
    mult = w / max(nw, 1)
    return buf.tobytes(), mult


# ---------------------------------------------------------------------------
# 平台 / 类型检测
# ---------------------------------------------------------------------------

def detect_platform(html_or_text: str = "") -> str:
    """从 HTML/innerText 推断验证码平台。"""
    blob = (html_or_text or "").lower()
    platforms = (
        ("tianai", ("tianai", "tac-", "slider-move-btn", "天爱")),
        ("geetest", ("geetest", "极验")),
        ("tcaptcha", ("tcaptcha", "腾讯验证")),
        ("yidun", ("yidun", "易盾", "nc_wrapper")),
        ("aliyun", ("aliyun", "ac-slider", "阿里云")),
    )
    for name, keywords in platforms:
        if any(k in blob for k in keywords):
            return name
    return "default"


def resolve_captcha_type(instruction: str = "", captcha_html: str = "") -> str:
    """
    推断验证码类型：优先使用验证码区域内的指令文本，避免整页导航栏干扰。
    captcha_html 应仅为验证码容器内的 HTML 片段，勿传入整页 content。
    """
    instr = (instruction or "").strip()
    if instr:
        t = detect_captcha_type(instr, "")
        if t != "unknown":
            return t
    html = (captcha_html or "").strip()
    if html:
        t = detect_captcha_type("", html)
        if t != "unknown":
            return t
    return "unknown"


def png_image_width(png_bytes: bytes) -> int:
    gray = _png_to_gray(png_bytes)
    if gray is None:
        return 0
    return int(gray.shape[1])


def scale_image_distance_to_track(
    distance_px: int,
    image_width_px: int,
    track_width_px: int,
    *,
    slider_width_px: int = 0,
) -> int:
    """将缺口在背景图上的像素距离映射为轨道上的拖动距离。"""
    if distance_px <= 0:
        return 0
    if image_width_px > 0 and track_width_px > 0:
        scaled = int(round(distance_px * track_width_px / image_width_px))
    else:
        scaled = int(distance_px)
    if track_width_px > 0:
        return clamp_slider_distance(scaled, track_width_px, slider_width_px)
    return max(0, scaled)


def detect_captcha_type(instruction_text: str = "", html_hint: str = "") -> str:
    """
    推断验证码类型：slider | curve | rotate | concat | click_text | click_icon | unknown
    """
    zh = (instruction_text or "") + (html_hint or "")
    blob = zh.lower()
    if any(k in zh for k in ("依次点击", "点选", "请点击")) or (
        "点击" in zh and any(k in zh for k in ("依次", "选择", "：", ":"))
    ):
        if any(k in zh for k in ("图标", "icon")):
            return "click_icon"
        return "click_text"
    if any(k in zh for k in ("曲线", "使曲线匹配", "曲线匹配")) or "curve" in blob:
        return "curve"
    if any(k in zh for k in ("旋转", "转动")) or "rotate" in blob:
        return "rotate"
    if any(k in zh for k in ("还原", "滑动还原", "拼接")) or "concat" in blob:
        return "concat"
    if any(k in zh for k in ("滑块", "拖动", "滑动")) or "slider" in blob:
        return "slider"
    if "canvas" in blob and "tianai" in blob:
        return "curve"
    return "unknown"


# ---------------------------------------------------------------------------
# Level 1 求解
# ---------------------------------------------------------------------------

def solve_slider_gap(bg_png: bytes, slider_png: Optional[bytes] = None) -> Optional[int]:
    """
    拼图滑块缺口水平偏移（像素）。OpenCV 为主；ddddocr 为可选增强。
    """
    require_cv2("滑块验证码识别")
    if not bg_png:
        return None

    if ddddocr_enabled():
        emit_captcha_status("正在使用 ddddocr 识别滑块缺口…")
        ocr = _get_ddddocr_slide()
        if ocr and slider_png:
            try:
                result = ocr.slide_match(slider_png, bg_png, simple_target=True)
                if isinstance(result, dict):
                    target = result.get("target")
                    if isinstance(target, (list, tuple)) and target:
                        dist = int(target[0])
                        if 0 < dist < 800:
                            uat_logger.info("[CAPTCHA] ddddocr slide_match distance=%spx", dist)
                            return dist
                elif isinstance(result, (int, float)) and 0 < result < 800:
                    uat_logger.info("[CAPTCHA] ddddocr slide_match distance=%spx", int(result))
                    return int(result)
            except Exception as e:
                uat_logger.debug("ddddocr slide_match failed: %s", e)
    elif _env_bool("CAPTCHA_DDDDOCR_ENABLE", False) and not is_ddddocr_installed():
        uat_logger.info(
            "[CAPTCHA] CAPTCHA_DDDDOCR_ENABLE=1 但 ddddocr 未安装，已回退 OpenCV。"
            "可通过 /api/captcha/optional-deps/install 安装。"
        )

    emit_captcha_status("正在用图像算法识别滑块缺口…")
    return _solve_slider_gap_opencv(bg_png, slider_png)


def _solve_gap_by_gradient(gray: np.ndarray) -> Optional[int]:
    """拼图缺口：垂直边缘能量峰值（含平滑与次峰选取）。"""
    w_img = gray.shape[1]
    if w_img < 40:
        return None
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    sobel = cv2.Sobel(blurred, cv2.CV_64F, 1, 0, ksize=3)
    col_energy = np.mean(np.abs(sobel), axis=0)
    kernel = np.array([0.05, 0.1, 0.2, 0.3, 0.2, 0.1, 0.05])
    col_energy = np.convolve(col_energy, kernel, mode="same")
    start = int(w_img * 0.12)
    end = int(w_img * 0.92)
    region = col_energy[start:end]
    if len(region) < 10:
        return None
    peaks: List[Tuple[float, int]] = []
    for i in range(2, len(region) - 2):
        v = region[i]
        if v >= region[i - 1] and v >= region[i + 1] and v >= region[i - 2] and v >= region[i + 2]:
            if v >= region.max() * 0.35:
                peaks.append((float(v), i + start))
    if not peaks:
        peak = int(np.argmax(region)) + start
        if start < peak < end:
            uat_logger.info("[CAPTCHA] gradient gap x=%spx (img_w=%s)", peak, w_img)
            return peak
        return None
    peaks.sort(key=lambda t: t[0], reverse=True)
    # 优先取能量强且偏右的峰（缺口多在右半区）
    best = max(peaks[:4], key=lambda t: t[0] * 0.6 + (t[1] / max(w_img, 1)) * 0.4 * peaks[0][0])
    uat_logger.info("[CAPTCHA] gradient gap x=%spx peaks=%s (img_w=%s)", best[1], len(peaks), w_img)
    return best[1]


def _template_match_gap(gray: np.ndarray, tpl: np.ndarray) -> Optional[Tuple[int, float]]:
    """多方法/多尺度模板匹配，返回 (缺口 x, 置信度)。"""
    if tpl is None or tpl.shape[0] >= gray.shape[0] or tpl.shape[1] >= gray.shape[1]:
        return None
    best_x, best_conf = -1, 0.0
    methods = (cv2.TM_CCOEFF_NORMED, cv2.TM_CCORR_NORMED)
    for scale in (1.0, 0.92, 1.08):
        if abs(scale - 1.0) > 0.01:
            th, tw = max(4, int(tpl.shape[0] * scale)), max(4, int(tpl.shape[1] * scale))
            scaled = cv2.resize(tpl, (tw, th), interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR)
        else:
            scaled = tpl
        if scaled.shape[0] >= gray.shape[0] or scaled.shape[1] >= gray.shape[1]:
            continue
        for method in methods:
            try:
                result = cv2.matchTemplate(gray, scaled, method)
                _, max_val, _, max_loc = cv2.minMaxLoc(result)
                if max_val > best_conf:
                    best_conf = float(max_val)
                    best_x = int(max_loc[0])
            except cv2.error:
                continue
    # 边缘图再匹配一次（对光照变化更稳）
    gray_e = cv2.Canny(cv2.GaussianBlur(gray, (3, 3), 0), 50, 150)
    tpl_e = cv2.Canny(cv2.GaussianBlur(tpl, (3, 3), 0), 50, 150)
    if tpl_e.shape[0] < gray_e.shape[0] and tpl_e.shape[1] < gray_e.shape[1] and cv2.countNonZero(tpl_e) > 20:
        try:
            result = cv2.matchTemplate(gray_e, tpl_e, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(result)
            if max_val > best_conf * 0.85:
                best_conf = max(best_conf, float(max_val))
                best_x = int(max_loc[0])
        except cv2.error:
            pass
    if best_x >= 0 and best_conf > 0.35:
        return best_x, best_conf
    return None


def _solve_gap_by_edge_diff(bgr: np.ndarray) -> Optional[int]:
    """彩色拼图：通道差分 + 垂直边缘找缺口接缝。"""
    if bgr is None or bgr.size == 0:
        return None
    h, w = bgr.shape[:2]
    if w < 60:
        return None
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)
    l_ch = lab[:, :, 0]
    edges = cv2.Canny(cv2.GaussianBlur(l_ch, (5, 5), 0), 40, 120)
    col = np.sum(edges > 0, axis=0).astype(np.float32)
    col = np.convolve(col, np.array([0.08, 0.15, 0.25, 0.3, 0.25, 0.15, 0.08]), mode="same")
    lo, hi = int(w * 0.18), int(w * 0.90)
    region = col[lo:hi]
    if len(region) < 12:
        return None
    peak = int(np.argmax(region)) + lo
    if region.max() >= max(3.0, col.max() * 0.4) and lo < peak < hi:
        uat_logger.info("[CAPTCHA] edge-diff gap x=%spx (img_w=%s)", peak, w)
        return peak
    return None


def _pick_slider_gap_weighted(candidates: List[Tuple[int, float]], img_width: int) -> Optional[int]:
    """加权融合多个缺口候选（x, weight）。"""
    if not candidates or img_width <= 0:
        return None
    lo = max(8, int(img_width * 0.06))
    hi = int(img_width * 0.94)
    filtered = [(int(x), float(w)) for x, w in candidates if lo <= x <= hi and w > 0]
    if not filtered:
        return None
    filtered.sort(key=lambda t: t[0])
    clusters: List[List[Tuple[int, float]]] = []
    for x, w in filtered:
        if not clusters or x - clusters[-1][-1][0] > max(18, int(img_width * 0.06)):
            clusters.append([(x, w)])
        else:
            clusters[-1].append((x, w))
    def cluster_score(group: List[Tuple[int, float]]) -> float:
        return sum(w for _, w in group) + len(group) * 0.15

    best = max(clusters, key=cluster_score)
    total_w = sum(w for _, w in best)
    if total_w <= 0:
        return best[0][0]
    x_weighted = int(round(sum(x * w for x, w in best) / total_w))
    uat_logger.info(
        "[CAPTCHA] fused gap=%spx from %s methods (clusters=%s)",
        x_weighted,
        len(filtered),
        len(clusters),
    )
    return x_weighted


def _pick_slider_gap(candidates: List[int], img_width: int) -> Optional[int]:
    """从多个识别结果中选取最 plausible 的缺口位置（兼容旧接口）。"""
    weighted = [(c, 1.0) for c in candidates]
    return _pick_slider_gap_weighted(weighted, img_width)


def _solve_slider_gap_opencv(bg_png: bytes, slider_png: Optional[bytes] = None) -> Optional[int]:
    gray = _png_to_gray(bg_png)
    if gray is None:
        return None

    bgr = _png_to_bgr(bg_png)
    candidates: List[Tuple[int, float]] = []
    w_img = gray.shape[1]

    if slider_png:
        tpl = _png_to_gray(slider_png)
        if tpl is not None:
            matched = _template_match_gap(gray, tpl)
            if matched:
                candidates.append((matched[0], matched[1] * 1.2))

    contour_gap = _solve_gap_by_contours(gray)
    if contour_gap:
        candidates.append((contour_gap, 0.75))
    grad_gap = _solve_gap_by_gradient(gray)
    if grad_gap:
        candidates.append((grad_gap, 0.85))
    if bgr is not None:
        diff_gap = _solve_gap_by_edge_diff(bgr)
        if diff_gap:
            candidates.append((diff_gap, 0.9))

    picked = _pick_slider_gap_weighted(candidates, w_img)
    if picked:
        uat_logger.info("[CAPTCHA] OpenCV fused gap=%spx from %s candidates", picked, len(candidates))
    return picked


def _solve_gap_by_contours(gray: np.ndarray) -> Optional[int]:
    blurred = cv2.GaussianBlur(gray, (7, 7), 0)
    thresh = cv2.adaptiveThreshold(
        blurred, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 15, 3
    )
    edges = cv2.Canny(thresh, 20, 80)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel)
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    candidates: List[Tuple[float, int]] = []
    w_img = gray.shape[1]
    for contour in contours:
        area = cv2.contourArea(contour)
        if 80 < area < 4000:
            x, y, cw, ch = cv2.boundingRect(contour)
            ratio = cw / ch if ch > 0 else 0
            if 1.0 < ratio < 6 and x > w_img * 0.25:
                candidates.append((area, x + cw // 2))

    if not candidates:
        return None
    candidates.sort(key=lambda t: t[0], reverse=True)
    gap_x = candidates[0][1]
    w_img = gray.shape[1]
    # 返回相对左缘的缺口中心 x；拼图滑块通常从左端出发，近似为拖动距离
    if 10 < gap_x < int(w_img * 0.92):
        uat_logger.info("[CAPTCHA] contour gap center x=%spx (img_w=%s)", gap_x, w_img)
        return gap_x
    return None


def solve_curve_offset(bg_png: bytes) -> Optional[int]:
    """滑动曲线验证码：返回最佳 offset，低置信度时返回 None。"""
    off, conf = solve_curve_offset_with_confidence(bg_png)
    if off is not None and conf >= captcha_curve_confidence_min():
        return off
    if off is not None:
        uat_logger.info("[CAPTCHA] curve offset confidence %.3f < threshold, defer to VLM", conf)
    return None


def solve_curve_offset_with_confidence(bg_png: bytes) -> Tuple[Optional[int], float]:
    """
    滑动曲线验证码：提取高亮曲线层，搜索最佳水平 offset。
    返回 (offset, confidence 0~1)。
    """
    img = _png_to_bgr(bg_png)
    if img is None:
        return None, 0.0

    h, w = img.shape[:2]
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    mask_bright = cv2.inRange(hsv, (0, 0, 180), (180, 80, 255))
    mask_white = cv2.inRange(img, (200, 200, 200), (255, 255, 255))
    mask = cv2.bitwise_or(mask_bright, mask_white)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

    if cv2.countNonZero(mask) < 50:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        _, mask = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY)

    col_sum = np.sum(mask > 0, axis=0).astype(np.float32)
    if col_sum.max() < 3:
        return None, 0.0

    mid = w // 2
    left_part = mask[:, :mid]
    right_part = mask[:, mid:]
    if left_part.shape[1] < 20 or right_part.shape[1] < 20:
        return None, 0.0

    best_offset = 0
    best_score = -1.0
    max_shift = min(mid - 10, 200)
    for shift in range(5, max_shift):
        overlap_w = min(left_part.shape[1] - shift, right_part.shape[1])
        if overlap_w < 15:
            continue
        l_slice = left_part[:, shift : shift + overlap_w]
        r_slice = right_part[:, :overlap_w]
        score = float(np.mean((l_slice > 0) & (r_slice > 0)))
        if score > best_score:
            best_score = score
            best_offset = shift

    # 亚像素级精修：在最佳 offset 附近用边缘图再对齐
    if best_score > 0.03 and best_offset > 0:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        edge = cv2.Canny(cv2.GaussianBlur(gray, (3, 3), 0), 60, 140)
        le = edge[:, :mid]
        re = edge[:, mid:]
        refine_lo = max(5, best_offset - 8)
        refine_hi = min(max_shift, best_offset + 8)
        for shift in range(refine_lo, refine_hi + 1):
            ow = min(le.shape[1] - shift, re.shape[1])
            if ow < 12:
                continue
            es = float(np.mean((le[:, shift : shift + ow] > 0) & (re[:, :ow] > 0)))
            if es > best_score:
                best_score = es
                best_offset = shift

    if best_score > 0.05 and 0 < best_offset < 500:
        min_offset = max(15, int(w * 0.10))
        if best_offset < min_offset:
            uat_logger.info(
                "[CAPTCHA] curve offset=%spx too small (min=%spx), ignore false positive",
                best_offset,
                min_offset,
            )
            return None, min(1.0, best_score * 4.0)
        uat_logger.info("[CAPTCHA] curve offset=%spx score=%.3f", best_offset, best_score)
        return best_offset, min(1.0, best_score * 4.0)

    peaks = np.where(col_sum > col_sum.max() * 0.4)[0]
    if len(peaks) >= 2:
        dist = int(peaks[-1] - peaks[0])
        min_dist = max(15, int(w * 0.10))
        if min_dist <= dist < 400:
            conf = 0.55
            uat_logger.info("[CAPTCHA] curve peak distance=%spx conf=%.2f", dist, conf)
            return dist, conf
    return None, max(0.0, best_score)


def solve_rotate_angle(bg_png: bytes) -> Optional[int]:
    """旋转验证码：估计需旋转角度（0-360）。"""
    img = _png_to_bgr(bg_png)
    if img is None:
        return None
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 50, 150)
    circles = cv2.HoughCircles(
        gray, cv2.HOUGH_GRADIENT, 1, min(gray.shape) // 4, param1=80, param2=40, minRadius=20, maxRadius=0
    )
    if circles is not None and len(circles[0]) > 0:
        # 简化：默认 90 度步进尝试由上层拖动映射
        return 90
    lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=60, minLineLength=30, maxLineGap=10)
    if lines is not None and len(lines) > 0:
        angles = []
        for line in lines[:20]:
            x1, y1, x2, y2 = line[0]
            ang = np.degrees(np.arctan2(y2 - y1, x2 - x1))
            angles.append(ang)
        if angles:
            median = float(np.median(angles))
            return int(abs(median) % 180)
    return None


def solve_concat_offset(bg_png: bytes) -> Optional[int]:
    """滑动还原：水平拼接 offset，类似 curve。"""
    return solve_curve_offset(bg_png)


def _parse_instruction_targets(instruction: str) -> List[str]:
    s = (instruction or "").strip()
    if not s:
        return []
    m = re.search(
        r"(?:点击|选择|依次点击|请点击|请依次点击)\s*[：: ]?\s*[“\"'「]?(.*?)[”\"'」]?(?:$|。|，|,)",
        s,
    )
    body = m.group(1).strip() if m else s
    body = body.replace("依次", "").replace("点击", "").replace("选择", "").strip()
    if not body:
        return []
    parts = re.split(r"[、，,;\s]+", body)
    out: List[str] = []
    for p in parts:
        t = p.strip().strip("“”\"'「」")
        if len(t) >= 1:
            out.append(t)
    expanded: List[str] = []
    for p in out:
        if len(p) > 1 and all("\u4e00" <= c <= "\u9fff" for c in p):
            expanded.extend(list(p))
        else:
            expanded.append(p)
    return expanded[:8]


def parse_instruction_targets(instruction: str) -> List[str]:
    """从验证码指令解析需依次点击的文字/图标列表。"""
    return _parse_instruction_targets(instruction)


def _char_match(a: str, b: str) -> bool:
    """单字精确匹配（禁止子串误匹配如 堪↔勘）。"""
    a = (a or "").strip()
    b = (b or "").strip()
    if not a or not b:
        return False
    if a == b:
        return True
    if len(a) == 1 and len(b) == 1:
        return a == b
    return False


def _find_char_blobs_colored(bgr: np.ndarray) -> List[Tuple[int, int, int, int]]:
    """彩色点选验证码：高饱和度文字区域。"""
    if bgr is None or bgr.size == 0:
        return []
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    sat = hsv[:, :, 1]
    val = hsv[:, :, 2]
    mask = cv2.inRange(hsv, (0, 70, 60), (180, 255, 255))
    mask = cv2.bitwise_and(mask, cv2.inRange(val, 40, 240))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2)))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    h_img, w_img = bgr.shape[:2]
    blobs: List[Tuple[int, int, int, int, float]] = []
    for c in contours:
        x, y, cw, ch = cv2.boundingRect(c)
        area = cw * ch
        if area < 80 or area > w_img * h_img * 0.12:
            continue
        ratio = cw / max(ch, 1)
        if ratio < 0.2 or ratio > 5.0 or cw < 8 or ch < 8:
            continue
        mean_sat = float(np.mean(sat[y : y + ch, x : x + cw]))
        blobs.append((x, y, cw, ch, mean_sat + area * 0.01))
    blobs.sort(key=lambda t: t[4], reverse=True)
    picked: List[Tuple[int, int, int, int]] = []
    for x, y, cw, ch, _ in blobs:
        cx, cy = x + cw // 2, y + ch // 2
        if any(abs(cx - (px + pw // 2)) < 10 and abs(cy - (py + ph // 2)) < 10 for px, py, pw, ph in picked):
            continue
        picked.append((x, y, cw, ch))
    return picked


def _find_char_blobs(gray: np.ndarray) -> List[Tuple[int, int, int, int]]:
    """在验证码背景图中定位疑似单字区域。"""
    h_img, w_img = gray.shape[:2]
    blobs: List[Tuple[int, int, int, int, float]] = []
    for proc in (
        cv2.adaptiveThreshold(
            cv2.GaussianBlur(gray, (3, 3), 0),
            255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY_INV,
            31,
            8,
        ),
        cv2.bitwise_not(
            cv2.adaptiveThreshold(
                cv2.GaussianBlur(gray, (5, 5), 0),
                255,
                cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                cv2.THRESH_BINARY,
                31,
                5,
            )
        ),
        cv2.threshold(cv2.GaussianBlur(gray, (3, 3), 0), 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1],
    ):
        contours, _ = cv2.findContours(proc, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for c in contours:
            x, y, cw, ch = cv2.boundingRect(c)
            area = cw * ch
            if area < 100 or area > w_img * h_img * 0.15:
                continue
            ratio = cw / max(ch, 1)
            if ratio < 0.25 or ratio > 4.5:
                continue
            if cw < 10 or ch < 10:
                continue
            blobs.append((x, y, cw, ch, float(area) - y * 0.5))
    # 去重：中心相近的框合并
    blobs.sort(key=lambda t: t[4], reverse=True)
    picked: List[Tuple[int, int, int, int]] = []
    for x, y, cw, ch, _ in blobs:
        cx, cy = x + cw // 2, y + ch // 2
        if any(abs(cx - (px + pw // 2)) < 8 and abs(cy - (py + ph // 2)) < 8 for px, py, pw, ph in picked):
            continue
        picked.append((x, y, cw, ch))
    return picked


def _ocr_char_roi(pyt, gray: np.ndarray, x: int, y: int, w: int, h: int) -> Tuple[str, float]:
    pad = max(2, min(w, h) // 4)
    y0, x0 = max(0, y - pad), max(0, x - pad)
    y1, x1 = min(gray.shape[0], y + h + pad), min(gray.shape[1], x + w + pad)
    roi = gray[y0:y1, x0:x1]
    if roi.size == 0:
        return "", 0.0
    best_ch, best_conf = "", 0.0
    for fx in (2.0, 2.5, 3.0):
        scaled = cv2.resize(roi, None, fx=fx, fy=fx, interpolation=cv2.INTER_CUBIC)
        for proc in (scaled, cv2.bitwise_not(scaled)):
            _, bin_img = cv2.threshold(proc, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            for img_in in (proc, bin_img):
                for psm in ("10", "8", "7"):
                    try:
                        txt = pyt.image_to_string(
                            img_in, lang="chi_sim", config=f"--psm {psm} --oem 1"
                        ).strip()
                        conf_raw = pyt.image_to_data(
                            img_in, lang="chi_sim", config=f"--psm {psm}", output_type=pyt.Output.DICT
                        )
                        confs = [
                            float(c)
                            for c in conf_raw.get("conf", [])
                            if str(c).lstrip("-").isdigit() and float(c) >= 0
                        ]
                        conf = max(confs) if confs else 50.0
                        ch = txt[:1] if txt else ""
                        if ch and conf > best_conf:
                            best_ch, best_conf = ch, conf
                    except Exception:
                        continue
    return best_ch, best_conf


def _match_click_targets_tesseract(bg_png: bytes, targets: List[str]) -> List[Tuple[int, int]]:
    try:
        pyt = importlib.import_module("pytesseract")
    except Exception:
        return []
    gray = _png_to_gray(bg_png)
    if gray is None:
        return []
    data = pyt.image_to_data(gray, lang="chi_sim+eng", output_type=pyt.Output.DICT)
    n = len(data.get("text", []))
    boxes: List[Dict[str, Any]] = []
    for i in range(n):
        txt = (data["text"][i] or "").strip()
        if not txt:
            continue
        try:
            conf = float(data["conf"][i])
        except Exception:
            conf = 0.0
        if conf < 25:
            continue
        x, y = int(data["left"][i]), int(data["top"][i])
        w, h = int(data["width"][i]), int(data["height"][i])
        if w < 6 or h < 6:
            continue
        boxes.append({"text": txt, "conf": conf, "x": x, "y": y, "w": w, "h": h})

    used: set = set()
    points: List[Tuple[int, int]] = []
    for target in targets:
        best_idx = None
        best_score = -1.0
        for idx, b in enumerate(boxes):
            if idx in used:
                continue
            if not _char_match(b["text"], target):
                continue
            score = b["conf"] + (10 if b["text"] == target else 0)
            if score > best_score:
                best_score = score
                best_idx = idx
        if best_idx is None:
            return []
        used.add(best_idx)
        b = boxes[best_idx]
        points.append((b["x"] + b["w"] // 2, b["y"] + b["h"] // 2))
    return points


def _match_click_targets_blob_ocr(bg_png: bytes, targets: List[str]) -> List[Tuple[int, int]]:
    try:
        pyt = importlib.import_module("pytesseract")
    except Exception:
        return []
    gray = _png_to_gray(bg_png)
    if gray is None:
        return []
    bgr = _png_to_bgr(bg_png)
    blobs = _find_char_blobs(gray)
    if bgr is not None:
        colored = _find_char_blobs_colored(bgr)
        seen = {(x + w // 2, y + h // 2) for x, y, w, h in blobs}
        for x, y, w, h in colored:
            cx, cy = x + w // 2, y + h // 2
            if not any(abs(cx - sx) < 12 and abs(cy - sy) < 12 for sx, sy in seen):
                blobs.append((x, y, w, h))
                seen.add((cx, cy))
    labeled: List[Tuple[int, int, str, float]] = []
    for x, y, w, h in blobs:
        ch, conf = _ocr_char_roi(pyt, gray, x, y, w, h)
        if ch:
            labeled.append((x + w // 2, y + h // 2, ch, conf))

    used: set = set()
    points: List[Tuple[int, int]] = []
    for target in targets:
        best_i = None
        best_score = -1.0
        for i, (cx, cy, ch, conf) in enumerate(labeled):
            if i in used:
                continue
            if not _char_match(ch, target):
                continue
            if conf > best_score:
                best_score = conf
                best_i = i
        if best_i is None:
            return []
        used.add(best_i)
        cx, cy, _, _ = labeled[best_i]
        points.append((cx, cy))
    return points


def solve_click_targets_for_chars(bg_png: bytes, targets: List[str]) -> List[Tuple[int, int]]:
    """已知答案序列，在背景图中按顺序定位点击坐标。"""
    chars = [t.strip() for t in targets if t and t.strip()]
    if not chars or not bg_png:
        return []
    emit_captcha_status(f"答案顺序：{' → '.join(chars)}，正在图中定位…")
    uat_logger.info("[CAPTCHA] click sequence targets=%s", chars)

    for matcher in (_match_click_targets_tesseract, _match_click_targets_blob_ocr):
        pts = matcher(bg_png, chars)
        if len(pts) == len(chars):
            uat_logger.info("[CAPTCHA] matched all targets via %s", matcher.__name__)
            return pts

    uat_logger.warning("[CAPTCHA] could not locate all targets in image: %s", chars)
    return []


def solve_click_targets(
    bg_png: bytes,
    instruction: str = "",
    *,
    targets: Optional[List[str]] = None,
) -> List[Tuple[int, int]]:
    """
    点选类验证码：先解析指令中的答案序列，再按顺序在图中定位 (x, y)。
    不会在未能匹配全部目标时返回轮廓/随机坐标。
    """
    chars = [t for t in (targets or _parse_instruction_targets(instruction)) if t]
    if not chars:
        return []
    return solve_click_targets_for_chars(bg_png, chars)


# ---------------------------------------------------------------------------
# 人类轨迹
# ---------------------------------------------------------------------------

def build_human_drag_path(
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    *,
    steps: Optional[int] = None,
    overshoot_px: Optional[float] = None,
) -> List[Tuple[float, float]]:
    """
    生成加速-匀速-减速 + 微抖动 + 轻微 overshoot 的拖动路径点。
    """
    raw_steps = (os.environ.get("CAPTCHA_DRAG_STEPS") or "").strip()
    n = steps if steps is not None else (int(raw_steps) if raw_steps.isdigit() else 24)
    n = max(8, min(n, 60))

    raw_os = (os.environ.get("CAPTCHA_OVERSHOOT_PX") or "").strip()
    overshoot = overshoot_px
    if overshoot is None:
        overshoot = float(raw_os) if raw_os.replace(".", "", 1).isdigit() else 3.0

    end_x = x1 + random.uniform(0, overshoot) * (1 if x1 >= x0 else -1)
    end_y = y1 + random.uniform(-1.5, 1.5)

    path: List[Tuple[float, float]] = []
    for i in range(1, n + 1):
        t = i / n
        # ease-in-out cubic
        if t < 0.5:
            ease = 4 * t * t * t
        else:
            ease = 1 - pow(-2 * t + 2, 3) / 2
        x = x0 + (end_x - x0) * ease + random.uniform(-1.2, 1.2)
        y = y0 + (end_y - y0) * ease + random.uniform(-0.8, 0.8)
        path.append((x, y))

    # 回拉修正 overshoot
    if overshoot > 0:
        for j in range(1, 4):
            t = j / 3
            x = end_x + (x1 - end_x) * t
            y = end_y + (y1 - end_y) * t
            path.append((x, y))

    path.append((x1, y1))
    return path


# ---------------------------------------------------------------------------
# VLM 动作解析
# ---------------------------------------------------------------------------

@dataclass
class CaptchaAction:
    type: str = "unknown"
    distance: Optional[int] = None
    angle: Optional[int] = None
    points: List[Tuple[int, int]] = field(default_factory=list)
    raw: Dict[str, Any] = field(default_factory=dict)


def parse_vision_action(json_text: str) -> Optional[CaptchaAction]:
    """解析 VLM 返回的 JSON 动作。"""
    if not json_text:
        return None
    text = json_text.strip()
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None

    action_type = str(data.get("type") or data.get("action") or "unknown").lower()
    dist = data.get("distance") or data.get("offset") or data.get("moveX")
    angle = data.get("angle") or data.get("rotation")
    pts: List[Tuple[int, int]] = []
    raw_pts = data.get("points") or data.get("clicks") or []
    if isinstance(raw_pts, list):
        for item in raw_pts:
            if isinstance(item, dict) and "x" in item and "y" in item:
                pts.append((int(item["x"]), int(item["y"])))
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                pts.append((int(item[0]), int(item[1])))

    return CaptchaAction(
        type=action_type,
        distance=int(dist) if dist is not None else None,
        angle=int(angle) if angle is not None else None,
        points=pts,
        raw=data,
    )


def solve_with_vision_fallback(
    bg_png: bytes,
    instruction: str = "",
    captcha_hint: str = "",
) -> Optional[CaptchaAction]:
    """Level 2：调用 ai_vision_local.captcha_vision_solve（带超时）。"""
    if not vision_fallback_enabled():
        return None
    try:
        from modules.ai.ai_vision_local import captcha_vision_solve, vision_enabled

        if not vision_enabled():
            return None
        emit_captcha_status("正在 AI 分析验证码（可能需要数秒至十几秒）…")
        raw = captcha_vision_solve(
            bg_png,
            instruction,
            captcha_hint,
            timeout_sec=captcha_vision_timeout(),
        )
        action = parse_vision_action(raw)
        if action and action.type not in ("unknown", ""):
            emit_captcha_status("AI 分析完成，正在执行验证操作…")
            return action
    except Exception as e:
        uat_logger.debug("vision fallback failed: %s", e)
        emit_captcha_status("AI 分析未成功，将尝试其他方式…")
    return None


def solve_captcha(
    bg_png: bytes,
    *,
    captcha_type: str = "unknown",
    instruction: str = "",
    slider_png: Optional[bytes] = None,
    html_hint: str = "",
    coord_multiplier: float = 1.0,
) -> CaptchaAction:
    """
    统一求解入口：先 L1，失败再 VLM。
    coord_multiplier: Desktop 缩放识别时需将 distance/坐标乘以此系数。
    """
    ctype = captcha_type if captcha_type != "unknown" else detect_captcha_type(instruction, html_hint)
    action = CaptchaAction(type=ctype)
    mult = coord_multiplier if coord_multiplier > 0 else 1.0

    emit_captcha_status(f"正在识别验证码类型（{ctype}）…")

    # 曲线类：低置信度时 VLM 优先
    if ctype == "curve":
        off, conf = solve_curve_offset_with_confidence(bg_png)
        if off is not None and conf >= captcha_curve_confidence_min():
            action.type = "curve"
            action.distance = int(off * mult)
            return action
        if vision_fallback_enabled():
            vis = solve_with_vision_fallback(
                bg_png, instruction, f"type_hint=curve; conf={conf:.2f}"
            )
            if vis and (vis.distance or vis.points):
                if vis.distance:
                    vis.distance = int(vis.distance * mult)
                if vis.points:
                    vis.points = [(int(x * mult), int(y * mult)) for x, y in vis.points]
                return vis

    if ctype in ("slider", "unknown"):
        dist = solve_slider_gap(bg_png, slider_png)
        if dist is not None:
            action.type = "slider"
            action.distance = int(dist * mult)
            return action

    if ctype in ("curve", "unknown"):
        off = solve_curve_offset(bg_png)
        if off is not None:
            action.type = "curve"
            action.distance = int(off * mult)
            return action

    if ctype in ("concat",):
        off = solve_concat_offset(bg_png)
        if off is not None:
            action.type = "concat"
            action.distance = int(off * mult)
            return action

    if ctype in ("rotate",):
        ang = solve_rotate_angle(bg_png)
        if ang is not None:
            action.type = "rotate"
            action.angle = ang
            return action

    if ctype in ("click_text", "click_icon", "image", "unknown"):
        emit_captcha_status("正在识别点选目标…")
        pts = solve_click_targets(bg_png, instruction)
        if pts:
            action.type = "click"
            action.points = [(int(x * mult), int(y * mult)) for x, y in pts]
            return action

    hint = f"type_hint={ctype}; platform={detect_platform(html_hint)}"
    vis = solve_with_vision_fallback(bg_png, instruction, hint)
    if vis:
        if vis.distance:
            vis.distance = int(vis.distance * mult)
        if vis.points:
            vis.points = [(int(x * mult), int(y * mult)) for x, y in vis.points]
        return vis

    return action
