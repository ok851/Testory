# -*- coding: utf-8 -*-
"""Android 移动端 Flask 路由与用例执行。"""

from __future__ import annotations

import base64
import json
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from flask import jsonify, request
from flask_login import current_user, login_required

from mobile_device_manager import (
    adb_connect_wireless,
    adb_disconnect_device,
    adb_pair_wireless,
    capture_screenshot_frame,
    check_mobile_health,
    get_device_info,
    get_foreground_app,
    list_user_apps,
    list_usb_devices,
    pick_default_device,
    set_connected_udid,
)
from mobile_device_profiles import get_frame_preset, list_frame_presets
from mobile_adb_control import adb_press_back, adb_press_home, adb_swipe, smart_tap
from mobile_env_config import auto_connect_on_studio, mobile_driver_mode
from mobile_env_config import (
    mobile_enabled,
    mobile_runtime_available,
    public_config,
    resolve_mirror_backend,
    save_mobile_defaults,
    scrcpy_bridge_url,
)
from mobile_emulator_manager import (
    emulator_diagnostics,
    emulator_status,
    list_emulator_models,
    list_running_emulators,
    provision_avd_for_preset,
    start_avd,
    stop_avd,
)
from mobile_executor import get_mobile_executor
from mobile_mirror import disconnect_all_mirrors, get_mirror_session, start_scrcpy_mirror, stop_mirror

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
    """
    执行纯 Android 用例，写入 run_history 与 step_results。

    Returns:
        (flask_response, http_status)
    """
    if not mobile_runtime_available():
        from mobile_env_config import mobile_runtime_unavailable_reason

        reason = mobile_runtime_unavailable_reason() or "移动端不可用"
        run_id = db.create_run_history(case_id, "error", 0, reason, "", "")
        return jsonify({"success": False, "status": "error", "error": reason, "run_id": run_id}), 400

    ok, appium_msg = get_mobile_executor().check_appium_server()
    if not ok:
        run_id = db.create_run_history(case_id, "error", 0, appium_msg, "", "")
        return jsonify({"success": False, "status": "error", "error": appium_msg, "run_id": run_id}), 503

    executor = get_mobile_executor()
    caps = dict(capabilities or {})
    if udid:
        caps["udid"] = udid
    try:
        if not executor.is_connected:
            executor.connect(caps or None)
    except Exception as exc:
        run_id = db.create_run_history(case_id, "error", 0, str(exc), "", "")
        return jsonify({"success": False, "status": "error", "error": str(exc), "run_id": run_id}), 503

    step_results_list: List[Dict[str, Any]] = []
    screenshots: List[str] = []
    extracted_text = ""
    expected_text = case.get("expected_result") or ""

    try:
        from execution_factory import get_executor_factory

        factory = get_executor_factory()
        total = len(steps)
        for step_index, step in enumerate(steps, start=1):
            if job_cancelled and job_cancelled():
                raise RuntimeError("用户已停止执行")

            action = step.get("action", "")
            selector_value = db.resolve_variables(
                step.get("selector_value", ""),
                project_id=case.get("project_id"),
                case_id=case_id,
            )
            input_value = db.resolve_variables(
                step.get("input_value", ""),
                project_id=case.get("project_id"),
                case_id=case_id,
            )
            description = step.get("description", "")
            step_start = time.time()

            if job_update:
                job_update(
                    current_step_order=step_index,
                    current_action=action,
                    message=f"正在执行 Android 步骤 {step_index}/{total}: {action}",
                )

            exec_step = dict(step)
            exec_step["selector_value"] = selector_value
            exec_step["input_value"] = input_value
            try:
                result = factory.execute_mobile_step(
                    exec_step,
                    selector_value=selector_value,
                    input_value=input_value,
                )
                step_status = "success"
                step_error = ""
                step_screenshot = (result or {}).get("screenshot") or ""
            except Exception as exc:
                step_status = "error"
                step_error = str(exc)
                step_screenshot = ""
                step_results_list.append({
                    "step_id": step.get("id"),
                    "step_order": step.get("step_order", 0),
                    "action": action,
                    "selector_value": selector_value,
                    "input_value": input_value,
                    "description": description,
                    "status": step_status,
                    "error": step_error,
                    "screenshot": step_screenshot,
                    "duration": round(time.time() - step_start, 3),
                    "automation_layer": "android",
                })
                if step_screenshot:
                    screenshots.append(step_screenshot)
                raise

            step_duration = round(time.time() - step_start, 3)
            step_results_list.append({
                "step_id": step.get("id"),
                "step_order": step.get("step_order", 0),
                "action": action,
                "selector_value": selector_value,
                "input_value": input_value,
                "description": description,
                "status": step_status,
                "error": step_error,
                "screenshot": step_screenshot,
                "duration": step_duration,
                "automation_layer": "android",
            })
            if step_screenshot:
                screenshots.append(step_screenshot)
            if job_update:
                job_update(
                    completed_steps=len(step_results_list),
                    message=f"已完成 {len(step_results_list)}/{total} 步",
                )

        duration = round(time.time() - start_time, 2)
        run_id = db.create_run_history(case_id, "success", duration, "", extracted_text, expected_text)
        try:
            conn = __import__("sqlite3").connect(db.db_path)
            conn.execute(
                "UPDATE run_history SET screenshots = ? WHERE id = ?",
                (json.dumps(screenshots), run_id),
            )
            conn.commit()
            conn.close()
        except Exception:
            pass
        for sr in step_results_list:
            db.create_step_result(
                run_id,
                sr["step_id"],
                sr["step_order"],
                sr["action"],
                sr["selector_value"],
                sr["input_value"],
                sr["description"],
                sr["status"],
                sr["error"],
                sr["screenshot"],
                sr["duration"],
            )
        return jsonify({
            "success": True,
            "status": "success",
            "duration": duration,
            "run_id": run_id,
            "step_results": step_results_list,
        }), 200

    except Exception as exc:
        duration = round(time.time() - start_time, 2)
        error_msg = str(exc)
        status = "stopped" if "用户已停止" in error_msg else "error"
        try:
            run_id = db.create_run_history(case_id, status, duration, error_msg, extracted_text, expected_text)
            for sr in step_results_list:
                db.create_step_result(
                    run_id,
                    sr["step_id"],
                    sr["step_order"],
                    sr["action"],
                    sr["selector_value"],
                    sr["input_value"],
                    sr["description"],
                    sr["status"],
                    sr["error"],
                    sr["screenshot"],
                    sr["duration"],
                )
        except Exception as hist_exc:
            uat_logger.error("保存 Android 运行历史失败: %s", hist_exc)
            run_id = None
        return jsonify({
            "success": False,
            "status": status,
            "duration": duration,
            "error": error_msg,
            "run_id": run_id,
        }), 200
    finally:
        try:
            executor.disconnect()
        except Exception:
            pass


