"""移动端 Playground（Phase 2）：Tap / Assert / Query / Act 即时操作。"""
from __future__ import annotations

import json
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from vision_action_port import ActResult, MobileVisionActionPort


def _wait_after_action_ms() -> int:
    from mobile_env_config import mobile_wait_after_action_ms

    return mobile_wait_after_action_ms()


def _act_cycle_limit() -> int:
    import os

    try:
        return max(1, min(20, int(os.environ.get("MOBILE_PLAYGROUND_ACT_LIMIT", "8"))))
    except ValueError:
        return 8


def _record_playground_step(
    *,
    action: str,
    description: str,
    ok: bool,
    message: str,
    png_before: Optional[bytes] = None,
    png_after: Optional[bytes] = None,
    duration_ms: int = 0,
) -> Optional[Dict[str, Any]]:
    from vision_step_report import VisionReplaySession, vision_replay_enabled

    if not vision_replay_enabled():
        return None
    sess = VisionReplaySession.start(platform="android")
    step = {"action": action, "description": description}
    status = "success" if ok else "error"
    sess.record(1, step, status, message=message, png_bytes=png_before, duration_ms=duration_ms)
    if png_after and ok:
        sess.record(
            2,
            {"action": "screenshot", "description": "操作后画面"},
            "success",
            message="",
            png_bytes=png_after,
        )
    return sess.finalize()


def _capture_png(port: MobileVisionActionPort) -> Tuple[Optional[bytes], str]:
    try:
        frame = port.capture()
        return frame.png_bytes, ""
    except Exception as e:
        return None, str(e)


def playground_tap(udid: str, locate: str) -> Dict[str, Any]:
    locate = (locate or "").strip()
    if not locate:
        return {"success": False, "error": "请描述要点击的元素"}
    port = MobileVisionActionPort(udid)
    t0 = time.time()
    png_before, cap_err = _capture_png(port)
    if cap_err and not png_before:
        return {"success": False, "error": cap_err or "无法获取设备画面"}
    result = port.tap(locate)
    time.sleep(_wait_after_action_ms() / 1000.0)
    png_after, _ = _capture_png(port)
    replay = _record_playground_step(
        action="ai_tap",
        description=locate,
        ok=result.ok,
        message=result.message,
        png_before=png_before,
        png_after=png_after if result.ok else None,
        duration_ms=int((time.time() - t0) * 1000),
    )
    out: Dict[str, Any] = {
        "success": result.ok,
        "message": result.message or ("已点击" if result.ok else "点击失败"),
    }
    if not result.ok:
        out["error"] = out["message"]
    if replay:
        out["replay"] = replay
        out["replay_url"] = replay.get("url")
    return out


def playground_assert(udid: str, condition: str) -> Dict[str, Any]:
    condition = (condition or "").strip()
    if not condition:
        return {"success": False, "error": "请描述要检查的内容"}
    port = MobileVisionActionPort(udid)
    t0 = time.time()
    png_before, cap_err = _capture_png(port)
    if cap_err and not png_before:
        return {"success": False, "error": cap_err or "无法获取设备画面"}
    result = port.assert_vision(condition)
    replay = _record_playground_step(
        action="assert_vision",
        description=condition,
        ok=result.ok,
        message=result.message,
        png_before=png_before,
        duration_ms=int((time.time() - t0) * 1000),
    )
    out: Dict[str, Any] = {
        "success": result.ok,
        "message": result.message,
        "passed": result.ok,
    }
    if not result.ok:
        out["error"] = result.message
    if replay:
        out["replay"] = replay
        out["replay_url"] = replay.get("url")
    return out


def playground_query(udid: str, prompt: str) -> Dict[str, Any]:
    prompt = (prompt or "").strip()
    if not prompt:
        return {"success": False, "error": "请描述要从画面读取的内容"}
    port = MobileVisionActionPort(udid)
    t0 = time.time()
    png_before, cap_err = _capture_png(port)
    if cap_err and not png_before:
        return {"success": False, "error": cap_err or "无法获取设备画面"}
    text, err = port.query(prompt)
    ok = bool(text) and not err
    replay = _record_playground_step(
        action="extract_vision",
        description=prompt,
        ok=ok,
        message=text or err,
        png_before=png_before,
        duration_ms=int((time.time() - t0) * 1000),
    )
    if not ok:
        return {
            "success": False,
            "error": err or "未能从画面读取信息",
            "replay": replay,
            "replay_url": (replay or {}).get("url"),
        }
    return {
        "success": True,
        "data": text,
        "message": text,
        "replay": replay,
        "replay_url": (replay or {}).get("url"),
    }


