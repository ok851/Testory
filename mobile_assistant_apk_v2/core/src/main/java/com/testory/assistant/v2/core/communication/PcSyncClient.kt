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

    /** 拉取所有用例列表 */
    suspend fun pullAllCases(): List<TestCase>

    /** 推送录制步骤（实时 streaming） */
    suspend fun pushStep(step: Step): SyncResult

    /** 拉取步骤 */
    suspend fun pullSteps(caseId: String): List<Step>

    /** 上报回放结果 */
    suspend fun reportReplayResult(runId: String, results: List<StepResult>): SyncResult

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
data class CaseListResponse(val success: Boolean, val cases: List<TestCase> = emptyList())

@Serializable
data class StepListResponse(val success: Boolean, val steps: List<Step> = emptyList())

@Serializable
data class ReplayReportRequest(
    val runId: String,
    val caseId: String,
    val success: Boolean,
    val stepResults: List<StepResult>
)
