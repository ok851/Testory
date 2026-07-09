package com.testory.assistant.v2.core.database.dao

import androidx.room.*
import com.testory.assistant.v2.core.database.entity.CaseEntity
import kotlinx.coroutines.flow.Flow

@Dao
interface CaseDao {
    @Query("SELECT * FROM test_cases ORDER BY updated_at DESC")
    fun observeAll(): Flow<List<CaseEntity>>

    @Query("SELECT * FROM test_cases WHERE id = :caseId")
    suspend fun getById(caseId: String): CaseEntity?

    @Query("SELECT * FROM test_cases WHERE id = :caseId")
    fun observeById(caseId: String): Flow<CaseEntity?>

    @Query("SELECT * FROM test_cases WHERE target_package = :packageName ORDER BY updated_at DESC")
    fun observeByPackage(packageName: String): Flow<List<CaseEntity>>

    @Query("SELECT * FROM test_cases WHERE name LIKE '%' || :query || '%' OR description LIKE '%' || :query || '%'")
    fun search(query: String): Flow<List<CaseEntity>>

    @Query("SELECT * FROM test_cases WHERE sync_status != 'SYNCED' OR sync_status = 'CONFLICT'")
    suspend fun getUnsyncedCases(): List<CaseEntity>

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun upsert(entity: CaseEntity)

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun upsertAll(entities: List<CaseEntity>)

    @Query("UPDATE test_cases SET sync_status = :status, remote_id = :remoteId WHERE id = :id")
    suspend fun updateSyncStatus(id: String, status: String, remoteId: String? = null)

    @Delete
    suspend fun delete(entity: CaseEntity)

    @Query("DELETE FROM test_cases WHERE id = :caseId")
    suspend fun deleteById(caseId: String)

    @Query("SELECT COUNT(*) FROM test_cases")
    suspend fun count(): Int

    @Query("DELETE FROM test_cases")
    suspend fun deleteAll()

    @Query("SELECT DISTINCT project_name FROM test_cases WHERE project_name != '' ORDER BY project_name")
    fun observeAllProjectNames(): Flow<List<String>>

    @Query("SELECT * FROM test_cases WHERE project_name = :projectName ORDER BY updated_at DESC")
    fun observeByProject(projectName: String): Flow<List<CaseEntity>>

    @Query("SELECT * FROM test_cases WHERE project_name = '' OR project_name IS NULL ORDER BY updated_at DESC")
    fun observeUngrouped(): Flow<List<CaseEntity>>
}
