package com.testory.assistant.v2.core.model

import kotlinx.serialization.Serializable

/**
 * 设备信息 — 用于 PC 端识别和管理。
 */
@Serializable
data class DeviceInfo(
    val deviceId: String = "",
    val deviceName: String = "",
    val model: String = "",
    val manufacturer: String = "",
    val androidVersion: String = "",
    val sdkInt: Int = 0,
    val screenWidth: Int = 0,
    val screenHeight: Int = 0,
    val densityDpi: Int = 0,
    /** 屏幕方向: 0=竖屏, 1=横屏 */
    val orientation: Int = 0,
    /** 电池电量 (0-100) */
    val batteryLevel: Int = 100,
    /** 是否充电中 */
    val isCharging: Boolean = false,
    /** WiFi 连接名 */
    val wifiSsid: String = "",
    /** 设备 IP 地址 */
    val ipAddress: String = "",
    /** 当前前台应用包名 */
    val currentPackageName: String = "",
    /** 当前前台 Activity */
    val currentActivity: String = ""
)

/**
 * 录制会话状态
 */
enum class RecordingState {
    IDLE,
    RECORDING,
    PAUSED,
    SAVING
}

/**
 * 回放执行状态
 */
enum class ReplayState {
    IDLE,
    RUNNING,
    PAUSED,
    COMPLETED,
    FAILED,
    CANCELLED
}

/**
 * 投屏状态
 */
enum class MirrorState {
    DISCONNECTED,
    CONNECTING,
    STREAMING,
    ERROR
}

/**
 * 单个步骤回放结果
 */
@Serializable
data class StepResult(
    val stepIndex: Int = 0,
    val stepId: String = "",
    val success: Boolean = false,
    val errorMessage: String = "",
    val durationMs: Long = 0,
    /** 步骤描述 */
    val stepDescription: String = "",
    /** 实际使用的定位策略 */
    val actualStrategy: String = "",
    /** 实际点击的坐标 */
    val actualCoordinate: ScreenCoordinate? = null
)

/**
 * 设备与 PC 的连接状态
 */
enum class PcConnectionState {
    DISCONNECTED,
    CONNECTING,
    CONNECTED,
    RECONNECTING
}
