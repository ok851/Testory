/**
 * 全站壳：导航预取。桌面端换页不做 opacity/底色强刷，避免「关灯」黑蒙版。
 */
(function () {
    'use strict';

    var prefetched = Object.create(null);
    var isDesktop = false;

    try {
        if (document.body && document.body.classList.contains('testory-desktop-client')) {
            isDesktop = true;
            document.documentElement.classList.add('testory-desktop-html', 'testory-no-view-transition');
        }
    } catch (e) {}

    function themeGapColor() {
        var dark = document.documentElement.classList.contains('dark');
        if (isDesktop) {
            /* 桌面 WebView 间隙用接近页面的底色，暗色主题深底、浅色主题浅底 */
            return dark ? '#030712' : '#f9fafb';
        }
        return dark ? '#030712' : '#f9fafb';
    }

    function paintGapBg() {
        try {
            var gap = themeGapColor();
            document.documentElement.style.backgroundColor = gap;
            /* 不强制改 body，避免与模块白卡片/主题 CSS 打架 */
        } catch (e) {}
    }

    function syncWebviewBgSafe() {
        paintGapBg();
        if (!isDesktop) return;
        try {
            var a = window.pywebview && window.pywebview.api;
            if (!a || !a.set_chrome_background) return;
            var gap = themeGapColor();
            setTimeout(function () {
                try { a.set_chrome_background(gap); } catch (e) {}
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

    /* 桌面端：换页点击不再刷深色底 / route-pending 蒙版感 */
    document.addEventListener('click', function (e) {
        if (isDesktop) return;
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
        paintGapBg();
        document.documentElement.classList.add('testory-route-pending');
    }, true);

    window.addEventListener('pageshow', function () {
        document.documentElement.classList.remove('testory-route-pending');
        syncWebviewBgSafe();
    });

    document.addEventListener('DOMContentLoaded', function () {
        document.documentElement.classList.remove('testory-route-pending');
        syncWebviewBgSafe();
    });

    window.addEventListener('pywebviewready', syncWebviewBgSafe);
})();
