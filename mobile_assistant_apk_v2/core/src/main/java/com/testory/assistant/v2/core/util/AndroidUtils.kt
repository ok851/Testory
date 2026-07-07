package com.testory.assistant.v2.core.util

import android.content.Context
import android.content.Intent
import android.provider.Settings
import android.os.Build

/**
 * Helper utility for common Android checks and navigations.
 */
object AndroidUtils {

    fun isAccessibilityServiceEnabled(context: Context, serviceClass: String): Boolean {
        val enabledServices = Settings.Secure.getString(
            context.contentResolver,
            Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES
        ) ?: return false
        return enabledServices.split(':').any {
            it.equals(serviceClass, ignoreCase = true) ||
            it.endsWith(serviceClass, ignoreCase = true)
        }
    }

    fun openAccessibilitySettings(context: Context) {
        context.startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS).apply {
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        })
    }

    fun openAppSettings(context: Context) {
        context.startActivity(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS).apply {
            data = android.net.Uri.parse("package:${context.packageName}")
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        })
    }
}
