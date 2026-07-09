package com.testory.assistant.v2.core.database.dao

import androidx.room.*
import com.testory.assistant.v2.core.database.entity.RunHistoryEntity
import kotlinx.coroutines.flow.Flow

@Dao
interface RunHistoryDao {
    @Query("SELECT * FROM run_history ORDER BY run_at DESC LIMIT 50")
    fun observeRecent(): Flow<List<RunHistoryEntity>>

    @Query("SELECT * FROM run_history WHERE case_id = :caseId ORDER BY run_at DESC")
    fun observeByCaseId(caseId: String): Flow<List<RunHistoryEntity>>

    @Query("SELECT * FROM run_history WHERE id = :runId")
    suspend fun getById(runId: String): RunHistoryEntity?

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insert(run: RunHistoryEntity)

    @Query("DELETE FROM run_history WHERE run_at < :beforeTimestamp")
    suspend fun deleteOlderThan(beforeTimestamp: Long)

    @Query("DELETE FROM run_history WHERE case_id = :caseId")
    suspend fun deleteByCaseId(caseId: String)

    @Query("SELECT COUNT(*) FROM run_history")
    suspend fun count(): Int

    @Query("""
        SELECT AVG(CASE WHEN success THEN 1.0 ELSE 0.0 END) 
        FROM run_history 
        WHERE case_id = :caseId AND run_at > :since
    """)
    suspend fun getSuccessRate(caseId: String, since: Long = 0): Double

    @Query("DELETE FROM run_history")
    suspend fun deleteAll()
}
