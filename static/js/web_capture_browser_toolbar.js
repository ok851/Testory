/* 浏览器内可拖动网页捕获悬浮窗（待命/捕获控制，高亮由 highlight.js 负责） */
(function () {
    if (window.__uatWebCaptureToolbarLoaded) return;
    window.__uatWebCaptureToolbarLoaded = true;

    var API_BASE = '__API_BASE__';
    var SESSION = '__SESSION__';
    var armed = false;
    var host = null;
    var shadow = null;
    var drag = { active: false, ox: 0, oy: 0, x: 24, y: 24 };

    function el(id) {
        return shadow ? shadow.getElementById(id) : null;
    }

    function apiPost(path, body) {
        return fetch(API_BASE + path, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body || {})
        }).then(function (r) { return r.json(); });
    }

    function setArmed(on) {
        armed = !!on;
        var pill = el('uatWcPill');
        var armBtn = el('uatWcArm');
        var disarmBtn = el('uatWcDisarm');
        var status = el('uatWcStatus');
        if (pill) {
            pill.textContent = armed ? '● 捕获中' : '● 待命';
            pill.className = 'uat-wc-pill' + (armed ? ' on' : '');
        }
        if (armBtn) armBtn.disabled = armed;
        if (disarmBtn) disarmBtn.disabled = !armed;
        if (status && armed) {
            status.textContent = '悬停高亮目标元素，单击确认拾取。';
        } else if (status && !armed) {
            status.textContent = '打开待测页后点「开始捕获」进入拾取。';
        }
    }

    function syncArmStatus() {
        if (!API_BASE || !SESSION) return;
        fetch(API_BASE + '/api/web-capture/arm-status?session=' + encodeURIComponent(SESSION))
            .then(function (r) { return r.json(); })
            .then(function (j) {
                if (j && j.success) setArmed(!!j.armed);
            })
            .catch(function () {});
    }

    function armCapture() {
        apiPost('/api/web-capture/arm', { session_id: SESSION })
            .then(function (j) {
                if (j.success) {
                    setArmed(true);
                    var status = el('uatWcStatus');
                    if (status && j.message) status.textContent = j.message;
                } else {
                    alert(j.error || '开始捕获失败');
                }
            })
            .catch(function (e) { alert('开始捕获失败：' + e); });
    }

    function disarmCapture() {
        apiPost('/api/web-capture/disarm', { session_id: SESSION })
            .then(function () { setArmed(false); })
            .catch(function () { setArmed(false); });
    }

    function endCapture() {
        apiPost('/api/web-capture/stop', { session_id: SESSION })
            .then(function () {
                if (window.__uatWebCaptureDisarm) window.__uatWebCaptureDisarm();
                removeToolbar();
            })
            .catch(function () { removeToolbar(); });
    }

    function removeToolbar() {
        if (host && host.parentNode) host.parentNode.removeChild(host);
        window.__uatWebCaptureToolbarLoaded = false;
    }

    function onPointerDown(e) {
        if (!e.target || !e.target.closest || !e.target.closest('.uat-wc-drag')) return;
        drag.active = true;
        drag.ox = e.clientX - drag.x;
        drag.oy = e.clientY - drag.y;
        e.preventDefault();
    }

    function onPointerMove(e) {
        if (!drag.active || !host) return;
        drag.x = Math.max(8, Math.min(window.innerWidth - 120, e.clientX - drag.ox));
        drag.y = Math.max(8, Math.min(window.innerHeight - 80, e.clientY - drag.oy));
        host.style.left = drag.x + 'px';
        host.style.top = drag.y + 'px';
    }

    function onPointerUp() {
        drag.active = false;
    }

    function buildToolbar() {
        host = document.createElement('div');
        host.id = 'uat-web-capture-toolbar-host';
        host.style.cssText = 'position:fixed;left:24px;top:24px;z-index:2147483647;';
        shadow = host.attachShadow({ mode: 'open' });
        shadow.innerHTML = [
            '<style>',
            '.uat-wc-root{font-family:Segoe UI,system-ui,sans-serif;width:320px;background:#fff;',
            'border:1px solid #e5e7eb;border-radius:12px;box-shadow:0 8px 28px rgba(0,0,0,.18);overflow:hidden;}',
            '.uat-wc-drag{cursor:move;padding:10px 12px 6px;display:flex;align-items:center;gap:6px;',
            'border-bottom:1px solid #f1f5f9;user-select:none;}',
            '.uat-wc-title{font-size:13px;font-weight:700;color:#111827;}',
            '.uat-wc-badge{font-size:10px;font-weight:700;color:#dc2626;background:#fef2f2;',
            'padding:2px 6px;border-radius:4px;}',
            '.uat-wc-body{padding:8px 12px 10px;}',
            '.uat-wc-pill{display:inline-block;font-size:11px;font-weight:600;padding:3px 8px;',
            'border-radius:6px;background:#f1f5f9;color:#6b7280;margin-bottom:6px;}',
            '.uat-wc-pill.on{background:#ecfdf5;color:#16a34a;}',
            '.uat-wc-status{font-size:11px;color:#64748b;line-height:1.45;min-height:28px;margin-bottom:8px;}',
            '.uat-wc-actions{display:flex;flex-wrap:wrap;gap:6px;padding:0 12px 12px;}',
            '.uat-wc-btn{border:none;border-radius:8px;padding:7px 12px;font-size:12px;font-weight:600;cursor:pointer;}',
            '.uat-wc-btn-primary{background:#dc2626;color:#fff;}',
            '.uat-wc-btn-primary:disabled{opacity:.5;cursor:not-allowed;}',
            '.uat-wc-btn-secondary{background:#e5e7eb;color:#111827;}',
            '</style>',
            '<div class="uat-wc-root">',
            '<div class="uat-wc-drag"><span class="uat-wc-title">网页元素捕获</span><span class="uat-wc-badge">DOM</span></div>',
            '<div class="uat-wc-body">',
            '<div id="uatWcPill" class="uat-wc-pill">● 待命</div>',
            '<div id="uatWcStatus" class="uat-wc-status">打开待测页后点「开始捕获」进入拾取。</div>',
            '</div>',
            '<div class="uat-wc-actions">',
            '<button type="button" class="uat-wc-btn uat-wc-btn-primary" id="uatWcArm">开始捕获</button>',
            '<button type="button" class="uat-wc-btn uat-wc-btn-secondary" id="uatWcDisarm" disabled>停止捕获</button>',
            '<button type="button" class="uat-wc-btn uat-wc-btn-secondary" id="uatWcEnd">结束捕获</button>',
            '</div></div>'
        ].join('');

        document.documentElement.appendChild(host);
        shadow.getElementById('uatWcArm').addEventListener('click', armCapture);
        shadow.getElementById('uatWcDisarm').addEventListener('click', disarmCapture);
        shadow.getElementById('uatWcEnd').addEventListener('click', endCapture);
        shadow.querySelector('.uat-wc-drag').addEventListener('mousedown', onPointerDown);
        window.addEventListener('mousemove', onPointerMove);
        window.addEventListener('mouseup', onPointerUp);

        document.addEventListener('keydown', function (e) {
            if (e.key === 'F2') {
                e.preventDefault();
                if (armed) disarmCapture();
                else armCapture();
            }
            if (e.key === 'Escape' && armed) disarmCapture();
        }, true);

        setArmed(false);
        syncArmStatus();
        setInterval(syncArmStatus, 1200);
    }

    window.__uatWebCaptureShowToolbar = function () {
        if (!host || !host.isConnected) buildToolbar();
        else syncArmStatus();
    };

    window.__uatWebCaptureHideToolbar = function () {
        removeToolbar();
    };

    buildToolbar();
})();
