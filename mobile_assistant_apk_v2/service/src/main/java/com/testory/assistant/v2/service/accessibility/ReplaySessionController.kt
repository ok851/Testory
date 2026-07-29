package com.testory.assistant.v2.service.accessibility

import kotlinx.coroutines.CompletableDeferred
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicReference

/**
 * 回放会话控制 — 悬浮条 Pause/Resume 与 ReplayViewModel 共享。
 */
object ReplaySessionController {
    private val paused = AtomicBoolean(false)
    private val gate = AtomicReference<CompletableDeferred<Unit>?>(null)

    fun reset() {
        paused.set(false)
        gate.getAndSet(null)?.complete(Unit)
    }

    fun requestPause() {
        paused.set(true)
    }

    fun requestResume() {
        paused.set(false)
        gate.getAndSet(null)?.complete(Unit)
    }

    fun isPaused(): Boolean = paused.get()

    /**
     * 若处于暂停，挂起直到 resume / cancel。
     * @return false 表示被取消（gate 被 complete 以外的方式结束时仍返回 true；用 Job cancel 中断）
     */
    suspend fun awaitIfPaused() {
        if (!paused.get()) return
        val deferred = CompletableDeferred<Unit>()
        gate.set(deferred)
        // 双检：resume 可能已在设置 deferred 前到达
        if (!paused.get()) {
            gate.compareAndSet(deferred, null)
            return
        }
        deferred.await()
    }
}
