# -*- coding: utf-8 -*-
"""自动更新共享逻辑（清单、下载、校验）。"""
from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, Optional, Tuple


def parse_version(v: str) -> Tuple[int, ...]:
    parts = re.findall(r"\d+", v or "0")
    return tuple(int(p) for p in parts) or (0,)


def version_newer(remote: str, local: str) -> bool:
    return parse_version(remote) > parse_version(local)


def version_equals(a: str, b: str) -> bool:
    return parse_version(a) == parse_version(b)


def verify_sha256(path: str, expected_hex: str) -> bool:
    if not expected_hex:
        return True
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest().lower() == expected_hex.strip().lower()


def fetch_manifest(url: str, timeout: float = 15.0) -> Dict[str, Any]:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def check_for_update(
    *,
    manifest_url: Optional[str] = None,
    current_version: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    if (os.environ.get("UAT_UPDATE_SKIP") or "").strip() in ("1", "true", "yes"):
        return None
    url = (manifest_url or os.environ.get("UAT_UPDATE_MANIFEST_URL") or "").strip()
    if not url:
        return None
    local = (current_version or os.environ.get("UAT_APP_VERSION") or "1.0.0").strip()
    try:
        data = fetch_manifest(url)
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError) as e:
        raise RuntimeError(f"无法获取更新清单: {e}") from e
    remote_ver = (data.get("version") or "").strip()
    if not remote_ver or not version_newer(remote_ver, local):
        return None
    base = (data.get("base_version") or data.get("patch_base_version") or "").strip()
    patch_url = (data.get("patch_url") or "").strip()
    patch_sha = (data.get("patch_sha256") or "").strip()
    can_delta = bool(
        patch_url
        and base
        and version_equals(base, local)
    )
    return {
        "current": local,
        "remote": remote_ver,
        "mandatory": bool(data.get("mandatory")),
        "package_url": (data.get("package_url") or "").strip(),
        "sha256": (data.get("sha256") or "").strip(),
        "release_notes": data.get("release_notes") or "",
        "release_notes_url": data.get("release_notes_url") or "",
        "can_delta": can_delta,
        "patch_url": patch_url,
        "patch_sha256": patch_sha,
        "patch_base_version": base,
        "patch_cache_basename": (data.get("patch_cache_basename") or "uat_platform_setup.exe").strip(),
    }


def download_file(
    url: str,
    dest: str,
    *,
    progress: Optional[Callable[[int, Optional[int]], None]] = None,
) -> str:
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=120) as resp:
        total = int(resp.headers.get("Content-Length") or 0) or None
        done = 0
        chunk = 1024 * 256
        with open(dest, "wb") as out:
            while True:
                buf = resp.read(chunk)
                if not buf:
                    break
                out.write(buf)
                done += len(buf)
                if progress:
                    progress(done, total)
    return dest
