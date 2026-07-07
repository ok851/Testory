package com.testory.assistant.v2.core.model

import kotlinx.serialization.Serializable

/**
 * 测试用例 — 一组有序步骤的集合。
 */
@Serializable
data class TestCase(
    val id: String = "",
    val name: String = "",
    val description: String = "",
    /** 目标应用包名 */
    val targetPackage: String = "",
    /** 目标应用名 (人类可读) */
    val targetAppName: String = "",
    /** 步骤列表 */
    val steps: List<Step> = emptyList(),
    /** 用例标签 (用于分类) */
    val tags: List<String> = emptyList(),
    /** 创建来源 */
    val source: CaseSource = CaseSource.MANUAL,
    /** 创建时间 */
    val createdAt: Long = System.currentTimeMillis(),
    /** 更新时间 */
    val updatedAt: Long = System.currentTimeMillis(),
    /** PC 端远程 ID (用于同步) */
    val remoteId: String? = null,
    /** 同步状态 */
    val syncStatus: SyncStatus = SyncStatus.LOCAL_ONLY,
    /** 上次运行结果摘要 */
    val lastRunResult: RunResultSummary? = null
)

@Serializable
enum class CaseSource {
    /** 手动创建 */
    MANUAL,
    /** 录制创建 */
    RECORDED,
    /** AI 对话创建 */
    AI_GENERATED,
    /** 模板创建 */
    TEMPLATE
}

@Serializable
enum class SyncStatus {
    /** 仅存在于本地 */
    LOCAL_ONLY,
    /** 已同步到 PC */
    SYNCED,
    /** 本地有未同步的修改 */
    MODIFIED,
    /** PC 端有更新 */
    REMOTE_UPDATED,
    /** 冲突 (两边都改了) */
    CONFLICT
}

@Serializable
data class RunResultSummary(
    val runId: String = "",
    val success: Boolean = false,
    val totalSteps: Int = 0,
    val passedSteps: Int = 0,
    val failedStepIndex: Int = -1,
    val durationMs: Long = 0,
    val runAt: Long = System.currentTimeMillis()
)
