package com.testory.assistant.v2.service.accessibility

import android.graphics.Rect
import android.os.Build
import android.view.accessibility.AccessibilityNodeInfo
import com.testory.assistant.v2.core.model.NodeInfo
import com.testory.assistant.v2.core.model.ScreenRect
import java.util.ArrayDeque
import javax.inject.Inject
import javax.inject.Singleton

/**
 * 节点分析器 — 从 AccessibilityNodeInfo 提取结构化数据。
 *
 * 移植自旧版 NodeLocatorHelper + OperationNodeLocator 的核心逻辑：
 * - 提取 NodeInfo (纯数据) 防止持有原生引用导致内存泄漏
 * - 构建 UI 树 (供 AI 分析和 PC 端展示)
 * - 查找最小面积的最深可交互节点 (SoloPi PositionLocator 子集)
 */
@Singleton
class NodeAnalyzer @Inject constructor() {

    /**
     * 从 AccessibilityNodeInfo 提取纯数据 NodeInfo。
     * 不持有原生引用，避免内存泄漏。
     */
    fun extractNodeInfo(node: AccessibilityNodeInfo): NodeInfo {
        val rect = Rect()
        node.getBoundsInScreen(rect)

        val rawText = node.text?.toString()?.trim().orEmpty()
        // EditText 未输入时，可见文案往往在 hint 上（如「输入手机号码」）
        val hint = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            node.hintText?.toString()?.trim().orEmpty()
        } else {
            ""
        }
        val desc = node.contentDescription?.toString()?.trim().orEmpty()
        val effectiveText = rawText.ifBlank { hint }.ifBlank { desc }

