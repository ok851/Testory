# -*- coding: utf-8 -*-
"""Hermes 内嵌配置：安装目录 .env 模板与 LLM provider 同步。"""
from __future__ import annotations

import json
import os
import secrets
from pathlib import Path
from typing import Any, Dict, Optional

from modules.core.logger import uat_logger

_ACTIVE_CDP_ENDPOINT: str = ""


def hermes_home_dir() -> Path:
    raw = (os.environ.get("HERMES_HOME") or "").strip()
    if raw:
        # 展开未解析的 %UAT_DATA_DIR% 占位（部分 .env 会原样写入）
        uat = (os.environ.get("UAT_DATA_DIR") or "").strip()
        if "%UAT_DATA_DIR%" in raw or "%uat_data_dir%" in raw.lower():
            if uat:
                raw = raw.replace("%UAT_DATA_DIR%", uat).replace("%uat_data_dir%", uat)
            else:
                # 仓库内字面目录名「%UAT_DATA_DIR%/hermes」是历史残留毒目录：
                # 写入会触发 WinError 5（拒绝访问），且并非真实数据目录。
                # 不再回退到它，统一落到 LOCALAPPDATA/Testory/hermes（与日志路径逻辑一致）。
                return Path(os.environ.get("LOCALAPPDATA", "")) / "Testory" / "hermes"
        return Path(os.path.expandvars(raw))
    uat = (os.environ.get("UAT_DATA_DIR") or "").strip()
    if uat:
        return Path(uat) / "hermes"
    return Path(os.environ.get("LOCALAPPDATA", "")) / "Testory" / "hermes"


def hermes_skills_dir() -> Path:
    custom = (os.environ.get("HERMES_SKILLS_DIR") or "").strip()
    if custom:
        return Path(custom)
    return hermes_home_dir() / "skills"


def hermes_skill_versions_dir() -> Path:
    """技能版本历史存储目录。"""
    return hermes_home_dir() / "skill_versions"


def hermes_selector_store_path() -> Path:
    """跨用例选择器知识库 JSON 路径。"""
    p = hermes_home_dir() / "selector_store.json"
    return p


def hermes_skill_max_versions() -> int:
    """每个 Skill 保留的最大历史版本数（0 表示不限制）。"""
    try:
        return max(0, int(os.environ.get("HERMES_SKILL_MAX_VERSIONS", "10") or "10"))
    except ValueError:
        return 10


def _read_active_llm_profile() -> Dict[str, Any]:
    try:
        from modules.ai.ai_config_paths import ai_model_registry_path

        path = ai_model_registry_path()
        if not path.is_file():
            return {}
        raw = json.loads(path.read_text(encoding="utf-8"))
        aid = (raw.get("active_profile_id") or "").strip()
        for p in raw.get("profiles") or []:
            if isinstance(p, dict) and p.get("id") == aid:
                return p
        if raw.get("profiles") and isinstance(raw["profiles"][0], dict):
            return raw["profiles"][0]
    except Exception:
        pass
    return {}


