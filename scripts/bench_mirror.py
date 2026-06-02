#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""对比 screencap 与 scrcpy_ws 投屏性能（需已连接 emulator-*）。"""

from __future__ import annotations

import argparse
import statistics
import time


def bench_screencap(serial: str, n: int = 30) -> dict:
    from mobile_device_manager import capture_screenshot_frame

    latencies = []
    for _ in range(n):
        t0 = time.perf_counter()
        frame, _ = capture_screenshot_frame(serial)
        latencies.append((time.perf_counter() - t0) * 1000)
        if not frame:
            break
    if not latencies:
        return {"backend": "screencap", "error": "no frames"}
    avg = statistics.mean(latencies)
    return {
        "backend": "screencap",
        "samples": len(latencies),
        "avg_ms": round(avg, 1),
        "p95_ms": round(sorted(latencies)[int(len(latencies) * 0.95) - 1], 1),
        "avg_fps": round(1000 / avg, 1) if avg > 0 else 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Mirror backend benchmark")
    parser.add_argument("--serial", default="emulator-5554")
    parser.add_argument("--frames", type=int, default=30)
    args = parser.parse_args()

    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

    print("Benchmark serial:", args.serial)
    print(bench_screencap(args.serial, args.frames))
    from mobile_scrcpy_bridge import bridge_health

    print("Bridge:", bridge_health())
    print("Note: scrcpy_ws FPS is measured in browser toolbar after connect.")


if __name__ == "__main__":
    main()
