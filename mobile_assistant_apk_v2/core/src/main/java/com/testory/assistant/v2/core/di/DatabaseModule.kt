package com.testory.assistant.v2.core.di

import android.content.Context
import androidx.room.Room
import com.testory.assistant.v2.core.database.AppDatabase
import com.testory.assistant.v2.core.database.dao.CaseDao
import com.testory.assistant.v2.core.database.dao.RunHistoryDao
import com.testory.assistant.v2.core.database.dao.StepDao
import dagger.Module
import dagger.Provides
import dagger.hilt.InstallIn
import dagger.hilt.android.qualifiers.ApplicationContext
import dagger.hilt.components.SingletonComponent
import javax.inject.Singleton

@Module
@InstallIn(SingletonComponent::class)
object DatabaseModule {

    @Provides
    @Singleton
    fun provideDatabase(@ApplicationContext context: Context): AppDatabase {
        return Room.databaseBuilder(
            context,
            AppDatabase::class.java,
            AppDatabase.DATABASE_NAME
        )
            .fallbackToDestructiveMigration()
            .build()
    }

    @Provides
    fun provideCaseDao(db: AppDatabase): CaseDao = db.caseDao()

    @Provides
    fun provideStepDao(db: AppDatabase): StepDao = db.stepDao()

    @Provides
    fun provideRunHistoryDao(db: AppDatabase): RunHistoryDao = db.runHistoryDao()
}
