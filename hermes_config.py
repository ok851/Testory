# -*- coding: utf-8 -*-
"""Hermes 内嵌配置：安装目录 .env 模板与 LLM provider 同步。"""
from __future__ import annotations

import json
import os
import secrets
from pathlib import Path
from typing import Any, Dict, Optional

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
                # 仓库内常见字面目录名「%UAT_DATA_DIR%/hermes」
                lit = Path(__file__).resolve().parent / "%UAT_DATA_DIR%" / "hermes"
                if lit.is_dir():
                    return lit
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
        from ai_config_paths import ai_model_registry_path

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
        'toolsets=["hermes-cli","browser","web","memory","skills","terminal"]',
        "HERMES_BROWSER_MODE=cdp_attach",
    ]
    cdp = (os.environ.get("HERMES_CDP_ENDPOINT") or "").strip()
    if cdp:
        lines.append(f"HERMES_CDP_ENDPOINT={cdp}")
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
    """检查当前平台推理配置能否被 Hermes（OpenAI 兼容 + Bearer）正常使用。"""
    prof = _read_active_llm_profile()
    from ai_multi_provider import normalize_api_key, _uses_xiaomimimo_auth

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
    }
    if style == "ollama" or prov == "ollama":
        out["ok"] = True
        out["reason"] = "ollama"
        return out
    if _uses_xiaomimimo_auth(base, prov, key):
        # 尝试从注册表找一个 Bearer 兼容配置给 Hermes 用
        alt = _find_bearer_compatible_profile()
        if alt:
            out["ok"] = True
            out["reason"] = "fallback_bearer_profile"
            out["fallback_profile_id"] = alt.get("id")
            out["fallback_model_id"] = alt.get("model_id")
            out["message"] = (
                f"当前页面引擎为小米 MiMo（不兼容 Hermes），"
                f"已为智能体改用备用模型 {(alt.get('label') or alt.get('model_id') or '')}。"
            )
            out["active_is_xiaomi"] = True
            out["hermes_profile"] = alt
            return out
        out["ok"] = False
        out["reason"] = "xiaomi_mimo_api_key_header"
        out["message"] = (
            "当前推理引擎为小米 MiMo（api-key 头）。Hermes 只支持 Authorization: Bearer，"
            "会报 Missing Authentication header。"
            "请在左侧改选 DeepSeek / OpenAI 等 Bearer 兼容模型并设为当前引擎，然后停止再启动智能体。"
            "桌面任务将自动走平台本机执行。"
        )
        return out
    if style in ("anthropic_messages",) or prov == "anthropic":
        out["ok"] = False
        out["reason"] = "anthropic_not_openai_compat"
        out["message"] = (
            "当前推理引擎为 Anthropic Messages API，Hermes 默认按 OpenAI 兼容调用。"
            "请为智能体改用 OpenAI 兼容模型，或桌面任务走平台本机执行。"
        )
        return out
    if not key:
        out["ok"] = False
        out["reason"] = "missing_api_key"
        out["message"] = "未配置推理引擎 API Key，Hermes 无法调用上游模型。"
        return out
    if not base:
        out["ok"] = False
        out["reason"] = "missing_base_url"
        out["message"] = "未配置推理引擎 base_url。"
        return out
    out["reason"] = "openai_compatible"
    out["hermes_profile"] = prof
    return out


def _find_bearer_compatible_profile() -> Optional[Dict[str, Any]]:
    """在模型注册表中找一个 Hermes 可用的 Bearer 配置（跳过小米 MiMo / Anthropic）。"""
    try:
        from ai_config_paths import ai_model_registry_path
        from ai_multi_provider import normalize_api_key, _uses_xiaomimimo_auth

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


def sync_platform_llm_credentials_to_hermes_env() -> Dict[str, Any]:
    """把平台当前（或备用 Bearer）推理配置 upsert 进 HERMES_HOME/.env。"""
    home = hermes_home_dir()
    home.mkdir(parents=True, exist_ok=True)
    env_path = home / ".env"
    status = hermes_upstream_llm_status()
    from ai_multi_provider import normalize_api_key

    prof = status.get("hermes_profile") or _read_active_llm_profile()
    if status.get("reason") == "fallback_bearer_profile" and status.get("hermes_profile"):
        prof = status["hermes_profile"]

    base = (prof.get("base_url") or os.environ.get("LOCAL_LLM_BASE_URL") or "").strip()
    model_id = (prof.get("model_id") or "").strip()
    key = normalize_api_key(prof.get("api_key"))
    prov = (prof.get("provider") or "").strip()
    style = (prof.get("api_style") or "").strip()

    lines: list[str] = []
    if env_path.is_file():
        try:
            lines = env_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = []
    if not lines:
        lines = build_hermes_env_lines().splitlines()

    if style == "ollama" or prov == "ollama":
        lines = _upsert_env_line(lines, "PROVIDER", "openai_compatible")
        if base:
            lines = _upsert_env_line(lines, "OPENAI_API_BASE", base.rstrip("/"))
        if model_id:
            lines = _upsert_env_line(lines, "OPENAI_MODEL", model_id)
        lines = _upsert_env_line(lines, "OPENAI_API_KEY", "ollama")
    elif status.get("ok") and key and base:
        lines = _upsert_env_line(lines, "PROVIDER", "openai_compatible")
        lines = _upsert_env_line(lines, "OPENAI_API_BASE", base.rstrip("/"))
        if model_id:
            lines = _upsert_env_line(lines, "OPENAI_MODEL", model_id)
        lines = _upsert_env_line(lines, "OPENAI_API_KEY", key)

    _write_hermes_env_lines(lines)
    _sync_hermes_env_to_process(env_path)
    return {"synced": True, "status": status, "env_path": str(env_path)}


