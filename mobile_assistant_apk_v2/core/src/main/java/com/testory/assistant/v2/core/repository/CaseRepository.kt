package com.testory.assistant.v2.core.repository

import com.testory.assistant.v2.core.communication.PcSyncClient
import com.testory.assistant.v2.core.communication.SyncCaseSummary
import com.testory.assistant.v2.core.database.dao.CaseDao
import com.testory.assistant.v2.core.database.dao.RunHistoryDao
import com.testory.assistant.v2.core.database.dao.StepDao
import com.testory.assistant.v2.core.database.entity.CaseEntity
import com.testory.assistant.v2.core.database.entity.StepEntity
import com.testory.assistant.v2.core.database.entity.toDomain
import com.testory.assistant.v2.core.database.entity.toEntity
import com.testory.assistant.v2.core.model.*
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map
import kotlinx.serialization.json.Json
import java.util.UUID
import javax.inject.Inject
import javax.inject.Singleton

/**
 * 用例仓库 — 统一管理本地 Room DB 和 PC 端同步。
 */
@Singleton
class CaseRepository @Inject constructor(
    private val caseDao: CaseDao,
    private val stepDao: StepDao,
    private val runHistoryDao: RunHistoryDao,
    private val pcSyncClient: PcSyncClient
) {

    // ── Observe ──

    fun observeAllCases(): Flow<List<TestCase>> = caseDao.observeAll().map { entities ->
        val result = mutableListOf<TestCase>()
        for (caseEntity in entities) {
            val steps = stepDao.getByCaseId(caseEntity.id).map { it.toDomain() }
            result.add(caseEntity.toDomain(steps))
        }
        result
    }

    fun observeCase(caseId: String): Flow<TestCase?> = caseDao.observeById(caseId).map { entity ->
        entity?.let {
            val steps = stepDao.getByCaseId(it.id).map { step -> step.toDomain() }
            it.toDomain(steps)
        }
    }

    fun observeCasesByPackage(packageName: String): Flow<List<TestCase>> =
        caseDao.observeByPackage(packageName).map { entities ->
            val result = mutableListOf<TestCase>()
            for (entity in entities) {
                val steps = stepDao.getByCaseId(entity.id).map { step -> step.toDomain() }
                result.add(entity.toDomain(steps))
            }
            result
        }

    fun observeRunHistory(caseId: String): Flow<List<RunResultSummary>> =
        runHistoryDao.observeByCaseId(caseId).map { entities ->
            entities.map { it.toDomain() }
        }

    fun observeRecentRunHistory(): Flow<List<RunResultSummary>> =
        runHistoryDao.observeRecent().map { entities ->
            entities.map { it.toDomain() }
        }

    // ── CRUD ──

    suspend fun getCase(caseId: String): TestCase? {
        val entity = caseDao.getById(caseId) ?: return null
        val steps = stepDao.getByCaseId(caseId).map { it.toDomain() }
        return entity.toDomain(steps)
    }

    suspend fun saveCase(testCase: TestCase): String {
        val caseId = testCase.id.ifEmpty { UUID.randomUUID().toString() }
        val entity = testCase.copy(id = caseId).toEntity()
        caseDao.upsert(entity)

        // Save steps
        val stepEntities = testCase.steps.mapIndexed { index, step ->
            step.copy(
                id = step.id.ifEmpty { UUID.randomUUID().toString() },
                caseId = caseId,
                index = index + 1
            ).toEntity()
        }
        stepDao.replaceAll(caseId, stepEntities)

        // Optionally sync to PC
        try {
            pcSyncClient.pushCase(testCase.copy(id = caseId))
        } catch (_: Exception) {
            // Sync failure is non-blocking
        }

        return caseId
    }

    suspend fun updateSteps(caseId: String, steps: List<com.testory.assistant.v2.core.model.Step>) {
        val entities = steps.mapIndexed { index, step ->
            step.copy(caseId = caseId, index = index + 1).toEntity()
        }
        stepDao.replaceAll(caseId, entities)
        caseDao.upsert(
            caseDao.getById(caseId)?.copy(
                stepCount = steps.size,
                updatedAt = System.currentTimeMillis(),
                syncStatus = SyncStatus.MODIFIED.name
            ) ?: return
        )
    }

    suspend fun deleteCase(caseId: String) {
        caseDao.deleteById(caseId)
        // Steps are cascade-deleted by Room
    }

    suspend fun saveRunResult(caseId: String, caseName: String, run: RunResultSummary) {
        runHistoryDao.insert(
            com.testory.assistant.v2.core.database.entity.RunHistoryEntity(
                id = run.runId.ifEmpty { UUID.randomUUID().toString() },
                caseId = caseId,
                caseName = caseName,
                success = run.success,
                totalSteps = run.totalSteps,
                passedSteps = run.passedSteps,
                failedStepIndex = run.failedStepIndex,
                failedStepError = run.failedStepIndex.let { if (it > 0) "第 $it 步失败" else "" },
                durationMs = run.durationMs,
                runAt = run.runAt,
                stepResultsJson = run.stepResultsJson
            )
        )

        val entity = caseDao.getById(caseId)
        if (entity != null) {
            caseDao.upsert(
                entity.copy(
                    lastRunSuccess = run.success,
                    updatedAt = System.currentTimeMillis()
                )
            )
        }
    }

    // ── Sync ──

    suspend fun syncWithPc() {
        val unsynced = caseDao.getUnsyncedCases()
        pushCasesByIds(unsynced.map { it.id }.toSet())
    }

    suspend fun pushCasesByIds(caseIds: Set<String>) {
        for (caseId in caseIds) {
            val entity = caseDao.getById(caseId) ?: continue
            val steps = stepDao.getByCaseId(caseId).map { it.toDomain() }
            val testCase = entity.toDomain(steps)
            try {
                val result = pcSyncClient.pushCase(testCase)
                if (result is com.testory.assistant.v2.core.communication.SyncResult.Success) {
                    caseDao.updateSyncStatus(caseId, SyncStatus.SYNCED.name)
                }
            } catch (_: Exception) { }
        }
    }

    suspend fun pullFromPc(): List<String> {
        try {
            val summaries = pullCaseSummaries()
            val idsToPull = summaries.filter { summary ->
                val existing = caseDao.getById(summary.id)
                existing == null || existing.syncStatus == SyncStatus.REMOTE_UPDATED.name
            }.map { it.id }
            if (idsToPull.isEmpty()) return emptyList()
            val fullCases = pcSyncClient.pullCasesByIds(idsToPull)
            val ids = mutableListOf<String>()
            for (testCase in fullCases) {
                saveCase(testCase)
                ids.add(testCase.id)
            }
            return ids
        } catch (_: Exception) {
            return emptyList()
        }
    }

    suspend fun pullCaseSummaries(): List<SyncCaseSummary> {
        return try {
            pcSyncClient.pullCaseSummaries()
        } catch (_: Exception) {
            emptyList()
        }
    }

    suspend fun pullCasesByIds(ids: List<String>): List<String> {
        try {
            if (ids.isEmpty()) return emptyList()
            val fullCases = pcSyncClient.pullCasesByIds(ids)
            val savedIds = mutableListOf<String>()
            for (testCase in fullCases) {
                saveCase(testCase)
                savedIds.add(testCase.id)
            }
            return savedIds
        } catch (_: Exception) {
            return emptyList()
        }
    }

    fun isPcConnected(): Boolean {
        return pcSyncClient.state.value == com.testory.assistant.v2.core.model.PcConnectionState.CONNECTED
    }

    suspend fun getDeviceInfo(): DeviceInfo {
        return pcSyncClient.getDeviceInfo()
    }

    suspend fun reportReplayResult(
        caseId: String, runId: String,
        stepResults: List<StepResult>,
        deviceInfo: DeviceInfo
    ) {
        try {
            val allPassed = stepResults.all { it.success }
            pcSyncClient.reportReplayResult(
                caseId = caseId,
                runId = runId,
                deviceModel = deviceInfo.model,
                androidVersion = deviceInfo.androidVersion,
                deviceName = deviceInfo.deviceName,
                success = allPassed,
                totalSteps = stepResults.size,
                passedSteps = stepResults.count { it.success },
                durationMs = stepResults.sumOf { it.durationMs },
                results = stepResults
            )
        } catch (_: Exception) { }
    }

    fun searchCases(query: String): Flow<List<TestCase>> =
        caseDao.search(query).map { entities ->
            val result = mutableListOf<TestCase>()
            for (entity in entities) {
                val steps = stepDao.getByCaseId(entity.id).map { step -> step.toDomain() }
                result.add(entity.toDomain(steps))
            }
            result
        }

    suspend fun getCaseCount(): Int = caseDao.count()

    suspend fun clearAll() {
        runHistoryDao.deleteAll()
        stepDao.deleteAll()
        caseDao.deleteAll()
    }
}