def _mirror_payload(udid: str, session_id: str) -> Dict[str, Any]:
    backend = resolve_mirror_backend(udid)
    payload: Dict[str, Any] = {
        "mirror_backend": backend,
        "mirror_frame_url": f"/api/mobile/mirror/frame?session_id={session_id}&udid={udid}",
    }
    if backend == "scrcpy_ws":
        from mobile_scrcpy_bridge import bridge_health, ensure_bridge_started

        ensure_bridge_started()
        health = bridge_health()
        if not health.get("scrcpy_server_ready"):
            payload["mirror_backend"] = "screencap"
            payload["mirror_fallback_reason"] = "未找到 scrcpy-server，已降级为截图投屏"
            return payload
        payload["mirror_ws_url"] = f"{scrcpy_bridge_url()}/?serial={udid}"
        payload["bridge"] = health
    return payload


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
            "scrcpy_path": body.get("scrcpy_path"),
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
        if not devices:
            health = check_mobile_health()
            if not health.get("adb_ok"):
                return jsonify({"success": False, "error": health.get("adb_message"), "devices": []}), 503
        return jsonify({"success": True, "devices": devices})

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
        caps = body.get("capabilities") if isinstance(body.get("capabilities"), dict) else {}
        if udid:
            caps["udid"] = udid
        executor = get_mobile_executor()
        ok, msg = executor.check_appium_server()
        if not ok:
            return jsonify({"success": False, "error": msg}), 503
        try:
            executor.connect(caps or None)
        except Exception as exc:
            return jsonify({"success": False, "error": str(exc)}), 503
        resolved_udid = udid or executor.connected_udid or ""
        set_connected_udid(resolved_udid)
        mirror = start_scrcpy_mirror(resolved_udid)
        device_info = get_device_info(resolved_udid)
        fg = get_foreground_app(resolved_udid) or {}
        suggested_pkg = (
            (caps.get("appPackage") or "").strip()
            or device_info.get("foreground_package")
            or ""
        )
        return jsonify({
            "success": True,
            "udid": resolved_udid,
            "session_id": mirror.get("session_id"),
            **_mirror_payload(resolved_udid, mirror.get("session_id") or ""),
            "scrcpy_started": mirror.get("scrcpy_started"),
            "device": device_info,
            "suggested_app_package": suggested_pkg,
            "foreground": fg,
            "appium_connected": executor.is_connected,
            "is_emulator": resolved_udid.startswith("emulator-"),
        })

    @app.route("/api/mobile/disconnect", methods=["POST"])
    @login_required
    @api_error_handler
    def api_mobile_disconnect():
        body = request.get_json(silent=True) or {}
        session_id = (body.get("session_id") or "").strip()
        udid = (body.get("udid") or "").strip()
        from mobile_device_manager import get_connected_udid

        if not udid:
            udid = get_connected_udid() or ""
        if session_id:
            stop_mirror(session_id)
        disconnect_all_mirrors()
        get_mobile_executor().disconnect()
        if udid and ":" in udid:
            adb_disconnect_device(udid)
        set_connected_udid(None)
        return jsonify({"success": True})

    @app.route("/api/mobile/mirror/frame", methods=["GET"])
    @login_required
    @api_error_handler
    def api_mobile_mirror_frame():
        session_id = (request.args.get("session_id") or "").strip()
        sess = get_mirror_session(session_id) if session_id else None
        udid = (request.args.get("udid") or "").strip() or (sess or {}).get("udid") or ""
        from mobile_device_manager import get_connected_udid

        if not udid:
            udid = get_connected_udid() or ""
        frame, fmt = capture_screenshot_frame(udid)
        if not frame:
            return jsonify({"success": False, "error": "无法获取设备截图"}), 503
        b64 = base64.b64encode(frame).decode("ascii")
        return jsonify({"success": True, "format": fmt, "data": b64})

    @app.route("/api/mobile/wireless/connect", methods=["POST"])
    @login_required
    @_roles("admin", "tester", "project_manager", "test_lead")
    @api_error_handler
    @log_api_request
    def api_mobile_wireless_connect():
        """
        无线调试：可选 adb pair，再 adb connect，返回 udid 供一键投屏。
        body: host, pair_port, pairing_code, connect_port
        """
        blocked = _require_mobile_enabled()
        if blocked:
            return blocked
        body = request.get_json(silent=True) or {}
        host = (body.get("host") or body.get("ip") or "").strip()
        pairing_code = (body.get("pairing_code") or body.get("code") or "").strip()
        pair_port = body.get("pair_port")
        connect_port = body.get("connect_port") or body.get("debug_port")

        if pairing_code:
            if pair_port is None or str(pair_port).strip() == "":
                return jsonify({"success": False, "error": "填写配对码时需同时提供配对端口"}), 400
            ok, msg = adb_pair_wireless(host, int(pair_port), pairing_code)
            if not ok:
                return jsonify({"success": False, "error": msg, "stage": "pair"}), 400

        if connect_port is None or str(connect_port).strip() == "":
            return jsonify({
                "success": False,
                "error": "需要调试端口（无线调试页「IP 地址和端口」中的端口，非配对端口）",
                "stage": "connect",
            }), 400

        ok, msg, udid = adb_connect_wireless(host, int(connect_port))
        if not ok:
            return jsonify({"success": False, "error": msg, "stage": "connect"}), 400

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

        return jsonify({
            "success": True,
            "message": msg,
            "udid": udid,
            "devices": devices,
            "paired": bool(pairing_code),
        })

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
        udid = (body.get("udid") or "").strip()
        from mobile_device_manager import get_connected_udid

        if not udid:
            udid = get_connected_udid() or ""
        if not udid:
            return jsonify({"success": False, "error": "请先连接设备"}), 400
        try:
            executor = get_mobile_executor()
            result = executor.tap_at_coordinates(x, y)
            return jsonify({"success": True, **result})
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

    @app.route("/api/mobile/pick-at", methods=["POST"])
    @login_required
    @_roles("admin", "tester", "project_manager", "test_lead")
    @api_error_handler
    def api_mobile_pick_at():
        """点屏拾取：返回元素定位、坐标与 Airtest 风格图像模板建议。"""
        blocked = _require_mobile_enabled()
        if blocked:
            return blocked
        body = request.get_json(silent=True) or {}
        try:
            x = int(body.get("x"))
            y = int(body.get("y"))
        except (TypeError, ValueError):
            return jsonify({"success": False, "error": "需要整数坐标 x, y"}), 400
        udid = (body.get("udid") or "").strip()
        from mobile_device_manager import get_connected_udid

        if not udid:
            udid = get_connected_udid() or ""
        if not udid:
            return jsonify({"success": False, "error": "请先连接设备"}), 400
        half = int(body.get("template_half") or 40)
        from mobile_ui_probe import pick_at_point

        result = pick_at_point(udid, x, y, half_size=half)
        return jsonify({"success": True, **result})

    @app.route("/api/mobile/record-step", methods=["POST"])
    @login_required
    @_roles("admin", "tester", "project_manager", "test_lead")
    @api_error_handler
    @log_api_request
    def api_mobile_record_step():
        """将点屏拾取结果写入当前用例步骤。"""
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
                get_mobile_executor().tap_at_coordinates(x, y)
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
        default_dev = pick_default_device()
        from mobile_scrcpy_bridge import bridge_health, ensure_bridge_started

        if mobile_enabled():
            ensure_bridge_started()
        return jsonify({
            "success": True,
            **public_config(),
            "health": health,
            "devices": devices,
            "default_device": default_dev,
            "frame_presets": list_frame_presets(),
            "auto_connect_default": auto_connect_on_studio(),
            "driver_mode": mobile_driver_mode(),
            "emulator": emulator_status(),
            "bridge": bridge_health(),
        })

    @app.route("/api/mobile/auto-connect", methods=["POST"])
    @login_required
    @_roles("admin", "tester", "project_manager", "test_lead")
    @api_error_handler
    @log_api_request
    def api_mobile_auto_connect():
        """
        一键连接：自动选择首台已授权设备，启动投屏，可选尝试 Appium。
        """
        blocked = _require_mobile_enabled()
        if blocked:
            return blocked
        body = request.get_json(silent=True) or {}
        udid = (body.get("udid") or "").strip()
        try_appium = body.get("try_appium")
        if try_appium is None:
            try_appium = mobile_driver_mode() in ("auto", "appium")

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
                "error": "未发现已授权设备。请 USB/无线连接后在手机上允许调试，或先在下方完成无线配对。",
            }), 503

        udid = dev.get("udid") or ""
        set_connected_udid(udid)
        mirror = start_scrcpy_mirror(udid)
        device_info = get_device_info(udid)
        appium_ok = False
        appium_error = ""
        if try_appium and mobile_runtime_available():
            executor = get_mobile_executor()
            ok, msg = executor.check_appium_server()
            if ok:
                caps: Dict[str, Any] = {}
                pkg = (
                    (body.get("app_package") or "").strip()
                    or device_info.get("foreground_package")
                    or ""
                )
                if pkg:
                    caps["appPackage"] = pkg
                caps["udid"] = udid
                try:
                    executor.connect(caps)
                    appium_ok = True
                except Exception as exc:
                    appium_error = str(exc)
            else:
                appium_error = msg

        apps = list_user_apps(udid, limit=60)
        return jsonify({
            "success": True,
            "udid": udid,
            "device": device_info,
            "session_id": mirror.get("session_id"),
            **_mirror_payload(udid, mirror.get("session_id") or ""),
            "scrcpy_started": mirror.get("scrcpy_started"),
            "appium_connected": appium_ok,
            "appium_error": appium_error or None,
            "apps": apps,
            "suggested_app_package": device_info.get("foreground_package") or "",
            "frame_preset": get_frame_preset(body.get("frame_preset") or "generic_19_9"),
            "is_emulator": udid.startswith("emulator-"),
        })

    @app.route("/api/mobile/emulator/status", methods=["GET"])
    @login_required
    @api_error_handler
    def api_mobile_emulator_status():
        blocked = _require_mobile_enabled()
        if blocked:
            return blocked
        from mobile_scrcpy_bridge import bridge_health

        return jsonify({"success": True, **emulator_status(), "bridge": bridge_health()})

    @app.route("/api/mobile/emulator/diagnostics", methods=["GET"])
    @login_required
    @api_error_handler
    def api_mobile_emulator_diagnostics():
        blocked = _require_mobile_enabled()
        if blocked:
            return blocked
        diag = emulator_diagnostics()
        return jsonify({"success": True, **diag})

    @app.route("/api/mobile/emulator/repair", methods=["POST"])
    @login_required
    @_roles("admin", "tester", "project_manager", "test_lead")
    @api_error_handler
    @log_api_request
    def api_mobile_emulator_repair():
        blocked = _require_mobile_enabled()
        if blocked:
            return blocked
        body = request.get_json(silent=True) or {}
        preset_id = (body.get("preset_id") or "").strip()
        if preset_id:
            ok, avd_name, msg = provision_avd_for_preset(preset_id)
            return jsonify({
                "success": ok,
                "message": msg,
                "avd_name": avd_name,
                "emulator": emulator_status(),
            }), (200 if ok else 503)
        from mobile_emulator_sdk_bundles import ensure_emulator_sdk_ready

        result = ensure_emulator_sdk_ready()
        ok = bool(result.get("success"))
        result["emulator"] = emulator_status()
        return jsonify(result), (200 if ok else 503)

    @app.route("/api/mobile/emulator/models", methods=["GET"])
    @login_required
    @api_error_handler
    def api_mobile_emulator_models():
        blocked = _require_mobile_enabled()
        if blocked:
            return blocked
        return jsonify({"success": True, "models": list_emulator_models()})

    @app.route("/api/mobile/emulator/switch-model", methods=["POST"])
    @login_required
    @_roles("admin", "tester", "project_manager", "test_lead")
    @api_error_handler
    @log_api_request
    def api_mobile_emulator_switch_model():
        blocked = _require_mobile_enabled()
        if blocked:
            return blocked
        body = request.get_json(silent=True) or {}
        preset_id = (body.get("preset_id") or body.get("model_id") or "").strip()
        if not preset_id:
            return jsonify({"success": False, "error": "需要 preset_id"}), 400
        try:
            port = int(body.get("port") or 5554)
        except (TypeError, ValueError):
            port = 5554
        gpu = (body.get("gpu") or "swiftshader_indirect").strip()
        if "no_window" in body:
            no_window = bool(body.get("no_window"))
        else:
            no_window = True
        async_start = body.get("async")
        if async_start is None:
            async_start = True
        if async_start:
            from emulator_start_jobs import start_switch_model_job

            job_id = start_switch_model_job(
                preset_id,
                port=port,
                gpu=gpu,
                no_window=no_window,
            )
            return jsonify({"success": True, "async": True, "job_id": job_id, "preset_id": preset_id})
        from mobile_emulator_manager import switch_emulator_model

        ok, msg, meta = switch_emulator_model(
            preset_id,
            port=port,
            gpu=gpu,
            no_window=no_window,
        )
        if not ok:
            return jsonify({"success": False, "error": msg, **(meta or {})}), 503
        return jsonify({"success": True, "message": msg, **meta, "devices": list_usb_devices()})

    @app.route("/api/mobile/emulator/start", methods=["POST"])
    @login_required
    @_roles("admin", "tester", "project_manager", "test_lead")
    @api_error_handler
    @log_api_request
    def api_mobile_emulator_start():
        blocked = _require_mobile_enabled()
        if blocked:
            return blocked
        body = request.get_json(silent=True) or {}
        avd_name = (body.get("avd_name") or body.get("name") or "").strip()
        preset_id = (body.get("preset_id") or body.get("model_id") or "").strip()
        if not avd_name and preset_id:
            from mobile_emulator_manager import get_preset_by_id

            preset = get_preset_by_id(preset_id)
            if preset:
                avd_name = (preset.get("avd_name_hint") or "").strip()
        if not avd_name:
            return jsonify({"success": False, "error": "需要 avd_name"}), 400
        try:
            port = int(body.get("port") or 5554)
        except (TypeError, ValueError):
            port = 5554
        gpu = (body.get("gpu") or "host").strip()
        if "no_window" in body:
            no_window = bool(body.get("no_window"))
        else:
            no_window = True
        async_start = body.get("async")
        if async_start is None:
            async_start = True
        if async_start:
            from emulator_start_jobs import start_emulator_job

            job_id = start_emulator_job(
                avd_name,
                port=port,
                gpu=gpu,
                no_window=no_window,
            )
            return jsonify({"success": True, "async": True, "job_id": job_id})
        ok, msg, meta = start_avd(avd_name, port=port, gpu=gpu, no_window=no_window)
        if not ok:
            return jsonify({"success": False, "error": msg}), 503
        return jsonify({"success": True, "message": msg, **meta, "devices": list_usb_devices()})

    @app.route("/api/mobile/emulator/start/job/<job_id>", methods=["GET"])
    @login_required
    @api_error_handler
    def api_mobile_emulator_start_job(job_id: str):
        blocked = _require_mobile_enabled()
        if blocked:
            return blocked
        from emulator_start_jobs import get_job

        job = get_job(job_id)
        if not job:
            return jsonify({"success": False, "ok": False, "error": "启动任务不存在或已过期"}), 404
        return jsonify({"success": True, "ok": True, "job": job})

    @app.route("/api/mobile/emulator/stop", methods=["POST"])
    @login_required
    @_roles("admin", "tester", "project_manager", "test_lead")
    @api_error_handler
    @log_api_request
    def api_mobile_emulator_stop():
        blocked = _require_mobile_enabled()
        if blocked:
            return blocked
        body = request.get_json(silent=True) or {}
        serial = (body.get("serial") or body.get("udid") or "").strip()
        avd_name = (body.get("avd_name") or body.get("name") or "").strip()
        ok, msg = stop_avd(serial=serial, avd_name=avd_name)
        return jsonify({"success": ok, "message": msg, "devices": list_usb_devices()})

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
        from database import Database

        body = request.get_json(silent=True) or {}
        case_id = body.get("case_id")
        if not case_id:
            return jsonify({"success": False, "error": "缺少 case_id"}), 400
        try:
            case_id = int(case_id)
        except (TypeError, ValueError):
            return jsonify({"success": False, "error": "case_id 无效"}), 400

        db = Database()
        from app import load_case_and_steps, _case_run_cancelled, _case_run_lock, _case_run_jobs, _case_job_update

        case, steps = load_case_and_steps(case_id, db)
        if not case:
            return jsonify({"success": False, "error": "测试用例不存在"}), 404
        if not steps:
            return jsonify({"success": False, "error": "该用例没有步骤"}), 400

        user_id = current_user.id
        start_time = time.time()

        machine_lock_acquired = False
        try:
            from execution_lock import ExecutionLockError, acquire as acquire_machine_lock, release as release_machine_lock

            machine_lock_acquired = acquire_machine_lock(
                owner=f"mobile_run:{case_id}:user:{user_id}", timeout_sec=120
            )
            if not machine_lock_acquired:
                return jsonify({
                    "success": False,
                    "error": "本机已有自动化任务在执行，请稍后再试。",
                    "lock": "busy",
                }), 409
        except ExecutionLockError as lock_exc:
            return jsonify({"success": False, "error": str(lock_exc), "lock": "busy"}), 409
        except ImportError:
            release_machine_lock = None  # type: ignore

        with _case_run_lock:
            _case_run_jobs[user_id] = {
                "active": True,
                "cancel_requested": False,
                "case_id": case_id,
                "case_name": case.get("name", ""),
                "total_steps": len(steps),
                "completed_steps": 0,
                "current_step_order": 0,
                "current_action": "",
                "message": "准备执行 Android 用例...",
                "started_at": start_time,
                "platform": "android",
            }

        try:
            resp, status = execute_mobile_case(
                case_id,
                case,
                steps,
                db,
                user_id,
                start_time,
                capabilities=body.get("capabilities") if isinstance(body.get("capabilities"), dict) else None,
                udid=(body.get("udid") or "").strip(),
                job_update=lambda **kw: _case_job_update(user_id, **kw),
                job_cancelled=lambda: _case_run_cancelled(user_id),
            )
            return resp, status
        finally:
            with _case_run_lock:
                if user_id in _case_run_jobs:
                    _case_run_jobs[user_id]["active"] = False
            if machine_lock_acquired:
                try:
                    release_machine_lock()
                except Exception:
                    pass
