/* 网页捕获高亮脚本 — 注入用户浏览器页面（无内嵌面板，控制在外部捕获器窗口） */
(function () {
    if (window.__uatWebCaptureHighlightLoaded) return;
    window.__uatWebCaptureHighlightLoaded = true;

    var API_BASE = '__API_BASE__';
    var SESSION = '__SESSION__';
    var PAGE_ONLY = '__PAGE_ONLY__' === '1';
    var THEME = { border: '#16a34a', fill: 'rgba(187,247,208,0.35)' };

    var state = {
        armed: false,
        overlay: null,
        lastHoverTs: 0,
        lastHoverX: -1,
        lastHoverY: -1,
        hoverBusy: false
    };

    var HOVER_INTERVAL = 280;
    var HOVER_MIN_PX = 8;

    function isDynamicId(id) {
        return /(\d{6,}|[a-f0-9]{10,})/i.test(id || '');
    }

    function isDynamicClass(c) {
        if (!c || c.length <= 2) return true;
        if (/\d{4,}/.test(c)) return true;
        if (/[a-f0-9]{8,}/i.test(c)) return true;
        if (/^(css|sc|jsx|emotion)-[a-z0-9]+$/i.test(c)) return true;
        if (/^v-[a-f0-9]{6,}$/i.test(c)) return true;
        return false;
    }

    function stableClassSelector(el) {
        if (!el || !el.classList || !el.classList.length) return '';
        var keep = [];
        for (var i = 0; i < el.classList.length; i++) {
            var c = el.classList[i];
            if (!isDynamicClass(c)) keep.push(c);
            if (keep.length >= 3) break;
        }
        return keep.length ? '.' + keep.join('.') : '';
    }

    function resolveTargetAt(x, y) {
        var el = document.elementFromPoint(x, y);
        if (!el) return null;
        if (el.shadowRoot && el.shadowRoot.elementFromPoint) {
            var inner = el.shadowRoot.elementFromPoint(x, y);
            if (inner) return inner;
        }
        try {
            var path = el.composedPath ? el.composedPath() : [el];
            for (var i = 0; i < path.length; i++) {
                var n = path[i];
                if (n && n.nodeType === 1 && n.id !== 'uat-web-capture-overlay') return n;
            }
        } catch (e) { /* ignore */ }
        return el.id === 'uat-web-capture-overlay' ? null : el;
    }

    function elementLabel(el) {
        if (!el || !el.tagName) return '';
        var tag = el.tagName.toLowerCase();
        var id = el.id || '';
        var name = el.getAttribute('name') || '';
        var aria = el.getAttribute('aria-label') || '';
        var txt = (el.innerText || el.textContent || '').trim().slice(0, 24);
        if (id && !isDynamicId(id)) return tag + '#' + id;
        if (name) return tag + '[name=' + name + ']';
        if (aria) return tag + '[aria-label=' + aria.slice(0, 20) + ']';
        if (txt) return tag + ' "' + txt + '"';
        return tag;
    }

    function getAccessibleName(el) {
        if (!el) return '';
        var aria = el.getAttribute('aria-label') || '';
        if (aria) return aria.trim();
        var labelled = el.getAttribute('aria-labelledby');
        if (labelled) {
            var ref = document.getElementById(labelled);
            if (ref) return (ref.innerText || ref.textContent || '').trim();
        }
        var id = el.id;
        if (id) {
            var lbl = document.querySelector('label[for="' + id.replace(/"/g, '\\"') + '"]');
            if (lbl) return (lbl.innerText || lbl.textContent || '').trim();
        }
        var ph = el.getAttribute('placeholder') || '';
        if (ph) return ph.trim();
        return (el.getAttribute('title') || '').trim();
    }

    function getImplicitRole(el) {
        if (!el) return '';
        var r = el.getAttribute('role') || '';
        if (r) return r;
        var tag = (el.tagName || '').toLowerCase();
        var map = { button: 'button', a: 'link', input: 'textbox', textarea: 'textbox', select: 'combobox' };
        if (tag === 'input') {
            var t = (el.getAttribute('type') || 'text').toLowerCase();
            if (t === 'button' || t === 'submit') return 'button';
            if (t === 'checkbox') return 'checkbox';
            if (t === 'radio') return 'radio';
        }
        return map[tag] || '';
    }

    function buildStructuralCss(el) {
        var parts = [];
        var cur = el;
        var depth = 0;
        while (cur && cur.nodeType === 1 && depth < 6) {
            var tag = cur.tagName.toLowerCase();
            var seg = tag;
            var id = cur.id || '';
            if (id && !isDynamicId(id)) {
                parts.unshift('#' + id + (parts.length ? ' > ' + parts.join(' > ') : ''));
                return parts.join('');
            }
            var cls = stableClassSelector(cur);
            if (cls) seg += cls;
            else {
                var idx = 1;
                var sib = cur.previousElementSibling;
                while (sib) {
                    if (sib.tagName === cur.tagName) idx++;
                    sib = sib.previousElementSibling;
                }
                seg += ':nth-of-type(' + idx + ')';
            }
            parts.unshift(seg);
            cur = cur.parentElement;
            depth++;
        }
        return parts.join(' > ');
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
        if (el.id && !isDynamicId(el.id)) {
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

    function countCss(sel) {
        try { return document.querySelectorAll(sel).length; } catch (e) { return -1; }
    }

    function countXpath(xp) {
        try {
            return document.evaluate(xp, document, null, XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null).snapshotLength;
        } catch (e) { return -1; }
    }

    function buildVerifiedCounts(el, elementInfo) {
        var counts = {};
        var attrs = elementInfo.attributes || {};
        var tag = (elementInfo.tagName || '').toLowerCase();
        var testid = attrs['data-testid'] || attrs['data-test'] || '';
        if (testid) counts['data|testid=' + testid] = countCss('[data-testid="' + testid + '"]');
        var name = attrs.name || elementInfo.name || '';
        if (name) counts['name|' + name] = countCss('[name="' + name.replace(/"/g, '\\"') + '"]');
        var eid = elementInfo.id || '';
        if (eid && !isDynamicId(eid)) counts['id|' + eid] = countCss('#' + eid);
        var aria = attrs['aria-label'] || '';
        var role = attrs.role || elementInfo.role || '';
        if (aria && role) counts['aria|role=' + role + '[name=' + aria + ']'] = -1;
        var css = buildStructuralCss(el);
        if (css) counts['css|' + css] = countCss(css);
        var xp = elementInfo.xpath || '';
        if (xp) counts['xpath|' + xp] = countXpath(xp);
        return counts;
    }

    function buildPayload(el) {
        var attrs = collectAttributes(el);
        var elementInfo = {
            tagName: el.tagName,
            id: el.id || '',
            className: el.className || '',
            textContent: (el.innerText || el.textContent || '').trim().slice(0, 200),
            name: el.getAttribute('name') || '',
            attributes: attrs,
            xpath: getXPath(el),
            xpath_absolute: getXPath(el),
            accessible_name: getAccessibleName(el),
            role: getImplicitRole(el),
            structural_css: buildStructuralCss(el)
        };
        var verified_counts = buildVerifiedCounts(el, elementInfo);
        return {
            selector: buildStructuralCss(el) || (el.id && !isDynamicId(el.id) ? '#' + el.id : el.tagName.toLowerCase()),
            css_selector: buildStructuralCss(el),
            structural_css: buildStructuralCss(el),
            elementInfo: elementInfo,
            dom_path: buildDomPath(el),
            xpath_absolute: getXPath(el),
            verified_counts: verified_counts,
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
            ';background:' + THEME.fill + ';border-radius:4px;z-index:2147483646;display:none;box-sizing:border-box;';
        host.appendChild(ov);
        state.overlay = ov;
        return ov;
    }

    function notifyHover(label) {
        try {
            if (window.parent && window.parent !== window) {
                window.parent.postMessage({ __uatWebCaptureHover: true, label: label }, '*');
            }
            if (window.opener) {
                window.opener.postMessage({ __uatWebCaptureHover: true, label: label }, '*');
            }
        } catch (e) { /* ignore */ }
    }

    function showHighlight(el) {
        if (!el) return;
        var r = el.getBoundingClientRect();
        var ov = ensureOverlay();
        if (!ov || r.width < 1 && r.height < 1) return;
        ov.style.display = 'block';
        ov.style.left = r.left + 'px';
        ov.style.top = r.top + 'px';
        ov.style.width = Math.max(r.width, 4) + 'px';
        ov.style.height = Math.max(r.height, 4) + 'px';
        notifyHover(elementLabel(el));
    }

    function postPick(payload) {
        var body = { session_id: SESSION, payload: payload };
        if (window !== window.top) {
            try {
                window.parent.postMessage({ __uatWebCapturePick: true, payload: payload }, '*');
            } catch (e) { /* ignore */ }
        }
        try {
            if (typeof chrome !== 'undefined' && chrome.runtime && chrome.runtime.sendMessage) {
                chrome.runtime.sendMessage({ type: 'pick', payload: payload });
            }
        } catch (e) { /* ignore */ }
        if (!API_BASE || !SESSION) return;
        fetch(API_BASE + '/api/web-capture/pick', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        }).catch(function () {});
    }

    function onMove(e) {
        if (!state.armed) return;
        var now = Date.now();
        var dx = Math.abs(e.clientX - state.lastHoverX);
        var dy = Math.abs(e.clientY - state.lastHoverY);
        if (now - state.lastHoverTs < HOVER_INTERVAL && dx < HOVER_MIN_PX && dy < HOVER_MIN_PX) return;
        state.lastHoverTs = now;
        state.lastHoverX = e.clientX;
        state.lastHoverY = e.clientY;
        var t = resolveTargetAt(e.clientX, e.clientY);
        if (!t || t.id === 'uat-web-capture-overlay') return;
        showHighlight(t);
    }

    function onClick(e) {
        if (!state.armed) return;
        var t = resolveTargetAt(e.clientX, e.clientY) || e.target;
        if (!t || t.id === 'uat-web-capture-overlay') return;
        e.preventDefault();
        e.stopPropagation();
        e.stopImmediatePropagation();
        state.armed = false;
        postPick(buildPayload(t));
        if (state.overlay) state.overlay.style.display = 'none';
    }

    window.__uatWebCaptureArm = function () {
        state.armed = true;
        state.lastHoverTs = 0;
    };

    window.__uatWebCaptureDisarm = function () {
        state.armed = false;
        if (state.overlay) state.overlay.style.display = 'none';
    };

    document.addEventListener('mousemove', onMove, true);
    document.addEventListener('click', onClick, true);

    if (window === window.top) {
        window.addEventListener('message', function (ev) {
            if (!ev.data) return;
            if (ev.data.__uatWebCapturePick === true && ev.data.payload) {
                postPick(ev.data.payload);
            }
        });
    }

    document.addEventListener('keydown', function (e) {
        if (e.key === 'F2') {
            e.preventDefault();
            if (state.armed) window.__uatWebCaptureDisarm();
            else window.__uatWebCaptureArm();
        }
        if (e.key === 'Escape' && state.armed) {
            window.__uatWebCaptureDisarm();
        }
    }, true);

    if (API_BASE && SESSION) {
        setInterval(function () {
            fetch(API_BASE + '/api/web-capture/arm-status?session=' + encodeURIComponent(SESSION))
                .then(function (r) { return r.json(); })
                .then(function (j) {
                    if (!j || !j.success) return;
                    if (j.armed && !state.armed) window.__uatWebCaptureArm();
                    if (!j.armed && state.armed) window.__uatWebCaptureDisarm();
                })
                .catch(function () {});
        }, 700);
    }
})();