def build_hermes_env_lines(*, api_key: Optional[str] = None) -> str:
    """生成 Hermes ~/.env 内容（写入 HERMES_HOME/.env）。"""
    key = (api_key or os.environ.get("HERMES_API_SERVER_KEY") or "").strip()
    if not key:
        key = secrets.token_urlsafe(24)
    prof = _read_active_llm_profile()
    base_url = (prof.get("base_url") or os.environ.get("LOCAL_LLM_BASE_URL") or "http://127.0.0.1:11434/v1").strip()
    model_id = (prof.get("model_id") or os.environ.get("LOCAL_LLM_MODEL_MID") or "llama3:8b-instruct").strip()
    api_key_llm = (prof.get("api_key") or "").strip()
    lines = [
        "API_SERVER_ENABLED=true",
        f"API_SERVER_KEY={key}",
        "API_SERVER_HOST=127.0.0.1",
        "API_SERVER_PORT=8642",
        # 不含 skills/terminal：避免 Hermes 默认提示驱动 skill_view / bash 死循环；
        # 网页走 browser_*（CDP attach），桌面走 MCP windows_*。
        'toolsets=["hermes-cli","browser","web","memory"]',
        "HERMES_BROWSER_MODE=cdp_attach",
    ]
    cdp = (os.environ.get("HERMES_CDP_ENDPOINT") or "").strip()
    if cdp:
        lines.append(f"HERMES_CDP_ENDPOINT={cdp}")
        lines.append(f"BROWSER_CDP_URL={cdp}")
    # 独立 LLM 配置优先：如果设置了 HERMES_LLM_PROVIDER，使用独立配置而非平台配置
    hermes_provider = (os.environ.get("HERMES_LLM_PROVIDER") or "").strip()
    hermes_model = (os.environ.get("HERMES_LLM_MODEL") or "").strip()
    if hermes_provider:
        hermes_base = (os.environ.get("HERMES_LLM_BASE_URL") or "").strip()
        hermes_key = (os.environ.get("HERMES_LLM_API_KEY") or "").strip()
        lines.append(f"PROVIDER={hermes_provider}")
        if hermes_model:
            lines.append(f"OPENAI_MODEL={hermes_model}")
        if hermes_base:
            lines.append(f"OPENAI_API_BASE={hermes_base.rstrip('/')}")
        if hermes_key:
            lines.append(f"OPENAI_API_KEY={hermes_key}")
    elif prof.get("api_style") == "ollama" or prof.get("provider") == "ollama":
        lines.extend(
            [
                "PROVIDER=openai_compatible",
                f"OPENAI_API_BASE={base_url.rstrip('/')}",
                f"OPENAI_MODEL={model_id}",
                "OPENAI_API_KEY=ollama",
            ]
        )
    elif base_url:
        lines.extend(
            [
                "PROVIDER=openai_compatible",
                f"OPENAI_API_BASE={base_url.rstrip('/')}",
                f"OPENAI_MODEL={model_id}",
            ]
        )
        if api_key_llm:
            lines.append(f"OPENAI_API_KEY={api_key_llm}")
    # 将独立 LLM 配置变量写入 .env，供 Hermes 读取
    if hermes_provider:
        lines.append(f"HERMES_LLM_PROVIDER={hermes_provider}")
    if hermes_model:
        lines.append(f"HERMES_LLM_MODEL={hermes_model}")
    # 无 LLM key 时 Hermes 会报 Missing Authentication header，并导致「只能启动应用」的假象
    has_llm_key = any(ln.startswith("OPENAI_API_KEY=") and not ln.endswith("=ollama") for ln in lines)
    if hermes_provider and hermes_provider != "ollama" and not has_llm_key and not (os.environ.get("HERMES_LLM_API_KEY") or "").strip():
        # 回退到平台当前推理配置
        if api_key_llm:
            lines.append(f"OPENAI_API_KEY={api_key_llm}")
        elif (os.environ.get("OPENAI_API_KEY") or "").strip():
            lines.append(f"OPENAI_API_KEY={os.environ.get('OPENAI_API_KEY').strip()}")
    return "\n".join(lines) + "\n"


def resolve_hermes_api_server_key(*, persist_if_empty: bool = False) -> str:
    """解析与 Hermes Gateway 一致的 API_SERVER_KEY。

    优先级：HERMES_HOME/.env 的 API_SERVER_KEY → 进程 HERMES_API_SERVER_KEY / API_SERVER_KEY。
    注意：不要因为 key 含「replace-with」就擅自换成另一个值——官方 Hermes 会把该字符串当真实密钥使用；
    平台曾因此用 testory-local-key 探测，导致已就绪的 Gateway 一直被判定为未启动直至超时。
    """
    home_key = ""
    env_path = hermes_home_dir() / ".env"
    if env_path.is_file():
        try:
            from dotenv import dotenv_values

            vals = dotenv_values(env_path)
            home_key = (vals.get("API_SERVER_KEY") or vals.get("HERMES_API_SERVER_KEY") or "").strip()
        except Exception:
            try:
                for line in env_path.read_text(encoding="utf-8").splitlines():
                    if line.startswith("API_SERVER_KEY="):
                        home_key = line.split("=", 1)[1].strip().strip('"').strip("'")
                        break
            except OSError:
                pass
    proc_key = (
        (os.environ.get("HERMES_API_SERVER_KEY") or "").strip()
        or (os.environ.get("API_SERVER_KEY") or "").strip()
    )
    key = home_key or proc_key
    if not key:
        key = "testory-local-key"
        if persist_if_empty and env_path.parent.is_dir():
            try:
                ensure_hermes_home()
                lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.is_file() else []
                lines = _upsert_env_line(list(lines), "API_SERVER_KEY", key)
                _write_hermes_env_lines(lines)
            except Exception:
                pass
    # 同步到进程，供 HermesGatewayClient 使用
    os.environ["HERMES_API_SERVER_KEY"] = key
    os.environ["API_SERVER_KEY"] = key
    return key


