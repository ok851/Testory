# -*- coding: utf-8 -*-
"""ADB getevent 触摸录制器（Airtest 风格）。

录制由 PC 端通过 ADB 直接读取设备触摸事件，不再依赖设备端 APK 的 Cover 拦截。
设计要点：
  1. 自动识别触摸屏设备（getevent -p）。
  2. 兼容 getevent -t / -lt 输出格式（十六进制 / 事件名）。
  3. 基于 ABS_MT_TRACKING_ID 的按指针状态机，过滤多指噪声。
  4. 坐标从触摸设备分辨率映射到屏幕分辨率。
"""
from __future__ import annotations

import logging
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from modules.mobile.mobile_device_manager import adb_path

_logger = logging.getLogger(__name__)


def _adb_shell_popen(udid: str, *args: str) -> subprocess.Popen:
    cmd = [adb_path()]
    if udid:
        cmd.extend(["-s", udid])
    cmd.extend(["shell", *args])
    return subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        bufsize=1,
    )


def _adb_shell(udid: str, *args: str, timeout: int = 10) -> Tuple[int, str, str]:
    cmd = [adb_path()]
    if udid:
        cmd.extend(["-s", udid])
    cmd.extend(["shell", *args])
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip()
    except Exception as exc:
        return 1, "", str(exc)


@dataclass
class _PointerState:
    tracking_id: int = -1
    start_x: int = 0
    start_y: int = 0
    end_x: int = 0
    end_y: int = 0
    start_ms: float = 0.0
    end_ms: float = 0.0
    has_x: bool = False
    has_y: bool = False
    start_set: bool = False


@dataclass
class Gesture:
    type: str  # "tap", "long_press", "swipe"
    x: int = 0
    y: int = 0
    x1: int = 0
    y1: int = 0
    x2: int = 0
    y2: int = 0
    duration_ms: int = 0
    ts: float = 0.0
    raw: Dict = field(default_factory=dict)


