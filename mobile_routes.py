# -*- coding: utf-8 -*-
"""Android 移动端 Flask 路由与用例执行。"""

from __future__ import annotations

import json
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from flask import Response, jsonify, request, stream_with_context
from flask_login import current_user, login_required

from mobile_device_manager import (
    wireless_pair_and_connect,
    adb_disconnect_device,
    check_mobile_health,
    collect_device_warnings,
    format_connect_error,
    get_device_info,
    get_foreground_app,
    is_emulator_udid,
    list_devices_for_ui,
    list_emulators,
    list_usb_devices,
    list_user_apps,
    pick_default_emulator,
    pick_default_device,
    prune_stale_adb_devices,
    set_connected_udid,
)
from mobile_adb_control import adb_press_back, adb_press_home, adb_swipe, smart_tap
from mobile_env_config import (
    auto_connect_on_studio,
    mobile_driver_mode,
    mobile_enabled,
    mobile_runtime_available,
    public_config,
    requires_appium_for_execution,
    save_mobile_defaults,
)
from mobile_agent_client import (
    agent_connect_device,
    agent_disconnect_device,
    agent_install_plugin,
    agent_page_source,
    agent_plugin_status,
    agent_replay_step,
    agent_replay_steps,
    agent_scan_devices,
    agent_screenshot,
    agent_start_recording,
    agent_stop_recording,
    agent_pause_recording,
    agent_resume_recording,
    agent_live_recording_steps,
    mobile_agent_enabled,
    mobile_agent_ws_url,
)

try:
    from uat_logger import uat_logger
except ImportError:
    import logging

    uat_logger = logging.getLogger(__name__)

def _require_mobile_enabled():
    if not mobile_enabled():
        return jsonify({
            "success": False,
            "error": "移动端测试未启用，请在 .env 中设置 ENABLE_MOBILE=1",
            "enabled": False,
        }), 403
    return None


def _mobile_phone_only_response():
    """PC 端不再驱动录制/回放/运行，仅保留同步管理。"""
    return jsonify({
        "success": False,
        "error": "该功能已移至手机 Testory 助手，PC 端仅支持配对与步骤同步管理",
        "deprecated": True,
    }), 410


def execute_mobile_case(
    case_id: int,
    case: Dict[str, Any],
    steps: List[Dict[str, Any]],
    db: Any,
    user_id: int,
    start_time: float,
    *,
    capabilities: Optional[Dict[str, Any]] = None,
    udid: str = "",
    job_update: Optional[Callable[..., None]] = None,
    job_cancelled: Optional[Callable[[], bool]] = None,
) -> Tuple[Any, int]:
    """执行纯 Android 用例 — 已移至手机助手，PC 不再回放。"""
    msg = "PC 端已不再执行移动端用例，请在手机 Testory 助手内运行"
    run_id = db.create_run_history(case_id, "error", 0, msg, "", "")
    return jsonify({
        "success": False,
        "status": "error",
        "error": msg,
        "run_id": run_id,
        "deprecated": True,
    }), 410


def _resolve_request_udid(body: Optional[Dict[str, Any]] = None) -> str:
    body = body if isinstance(body, dict) else {}
    udid = (body.get("udid") or "").strip()
    if not udid:
        from mobile_device_manager import get_connected_udid

        udid = get_connected_udid() or ""
    return udid

def _connect_response(udid, agent_result):
    info = get_device_info(udid)
    return {
        "success": True,
        "udid": udid,
        "session_id": agent_result.get("session_id") or "",
        "device_width": info.get("width") or 1080,
        "device_height": info.get("height") or 1920,
        "assistant_installed": agent_result.get("assistant_installed", False),
        "assistant_connected": agent_result.get("assistant_connected", False),
        "appium_connected": agent_result.get("appium_connected", False),
        "device_info": info,
    }