def hermes_upstream_llm_status() -> Dict[str, Any]:
    """检查当前平台「前端所选」推理配置能否同步给 Hermes。

    原则：智能体必须与页面当前引擎一致，禁止静默回退到注册表里另一个供应商
   （曾导致 UI 显示 MiMo、Hermes 仍打 DeepSeek）。
    """
    prof = _read_active_llm_profile()
    from modules.ai.ai_multi_provider import normalize_api_key, _uses_xiaomimimo_auth

    base = (prof.get("base_url") or "").strip()
    key = normalize_api_key(prof.get("api_key"))
    prov = (prof.get("provider") or "").strip()
    style = (prof.get("api_style") or "").strip()
    out: Dict[str, Any] = {
        "ok": True,
        "provider": prov,
        "model_id": (prof.get("model_id") or "").strip(),
        "label": (prof.get("label") or prof.get("model_id") or "").strip(),
        "reason": "",
        "hermes_profile": prof,
        "active_profile_id": (prof.get("id") or "").strip(),
    }
    if style == "ollama" or prov == "ollama":
        out["ok"] = True
        out["reason"] = "ollama"
        return out
    if _uses_xiaomimimo_auth(base, prov, key):
        # MiMo Token Plan 同时接受 api-key 与 Authorization: Bearer；Hermes 用 Bearer 即可
        out["ok"] = True
        out["reason"] = "openai_compatible_xiaomi"
        out["active_is_xiaomi"] = True
        if not key or not base:
            out["ok"] = False
            out["reason"] = "xiaomi_missing_credentials"
            out["message"] = "当前引擎为小米 MiMo，但缺少 API Key 或地址，请到模型配置补全。"
        return out
    if style in ("anthropic_messages",) or prov == "anthropic":
        out["ok"] = False
        out["reason"] = "anthropic_not_openai_compat"
        out["message"] = (
            "当前推理引擎为 Anthropic Messages API，Hermes 默认按 OpenAI 兼容调用。"
            "请改选 OpenAI 兼容模型后再启动智能体。"
        )
        return out
    if not key:
        out["ok"] = False
        out["reason"] = "missing_api_key"
        out["message"] = "未配置推理引擎 API Key，智能体无法调用上游模型。"
        return out
    if not base:
        out["ok"] = False
        out["reason"] = "missing_base_url"
        out["message"] = "未配置推理引擎地址。"
        return out
    out["reason"] = "openai_compatible"
    return out


def _find_bearer_compatible_profile() -> Optional[Dict[str, Any]]:
    """在模型注册表中找一个 Hermes 可用的 Bearer 配置（跳过小米 MiMo / Anthropic）。"""
    try:
        from modules.ai.ai_config_paths import ai_model_registry_path
        from modules.ai.ai_multi_provider import normalize_api_key, _uses_xiaomimimo_auth

        path = ai_model_registry_path()
        if not path.is_file():
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
        for p in raw.get("profiles") or []:
            if not isinstance(p, dict):
                continue
            prov = (p.get("provider") or "").strip()
            style = (p.get("api_style") or "").strip()
            base = (p.get("base_url") or "").strip()
            key = normalize_api_key(p.get("api_key"))
            if style == "ollama" or prov == "ollama":
                return p
            if style == "anthropic_messages" or prov == "anthropic":
                continue
            if _uses_xiaomimimo_auth(base, prov, key):
                continue
            if key and base:
                return p
    except Exception:
        pass
    return None


def hermes_config_yaml_path() -> Path:
    return hermes_home_dir() / "config.yaml"


def _load_hermes_config_yaml() -> Dict[str, Any]:
    path = hermes_config_yaml_path()
    if not path.is_file():
        return {}
    try:
        import yaml

        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _dump_hermes_config_yaml(data: Dict[str, Any]) -> None:
    import yaml

    path = hermes_config_yaml_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump(
        data,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
    )
    path.write_text(text, encoding="utf-8")


def _normalize_openai_compatible_base_url(url: str) -> str:
    """OpenAI 兼容端点补全 /v1（DeepSeek/MiMo 等）；已带版本路径则不动。"""
    u = (url or "").strip().rstrip("/")
    if not u:
        return u
    lower = u.lower()
    if lower.endswith("/v1") or "/v1/" in lower or lower.endswith("/v1beta") or "/openai/v1" in lower:
        return u
    return f"{u}/v1"


