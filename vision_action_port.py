"""
跨端视觉动作抽象（Phase 4a）：capture → ground → act。

Web / Mobile / Desktop 对 Hermes、CLI、MCP 暴露统一语义，底层仍走各 Gateway。
"""
from __future__ import annotations

import base64
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, Tuple


@dataclass
class CaptureFrame:
    png_bytes: bytes
    width: int
    height: int
    meta: Optional[Dict[str, Any]] = None


@dataclass
class GroundPoint:
    x: int
    y: int
    prompt: str = ""


@dataclass
class ActResult:
    ok: bool
    message: str = ""
    raw: Optional[Dict[str, Any]] = None


class VisionActionPort(ABC):
    """三端统一：截图、定位、点击、输入、断言、批量步骤。"""

    platform: str = "web"

    @abstractmethod
    def capture(self) -> CaptureFrame:
        raise NotImplementedError

    @abstractmethod
    def ground(self, description: str, frame: Optional[CaptureFrame] = None) -> Optional[GroundPoint]:
        raise NotImplementedError

    @abstractmethod
    def tap(self, description: str) -> ActResult:
        raise NotImplementedError

    @abstractmethod
    def input_text(self, description: str, text: str) -> ActResult:
        raise NotImplementedError

    def assert_vision(self, condition_nl: str) -> ActResult:
        from ai_vision_insight import assert_vision_condition_on_png

        frame = self.capture()
        ok, reason = assert_vision_condition_on_png(frame.png_bytes, condition_nl)
        return ActResult(ok=ok, message=reason)

    def query(self, prompt_nl: str) -> Tuple[str, str]:
        from ai_vision_insight import extract_vision_from_png

        frame = self.capture()
        return extract_vision_from_png(frame.png_bytes, prompt_nl)

    def run_steps(self, steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        raise NotImplementedError


class WebVisionActionPort(VisionActionPort):
    platform = "web"

    def __init__(self, session_id: str, *, user_id: int = 0):
        self.session_id = (session_id or "").strip()
        self.user_id = user_id

    def capture(self) -> CaptureFrame:
        from testory_cli._gateway import gateway_screenshot_png

        png, err = gateway_screenshot_png(self.session_id, user_id=self.user_id)
        if err or not png:
            raise RuntimeError(err or "截图失败")
        return CaptureFrame(png_bytes=png, width=1280, height=720)

    def ground(self, description: str, frame: Optional[CaptureFrame] = None) -> Optional[GroundPoint]:
        from ai_vision_grounding import ground_element_from_png

        fr = frame or self.capture()
        hit = ground_element_from_png(
            fr.png_bytes,
            description,
            viewport_w=fr.width,
            viewport_h=fr.height,
        )
        if not hit:
            return None
        return GroundPoint(x=hit.cx, y=hit.cy, prompt=description)

    def tap(self, description: str) -> ActResult:
        from ai_step_normalization import normalize_ai_step
        from testory_cli._gateway import gateway_run_steps

        step = normalize_ai_step(
            {"action": "ai_tap", "description": description, "locate_prompt": description}
        )
        j, err = gateway_run_steps(self.session_id, [step], user_id=self.user_id)
        if err:
            return ActResult(ok=False, message=err)
        rs = (j or {}).get("results") or [{}]
        if rs and rs[0].get("ok"):
            return ActResult(ok=True, message="已尝试点击（请结合画面确认）")
        return ActResult(ok=False, message=str(rs[0].get("error") if rs else "失败"))

    def input_text(self, description: str, text: str) -> ActResult:
        from ai_step_normalization import normalize_ai_step
        from testory_cli._gateway import gateway_run_steps

        step = normalize_ai_step(
            {
                "action": "ai_input",
                "description": description,
                "locate_prompt": description,
                "input_value": text,
            }
        )
        j, err = gateway_run_steps(self.session_id, [step], user_id=self.user_id)
        if err:
            return ActResult(ok=False, message=err)
        rs = (j or {}).get("results") or [{}]
        if rs and rs[0].get("ok"):
            return ActResult(ok=True, message="已尝试输入（请结合画面确认）")
        return ActResult(ok=False, message=str(rs[0].get("error") if rs else "失败"))

    def run_steps(self, steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        from testory_cli._gateway import gateway_run_steps

        j, err = gateway_run_steps(self.session_id, steps, user_id=self.user_id)
        if err:
            return [{"ok": False, "error": err}]
        return list((j or {}).get("results") or [])


class MobileVisionActionPort(VisionActionPort):
    platform = "android"

    def __init__(self, udid: str = ""):
        self.udid = (udid or "").strip()

    def capture(self) -> CaptureFrame:
        from mobile_agent_client import agent_screenshot

        j = agent_screenshot(self.udid, use_plugin=True)
        b64 = (j or {}).get("image_b64") or (j or {}).get("data") or ""
        if isinstance(b64, dict):
            b64 = b64.get("data") or ""
        if not b64:
            raise RuntimeError((j or {}).get("error") or "移动端截图失败")
        png = base64.b64decode(b64)
        w = int((j or {}).get("width") or 1080)
        h = int((j or {}).get("height") or 1920)
        return CaptureFrame(png_bytes=png, width=w, height=h)

    def ground(self, description: str, frame: Optional[CaptureFrame] = None) -> Optional[GroundPoint]:
        from mobile_vision_tap import ground_mobile_element

        fr = frame or self.capture()
        pt = ground_mobile_element(fr.png_bytes, description, viewport_w=fr.width, viewport_h=fr.height)
        if not pt:
            return None
        return GroundPoint(x=pt[0], y=pt[1], prompt=description)

    def tap(self, description: str) -> ActResult:
        from mobile_vision_tap import tap_mobile_by_description

        ok, msg = tap_mobile_by_description(self.udid, description)
        return ActResult(ok=ok, message=msg)

    def input_text(self, description: str, text: str) -> ActResult:
        tap_r = self.tap(description)
        if not tap_r.ok:
            return tap_r
        from mobile_agent_client import agent_replay_step

        step = {"action": "input_text", "input_value": text}
        j = agent_replay_step(self.udid, step, step_index=0)
        if (j or {}).get("success") is False:
            return ActResult(ok=False, message=str((j or {}).get("error") or "输入失败"))
        return ActResult(ok=True, message="已尝试输入（请结合画面确认）")

    def run_steps(self, steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        from mobile_agent_client import agent_replay_steps

        return list((agent_replay_steps(self.udid, steps) or {}).get("results") or [])


class DesktopVisionActionPort(VisionActionPort):
    """桌面端：UIA 优先，视觉坐标作 Tier4 兜底。"""

    platform = "desktop"

    def capture(self) -> CaptureFrame:
        from desktop_visual_engine import capture_virtual_desktop_png

        png = capture_virtual_desktop_png()
        if not png:
            raise RuntimeError("桌面截图失败")
        return CaptureFrame(png_bytes=png, width=1920, height=1080)

    def ground(self, description: str, frame: Optional[CaptureFrame] = None) -> Optional[GroundPoint]:
        from ai_vision_grounding import ground_element_from_png

        fr = frame or self.capture()
        hit = ground_element_from_png(
            fr.png_bytes, description, viewport_w=fr.width, viewport_h=fr.height
        )
        if not hit:
            return None
        return GroundPoint(x=hit.cx, y=hit.cy, prompt=description)

    def tap(self, description: str) -> ActResult:
        pt = self.ground(description)
        if not pt:
            return ActResult(ok=False, message=f"未找到：{description[:80]}")
        try:
            import pyautogui  # type: ignore

            pyautogui.click(pt.x, pt.y)
            return ActResult(ok=True, message="已尝试点击（请结合画面确认）")
        except Exception as e:
            return ActResult(ok=False, message=str(e))

    def input_text(self, description: str, text: str) -> ActResult:
        tap_r = self.tap(description)
        if not tap_r.ok:
            return tap_r
        try:
            import pyautogui  # type: ignore

            pyautogui.typewrite(text, interval=0.02)
            return ActResult(ok=True, message="已尝试输入（请结合画面确认）")
        except Exception as e:
            return ActResult(ok=False, message=str(e))

    def run_steps(self, steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        from desktop_automation import sync_desktop_execute_step

        out = []
        for st in steps:
            try:
                row = sync_desktop_execute_step(st)
                out.append({"ok": True, "result": row})
            except Exception as e:
                out.append({"ok": False, "error": str(e)})
                break
        return out


def create_vision_port(platform: str, **kwargs) -> VisionActionPort:
    p = (platform or "web").strip().lower()
    if p in ("android", "mobile"):
        return MobileVisionActionPort(kwargs.get("udid") or "")
    if p == "desktop":
        return DesktopVisionActionPort()
    return WebVisionActionPort(kwargs.get("session_id") or "", user_id=int(kwargs.get("user_id") or 0))
