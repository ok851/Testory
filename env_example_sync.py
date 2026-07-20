"""
将 .env.example 中尚未出现在 .env 的 KEY=value 追加到 .env（不覆盖、不删除已有项）。

解析规则：整行去掉首尾空白后，若以 # 开头则去掉第一个 # 及其后空白，再按 KEY=VALUE 识别；
KEY 须匹配 [A-Za-z_][A-Za-z0-9_]*；支持可选前缀 export 。

本机功能开关：从 example 同步时，下列 KEY 若示例值为 0/off/空，会写入「开启」默认值（见 _LOCAL_ON_VALUES）。

在 load_dotenv 之前生效的跳过开关（须由系统/IDE 注入，不能写在 .env 里）：
  SKIP_ENV_EXAMPLE_SYNC=1  — 不写 .env、不合并。
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_OFF_VALUES = frozenset({"0", "false", "no", "off", ""})

# 从 .env.example 同步到 .env 时，若示例为关闭态则提升为本机推荐「开」值（不覆盖 .env 已有行）
_LOCAL_ON_VALUES: dict[str, str] = {
    "AI_CHAT_TOOLS_ENABLE": "1",
    "AI_CHAT_TOOLS_OLLAMA_ENABLE": "1",
    "AI_LOCATOR_RESOLVE_ENABLE": "1",
    "AI_FAILURE_DIAG_ENABLE": "1",
    "AI_STEP_FAILURE_DIAG": "1",
    "AI_STEP_FAILURE_DIAG_LLM": "1",
    "AI_DIAG_CDP_ENABLE": "1",
    "AI_SELECTOR_FALLBACK": "1",
    "LOCAL_AI_HEURISTIC_SELECTOR_REPAIR": "1",
    "LOCAL_AI_SELECTOR_CLAMP": "1",
    "LOCAL_VISION_ENABLE": "1",
    "LOCAL_VISION_VERIFY": "1",
    "LOCAL_VISION_RECOVERY": "1",
    "LOCAL_OCR_ENABLE": "1",
    "LOCAL_MEMORY_ENABLE": "1",
    "ENABLE_MOBILE": "1",
    "MOBILE_EMULATOR_MODE": "1",
    "MOBILE_AUTO_CONNECT": "1",
    "CAPTCHA_VISION_FALLBACK": "1",
    "CAPTCHA_REQUIRE_USER_SCOPE": "1",
    "EMBEDDED_SNAP_AFTER_STEP": "1",
    "EMBEDDED_BROWSER_AUTO_START_GATEWAY": "1",
    "HERMES_AUTO_START_GATEWAY": "0",
    "HERMES_GATEWAY_URL": "http://127.0.0.1:8642",
    "AI_AGENT_BACKEND": "hermes",
    "AI_HERMES_HEAL_ENABLE": "1",
    "LOCATOR_TIER_VISUAL_ENABLE": "1",
    "LOCATOR_TIER_COORD_ENABLE": "1",
    "LOCATOR_TIER_VLM_ENABLE": "1",
    "LOCATOR_VLM_CACHE_ENABLE": "1",
    "AI_VISION_INSIGHT_ENABLE": "1",
    "AI_WAIT_VISION_ENABLE": "1",
    "VISION_STEP_REPORT_ENABLE": "1",
    "LOCATOR_VLM_SHRINK_FACTOR": "1",
    "LOCATOR_VLM_MODEL": "",
    "DEPLOYMENT_PROFILE": "local",
    "DESKTOP_EXECUTION_MODE": "inprocess",
    "PLAYWRIGHT_HEADLESS": "0",
}


def _coerce_local_on_value(key: str, val: str) -> str:
    if key not in _LOCAL_ON_VALUES:
        return val.rstrip()
    if val.strip().lower() in _OFF_VALUES:
        return _LOCAL_ON_VALUES[key]
    return val.rstrip()


def _parse_assignment_line(line: str) -> tuple[str, str] | None:
    s = line.strip()
    if not s:
        return None
    if s.startswith("#"):
        inner = s[1:].lstrip()
        if not inner or inner.startswith("#"):
            return None
        s = inner
    if s.startswith("export "):
        s = s[7:].lstrip()
    if "=" not in s:
        return None
    key, _, val = s.partition("=")
    key = key.strip()
    if not _KEY.match(key):
        return None
    return key, _coerce_local_on_value(key, val.rstrip())


def _active_keys_in_env(text: str) -> set[str]:
    keys: set[str] = set()
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("export "):
            s = s[7:].lstrip()
        if "=" not in s:
            continue
        key = s.split("=", 1)[0].strip()
        if _KEY.match(key):
            keys.add(key)
    return keys


def _pairs_from_example(text: str) -> list[tuple[str, str]]:
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for line in text.splitlines():
        p = _parse_assignment_line(line)
        if not p:
            continue
        k, v = p
        if k in seen:
            continue
        seen.add(k)
        out.append((k, v))
    return out


def sync_env_from_example(root: Path | None = None, *, ignore_skip: bool = False) -> dict:
    """
    若存在 .env.example，把其中定义、但 .env 尚未声明的变量追加到 .env。
    若 .env 不存在则创建（仅含同步块）。
    返回 {"ok": bool, "added": list[str], "reason": str?}
    """
    if (
        not ignore_skip
        and os.environ.get("SKIP_ENV_EXAMPLE_SYNC", "").strip().lower() in ("1", "true", "yes", "on")
    ):
        return {"ok": True, "added": [], "skipped": "SKIP_ENV_EXAMPLE_SYNC"}
    root = root or Path(__file__).resolve().parent
    example_path = root / ".env.example"
    env_path = root / ".env"
    if not example_path.is_file():
        return {"ok": False, "added": [], "reason": "missing .env.example"}
    ex_text = example_path.read_text(encoding="utf-8-sig")
    pairs = _pairs_from_example(ex_text)
    env_text = env_path.read_text(encoding="utf-8-sig") if env_path.is_file() else ""
    have = _active_keys_in_env(env_text)
    to_add = [(k, v) for k, v in pairs if k not in have]
    if not to_add:
        return {"ok": True, "added": []}
    sep = "\n\n# --- synced from .env.example (missing keys only; edit freely) ---\n"
    block = sep + "".join(f"{k}={v}\n" for k, v in to_add)
    if env_text and not env_text.endswith("\n"):
        env_text += "\n"
    env_path.write_text(env_text + block, encoding="utf-8", newline="\n")
    return {"ok": True, "added": [k for k, _ in to_add]}


def main(argv: list[str]) -> int:
    root = Path(__file__).resolve().parent
    if len(argv) > 1:
        root = Path(argv[1]).resolve()
    r = sync_env_from_example(root, ignore_skip=True)
    if not r["ok"]:
        print(f"skip: {r.get('reason', 'unknown')}", file=sys.stderr)
        return 1
    added = r.get("added") or []
    if not added:
        print(".env already has all keys found in .env.example")
        return 0
    print(f"appended {len(added)} key(s) to .env:")
    for k in added:
        print(f"  + {k}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
