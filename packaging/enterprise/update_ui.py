# -*- coding: utf-8 -*-
"""
图形化更新向导（tkinter）：差分包或完整安装包。

用法:
  python packaging/enterprise/update_ui.py
  UAT_UPDATE_MANIFEST_URL=https://... UAT_APP_VERSION=1.0.0 python packaging/enterprise/update_ui.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any, Dict, Optional

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from packaging.enterprise.update_core import (  # noqa: E402
    check_for_update,
    download_file,
    verify_sha256,
)
from packaging.enterprise.update_patch import apply_patch  # noqa: E402


def _cache_dir() -> Path:
    base = os.environ.get("PROGRAMDATA") or os.environ.get("TEMP") or "."
    p = Path(base) / "HuFirst" / "UAT" / "update_cache"
    p.mkdir(parents=True, exist_ok=True)
    return p


class UpdateDialog(tk.Tk):
    def __init__(self, info: Dict[str, Any]):
        super().__init__()
        self.info = info
        self.title("HuFirst UAT 更新")
        self.geometry("520x380")
        self.resizable(False, False)
        self._build()
        self._worker: Optional[threading.Thread] = None

    def _build(self) -> None:
        f = ttk.Frame(self, padding=16)
        f.pack(fill=tk.BOTH, expand=True)
        ttk.Label(
            f,
            text=f"发现新版本 {self.info['remote']}",
            font=("Segoe UI", 14, "bold"),
        ).pack(anchor=tk.W)
        ttk.Label(f, text=f"当前版本: {self.info['current']}").pack(anchor=tk.W, pady=(4, 8))
        notes = (self.info.get("release_notes") or "").strip()
        if notes:
            txt = tk.Text(f, height=6, wrap=tk.WORD, font=("Segoe UI", 10))
            txt.insert("1.0", notes)
            txt.configure(state=tk.DISABLED)
            txt.pack(fill=tk.BOTH, expand=True, pady=4)
        if self.info.get("release_notes_url"):
            ttk.Label(f, text=self.info["release_notes_url"], foreground="#2563eb").pack(anchor=tk.W)

        self.mode_var = tk.StringVar(value="delta" if self.info.get("can_delta") else "full")
        modes = ttk.LabelFrame(f, text="更新方式", padding=8)
        modes.pack(fill=tk.X, pady=8)
        if self.info.get("can_delta"):
            ttk.Radiobutton(
                modes,
                text=f"差分更新（推荐，基于 v{self.info['patch_base_version']} 缓存）",
                variable=self.mode_var,
                value="delta",
            ).pack(anchor=tk.W)
        ttk.Radiobutton(
            modes,
            text="完整安装包",
            variable=self.mode_var,
            value="full",
        ).pack(anchor=tk.W)

        self.progress = ttk.Progressbar(f, mode="determinate", maximum=100)
        self.progress.pack(fill=tk.X, pady=8)
        self.status = ttk.Label(f, text="")
        self.status.pack(anchor=tk.W)

        btns = ttk.Frame(f)
        btns.pack(fill=tk.X, pady=(8, 0))
        self.btn_update = ttk.Button(btns, text="下载并更新", command=self._start)
        self.btn_update.pack(side=tk.LEFT)
        if not self.info.get("mandatory"):
            ttk.Button(btns, text="稍后", command=self.destroy).pack(side=tk.RIGHT)

    def _set_progress(self, done: int, total: Optional[int]) -> None:
        if total and total > 0:
            self.progress["value"] = min(100, 100.0 * done / total)
        else:
            self.progress.configure(mode="indeterminate")
            self.progress.start(8)
        self.status.configure(text=f"已下载 {done // 1024} KB" + (f" / {total // 1024} KB" if total else ""))

    def _start(self) -> None:
        self.btn_update.configure(state=tk.DISABLED)
        self._worker = threading.Thread(target=self._run_update, daemon=True)
        self._worker.start()

    def _run_update(self) -> None:
        try:
            path = self._download_and_prepare()
            self.after(0, lambda: self._launch_installer(path))
        except Exception as e:
            self.after(0, lambda: messagebox.showerror("更新失败", str(e)))
            self.after(0, lambda: self.btn_update.configure(state=tk.NORMAL))

    def _download_and_prepare(self) -> Path:
        cache = _cache_dir()
        use_delta = self.mode_var.get() == "delta" and self.info.get("can_delta")

        def prog(done: int, total: Optional[int]) -> None:
            self.after(0, lambda: self._set_progress(done, total))

        if use_delta:
            base_name = self.info.get("patch_cache_basename") or "uat_platform_setup.exe"
            base_path = cache / base_name
            if not base_path.is_file():
                raise FileNotFoundError(
                    f"本地缺少差分基线文件 {base_path}，请选择「完整安装包」或先安装上一版本。"
                )
            patch_dest = cache / f"patch_{self.info['remote']}.bsdiff"
            self.after(0, lambda: self.status.configure(text="正在下载差分包…"))
            download_file(self.info["patch_url"], str(patch_dest), progress=prog)
            if not verify_sha256(str(patch_dest), self.info.get("patch_sha256") or ""):
                patch_dest.unlink(missing_ok=True)
                raise RuntimeError("差分包 SHA256 校验失败")
            out_installer = cache / f"uat_platform_setup_{self.info['remote']}.exe"
            self.after(0, lambda: self.status.configure(text="正在合并差分…"))
            apply_patch(base_path, patch_dest, out_installer)
            return out_installer

        url = self.info.get("package_url") or ""
        if not url:
            raise RuntimeError("清单缺少 package_url")
        fname = os.path.basename(url.split("?")[0]) or f"uat_{self.info['remote']}.exe"
        dest = cache / fname
        self.after(0, lambda: self.status.configure(text="正在下载完整安装包…"))
        download_file(url, str(dest), progress=prog)
        if not verify_sha256(str(dest), self.info.get("sha256") or ""):
            dest.unlink(missing_ok=True)
            raise RuntimeError("安装包 SHA256 校验失败")
        return dest

    def _launch_installer(self, path: Path) -> None:
        self.progress.stop()
        self.progress.configure(mode="determinate", value=100)
        if messagebox.askyesno("准备安装", f"将启动安装程序：\n{path}\n\n是否继续？"):
            subprocess.Popen([str(path)], shell=True)
        self.destroy()


def main() -> int:
    try:
        info = check_for_update()
    except RuntimeError as e:
        messagebox.showerror("更新检查", str(e))
        return 1
    if not info:
        messagebox.showinfo("更新", "当前已是最新版本。")
        return 0
    if info.get("mandatory"):
        dlg = UpdateDialog(info)
        dlg.mainloop()
        return 0
    dlg = UpdateDialog(info)
    dlg.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
