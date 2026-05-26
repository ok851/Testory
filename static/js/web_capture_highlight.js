/* 网页 CDP/扩展 捕获高亮脚本 — 与 web_dom_picker_inject.js 独立 */
(function () {
    if (window.__uatWebCaptureHighlightLoaded) return;
    window.__uatWebCaptureHighlightLoaded = true;

    var API_BASE = '__API_BASE__';
    var SESSION = '__SESSION__';
    var THEME = {
        border: '#16a34a',
        fill: 'rgba(187,247,208,0.35)'
    };

    var state = {
        armed: false,
        overlay: null,
        panel: null
    };

    function isPickerNode(node) {
        if (!node) return false;
        if (state.panel && (node === state.panel || state.panel.contains(node))) return true;
        return node.id === 'uat-web-capture-overlay';
    }

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

    function buildDomPath(el) {
        var path = [];
        var cur = el;
        while (cur && cur.nodeType === 1 && path.length < 12) {
            var tag = cur.tagName.toLowerCase();
            var idx = 0;
            var sib = cur.parentElement ? cur.parentElement.children : [];
            for (var i = 0; i < sib.length; i++) {
                if (sib[i].tagName === cur.tagName) idx++;
                if (sib[i] === cur) break;
            }
            path.unshift({ tag: tag, index: idx - 1, id: cur.id || '', class: cur.className || '' });
            cur = cur.parentElement;
        }
        return path;
    }

    function getXPath(el) {
        if (!el || el.nodeType !== 1) return '';
        if (el.id && !/(\d{6,}|[a-f0-9]{10,})/i.test(el.id)) {
            return '//*[@id="' + el.id.replace(/"/g, '\\"') + '"]';
        }
        var parts = [];
        var cur = el;
        while (cur && cur.nodeType === 1) {
            var tag = cur.tagName.toLowerCase();
            var ix = 1;
            var sib = cur.previousElementSibling;
            while (sib) {
                if (sib.tagName === cur.tagName) ix++;
                sib = sib.previousElementSibling;
            }
            parts.unshift(tag + '[' + ix + ']');
            cur = cur.parentElement;
        }
        return '/' + parts.join('/');
    }

    function collectAttributes(el) {
        var out = {};
        if (!el || !el.attributes) return out;
        for (var i = 0; i < el.attributes.length; i++) {
            var a = el.attributes[i];
            out[a.name] = a.value;
        }
        return out;
    }

    function buildPayload(el) {
        var sel = generateSelector(el);
        return {
            selector: sel,
            css_selector: sel,
            elementInfo: {
                tagName: el.tagName,
                id: el.id || '',
                className: el.className || '',
                textContent: (el.innerText || el.textContent || '').trim().slice(0, 200),
                name: el.getAttribute('name') || '',
                attributes: collectAttributes(el),
                xpath: getXPath(el),
                xpath_absolute: getXPath(el)
            },
            dom_path: buildDomPath(el),
            xpath_absolute: getXPath(el),
            source_url: location.href,
            source_frame: window !== window.top ? (window.name || 'iframe') : ''
        };
    }

    function ensureOverlay() {
        var host = document.body || document.documentElement;
        if (!host) return null;
        if (state.overlay && state.overlay.isConnected) return state.overlay;
        var ov = document.createElement('div');
        ov.id = 'uat-web-capture-overlay';
        ov.style.cssText = 'position:fixed;pointer-events:none;border:2px solid ' + THEME.border +
            ';background:' + THEME.fill + ';border-radius:4px;z-index:2147483646;display:none;';
        host.appendChild(ov);
        state.overlay = ov;
        return ov;
    }

    function ensurePanel() {
        if (state.panel && state.panel.isConnected) return state.panel;
        var p = document.createElement('div');
        p.id = 'uat-web-capture-panel';
        p.style.cssText = 'position:fixed;top:12px;left:12px;z-index:2147483647;background:#fff;border:1px solid #e5e7eb;border-radius:8px;padding:10px 14px;font:13px sans-serif;box-shadow:0 4px 12px rgba(0,0,0,.12);';
        p.innerHTML = '<div style="font-weight:700;margin-bottom:6px;">UAT 网页捕获</div>' +
            '<div id="uat-wc-status" style="color:#6b7280;margin-bottom:8px;">未开始</div>' +
            '<button type="button" id="uat-wc-arm" style="background:#dc2626;color:#fff;border:none;padding:6px 12px;border-radius:6px;cursor:pointer;">开始捕获</button>';
        document.body.appendChild(p);
        state.panel = p;
        p.querySelector('#uat-wc-arm').addEventListener('click', function () {
            window.__uatWebCaptureArm();
        });
        return p;
    }

    function postPick(payload) {
        if (!API_BASE || !SESSION) return;
        fetch(API_BASE + '/api/web-capture/pick', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ session_id: SESSION, payload: payload })
        }).catch(function () {});
    }

    function onMove(e) {
        if (!state.armed) return;
        var t = document.elementFromPoint(e.clientX, e.clientY);
        if (!t || isPickerNode(t)) return;
        var r = t.getBoundingClientRect();
        var ov = ensureOverlay();
        if (!ov) return;
        ov.style.display = 'block';
        ov.style.left = r.left + 'px';
        ov.style.top = r.top + 'px';
        ov.style.width = r.width + 'px';
        ov.style.height = r.height + 'px';
    }

    function onClick(e) {
        if (!state.armed) return;
        var t = e.target;
        if (!t || isPickerNode(t)) return;
        e.preventDefault();
        e.stopPropagation();
        state.armed = false;
        var st = document.getElementById('uat-wc-status');
        if (st) st.textContent = '已拾取，正在上报…';
        postPick(buildPayload(t));
        if (st) st.textContent = '已捕获元素';
    }

    window.__uatWebCaptureArm = function () {
        state.armed = true;
        ensurePanel();
        var st = document.getElementById('uat-wc-status');
        if (st) st.textContent = '悬停高亮，单击拾取';
    };

    document.addEventListener('mousemove', onMove, true);
    document.addEventListener('click', onClick, true);
    ensurePanel();
})();
