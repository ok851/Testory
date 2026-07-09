package com.testory.assistant.v2.core.database

import androidx.room.Database
import androidx.room.RoomDatabase
import androidx.room.migration.Migration
import androidx.sqlite.db.SupportSQLiteDatabase
import com.testory.assistant.v2.core.database.dao.CaseDao
import com.testory.assistant.v2.core.database.dao.RunHistoryDao
import com.testory.assistant.v2.core.database.dao.StepDao
import com.testory.assistant.v2.core.database.entity.CaseEntity
import com.testory.assistant.v2.core.database.entity.RunHistoryEntity
import com.testory.assistant.v2.core.database.entity.StepEntity

@Database(
    entities = [
        CaseEntity::class,
        StepEntity::class,
        RunHistoryEntity::class
    ],
    version = 2,
    exportSchema = false
)
abstract class AppDatabase : RoomDatabase() {
    abstract fun caseDao(): CaseDao
    abstract fun stepDao(): StepDao
    abstract fun runHistoryDao(): RunHistoryDao

    companion object {
        const val DATABASE_NAME = "testory_assistant_v2.db"

        val MIGRATION_1_2 = object : Migration(1, 2) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL("ALTER TABLE test_cases ADD COLUMN project_id TEXT NOT NULL DEFAULT ''")
                db.execSQL("ALTER TABLE test_cases ADD COLUMN project_name TEXT NOT NULL DEFAULT ''")
            }
        }
    }
}
