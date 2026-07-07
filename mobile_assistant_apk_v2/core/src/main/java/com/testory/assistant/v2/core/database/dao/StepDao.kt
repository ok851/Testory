package com.testory.assistant.v2.core.database.dao

import androidx.room.*
import com.testory.assistant.v2.core.database.entity.StepEntity
import kotlinx.coroutines.flow.Flow

@Dao
interface StepDao {
    @Query("SELECT * FROM steps WHERE case_id = :caseId ORDER BY step_index ASC")
    suspend fun getByCaseId(caseId: String): List<StepEntity>

    @Query("SELECT * FROM steps WHERE case_id = :caseId ORDER BY step_index ASC")
    fun observeByCaseId(caseId: String): Flow<List<StepEntity>>

    @Query("SELECT * FROM steps WHERE id = :stepId")
    suspend fun getById(stepId: String): StepEntity?

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun upsert(step: StepEntity)

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun upsertAll(steps: List<StepEntity>)

    @Query("UPDATE steps SET step_index = :newIndex WHERE id = :stepId")
    suspend fun updateIndex(stepId: String, newIndex: Int)

    @Query("DELETE FROM steps WHERE case_id = :caseId")
    suspend fun deleteByCaseId(caseId: String)

    @Delete
    suspend fun delete(step: StepEntity)

    @Query("SELECT COUNT(*) FROM steps WHERE case_id = :caseId")
    suspend fun countByCaseId(caseId: String): Int

    @Transaction
    suspend fun replaceAll(caseId: String, steps: List<StepEntity>) {
        deleteByCaseId(caseId)
        upsertAll(steps)
    }
}
