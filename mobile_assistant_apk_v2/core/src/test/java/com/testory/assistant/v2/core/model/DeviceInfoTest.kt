package com.testory.assistant.v2.core.model

import org.junit.Assert.*
import org.junit.Test

class DeviceInfoTest {

    @Test
    fun `DeviceInfo should store basic fields`() {
        val device = DeviceInfo(
            deviceId = "abc123",
            model = "Pixel 6",
            brand = "Google",
            androidVersion = "14",
            screenWidth = 1080,
            screenHeight = 2400,
            dpi = 420
        )
        assertEquals("abc123", device.deviceId)
        assertEquals("Pixel 6", device.model)
        assertEquals("Google", device.brand)
        assertEquals("14", device.androidVersion)
        assertEquals(1080, device.screenWidth)
        assertEquals(2400, device.screenHeight)
        assertEquals(420, device.dpi)
    }

    @Test
    fun `DeviceInfo should serialize and deserialize`() {
        val device = DeviceInfo(
            deviceId = "serial-001",
            model = "Galaxy S24",
            brand = "Samsung",
            androidVersion = "14",
            screenWidth = 1080,
            screenHeight = 2340,
            dpi = 450
        )

        val json = device.toJson()
        val restored = DeviceInfo.fromJson(json)

        assertEquals(device.deviceId, restored.deviceId)
        assertEquals(device.model, restored.model)
        assertEquals(device.screenWidth, restored.screenWidth)
        assertEquals(device.screenHeight, restored.screenHeight)
    }
}