def ensure_hermes_home(*, force_env: bool = False) -> Path:
    """创建 HERMES_HOME、skills / skill_versions 目录与默认 .env（不覆盖已有 .env 除非 force_env）。"""
    home = hermes_home_dir()
    home.mkdir(parents=True, exist_ok=True)
    hermes_skills_dir().mkdir(parents=True, exist_ok=True)
    hermes_skill_versions_dir().mkdir(parents=True, exist_ok=True)
    env_path = home / ".env"
    if force_env or not env_path.is_file():
        env_path.write_text(build_hermes_env_lines(), encoding="utf-8")
    _sync_hermes_env_to_process(env_path)
    return home


# 平台推理配置写入 HERMES_HOME/.env 后，必须强制覆盖进程内旧值（否则会一直用上次的 MiMo Key）
_HERMES_LLM_ENV_KEYS = frozenset(
    {
        "PROVIDER",
        "OPENAI_API_BASE",
        "OPENAI_BASE_URL",
        "OPENAI_MODEL",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_BASE_URL",
    }
)


def hermes_desired_llm_fingerprint() -> str:
    """当前平台应为 Hermes 使用的上游模型指纹（用于检测是否需重启）。"""
    from ai_multi_provider import normalize_api_key

    status = hermes_upstream_llm_status()
    prof = status.get("hermes_profile") or _read_active_llm_profile()
    base = (prof.get("base_url") or "").strip().rstrip("/")
    model = (prof.get("model_id") or "").strip()
    key = normalize_api_key(prof.get("api_key"))
    tail = key[-8:] if key else ""
    return f"{base}|{model}|{tail}"


def hermes_env_llm_snapshot() -> Dict[str, str]:
    """读取 HERMES_HOME/.env 中的上游模型摘要（供 UI 展示「智能体实际模型」）。"""
    env_path = hermes_home_dir() / ".env"
    out: Dict[str, str] = {"model": "", "base_url": "", "provider": ""}
    if not env_path.is_file():
        return out
    try:
        from dotenv import dotenv_values

        vals = dotenv_values(env_path)
        out["model"] = str(vals.get("OPENAI_MODEL") or "").strip()
        out["base_url"] = str(vals.get("OPENAI_API_BASE") or vals.get("OPENAI_BASE_URL") or "").strip()
        out["provider"] = str(vals.get("PROVIDER") or "").strip()
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
    if "HERMES_BROWSER_MODE=cdp_attach" not in lines:
        lines.append("HERMES_BROWSER_MODE=cdp_attach")
    _write_hermes_env_lines(lines)
    os.environ["HERMES_CDP_ENDPOINT"] = ws
    os.environ["HERMES_BROWSER_MODE"] = "cdp_attach"
    _ACTIVE_CDP_ENDPOINT = ws
    if restart_gateway:
        # 优先尝试热更新 CDP 端点，避免重启 Hermes 进程
        if not _try_hot_update_cdp(ws):
            try:
                from hermes_service_bootstrap import restart_hermes_gateway

                restart_hermes_gateway()
            except Exception:
                pass
    return True


def clear_hermes_cdp_endpoint(*, restart_gateway: bool = True) -> None:
    """本机浏览器会话结束时清除 CDP attach 配置。"""
    global _ACTIVE_CDP_ENDPOINT
    _ACTIVE_CDP_ENDPOINT = ""
    os.environ.pop("HERMES_CDP_ENDPOINT", None)
    env_path = _hermes_env_path()
    if env_path.is_file():
        lines = _remove_env_line(env_path.read_text(encoding="utf-8", errors="replace").splitlines(), "HERMES_CDP_ENDPOINT")
        _write_hermes_env_lines(lines)
    if restart_gateway:
        try:
            from hermes_service_bootstrap import restart_hermes_gateway

            restart_hermes_gateway()
        except Exception:
            pass
