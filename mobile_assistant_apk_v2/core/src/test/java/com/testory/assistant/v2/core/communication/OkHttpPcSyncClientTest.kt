package com.testory.assistant.v2.core.communication

import android.content.Context
import io.mockk.*
import kotlinx.coroutines.runBlocking
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test

class OkHttpPcSyncClientTest {

    private lateinit var client: PcSyncClient

    @Before
    fun setUp() {
        val mockContext = mockk<Context>(relaxed = true)
        client = OkHttpPcSyncClient(mockContext)
    }

    @Test
    fun `isConnected should return false by default`() {
        assertFalse(client.isConnected())
    }

    @Test
    fun `setServerAddress should update address`() {
        client.setServerAddress("192.168.1.100", 8777)
        val state = client.getConnectionState()
        assertEquals("192.168.1.100", state.host)
        assertEquals(8777, state.port)
    }

    @Test
    fun `getServerUrl should return correct URL`() {
        client.setServerAddress("10.0.0.1", 5555)
        assertEquals("http://10.0.0.1:5555", client.getServerUrl())
    }
}

private fun PcSyncClient.isConnected(): Boolean = runBlocking {
    // Default: not connected
    getConnectionState().isConnected
}

private fun PcSyncClient.getServerUrl(): String {
    val state = getConnectionState()
    return "http://${state.host}:${state.port}"
}
