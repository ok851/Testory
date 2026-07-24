# -*- coding: utf-8 -*-
"""Fake ERP 桌面客户端（企业样例，无需真实 ERP 安装）。

窗口标题即为订单号，便于 UIA attach + store_as 抽取后与 API 变量断言一致。

用法:
  python demos/erp_desktop_sample/fake_erp_client.py --order-id ORD-DEMO-404
"""

from __future__ import annotations

import argparse
import sys
import tkinter as tk


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Testory Fake ERP desktop sample")
    p.add_argument("--order-id", default="ORD-DEMO-404", help="显示在窗口标题中的订单号")
    p.add_argument("--hold-sec", type=float, default=120.0, help="窗口保持秒数（验收后可关）")
    args = p.parse_args(argv)
    order_id = (args.order_id or "ORD-DEMO-404").strip()

    root = tk.Tk()
    # 标题 = 订单号：跨端 attach/store_as 可直接当 erp_order_id
    root.title(order_id)
    root.geometry("420x180")
    root.attributes("-topmost", True)
    frm = tk.Frame(root, padx=16, pady=16)
    frm.pack(fill=tk.BOTH, expand=True)
    tk.Label(frm, text="Testory Fake ERP", font=("Segoe UI", 14, "bold")).pack(anchor="w")
    tk.Label(frm, text="订单号（窗口标题同步）", font=("Segoe UI", 10)).pack(anchor="w", pady=(12, 0))
    tk.Label(frm, text=order_id, font=("Consolas", 16), fg="#0f766e").pack(anchor="w")
    tk.Label(
        frm,
        text="本窗口仅用于平台 Desktop 主路径样例，非真实 ERP。",
        font=("Segoe UI", 9),
        fg="#64748b",
    ).pack(anchor="w", pady=(16, 0))

    hold_ms = max(5, int(float(args.hold_sec) * 1000))
    root.after(hold_ms, root.destroy)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
