/**
 * 桌面无边框窗口控制（pywebview js_api）
 */
(function () {
    'use strict';

    if (!document.body.classList.contains('testory-frameless-shell')) return;

    function api() {
        return window.pywebview && window.pywebview.api;
    }

    function bindControls() {
        var bar = document.getElementById('testoryDesktopTitlebar');
        if (!bar) return;
        bar.querySelectorAll('[data-win]').forEach(function (btn) {
            btn.addEventListener('click', function (e) {
                e.preventDefault();
                var action = btn.getAttribute('data-win');
                var a = api();
                if (!a) return;
                if (action === 'minimize' && a.minimize) a.minimize();
                else if (action === 'maximize' && a.toggle_maximize) a.toggle_maximize();
                else if (action === 'close' && a.close) a.close();
            });
        });
    }

    function persistGeometry() {
        var a = api();
        if (!a || !a.save_geometry) return;
        try {
            a.save_geometry(JSON.stringify({
                width: window.innerWidth,
                height: window.innerHeight,
                maximized: !!document.documentElement.dataset.testoryMaximized
            }));
        } catch (e) {}
    }

    window.addEventListener('beforeunload', persistGeometry);
    document.addEventListener('DOMContentLoaded', bindControls);

    window.addEventListener('pywebviewready', bindControls);
})();
