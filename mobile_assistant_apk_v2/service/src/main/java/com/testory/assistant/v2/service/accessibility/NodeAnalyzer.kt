package com.testory.assistant.v2.service.accessibility

import android.graphics.Rect
import android.view.accessibility.AccessibilityNodeInfo
import com.testory.assistant.v2.core.model.NodeInfo
import com.testory.assistant.v2.core.model.ScreenRect
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

        return NodeInfo(
            className = node.className?.toString() ?: "",
            text = node.text?.toString() ?: "",
            contentDescription = node.contentDescription?.toString() ?: "",
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
            if (!node.text.isNullOrBlank()) score += 2
            if (!node.contentDescription.isNullOrBlank()) score += 2
            if (!node.viewIdResourceName.isNullOrBlank()) score += 3
            return score
        }

        fun visit(node: AccessibilityNodeInfo) {
            node.getBoundsInScreen(rect)
            if (!rect.contains(x, y)) return

            val area = (rect.right - rect.left) * (rect.bottom - rect.top)
            if (area > 0) {
                val boost = identityBoost(node)
                val betterArea = area < bestArea
                val sameAreaRicher = area == bestArea && boost > 0 &&
                    (bestSnapshot == null || boost > identityBoostScore(bestSnapshot))
                // 叶子或有身份信息的节点才更新 best，避免停在巨大 WebView/DecorView
                val meaningful = boost > 0 || node.childCount == 0
                if (meaningful && (betterArea || sameAreaRicher)) {
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
        return bestSnapshot
    }

    private fun identityBoostScore(info: NodeInfo?): Int {
        if (info == null) return 0
        var score = 0
        if (info.isClickable || info.isEditable) score += 3
        if (info.text.isNotBlank()) score += 2
        if (info.contentDescription.isNotBlank()) score += 2
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
