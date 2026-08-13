# -*- coding: utf-8 -*-
"""Update wait_for_run_job to pass abort_reason."""

FILE = r"D:\mkst_baixiang\Python_Code\NewUITestPlatform\NewUITestPlatform\mobile_sync_store.py"

with open(FILE, "r", encoding="utf-8") as f:
    content = f.read()

# Update the abort handling in wait_for_run_job to pass abort_reason
old = (
    '        if abort_event is not None and getattr(abort_event, "is_set", lambda: False)():\n'
    '            cancel_run_job(\n'
    '                job_id,\n'
    '                error="任务已中止，停止等待手机本机执行",\n'
    '                error_code="MOBILE_AWAIT_ABORTED",\n'
    '            )'
)
new = (
    '        if abort_event is not None and getattr(abort_event, "is_set", lambda: False)():\n'
    '            _reason = str(getattr(abort_event, "_abort_reason", "") or "").strip() or "user_pause"\n'
    '            cancel_run_job(\n'
    '                job_id,\n'
    '                error="任务已中止，停止等待手机本机执行",\n'
    '                error_code="MOBILE_AWAIT_ABORTED",\n'
    '                abort_reason=_reason,\n'
    '            )'
)
assert old in content, "old not found"
content = content.replace(old, new, 1)

with open(FILE, "w", encoding="utf-8") as f:
    f.write(content)

print("OK: wait_for_run_job updated with abort_reason")
