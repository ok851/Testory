/**
 * API testing shared utilities + list page logic.
 */
(function (global) {
    'use strict';

    const cred = { credentials: 'same-origin' };

    async function parseJsonResponse(r) {
        const text = await r.text();
        try {
            return { ok: r.ok, status: r.status, data: text ? JSON.parse(text) : {} };
        } catch (e) {
            return { ok: r.ok, status: r.status, data: { error: text.slice(0, 200) || '响应不是 JSON' } };
        }
    }

    function escapeHtml(s) {
        if (!s) return '';
        const d = document.createElement('div');
        d.textContent = s;
        return d.innerHTML;
    }

    function toast(msg, type) {
        type = type || 'info';
        const el = document.createElement('div');
        el.className = 'api-toast api-toast-' + type;
        el.textContent = msg;
        document.body.appendChild(el);
        setTimeout(function () {
            el.style.opacity = '0';
            el.style.transition = 'opacity 0.2s';
            setTimeout(function () { el.remove(); }, 220);
        }, 2800);
    }

    function toastErr(msg) {
        if (typeof Swal !== 'undefined') {
            Swal.fire({ icon: 'error', title: msg, showConfirmButton: true });
        } else {
            toast(msg, 'error');
        }
    }

    function toastOk(msg) {
        toast(msg, 'success');
    }

    function showModal(el) {
        el.classList.remove('hidden');
        el.classList.add('flex');
    }

    function hideModal(el) {
        el.classList.add('hidden');
        el.classList.remove('flex');
    }

    function methodBadgeClass(method) {
        const m = (method || 'GET').toUpperCase();
        const map = { GET: 'get', POST: 'post', PUT: 'put', PATCH: 'patch', DELETE: 'delete' };
        return 'method-badge method-badge-' + (map[m] || 'default');
    }

    function statusBadgeClass(code) {
        if (code == null) return 'api-wb-status-err';
        const c = parseInt(code, 10);
        if (c >= 200 && c < 300) return 'api-wb-status-2xx';
        if (c >= 300 && c < 400) return 'api-wb-status-3xx';
        if (c >= 400 && c < 500) return 'api-wb-status-4xx';
        if (c >= 500) return 'api-wb-status-5xx';
        return 'api-wb-status-err';
    }

    function formatBytes(n) {
        if (n == null || isNaN(n)) return '—';
        if (n < 1024) return n + ' B';
        if (n < 1048576) return (n / 1024).toFixed(1) + ' KB';
        return (n / 1048576).toFixed(1) + ' MB';
    }

    function parseStepSpec(step) {
        if (!step) return {};
        try {
            const raw = step.api_spec;
            return typeof raw === 'string' ? JSON.parse(raw || '{}') : (raw || {});
        } catch (e) {
            return {};
        }
    }

    function renderRunResultsHtml(data, opts) {
        opts = opts || {};
        const results = data.step_results || [];
        const dur = data.duration != null ? data.duration + 's' : '—';
        const status = data.status || 'unknown';
        let html = '<div class="px-4 py-3 text-sm border-b border-slate-200 dark:border-gray-700">';
        html += '<span class="font-semibold">执行结果：</span> ' + escapeHtml(status);
        html += ' · 耗时 ' + escapeHtml(String(dur));
        if (data.run_history_id) html += ' · 记录 #' + escapeHtml(String(data.run_history_id));
        if (data.error) html += '<div class="text-red-600 dark:text-red-400 mt-1">' + escapeHtml(data.error) + '</div>';
        if (data.warning) html += '<div class="text-amber-600 dark:text-amber-400 mt-1">' + escapeHtml(data.warning) + '</div>';
        html += '</div>';
        if (!results.length) {
            html += '<p class="p-4 text-sm text-gray-500">无步骤结果</p>';
            return html;
        }
        html += '<div class="api-wb-run-results">';
        results.forEach(function (r, i) {
            const st = r.status || 'unknown';
            const fail = st === 'error' || st === 'failed';
            const step = r.step || {};
            const spec = parseStepSpec(step);
            const method = (spec.method || 'GET').toUpperCase();
            const url = spec.url || '';
            const code = r.api_status_code;
            const ms = r.api_elapsed_ms;
            html += '<div class="api-wb-run-step' + (fail ? ' fail' : '') + '" data-run-idx="' + i + '">';
            html += '<span class="' + methodBadgeClass(method) + '">' + escapeHtml(method) + '</span>';
            html += '<div class="flex-1 min-w-0">';
            html += '<div class="font-mono text-xs truncate">' + escapeHtml(url.slice(0, 100)) + '</div>';
            html += '<div class="text-xs text-gray-500 mt-0.5">';
            if (code != null) html += 'HTTP ' + code;
            if (ms != null) html += (code != null ? ' · ' : '') + ms + 'ms';
            html += ' · ' + (fail ? '失败' : '成功');
            if (r.error) html += ' — ' + escapeHtml(String(r.error).slice(0, 120));
            html += '</div></div></div>';
        });
        html += '</div>';
        return html;
    }

    function showRunResultDrawer(data, opts) {
        opts = opts || {};
        const existing = document.getElementById('apiRunResultDrawer');
        if (existing) existing.remove();
        const drawer = document.createElement('div');
        drawer.id = 'apiRunResultDrawer';
        drawer.className = 'api-run-result-drawer';
        drawer.innerHTML =
            '<div class="api-run-result-panel">' +
            '<div class="api-run-result-head">' +
            '<h3 class="text-base font-semibold">' + escapeHtml(opts.title || '运行结果') + '</h3>' +
            '<button type="button" class="api-wb-btn api-wb-btn-ghost" id="apiRunResultClose">关闭</button>' +
            '</div>' +
            '<div class="api-run-result-body" id="apiRunResultBody">' + renderRunResultsHtml(data, opts) + '</div>' +
            '</div>';
        document.body.appendChild(drawer);
        drawer.addEventListener('click', function (e) {
            if (e.target === drawer) drawer.remove();
        });
        document.getElementById('apiRunResultClose').addEventListener('click', function () {
            drawer.remove();
        });
        const body = document.getElementById('apiRunResultBody');
        if (body && opts.onStepClick) {
            body.querySelectorAll('.api-wb-run-step').forEach(function (el) {
                el.addEventListener('click', function () {
                    const idx = parseInt(el.getAttribute('data-run-idx'), 10);
                    const r = (data.step_results || [])[idx];
                    if (r) opts.onStepClick(r, idx);
                });
            });
        }
        return drawer;
    }

    global.UatApi = {
        cred: cred,
        parseJsonResponse: parseJsonResponse,
        escapeHtml: escapeHtml,
        toast: toast,
        toastErr: toastErr,
        toastOk: toastOk,
        showModal: showModal,
        hideModal: hideModal,
        methodBadgeClass: methodBadgeClass,
        statusBadgeClass: statusBadgeClass,
        formatBytes: formatBytes,
        parseStepSpec: parseStepSpec,
        renderRunResultsHtml: renderRunResultsHtml,
        showRunResultDrawer: showRunResultDrawer,
    };

    /* ---- List page (api_testing.html) ---- */
    if (!document.getElementById('apiProjectSelect')) return;

    function readCaseDetailFmtFromDom() {
        var a = document.getElementById('uatCaseDetailUrlTmpl');
        if (!a || !a.getAttribute('href')) return '/api-testing/case/__ID__';
        try {
            var u = new URL(a.href, window.location.href);
            return u.pathname + (u.search || '');
        } catch (e) {
            var h = a.getAttribute('href') || '';
            return h.split('#')[0] || '/api-testing/case/__ID__';
        }
    }

    function buildCaseDetailUrl(caseId, projId) {
        var fmt = readCaseDetailFmtFromDom();
        if (fmt.indexOf('__ID__') < 0) fmt = '/api-testing/case/__ID__';
        var path = fmt.replace('__ID__', String(caseId));
        if (!projId) return path;
        var join = path.indexOf('?') >= 0 ? '&' : '?';
        return path + join + 'project_id=' + encodeURIComponent(projId);
    }

    try {
        var u0 = new URL(window.location.href);
        var cid0 = u0.searchParams.get('case_id');
        if (cid0 && /^\d+$/.test(cid0)) {
            var p0 = u0.searchParams.get('project_id');
            window.location.replace(buildCaseDetailUrl(parseInt(cid0, 10), p0));
            return;
        }
    } catch (e0) { /* skip */ }

    let projectId = null;
    let addCaseUiMode = false;
    let allCases = [];
    let importApiPreviewItems = null;

    function qs(name) {
        return new URL(window.location.href).searchParams.get(name);
    }

    function setAddCaseTab(fromUi) {
        addCaseUiMode = fromUi;
        document.getElementById('addPanelBlank').classList.toggle('hidden', fromUi);
        document.getElementById('addPanelFromUi').classList.toggle('hidden', !fromUi);
        const bBlank = document.getElementById('addTabBlank');
        const bUi = document.getElementById('addTabFromUi');
        bBlank.className = fromUi
            ? 'flex-1 py-2 rounded-md text-sm font-medium text-gray-600 dark:text-gray-400'
            : 'flex-1 py-2 rounded-md text-sm font-medium bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 shadow-sm';
        bUi.className = !fromUi
            ? 'flex-1 py-2 rounded-md text-sm font-medium text-gray-600 dark:text-gray-400'
            : 'flex-1 py-2 rounded-md text-sm font-medium bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 shadow-sm';
    }

    async function loadProjects() {
        const sel = document.getElementById('apiProjectSelect');
        const r = await fetch('/api/projects', { ...cred });
        const { ok, data: d } = await parseJsonResponse(r);
        if (!ok) {
            sel.innerHTML = '<option value="">加载项目失败</option>';
            toastErr(d.error || '加载项目失败');
            return;
        }
        sel.innerHTML = '<option value="">-- 选择项目 --</option>';
        (d.projects || []).forEach(function (p) {
            const o = document.createElement('option');
            o.value = p.id;
            o.textContent = p.name || ('项目#' + p.id);
            sel.appendChild(o);
        });
        const fromUrl = qs('project_id');
        if (fromUrl && sel.querySelector('option[value="' + fromUrl + '"]')) sel.value = fromUrl;
        sel.addEventListener('change', function () {
            projectId = sel.value ? parseInt(sel.value, 10) : null;
            loadApiCases();
        });
        if (sel.value) {
            projectId = parseInt(sel.value, 10);
            loadApiCases();
        }
    }

    function filterCases(cases, q) {
        q = (q || '').trim().toLowerCase();
        if (!q) return cases;
        return cases.filter(function (c) {
            return (c.name || '').toLowerCase().indexOf(q) >= 0;
        });
    }

    function renderCasesTable(cases) {
        const hint = document.getElementById('apiCaseHint');
        const table = document.getElementById('apiCasesTable');
        const body = document.getElementById('apiCasesBody');
        const searchQ = (document.getElementById('apiCaseSearch') || {}).value || '';
        const filtered = filterCases(cases, searchQ);
        body.innerHTML = '';
        if (!projectId) {
            hint.style.display = 'block';
            table.classList.add('hidden');
            hint.textContent = '请选择项目。';
            return;
        }
        if (!filtered.length) {
            hint.style.display = 'block';
            table.classList.add('hidden');
            hint.textContent = searchQ ? '没有匹配的用例。' : '当前项目下还没有接口用例，请先点「新建用例」创建。';
            return;
        }
        hint.style.display = 'none';
        table.classList.remove('hidden');
        filtered.forEach(function (c) {
            const tr = document.createElement('tr');
            tr.className = 'hover:bg-slate-50 dark:hover:bg-gray-800/50';
            const cnt = c.step_count != null ? c.step_count : 0;
            tr.innerHTML =
                '<td class="px-4 py-3 font-medium">' + escapeHtml(c.name || '') + '</td>' +
                '<td class="px-4 py-3"><span class="inline-flex items-center px-2 py-0.5 rounded-full text-xs bg-slate-100 dark:bg-gray-700">' +
                cnt + ' 请求</span></td>' +
                '<td class="px-4 py-3"><div class="flex flex-wrap gap-2"></div></td>';
            const td = tr.querySelector('td:last-child div');
            function mk(cls, label, fn) {
                const b = document.createElement('button');
                b.className = 'px-3 py-1.5 rounded-md text-xs font-medium ' + cls;
                b.textContent = label;
                b.onclick = fn;
                return b;
            }
            td.appendChild(mk('bg-slate-200 text-slate-800 hover:bg-slate-300 dark:bg-gray-700 dark:text-gray-100', '查看接口', function () {
                window.location.href = buildCaseDetailUrl(c.id, projectId);
            }));
            td.appendChild(mk('bg-amber-500 text-white hover:bg-amber-600', '运行', function () { runApiCase(c.id, c.name); }));
            td.appendChild(mk('bg-slate-200 text-slate-800 hover:bg-slate-300 dark:bg-gray-700 dark:text-gray-100', '复制', function () { duplicateCase(c.id); }));
            td.appendChild(mk('bg-red-600 text-white hover:bg-red-700', '删除', function () { deleteApiCase(c.id); }));
            body.appendChild(tr);
        });
    }

    async function loadApiCases() {
        if (!projectId) {
            allCases = [];
            renderCasesTable([]);
            return;
        }
        const r = await fetch('/api/projects/' + projectId + '/cases?case_type=api', { ...cred });
        const { ok, data: d } = await parseJsonResponse(r);
        if (!ok) {
            allCases = [];
            const hint = document.getElementById('apiCaseHint');
            hint.style.display = 'block';
            document.getElementById('apiCasesTable').classList.add('hidden');
            hint.textContent = d.error || '加载用例失败';
            return;
        }
        allCases = d.cases || [];
        renderCasesTable(allCases);
    }

    async function refreshAddCaseSourceOptions() {
        const sel = document.getElementById('addCaseSource');
        const hint = document.getElementById('addCaseSourceHint');
        sel.innerHTML = '<option value="">不复用（仅新建用例）</option>';
        if (!projectId) {
            hint.textContent = '请先选择项目。';
            return;
        }
        const r = await fetch('/api/projects/' + projectId + '/cases?case_type=api', { ...cred });
        const { ok, data: d } = await parseJsonResponse(r);
        if (!ok) {
            hint.textContent = '无法加载接口用例列表，可仅创建空白用例。';
            return;
        }
        hint.textContent = '选择已有用例时，将复制其下全部 HTTP 请求到新用例。';
        (d.cases || []).forEach(function (c) {
            const o = document.createElement('option');
            o.value = String(c.id);
            o.textContent = (c.name || ('#' + c.id)) + '（' + (c.step_count != null ? c.step_count : 0) + ' 个请求）';
            sel.appendChild(o);
        });
    }

    async function loadUiForAddModal() {
        const sel = document.getElementById('addModalWizUiCaseId');
        if (!projectId) {
            sel.innerHTML = '<option value="">请先选择项目</option>';
            return;
        }
        const r = await fetch('/api/projects/' + projectId + '/cases?case_type=ui', { ...cred });
        const { ok, data: d } = await parseJsonResponse(r);
        if (!ok) {
            sel.innerHTML = '<option value="">加载失败</option>';
            return;
        }
        sel.innerHTML = '<option value="">-- 选择 Web 用例 --</option>';
        (d.cases || []).forEach(function (c) {
            const o = document.createElement('option');
            o.value = c.id;
            o.textContent = (c.name || ('#' + c.id)) + ' (' + (c.step_count || 0) + '步)';
            sel.appendChild(o);
        });
    }

    async function openAddCaseModal() {
        if (!projectId) {
            toastErr('请先选择项目');
            return;
        }
        document.getElementById('addCaseName').value = '';
        document.getElementById('addUiOverrideName').value = '';
        setAddCaseTab(false);
        await refreshAddCaseSourceOptions();
        await loadUiForAddModal();
        showModal(document.getElementById('addCaseModal'));
    }

    function closeAddCaseModal() {
        hideModal(document.getElementById('addCaseModal'));
    }

    async function submitAddCase() {
        if (addCaseUiMode) {
            const src = parseInt(document.getElementById('addModalWizUiCaseId').value, 10);
            if (!src) {
                toastErr('请选择源 Web 用例');
                return;
            }
            const nm = document.getElementById('addUiOverrideName').value.trim();
            const body = { project_id: projectId, source_ui_case_id: src, migrate_api_steps: true };
            if (nm) body.name = nm;
            const r = await fetch('/api/api-cases/from-ui-case', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                ...cred,
                body: JSON.stringify(body),
            });
            const { ok, data: d } = await parseJsonResponse(r);
            if (!ok || !d.success) {
                toastErr(d.error || '创建失败');
                return;
            }
            closeAddCaseModal();
            loadApiCases();
            toastOk('已从 Web 用例迁移接口步骤');
            return;
        }
        const name = document.getElementById('addCaseName').value.trim();
        if (!name) {
            toastErr('请填写新用例名称');
            return;
        }
        const src = (document.getElementById('addCaseSource').value || '').trim();
        if (!src) {
            const r = await fetch('/api/api-cases/from-ui-case', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                ...cred,
                body: JSON.stringify({ project_id: projectId, name: name }),
            });
            const { ok, data: d } = await parseJsonResponse(r);
            if (!ok || !d.success) {
                toastErr(d.error || '创建失败');
                return;
            }
            closeAddCaseModal();
            loadApiCases();
            toastOk('已新建用例');
            return;
        }
        const r = await fetch('/api/api-cases/' + encodeURIComponent(src) + '/duplicate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            ...cred,
            body: JSON.stringify({ name: name }),
        });
        const { ok, data: d } = await parseJsonResponse(r);
        if (!ok || !d.success) {
            toastErr(d.error || '复用失败');
            return;
        }
        closeAddCaseModal();
        loadApiCases();
        toastOk('已新建用例（已复用全部请求）');
    }

    async function duplicateCase(caseId) {
        const r = await fetch('/api/api-cases/' + caseId + '/duplicate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            ...cred,
            body: '{}',
        });
        const { ok, data: d } = await parseJsonResponse(r);
        if (!ok || !d.success) {
            toastErr(d.error || '复制失败');
            return;
        }
        loadApiCases();
        toastOk('已复制');
    }

    async function deleteApiCase(caseId) {
        if (!confirm('确认删除？')) return;
        const r = await fetch('/api/cases/' + caseId, { method: 'DELETE', ...cred });
        const { ok, data: d } = await parseJsonResponse(r);
        if (!ok || d.success === false) {
            toastErr(d.error || '删除失败');
            return;
        }
        loadApiCases();
        toastOk('已删除');
    }

    async function runApiCase(caseId, caseName) {
        const r = await fetch('/api/api-cases/' + caseId + '/run', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            ...cred,
            body: '{}',
        });
        const { ok, data: d } = await parseJsonResponse(r);
        if (!ok) {
            toastErr(d.error || '执行失败');
            return;
        }
        showRunResultDrawer(d, { title: '运行结果 — ' + (caseName || ('#' + caseId)) });
        if (d.success) {
            toastOk('执行完成：' + (d.status || 'success'));
        } else {
            toastErr(d.error || '执行未全部成功');
        }
    }

    function openImportApiModal() {
        if (!projectId) {
            toastErr('请先选择项目');
            return;
        }
        importApiPreviewItems = null;
        document.getElementById('importApiPreviewBox').style.display = 'none';
        document.getElementById('importApiPreviewTable').innerHTML = '';
        document.getElementById('importApiPreviewHint').textContent = '';
        document.getElementById('importApiCommitBtn').disabled = true;
        document.getElementById('importApiFile').value = '';
        document.getElementById('importApiBaseUrl').value = '';
        document.getElementById('importApiCaseName').value = '';
        showModal(document.getElementById('importApiModal'));
    }

    function closeImportApiModal() {
        hideModal(document.getElementById('importApiModal'));
    }

    async function importApiPreview() {
        const fi = document.getElementById('importApiFile');
        const hint = document.getElementById('importApiPreviewHint');
        importApiPreviewItems = null;
        document.getElementById('importApiCommitBtn').disabled = true;
        hint.textContent = '';
        document.getElementById('importApiPreviewBox').style.display = 'none';
        if (!fi.files || !fi.files[0]) {
            toastErr('请选择 JSON 文件');
            return;
        }
        hint.textContent = '解析中…';
        try {
            const text = await fi.files[0].text();
            const baseUrl = document.getElementById('importApiBaseUrl').value.trim();
            const r = await fetch('/api/ai/import/api-spec/preview', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                ...cred,
                body: JSON.stringify({ content: text, base_url: baseUrl }),
            });
            const { ok, data: d } = await parseJsonResponse(r);
            if (!ok || !d.success) {
                hint.textContent = '';
                toastErr(d.error || '预览失败');
                return;
            }
            importApiPreviewItems = d.items || [];
            hint.textContent = '共 ' + importApiPreviewItems.length + ' 条（' + (d.kind || '') + '）';
            document.getElementById('importApiPreviewBox').style.display = 'block';
            let html = '<thead><tr><th>方法</th><th>名称</th><th>URL</th></tr></thead><tbody>';
            importApiPreviewItems.slice(0, 80).forEach(function (it) {
                const sp = it.api_spec || {};
                html += '<tr><td>' + escapeHtml(sp.method || '') + '</td><td>' + escapeHtml(it.name || '') + '</td><td>' + escapeHtml((it.path || sp.url || '').slice(0, 100)) + '</td></tr>';
            });
            html += '</tbody>';
            document.getElementById('importApiPreviewTable').innerHTML = html;
            document.getElementById('importApiCommitBtn').disabled = !importApiPreviewItems.length;
        } catch (e) {
            hint.textContent = '';
            toastErr(e.message || '预览异常');
        }
    }

    async function importApiCommit() {
        if (!projectId || !importApiPreviewItems || !importApiPreviewItems.length) return;
        const name = document.getElementById('importApiCaseName').value.trim();
        if (!name) {
            toastErr('请填写新用例名称');
            return;
        }
        const r = await fetch('/api/ai/import/api-spec/commit', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            ...cred,
            body: JSON.stringify({ project_id: projectId, case_name: name, items: importApiPreviewItems }),
        });
        const { ok, data: d } = await parseJsonResponse(r);
        if (!ok || !d.success) {
            toastErr(d.error || '导入失败');
            return;
        }
        closeImportApiModal();
        loadApiCases();
        toastOk('已创建用例 #' + d.case_id + '，共 ' + (d.steps_created || 0) + ' 个请求');
    }

    document.addEventListener('DOMContentLoaded', function () {
        document.getElementById('btnImportApiSpec').addEventListener('click', openImportApiModal);
        document.getElementById('importApiCancelBtn').addEventListener('click', closeImportApiModal);
        document.getElementById('importApiPreviewBtn').addEventListener('click', function () { void importApiPreview(); });
        document.getElementById('importApiCommitBtn').addEventListener('click', function () { void importApiCommit(); });
        document.getElementById('importApiModal').addEventListener('click', function (e) {
            if (e.target.id === 'importApiModal') closeImportApiModal();
        });
        document.getElementById('btnAddCase').addEventListener('click', openAddCaseModal);
        document.getElementById('btnRefreshCases').addEventListener('click', loadApiCases);
        document.getElementById('addTabBlank').addEventListener('click', function () { setAddCaseTab(false); });
        document.getElementById('addTabFromUi').addEventListener('click', function () { setAddCaseTab(true); });
        document.getElementById('addCaseCancelBtn').addEventListener('click', closeAddCaseModal);
        document.getElementById('addCaseSubmitBtn').addEventListener('click', submitAddCase);
        document.getElementById('addCaseModal').addEventListener('click', function (e) {
            if (e.target.id === 'addCaseModal') closeAddCaseModal();
        });
        var searchEl = document.getElementById('apiCaseSearch');
        if (searchEl) {
            searchEl.addEventListener('input', function () { renderCasesTable(allCases); });
        }
        loadProjects();
        var err = qs('err');
        if (err) {
            var msg = {
                case_not_found: '用例不存在或已删除。',
                not_api_case: '该用例不是接口用例。',
                no_project_access: '无权限查看该用例。',
            }[err] || err;
            toastErr(msg);
            try {
                var clean = new URL(window.location.href);
                clean.searchParams.delete('err');
                window.history.replaceState({}, '', clean.pathname + clean.search);
            } catch (e2) { /* skip */ }
        }
    });
})(typeof window !== 'undefined' ? window : global);