def register_mobile_routes(app, *, api_error_handler, log_api_request, role_required=None):
    """注册移动端 API 到 Flask app。"""

    def _roles(*args):
        if role_required is None:
            return lambda f: f
        return role_required(*args)

    @app.route("/api/mobile/config", methods=["GET"])
    @login_required
    @api_error_handler
    def api_mobile_config():
        blocked = _require_mobile_enabled()
        if blocked:
            return blocked
        return jsonify({"success": True, **public_config()})

    @app.route("/api/mobile/config", methods=["POST"])
    @login_required
    @api_error_handler
    def api_mobile_config_save():
        blocked = _require_mobile_enabled()
        if blocked:
            return blocked
        body = request.get_json(silent=True) or {}
        save_mobile_defaults({
            "appium_server_url": body.get("appium_server_url"),
            "app_package": body.get("app_package"),
            "app_activity": body.get("app_activity"),
            "device_name": body.get("device_name"),
            "udid": body.get("udid"),
            "adb_path": body.get("adb_path"),
        })
        return jsonify({"success": True, **public_config()})

    @app.route("/api/mobile/health", methods=["GET"])
    @login_required
    @api_error_handler
    def api_mobile_health():
        blocked = _require_mobile_enabled()
        if blocked:
            return blocked
        return jsonify({"success": True, **check_mobile_health()})

    @app.route("/api/mobile/devices", methods=["GET"])
    @login_required
    @_roles("admin", "tester", "project_manager", "test_lead")
    @api_error_handler
    def api_mobile_devices():
        blocked = _require_mobile_enabled()
        if blocked:
            return blocked
        devices = list_usb_devices()
        emulators = list_emulators()
        real_devices = list_devices_for_ui("real")
        if not devices:
            health = check_mobile_health()
            if not health.get("adb_ok"):
                return jsonify({"success": False, "error": health.get("adb_message"), "devices": []}), 503
        return jsonify({
            "success": True,
            "devices": devices,
            "emulators": emulators,
            "real_devices": real_devices,
            "device_warnings": collect_device_warnings(real_devices),
        })

    @app.route("/api/mobile/devices/prune", methods=["POST"])
    @login_required
    @_roles("admin", "tester", "project_manager", "test_lead")
    @api_error_handler
    @log_api_request
    def api_mobile_devices_prune():
        """清理离线、deny 前缀等幽灵 adb 设备。"""
        blocked = _require_mobile_enabled()
        if blocked:
            return blocked
        result = prune_stale_adb_devices()
        real_devices = list_devices_for_ui("real")
        return jsonify({
            "success": True,
            "pruned": result.get("pruned") or [],
            "errors": result.get("errors") or [],
            "devices": result.get("devices") or [],
            "real_devices": real_devices,
            "emulators": result.get("emulators") or list_emulators(),
            "device_warnings": collect_device_warnings(real_devices),
        })

    @app.route("/api/mobile/connect", methods=["POST"])
    @login_required
    @_roles("admin", "tester", "project_manager", "test_lead")
    @api_error_handler
    @log_api_request
    def api_mobile_connect():
        blocked = _require_mobile_enabled()
        if blocked:
            return blocked
        body = request.get_json(silent=True) or {}
        udid = (body.get("udid") or "").strip()
        if not udid:
            dev = pick_default_device()
            udid = (dev or {}).get("udid") or ""
        result = agent_connect_device(udid=udid)
        if not result.get("success"):
            return jsonify(result), 503
        set_connected_udid(result.get("udid") or udid)
        return jsonify(_connect_response(udid, result))

    @app.route("/api/mobile/disconnect", methods=["POST"])
    @login_required
    @api_error_handler
    def api_mobile_disconnect():
        body = request.get_json(silent=True) or {}
        udid = _resolve_request_udid(body)
        session_id = (body.get("session_id") or "").strip()
        # scrcpy/mirror removed ? mobile mirror feature retired
        agent_disconnect_device(udid)
        set_connected_udid(None)
        return jsonify({"success": True})

    @app.route("/api/mobile/emulators", methods=["GET"])
    @login_required
    @api_error_handler
    def api_mobile_emulators():
        blocked = _require_mobile_enabled()
        if blocked:
            return blocked
        emulators = list_emulators()
        return jsonify({"success": True, "emulators": emulators, "count": len(emulators)})

    @app.route("/api/mobile/emulator/connect", methods=["POST"])
    @login_required
    @_roles("admin", "tester", "project_manager", "test_lead")
    @api_error_handler
    @log_api_request
    def api_mobile_emulator_connect():
        blocked = _require_mobile_enabled()
        if blocked:
            return blocked
        body = request.get_json(silent=True) or {}
        udid = (body.get("udid") or "").strip()
        if not udid:
            dev = pick_default_emulator()
            udid = (dev or {}).get("udid") or ""
        if not udid:
            return jsonify({"success": False, "error": "未发现已启动的模拟器"}), 503
        result = agent_connect_device(udid=udid)
        if not result.get("success"):
            return jsonify(result), 503
        set_connected_udid(result.get("udid") or udid)
        return jsonify(_connect_response(udid, result))

    @app.route("/api/mobile/wireless/connect", methods=["POST"])
    @login_required
    @_roles("admin", "tester", "project_manager", "test_lead")
    @api_error_handler
    @log_api_request
    def api_mobile_wireless_connect():
        """
        无线调试：可选 adb pair，再 adb connect，返回 udid 供一键投屏。
        body: host, port, pairing_code（port 为「使用配对码配对设备」弹窗中的端口）
        """
        blocked = _require_mobile_enabled()
        if blocked:
            return blocked
        body = request.get_json(silent=True) or {}
        host = (body.get("host") or body.get("ip") or "").strip()
        pairing_code = (body.get("pairing_code") or body.get("code") or "").strip()
        unified_port = (
            body.get("port")
            or body.get("connect_port")
            or body.get("debug_port")
            or body.get("pair_port")
        )

        if not host:
            return jsonify({"success": False, "error": "请填写手机 IP"}), 400
        if unified_port is None or str(unified_port).strip() == "":
            return jsonify({"success": False, "error": "请填写端口"}), 400
        if not pairing_code:
            return jsonify({"success": False, "error": "请填写 6 位配对码"}), 400
        if len(pairing_code) != 6 or not pairing_code.isdigit():
            return jsonify({"success": False, "error": "配对码须为 6 位数字"}), 400

        try:
            port_int = int(unified_port)
        except (TypeError, ValueError):
            return jsonify({"success": False, "error": "端口无效"}), 400

        ok, msg, udid, stage = wireless_pair_and_connect(host, port_int, pairing_code)
        if not ok:
            return jsonify({"success": False, "error": msg, "stage": stage}), 400

        devices = list_usb_devices()
        matched = next((d for d in devices if d.get("udid") == udid), None)
        if not matched:
            for d in devices:
                if (d.get("udid") or "").startswith(host + ":"):
                    matched = d
                    udid = d.get("udid") or udid
                    break
        if matched and matched.get("state") != "device":
            return jsonify({
                "success": False,
                "error": f"设备已连接但状态为 {matched.get('state')}，请在手机上允许无线调试",
                "udid": udid,
                "devices": devices,
            }), 400

        set_connected_udid(udid)
        agent_result = agent_connect_device(udid=udid)
        if not agent_result.get("success"):
            return jsonify({
                "success": True,
                "message": msg,
                "udid": udid,
                "devices": devices,
                "paired": bool(pairing_code),
                **agent_result,
            })
        return jsonify(_connect_response(
            udid,
            agent_result,
            extra={"message": msg, "devices": devices, "paired": bool(pairing_code)},
        ))

    @app.route("/api/mobile/tap-at", methods=["POST"])
    @login_required
    @_roles("admin", "tester", "project_manager", "test_lead")
    @api_error_handler
    def api_mobile_tap_at():
        blocked = _require_mobile_enabled()
        if blocked:
            return blocked
        body = request.get_json(silent=True) or {}
        try:
            x = int(body.get("x"))
            y = int(body.get("y"))
        except (TypeError, ValueError):
            return jsonify({"success": False, "error": "需要整数坐标 x, y"}), 400
        udid = _resolve_request_udid(body)
        if not udid:
            return jsonify({"success": False, "error": "请先连接设备"}), 400
        try:
            from mobile_agent_client import agent_replay_step

            result = agent_replay_step(
                udid,
                {
                    "action": "tap",
                    "selector_type": "viewport_coord",
                    "selector_value": json.dumps({"x": x, "y": y}),
                    "mobile_spec": {"viewport_coord": {"x": x, "y": y}},
                },
            )
            if not result.get("success"):
                return jsonify({"success": False, "error": result.get("error")}), 500
            return jsonify({"success": True, **(result.get("result") or {})})
        except Exception as exc:
            return jsonify({"success": False, "error": str(exc)}), 500

    @app.route("/api/mobile/swipe-at", methods=["POST"])
    @login_required
    @_roles("admin", "tester", "project_manager", "test_lead")
    @api_error_handler
    def api_mobile_swipe_at():
        blocked = _require_mobile_enabled()
        if blocked:
            return blocked
        body = request.get_json(silent=True) or {}
        try:
            x1, y1 = int(body.get("x1")), int(body.get("y1"))
            x2, y2 = int(body.get("x2")), int(body.get("y2"))
        except (TypeError, ValueError):
            return jsonify({"success": False, "error": "需要 x1,y1,x2,y2"}), 400
        udid = (body.get("udid") or "").strip()
        from mobile_device_manager import get_connected_udid

        if not udid:
            udid = get_connected_udid() or ""
        if not udid:
            return jsonify({"success": False, "error": "请先连接设备"}), 400
        try:
            result = adb_swipe(udid, x1, y1, x2, y2, int(body.get("duration_ms") or 300))
            return jsonify({"success": True, **result})
        except Exception as exc:
            return jsonify({"success": False, "error": str(exc)}), 500

    @app.route("/api/mobile/device-keys", methods=["POST"])
    @login_required
    @api_error_handler
    def api_mobile_device_keys():
        blocked = _require_mobile_enabled()
        if blocked:
            return blocked
        body = request.get_json(silent=True) or {}
        key = (body.get("key") or "").strip().lower()
        udid = (body.get("udid") or "").strip()
        from mobile_device_manager import get_connected_udid

        if not udid:
            udid = get_connected_udid() or ""
        if not udid:
            return jsonify({"success": False, "error": "请先连接设备"}), 400
        try:
            if key == "home":
                adb_press_home(udid)
            elif key == "back":
                adb_press_back(udid)
            else:
                return jsonify({"success": False, "error": "不支持按键"}), 400
            return jsonify({"success": True, "key": key})
        except Exception as exc:
            return jsonify({"success": False, "error": str(exc)}), 500

    @app.route("/api/mobile/arm", methods=["POST"])
    @login_required
    @_roles("admin", "tester", "project_manager", "test_lead")
    @api_error_handler
    def api_mobile_arm():
        """已移除：请在手机助手内录制。"""
        blocked = _require_mobile_enabled()
        if blocked:
            return blocked
        return _mobile_phone_only_response()

    @app.route("/api/mobile/disarm", methods=["POST"])
    @login_required
    @_roles("admin", "tester", "project_manager", "test_lead")
    @api_error_handler
    def api_mobile_disarm():
        blocked = _require_mobile_enabled()
        if blocked:
            return blocked
        return _mobile_phone_only_response()

    @app.route("/api/mobile/bridge/<action>", methods=["POST"])
    @login_required
    @_roles("admin", "tester", "project_manager", "test_lead")
    @api_error_handler
    def api_mobile_bridge_action(action: str):
        blocked = _require_mobile_enabled()
        if blocked:
            return blocked
        return jsonify({
            "success": False,
            "error": "bridge_daemon 已移除，请使用 Mobile Agent 插件 API",
            "deprecated": True,
        }), 410

    @app.route("/api/mobile/assistant/status", methods=["GET"])
    @login_required
    @_roles("admin", "tester", "project_manager", "test_lead")
    @api_error_handler
    def api_mobile_assistant_status():
        blocked = _require_mobile_enabled()
        if blocked:
            return blocked
        udid = (request.args.get("udid") or "").strip()
        from mobile_device_manager import get_connected_udid

        if not udid:
            udid = get_connected_udid() or ""
        st = agent_plugin_status(udid)
        st["agent_ws_url"] = mobile_agent_ws_url()
        st["udid"] = udid
        return jsonify(st)

    @app.route("/api/mobile/assistant/event", methods=["POST"])
    @login_required
    @_roles("admin", "tester", "project_manager", "test_lead")
    @api_error_handler
    @log_api_request
    def api_mobile_assistant_event():
        """助手 WebSocket 或前端轮询写入的事件 → 可选落库为步骤。"""
        blocked = _require_mobile_enabled()
        if blocked:
            return blocked
        body = request.get_json(silent=True) or {}
        event = body.get("event") if isinstance(body.get("event"), dict) else body
        if not isinstance(event, dict):
            return jsonify({"success": False, "error": "需要 event 对象"}), 400
        from mobile_assistant_events import normalize_assistant_event

        step_fields = normalize_assistant_event(event)
        case_id = body.get("case_id") or event.get("case_id")
        persist = body.get("persist", True)
        if not case_id:
            return jsonify({
                "success": True,
                "step_preview": step_fields,
                "persisted": False,
                "message": "已归一化事件（未指定 case_id，未写入）",
            })
        try:
            case_id = int(case_id)
        except (TypeError, ValueError):
            return jsonify({"success": False, "error": "case_id 无效"}), 400
        if not persist:
            return jsonify({"success": True, "step_preview": step_fields, "persisted": False})

        from database import Database
        import json as _json

        db = Database()
        case_row = db.get_test_case_v2(case_id)
        if not case_row:
            return jsonify({"success": False, "error": "用例不存在"}), 404
        pid = case_row.get("project_id")
        if pid and not db.check_project_access(current_user.id, int(pid), "editor"):
            return jsonify({"success": False, "error": "无权限修改此用例"}), 403
        ms = step_fields.get("mobile_spec") or {}
        if isinstance(ms, dict):
            ms["source"] = "assistant"
        step_id = db.create_test_step(
            case_id,
            step_fields.get("action") or "tap",
            step_fields.get("selector_type") or "",
            step_fields.get("selector_value") or "",
            input_value=step_fields.get("input_value") or "",
            description=step_fields.get("description") or "",
            automation_layer="android",
            mobile_spec=_json.dumps(ms, ensure_ascii=False) if ms else "",
        )
        return jsonify({
            "success": True,
            "step_id": step_id,
            "step": step_fields,
            "persisted": True,
        })

    @app.route("/api/mobile/assistant/install", methods=["POST"])
    @login_required
    @_roles("admin", "tester", "project_manager", "test_lead")
    @api_error_handler
    @log_api_request
    def api_mobile_assistant_install():
        blocked = _require_mobile_enabled()
        if blocked:
            return blocked
        body = request.get_json(silent=True) or {}
        udid = (body.get("udid") or "").strip()
        from mobile_device_manager import get_connected_udid

        if not udid:
            udid = get_connected_udid() or ""
        open_app = bool(body.get("open_app"))
        return jsonify(agent_install_plugin(udid, launch_app=open_app))

    @app.route("/api/mobile/appium/start", methods=["POST"])
    @login_required
    @_roles("admin", "tester", "project_manager", "test_lead")
    @api_error_handler
    @log_api_request
    def api_mobile_appium_start():
        return jsonify({
            "success": False,
            "error": "Appium 已退役，请使用 Mobile Agent + Recorder Plugin",
            "deprecated": True,
        }), 410

    @app.route("/api/mobile/assistant/events", methods=["GET"])
    @login_required
    @_roles("admin", "tester", "project_manager", "test_lead")
    @api_error_handler
    def api_mobile_assistant_events_poll():
        """已废弃：请使用 Agent WebSocket /internal/events。"""
        return jsonify({
            "success": True,
            "events": [],
            "count": 0,
            "deprecated": True,
            "agent_ws_url": mobile_agent_ws_url(),
        })

    @app.route("/api/mobile/record-step", methods=["POST"])
    @login_required
    @_roles("admin", "tester", "project_manager", "test_lead")
    @api_error_handler
    @log_api_request
    def api_mobile_record_step():
        """画布点选录制已移除。"""
        return jsonify({
            "success": False,
            "error": "画布点选录制已移除，请在手机上直接操作并使用「开始录制」",
            "deprecated": True,
        }), 410
        blocked = _require_mobile_enabled()
        if blocked:
            return blocked
        body = request.get_json(silent=True) or {}
        case_id = body.get("case_id")
        if not case_id:
            return jsonify({"success": False, "error": "缺少 case_id"}), 400
        try:
            case_id = int(case_id)
        except (TypeError, ValueError):
            return jsonify({"success": False, "error": "case_id 无效"}), 400

        kind = (body.get("kind") or body.get("pick_type") or "element").strip().lower()
        try:
            x = int(body.get("x"))
            y = int(body.get("y"))
        except (TypeError, ValueError):
            return jsonify({"success": False, "error": "需要坐标 x, y"}), 400

        udid = (body.get("udid") or "").strip()
        from mobile_device_manager import get_connected_udid

        if not udid:
            udid = get_connected_udid() or ""
        if not udid:
            return jsonify({"success": False, "error": "请先连接设备"}), 400

        from mobile_ui_probe import pick_at_point

        picked = pick_at_point(udid, x, y, half_size=int(body.get("template_half") or 40))
        suggestions = picked.get("suggestions") or []
        chosen = None
        for s in suggestions:
            if s.get("kind") == kind:
                chosen = s
                break
        if not chosen and suggestions:
            chosen = suggestions[0]
        if not chosen:
            return jsonify({"success": False, "error": "无法生成步骤建议"}), 500

        if body.get("also_tap"):
            try:
                get_mobile_executor().tap_at_coordinates(x, y, udid=udid)
            except Exception:
                pass

        from database import Database

        db = Database()
        case_row = db.get_test_case_v2(case_id)
        if not case_row:
            return jsonify({"success": False, "error": "用例不存在"}), 404
        pid = case_row.get("project_id")
        if pid and not db.check_project_access(current_user.id, int(pid), "editor"):
            return jsonify({"success": False, "error": "无权限修改此用例"}), 403

        action = chosen.get("action") or "tap"
        from desktop_automation import validate_step_for_layer

        layer_err = validate_step_for_layer(action, "android")
        if layer_err:
            return jsonify({"success": False, "error": layer_err}), 422

        import json as _json

        ms = chosen.get("mobile_spec") or {}
        step_id = db.create_test_step(
            case_id,
            action,
            chosen.get("selector_type") or chosen.get("strategy") or "",
            chosen.get("selector_value") or "",
            body.get("input_value") or "",
            body.get("description") or chosen.get("description") or "",
            None,
            "",
            "",
            "",
            "",
            False,
            "",
            "equals",
            "",
            1,
            "",
            automation_layer="android",
            desktop_spec="",
            mobile_spec=_json.dumps(ms, ensure_ascii=False) if ms else "",
            captcha_max_attempts=None,
        )
        return jsonify({
            "success": True,
            "step_id": step_id,
            "step": chosen,
            "picked": picked,
        })

    @app.route("/api/mobile/record-swipe-step", methods=["POST"])
    @login_required
    @_roles("admin", "tester", "project_manager", "test_lead")
    @api_error_handler
    @log_api_request
    def api_mobile_record_swipe_step():
        return jsonify({
            "success": False,
            "error": "画布滑动录制已移除，请在手机上直接滑动并使用「开始录制」",
            "deprecated": True,
        }), 410
        blocked = _require_mobile_enabled()
        if blocked:
            return blocked
        body = request.get_json(silent=True) or {}
        case_id = body.get("case_id")
        if not case_id:
            return jsonify({"success": False, "error": "缺少 case_id"}), 400
        try:
            case_id = int(case_id)
            x1, y1 = int(body.get("x1")), int(body.get("y1"))
            x2, y2 = int(body.get("x2")), int(body.get("y2"))
        except (TypeError, ValueError):
            return jsonify({"success": False, "error": "需要 case_id 与坐标 x1,y1,x2,y2"}), 400

        udid = (body.get("udid") or "").strip()
        from mobile_device_manager import get_connected_udid

        if not udid:
            udid = get_connected_udid() or ""
        if not udid:
            return jsonify({"success": False, "error": "请先连接设备"}), 400

        if body.get("also_swipe"):
            try:
                adb_swipe(udid, x1, y1, x2, y2, int(body.get("duration_ms") or 300))
            except Exception:
                pass

        from database import Database
        import json as _json

        db = Database()
        case_row = db.get_test_case_v2(case_id)
        if not case_row:
            return jsonify({"success": False, "error": "用例不存在"}), 404
        pid = case_row.get("project_id")
        if pid and not db.check_project_access(current_user.id, int(pid), "editor"):
            return jsonify({"success": False, "error": "无权限修改此用例"}), 403

        from desktop_automation import validate_step_for_layer

        layer_err = validate_step_for_layer("swipe", "android")
        if layer_err:
            return jsonify({"success": False, "error": layer_err}), 422

        duration_ms = int(body.get("duration_ms") or 300)
        mobile_spec = {"x1": x1, "y1": y1, "x2": x2, "y2": y2, "duration_ms": duration_ms}
        description = body.get("description") or f"滑动 ({x1},{y1})→({x2},{y2})"
        step_id = db.create_test_step(
            case_id,
            "swipe",
            "",
            "",
            "",
            description,
            None,
            "",
            "",
            "",
            "",
            False,
            "",
            "equals",
            "",
            1,
            "",
            automation_layer="android",
            desktop_spec="",
            mobile_spec=_json.dumps(mobile_spec, ensure_ascii=False),
            captcha_max_attempts=None,
        )
        return jsonify({
            "success": True,
            "step_id": step_id,
            "step": {
                "action": "swipe",
                "description": description,
                "mobile_spec": mobile_spec,
                "automation_layer": "android",
            },
        })

    @app.route("/api/mobile/replay-actions", methods=["POST"])
    @login_required
    @_roles("admin", "tester", "project_manager", "test_lead")
    @api_error_handler
    @log_api_request
    def api_mobile_replay_actions():
        """已移除：请在手机助手内回放。"""
        blocked = _require_mobile_enabled()
        if blocked:
            return blocked
        return _mobile_phone_only_response()

    @app.route("/api/mobile/cases/<int:case_id>/steps", methods=["GET"])
    @login_required
    @api_error_handler
    def api_mobile_case_steps(case_id: int):
        """移动端测试页加载用例全部步骤（不受通用分页 100 条限制）。"""
        from database import Database

        db = Database()
        case_row = db.get_test_case_v2(case_id)
        if not case_row:
            return jsonify({"success": False, "error": "用例不存在"}), 404
        pid = case_row.get("project_id")
        if pid and not db.check_project_access(current_user.id, int(pid), "viewer"):
            return jsonify({"success": False, "error": "无权限"}), 403
        limit = min(500, max(1, int(request.args.get("limit") or 500)))
        steps = db.get_case_steps(case_id, page=1, page_size=limit)
        return jsonify({"success": True, "steps": steps, "total": len(steps)})

    @app.route("/api/mobile/testing/bootstrap", methods=["GET"])
    @app.route("/api/mobile/studio/bootstrap", methods=["GET"])
    @login_required
    @api_error_handler
    def api_mobile_studio_bootstrap():
        """移动端测试页一次性加载：健康检查、设备、型号预设、驱动说明。"""
        blocked = _require_mobile_enabled()
        if blocked:
            return blocked
        health = check_mobile_health()
        devices = health.get("devices") or []
        real_devices = list_devices_for_ui("real")
        default_dev = pick_default_device()
        from mobile_device_profiles import list_frame_presets
        from mobile_assistant_bundles import assistant_installed_on_device

        udid_conn = health.get("connected_udid") or ""
        ast = agent_plugin_status(udid_conn) if udid_conn else {}

        return jsonify({
            "success": True,
            **public_config(),
            "health": health,
            "devices": devices,
            "real_devices": real_devices,
            "emulators": list_emulators(),
            "device_warnings": collect_device_warnings(real_devices),
            "default_device": default_dev,
            "frame_presets": list_frame_presets(),
            "auto_connect_default": auto_connect_on_studio(),
            "driver_mode": mobile_driver_mode(),
            "requires_appium": False,
            "assistant_installed": assistant_installed_on_device(udid_conn) if udid_conn else False,
            "assistant_connected": bool(ast.get("plugin_ready")),
            "agent_ws_url": mobile_agent_ws_url(),
            "agent_enabled": mobile_agent_enabled(),
        })

    @app.route("/api/mobile/auto-connect", methods=["POST"])
    @login_required
    @_roles("admin", "tester", "project_manager", "test_lead")
    @api_error_handler
    @log_api_request
    def api_mobile_auto_connect():
        blocked = _require_mobile_enabled()
        if blocked:
            return blocked
        body = request.get_json(silent=True) or {}
        udid = (body.get("udid") or "").strip()
        dev = None
        if udid:
            for d in list_usb_devices():
                if d.get("udid") == udid:
                    dev = d
                    break
        if not dev:
            dev = pick_default_device()
        if not dev or dev.get("state") != "device":
            return jsonify({
                "success": False,
                "error": format_connect_error(dev),
                "device_state": (dev or {}).get("state") or "",
                "udid": (dev or {}).get("udid") or udid,
            }), 422
        udid = dev.get("udid") or ""
        result = agent_connect_device(udid=udid)
        if not result.get("success"):
            return jsonify(result), 503
        set_connected_udid(result.get("udid") or udid)
        return jsonify(_connect_response(udid, result))

    @app.route("/api/mobile/diagnostics", methods=["GET"])
    @login_required
    @api_error_handler
    def api_mobile_diagnostics():
        """检测 adb、Appium 等移动端环境。"""
        blocked = _require_mobile_enabled()
        if blocked:
            return blocked
        from mobile_env_config import (
            adb_path,
            adb_path_source,
            mobile_runtime_available,
            mobile_runtime_unavailable_reason,
            requires_appium_for_execution,
        )

        health = check_mobile_health()
        checks = []
        adb_ok = bool(health.get("adb_ok"))
        checks.append({
            "id": "adb",
            "label": "ADB",
            "ok": adb_ok,
            "detail": health.get("adb_message") or adb_path(),
            "optional": False,
        })
        agent_ok = mobile_agent_enabled()
        checks.append({
            "id": "mobile_agent",
            "label": "Mobile Agent Gateway",
            "ok": agent_ok,
            "detail": "TestoryMobileGw 已配置" if agent_ok else "未启动或未配置 MOBILE_AGENT_GATEWAY_URL",
            "optional": False,
        })
        runtime_ok = mobile_runtime_available()
        checks.append({
            "id": "mobile_runtime",
            "label": "移动端执行运行时",
            "ok": runtime_ok,
            "detail": mobile_runtime_unavailable_reason() or "就绪",
            "optional": False,
        })
        blocking = None
        if not adb_ok:
            blocking = health.get("adb_message") or "ADB 不可用，请配置 ADB_PATH 或安装 Platform-Tools"
        ready = adb_ok
        return jsonify({
            "success": True,
            "ready": ready,
            "blocking_reason": blocking,
            "checks": checks,
            "adb_path": adb_path(),
            "adb_path_source": adb_path_source(),
            "health": health,
        })

    @app.route("/api/element-repository", methods=["GET"])
    @login_required
    @api_error_handler
    def api_element_repository_list():
        from database import Database

        project_id = request.args.get("project_id")
        if not project_id:
            return jsonify({"success": False, "error": "缺少 project_id"}), 400
        try:
            project_id = int(project_id)
        except (TypeError, ValueError):
            return jsonify({"success": False, "error": "project_id 无效"}), 400
        db = Database()
        if not db.check_project_access(current_user.id, project_id, "viewer"):
            return jsonify({"success": False, "error": "无权限"}), 403
        platform = (request.args.get("platform") or "").strip()
        elements = db.list_element_repository(project_id, platform=platform)
        return jsonify({"success": True, "elements": elements})

    @app.route("/api/element-repository", methods=["POST"])
    @login_required
    @_roles("admin", "tester", "project_manager", "test_lead")
    @api_error_handler
    @log_api_request
    def api_element_repository_create():
        from database import Database

        body = request.get_json(silent=True) or {}
        project_id = body.get("project_id")
        alias = (body.get("alias") or "").strip()
        if not project_id or not alias:
            return jsonify({"success": False, "error": "缺少 project_id 或 alias"}), 400
        try:
            project_id = int(project_id)
        except (TypeError, ValueError):
            return jsonify({"success": False, "error": "project_id 无效"}), 400
        db = Database()
        if not db.check_project_access(current_user.id, project_id, "editor"):
            return jsonify({"success": False, "error": "无权限"}), 403
        try:
            eid = db.create_element_repository_entry(
                project_id,
                alias,
                body.get("platform") or "android",
                body.get("selector_type") or "accessibility_id",
                body.get("selector_value") or "",
                body.get("attributes") if isinstance(body.get("attributes"), dict) else {},
            )
        except Exception as exc:
            return jsonify({"success": False, "error": str(exc)}), 400
        return jsonify({"success": True, "id": eid})

    @app.route("/api/element-repository/<int:element_id>", methods=["PUT"])
    @login_required
    @_roles("admin", "tester", "project_manager", "test_lead")
    @api_error_handler
    @log_api_request
    def api_element_repository_update(element_id: int):
        from database import Database

        body = request.get_json(silent=True) or {}
        db = Database()
        try:
            ok = db.update_element_repository_entry(
                element_id,
                alias=body.get("alias"),
                selector_type=body.get("selector_type"),
                selector_value=body.get("selector_value"),
                attributes=body.get("attributes") if isinstance(body.get("attributes"), dict) else None,
            )
        except Exception as exc:
            return jsonify({"success": False, "error": str(exc)}), 400
        if not ok:
            return jsonify({"success": False, "error": "更新失败或元素不存在"}), 404
        return jsonify({"success": True})

    @app.route("/api/mobile/apps", methods=["GET"])
    @login_required
    @api_error_handler
    def api_mobile_apps():
        blocked = _require_mobile_enabled()
        if blocked:
            return blocked
        udid = (request.args.get("udid") or "").strip()
        from mobile_device_manager import get_connected_udid

        if not udid:
            udid = get_connected_udid() or ""
        apps = list_user_apps(udid, limit=int(request.args.get("limit") or 80))
        fg = get_foreground_app(udid) if udid else None
        return jsonify({"success": True, "apps": apps, "foreground": fg})

    @app.route("/api/mobile/run", methods=["POST"])
    @login_required
    @api_error_handler
    @log_api_request
    def api_mobile_run():
        blocked = _require_mobile_enabled()
        if blocked:
            return blocked
        return _mobile_phone_only_response()

    @app.route("/api/mobile/check-env", methods=["GET"])
    @login_required
    @api_error_handler
    def api_mobile_check_env():
        health = check_mobile_health()
        devices = health.get("devices") or []
        return jsonify({
            "success": True,
            "ready": bool(health.get("adb_ok")),
            "adb_version": health.get("adb_version") or "已安装",
            "device_count": len(devices),
            "reason": health.get("adb_message") if not health.get("adb_ok") else "",
        })

    @app.route("/api/mobile/env-config", methods=["GET"])
    @login_required
    @api_error_handler
    def api_mobile_env_config():
        health = check_mobile_health()
        return jsonify({
            "success": True,
            **public_config(),
            "health": health,
            "auto_connect_default": auto_connect_on_studio(),
            "driver_mode": mobile_driver_mode(),
            "agent_enabled": mobile_agent_enabled(),
        })

    @app.route("/api/mobile/device/pair", methods=["POST"])
    @login_required
    @api_error_handler
    def api_mobile_device_pair():
        # 原缺陷：随机码未写入 _PAIR_CODES，设备 confirm 恒失败。
        from database import Database
        from flask_login import current_user
        from mobile_sync_store import pair_code_payload

        db = Database()
        tid = db.get_user_tenant_id(current_user.id)
        return jsonify(pair_code_payload(current_user.id, tid))

    @app.route("/api/mobile/recording/start", methods=["POST"])
    @login_required
    @_roles("admin", "tester", "project_manager", "test_lead")
    @api_error_handler
    def api_mobile_recording_start():
        blocked = _require_mobile_enabled()
        if blocked:
            return blocked
        return _mobile_phone_only_response()

    @app.route("/api/mobile/recording/stop", methods=["POST"])
    @login_required
    @_roles("admin", "tester", "project_manager", "test_lead")
    @api_error_handler
    def api_mobile_recording_stop():
        blocked = _require_mobile_enabled()
        if blocked:
            return blocked
        return _mobile_phone_only_response()

    @app.route("/api/mobile/recording/pause", methods=["POST"])
    @login_required
    @_roles("admin", "tester", "project_manager", "test_lead")
    @api_error_handler
    def api_mobile_recording_pause():
        blocked = _require_mobile_enabled()
        if blocked:
            return blocked
        return _mobile_phone_only_response()

    @app.route("/api/mobile/recording/resume", methods=["POST"])
    @login_required
    @_roles("admin", "tester", "project_manager", "test_lead")
    @api_error_handler
    def api_mobile_recording_resume():
        blocked = _require_mobile_enabled()
        if blocked:
            return blocked
        return _mobile_phone_only_response()

    @app.route("/api/mobile/recording/steps", methods=["GET"])
    @login_required
    @api_error_handler
    def api_mobile_recording_steps():
        return _mobile_phone_only_response()

    @app.route("/api/mobile/recording/clear", methods=["POST"])
    @login_required
    @_roles("admin", "tester", "project_manager", "test_lead")
    @api_error_handler
    def api_mobile_recording_clear():
        blocked = _require_mobile_enabled()
        if blocked:
            return blocked
        return _mobile_phone_only_response()

    @app.route("/api/mobile/recording/save-steps", methods=["POST"])
    @login_required
    @_roles("admin", "tester", "project_manager", "test_lead")
    @api_error_handler
    def api_mobile_recording_save_steps():
        return _mobile_phone_only_response()

    @app.route("/api/mobile/recording/steps/update", methods=["POST"])
    @login_required
    @_roles("admin", "tester", "project_manager", "test_lead")
    @api_error_handler
    def api_mobile_recording_steps_update():
        return _mobile_phone_only_response()

    @app.route("/api/mobile/recording/steps/delete", methods=["POST"])
    @login_required
    @_roles("admin", "tester", "project_manager", "test_lead")
    @api_error_handler
    def api_mobile_recording_steps_delete():
        return _mobile_phone_only_response()

    # Register mobile-to-PC sync routes (pairing, cases, run jobs)
    try:
        from mobile_sync_store import register_sync_routes
        register_sync_routes(
            app,
            api_error_handler=api_error_handler,
            login_required=login_required,
            role_required=role_required,
        )
    except ImportError:
        pass  # sync module may not be available
