package com.testory.assistant.v2.core.database.entity

import androidx.room.ColumnInfo
import androidx.room.Entity
import androidx.room.PrimaryKey
import com.testory.assistant.v2.core.model.RunResultSummary

@Entity(tableName = "run_history")
data class RunHistoryEntity(
    @PrimaryKey
    @ColumnInfo(name = "id")
    val id: String,

    @ColumnInfo(name = "case_id")
    val caseId: String,

    @ColumnInfo(name = "case_name")
    val caseName: String = "",

    @ColumnInfo(name = "success")
    val success: Boolean = false,

    @ColumnInfo(name = "total_steps")
    val totalSteps: Int = 0,

    @ColumnInfo(name = "passed_steps")
    val passedSteps: Int = 0,

    @ColumnInfo(name = "failed_step_index")
    val failedStepIndex: Int = -1,

    @ColumnInfo(name = "failed_step_error")
    val failedStepError: String = "",

    @ColumnInfo(name = "duration_ms")
    val durationMs: Long = 0,

    @ColumnInfo(name = "run_at")
    val runAt: Long = System.currentTimeMillis(),

    /** 步骤结果详情 (JSON) */
    @ColumnInfo(name = "step_results_json")
    val stepResultsJson: String = "[]"
)

fun RunHistoryEntity.toDomain(): RunResultSummary = RunResultSummary(
    runId = id,
    success = success,
    totalSteps = totalSteps,
    passedSteps = passedSteps,
    failedStepIndex = failedStepIndex,
    durationMs = durationMs,
    runAt = runAt,
    stepResultsJson = stepResultsJson
)
