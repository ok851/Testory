# -*- coding: utf-8 -*-
"""Add fetchJobStatus to PcSyncClient interface."""

FILE = r"D:\mkst_baixiang\Python_Code\NewUITestPlatform\NewUITestPlatform\mobile_assistant_apk_v2\core\src\main\java\com\testory\assistant\v2\core\communication\PcSyncClient.kt"

with open(FILE, "r", encoding="utf-8") as f:
    content = f.read()

# Add fetchJobStatus after reportJobResult
old = '    /** 上报 job 完成事件（含 variables.sms_otp） */\n    suspend fun reportJobResult(jobId: String, payloadJson: String): SyncResult\n\n    /** 获取设备信息 */'
new = '    /** 上报 job 完成事件（含 variables.sms_otp） */\n    suspend fun reportJobResult(jobId: String, payloadJson: String): SyncResult\n\n    /** 轻量级 job 状态查询：手机回放中每步轮询，检测 PC 是否已取消 */\n    suspend fun fetchJobStatus(jobId: String): JobStatusInfo?\n\n    /** 获取设备信息 */'

assert old in content, f"old not found in {FILE}"
content = content.replace(old, new, 1)

# Add JobStatusInfo data class after PendingRunJob
old2 = '''data class PendingRunJob(
    val jobId: String,
    val caseId: Int = 0,
    val jobKind: String = "run_steps",
    /** 已映射为 Unified Step IR；extract_otp 也可从 locator/inputText 读 hint/pattern */
    val steps: List<com.testory.assistant.v2.core.model.Step> = emptyList()
)'''
new2 = '''data class PendingRunJob(
    val jobId: String,
    val caseId: Int = 0,
    val jobKind: String = "run_steps",
    /** 已映射为 Unified Step IR；extract_otp 也可从 locator/inputText 读 hint/pattern */
    val steps: List<com.testory.assistant.v2.core.model.Step> = emptyList()
)

data class JobStatusInfo(
    val jobId: String,
    val status: String,
    val shouldAbort: Boolean = false,
    val abortReason: String = "",
    val error: String = ""
)'''

assert old2 in content, f"old2 not found in {FILE}"
content = content.replace(old2, new2, 1)

with open(FILE, "w", encoding="utf-8") as f:
    f.write(content)

print("OK: PcSyncClient interface updated")
