/**
 * 全站壳：导航预取；桌面换页防白闪（WebView 深色间隙底，不整页 opacity 关灯）
 */
(function () {
    'use strict';

    try {
        // 尽早同步 html 底色，避免换页/渲染间隙出现白屏闪烁
        (function () {
            try {
                document.documentElement.style.backgroundColor =
                    (document.documentElement.classList.contains('dark') || localStorage.getItem('ui_theme') === 'dark')
                        ? '#030712'
                        : '#f9fafb';
            } catch (e) {}
        })();
    } catch (e) {}
    var prefetched = Object.create(null);
    var isDesktop = false;
    /* 换页无文档间隙：必须用深色，近白 (#f9fafb) 会被看成白屏闪烁 */
    var DESKTOP_GAP = '#050816';

    try {
        if (document.body && document.body.classList.contains('testory-desktop-client')) {
            isDesktop = true;
            document.documentElement.classList.add('testory-desktop-html', 'testory-no-view-transition');
        }
    } catch (e) {}

    function pageBgColor() {
        return document.documentElement.classList.contains('dark') ? '#030712' : '#f9fafb';
    }

    function paintPageBg() {
        try {
            document.documentElement.style.backgroundColor = pageBgColor();
        } catch (e) {}
    }

    function syncWebviewGapSafe() {
        paintPageBg();
        if (!isDesktop) return;
        try {
            var a = window.pywebview && window.pywebview.api;
            if (!a || !a.set_chrome_background) return;
            /* 只异步设深色间隙，禁止在点击路径同步调用（会死锁） */
            setTimeout(function () {
                try { a.set_chrome_background(DESKTOP_GAP); } catch (e) {}
            }, 0);
        } catch (e) {}
    }

    function mayPrefetch(url) {
        if (!url || prefetched[url]) return false;
        try {
            var u = new URL(url, window.location.href);
            if (u.origin !== window.location.origin) return false;
            if (u.pathname === window.location.pathname && u.search === window.location.search) return false;
            return u.pathname.indexOf('/api/') !== 0;
        } catch (e) {
            return false;
        }
    }

    function prefetchUrl(url) {
        if (!mayPrefetch(url)) return;
        prefetched[url] = 1;
        var link = document.createElement('link');
        link.rel = 'prefetch';
        link.href = url;
        link.as = 'document';
        document.head.appendChild(link);
    }

    document.addEventListener('mouseover', function (e) {
        var a = e.target && e.target.closest
            ? e.target.closest('#testoryMainNav a[href^="/"]')
            : null;
        if (a && a.href) prefetchUrl(a.href);
    }, { passive: true });

    document.addEventListener('focusin', function (e) {
        var a = e.target && e.target.closest
            ? e.target.closest('#testoryMainNav a[href^="/"]')
            : null;
        if (a && a.href) prefetchUrl(a.href);
    });

    document.addEventListener('click', function (e) {
        var a = e.target && e.target.closest
            ? e.target.closest('a[href^="/"]')
            : null;
        if (!a || !a.href) return;
        if (e.defaultPrevented || e.button !== 0) return;
        if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
        if (a.target && a.target !== '_self') return;
        try {
            var u = new URL(a.href, window.location.href);
            if (u.origin !== window.location.origin) return;
            if (u.pathname.indexOf('/api/') === 0) return;
            if (u.pathname === window.location.pathname && u.search === window.location.search) return;
        } catch (err) {
            return;
        }
        /* 桌面：换页前异步把 WebView 间隙压成深色，防白闪；不改 body、不整页隐藏 */
        if (isDesktop) {
            syncWebviewGapSafe();
            return;
        }
        paintPageBg();
        document.documentElement.classList.add('testory-route-pending');
    }, true);

    window.addEventListener('pageshow', function () {
        document.documentElement.classList.remove('testory-route-pending');
        syncWebviewGapSafe();
    });

    document.addEventListener('DOMContentLoaded', function () {
        document.documentElement.classList.remove('testory-route-pending');
        syncWebviewGapSafe();
    });

    window.addEventListener('pywebviewready', syncWebviewGapSafe);
})();
