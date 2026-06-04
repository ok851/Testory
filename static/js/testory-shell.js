/**
 * 全站壳：导航预取、切换轻提示（仍整页导航，依赖 CSS View Transition 减轻闪烁）
 */
(function () {
    'use strict';

    var prefetched = Object.create(null);

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
            ? e.target.closest('#testoryMainNav a[href^="/"]')
            : null;
        if (!a || !a.href) return;
        if (e.defaultPrevented || e.button !== 0) return;
        if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
        if (a.target && a.target !== '_self') return;
        try {
            var u = new URL(a.href, window.location.href);
            if (u.origin !== window.location.origin) return;
            if (u.pathname === window.location.pathname && u.search === window.location.search) return;
        } catch (err) {
            return;
        }
        document.documentElement.classList.add('testory-route-pending');
    }, true);

    window.addEventListener('pageshow', function () {
        document.documentElement.classList.remove('testory-route-pending');
    });
})();
