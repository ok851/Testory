/**
 * 移动端 Playground：Tap / Assert / Query / Act
 */
(function (global) {
    'use strict';

    function $(id) {
        return document.getElementById(id);
    }

    function getUdid() {
        var ms = global.MobileStudio || global.MobileTesting;
        if (ms && ms.state && ms.state.udid) return ms.state.udid;
        if (ms && typeof ms.getUdid === 'function') return ms.getUdid() || '';
        return '';
    }

    function refreshMirror() {
        var ms = global.MobileStudio || global.MobileTesting;
        if (ms && typeof ms.refreshMirror === 'function') ms.refreshMirror();
    }

    function setStatus(msg, kind) {
        var el = $('msPlaygroundStatus');
        if (!el) return;
        if (!msg) {
            el.hidden = true;
            el.textContent = '';
            el.className = 'ms-playground-status';
            return;
        }
        el.hidden = false;
        el.textContent = msg;
        el.className = 'ms-playground-status ms-playground-status--' + (kind || 'info');
    }

    function showReplay(url, replayMeta) {
        var link = $('msPlaygroundReplayLink');
        if (!link) return;
        var runId = (replayMeta && replayMeta.run_id) || '';
        if (!url && !runId) {
            link.hidden = true;
            link.removeAttribute('href');
            updateSaveButton('');
            return;
        }
        if (url) {
            link.href = url;
            link.hidden = false;
            if (!runId) {
                var m = url.match(/\/replay\/([^/]+)\//);
                runId = m ? m[1] : '';
            }
        }
        updateSaveButton(runId);
    }

    var lastReplayRunId = '';

    function getCaseId() {
        var ms = global.MobileStudio || global.MobileTesting;
        if (ms && ms.state && ms.state.caseId) return ms.state.caseId;
        return null;
    }

    function updateSaveButton(runId) {
        lastReplayRunId = runId || '';
        var btn = $('msPgSaveStepsBtn');
        if (!btn) return;
        btn.hidden = !(lastReplayRunId && getCaseId());
    }

    async function saveToCase() {
        var caseId = getCaseId();
        if (!caseId) {
            setStatus('请先在左侧选择用例', 'warn');
            return;
        }
        if (!lastReplayRunId) {
            setStatus('暂无可保存的回放记录', 'warn');
            return;
        }
        setStatus('正在保存步骤…', 'busy');
        var data = await postJson('/api/mobile/playground/save-steps', {
            run_id: lastReplayRunId,
            case_id: caseId,
        });
        if (data.success) {
            setStatus(data.message || ('已保存 ' + (data.step_count || 0) + ' 步'), 'ok');
            var ms = global.MobileStudio || global.MobileTesting;
            if (ms && typeof ms.loadSteps === 'function') ms.loadSteps();
        } else {
            setStatus(data.error || '保存失败', 'err');
        }
    }

    async function postJson(path, body) {
        var r = await fetch(path, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'same-origin',
            body: JSON.stringify(body || {}),
        });
        var data = await r.json().catch(function () { return {}; });
        if (!r.ok && !data.error) data.error = data.message || ('HTTP ' + r.status);
        return data;
    }

    function requireDevice() {
        var udid = getUdid();
        if (!udid) {
            setStatus('请先连接设备', 'warn');
            return null;
        }
        return udid;
    }

    function switchTab(name) {
        document.querySelectorAll('.ms-playground-tab').forEach(function (btn) {
            btn.classList.toggle('ms-playground-tab--active', btn.getAttribute('data-pg-tab') === name);
        });
        ['tap', 'assert', 'query', 'act'].forEach(function (tab) {
            var panel = $('msPlaygroundPanel' + tab.charAt(0).toUpperCase() + tab.slice(1));
            if (panel) panel.hidden = tab !== name;
        });
    }

    async function runTap() {
        var udid = requireDevice();
        if (!udid) return;
        var locate = ($('msPgTapInput') || {}).value || '';
        if (!locate.trim()) {
            setStatus('请输入要点击的元素描述', 'warn');
            return;
        }
        setStatus('正在查找并点击…', 'busy');
        showReplay('');
        var data = await postJson('/api/mobile/playground/tap', { udid: udid, locate: locate.trim() });
        if (data.success) {
            setStatus(data.message || '已点击', 'ok');
            showReplay(data.replay_url || '', data.replay);
            refreshMirror();
        } else {
            setStatus(data.error || data.message || '点击失败', 'err');
            showReplay(data.replay_url || '', data.replay);
        }
    }

    async function runAssert() {
        var udid = requireDevice();
        if (!udid) return;
        var condition = ($('msPgAssertInput') || {}).value || '';
        if (!condition.trim()) {
            setStatus('请输入要检查的内容', 'warn');
            return;
        }
        setStatus('正在理解画面并检查…', 'busy');
        showReplay('');
        var data = await postJson('/api/mobile/playground/assert', { udid: udid, condition: condition.trim() });
        if (data.success) {
            setStatus(data.message || '检查通过', 'ok');
            showReplay(data.replay_url || '', data.replay);
        } else {
            setStatus(data.error || data.message || '检查未通过', 'err');
            showReplay(data.replay_url || '', data.replay);
        }
    }

    async function runQuery() {
        var udid = requireDevice();
        if (!udid) return;
        var prompt = ($('msPgQueryInput') || {}).value || '';
        if (!prompt.trim()) {
            setStatus('请输入要读取的内容', 'warn');
            return;
        }
        setStatus('正在从画面读取…', 'busy');
        showReplay('');
        var resEl = $('msPgQueryResult');
        if (resEl) resEl.hidden = true;
        var data = await postJson('/api/mobile/playground/query', { udid: udid, prompt: prompt.trim() });
        if (data.success) {
            setStatus('读取完成', 'ok');
            showReplay(data.replay_url || '', data.replay);
            if (resEl) {
                resEl.textContent = data.data || data.message || '';
                resEl.hidden = false;
            }
        } else {
            setStatus(data.error || '读取失败', 'err');
            showReplay(data.replay_url || '', data.replay);
        }
    }

    async function runAct() {
        var udid = requireDevice();
        if (!udid) return;
        var goal = ($('msPgActInput') || {}).value || '';
        if (!goal.trim()) {
            setStatus('请输入要完成的目标', 'warn');
            return;
        }
        setStatus('正在规划并执行，请稍候…', 'busy');
        showReplay('');
        var btn = $('msPgActBtn');
        if (btn) btn.disabled = true;
        try {
            var data = await postJson('/api/mobile/playground/act', { udid: udid, goal: goal.trim() });
            if (data.success) {
                var stepsN = (data.steps || []).length;
                setStatus((data.message || '已完成') + (stepsN ? '（' + stepsN + ' 步）' : ''), 'ok');
                showReplay(data.replay_url || '', data.replay);
                refreshMirror();
            } else {
                setStatus(data.error || '执行未完成', 'err');
                showReplay(data.replay_url || '', data.replay);
            }
        } finally {
            if (btn) btn.disabled = false;
        }
    }

    function bindEvents() {
        document.querySelectorAll('.ms-playground-tab').forEach(function (btn) {
            btn.addEventListener('click', function () {
                switchTab(btn.getAttribute('data-pg-tab') || 'tap');
            });
        });
        var tapBtn = $('msPgTapBtn');
        if (tapBtn) tapBtn.addEventListener('click', function () { runTap().catch(function (e) { setStatus(e.message, 'err'); }); });
        var assertBtn = $('msPgAssertBtn');
        if (assertBtn) assertBtn.addEventListener('click', function () { runAssert().catch(function (e) { setStatus(e.message, 'err'); }); });
        var queryBtn = $('msPgQueryBtn');
        if (queryBtn) queryBtn.addEventListener('click', function () { runQuery().catch(function (e) { setStatus(e.message, 'err'); }); });
        var actBtn = $('msPgActBtn');
        if (actBtn) actBtn.addEventListener('click', function () { runAct().catch(function (e) { setStatus(e.message, 'err'); }); });
        var tapInput = $('msPgTapInput');
        if (tapInput) tapInput.addEventListener('keydown', function (e) { if (e.key === 'Enter') runTap(); });
        var saveBtn = $('msPgSaveStepsBtn');
        if (saveBtn) saveBtn.addEventListener('click', function () { saveToCase().catch(function (e) { setStatus(e.message, 'err'); }); });
        var caseSel = $('msCaseSelect');
        if (caseSel) caseSel.addEventListener('change', function () { updateSaveButton(lastReplayRunId); });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', bindEvents);
    } else {
        bindEvents();
    }
})(typeof window !== 'undefined' ? window : this);