def _vendor_api_key_env_name(base_url: str) -> str:
    """从 host 推导 DEEPSEEK_API_KEY 这类变量名（对齐 Hermes _host_derived_api_key）。"""
    try:
        from urllib.parse import urlparse

        host = (urlparse(base_url if "://" in base_url else f"https://{base_url}").hostname or "").lower()
    except Exception:
        host = ""
    if not host or host in ("localhost", "127.0.0.1"):
        return ""
    # api.deepseek.com → deepseek；api.groq.com → groq
    parts = [p for p in host.split(".") if p and p not in ("www", "api", "openai", "open")]
    if not parts:
        return ""
    vendor = parts[0].replace("-", "").upper()
    if vendor in ("OPENAI", "OPENROUTER", "OLLAMA"):
        return ""
    return f"{vendor}_API_KEY"


def sync_platform_llm_to_hermes_config_yaml(
    *,
    base_url: str,
    model_id: str,
    api_key: str,
    api_style: str = "",
) -> Dict[str, Any]:
    """把平台所选模型写入 HERMES_HOME/config.yaml 的 model 段。

    新版 Hermes 以 config.yaml 为端点/模型唯一真相源，不再读 OPENAI_API_BASE。
    provider=custom + base_url + api_key，避免回落到默认 OpenRouter。
    """
    out: Dict[str, Any] = {"changed": False, "path": str(hermes_config_yaml_path())}
    base = (base_url or "").strip().rstrip("/")
    model = (model_id or "").strip()
    key = (api_key or "").strip()
    style = (api_style or "").strip().lower()
    if not base or not model:
        out["skipped"] = True
        out["reason"] = "missing_base_or_model"
        return out

    if style in ("", "openai", "openai_compatible", "openai-compatible", "ollama"):
        base = _normalize_openai_compatible_base_url(base)

    cfg = _load_hermes_config_yaml()
    before_model = cfg.get("model")
    before_snap = json.dumps(before_model, ensure_ascii=False, sort_keys=True) if before_model is not None else ""

    if style in ("anthropic", "claude"):
        model_cfg: Dict[str, Any] = {
            "provider": "anthropic",
            "default": model,
            "base_url": base,
        }
    else:
        # custom：非 OpenRouter 的 OpenAI 兼容网关（DeepSeek / 通义 / 本地等）
        model_cfg = {
            "provider": "custom",
            "default": model,
            "base_url": base,
            "api_mode": "chat_completions",
        }
    if key and key.lower() != "ollama":
        model_cfg["api_key"] = key

    cfg["model"] = model_cfg
    after_snap = json.dumps(model_cfg, ensure_ascii=False, sort_keys=True)
    if before_snap != after_snap:
        _dump_hermes_config_yaml(cfg)
        out["changed"] = True
    out["model"] = {"provider": model_cfg.get("provider"), "default": model, "base_url": base}
    return out


