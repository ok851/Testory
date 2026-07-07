package com.testory.assistant.v2.core.di

import dagger.Module
import dagger.hilt.InstallIn
import dagger.hilt.android.components.ViewModelComponent

/**
 * Repository 绑定由 @Inject constructor 自动提供，无需手动 @Provides。
 * 保留此模块以备未来需要手动绑定的场景。
 */
@Module
@InstallIn(ViewModelComponent::class)
object RepositoryModule