        return NodeInfo(
            className = node.className?.toString() ?: "",
            text = effectiveText,
            contentDescription = desc.ifBlank { hint },
            resourceId = node.viewIdResourceName ?: "",
            packageName = node.packageName?.toString() ?: "",
            bounds = ScreenRect(rect.left, rect.top, rect.right, rect.bottom),
            isClickable = node.isClickable,
            isEditable = node.isEditable,
            isChecked = node.isChecked,
            windowId = node.windowId,
            childCount = node.childCount
        )
    }

    /**
     * 查找最小面积的最深可操作节点。
     * 借鉴 SoloPi PositionLocator: 在坐标位置下找到最小的可点击叶子节点。
     *
     * 修复：旧实现在面积未改善时 `continue` 跳过子节点遍历，导致只能命中大容器。
     */
    fun findBestNode(root: AccessibilityNodeInfo, x: Int, y: Int): NodeInfo? {
        var bestSnapshot: NodeInfo? = null
        var bestArea = Int.MAX_VALUE
        val rect = Rect()
        val obtained = mutableListOf<AccessibilityNodeInfo>()

        fun identityBoost(node: AccessibilityNodeInfo): Int {
            var score = 0
            if (node.isClickable || node.isEditable || node.isCheckable) score += 3
            // 有可读标签的节点大幅加分，避免桌面图标只录到无字 ImageView
            if (!node.text.isNullOrBlank()) score += 8
            if (!node.contentDescription.isNullOrBlank()) score += 6
            if (!node.viewIdResourceName.isNullOrBlank()) score += 3
            return score
        }

        fun visit(node: AccessibilityNodeInfo) {
            node.getBoundsInScreen(rect)
            if (!rect.contains(x, y)) return

            val area = (rect.right - rect.left) * (rect.bottom - rect.top)
            if (area > 0 && area < 500_000) {
                val boost = identityBoost(node)
                val score = boost * 80_000 - area
                val bestScore = (bestSnapshot?.let { identityBoostScore(it) } ?: 0) * 80_000 -
                    if (bestArea == Int.MAX_VALUE) 0 else bestArea
                val meaningful = boost > 0 || node.childCount == 0
                if (meaningful && (bestSnapshot == null || score > bestScore)) {
                    bestArea = area
                    bestSnapshot = extractNodeInfo(node)
                }
            }

            // 始终深入包含该点的子节点
            for (i in 0 until node.childCount) {
                val child = node.getChild(i) ?: continue
                obtained.add(child)
                visit(child)
            }
        }

        try {
            visit(root)
        } finally {
            for (n in obtained) {
                try { n.recycle() } catch (_: Exception) {}
            }
        }
        // 桌面图标常点到无文字的 ImageView；补全同组 Text 标签（应用名）
        return bestSnapshot?.let { enrichWithNearbyLabel(root, it) }
    }

    /**
     * 为无文本的节点补全附近/同组标签。
     * 桌面分页图标：点击命中图标 ImageView，应用名在兄弟 TextView 上。
     */
    fun enrichWithNearbyLabel(root: AccessibilityNodeInfo, info: NodeInfo): NodeInfo {
        if (info.text.isNotBlank() || info.contentDescription.isNotBlank()) return info
        val cx = info.bounds.centerX
        val cy = info.bounds.centerY
        if (cx <= 0 && cy <= 0) return info

        var bestLabel = ""
        var bestDist = Int.MAX_VALUE
        val iconW = (info.bounds.right - info.bounds.left).coerceAtLeast(48)
        val iconH = (info.bounds.bottom - info.bounds.top).coerceAtLeast(48)
        val rect = Rect()
        val stack = ArrayDeque<AccessibilityNodeInfo>()
        stack.add(AccessibilityNodeInfo.obtain(root))
        try {
            while (stack.isNotEmpty()) {
                val node = stack.removeFirst()
                try {
                    node.getBoundsInScreen(rect)
                    val label = readableLabel(node)
                    if (label.isNotBlank() && label.length in 1..48 && !looksLikeNoiseLabel(label)) {
                        val lx = rect.centerX()
                        val ly = rect.centerY()
                        val dx = kotlin.math.abs(lx - cx)
                        val dy = ly - cy
                        // 放宽：桌面图标标题可能在图标正下方较远，或同 cell 内
                        val nearHoriz = dx <= iconW + 80
                        val nearVert = dy in -iconH..(iconH + 280)
                        val overlaps = rect.contains(cx, cy)
                        val sameColumn = dx <= iconW / 2 + 24 && dy in 0..(iconH + 320)
                        if (overlaps || (nearHoriz && nearVert) || sameColumn) {
                            val dist = dx + kotlin.math.abs(dy)
                            // 优先正下方短标签（应用名）
                            val prefer = if (sameColumn && label.length <= 16) dist - 50 else dist
                            if (prefer < bestDist) {
                                bestDist = prefer
                                bestLabel = label
                            }
                        }
                    }
                    for (i in 0 until node.childCount) {
                        node.getChild(i)?.let { stack.add(it) }
                    }
                } finally {
                    try { node.recycle() } catch (_: Exception) {}
                }
            }
        } catch (_: Exception) { }

        if (bestLabel.isBlank()) return info
        return info.copy(
            text = bestLabel,
            contentDescription = info.contentDescription.ifBlank { bestLabel }
        )
    }

    private fun readableLabel(node: AccessibilityNodeInfo): String {
        val text = node.text?.toString()?.trim().orEmpty()
        if (text.isNotBlank()) return text
        val desc = node.contentDescription?.toString()?.trim().orEmpty()
        if (desc.isNotBlank()) return desc
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            return node.hintText?.toString()?.trim().orEmpty()
        }
        return ""
    }

    private fun looksLikeNoiseLabel(label: String): Boolean {
        val t = label.trim()
        if (t.isEmpty()) return true
        // 过滤纯数字、分页点、系统噪声
        if (t.all { it.isDigit() || it == '.' || it == '%' }) return true
        if (t in setOf(".", "…", "搜索", "Search", "文件夹")) return true
        return false
    }

    private fun identityBoostScore(info: NodeInfo?): Int {
        if (info == null) return 0
        var score = 0
        if (info.isClickable || info.isEditable) score += 3
        if (info.text.isNotBlank()) score += 8
        if (info.contentDescription.isNotBlank()) score += 6
        if (info.resourceId.isNotBlank()) score += 3
        return score
    }

    /**
     * 构建 UI 树 — 将 AccessibilityNodeInfo 树转换为纯数据结构。
     * 供 AI 分析、PC 端展示和视觉匹配使用。
     */
    fun buildUiTree(root: AccessibilityNodeInfo): UiTree {
        val nodes = buildUiNodes(root)
        return UiTree(nodes = nodes)
    }

    private fun buildUiNodes(node: AccessibilityNodeInfo): List<UiNode> {
        val nodes = mutableListOf<UiNode>()
        val rect = Rect()
        node.getBoundsInScreen(rect)

        // Only include meaningful nodes (those with bounds or text)
        val hasContent = rect.width() > 0 && rect.height() > 0

        if (hasContent || node.text?.isNotBlank() == true || node.contentDescription?.isNotBlank() == true) {
            val children = mutableListOf<UiNode>()
            for (i in 0 until node.childCount) {
                val child = node.getChild(i) ?: continue
                children.addAll(buildUiNodes(child))
            }

            nodes.add(
                UiNode(
                    className = node.className?.toString() ?: "",
                    text = node.text?.toString() ?: "",
                    contentDescription = node.contentDescription?.toString() ?: "",
                    resourceId = node.viewIdResourceName ?: "",
                    bounds = ScreenRect(rect.left, rect.top, rect.right, rect.bottom),
                    isClickable = node.isClickable,
                    isEditable = node.isEditable,
                    children = children
                )
            )
        } else {
            // Traverse children even if parent has no content
            for (i in 0 until node.childCount) {
                val child = node.getChild(i) ?: continue
                nodes.addAll(buildUiNodes(child))
            }
        }

        return nodes
    }

    /**
     * 从 UI 树中找到与给定文本匹配的节点。
     */
    fun findNodeByText(tree: UiTree, text: String): UiNode? {
        for (node in tree.nodes) {
            val found = findInNode(node, text)
            if (found != null) return found
        }
        return null
    }

    private fun findInNode(node: UiNode, text: String): UiNode? {
        if (node.text.contains(text, ignoreCase = true) ||
            node.contentDescription.contains(text, ignoreCase = true)) {
            return node
        }
        for (child in node.children) {
            val found = findInNode(child, text)
            if (found != null) return found
        }
        return null
    }

    /**
     * 获取坐标处的所有控件 (按深度排序)。
     */
    fun getNodesAt(root: AccessibilityNodeInfo, x: Int, y: Int): List<NodeInfo> {
        val results = mutableListOf<NodeInfo>()
        val rect = Rect()

        fun traverse(node: AccessibilityNodeInfo) {
            node.getBoundsInScreen(rect)
            if (rect.contains(x, y)) {
                results.add(extractNodeInfo(node))
                for (i in 0 until node.childCount) {
                    val child = node.getChild(i) ?: continue
                    traverse(child)
                }
            }
        }

        traverse(root)
        return results
    }
}