def sync_platform_llm_credentials_to_hermes_env() -> Dict[str, Any]:
    """把平台「当前前端所选」推理配置同步到 Hermes：.env + config.yaml model。

    始终以 active profile 为准，禁止改写成注册表里其它供应商。
    """
    home = hermes_home_dir()
    home.mkdir(parents=True, exist_ok=True)
    env_path = home / ".env"
    status = hermes_upstream_llm_status()
    from modules.ai.ai_multi_provider import normalize_api_key

    # 强制用前端当前引擎；不再使用 fallback_bearer_profile 替换供应商
    prof = _read_active_llm_profile()
    if not prof and status.get("hermes_profile"):
        prof = status["hermes_profile"]

    base = (prof.get("base_url") or os.environ.get("LOCAL_LLM_BASE_URL") or "").strip()
    model_id = (prof.get("model_id") or "").strip()
    key = normalize_api_key(prof.get("api_key"))
    prov = (prof.get("provider") or "").strip()
    style = (prof.get("api_style") or "").strip()
    before_snap = hermes_env_llm_snapshot()

    lines: list[str] = []
    if env_path.is_file():
        try:
            lines = env_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = []
    if not lines:
        lines = build_hermes_env_lines().splitlines()

    effective_base = base.rstrip("/")
    can_sync = bool(status.get("ok")) and bool(model_id) and (
        style == "ollama" or prov == "ollama" or (bool(key) and bool(base))
    )
    if style == "ollama" or prov == "ollama":
        effective_base = _normalize_openai_compatible_base_url(effective_base) if effective_base else ""
        lines = _upsert_env_line(lines, "PROVIDER", "openai_compatible")
        lines = _upsert_env_line(lines, "HERMES_INFERENCE_PROVIDER", "custom")
        if effective_base:
            lines = _upsert_env_line(lines, "OPENAI_API_BASE", effective_base)
            lines = _upsert_env_line(lines, "CUSTOM_BASE_URL", effective_base)
        if model_id:
            lines = _upsert_env_line(lines, "OPENAI_MODEL", model_id)
        lines = _upsert_env_line(lines, "OPENAI_API_KEY", "ollama")
    elif can_sync and key and base:
        if style.lower() in ("anthropic", "claude"):
            lines = _upsert_env_line(lines, "PROVIDER", "anthropic")
            lines = _upsert_env_line(lines, "HERMES_INFERENCE_PROVIDER", "anthropic")
            lines = _upsert_env_line(lines, "ANTHROPIC_BASE_URL", base.rstrip("/"))
            lines = _upsert_env_line(lines, "ANTHROPIC_API_KEY", key)
            if model_id:
                lines = _upsert_env_line(lines, "OPENAI_MODEL", model_id)
        else:
            effective_base = _normalize_openai_compatible_base_url(base)
            lines = _upsert_env_line(lines, "PROVIDER", "openai_compatible")
            lines = _upsert_env_line(lines, "HERMES_INFERENCE_PROVIDER", "custom")
            lines = _upsert_env_line(lines, "OPENAI_API_BASE", effective_base)
            lines = _upsert_env_line(lines, "CUSTOM_BASE_URL", effective_base)
            if model_id:
                lines = _upsert_env_line(lines, "OPENAI_MODEL", model_id)
            lines = _upsert_env_line(lines, "OPENAI_API_KEY", key)
            vendor_key = _vendor_api_key_env_name(effective_base)
            if vendor_key:
                lines = _upsert_env_line(lines, vendor_key, key)

    _write_hermes_env_lines(lines)
    _sync_hermes_env_to_process(env_path)

    yaml_info: Dict[str, Any] = {"skipped": True, "reason": "no_credentials"}
    if (style == "ollama" or prov == "ollama") and effective_base and model_id:
        yaml_info = sync_platform_llm_to_hermes_config_yaml(
            base_url=effective_base,
            model_id=model_id,
            api_key="ollama",
            api_style="ollama",
        )
    elif can_sync and key and base and model_id:
        yaml_info = sync_platform_llm_to_hermes_config_yaml(
            base_url=effective_base or base,
            model_id=model_id,
            api_key=key,
            api_style=style or "openai_compatible",
        )

    after_snap = hermes_env_llm_snapshot()
    model_switched = (
        (before_snap.get("model") or "") != (after_snap.get("model") or "")
        or (before_snap.get("base_url") or "") != (after_snap.get("base_url") or "")
    )
    return {
        "synced": bool(can_sync),
        "status": status,
        "env_path": str(env_path),
        "config_yaml": yaml_info,
        "config_changed": bool(yaml_info.get("changed")) or model_switched,
        "active_profile_id": (prof.get("id") or "").strip(),
        "synced_model": model_id,
        "synced_base_url": effective_base or base,
        "hermes_snapshot": after_snap,
    }

def _testory_soul_md() -> str:
    """覆盖 Hermes SOUL：DOM 优先，禁止 skill_view / 重复 navigate / 空白标签。"""
    return (
        "You are Testory's browser/desktop automation executor (via Hermes).\n"
        "CRITICAL OVERRIDES (higher priority than any default Hermes guidance):\n"
        "1. Do NOT call skill_view, skill_list, skill_manage, or terminal/bash/curl.\n"
        "2. Web tasks: browser is already CDP-attached and usually already on the target URL. "
        "Do NOT call browser_navigate again (that reinventing-the-wheel opens blank tabs). "
        "Prefer the DOM/interactive-controls list in the user message; "
        "browser_snapshot is an accessibility/DOM ref tree (NOT a screenshot) — use at most once when DOM list is insufficient; "
        "vision/screenshot is last-resort only.\n"
        "3. Never open blank tabs.\n"
        "4. Same tool twice with no progress → stop and say NEED_USER_ACTION.\n"
        "5. Desktop short tasks prefer MCP windows_* / get_screen_* when available.\n"
    )


def ensure_hermes_web_safe_toolsets() -> bool:
    """把已有 HERMES_HOME/.env 的 toolsets 中 skills/terminal/脏空项去掉。"""
    env_path = hermes_home_dir() / ".env"
    if not env_path.is_file():
        return False
    try:
        text = env_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    safe = '["hermes-cli","browser","web","memory"]'
    changed = False
    out_lines: list[str] = []
    for line in text.splitlines():
        if line.strip().startswith("toolsets="):
            val = line.split("=", 1)[1]
            if any(tok in val for tok in ("skills", "terminal", '""', ",,")):
                out_lines.append(f"toolsets={safe}")
                changed = True
                continue
        out_lines.append(line)
    if changed:
        try:
            _write_hermes_env_lines(out_lines)
        except Exception:
            return False
    return changed


