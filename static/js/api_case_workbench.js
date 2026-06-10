/**
 * API case detail workbench — Postman-style split layout.
 */
(function () {
    'use strict';

    const U = window.UatApi || {};
    const parseJsonResponse = U.parseJsonResponse;
    const escapeHtml = U.escapeHtml;
    const toastErr = U.toastErr;
    const toastOk = U.toastOk;
    const methodBadgeClass = U.methodBadgeClass;
    const statusBadgeClass = U.statusBadgeClass;
    const formatBytes = U.formatBytes;
    const parseStepSpec = U.parseStepSpec;
    const renderRunResultsHtml = U.renderRunResultsHtml;
    const cred = U.cred || { credentials: 'same-origin' };

    const boot = document.getElementById('uatApiWorkbenchBoot');
    if (!boot) return;

    let CASE_ID = 0;
    let PROJECT_ID = 0;
    try {
        const cfg = JSON.parse(boot.textContent || '{}');
        CASE_ID = cfg.case_id || 0;
        PROJECT_ID = cfg.project_id || 0;
    } catch (e) { /* skip */ }

    let editingStepId = null;
    let selectedStepId = null;
    let stepsCache = [];
    let lastRunData = null;
    let dragStepId = null;

    window.__uatApiCaseSteps = stepsCache;

    function $(id) { return document.getElementById(id); }

    function sortedSteps(steps) {
        return (steps || []).slice().sort(function (a, b) {
            return (parseInt(a.step_order, 10) || 0) - (parseInt(b.step_order, 10) || 0);
        });
    }

    /* ---- KV helpers ---- */
    function kvTbodyRead(tbody) {
        const o = {};
        if (!tbody) return o;
        tbody.querySelectorAll('tr').forEach(function (tr) {
            const k = (tr.querySelector('input.k') || {}).value;
            const v = (tr.querySelector('input.v') || {}).value;
            if (k && String(k).trim()) o[String(k).trim()] = (v == null ? '' : String(v));
        });
        return o;
    }

    function addKvRow(tbody, k, v) {
        const tr = document.createElement('tr');
        tr.innerHTML =
            '<td><input type="text" class="k" placeholder="key"></td>' +
            '<td><input type="text" class="v" placeholder="value"></td>' +
            '<td class="w-8"><button type="button" class="text-red-600 text-sm px-1 nr-rm" title="删除">×</button></td>';
        tr.querySelector('.k').value = k || '';
        tr.querySelector('.v').value = v || '';
        tr.querySelector('.nr-rm').addEventListener('click', function () { tr.remove(); });
        tbody.appendChild(tr);
    }

    function clearKVTbody(tbody) {
        if (tbody) tbody.innerHTML = '';
    }

    function syncBodyPanels() {
        const t = $('nrBodyType').value;
        $('nrBodyJsonWrap').classList.toggle('hidden', t !== 'json');
        $('nrBodyFormWrap').classList.toggle('hidden', t !== 'form');
        $('nrBodyRawWrap').classList.toggle('hidden', t !== 'raw');
    }

    function syncAuthPanels() {
        const t = ($('nrAuthType').value || 'none').toLowerCase();
        $('nrAuthBearerWrap').classList.toggle('hidden', t !== 'bearer');
        $('nrAuthBasicWrap').classList.toggle('hidden', t !== 'basic');
    }

    function switchReqTab(name) {
        document.querySelectorAll('.api-wb-tab[data-req-tab]').forEach(function (btn) {
            btn.classList.toggle('active', btn.getAttribute('data-req-tab') === name);
        });
        document.querySelectorAll('.api-wb-tab-panel[data-req-panel]').forEach(function (p) {
            p.classList.toggle('active', p.getAttribute('data-req-panel') === name);
        });
    }

    function switchRespTab(name) {
        document.querySelectorAll('.api-wb-tab[data-resp-tab]').forEach(function (btn) {
            btn.classList.toggle('active', btn.getAttribute('data-resp-tab') === name);
        });
        document.querySelectorAll('.api-wb-resp-panel').forEach(function (p) {
            p.classList.toggle('hidden', p.getAttribute('data-resp-panel') !== name);
        });
    }

    function buildSpecFromForm() {
        const method = ($('nrMethod').value || 'GET').toUpperCase();
        const url = ($('nrUrl').value || '').trim();
        if (!url) throw new Error('请填写请求 URL');
        const spec = { method: method, url: url };
        const exp = parseInt($('nrExpected').value, 10);
        spec.expected_status = isNaN(exp) ? 200 : exp;
        const to = parseFloat($('nrTimeout').value);
        spec.timeout = !isNaN(to) && to > 0 ? to : 30;
        spec.verify_ssl = $('nrVerifySsl').checked;
        spec.follow_redirects = $('nrFollowRedirects').checked;
        spec.use_browser_cookies = $('nrUseBrowserCookies').checked;
        const auth = ($('nrAuthType').value || 'none').toLowerCase();
        spec.auth_type = auth;
        if (auth === 'bearer') spec.bearer_token = $('nrBearerToken').value || '';
        else if (auth === 'basic') {
            spec.basic_username = $('nrBasicUser').value || '';
            spec.basic_password = $('nrBasicPass').value || '';
        }
        const q = kvTbodyRead($('nrQueryTbody'));
        if (Object.keys(q).length) spec.params = q;
        const h = kvTbodyRead($('nrHeaderTbody'));
        if (Object.keys(h).length) spec.headers = h;
        const bt = $('nrBodyType').value;
        spec.body_type = bt;
        if (bt === 'json') {
            const raw = ($('nrBodyJson').value || '').trim();
            if (raw) {
                try { spec.body_json = JSON.parse(raw); }
                catch (e) { throw new Error('Body JSON 无效：' + e.message); }
            }
        } else if (bt === 'form') {
            const fd = kvTbodyRead($('nrFormTbody'));
            if (Object.keys(fd).length) spec.body_form = fd;
        } else if (bt === 'raw') {
            const rb = $('nrBodyRaw').value;
            if (rb) spec.body_raw = rb;
            const ct = ($('nrRawCt').value || '').trim();
            if (ct) spec.raw_content_type = ct;
        }
        const pdm = parseInt($('nrPreDelayMs').value, 10);
        if (!isNaN(pdm) && pdm > 0) spec.pre_delay_ms = pdm;
        const jp = ($('nrJsonPath').value || '').trim();
        const evRaw = ($('nrExpectedJson').value || '').trim();
        if (jp) {
            spec.json_path = jp;
            if (evRaw !== '') {
                try { spec.expected_json_value = JSON.parse(evRaw); }
                catch (e1) { spec.expected_json_value = evRaw; }
            }
        }
        var pc = ($('nrPrerequestChain').value || '').trim();
        if (pc) {
            try {
                var arr = JSON.parse(pc);
                if (!Array.isArray(arr)) throw new Error('须为数组');
                spec.prerequest_chain = arr;
            } catch (e2) { throw new Error('前置请求链 JSON 无效：' + (e2.message || String(e2))); }
        }
        var preS = ($('nrPrescript').value || '').trim();
        if (preS) spec.prescript = preS;
        var postS = ($('nrPostscript').value || '').trim();
        if (postS) spec.postscript = postS;
        var ex = ($('nrExtractVariables').value || '').trim();
        if (ex) {
            try {
                var earr = JSON.parse(ex);
                if (!Array.isArray(earr)) throw new Error('须为数组');
                spec.extract_variables = earr;
            } catch (e3) { throw new Error('提取变量 JSON 无效：' + (e3.message || String(e3))); }
        }
        if ($('nrPersistExtracts').checked) spec.persist_extracts_to_case = true;
        return spec;
    }

    function getSpecFromForm() {
        const adv = ($('nrApiSpecJson').value || '').trim();
        if (adv) {
            try { return JSON.parse(adv); }
            catch (e) { throw new Error('高级 JSON 无效：' + e.message); }
        }
        return buildSpecFromForm();
    }

    function applySpecToForm(spec) {
        if (!spec || typeof spec !== 'object') spec = {};
        $('nrMethod').value = (spec.method || 'GET').toUpperCase();
        $('nrUrl').value = spec.url || '';
        $('nrExpected').value = String(spec.expected_status != null ? spec.expected_status : 200);
        $('nrTimeout').value = String(spec.timeout != null ? spec.timeout : 30);
        $('nrVerifySsl').checked = spec.verify_ssl !== false;
        $('nrFollowRedirects').checked = spec.follow_redirects !== false;
        $('nrUseBrowserCookies').checked = !!spec.use_browser_cookies;
        const auth = (spec.auth_type || 'none').toLowerCase();
        $('nrAuthType').value = auth === 'bearer' || auth === 'basic' ? auth : 'none';
        $('nrBearerToken').value = spec.bearer_token || '';
        $('nrBasicUser').value = spec.basic_username || '';
        $('nrBasicPass').value = spec.basic_password || '';
        syncAuthPanels();
        clearKVTbody($('nrQueryTbody'));
        clearKVTbody($('nrHeaderTbody'));
        clearKVTbody($('nrFormTbody'));
        const params = spec.params || spec.query || {};
        if (params && typeof params === 'object') {
            Object.keys(params).forEach(function (k) { addKvRow($('nrQueryTbody'), k, params[k]); });
        }
        const headers = spec.headers || {};
        if (headers && typeof headers === 'object') {
            Object.keys(headers).forEach(function (k) { addKvRow($('nrHeaderTbody'), k, headers[k]); });
        }
        const bt = (spec.body_type || 'none').toLowerCase();
        $('nrBodyType').value = ['none', 'json', 'form', 'raw'].indexOf(bt) >= 0 ? bt : 'none';
        $('nrBodyJson').value = spec.body_json != null
            ? (typeof spec.body_json === 'string' ? spec.body_json : JSON.stringify(spec.body_json, null, 2))
            : '';
        const bf = spec.body_form || {};
        if (bf && typeof bf === 'object') {
            Object.keys(bf).forEach(function (k) { addKvRow($('nrFormTbody'), k, bf[k]); });
        }
        $('nrBodyRaw').value = spec.body_raw || '';
        $('nrRawCt').value = spec.raw_content_type || '';
        const pdm = spec.pre_delay_ms;
        $('nrPreDelayMs').value = (pdm != null && parseInt(pdm, 10) > 0) ? String(parseInt(pdm, 10)) : '';
        $('nrJsonPath').value = spec.json_path || '';
        const ev = spec.expected_json_value;
        $('nrExpectedJson').value = (ev !== undefined && ev !== null)
            ? (typeof ev === 'string' ? ev : JSON.stringify(ev, null, 2)) : '';
        $('nrPrerequestChain').value = spec.prerequest_chain != null ? JSON.stringify(spec.prerequest_chain, null, 2) : '[]';
        $('nrPrescript').value = spec.prescript || spec.pre_request_script || '';
        $('nrPostscript').value = spec.postscript || spec.post_request_script || '';
        var eva = spec.extract_variables != null ? spec.extract_variables : spec.extract;
        $('nrExtractVariables').value = eva != null ? JSON.stringify(eva, null, 2) : '[]';
        $('nrPersistExtracts').checked = !!spec.persist_extracts_to_case;
        syncBodyPanels();
    }

    function resetForm() {
        editingStepId = null;
        selectedStepId = null;
        $('nrMethod').value = 'GET';
        $('nrUrl').value = '';
        $('nrExpected').value = '200';
        $('nrBodyType').value = 'none';
        $('nrBodyJson').value = '';
        $('nrBodyRaw').value = '';
        $('nrRawCt').value = '';
        $('nrDesc').value = '';
        $('nrApiSpecJson').value = '';
        $('nrTimeout').value = '30';
        $('nrVerifySsl').checked = true;
        $('nrFollowRedirects').checked = true;
        $('nrUseBrowserCookies').checked = false;
        $('nrAuthType').value = 'none';
        $('nrBearerToken').value = '';
        $('nrBasicUser').value = '';
        $('nrBasicPass').value = '';
        syncAuthPanels();
        $('nrPreDelayMs').value = '';
        $('nrJsonPath').value = '';
        $('nrExpectedJson').value = '';
        $('nrPrerequestChain').value = '[]';
        $('nrPrescript').value = '';
        $('nrPostscript').value = '';
        $('nrExtractVariables').value = '[]';
        $('nrPersistExtracts').checked = false;
        clearKVTbody($('nrQueryTbody'));
        clearKVTbody($('nrHeaderTbody'));
        clearKVTbody($('nrFormTbody'));
        syncBodyPanels();
        $('btnDeleteStep').classList.add('hidden');
        $('apiEditorEmpty').classList.remove('hidden');
        $('apiEditorForm').classList.add('hidden');
    }

    function showEditor() {
        $('apiEditorEmpty').classList.add('hidden');
        $('apiEditorForm').classList.remove('hidden');
    }

    /* ---- Response panel ---- */
    function showResponse(data, opts) {
        opts = opts || {};
        switchRespTab(opts.tab || 'body');
        const statusEl = $('apiRespStatus');
        const code = data.status_code;
        if (statusEl) {
            if (code != null) {
                statusEl.textContent = 'HTTP ' + code;
                statusEl.className = 'api-wb-status-badge ' + statusBadgeClass(code);
            } else {
                statusEl.textContent = opts.errorOnly ? '失败' : '—';
                statusEl.className = 'api-wb-status-badge api-wb-status-err';
            }
        }
        $('apiRespTime').textContent = data.elapsed_ms != null ? data.elapsed_ms + ' ms' : '—';
        let bodyTxt = data.response_text || '';
        if (!bodyTxt && data.response_json != null) {
            try { bodyTxt = JSON.stringify(data.response_json, null, 2); }
            catch (e) { bodyTxt = String(data.response_json); }
        }
        if (!bodyTxt && data.api_response_preview) bodyTxt = data.api_response_preview;
        $('apiRespSize').textContent = formatBytes(new Blob([bodyTxt || '']).size);
        let assertTxt = data.assert_message || '';
        if (data.error && !data.success) assertTxt = (assertTxt ? assertTxt + ' · ' : '') + data.error;
        if (data.ok_assert === false && data.error) assertTxt = data.error;
        $('apiRespAssert').textContent = assertTxt || (data.ok_assert === true ? '断言通过' : '');
        $('apiRespBody').value = bodyTxt || (data.error && !code ? data.error : '(无正文)');
        renderHeadersTable(data.response_headers || data.api_response_headers || {});
        const logs = data.script_logs || [];
        $('apiRespLogs').textContent = logs.length ? logs.join('\n') : (assertTxt || '—');
        renderExtractChips();
    }

    function renderHeadersTable(headers) {
        const wrap = $('apiRespHeadersWrap');
        if (!wrap) return;
        const keys = Object.keys(headers || {});
        if (!keys.length) {
            wrap.innerHTML = '<p class="p-4 text-sm text-gray-500">无响应头</p>';
            return;
        }
        let html = '<table class="api-wb-headers-table"><thead><tr><th>Key</th><th>Value</th></tr></thead><tbody>';
        keys.forEach(function (k) {
            html += '<tr><td>' + escapeHtml(k) + '</td><td>' + escapeHtml(String(headers[k])) + '</td></tr>';
        });
        html += '</tbody></table>';
        wrap.innerHTML = html;
    }

    function renderRunResultsInPanel(data) {
        lastRunData = data;
        const el = $('apiRespRunResults');
        if (!el) return;
        el.innerHTML = renderRunResultsHtml(data);
        el.querySelectorAll('.api-wb-run-step').forEach(function (row) {
            row.addEventListener('click', function () {
                const idx = parseInt(row.getAttribute('data-run-idx'), 10);
                const r = (data.step_results || [])[idx];
                if (!r) return;
                showResponse({
                    status_code: r.api_status_code,
                    elapsed_ms: r.api_elapsed_ms,
                    response_text: r.api_response_preview,
                    response_headers: r.api_response_headers,
                    assert_message: r.assert_message,
                    error: r.error,
                    ok_assert: r.status === 'success',
                });
                switchRespTab('body');
            });
        });
    }

    /* ---- JSON extract chips ---- */
    function guessNameFromJsonPath(path) {
        if (!path) return 'value';
        var parts = String(path).split('.');
        var last = parts[parts.length - 1] || 'value';
        last = last.replace(/\[\d+\]/g, '');
        return last.match(/^[a-zA-Z_][a-zA-Z0-9_]*$/) ? last : 'value';
    }

    function flattenJsonLeaves(obj, prefix, out, max) {
        if (out.length >= max) return;
        if (obj === null || typeof obj !== 'object') {
            if (prefix) out.push({ path: prefix, sample: obj });
            return;
        }
        if (Array.isArray(obj)) {
            if (!obj.length) { if (prefix) out.push({ path: prefix + '.0', sample: null }); return; }
            var first = obj[0];
            if (typeof first === 'object' && first !== null) flattenJsonLeaves(first, prefix ? prefix + '.0' : '0', out, max);
            else if (out.length < max) out.push({ path: (prefix ? prefix + '.0' : '0'), sample: first });
            return;
        }
        Object.keys(obj).forEach(function (k) {
            if (out.length >= max) return;
            var p = prefix ? prefix + '.' + k : k;
            var v = obj[k];
            if (v !== null && typeof v === 'object') flattenJsonLeaves(v, p, out, max);
            else if (out.length < max) out.push({ path: p, sample: v });
        });
    }

    function appendExtractRules(newRows) {
        var ta = $('nrExtractVariables');
        var arr = [];
        try { arr = JSON.parse((ta.value || '').trim() || '[]'); } catch (e) { arr = []; }
        if (!Array.isArray(arr)) arr = [];
        var names = {};
        arr.forEach(function (r) { if (r && r.name) names[String(r.name)] = true; });
        newRows.forEach(function (row) {
            if (!row || !row.name || names[row.name]) return;
            arr.push(row);
            names[row.name] = true;
        });
        ta.value = JSON.stringify(arr, null, 2);
    }

    function renderExtractChips() {
        var wrap = $('nrResponseExtractChips');
        if (!wrap) return;
        wrap.innerHTML = '';
        var raw = ($('apiRespBody').value || '').trim();
        if (!raw || (raw[0] !== '{' && raw[0] !== '[')) return;
        var data;
        try { data = JSON.parse(raw); } catch (e) { return; }
        var leaves = [];
        flattenJsonLeaves(data, '', leaves, 48);
        leaves.forEach(function (leaf) {
            if (!leaf.path) return;
            var btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'api-chip';
            btn.textContent = leaf.path;
            btn.addEventListener('click', function () {
                appendExtractRules([{ name: guessNameFromJsonPath(leaf.path), json_path: leaf.path }]);
                switchReqTab('advanced');
            });
            wrap.appendChild(btn);
        });
    }

    /* ---- Sidebar ---- */
    function renderSidebar() {
        const list = $('apiSidebarList');
        list.innerHTML = '';
        const sorted = sortedSteps(stepsCache);
        if (!sorted.length) {
            list.innerHTML = '<p class="text-xs text-gray-500 p-3">暂无请求，点击底部新建</p>';
            return;
        }
        sorted.forEach(function (s) {
            const spec = parseStepSpec(s);
            const method = (spec.method || 'GET').toUpperCase();
            const url = spec.url || '';
            const item = document.createElement('div');
            item.className = 'api-wb-step-item' + (selectedStepId === s.id ? ' active' : '');
            item.setAttribute('data-step-id', s.id);
            item.draggable = true;
            item.innerHTML =
                '<input type="checkbox" class="api-wb-step-cb rounded border-gray-400 shrink-0 mt-1" data-step-id="' + s.id + '" onclick="event.stopPropagation()">' +
                '<span class="api-wb-drag-handle" title="拖拽排序">⋮⋮</span>' +
                '<div class="api-wb-step-meta">' +
                '<span class="' + methodBadgeClass(method) + '">' + escapeHtml(method) + '</span>' +
                '<div class="api-wb-step-url">' + escapeHtml(url.slice(0, 80) || '（无 URL）') + '</div>' +
                '</div>';
            item.addEventListener('click', function (e) {
                if (e.target.closest('.api-wb-drag-handle')) return;
                selectStep(s.id);
            });
            item.addEventListener('dragstart', function (e) {
                dragStepId = s.id;
                item.classList.add('dragging');
                e.dataTransfer.effectAllowed = 'move';
            });
            item.addEventListener('dragend', function () {
                dragStepId = null;
                item.classList.remove('dragging');
                document.querySelectorAll('.api-wb-step-item').forEach(function (el) { el.classList.remove('drag-over'); });
            });
            item.addEventListener('dragover', function (e) {
                e.preventDefault();
                if (dragStepId && dragStepId !== s.id) item.classList.add('drag-over');
            });
            item.addEventListener('dragleave', function () { item.classList.remove('drag-over'); });
            item.addEventListener('drop', function (e) {
                e.preventDefault();
                item.classList.remove('drag-over');
                if (!dragStepId || dragStepId === s.id) return;
                reorderSteps(dragStepId, s.id);
            });
            list.appendChild(item);
        });
    }

    async function reorderSteps(fromId, toId) {
        const sorted = sortedSteps(stepsCache);
        const ids = sorted.map(function (s) { return s.id; });
        const fromIdx = ids.indexOf(fromId);
        const toIdx = ids.indexOf(toId);
        if (fromIdx < 0 || toIdx < 0) return;
        ids.splice(fromIdx, 1);
        ids.splice(toIdx, 0, fromId);
        const payload = ids.map(function (id, i) { return { id: id, order: i + 1 }; });
        const r = await fetch('/api/cases/' + CASE_ID + '/steps/order', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            ...cred,
            body: JSON.stringify({ steps: payload }),
        });
        const { ok, data: d } = await parseJsonResponse(r);
        if (!ok || d.success === false) {
            toastErr(d.error || '排序失败');
            return;
        }
        await loadSteps();
        toastOk('顺序已更新');
    }

    async function loadSteps() {
        const r = await fetch('/api/cases/' + CASE_ID + '/steps?page=1&page_size=200', { ...cred });
        const { ok, data: d } = await parseJsonResponse(r);
        if (!ok) {
            stepsCache = [];
            window.__uatApiCaseSteps = stepsCache;
            renderSidebar();
            return;
        }
        stepsCache = d.steps || [];
        window.__uatApiCaseSteps = stepsCache;
        renderSidebar();
    }

    async function selectStep(stepId) {
        selectedStepId = stepId;
        editingStepId = stepId;
        renderSidebar();
        const r = await fetch('/api/steps/' + stepId, { ...cred });
        const { ok, data: d } = await parseJsonResponse(r);
        if (!ok || !d.step) {
            toastErr(d.error || '加载失败');
            return;
        }
        const st = d.step;
        $('nrDesc').value = (st.description || '').trim();
        applySpecToForm(parseStepSpec(st));
        $('nrApiSpecJson').value = '';
        $('btnDeleteStep').classList.remove('hidden');
        showEditor();
        renderNeighborQuickPicks();
    }

    function newRequest() {
        resetForm();
        showEditor();
        $('nrUrl').focus();
        renderNeighborQuickPicks();
    }

    async function saveRequest() {
        var spec;
        try { spec = getSpecFromForm(); }
        catch (e) { toastErr(e.message); return; }
        const desc = $('nrDesc').value.trim();
        const payload = {
            action: 'api_request',
            selector_type: '',
            selector_value: '',
            input_value: '',
            description: desc,
            api_spec: JSON.stringify(spec),
        };
        var r;
        if (editingStepId) {
            r = await fetch('/api/steps/' + editingStepId, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                ...cred,
                body: JSON.stringify(payload),
            });
        } else {
            r = await fetch('/api/steps', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                ...cred,
                body: JSON.stringify(Object.assign({ case_id: CASE_ID }, payload)),
            });
        }
        const { ok, data: d } = await parseJsonResponse(r);
        if (!ok || d.success === false) {
            toastErr(d.error || '保存失败');
            return;
        }
        const newId = d.step_id || (d.step && d.step.id) || editingStepId;
        if (newId) {
            editingStepId = newId;
            selectedStepId = newId;
        }
        await loadSteps();
        $('btnDeleteStep').classList.remove('hidden');
        toastOk('已保存');
    }

    async function deleteStep() {
        if (!editingStepId) return;
        if (!confirm('确认删除此请求？')) return;
        const r = await fetch('/api/steps/' + editingStepId, { method: 'DELETE', ...cred });
        const { ok, data: d } = await parseJsonResponse(r);
        if (!ok || d.success === false) {
            toastErr(d.error || '删除失败');
            return;
        }
        resetForm();
        await loadSteps();
        toastOk('已删除');
    }

    async function sendRequest() {
        var spec;
        try { spec = getSpecFromForm(); }
        catch (e) { toastErr(e.message); return; }
        const btn = $('btnSend');
        if (btn) btn.disabled = true;
        try {
            const r = await fetch('/api/api-cases/dry-run-request', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                ...cred,
                body: JSON.stringify({ case_id: CASE_ID, api_spec: spec }),
            });
            const { ok, data: d } = await parseJsonResponse(r);
            showResponse(d, { errorOnly: !ok || !d.success });
            switchRespTab('body');
            if (!ok) {
                toastErr(d.error || '请求失败');
            } else if (!d.success) {
                toastErr(d.error || '试跑失败');
            }
        } finally {
            if (btn) btn.disabled = false;
        }
    }

    async function runSteps(stepIds) {
        const body = stepIds && stepIds.length ? { step_ids: stepIds } : {};
        const r = await fetch('/api/api-cases/' + CASE_ID + '/run', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            ...cred,
            body: JSON.stringify(body),
        });
        const { ok, data: d } = await parseJsonResponse(r);
        if (!ok) {
            renderRunResultsInPanel({ status: 'error', error: d.error || '执行失败', step_results: [] });
            switchRespTab('run');
            return;
        }
        renderRunResultsInPanel(d);
        switchRespTab('run');
        const first = (d.step_results || [])[0];
        if (first) {
            showResponse({
                status_code: first.api_status_code,
                elapsed_ms: first.api_elapsed_ms,
                response_text: first.api_response_preview,
                response_headers: first.api_response_headers,
                assert_message: first.assert_message,
                error: first.error,
                ok_assert: first.status === 'success',
            });
        }
    }

    function collectSelectedStepIds() {
        const out = [];
        document.querySelectorAll('.api-wb-step-cb:checked').forEach(function (cb) {
            const id = parseInt(cb.getAttribute('data-step-id'), 10);
            if (!isNaN(id)) out.push(id);
        });
        return out;
    }

    async function deleteCase() {
        if (!confirm('确认删除整个用例？')) return;
        const r = await fetch('/api/cases/' + CASE_ID, { method: 'DELETE', ...cred });
        const { ok, data: d } = await parseJsonResponse(r);
        if (!ok || d.success === false) {
            toastErr(d.error || '删除失败');
            return;
        }
        var back = '/api-testing';
        if (PROJECT_ID) back += '?project_id=' + PROJECT_ID;
        window.location.href = back;
    }

    /* ---- Neighbor quick picks (simplified from original) ---- */
    function getNeighbors(editingId, steps) {
        var sorted = sortedSteps(steps);
        if (!sorted.length) return { prevSpec: null, nextSpec: null };
        if (editingId == null) {
            var last = sorted[sorted.length - 1];
            return { prevSpec: parseStepSpec(last), nextSpec: null };
        }
        var idx = sorted.findIndex(function (s) { return s.id === editingId; });
        if (idx < 0) return { prevSpec: null, nextSpec: null };
        return {
            prevSpec: idx > 0 ? parseStepSpec(sorted[idx - 1]) : null,
            nextSpec: idx < sorted.length - 1 ? parseStepSpec(sorted[idx + 1]) : null,
        };
    }

    function renderNeighborQuickPicks() {
        var sumEl = $('nrNeighborSummary');
        if (!sumEl) return;
        var nb = getNeighbors(editingStepId, stepsCache);
        if (!stepsCache.length) {
            sumEl.textContent = '暂无步骤，保存后将出现在左侧列表。';
            return;
        }
        var parts = [];
        if (nb.prevSpec && nb.prevSpec.url) parts.push('可从前一步合并提取变量');
        if (nb.nextSpec) parts.push('可推断下一步所需 {{变量}}');
        sumEl.textContent = parts.length ? parts.join('；') : '当前为独立请求';
    }

    /* ---- Resizer ---- */
    function initResizer() {
        const resizer = $('apiResizer');
        const response = $('apiResponsePane');
        if (!resizer || !response) return;
        let dragging = false;
        resizer.addEventListener('mousedown', function (e) {
            dragging = true;
            resizer.classList.add('dragging');
            e.preventDefault();
        });
        document.addEventListener('mousemove', function (e) {
            if (!dragging) return;
            const main = $('apiWbMain');
            const rect = main.getBoundingClientRect();
            const fromBottom = rect.bottom - e.clientY;
            const pct = Math.min(Math.max(fromBottom / rect.height * 100, 15), 75);
            response.style.height = pct + '%';
        });
        document.addEventListener('mouseup', function () {
            dragging = false;
            resizer.classList.remove('dragging');
        });
    }

    /* ---- Init ---- */
    document.addEventListener('DOMContentLoaded', function () {
        $('nrBodyType').addEventListener('change', syncBodyPanels);
        $('nrAuthType').addEventListener('change', syncAuthPanels);
        $('btnSend').addEventListener('click', sendRequest);
        $('btnSave').addEventListener('click', saveRequest);
        $('btnDeleteStep').addEventListener('click', deleteStep);
        $('btnNewRequest').addEventListener('click', newRequest);
        $('btnNewRequestSide').addEventListener('click', newRequest);
        $('btnRunSelected').addEventListener('click', function () {
            const ids = collectSelectedStepIds();
            if (!ids.length) { toastErr('请先在左侧勾选要运行的接口'); return; }
            runSteps(ids);
        });
        $('btnRunCaseAll').addEventListener('click', function () { runSteps(null); });
        $('btnDeleteCase').addEventListener('click', deleteCase);
        $('btnRefreshReq').addEventListener('click', loadSteps);
        $('btnCopyBody').addEventListener('click', function () {
            navigator.clipboard.writeText($('apiRespBody').value || '').then(function () { toastOk('已复制'); });
        });
        $('btnFormatBody').addEventListener('click', function () {
            try {
                const v = ($('apiRespBody').value || '').trim();
                if (v) $('apiRespBody').value = JSON.stringify(JSON.parse(v), null, 2);
            } catch (e) { toastErr('不是合法 JSON'); }
        });
        document.querySelectorAll('.api-wb-tab[data-req-tab]').forEach(function (btn) {
            btn.addEventListener('click', function () { switchReqTab(btn.getAttribute('data-req-tab')); });
        });
        document.querySelectorAll('.api-wb-tab[data-resp-tab]').forEach(function (btn) {
            btn.addEventListener('click', function () { switchRespTab(btn.getAttribute('data-resp-tab')); });
        });
        document.querySelectorAll('.nr-add-kv').forEach(function (btn) {
            btn.addEventListener('click', function () {
                addKvRow($(btn.getAttribute('data-target')), '', '');
            });
        });
        document.addEventListener('keydown', function (e) {
            if (!$('apiEditorForm') || $('apiEditorForm').classList.contains('hidden')) return;
            if (e.ctrlKey && e.key === 'Enter') { e.preventDefault(); sendRequest(); }
            if (e.ctrlKey && (e.key === 's' || e.key === 'S')) { e.preventDefault(); saveRequest(); }
        });
        initResizer();
        loadSteps().then(function () {
            const sorted = sortedSteps(stepsCache);
            if (sorted.length) selectStep(sorted[0].id);
            else newRequest();
        });
    });
})();
