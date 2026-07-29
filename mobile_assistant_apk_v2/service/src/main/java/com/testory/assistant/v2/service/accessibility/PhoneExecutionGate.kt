package com.testory.assistant.v2.service.accessibility

import java.util.concurrent.atomic.AtomicBoolean

/**
 * 串行化本机回放：UI 回放与 PC job（run_steps）互斥。
 */
object PhoneExecutionGate {
    private val busy = AtomicBoolean(false)

    fun tryAcquire(): Boolean = busy.compareAndSet(false, true)

    fun release() {
        busy.set(false)
    }

    fun isBusy(): Boolean = busy.get()
}