# Hermes API Server 真正读的是 config.yaml platform_toolsets.api_server，
# 默认 hermes-api-server 复合集含 skill_view/terminal —— .env toolsets= 管不了。
_API_SERVER_SAFE_TOOLSETS = ["browser", "web", "memory"]


def ensure_hermes_api_server_toolsets() -> Dict[str, Any]:
    """写入 platform_toolsets.api_server，从工具列表中拿掉 skills/terminal。

    必须重启 Hermes Gateway 后生效。
    """
    out: Dict[str, Any] = {"ok": False, "changed": False, "path": ""}
    path = hermes_home_dir() / "config.yaml"
    out["path"] = str(path)
    try:
        import yaml
    except ImportError:
        out["error"] = "PyYAML missing"
        return out

    cfg: Dict[str, Any] = {}
    if path.is_file():
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if isinstance(raw, dict):
                cfg = raw
        except Exception as e:
            out["error"] = str(e)[:160]
            return out

    pt = cfg.get("platform_toolsets")
    if not isinstance(pt, dict):
        pt = {}
    desired = list(_API_SERVER_SAFE_TOOLSETS)
    current = pt.get("api_server")
    if isinstance(current, list) and [str(x) for x in current] == desired:
        out["ok"] = True
        out["toolsets"] = desired
        return out

    pt["api_server"] = desired
    cfg["platform_toolsets"] = pt
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            yaml.safe_dump(cfg, allow_unicode=True, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )
    except OSError as e:
        out["error"] = str(e)[:160]
        return out
    out["ok"] = True
    out["changed"] = True
    out["toolsets"] = desired
    return out


def ensure_hermes_home(*, force_env: bool = False) -> Path:
    """创建 HERMES_HOME、skills / skill_versions 目录与默认 .env（不覆盖已有 .env 除非 force_env）。"""
    home = hermes_home_dir()
    home.mkdir(parents=True, exist_ok=True)
    hermes_skills_dir().mkdir(parents=True, exist_ok=True)
    hermes_skill_versions_dir().mkdir(parents=True, exist_ok=True)
    env_path = home / ".env"
    if force_env or not env_path.is_file():
        env_path.write_text(build_hermes_env_lines(), encoding="utf-8")
    else:
        # 已有 .env：仍剥离 skills/terminal，避免旧配置继续放出 skill_view
        try:
            ensure_hermes_web_safe_toolsets()
        except Exception:
            pass
    # 始终写入/覆盖 SOUL.md，压过 Hermes 默认「先 skill_view」倾向
    try:
        (home / "SOUL.md").write_text(_testory_soul_md(), encoding="utf-8")
    except OSError:
        pass
    # 关键：限制 API Server 工具集（去掉 skill_view / terminal）
    try:
        ensure_hermes_api_server_toolsets()
    except Exception:
        pass
    _sync_hermes_env_to_process(env_path)
    # 同步仓库 bundled Skills（附录 B / manifest）；失败不阻断启动
    try:
        from modules.hermes.hermes_skill_bootstrap import sync_bundled_skills_to_hermes

        sync_bundled_skills_to_hermes(force=False)
    except Exception:
        pass
    # 幂等补丁 Hermes browser_snapshot/console 缓存（避免每次 CLI 子进程）
    try:
        from modules.hermes.patch_venv_hermes_tools import apply_patches as _apply_hermes_tool_patches

        _apply_hermes_tool_patches()
    except Exception:
        try:
            from modules.hermes import patch_venv_hermes_tools as _pvt

            if hasattr(_pvt, "main"):
                _pvt.main()
        except Exception:
            pass
    return home


# 平台推理配置写入 HERMES_HOME/.env 后，必须强制覆盖进程内旧值（否则会一直用上次的 MiMo Key）
_HERMES_LLM_ENV_KEYS = frozenset(
    {
        "PROVIDER",
        "HERMES_INFERENCE_PROVIDER",
        "OPENAI_API_BASE",
        "OPENAI_BASE_URL",
        "CUSTOM_BASE_URL",
        "OPENAI_MODEL",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_BASE_URL",
        "DEEPSEEK_API_KEY",
    }
)


def hermes_desired_llm_fingerprint() -> str:
    """当前平台应为 Hermes 使用的上游模型指纹（用于检测是否需重启）。"""
    from modules.ai.ai_multi_provider import normalize_api_key

    prof = _read_active_llm_profile()
    base = (prof.get("base_url") or "").strip()
    style = (prof.get("api_style") or "").strip()
    prov = (prof.get("provider") or "").strip()
    if style not in ("anthropic", "claude") and prov not in ("anthropic",):
        base = _normalize_openai_compatible_base_url(base) if base else ""
    else:
        base = base.rstrip("/")
    model = (prof.get("model_id") or "").strip()
    key = normalize_api_key(prof.get("api_key"))
    tail = key[-8:] if key else ""
    return f"{base}|{model}|{tail}"


