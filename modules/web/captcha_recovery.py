# -*- coding: utf-8 -*-
"""
验证码失败恢复：重试状态机、截图存档、友好报错（不强制关闭浏览器）。

策略（默认）：
  - 同一张验证码上求解 CAPTCHA_SOLVE_RETRY 次（默认 3），不刷新、不换题
  - 全部失败后若 CAPTCHA_AUTO_REFRESH=1，才刷新换题，最多 CAPTCHA_REFRESH_ROUNDS 轮
  - 仍失败 → 截图 + CaptchaManualRequiredError

禁止在全页范围点击 refresh/刷新，避免误触整页 reload。
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime
from typing import Awaitable, Callable, Optional

from modules.web.captcha_engine import (
    captcha_auto_refresh_enabled,
    captcha_container_selectors,
    captcha_refresh_rounds,
    captcha_solve_attempts,
    captcha_solve_retry_delay,
    captcha_total_solve_slots,
    emit_captcha_status,
    resolve_captcha_solve_attempts,
    set_captcha_solve_attempt_index,
    tianai_selectors,
)
from modules.core.logger import uat_logger

SolveOnceFn = Callable[[], Awaitable[bool]]

_CAPTCHA_ROOT_SELECTORS = (
    "#captcha-box",
    "#tianai-captcha",
    ".captcha-box",
    ".verification-box",
    ".verify-box",
) + tianai_selectors()

SCOPED_REFRESH_SELECTORS = (
    "#slider-refresh-btn",
    ".refresh-btn",
    '[id*="refresh-btn"]',
    '[class*="refresh-btn"]',
    '[class*="captcha"] .refresh-btn',
    '[class*="captcha"] [class*="refresh-btn"]',
)

SCOPED_CLOSE_SELECTORS = (
    "#slider-close-btn",
    ".close-btn",
    '[id*="close-btn"]',
    '[class*="close-btn"]',
    '[class*="captcha"] .close-btn',
)


class CaptchaManualRequiredError(Exception):
    """自动验证失败，需用户手动完成；不应触发浏览器强制关闭。"""

    def __init__(self, message: str, *, screenshot_path: Optional[str] = None):
        super().__init__(message)
        self.screenshot_path = screenshot_path
        self.user_message = message


def save_captcha_failure_screenshot(png_bytes: bytes, prefix: str = "captcha_fail") -> Optional[str]:
    if not png_bytes:
        return None
    try:
        logs_dir = os.path.join(os.getcwd(), "logs", "captcha_failures")
        os.makedirs(logs_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(logs_dir, f"{prefix}_{ts}.png")
        with open(path, "wb") as f:
            f.write(png_bytes)
        uat_logger.info("[CAPTCHA] failure screenshot saved: %s", path)
        return path
    except OSError as e:
        uat_logger.warning("[CAPTCHA] could not save failure screenshot: %s", e)
        return None


async def _find_captcha_root(page, captcha_root=None):
    """定位验证码根容器；若用户提供范围则仅用该范围。"""
    if captcha_root is not None:
        try:
            if await captcha_root.count() > 0 and await captcha_root.is_visible():
                return captcha_root
        except Exception:
            pass
        return None
    for sel in _CAPTCHA_ROOT_SELECTORS:
        try:
            loc = page.locator(sel).first
            if await loc.count() > 0 and await loc.is_visible():
                return loc
        except Exception:
            continue
    for sel in captcha_container_selectors():
        try:
            loc = page.locator(sel).first
            if await loc.count() > 0 and await loc.is_visible():
                box = await loc.bounding_box()
                if box and box.get("width", 0) >= 120 and box.get("height", 0) >= 80:
                    return loc
        except Exception:
            continue
    return None


async def _click_scoped(page, root, selectors: tuple) -> bool:
    targets = [root] if root is not None else []
    if not targets:
        return False
    for container in targets:
        for sel in selectors:
            try:
                loc = container.locator(sel).first
                if await loc.count() > 0 and await loc.is_visible():
                    await loc.click(timeout=2000)
                    uat_logger.info("[CAPTCHA] clicked scoped control: %s", sel)
                    await asyncio.sleep(0.35)
                    return True
            except Exception:
                continue
    return False


async def refresh_captcha_widget(page, captcha_root=None) -> bool:
    if not captcha_auto_refresh_enabled():
        uat_logger.info("[CAPTCHA] auto refresh disabled (CAPTCHA_AUTO_REFRESH=0)")
        return False
    emit_captcha_status("正在刷新验证码换题…")
    root = await _find_captcha_root(page, captcha_root)
    if root is None:
        uat_logger.warning("[CAPTCHA] 未在用户指定范围内找到刷新按钮，跳过刷新")
        return False
    return await _click_scoped(page, root, SCOPED_REFRESH_SELECTORS)


async def close_captcha_overlay(page) -> bool:
    """关闭弹层（默认重试流程不再调用，避免打断同题求解）。"""
    emit_captcha_status("正在关闭验证码弹层…")
    root = await _find_captcha_root(page)
    if root is None:
        return False
    return await _click_scoped(page, root, SCOPED_CLOSE_SELECTORS)


async def run_captcha_with_recovery(
    page,
    solve_once: SolveOnceFn,
    *,
    screenshot_fn: Optional[Callable[[], Awaitable[bytes]]] = None,
    captcha_root=None,
    max_retry: Optional[int] = None,
    solve_attempts: Optional[int] = None,
) -> bool:
    """
    max_retry 兼容旧参数：若传入则作为刷新轮数（需 AUTO_REFRESH=1）。
    solve_attempts: 步骤级最大验证次数；None 时使用环境变量 CAPTCHA_SOLVE_RETRY。
    captcha_root: 可选 async callable 返回用户指定的验证码容器 Locator。
    """
    per_image = resolve_captcha_solve_attempts(solve_attempts)
    refresh_rounds = captcha_refresh_rounds()
    if max_retry is not None and captcha_auto_refresh_enabled():
        refresh_rounds = max(0, min(int(max_retry), 2))

    async def _resolve_root():
        if captcha_root is None:
            return None
        try:
            root = captcha_root()
            if asyncio.iscoroutine(root):
                root = await root
            return root
        except Exception:
            return None

    total_slots = per_image * (1 + refresh_rounds)
    delay = captcha_solve_retry_delay()
    attempt_no = 0

    uat_logger.info(
        "[CAPTCHA] recovery: solve_per_image=%s refresh_rounds=%s total=%s auto_refresh=%s",
        per_image,
        refresh_rounds,
        total_slots,
        captcha_auto_refresh_enabled(),
    )

    for round_idx in range(1 + refresh_rounds):
        if round_idx > 0:
            emit_captcha_status("同题求解未通过，刷新换题后再试…")
            user_root = await _resolve_root()
            refreshed = await refresh_captcha_widget(page, captcha_root=user_root)
            if refreshed:
                await asyncio.sleep(0.45)
            else:
                uat_logger.info("[CAPTCHA] 刷新未执行，继续在当前题目上求解")

        for inner in range(per_image):
            attempt_no += 1
            set_captcha_solve_attempt_index(attempt_no)
            emit_captcha_status(f"正在自动验证（第 {attempt_no}/{total_slots} 次）…")
            try:
                if await solve_once():
                    emit_captcha_status("验证码已通过")
                    return True
            except Exception as e:
                uat_logger.warning("[CAPTCHA] attempt %s error: %s", attempt_no, e)

            if inner < per_image - 1:
                emit_captcha_status("本题重试…")
                await asyncio.sleep(delay)

    png = b""
    if screenshot_fn:
        try:
            png = await screenshot_fn()
        except Exception:
            pass
    if not png:
        try:
            png = await page.screenshot(full_page=False)
        except Exception:
            pass

    shot_path = save_captcha_failure_screenshot(png)
    msg = (
        "自动验证失败，请手动完成验证码后继续。"
        "浏览器会话已保留，您可在页面中手动拖动/点选后重新运行或继续后续步骤。"
    )
    if shot_path:
        msg += f" 失败截图已保存：{shot_path}"
    raise CaptchaManualRequiredError(msg, screenshot_path=shot_path)
