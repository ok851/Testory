# -*- coding: utf-8 -*-
"""Update PcRunJobPoller.kt to pass jobId to executeSteps."""

FILE = r"D:\mkst_baixiang\Python_Code\NewUITestPlatform\NewUITestPlatform\mobile_assistant_apk_v2\service\src\main\java\com\testory\assistant\v2\service\accessibility\PcRunJobPoller.kt"

with open(FILE, "r", encoding="utf-8") as f:
    content = f.read()

# Update handleRunSteps to pass jobId
old = '            val outcome = phoneJobExecutor.executeSteps(pending.steps)'
new = '            val outcome = phoneJobExecutor.executeSteps(pending.steps, jobId = pending.jobId)'

assert old in content, "old not found"
content = content.replace(old, new, 1)

with open(FILE, "w", encoding="utf-8") as f:
    f.write(content)

print("OK: PcRunJobPoller.kt updated")
