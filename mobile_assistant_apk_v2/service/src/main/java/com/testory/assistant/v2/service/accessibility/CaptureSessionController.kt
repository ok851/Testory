package com.testory.assistant.v2.service.accessibility

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/**
 * 跨 Activity 的元素捕获会话：悬浮窗捕获完成后把结果交给用例详情页。
 */
object CaptureSessionController {

    enum class Kind { CREATE, REPICK }

    data class Request(
        val caseId: String,
        val afterIndex: Int = -1,
        val kind: Kind = Kind.CREATE,
        val stepId: String = ""
    )

    data class Result(
        val request: Request,
        val picked: PickedElement?
    )

    @Volatile
    var current: Request? = null
        private set

    private val _pending = MutableStateFlow<Result?>(null)
    val pending: StateFlow<Result?> = _pending.asStateFlow()

    fun begin(request: Request) {
        current = request
        _pending.value = null
    }

    fun complete(picked: PickedElement?) {
        val req = current ?: return
        current = null
        _pending.value = Result(req, picked)
    }

    fun cancel() {
        complete(null)
    }

    fun consume() {
        _pending.value = null
    }
}