class AdbTouchRecorder:
    """通过 ADB getevent 录制设备触摸事件并识别为手势。"""

    # getevent -t 输出（指定单个设备时无路径前缀）：
    # [    3.234567] 0003 0035 00000123
    # getevent -t 输出（读取全部设备时含路径前缀）：
    # [    3.234567] /dev/input/event1: 0003 0035 00000123
    _HEX_RE = re.compile(
        r"^\s*\[\s*(\d+)\.(\d+)\]\s+(?:\S+:\s+)?([0-9a-fA-F]{4})\s+([0-9a-fA-F]{4})\s+([0-9a-fA-F]+)"
    )

    # getevent -lt 输出（含事件名）：
    # [    3.234567] /dev/input/event1: EV_ABS       ABS_MT_POSITION_X    00000123
    _LABEL_RE = re.compile(
        r"^\s*\[\s*(\d+)\.(\d+)\]\s+(?:\S+:\s+)?(\S+)\s+(\S+)\s+([0-9a-fA-F]+)"
    )

    # getevent -p 输出中的 ABS 行：0035  : value 0, min 0, max 2279, ...
    _ABS_RE = re.compile(r"^(\d{4})\s*:\s*value\s*\d+,\s*min\s*\d+,\s*max\s*(\d+)")

    # getevent -p 输出中的设备路径与名称
    _DEVICE_PATH_RE = re.compile(r"^add\s+device\s+\d+:\s+(\S+)")
    _DEVICE_NAME_RE = re.compile(r"^\s+name:\s+\"([^\"]+)\"")

    _EV_ABS = 0x03
    _EV_KEY = 0x01
    _ABS_MT_POSITION_X = 0x35
    _ABS_MT_POSITION_Y = 0x36
    _ABS_MT_TRACKING_ID = 0x39
    _BTN_TOUCH = 0x14A

    _MIN_SWIPE_PX = 40
    _LONG_PRESS_MS = 500
    _MIN_TAP_MS = 20

    _NAME_PREFERENCES = ("touchscreen", "touch", "fts", "synaptics", "atmel", "goodix", "himax")

    def __init__(self, udid: str, screen_width: int = 1080, screen_height: int = 1920):
        self.udid = (udid or "").strip()
        self.screen_width = max(1, screen_width)
        self.screen_height = max(1, screen_height)
        self.touch_max_x = 0
        self.touch_max_y = 0
        self.touch_device_path: Optional[str] = None
        self._proc: Optional[subprocess.Popen] = None
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._gestures: List[Gesture] = []
        self._pointers: Dict[int, _PointerState] = {}
        self._active_tracking_id: int = -1
        self._running = False
        self._error: Optional[str] = None
        self._device_resolved = False
        self._last_event_ms: float = 0.0

    def start(self) -> bool:
        """启动 ADB getevent 录制。"""
        if self._running:
            return True
        self._running = True
        self._error = None
        self._gestures = []
        self._pointers.clear()
        self._active_tracking_id = -1
        self._last_event_ms = time.time()

        if not self._device_resolved:
            self._resolve_touch_device()

        # 如果未解析到触摸屏，尝试默认 event2/event1 兜底
        device_arg = self.touch_device_path or ""
        if device_arg:
            _logger.info("ADB touch recorder using device %s (max_x=%d, max_y=%d)",
                         device_arg, self.touch_max_x, self.touch_max_y)
            self._proc = _adb_shell_popen(self.udid, "getevent", "-t", device_arg)
        else:
            _logger.warning("Could not resolve touch device; falling back to getevent -t (all devices)")
            self._proc = _adb_shell_popen(self.udid, "getevent", "-t")

        self._thread = threading.Thread(target=self._reader_loop, daemon=True, name=f"adb-touch-rec-{self.udid}")
        self._thread.start()
        return True

    def stop(self) -> List[Gesture]:
        """停止录制并返回剩余手势。"""
        self._running = False
        proc = self._proc
        if proc is not None:
            try:
                proc.terminate()
                proc.wait(timeout=1.0)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
            self._proc = None
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        self._thread = None
        return self.drain()

    def drain(self) -> List[Gesture]:
        """返回已录制的所有手势并清空缓冲。"""
        with self._lock:
            out = list(self._gestures)
            self._gestures = []
            return out

    def is_running(self) -> bool:
        return self._running

    def error(self) -> Optional[str]:
        return self._error

    def last_event_ms(self) -> float:
        return self._last_event_ms

    def _resolve_touch_device(self) -> None:
        """解析触摸屏设备路径与分辨率。"""
        code, out, err = _adb_shell(self.udid, "getevent", "-p", timeout=10)
        if code != 0 or not out:
            self._error = f"getevent -p failed: {err or out}"
            return

        devices: List[Tuple[str, str, int, int]] = []
        current_path = ""
        current_name = ""
        max_x = 0
        max_y = 0
        in_abs = False

        for raw_line in out.splitlines():
            line = raw_line.strip()

            path_m = self._DEVICE_PATH_RE.match(raw_line)
            if path_m:
                # 保存上一个设备
                if current_path and (max_x > 0 or max_y > 0):
                    devices.append((current_path, current_name, max_x, max_y))
                current_path = path_m.group(1)
                current_name = ""
                max_x = 0
                max_y = 0
                in_abs = False
                continue

            name_m = self._DEVICE_NAME_RE.match(raw_line)
            if name_m:
                current_name = name_m.group(1).lower()
                continue

            if line.startswith("ABS"):
                in_abs = True
                continue
            if in_abs and not line:
                in_abs = False
                continue

            if in_abs:
                m = self._ABS_RE.match(line)
                if m:
                    code_hex = m.group(1)
                    max_val = int(m.group(2))
                    if code_hex == "0035":
                        max_x = max_val
                    elif code_hex == "0036":
                        max_y = max_val

        # 保存最后一个设备
        if current_path and (max_x > 0 or max_y > 0):
            devices.append((current_path, current_name, max_x, max_y))

        if not devices:
            self._error = "No touch device found in getevent -p"
            self._device_resolved = True
            return

        # 优先选择名称含 touch 关键词的设备；否则选第一个同时有 x/y 的设备
        chosen: Optional[Tuple[str, str, int, int]] = None
        for path, name, mx, my in devices:
            if mx > 0 and my > 0 and any(k in name for k in self._NAME_PREFERENCES):
                chosen = (path, name, mx, my)
                break
        if chosen is None:
            for path, name, mx, my in devices:
                if mx > 0 and my > 0:
                    chosen = (path, name, mx, my)
                    break

        if chosen is None:
            chosen = devices[0]

        self.touch_device_path = chosen[0]
        self.touch_max_x = chosen[2]
        self.touch_max_y = chosen[3]
        self._device_resolved = True

    def _reader_loop(self) -> None:
        try:
            proc = self._proc
            if proc is None or proc.stdout is None:
                return
            for line in proc.stdout:
                if not self._running:
                    break
                line = line.strip()
                if not line:
                    continue
                self._last_event_ms = time.time()
                try:
                    self._parse_line(line)
                except Exception:
                    # 单行解析失败不中断录制
                    pass
        except Exception as exc:
            self._error = str(exc)
            _logger.warning("ADB touch recorder reader loop failed: %s", exc)
        finally:
            self._running = False

    def _parse_line(self, line: str) -> None:
        # 优先尝试十六进制格式
        m = self._HEX_RE.match(line)
        if m:
            ev_type = int(m.group(3), 16)
            ev_code = int(m.group(4), 16)
            ev_value = int(m.group(5), 16)
        else:
            # 回退到事件名格式
            m = self._LABEL_RE.match(line)
            if not m:
                return
            ev_type = self._ev_name_to_code(m.group(3))
            ev_code = self._abs_name_to_code(m.group(4))
            ev_value = int(m.group(5), 16)

        ts = int(m.group(1)) * 1000.0 + int(m.group(2)) / 1000.0

        if ev_type == self._EV_ABS:
            if ev_code == self._ABS_MT_POSITION_X:
                ptr = self._current_pointer()
                ptr.end_x = ev_value
                ptr.has_x = True
                if not ptr.start_set:
                    ptr.start_x = ev_value
                    ptr.start_set = True
            elif ev_code == self._ABS_MT_POSITION_Y:
                ptr = self._current_pointer()
                ptr.end_y = ev_value
                ptr.has_y = True
                if not ptr.start_set:
                    ptr.start_y = ev_value
                    ptr.start_set = True
            elif ev_code == self._ABS_MT_TRACKING_ID:
                if ev_value < 0 or ev_value >= 0xFFFFFFFF:
                    self._on_up(ts)
                else:
                    self._on_down(ts, ev_value)
        elif ev_type == self._EV_KEY and ev_code == self._BTN_TOUCH:
            if ev_value == 1:
                self._on_down(ts, 0)
            else:
                self._on_up(ts)

    def _current_pointer(self) -> _PointerState:
        """获取当前活跃指针；若无则创建一个占位。"""
        tid = self._active_tracking_id
        if tid < 0:
            tid = 0
            self._active_tracking_id = tid
        ptr = self._pointers.get(tid)
        if ptr is None:
            ptr = _PointerState(tracking_id=tid)
            self._pointers[tid] = ptr
        return ptr

    def _on_down(self, ts: float, tracking_id: int) -> None:
        # 若已有活跃指针，忽略新的（简化：只录单指）
        if self._active_tracking_id >= 0 and self._active_tracking_id != tracking_id:
            return
        self._active_tracking_id = tracking_id
        ptr = self._pointers.get(tracking_id)
        if ptr is None:
            ptr = _PointerState(tracking_id=tracking_id)
            self._pointers[tracking_id] = ptr
        ptr.start_x = ptr.end_x
        ptr.start_y = ptr.end_y
        ptr.start_ms = ts
        ptr.has_x = False
        ptr.has_y = False
        ptr.start_set = False

    def _on_up(self, ts: float) -> None:
        tid = self._active_tracking_id
        if tid < 0:
            return
        ptr = self._pointers.pop(tid, None)
        self._active_tracking_id = -1
        if ptr is None:
            return
        if not (ptr.has_x or ptr.has_y):
            return

        ptr.end_ms = ts
        x1 = self._scale_x(ptr.start_x)
        y1 = self._scale_y(ptr.start_y)
        x2 = self._scale_x(ptr.end_x)
        y2 = self._scale_y(ptr.end_y)
        duration = max(0, int(ptr.end_ms - ptr.start_ms))
        dx = abs(x2 - x1)
        dy = abs(y2 - y1)

        gesture: Optional[Gesture] = None
        if dx < self._MIN_SWIPE_PX and dy < self._MIN_SWIPE_PX:
            if duration < self._MIN_TAP_MS:
                return
            if duration >= self._LONG_PRESS_MS:
                gesture = Gesture(
                    type="long_press",
                    x=x2,
                    y=y2,
                    duration_ms=duration,
                    ts=time.time(),
                )
            else:
                gesture = Gesture(
                    type="tap",
                    x=x2,
                    y=y2,
                    duration_ms=duration,
                    ts=time.time(),
                )
        else:
            gesture = Gesture(
                type="swipe",
                x1=x1,
                y1=y1,
                x2=x2,
                y2=y2,
                duration_ms=duration,
                ts=time.time(),
            )

        if gesture is not None:
            with self._lock:
                self._gestures.append(gesture)

    def _scale_x(self, touch_x: int) -> int:
        if self.touch_max_x > 0:
            return int(round(touch_x / self.touch_max_x * self.screen_width))
        return touch_x

    def _scale_y(self, touch_y: int) -> int:
        if self.touch_max_y > 0:
            return int(round(touch_y / self.touch_max_y * self.screen_height))
        return touch_y

    @staticmethod
    def _ev_name_to_code(name: str) -> int:
        mapping = {
            "EV_SYN": 0x00,
            "EV_KEY": 0x01,
            "EV_REL": 0x02,
            "EV_ABS": 0x03,
            "EV_MSC": 0x04,
        }
        return mapping.get(name.strip(), 0)

    @staticmethod
    def _abs_name_to_code(name: str) -> int:
        mapping = {
            "ABS_MT_TOUCH_MAJOR": 0x30,
            "ABS_MT_TOUCH_MINOR": 0x31,
            "ABS_MT_WIDTH_MAJOR": 0x32,
            "ABS_MT_WIDTH_MINOR": 0x33,
            "ABS_MT_ORIENTATION": 0x34,
            "ABS_MT_POSITION_X": 0x35,
            "ABS_MT_POSITION_Y": 0x36,
            "ABS_MT_TOOL_TYPE": 0x37,
            "ABS_MT_BLOB_ID": 0x38,
            "ABS_MT_TRACKING_ID": 0x39,
            "ABS_MT_PRESSURE": 0x3A,
            "ABS_MT_DISTANCE": 0x3B,
            "ABS_MT_TOOL_X": 0x3C,
            "ABS_MT_TOOL_Y": 0x3D,
        }
        return mapping.get(name.strip(), 0)
