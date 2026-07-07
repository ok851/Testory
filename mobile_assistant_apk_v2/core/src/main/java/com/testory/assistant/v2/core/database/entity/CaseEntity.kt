package com.testory.assistant.v2.core.database.entity

import androidx.room.ColumnInfo
import androidx.room.Entity
import androidx.room.PrimaryKey
import com.testory.assistant.v2.core.model.CaseSource
import com.testory.assistant.v2.core.model.SyncStatus
import com.testory.assistant.v2.core.model.TestCase

@Entity(tableName = "test_cases")
data class CaseEntity(
    @PrimaryKey
    @ColumnInfo(name = "id")
    val id: String,

    @ColumnInfo(name = "name")
    val name: String,

    @ColumnInfo(name = "description")
    val description: String = "",

    @ColumnInfo(name = "target_package")
    val targetPackage: String = "",

    @ColumnInfo(name = "target_app_name")
    val targetAppName: String = "",

    @ColumnInfo(name = "tags")
    val tags: String = "[]",  // JSON array stored as string

    @ColumnInfo(name = "source")
    val source: String = "MANUAL",

    @ColumnInfo(name = "created_at")
    val createdAt: Long = System.currentTimeMillis(),

    @ColumnInfo(name = "updated_at")
    val updatedAt: Long = System.currentTimeMillis(),

    @ColumnInfo(name = "remote_id")
    val remoteId: String? = null,

    @ColumnInfo(name = "sync_status")
    val syncStatus: String = "LOCAL_ONLY",

    @ColumnInfo(name = "last_run_success")
    val lastRunSuccess: Boolean = false,

    @ColumnInfo(name = "last_run_at")
    val lastRunAt: Long = 0,

    @ColumnInfo(name = "total_steps")
    val totalSteps: Int = 0,

    @ColumnInfo(name = "step_count")
    val stepCount: Int = 0
)

fun CaseEntity.toDomain(steps: List<com.testory.assistant.v2.core.model.Step> = emptyList()): TestCase = TestCase(
    id = id,
    name = name,
    description = description,
    targetPackage = targetPackage,
    targetAppName = targetAppName,
    steps = steps,
    tags = parseTagsList(tags),
    source = try { CaseSource.valueOf(source) } catch (_: Exception) { CaseSource.MANUAL },
    createdAt = createdAt,
    updatedAt = updatedAt,
    remoteId = remoteId,
    syncStatus = try { SyncStatus.valueOf(syncStatus) } catch (_: Exception) { SyncStatus.LOCAL_ONLY },
    lastRunResult = if (lastRunAt > 0) {
        com.testory.assistant.v2.core.model.RunResultSummary(
            success = lastRunSuccess,
            totalSteps = totalSteps,
            runAt = lastRunAt
        )
    } else null
)

fun TestCase.toEntity(): CaseEntity = CaseEntity(
    id = id.ifEmpty { java.util.UUID.randomUUID().toString() },
    name = name,
    description = description,
    targetPackage = targetPackage,
    targetAppName = targetAppName,
    tags = tags.joinToString(prefix = "[", postfix = "]") { "\"$it\"" },
    source = source.name,
    createdAt = createdAt,
    updatedAt = System.currentTimeMillis(),
    remoteId = remoteId,
    syncStatus = syncStatus.name,
    lastRunSuccess = lastRunResult?.success ?: false,
    lastRunAt = lastRunResult?.runAt ?: 0,
    totalSteps = lastRunResult?.totalSteps ?: 0,
    stepCount = steps.size
)

private fun parseTagsList(json: String): List<String> {
    if (json.isBlank() || json == "[]") return emptyList()
    return try {
        json.removeSurrounding("[", "]")
            .split(",")
            .map { it.trim().removeSurrounding("\"") }
            .filter { it.isNotBlank() }
    } catch (_: Exception) { emptyList() }
}