def _parse_act_plan(raw: str) -> Optional[Dict[str, Any]]:
    text = (raw or "").strip()
    if not text:
        return None
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        try:
            obj = json.loads(m.group(0))
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
    low = text.lower()
    if "done" in low or "完成" in text or "已完成" in text:
        return {"action": "done", "summary": text[:200]}
    return None


def _plan_next_act_step(png_bytes: bytes, goal: str, history: List[str]) -> Tuple[Optional[Dict[str, Any]], str]:
    from ai_vision_insight import _insight_model
    from ai_vision_local import vision_describe, vision_enabled

    if not vision_enabled():
        return None, "视觉服务暂不可用，请确认 Ollama 视觉模型已启动"
    hist = "\n".join(f"- {h}" for h in history[-6:]) if history else "（尚无）"
    ins = (
        "你是 Android 界面操作助手。根据截图和用户总目标，规划【下一步】唯一操作。\n"
        f"总目标：{goal[:600]}\n"
        f"已完成步骤：\n{hist}\n\n"
        "仅回复一行 JSON，不要 markdown：\n"
        '{"action":"tap","target":"元素中文描述"}\n'
        '{"action":"input","target":"输入框描述","text":"要输入的文字"}\n'
        '{"action":"done","summary":"任务完成说明"}\n'
        "若无法继续，用 done 并说明原因。"
    )
    try:
        raw = vision_describe(png_bytes, ins, model=_insight_model())
    except ValueError as e:
        return None, str(e)
    plan = _parse_act_plan(raw)
    if not plan:
        return None, f"无法解析下一步操作：{(raw or '')[:120]}"
    return plan, ""


def playground_act(udid: str, goal: str) -> Dict[str, Any]:
    goal = (goal or "").strip()
    if not goal:
        return {"success": False, "error": "请描述要完成的目标"}
    port = MobileVisionActionPort(udid)
    history: List[str] = []
    steps_log: List[Dict[str, Any]] = []
    limit = _act_cycle_limit()

    from vision_step_report import VisionReplaySession, vision_replay_enabled

    replay_sess = VisionReplaySession.start(platform="android") if vision_replay_enabled() else None
    t0 = time.time()

    for i in range(1, limit + 1):
        png, cap_err = _capture_png(port)
        if not png:
            msg = cap_err or "无法获取设备画面"
            if replay_sess:
                replay_sess.record(i, {"action": "ai_act", "description": goal}, "error", msg, png_bytes=None)
            return {
                "success": False,
                "error": msg,
                "steps": steps_log,
                "replay_url": replay_sess.finalize().get("url") if replay_sess else None,
            }

        plan, plan_err = _plan_next_act_step(png, goal, history)
        if plan_err or not plan:
            if replay_sess:
                replay_sess.record(
                    i,
                    {"action": "ai_act", "description": goal},
                    "error",
                    plan_err or "规划失败",
                    png_bytes=png,
                )
            return {
                "success": False,
                "error": plan_err or "规划失败",
                "steps": steps_log,
                "replay_url": replay_sess.finalize().get("url") if replay_sess else None,
            }

        action = (plan.get("action") or "").strip().lower()
        if action == "done":
            summary = (plan.get("summary") or "任务已完成").strip()
            history.append(f"完成：{summary}")
            steps_log.append({"action": "done", "summary": summary, "ok": True})
            if replay_sess:
                replay_sess.record(
                    i,
                    {"action": "ai_act", "description": summary},
                    "success",
                    summary,
                    png_bytes=png,
                )
            replay_meta = replay_sess.finalize() if replay_sess else None
            return {
                "success": True,
                "message": summary,
                "steps": steps_log,
                "replay": replay_meta,
                "replay_url": (replay_meta or {}).get("url"),
                "duration_ms": int((time.time() - t0) * 1000),
            }

        result: ActResult
        label = ""
        if action == "tap":
            target = (plan.get("target") or plan.get("locate") or "").strip()
            label = f"点击：{target}"
            result = port.tap(target) if target else ActResult(ok=False, message="缺少点击目标")
        elif action == "input":
            target = (plan.get("target") or plan.get("locate") or "").strip()
            text = str(plan.get("text") or plan.get("value") or "")
            label = f"输入「{text}」到 {target}"
            result = port.input_text(target, text) if target else ActResult(ok=False, message="缺少输入框描述")
        else:
            result = ActResult(ok=False, message=f"不支持的操作类型：{action}")
            label = action

        history.append(f"{label} → {'成功' if result.ok else result.message}")
        steps_log.append({"action": action, "label": label, "ok": result.ok, "message": result.message})
        if replay_sess:
            replay_sess.record(
                i,
                {"action": action, "description": label},
                "success" if result.ok else "error",
                result.message,
                png_bytes=png,
            )
        if not result.ok:
            replay_meta = replay_sess.finalize() if replay_sess else None
            return {
                "success": False,
                "error": result.message,
                "steps": steps_log,
                "replay": replay_meta,
                "replay_url": (replay_meta or {}).get("url"),
            }
        time.sleep(_wait_after_action_ms() / 1000.0)

    replay_meta = replay_sess.finalize() if replay_sess else None
    return {
        "success": False,
        "error": f"已达到最大步骤数（{limit}），请拆分目标后重试",
        "steps": steps_log,
        "replay": replay_meta,
        "replay_url": (replay_meta or {}).get("url"),
    }


