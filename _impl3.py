# -*- coding: utf-8 -*-
"""Fix StepExecutionDetail to use correct time function."""

FILE = r"D:\mkst_baixiang\Python_Code\NewUITestPlatform\NewUITestPlatform\step_execution_detail.py"
with open(FILE, "r", encoding="utf-8") as f:
    content = f.read()

content = content.replace(
    'from time_utils import utc_now_iso',
    'from time_utils import utc_now_sqlite_str'
)
content = content.replace(
    '"started_at": utc_now_iso() if self.started_at > 0 else "",',
    '"started_at": utc_now_sqlite_str() if self.started_at > 0 else "",'
)

with open(FILE, "w", encoding="utf-8") as f:
    f.write(content)
print("OK: step_execution_detail.py fixed")