def hermes_disk_llm_fingerprint() -> str:
    """HERMES_HOME 磁盘上实际写入的模型指纹（config.yaml / .env）。"""
    from modules.ai.ai_multi_provider import normalize_api_key

    snap = hermes_env_llm_snapshot()
    base = (snap.get("base_url") or "").strip()
    if base:
        base = _normalize_openai_compatible_base_url(base)
    model = (snap.get("model") or "").strip()
    key = ""
    try:
        cfg = _load_hermes_config_yaml().get("model")
        if isinstance(cfg, dict):
            key = normalize_api_key(cfg.get("api_key"))
        if not key:
            from dotenv import dotenv_values

            vals = dotenv_values(hermes_home_dir() / ".env")
            key = normalize_api_key(vals.get("OPENAI_API_KEY") or vals.get("DEEPSEEK_API_KEY"))
    except Exception:
        pass
    tail = key[-8:] if key else ""
    return f"{base}|{model}|{tail}"


def hermes_env_llm_snapshot() -> Dict[str, str]:
    """读取 Hermes 实际应使用的上游模型摘要（优先 config.yaml，其次 .env）。"""
    out: Dict[str, str] = {"model": "", "base_url": "", "provider": ""}
    cfg = _load_hermes_config_yaml()
    model_cfg = cfg.get("model")
    if isinstance(model_cfg, dict):
        out["model"] = str(model_cfg.get("default") or model_cfg.get("model") or "").strip()
        out["base_url"] = str(model_cfg.get("base_url") or "").strip()
        out["provider"] = str(model_cfg.get("provider") or "").strip()
    elif isinstance(model_cfg, str) and model_cfg.strip():
        out["model"] = model_cfg.strip()
    if out["model"] and out["base_url"]:
        return out
    env_path = hermes_home_dir() / ".env"
    if not env_path.is_file():
        return out
    try:
        from dotenv import dotenv_values

        vals = dotenv_values(env_path)
        out["model"] = out["model"] or str(vals.get("OPENAI_MODEL") or "").strip()
        out["base_url"] = out["base_url"] or str(
            vals.get("CUSTOM_BASE_URL") or vals.get("OPENAI_API_BASE") or vals.get("OPENAI_BASE_URL") or ""
        ).strip()
        out["provider"] = out["provider"] or str(
            vals.get("HERMES_INFERENCE_PROVIDER") or vals.get("PROVIDER") or ""
        ).strip()
    except Exception:
        pass
    return out


def _sync_hermes_env_to_process(env_path: Path) -> None:
    """将 HERMES_HOME/.env 同步到进程环境（供 HermesGatewayClient / 子进程继承）。"""
    if not env_path.is_file():
        return
    try:
        from dotenv import dotenv_values

        for key, val in dotenv_values(env_path).items():
            if not key or val is None:
                continue
            sval = str(val)
            if key in _HERMES_LLM_ENV_KEYS:
                # 模型切换后必须覆盖，不能保留父进程里的旧 OPENAI_*
                os.environ[key] = sval
                continue
            current = os.environ.get(key)
            if not current or not current.strip():
                os.environ[key] = sval
    except ImportError:
        pass
    # Hermes 认 API_SERVER_KEY；平台客户端认 HERMES_API_SERVER_KEY —— 必须一致
    api_key = (os.environ.get("API_SERVER_KEY") or "").strip()
    hermes_key = (os.environ.get("HERMES_API_SERVER_KEY") or "").strip()
    if api_key:
        os.environ["HERMES_API_SERVER_KEY"] = api_key
    elif hermes_key:
        os.environ["API_SERVER_KEY"] = hermes_key
    if not (os.environ.get("HERMES_GATEWAY_URL") or "").strip():
        os.environ["HERMES_GATEWAY_URL"] = "http://127.0.0.1:8642"


def hermes_cdp_endpoint_active() -> str:
    """当前进程内已同步的画布 CDP WebSocket URL（空表示未 attach）。"""
    env = (os.environ.get("HERMES_CDP_ENDPOINT") or "").strip()
    if env:
        return env
    return (_ACTIVE_CDP_ENDPOINT or "").strip()


def hermes_cdp_attached() -> bool:
    return bool(hermes_cdp_endpoint_active())


def _hermes_env_path() -> Path:
    return hermes_home_dir() / ".env"


