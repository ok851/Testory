/**
 * 桌面无边框窗口控制（pywebview js_api）
 *
 * 功能：
 *  - 绑定最小化 / 最大化 / 关闭按钮
 *  - 标题栏双击切换最大化
 *  - 边框 8 向拖拽缩放（前端 pointer 跟踪 + set_bounds，不依赖系统白框）
 *  - 窗口状态同步（最大化图标切换）
 *  - 页面卸载前持久化窗口几何信息
 */
(function () {
    'use strict';

    if (!document.body.classList.contains('testory-frameless-shell')) return;

    var _pywebviewReady = !!(window.pywebview && window.pywebview.api);
    var _maximized = false;
    var _resizing = null;
    var _rafPending = false;
    var RESIZE_EDGES = [
        'left', 'right', 'top', 'bottom',
        'top-left', 'top-right', 'bottom-left', 'bottom-right'
    ];
    var MIN_W = 1024;
    var MIN_H = 640;

    function api() {
        return window.pywebview && window.pywebview.api;
    }

    function asPromise(value) {
        if (value && typeof value.then === 'function') return value;
        return Promise.resolve(value);
    }

    var ICON_MAX = '<svg width="10" height="10" viewBox="0 0 10 10" fill="none"><rect x="0.5" y="0.5" width="9" height="9" rx="1" stroke="currentColor"/></svg>';
    var ICON_RESTORE = '<svg width="10" height="10" viewBox="0 0 10 10" fill="none"><rect x="0.5" y="2.5" width="7" height="7" rx="1" stroke="currentColor"/><path d="M2.5 2.5V1H9.5V7.5H8" stroke="currentColor" fill="none"/></svg>';

    function updateMaximizeIcon() {
        var btn = document.querySelector('[data-win="maximize"]');
        if (!btn) return;
        btn.innerHTML = _maximized ? ICON_RESTORE : ICON_MAX;
        btn.title = _maximized ? '向下还原' : '最大化';
        document.body.classList.toggle('testory-window-maximized', !!_maximized);
    }

    function applyBounds(left, top, width, height) {
        var a = api();
        if (!a || !a.set_bounds) return;
        try {
            a.set_bounds(left, top, width, height);
        } catch (err) {}
    }

    function computeBounds(state, screenX, screenY) {
        var dx = screenX - state.sx;
        var dy = screenY - state.sy;
        var left = state.left;
        var top = state.top;
        var right = state.left + state.width;
        var bottom = state.top + state.height;
        var edge = state.edge;
        var moveLeft = edge === 'left' || edge === 'top-left' || edge === 'bottom-left';
        var moveRight = edge === 'right' || edge === 'top-right' || edge === 'bottom-right';
        var moveTop = edge === 'top' || edge === 'top-left' || edge === 'top-right';
        var moveBottom = edge === 'bottom' || edge === 'bottom-left' || edge === 'bottom-right';
        if (moveLeft) left = state.left + dx;
        if (moveRight) right = state.left + state.width + dx;
        if (moveTop) top = state.top + dy;
        if (moveBottom) bottom = state.top + state.height + dy;
        if (right - left < MIN_W) {
            if (moveLeft) left = right - MIN_W;
            else right = left + MIN_W;
        }
        if (bottom - top < MIN_H) {
            if (moveTop) top = bottom - MIN_H;
            else bottom = top + MIN_H;
        }
        return {
            left: Math.round(left),
            top: Math.round(top),
            width: Math.round(right - left),
            height: Math.round(bottom - top)
        };
    }

    function onResizePointerMove(e) {
        if (!_resizing) return;
        var next = computeBounds(_resizing, e.screenX, e.screenY);
        _resizing.pending = next;
        if (_rafPending) return;
        _rafPending = true;
        requestAnimationFrame(function () {
            _rafPending = false;
            if (!_resizing || !_resizing.pending) return;
            var b = _resizing.pending;
            _resizing.pending = null;
            applyBounds(b.left, b.top, b.width, b.height);
        });
    }

    function onResizePointerUp(e) {
        if (!_resizing) return;
        try {
            if (e && e.target && e.target.releasePointerCapture) {
                e.target.releasePointerCapture(e.pointerId);
            }
        } catch (err) {}
        if (_resizing.pending) {
            var b = _resizing.pending;
            applyBounds(b.left, b.top, b.width, b.height);
        }
        _resizing = null;
        _rafPending = false;
        window.removeEventListener('pointermove', onResizePointerMove, true);
        window.removeEventListener('pointerup', onResizePointerUp, true);
        window.removeEventListener('pointercancel', onResizePointerUp, true);
    }

    function startResize(edge, e) {
        var a = api();
        if (!a || !a.get_bounds || !a.set_bounds) return;
        var sx = e.screenX;
        var sy = e.screenY;
        asPromise(a.get_bounds()).then(function (b) {
            if (!b || _maximized) return;
            _resizing = {
                edge: edge,
                sx: sx,
                sy: sy,
                left: Number(b.left) || 0,
                top: Number(b.top) || 0,
                width: Number(b.width) || window.outerWidth,
                height: Number(b.height) || window.outerHeight,
                pending: null
            };
            window.addEventListener('pointermove', onResizePointerMove, true);
            window.addEventListener('pointerup', onResizePointerUp, true);
            window.addEventListener('pointercancel', onResizePointerUp, true);
        });
    }

    function ensureResizeHandles() {
        if (document.getElementById('testoryResizeHandles')) return;
        var root = document.createElement('div');
        root.id = 'testoryResizeHandles';
        root.setAttribute('aria-hidden', 'true');
        RESIZE_EDGES.forEach(function (edge) {
            var el = document.createElement('div');
            el.className = 'testory-resize-handle testory-resize-handle--' + edge;
            el.dataset.edge = edge;
            el.addEventListener('pointerdown', function (e) {
                if (e.button !== 0) return;
                if (_maximized) return;
                e.preventDefault();
                e.stopPropagation();
                e.stopImmediatePropagation();
                try {
                    el.setPointerCapture(e.pointerId);
                } catch (err) {}
                startResize(edge, e);
            });
            root.appendChild(el);
        });
        document.body.appendChild(root);
    }

    function bindControls() {
        var bar = document.getElementById('testoryDesktopTitlebar');
        if (!bar) return;

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

        var drag = bar.querySelector('.testory-desktop-chrome__drag');
        if (drag && !drag.dataset.dbound) {
            drag.dataset.dbound = '1';
            if (!drag.classList.contains('pywebview-drag-region')) {
                drag.classList.add('pywebview-drag-region');
            }
            drag.addEventListener('dblclick', function (e) {
                if (e.target !== drag && e.target.tagName !== 'IMG' && e.target.tagName !== 'SPAN') return;
                var a = api();
                if (!a || !a.toggle_maximize) return;
                _maximized = !_maximized;
                updateMaximizeIcon();
                a.toggle_maximize();
            });
        }

        ensureResizeHandles();
        updateMaximizeIcon();
    }

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

    document.addEventListener('DOMContentLoaded', bindControls);

    if (_pywebviewReady) {
        bindControls();
    } else {
        window.addEventListener('pywebviewready', function () {
            _pywebviewReady = true;
            bindControls();
        });
    }

    window.addEventListener('beforeunload', persistGeometry);

    window.addEventListener('resize', function () {
        var isMax = window.outerWidth >= screen.availWidth - 2 && window.outerHeight >= screen.availHeight - 2;
        if (isMax !== _maximized) {
            _maximized = isMax;
            updateMaximizeIcon();
        }
    });
})();
