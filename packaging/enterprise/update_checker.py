# -*- coding: utf-8 -*-
"""
企业自动更新检查（CLI）。图形界面见 update_ui.py。

环境变量:
  UAT_UPDATE_MANIFEST_URL  HTTPS 清单地址
  UAT_APP_VERSION          当前版本，默认 1.0.0
  UAT_UPDATE_SKIP          1 跳过检查
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from packaging.enterprise.update_core import check_for_update, download_file, verify_sha256


def prompt_and_download(info, download_dir: str) -> Optional[str]:
    print(f"\n发现新版本 {info['remote']}（当前 {info['current']}）")
    if info.get("release_notes"):
        print(info["release_notes"])
    if info.get("release_notes_url"):
        print(f"说明: {info['release_notes_url']}")
    if info.get("can_delta"):
        print(f"可用差分更新（基线 v{info['patch_base_version']}），图形界面: python packaging/enterprise/update_ui.py")
    if info.get("mandatory"):
        print("此更新为强制更新。")
    ans = input("是否下载完整安装包？[y/N]: ").strip().lower()
    if ans not in ("y", "yes"):
        return None
    url = info.get("package_url") or ""
    if not url:
        print("清单缺少 package_url", file=sys.stderr)
        return None
    os.makedirs(download_dir, exist_ok=True)
    fname = os.path.basename(url.split("?")[0]) or f"uat_update_{info['remote']}.exe"
    dest = os.path.join(download_dir, fname)
    print(f"下载: {url}")

    def prog(done, total):
        if total:
            print(f"\r  {100 * done // total}%", end="", flush=True)

    download_file(url, dest, progress=prog)
    print()
    if not verify_sha256(dest, info.get("sha256") or ""):
        os.remove(dest)
        raise RuntimeError("安装包 SHA256 校验失败")
    print(f"已保存: {dest}")
    return dest


def main() -> int:
    try:
        info = check_for_update()
    except RuntimeError as e:
        print(f"[update] {e}", file=sys.stderr)
        return 1
    if not info:
        print("已是最新版本或未配置 UAT_UPDATE_MANIFEST_URL。")
        return 0
    dl = os.path.join(os.environ.get("TEMP", "."), "uat_updates")
    try:
        prompt_and_download(info, dl)
    except Exception as e:
        print(f"[update] 失败: {e}", file=sys.stderr)
        return 1 if info.get("mandatory") else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
