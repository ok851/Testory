# -*- coding: utf-8 -*-
"""Modify app.py step execution loop to collect StepExecutionDetail."""

FILE = r"D:\mkst_baixiang\Python_Code\NewUITestPlatform\NewUITestPlatform\app.py"
with open(FILE, "r", encoding="utf-8") as f:
    content = f.read()

# 1. Add import at the top of the step loop (after step_index initialization)
old_loop_start = '''                    step_start_time = time.time()
                    # 🔥 修复：初始化为 error，只有执行成功才改为 success
                    step_status = 'error'
                    step_error = ''
                    step_screenshot = '''''

new_loop_start = '''                    step_start_time = time.time()
                    # 🔥 修复：初始化为 error，只有执行成功才改为 success
                    step_status = 'error'
                    step_error = ''
                    step_screenshot = ''

                    # ── 企业级步骤详情收集 ──
                    try:
                        from step_execution_detail import StepExecutionDetail
                        _step_detail = StepExecutionDetail(
                            step_id=step.get('id') or 0,
                            step_order=step.get('step_order', 0),
                            action=action,
                            selector_value=selector_value or "",
                            input_value=input_value or "",
                            description=description or "",
                        )
                        _step_detail.mark_started()
                        # 捕获执行前页面状态
                        try:
                            if automation.page:
                                _step_detail.page_url_before = automation.page.url or ""
                                _step_detail.page_title = await automation.page.title() if hasattr(automation.page, 'title') else ""
                        except Exception:
                            pass
                        _step_detail.iframe_context = iframe_for_step or ""
                    except ImportError:
                        _step_detail = None'''

assert old_loop_start in content, "old_loop_start not found"
content = content.replace(old_loop_start, new_loop_start, 1)

# 2. Replace the success step_results_list.append with detail-based recording
old_success_append = '''                    # ⭐⭐ 记录成功步骤结果
                    step_duration = round(time.time() - step_start_time, 3)
                    step_results_list.append({
                        'step_id': step.get('id'), 'step_order': step.get('step_order', 0),
                        'action': action, 'selector_value': selector_value,
                        'input_value': input_value, 'description': description,
                        'status': step_status, 'error': step_error,
                        'screenshot': step_screenshot, 'duration': step_duration
                    })'''

new_success_append = '''                    # ⭐⭐ 记录成功步骤结果
                    step_duration = round(time.time() - step_start_time, 3)
                    if _step_detail:
                        _step_detail.mark_finished(success=True)
                        try:
                            if automation.page:
                                _step_detail.page_url_after = automation.page.url or ""
                        except Exception:
                            pass
                        step_results_list.append(_step_detail.to_db_kwargs())
                    else:
                        step_results_list.append({
                            'step_id': step.get('id'), 'step_order': step.get('step_order', 0),
                            'action': action, 'selector_value': selector_value,
                            'input_value': input_value, 'description': description,
                            'status': step_status, 'error': step_error,
                            'screenshot': step_screenshot, 'duration': step_duration
                        })'''

assert old_success_append in content, f"old_success_append not found"
content = content.replace(old_success_append, new_success_append, 1)

# 3. Replace the error step_results_list.append with detail-based recording
old_error_append = '''                if not already_recorded and 'step' in dir() and step:
                    failed_step_duration = round(time.time() - step_start_time, 3) if 'step_start_time' in dir() else 0
                    step_results_list.append({
                        'step_id': step.get('id'), 'step_order': step.get('step_order', 0),
                        'action': step.get('action', ''), 'selector_value': step.get('selector_value', ''),
                        'input_value': step.get('input_value', ''), 'description': step.get('description', ''),
                        'status': 'error', 'error': error_msg,
                        'screenshot': failure_screenshot, 'duration': failed_step_duration
                    })'''

new_error_append = '''                if not already_recorded and 'step' in dir() and step:
                    failed_step_duration = round(time.time() - step_start_time, 3) if 'step_start_time' in dir() else 0
                    if '_step_detail' in dir() and _step_detail:
                        _step_detail.mark_finished(success=False, error=error_msg)
                        _step_detail.screenshot = failure_screenshot or ""
                        try:
                            if automation.page:
                                _step_detail.page_url_after = automation.page.url or ""
                        except Exception:
                            pass
                        step_results_list.append(_step_detail.to_db_kwargs())
                    else:
                        step_results_list.append({
                            'step_id': step.get('id'), 'step_order': step.get('step_order', 0),
                            'action': step.get('action', ''), 'selector_value': step.get('selector_value', ''),
                            'input_value': step.get('input_value', ''), 'description': step.get('description', ''),
                            'status': 'error', 'error': error_msg,
                            'screenshot': failure_screenshot, 'duration': failed_step_duration
                        })'''

assert old_error_append in content, "old_error_append not found"
content = content.replace(old_error_append, new_error_append, 1)

# 4. Replace create_step_result with create_step_result_v2 for success path
old_save_success = '''                    for sr in step_results_list:
                        db.create_step_result(run_id, sr['step_id'], sr['step_order'], sr['action'],
                            sr['selector_value'], sr['input_value'], sr['description'],
                            sr['status'], sr['error'], sr['screenshot'], sr['duration'])
                    try:
                        from ai_memory_store import ingest_successful_run, memory_ingest_run_success_enabled'''

new_save_success = '''                    for sr in step_results_list:
                        db.create_step_result_v2(run_id, **sr)
                    try:
                        from ai_memory_store import ingest_successful_run, memory_ingest_run_success_enabled'''

assert old_save_success in content, "old_save_success not found"
content = content.replace(old_save_success, new_save_success, 1)

# 5. Replace create_step_result with create_step_result_v2 for error path
old_save_error = '''                    for sr in step_results_list:
                        db.create_step_result(run_id, sr['step_id'], sr['step_order'], sr['action'],
                            sr['selector_value'], sr['input_value'], sr['description'],
                            sr['status'], sr['error'], sr['screenshot'], sr['duration'])
                    uat_logger.info(f"运行历史记录已保存，Run ID: {run_id}")'''

new_save_error = '''                    for sr in step_results_list:
                        db.create_step_result_v2(run_id, **sr)
                    uat_logger.info(f"运行历史记录已保存，Run ID: {run_id}")'''

assert old_save_error in content, "old_save_error not found"
content = content.replace(old_save_error, new_save_error, 1)

with open(FILE, "w", encoding="utf-8") as f:
    f.write(content)
print("OK: app.py step loop enhanced")