def _upsert_env_line(lines: list[str], key: str, value: str) -> list[str]:
    prefix = f"{key}="
    out: list[str] = []
    found = False
    for line in lines:
        if line.startswith(prefix):
            out.append(f"{prefix}{value}")
            found = True
        else:
            out.append(line)
    if not found:
        out.append(f"{prefix}{value}")
    return out


def _remove_env_line(lines: list[str], key: str) -> list[str]:
    prefix = f"{key}="
    return [line for line in lines if not line.startswith(prefix)]


def _write_hermes_env_lines(lines: list[str]) -> None:
    env_path = _hermes_env_path()
    env_path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join([ln for ln in lines if ln is not None]).strip()
    if text:
        text += "\n"
    env_path.write_text(text, encoding="utf-8")
    _sync_hermes_env_to_process(env_path)


def _try_hot_update_cdp(ws: str) -> bool:
    """尝试通过 Hermes HTTP API 热更新 CDP 端点，不支持则返回 False。"""
    try:
        import requests

        base = (os.environ.get("HERMES_GATEWAY_URL") or "http://127.0.0.1:8642").rstrip("/")
        resp = requests.post(
            f"{base}/v1/config/cdp",
            json={"cdp_endpoint": ws},
            timeout=5,
        )
        return resp.ok
    except Exception:
        return False


def sync_hermes_cdp_endpoint(cdp_ws_url: str, *, restart_gateway: bool = True) -> bool:
    """
    将本机浏览器（Edge/Chrome remote debugging）的 CDP WebSocket URL
    写入 HERMES_HOME/.env 与进程环境，供 Hermes gateway 以 cdp_attach 模式操作。

    返回值：
    - True: Hermes Gateway 已获取到 CDP 配置（热更新成功或已重启）
    - False: Hermes Gateway 需要重启才能获取配置（热更新失败且未重启）
    """
    global _ACTIVE_CDP_ENDPOINT
    ws = (cdp_ws_url or "").strip()
    if not ws:
        return False
    ensure_hermes_home()
    env_path = _hermes_env_path()
    if env_path.is_file():
        lines = env_path.read_text(encoding="utf-8", errors="replace").splitlines()
    else:
        lines = build_hermes_env_lines().splitlines()
    lines = _upsert_env_line(lines, "HERMES_CDP_ENDPOINT", ws)
    lines = _upsert_env_line(lines, "BROWSER_CDP_URL", ws)
    if "HERMES_BROWSER_MODE=cdp_attach" not in lines:
        lines.append("HERMES_BROWSER_MODE=cdp_attach")
    _write_hermes_env_lines(lines)
    os.environ["HERMES_CDP_ENDPOINT"] = ws
    os.environ["BROWSER_CDP_URL"] = ws
    os.environ["HERMES_BROWSER_MODE"] = "cdp_attach"
    _ACTIVE_CDP_ENDPOINT = ws

    # 尝试热更新 CDP 端点
    hot_update_ok = _try_hot_update_cdp(ws)

    if hot_update_ok:
        # 热更新成功，Hermes Gateway 已获取到配置
        return True

    # 热更新失败
    if restart_gateway:
        # 需要重启 Hermes Gateway
        try:
            from modules.hermes.hermes_service_bootstrap import restart_hermes_gateway

            restart_hermes_gateway()
            uat_logger.info("Hermes Gateway 已重启以加载 CDP 配置")
        except Exception as e:
            uat_logger.warning("重启 Hermes Gateway 失败: %s", e)
            return False
        return True
    else:
        # 不允许重启，返回 False 让调用方知道需要重启
        uat_logger.info("CDP 热更新失败，需要重启 Hermes Gateway")
        return False


def clear_hermes_cdp_endpoint(*, restart_gateway: bool = True) -> None:
    """本机浏览器会话结束时清除 CDP attach 配置。"""
    global _ACTIVE_CDP_ENDPOINT
    _ACTIVE_CDP_ENDPOINT = ""
    os.environ.pop("HERMES_CDP_ENDPOINT", None)
    os.environ.pop("BROWSER_CDP_URL", None)
    env_path = _hermes_env_path()
    if env_path.is_file():
        lines = _remove_env_line(env_path.read_text(encoding="utf-8", errors="replace").splitlines(), "HERMES_CDP_ENDPOINT")
        lines = _remove_env_line(lines, "BROWSER_CDP_URL")
        _write_hermes_env_lines(lines)
    if restart_gateway:
        try:
            from modules.hermes.hermes_service_bootstrap import restart_hermes_gateway

            restart_hermes_gateway()
        except Exception:
            pass