def _parse_act_input_label(label: str) -> Tuple[str, str]:
    """解析 Act 步骤标签「输入「text」到 target」。"""
    m = re.match(r"输入[「\"](.+?)[」\"]到\s*(.+)", (label or "").strip())
    if m:
        return m.group(2).strip(), m.group(1)
    return (label or "").strip(), ""


def replay_meta_to_test_steps(replay_steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """将 Playground 回放 meta 转为可写入用例的步骤。"""
    from ai_step_normalization import normalize_ai_step

    out: List[Dict[str, Any]] = []
    for st in replay_steps or []:
        if (st.get("status") or "").strip().lower() != "success":
            continue
        action = (st.get("action") or "").strip().lower()
        label = (st.get("label") or "").strip()
        msg = (st.get("message") or "").strip()
        if action in ("screenshot",):
            continue
        raw: Dict[str, Any] = {"automation_layer": "android"}
        if action == "ai_tap":
            desc = label or msg
            raw.update({"action": "ai_tap", "description": desc, "locate_prompt": desc})
        elif action == "assert_vision":
            desc = label or msg
            raw.update({"action": "assert_vision", "description": desc})
        elif action == "extract_vision":
            desc = label or msg
            raw.update({"action": "extract_vision", "description": desc})
        elif action == "tap":
            locate = label[3:].strip() if label.startswith("点击：") else label
            raw.update({"action": "ai_tap", "description": locate, "locate_prompt": locate})
        elif action == "input":
            target, text = _parse_act_input_label(label)
            raw.update({
                "action": "ai_input",
                "description": target,
                "locate_prompt": target,
                "input_value": text,
            })
        elif action == "ai_act":
            if label.startswith("完成：") or "完成" in label[:6]:
                continue
            desc = label or msg
            raw.update({"action": "assert_vision", "description": desc})
        else:
            continue
        out.append(normalize_ai_step(raw))
    return out


def playground_save_replay_to_case(run_id: str, case_id: int, *, user_id: int = 0) -> Dict[str, Any]:
    """将 Playground 回放步骤追加到用例（继承用例 unit_id）。"""
    import json as _json

    from database import Database
    from vision_step_report import replay_run_dir

    run_id = (run_id or "").strip()
    if not run_id:
        return {"success": False, "error": "缺少回放 run_id"}
    try:
        case_id = int(case_id)
    except (TypeError, ValueError):
        return {"success": False, "error": "case_id 无效"}

    meta_path = replay_run_dir(run_id) / "meta.json"
    if not meta_path.is_file():
        return {"success": False, "error": "回放记录不存在或已过期"}
    try:
        meta = _json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, _json.JSONDecodeError) as e:
        return {"success": False, "error": f"无法读取回放：{e}"}

    test_steps = replay_meta_to_test_steps(meta.get("steps") or [])
    if not test_steps:
        return {"success": False, "error": "没有可保存的成功步骤"}

    db = Database()
    case_row = db.get_test_case_v2(case_id)
    if not case_row:
        return {"success": False, "error": "用例不存在"}
    pid = case_row.get("project_id")
    if pid and user_id and not db.check_project_access(user_id, int(pid), "editor"):
        return {"success": False, "error": "无权限修改此用例"}

    created: List[int] = []
    for st in test_steps:
        ms = {"source": "playground", "replay_run_id": run_id}
        sid = db.create_test_step(
            case_id,
            st.get("action") or "tap",
            st.get("selector_type") or "",
            st.get("selector_value") or "",
            input_value=st.get("input_value") or "",
            description=st.get("description") or "",
            automation_layer="android",
            mobile_spec=_json.dumps(ms, ensure_ascii=False),
            locator_candidates=st.get("locate_prompt") or "",
        )
        created.append(sid)

    return {
        "success": True,
        "case_id": case_id,
        "unit_id": case_row.get("unit_id"),
        "step_ids": created,
        "step_count": len(created),
        "replay_url": f"/api/ai/vision/replay/{run_id}/",
        "message": f"已保存 {len(created)} 个步骤到用例",
    }

