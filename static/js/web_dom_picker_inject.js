/* 网页 DOM 捕获注入脚本（由平台 /api/web-dom-picker/inject.js 注入 __API_BASE__ / __SESSION__） */
(function () {
    if (window.__uatWebDomInjectLoaded) return;
    window.__uatWebDomInjectLoaded = true;

    var API_BASE = '__API_BASE__';
    var SESSION = '__SESSION__';

    var THEME = {
        bg: '#ffffff',
        border: '#e5e7eb',
        text: '#111827',
        muted: '#6b7280',
        primary: '#dc2626',
        primaryDark: '#b91c1c',
        secondary: '#475569',
        highlight: '#16a34a',
        highlightFill: 'rgba(187,247,208,0.35)'
    };

    var state = {
        armed: false,
        closed: false,
        overlay: null,
        panel: null,
        stateLbl: null,
        statusLbl: null,
        armBtn: null
    };

    function stableClassSelector(el) {
        if (!el || !el.classList || !el.classList.length) return '';
        var keep = [];
        for (var i = 0; i < el.classList.length; i++) {
            var c = el.classList[i];
            if (!c || c.length <= 2) continue;
            if (/\d{4,}/.test(c)) continue;
            if (/[a-f0-9]{8,}/i.test(c)) continue;
            keep.push(c);
            if (keep.length >= 3) break;
        }
        return keep.length ? '.' + keep.join('.') : '';
    }

    function generateSelector(element) {
        if (!element || !element.tagName) return '';
        var tag = element.tagName.toLowerCase();
        var id = element.id || '';
        if (id && !/(\d{6,}|[a-f0-9]{10,})/i.test(id)) return '#' + id;
        var cls = stableClassSelector(element);
        if (cls) return tag + cls;
        return tag;
    }

    function resolveTarget(raw) {
        if (!raw) return null;
        if (raw.nodeType === 1) return raw;
        return raw.parentElement || null;
    }

    function isPickerNode(node) {
        if (!node) return false;
        if (state.panel && (node === state.panel || state.panel.contains(node))) return true;
        return node.id === 'uat-web-dom-picker-overlay';
    }

    function ensureOverlay() {
        var host = document.body || document.documentElement;
        if (!host) return null;
        if (state.overlay && state.overlay.isConnected) return state.overlay;
        var ov = document.createElement('div');
        ov.id = 'uat-web-dom-picker-overlay';
        ov.style.cssText = 'position:fixed;pointer-events:none;border:2px solid ' + THEME.highlight +
            ';background:' + THEME.highlightFill + ';border-radius:4px;z-index:2147483646;display:none;box-sizing:border-box;';
        host.appendChild(ov);
        state.overlay = ov;
        return ov;
    }

    function hideOverlay() {
        if (state.overlay) state.overlay.style.display = 'none';
    }

    function showOverlayFor(target) {
        var rect = target.getBoundingClientRect();
        var ov = ensureOverlay();
        if (!ov) return;
        ov.style.display = 'block';
        ov.style.left = rect.left + 'px';
        ov.style.top = rect.top + 'px';
        ov.style.width = Math.max(rect.width, 2) + 'px';
        ov.style.height = Math.max(rect.height, 2) + 'px';
    }

    function postJson(path, body) {
        return fetch(API_BASE + path, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
            credentials: 'omit',
            mode: 'cors'
        }).then(function (r) { return r.json(); });
    }

    function buildPayload(target) {
        return {
            selector: generateSelector(target),
            source_frame: 'main',
            source_url: location.href || '',
            page_title: document.title || '',
            elementInfo: {
                tagName: target.tagName,
                id: target.id || '',
                className: target.className || '',
                textContent: target.textContent ? target.textContent.substring(0, 100) : '',
                attributes: {
                    type: target.type || '',
                    name: target.name || '',
                    value: target.value || '',
                    href: target.href || '',
                    src: target.src || '',
                    alt: target.alt || '',
                    title: target.title || '',
                    'data-testid': target.getAttribute('data-testid') || '',
                    'data-test': target.getAttribute('data-test') || '',
                    'data-id': target.getAttribute('data-id') || '',
                    role: target.getAttribute('role') || ''
                }
            }
        };
    }

    function setArmed(armed) {
        state.armed = !!armed;
        if (!state.armed) hideOverlay();
        if (state.armBtn) {
            state.armBtn.textContent = state.armed ? '■ 停止捕获' : '▶ 开始捕获';
            state.armBtn.style.background = state.armed ? THEME.primaryDark : THEME.primary;
        }
        if (state.stateLbl) {
            state.stateLbl.textContent = state.armed ? '● 捕获中' : '● 待命';
            state.stateLbl.style.color = state.armed ? THEME.highlight : THEME.muted;
            state.stateLbl.style.background = state.armed ? '#ecfdf5' : '#f1f5f9';
        }
        if (state.statusLbl) {
            state.statusLbl.textContent = state.armed
                ? '移动鼠标高亮元素边框，单击目标完成拾取'
                : '点击「开始捕获」后，在页面上单击目标元素';
        }
        broadcastFrameState(state.armed);
    }

    function broadcastFrameState(enabled) {
        try {
            var fs = document.querySelectorAll('iframe');
            for (var i = 0; i < fs.length; i++) {
                var f = fs[i];
                if (f && f.contentWindow) {
                    f.contentWindow.postMessage({ __uatWebDomPicker: true, type: 'picker_state', enabled: !!enabled }, '*');
                }
            }
        } catch (_) {}
    }

    function onMove(e) {
        if (!state.armed || state.closed) {
            hideOverlay();
            return;
        }
        var raw = (e.composedPath && e.composedPath()[0]) ? e.composedPath()[0] : e.target;
        var target = resolveTarget(raw);
        if (!target || isPickerNode(target)) {
            hideOverlay();
            return;
        }
        showOverlayFor(target);
    }

    function onClick(e) {
        if (!state.armed || state.closed) return;
        var raw = (e.composedPath && e.composedPath()[0]) ? e.composedPath()[0] : e.target;
        var target = resolveTarget(raw);
        if (!target || isPickerNode(target)) return;
        e.preventDefault();
        e.stopPropagation();
        var payload = buildPayload(target);
        hideOverlay();
        setArmed(false);
        postJson('/api/web-dom-picker/pick', { session: SESSION, payload: payload }).then(function (res) {
            if (state.statusLbl) {
                state.statusLbl.textContent = res && res.success
                    ? '已回传拾取结果，请返回平台确认'
                    : ((res && res.error) || '回传失败，请检查平台地址是否可访问');
            }
        }).catch(function () {
            if (state.statusLbl) state.statusLbl.textContent = '无法连接平台，请确认本机平台已启动且地址正确';
        });
    }

    function removePanel() {
        hideOverlay();
        if (state.panel && state.panel.parentNode) state.panel.parentNode.removeChild(state.panel);
        state.panel = null;
        state.closed = true;
        broadcastFrameState(false);
        postJson('/api/web-dom-picker/close', { session: SESSION }).catch(function () {});
    }

    function ensurePanel() {
        if (state.panel && state.panel.isConnected) return state.panel;
        var host = document.body || document.documentElement;
        if (!host) return null;

        var panel = document.createElement('div');
        panel.id = 'uat-web-dom-picker-panel';
        panel.style.cssText = 'position:fixed;top:16px;left:16px;z-index:2147483647;background:' + THEME.bg +
            ';color:' + THEME.text + ';border:1px solid ' + THEME.border +
            ';border-radius:10px;box-shadow:0 8px 24px rgba(0,0,0,.12);padding:14px;min-width:300px;max-width:400px;' +
            'font:13px/1.35 -apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Arial,sans-serif;';

        var hdr = document.createElement('div');
        hdr.style.cssText = 'display:flex;align-items:center;margin-bottom:8px;';
        var title = document.createElement('span');
        title.textContent = '网页元素捕获';
        title.style.cssText = 'font-weight:700;font-size:14px;';
        var badge = document.createElement('span');
        badge.textContent = 'DOM';
        badge.style.cssText = 'margin-left:8px;font-size:10px;font-weight:700;color:' + THEME.primary + ';background:#fef2f2;padding:2px 6px;border-radius:4px;';
        hdr.appendChild(title);
        hdr.appendChild(badge);
        panel.appendChild(hdr);

        var hint = document.createElement('div');
        hint.textContent = '悬停高亮边框，单击拾取；与自动化浏览器无关';
        hint.style.cssText = 'color:' + THEME.muted + ';font-size:12px;margin-bottom:10px;line-height:1.4;';
        panel.appendChild(hint);

        state.stateLbl = document.createElement('div');
        state.stateLbl.textContent = '● 待命';
        state.stateLbl.style.cssText = 'display:inline-block;font-size:12px;font-weight:600;padding:4px 8px;border-radius:6px;margin-bottom:8px;';
        panel.appendChild(state.stateLbl);

        state.statusLbl = document.createElement('div');
        state.statusLbl.textContent = '点击「开始捕获」后，在页面上单击目标元素';
        state.statusLbl.style.cssText = 'color:' + THEME.secondary + ';font-size:12px;margin-bottom:12px;line-height:1.4;';
        panel.appendChild(state.statusLbl);

        var btnRow = document.createElement('div');
        btnRow.style.cssText = 'display:flex;flex-wrap:wrap;gap:8px;';

        state.armBtn = document.createElement('button');
        state.armBtn.type = 'button';
        state.armBtn.textContent = '▶ 开始捕获';
        state.armBtn.style.cssText = 'border:none;border-radius:8px;padding:6px 12px;font-size:12px;font-weight:600;cursor:pointer;color:#fff;background:' + THEME.primary;
        state.armBtn.onclick = function () { setArmed(!state.armed); };
        btnRow.appendChild(state.armBtn);

        var endBtn = document.createElement('button');
        endBtn.type = 'button';
        endBtn.textContent = '结束';
        endBtn.style.cssText = 'border:none;border-radius:8px;padding:6px 12px;font-size:12px;font-weight:600;cursor:pointer;color:#fff;background:' + THEME.secondary;
        endBtn.onclick = removePanel;
        btnRow.appendChild(endBtn);
        panel.appendChild(btnRow);

        host.appendChild(panel);
        state.panel = panel;
        setArmed(false);
        return panel;
    }

    if (!window.__uatWebDomPickerBound) {
        window.__uatWebDomPickerBound = true;
        document.addEventListener('mousemove', onMove, true);
        document.addEventListener('click', onClick, true);
        window.addEventListener('message', function (evt) {
            try {
                var d = evt && evt.data;
                if (!(d && d.__uatWebDomPicker && d.type === 'selected' && d.payload)) return;
                postJson('/api/web-dom-picker/pick', { session: SESSION, payload: d.payload });
                setArmed(false);
                hideOverlay();
            } catch (_) {}
        }, true);
    }

    ensurePanel();
})();
