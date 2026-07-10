/**
 * 桌面无边框窗口控制（pywebview js_api）
 *
 * 功能：
 *  - 绑定最小化 / 最大化 / 关闭按钮
 *  - 标题栏双击切换最大化
 *  - 窗口状态同步（最大化图标切换）
 *  - 页面卸载前持久化窗口几何信息
 */
(function () {
    'use strict';

    if (!document.body.classList.contains('testory-frameless-shell')) return;

    var _pywebviewReady = !!(window.pywebview && window.pywebview.api);
    var _maximized = false;

    function api() {
        return window.pywebview && window.pywebview.api;
    }

    /* ---- 图标切换 ---- */
    var ICON_MIN = '<svg width="10" height="10" viewBox="0 0 10 10" fill="none"><rect y="4.5" width="10" height="1" rx="0.5" fill="currentColor"/></svg>';
    var ICON_MAX = '<svg width="10" height="10" viewBox="0 0 10 10" fill="none"><rect x="0.5" y="0.5" width="9" height="9" rx="1" stroke="currentColor"/></svg>';
    var ICON_RESTORE = '<svg width="10" height="10" viewBox="0 0 10 10" fill="none"><rect x="0.5" y="2.5" width="7" height="7" rx="1" stroke="currentColor"/><path d="M2.5 2.5V1H9.5V7.5H8" stroke="currentColor" fill="none"/></svg>';
    var ICON_CLOSE = '<svg width="10" height="10" viewBox="0 0 10 10" fill="none"><path d="M1 1l8 8M9 1L1 9" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/></svg>';

    function updateMaximizeIcon() {
        var btn = document.querySelector('[data-win="maximize"]');
        if (!btn) return;
        btn.innerHTML = _maximized ? ICON_RESTORE : ICON_MAX;
        btn.title = _maximized ? '向下还原' : '最大化';
    }

    /* ---- 事件绑定 ---- */
    function bindControls() {
        var bar = document.getElementById('testoryDesktopTitlebar');
        if (!bar) return;

        /* 按钮点击 */
        bar.querySelectorAll('[data-win]').forEach(function (btn) {
            if (btn.dataset.bound) return;
            btn.dataset.bound = '1';
            btn.addEventListener('click', function (e) {
                e.preventDefault();
                e.stopPropagation();
                var action = btn.getAttribute('data-win');
                var a = api();
                if (!a) return;
                if (action === 'minimize' && a.minimize) {
                    a.minimize();
                } else if (action === 'maximize' && a.toggle_maximize) {
                    _maximized = !_maximized;
                    updateMaximizeIcon();
                    try {
                        var result = a.toggle_maximize();
                        if (result && typeof result.then === 'function') {
                            result.then(function (val) {
                                if (typeof val === 'boolean') {
                                    _maximized = val;
                                    updateMaximizeIcon();
                                }
                            });
                        }
                    } catch (err) {}
                } else if (action === 'close' && a.close) {
                    a.close();
                }
            });
        });

        /* 标题栏双击切换最大化 */
        var drag = bar.querySelector('.testory-desktop-chrome__drag');
        if (drag && !drag.dataset.dbBound) {
            drag.dataset.dbBound = '1';
            drag.addEventListener('dblclick', function (e) {
                /* 仅在拖拽区域本身双击时触发，不干扰子元素 */
                if (e.target !== drag && e.target.tagName !== 'IMG' && e.target.tagName !== 'SPAN') return;
                var a = api();
                if (!a || !a.toggle_maximize) return;
                _maximized = !_maximized;
                updateMaximizeIcon();
                a.toggle_maximize();
            });
        }

        updateMaximizeIcon();
    }

    /* ---- 几何信息持久化 ---- */
    function persistGeometry() {
        var a = api();
        if (!a || !a.save_geometry) return;
        try {
            a.save_geometry(JSON.stringify({
                width: window.innerWidth,
                height: window.innerHeight,
                maximized: _maximized
            }));
        } catch (e) {}
    }

    /* ---- 初始化 ---- */
    document.addEventListener('DOMContentLoaded', bindControls);

    /* pywebviewready 可能已触发，也可能未触发，两种情况都处理 */
    if (_pywebviewReady) {
        bindControls();
    } else {
        window.addEventListener('pywebviewready', function () {
            _pywebviewReady = true;
            bindControls();
        });
    }

    window.addEventListener('beforeunload', persistGeometry);

    /* 监听窗口大小变化，更新最大化状态图标 */
    window.addEventListener('resize', function () {
        /* 通过比较窗口大小与屏幕大小来判断是否最大化 */
        var isMax = window.outerWidth >= screen.availWidth - 2 && window.outerHeight >= screen.availHeight - 2;
        if (isMax !== _maximized) {
            _maximized = isMax;
            updateMaximizeIcon();
        }
    });
})();
