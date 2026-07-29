package com.testory.assistant.v2.core.database.dao

import androidx.room.*
import com.testory.assistant.v2.core.database.entity.CaseEntity
import kotlinx.coroutines.flow.Flow

@Dao
abstract class CaseDao {
    @Query("SELECT * FROM test_cases ORDER BY updated_at DESC")
    abstract fun observeAll(): Flow<List<CaseEntity>>

    @Query("SELECT * FROM test_cases WHERE id = :caseId")
    abstract suspend fun getById(caseId: String): CaseEntity?

    @Query("SELECT * FROM test_cases WHERE id = :caseId")
    abstract fun observeById(caseId: String): Flow<CaseEntity?>

    @Query("SELECT * FROM test_cases WHERE target_package = :packageName ORDER BY updated_at DESC")
    abstract fun observeByPackage(packageName: String): Flow<List<CaseEntity>>

    @Query("SELECT * FROM test_cases WHERE name LIKE '%' || :query || '%' OR description LIKE '%' || :query || '%'")
    abstract fun search(query: String): Flow<List<CaseEntity>>

    @Query("SELECT * FROM test_cases WHERE sync_status != 'SYNCED' OR sync_status = 'CONFLICT'")
    abstract suspend fun getUnsyncedCases(): List<CaseEntity>

    /**
     * 禁止对带 CASCADE 子表的父行使用 OnConflictStrategy.REPLACE：
     * SQLite REPLACE = 先 DELETE 再 INSERT，会级联清空 steps。
     */
    @Insert(onConflict = OnConflictStrategy.ABORT)
    protected abstract suspend fun insert(entity: CaseEntity)

    @Update
    protected abstract suspend fun update(entity: CaseEntity)

    @Transaction
    open suspend fun upsert(entity: CaseEntity) {
        val existing = getById(entity.id)
        if (existing == null) {
            insert(entity)
        } else {
            update(entity)
        }
    }

    @Transaction
    open suspend fun upsertAll(entities: List<CaseEntity>) {
        for (entity in entities) {
            upsert(entity)
        }
    }

    @Query(
        """
        UPDATE test_cases SET
            last_run_success = :success,
            last_run_at = :runAt,
            total_steps = :totalSteps,
            updated_at = :updatedAt
        WHERE id = :caseId
        """
    )
    abstract suspend fun updateLastRun(
        caseId: String,
        success: Boolean,
        runAt: Long,
        totalSteps: Int,
        updatedAt: Long
    )

    @Query("UPDATE test_cases SET sync_status = :status, remote_id = :remoteId WHERE id = :id")
    abstract suspend fun updateSyncStatus(id: String, status: String, remoteId: String? = null)

    @Query(
        """
        UPDATE test_cases SET
            step_count = :stepCount,
            updated_at = :updatedAt,
            sync_status = :syncStatus
        WHERE id = :caseId
        """
    )
    abstract suspend fun updateStepCount(
        caseId: String,
        stepCount: Int,
        updatedAt: Long,
        syncStatus: String
    )

    @Delete
    abstract suspend fun delete(entity: CaseEntity)

    @Query("DELETE FROM test_cases WHERE id = :caseId")
    abstract suspend fun deleteById(caseId: String)

    @Query("SELECT COUNT(*) FROM test_cases")
    abstract suspend fun count(): Int

    @Query("DELETE FROM test_cases")
    abstract suspend fun deleteAll()

    @Query("SELECT DISTINCT project_name FROM test_cases WHERE project_name != '' ORDER BY project_name")
    abstract fun observeAllProjectNames(): Flow<List<String>>

    @Query("SELECT * FROM test_cases WHERE project_name = :projectName ORDER BY updated_at DESC")
    abstract fun observeByProject(projectName: String): Flow<List<CaseEntity>>

    @Query("SELECT * FROM test_cases WHERE project_name = '' OR project_name IS NULL ORDER BY updated_at DESC")
    abstract fun observeUngrouped(): Flow<List<CaseEntity>>
}
