package com.testory.assistant.v2

import android.app.Application
import dagger.hilt.android.HiltAndroidApp

@HiltAndroidApp
class TestoryApp : Application() {

    override fun onCreate() {
        super.onCreate()
        instance = this
    }

    companion object {
        lateinit var instance: TestoryApp
            private set
    }
}
