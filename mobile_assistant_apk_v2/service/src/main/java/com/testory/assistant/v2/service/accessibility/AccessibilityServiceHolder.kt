package com.testory.assistant.v2.service.accessibility

import android.util.Log

/**
 * AssistantAccessibilityService 的单例持有者。
 *
 * AccessibilityService 由 Android 系统绑定/解绑，无法通过 Hilt 直接注入到 ViewModel。
 * 使用此对象在 Service 生命周期内暴露当前实例，供录制/回放模块调用。
 */
object AccessibilityServiceHolder {

    private const val TAG = "AssistantServiceHolder"

    @Volatile
    private var _instance: AssistantAccessibilityService? = null

    val instance: AssistantAccessibilityService? get() = _instance

    fun attach(service: AssistantAccessibilityService) {
        Log.i(TAG, "Accessibility service attached")
        _instance = service
    }

    fun detach(service: AssistantAccessibilityService) {
        if (_instance === service) {
            Log.i(TAG, "Accessibility service detached")
            _instance = null
        }
    }
}
