# -*- coding: utf-8 -*-
"""getevent touch capture - referenced SoloPi TouchEventTracker"""

from __future__ import annotations
import re, subprocess, threading, time
from typing import Any, Dict, List, Optional, Tuple
from modules.mobile.mobile_adb_control import adb_path, adb_get_screen_size

EV_ABS = 0x03
EV_KEY = 0x01
ABS_MT_TRACKING_ID = 0x39
ABS_MT_POSITION_X = 0x35
ABS_MT_POSITION_Y = 0x36
BTN_TOUCH = 0x14a

_RE_LINE = re.compile(r"^\s*\[\s*(\d+)\.(\d+)\]\s+\S+:\s+([0-9a-fA-F]{4})\s+([0-9a-fA-F]{4})\s+([0-9a-fA-F]+)")
_RE_BTN = re.compile(r"^\s*\[\s*(\d+)\.(\d+)\]\s+\S+:\s+([0-9a-fA-F]{4})\s+([0-9a-fA-F]{4})\s+(DOWN|UP)")

def _pts(s, u): return float(s) + float(u) / 1_000_000

class TouchEvent:
    __slots__ = ("down_ts","up_ts","x_start","y_start","x_end","y_end")
    def __init__(self):
        self.down_ts = self.up_ts = 0.0
        self.x_start = self.y_start = self.x_end = self.y_end = 0
    @property
    def duration_s(self): return max(0.0, self.up_ts - self.down_ts)
    @property
    def is_tap(self):
        return abs(self.x_end-self.x_start) <= 30 and abs(self.y_end-self.y_start) <= 30 and self.duration_s < 1.0
    def to_step(self, sw, sh):
        if self.is_tap:
            return {"action": "tap", "mobile_spec": {"x": self.x_end, "y": self.y_end, "screen_width": sw, "screen_height": sh, "source": "getevent"}}
        return {"action": "swipe", "mobile_spec": {"x1": self.x_start, "y1": self.y_start, "x2": self.x_end, "y2": self.y_end, "screen_width": sw, "screen_height": sh, "source": "getevent"}}
    def __repr__(self):
        return f"TouchEvent(down={self.down_ts:.3f}, up={self.up_ts:.3f}, start=({self.x_start},{self.y_start}), end=({self.x_end},{self.y_end}), tap={self.is_tap})"

class GetEventCapture:
    def __init__(self, udid):
        self._udid = (udid or "").strip()
        self._proc = None
        self._thread = None
        self._running = threading.Event()
        self._events = []
        self._lock = threading.Lock()
        self._sw, self._sh = 1080, 1920
        self._cur = None
        self._px = self._py = 0
        self._have_xy = False

    def _cmd(self):
        c = [adb_path()]
        if self._udid: c.extend(["-s", self._udid])
        c.extend(["shell", "getevent", "-lt"])
        return c

    def start(self):
        if self._running.is_set(): return
        try: self._sw, self._sh = adb_get_screen_size(self._udid)
        except: pass
        self._running.set()
        self._thread = threading.Thread(target=self._loop, daemon=True, name=f"getevent-{self._udid}")
        self._thread.start()

    def stop(self):
        self._running.clear()
        if self._proc:
            try: self._proc.terminate()
            except: pass
            self._proc = None

    def drain_events(self):
        with self._lock:
            ev = list(self._events)
            self._events.clear()
        return ev

    def _loop(self):
        try:
            self._proc = subprocess.Popen(self._cmd(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=1, text=False)
        except Exception as e:
            import logging; logging.error("getevent start failed: %s", e)
            self._running.clear(); return
        buf = b""
        while self._running.is_set() and self._proc.poll() is None:
            try:
                chunk = self._proc.stdout.read(4096)
                if not chunk: break
                buf += chunk
                while b"\n" in buf:
                    lb, buf = buf.split(b"\n", 1)
                    try: self._parse(lb.decode("utf-8", errors="replace"))
                    except: pass
            except: break
        self._running.clear()

    def _parse(self, line):
        line = line.strip()
        if not line: return
        m = _RE_LINE.match(line)
        if m:
            ts = _pts(m.group(1), m.group(2))
            self._handle(ts, int(m.group(3),16), int(m.group(4),16), int(m.group(5),16))
            return
        m = _RE_BTN.match(line)
        if m and int(m.group(3),16) == EV_KEY and int(m.group(4),16) == BTN_TOUCH:
            ts = _pts(m.group(1), m.group(2))
            if m.group(5) == "DOWN": self._down(ts)
            else: self._up(ts)

    def _handle(self, ts, tp, code, val):
        if tp == EV_ABS:
            if code == ABS_MT_TRACKING_ID:
                if val >= 0xFFFFFFFF or val < 0: self._up(ts)
                else: self._down(ts)
            elif code == ABS_MT_POSITION_X: self._px = val; self._have_xy = True
            elif code == ABS_MT_POSITION_Y: self._py = val; self._have_xy = True
        elif tp == EV_KEY and code == BTN_TOUCH:
            if val == 0: self._up(ts)
            elif val == 1: self._down(ts)

    def _down(self, ts):
        self._cur = TouchEvent(); self._cur.down_ts = ts; self._have_xy = False

    def _up(self, ts):
        if self._cur is None: return
        e = self._cur; e.up_ts = ts
        if self._have_xy:
            e.x_end, e.y_end = self._px, self._py
            if e.x_start == 0 and e.y_start == 0: e.x_start, e.y_start = self._px, self._py
        with self._lock: self._events.append(e)
        self._cur = None; self._have_xy = False

    def on_move(self, x, y):
        if self._cur and self._have_xy:
            if self._cur.x_start == 0 and self._cur.y_start == 0: self._cur.x_start, self._cur.y_start = x, y
            self._cur.x_end, self._cur.y_end = x, y
        self._have_xy = False
