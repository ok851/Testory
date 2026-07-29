package com.testory.assistant.v2.core.communication

import com.testory.assistant.v2.core.model.*
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json

/**
 * PC 端同步客户端 — HTTP REST 协议（向后兼容 v1 的 PluginHttpServer）。
 *
 * gRPC 迁移将在 Phase 2 实现，Phase 1 保持 HTTP/JSON 兼容
 * 以确保与现有 Python 后端无缝对接。
 */
interface PcSyncClient {

    /** 连接状态 */
    val state: kotlinx.coroutines.flow.StateFlow<PcConnectionState>

    /** 连接到 PC 端 (IP:Port) */
    suspend fun connect(host: String, port: Int): Boolean

    /** 使用配对码连接（优先方式，自动获取 device_token） */
    suspend fun connectWithPairCode(host: String, port: Int, pairCode: String): Boolean

    /** 断开连接 */
    suspend fun disconnect()

    /** 获取已缓存的 device_token（用于 UI 显示连接状态） */
    suspend fun getDeviceToken(): String?

    /** 同步用例到 PC */
    suspend fun pushCase(testCase: TestCase): SyncResult

    /** 从 PC 拉取用例 */
    suspend fun pullCase(caseId: String): TestCase?

    /** 拉取所有用例摘要列表 */
    suspend fun pullCaseSummaries(): List<SyncCaseSummary>

    /** 批量拉取完整用例（含步骤） */
    suspend fun pullCasesByIds(ids: List<String>): List<TestCase>

    /** 拉取所有用例列表 (已废弃，请使用 pullCaseSummaries + pullCasesByIds) */
    @Deprecated("Use pullCaseSummaries() + pullCasesByIds()")
    suspend fun pullAllCases(): List<TestCase>

    /** 推送录制步骤（实时 streaming） */
    suspend fun pushStep(step: Step): SyncResult

    /** 拉取步骤 */
    suspend fun pullSteps(caseId: String): List<Step>

    /** 上报回放结果（含设备信息） */
    suspend fun reportReplayResult(
        caseId: String, runId: String,
        deviceModel: String, androidVersion: String, deviceName: String,
        success: Boolean, totalSteps: Int, passedSteps: Int, durationMs: Long,
        results: List<StepResult>
    ): SyncResult

    /** AI 生成测试步骤（经 PC 已绑定大模型） */
    /** AI：mode=chat 自由对话；mode=generate 生成可回放步骤 */
    suspend fun aiGenerateSteps(message: String, mode: String = "chat"): AiGenerateResult

    /** 查询 PC 端 AI 模型就绪态（无密钥） */
    suspend fun fetchAiStatus(): AiStatusResult

    /** 手机截图验证码 → PC VLM 解法 */
    suspend fun solveCaptcha(imageBase64: String, hint: String = "", instruction: String = ""): CaptchaSolveResult

    /** 拉取 PC 下发的本机执行 job；jobKind 为空则取任意 pending */
    suspend fun fetchPendingRunJob(jobKind: String = ""): PendingRunJob?

    /** 上报 job 完成事件（含 variables.sms_otp） */
    suspend fun reportJobResult(jobId: String, payloadJson: String): SyncResult

    /** 获取设备信息 */
    suspend fun getDeviceInfo(): DeviceInfo
}

sealed class SyncResult {
    data object Success : SyncResult()
    data class Error(val message: String, val code: Int = 0) : SyncResult()
    data object NetworkUnavailable : SyncResult()
}

// ── JSON DTOs for HTTP communication ──

@Serializable
data class SyncStartRequest(val deviceId: String, val deviceInfo: DeviceInfo)

@Serializable
data class SyncResponse(val success: Boolean, val message: String = "", val data: String = "")

@Serializable
data class SyncCaseSummary(
    val id: String,
    val name: String,
    @kotlinx.serialization.SerialName("project_id") val projectId: String = "",
    @kotlinx.serialization.SerialName("project_name") val projectName: String = "",
    val description: String = "",
    @kotlinx.serialization.SerialName("step_count") val stepCount: Int = 0
)

@Serializable
data class CaseListResponse(val success: Boolean, val cases: List<SyncCaseSummary> = emptyList())

@Serializable
data class PullBatchRequest(val case_ids: List<String>)

@Serializable
data class StepListResponse(val success: Boolean, val steps: List<Step> = emptyList())

@Serializable
data class ReplayReportRequest(
    val runId: String,
    val caseId: String,
    val success: Boolean,
    val stepResults: List<StepResult>
)

@Serializable
data class RunEventsRequest(
    val case_id: String,
    val run_id: String,
    val device_model: String,
    val android_version: String,
    val device_name: String,
    val success: Boolean,
    val total_steps: Int,
    val passed_steps: Int,
    val duration_ms: Long,
    val results: List<RunStepEvent>
)

@Serializable
data class RunStepEvent(
    val step_index: Int,
    val step_id: String,
    val action: String,
    val status: String,
    val error: String = "",
    val duration_ms: Long = 0,
    val description: String = ""
)

// ── AI Generate DTOs ──

@Serializable
data class AiGenerateRequest(
    val message: String,
    val mode: String = "chat"
)

@Serializable
data class AiGenerateResponse(
    val success: Boolean,
    val error: String? = null,
    val case_name: String? = null,
    val description: String? = null,
    val expected_result: String? = null,
    val steps: List<AiStepDto> = emptyList(),
    val ai_status: AiStatusDto? = null
)

@Serializable
data class AiStepDto(
    val action: String = "tap",
    val selector_type: String = "",
    val selector_value: String = "",
    val input_value: String = "",
    val description: String = "",
    val automation_layer: String = "android"
)

@Serializable
data class AiStatusDto(
    val ready: Boolean = false,
    val provider: String = "",
    val model: String = "",
    val profile_id: String = "",
    val message: String = ""
)

@Serializable
data class AiStatusResponse(
    val success: Boolean = false,
    val connected: Boolean = false,
    val ready: Boolean = false,
    val provider: String = "",
    val model: String = "",
    val profile_id: String = "",
    val message: String = "",
    val error: String? = null
)

data class AiGenerateResult(
    val success: Boolean,
    val error: String? = null,
    val caseName: String = "",
    val description: String = "",
    val expectedResult: String = "",
    val steps: List<com.testory.assistant.v2.core.model.Step> = emptyList(),
    val provider: String = "",
    val model: String = ""
)

data class AiStatusResult(
    val success: Boolean,
    val ready: Boolean = false,
    val provider: String = "",
    val model: String = "",
    val message: String = "",
    val error: String? = null
)

data class CaptchaSolveResult(
    val success: Boolean,
    val solutionType: String = "",
    val distance: Int = 0,
    val angle: Int = 0,
    val points: List<Pair<Int, Int>> = emptyList(),
    val raw: String = "",
    val error: String? = null
)

@Serializable
data class PendingRunJobResponse(
    val success: Boolean = false,
    val has_job: Boolean = false,
    val job_id: String = "",
    val case_id: Int = 0,
    val job_kind: String = "run_steps",
    val steps: List<AiStepDto> = emptyList()
)

data class PendingRunJob(
    val jobId: String,
    val caseId: Int = 0,
    val jobKind: String = "run_steps",
    /** 已映射为 Unified Step IR；extract_otp 也可从 locator/inputText 读 hint/pattern */
    val steps: List<com.testory.assistant.v2.core.model.Step> = emptyList()
)