import pathlib

p = pathlib.Path(r'D:\mkst_baixiang\Python_Code\NewUITestPlatform\NewUITestPlatform\mobile_routes.py')
text = p.read_text(encoding='utf-8')

old = '''def _connect_response_with_mirror(
    udid: str,
    agent_result: Dict[str, Any],
    *,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """基于 _connect_response 结果追加投屏信息。"""
    from mobile_mirror import start_scrcpy_mirror

    resolved = (agent_result.get("udid") or udid or "").strip()
    mirror = start_scrcpy_mirror(resolved)
    client_host = (request.host or "").split(":")[0] if request else ""
    out = _connect_response(resolved, agent_result)
    out["session_id"] = mirror.get("session_id") or out.get("session_id") or ""
    out["scrcpy_started"] = bool(mirror.get("scrcpy_started"))
    out.update(_mirror_payload(resolved, mirror.get("session_id") or "", client_host=client_host))
    if extra:
        out.update(extra)
    return out'''

new = '''def _connect_response_with_mirror(
    udid: str,
    agent_result: Dict[str, Any],
    *,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """基于 _connect_response 结果追加投屏标记（不触发 warm，由前端异步初始化）。"""
    from mobile_mirror import start_scrcpy_mirror
    from mobile_env_config import scrcpy_available

    resolved = (agent_result.get("udid") or udid or "").strip()
    mirror = start_scrcpy_mirror(resolved)
    out = _connect_response(resolved, agent_result)
    out["session_id"] = mirror.get("session_id") or out.get("session_id") or ""
    out["scrcpy_started"] = bool(mirror.get("scrcpy_started"))
    out["scrcpy_available"] = scrcpy_available()
    if extra:
        out.update(extra)
    return out'''

if old in text:
    text = text.replace(old, new, 1)
    p.write_text(text, encoding='utf-8')
    print('OK: removed blocking _mirror_payload from connect')
else:
    # debug
    idx = text.find('def _connect_response_with_mirror')
    if idx >= 0:
        print(repr(text[idx:idx+600]))
    else:
        print('NOT FOUND')
