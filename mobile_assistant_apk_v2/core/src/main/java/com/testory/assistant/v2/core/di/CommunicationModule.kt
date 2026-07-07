package com.testory.assistant.v2.core.di

import android.content.Context
import com.testory.assistant.v2.core.communication.OkHttpPcSyncClient
import com.testory.assistant.v2.core.communication.PcSyncClient
import dagger.Module
import dagger.Provides
import dagger.hilt.InstallIn
import dagger.hilt.android.qualifiers.ApplicationContext
import dagger.hilt.components.SingletonComponent
import javax.inject.Singleton

@Module
@InstallIn(SingletonComponent::class)
object CommunicationModule {

    @Provides
    @Singleton
    fun providePcSyncClient(
        @ApplicationContext context: Context
    ): PcSyncClient {
        return OkHttpPcSyncClient(context)
    }
}
