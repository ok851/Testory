package com.testory.assistant.v2.service.accessibility

import com.testory.assistant.v2.core.model.Locator
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.withTimeoutOrNull
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicReference

data class PickedElement(
    val locator: Locator,
    val label: String = ""
)

/**
 * 屏上点选拾取：开启后下一笔有效点击的节点信息回写给用例编辑。
 */
object PickModeController {
    private val active = AtomicBoolean(false)
    private val deferred = AtomicReference<CompletableDeferred<PickedElement?>>(null)

    fun isActive(): Boolean = active.get()

    fun startPick() {
        deferred.getAndSet(null)?.complete(null)
        deferred.set(CompletableDeferred())
        active.set(true)
    }

    fun cancel() {
        active.set(false)
        deferred.getAndSet(null)?.complete(null)
    }

    fun submit(picked: PickedElement) {
        if (!active.getAndSet(false)) return
        deferred.getAndSet(null)?.complete(picked)
    }

    suspend fun awaitPick(timeoutMs: Long): PickedElement? {
        val d = deferred.get() ?: return null
        return withTimeoutOrNull(timeoutMs) { d.await() }.also {
            active.set(false)
        }
    }
}
