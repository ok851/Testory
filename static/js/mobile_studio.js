/**
 * Testory 移动端测试 v3 — 连接、录制、回放
 * Design inspired by SoloPi: 步骤可视化、隐式等待、弹窗处理、跨分辨率适配
 */
(function (global) {
    'use strict';

    var state = {
        bootstrap: null, projectId: null, caseId: null, steps: [],
        udid: '', deviceWidth: 1080, deviceHeight: 1920,
        connected: false, diagnosticsCache: null, recordingSession: [],
        deviceTab: 'real', realDevices: [], deviceWarnings: [],
        assistantPollTimer: null, assistantInstalled: false, assistantConnected: false,
        liveSteps: [], recording: false, recordingPaused: false,
        selectedLiveIndex: -1, sessionId: '',
        agentWsUrl: '',
        recordingWs: null,
        replayHandleDialogs: true, replayCrossResolution: true, replayStepTimeout: 30,
        toastTimer: null,
    };

    function $(id) { return document.getElementById(id); }

    function toast(msg, kind) {
        var el = $('msToast'); if (!el) return;
        el.textContent = msg; el.className = 'ms-toast ms-toast--' + (kind || 'ok');
        el.style.display = ''; clearTimeout(state.toastTimer);
        state.toastTimer = setTimeout(function () { el.style.display = 'none'; }, 3500);
    }

    function setStatus(msg, kind) {
        var el = $('msStatus'); if (!el) return;
        el.textContent = msg || ''; el.className = 'ms-status' + (kind ? ' ms-status--' + kind : '');
        if (kind === 'err' || kind === 'ok') toast(msg, kind);
    }

    async function apiJson(url, opts) {
        var r = await fetch(url, Object.assign({ credentials: 'same-origin' }, opts || {}));
        var data = await r.json().catch(function () { return {}; });
        if (!r.ok) throw new Error(data.error || ('HTTP ' + r.status));
        if (data.success === false && data.error) throw new Error(data.error);
        return data;
    }

    function escapeHtml(s) { return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }

    // ─── UI helpers ────────────────────────────────────────

    function updateConnectBadge() {
        var b = $('msConnectBadge'); if (!b) return;
        if (state.connected) { b.textContent = '已连接'; b.className = 'ms-connect-badge ms-connect-badge--connected'; }
        else { b.textContent = '未连接'; b.className = 'ms-connect-badge'; }
    }

    function updateAssistantBadge() {
        var b = $('msAssistantBadge'); if (!b) return;
        if (state.assistantConnected) { b.textContent = '插件 ✓'; b.className = 'ms-assistant-badge ms-assistant-badge--on'; }
        else if (state.assistantInstalled) { b.textContent = '插件 已安装'; b.className = 'ms-assistant-badge ms-assistant-badge--installed'; }
        else { b.textContent = '插件 —'; b.className = 'ms-assistant-badge'; }
    }

    function refreshAllButtons() {
        var con = state.connected && !!state.udid;
        var hasCase = !!state.caseId;
        var rec = state.recording;
        var hasSteps = state.liveSteps.length > 0;

        setBtn('msBtnConnect', !con);
        setBtn('msBtnDisconnect', con);
        setBtn('msBtnInstallPlugin', con && !state.assistantInstalled);
        setBtn('msBtnRun', con && hasCase);
        setBtn('msBtnOpenSteps', hasCase);
        // 记录按钮不再要求先选用例，连接设备即可
        setBtn('msBtnStartRecord', con && !rec);
        setBtn('msBtnReplaySession', con && hasSteps);
        setBtn('msBtnReplayFrom', con && hasSteps && state.selectedLiveIndex >= 0);
        setBtn('msBtnClearSession', hasSteps && !rec);

        var hint = $('msRecordHint');
        if (hint) hint.style.display = rec ? '' : 'none';
    }

    function setBtn(id, enabled) {
        var el = $(id); if (el) el.disabled = !enabled;
    }

    // ─── 设备连接 ──────────────────────────────────────────

    function getRealDeviceList() { return state.realDevices || []; }

    async function refreshDeviceList() {
        try {
            var data = await apiJson('/api/mobile/devices');
            state.realDevices = data.devices || [];
            state.deviceWarnings = data.warnings || [];
            var sel = $('msDeviceSelect'); if (!sel) return;
            sel.innerHTML = '<option value="">— 选择设备 —</option>';
            state.realDevices.forEach(function (d) {
                sel.innerHTML += '<option value="' + escapeHtml(d.udid || '') + '">' + escapeHtml((d.model || d.udid || '') + ' [' + (d.state || '') + ']') + '</option>';
            });
            toast('发现 ' + state.realDevices.length + ' 台设备', 'ok');
        } catch (e) { setStatus('获取设备失败: ' + e.message, 'err'); }
    }

    async function connectDevice() {
        var udid = '';
        if (state.deviceTab === 'real') { var s = $('msDeviceSelect'); udid = s ? s.value : ''; }
        if (!udid) { setStatus('请先选择设备', 'warn'); return; }
        setStatus('正在连接…', 'warn');
        try {
            var data = await apiJson('/api/mobile/connect', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ udid: udid }),
            });
            state.connected = true; state.udid = udid;
            state.sessionId = data.session_id || '';
            state.deviceWidth = data.device_width || 1080;
            state.deviceHeight = data.device_height || 1920;
            state.assistantInstalled = !!data.assistant_installed;
            state.assistantConnected = !!data.assistant_connected;
            state.agentWsUrl = data.agent_ws_url || state.agentWsUrl || '';
            updateConnectBadge(); updateAssistantBadge(); refreshAllButtons();
            setStatus('设备已连接 — ' + state.deviceWidth + 'x' + state.deviceHeight, 'ok');
        } catch (e) {
            state.connected = false; updateConnectBadge(); refreshAllButtons();
            setStatus('连接失败: ' + e.message, 'err');
        }
    }

    async function disconnectDevice() {
        if (!state.udid) return;
        try { await apiJson('/api/mobile/disconnect', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ udid: state.udid }) }); } catch (e) {}
        state.connected = false; state.udid = ''; state.assistantConnected = false;
        updateConnectBadge(); updateAssistantBadge(); refreshAllButtons();
        setStatus('设备已断开', 'ok');
    }

    async function wirelessConnect() {
        var ip = ($('msWirelessIp') && $('msWirelessIp').value || '').trim();
        var port = ($('msWirelessPort') && $('msWirelessPort').value || '5555').trim();
        var code = ($('msWirelessCode') && $('msWirelessCode').value || '').trim();
        if (!ip) { setStatus('请输入手机 IP', 'warn'); return; }
        setStatus('正在无线连接 ' + ip + ':' + port + '…', 'warn');
        try {
            var data = await apiJson('/api/mobile/wireless/connect', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ ip: ip, port: port, pairing_code: code }),
            });
            state.connected = true; state.udid = data.udid || (ip + ':' + port);
            updateConnectBadge(); refreshAllButtons();
            setStatus('无线连接成功', 'ok');
        } catch (e) { setStatus('连接失败: ' + e.message, 'err'); }
    }

    async function installAssistant() {
        if (!state.udid) { setStatus('请先连接设备', 'warn'); return; }
        setStatus('正在安装设备插件…', 'warn');
        try {
            var data = await apiJson('/api/mobile/assistant/install', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ udid: state.udid }),
            });
            if (data.success) {
                state.assistantInstalled = true;
                updateAssistantBadge(); refreshAllButtons();
                setStatus('插件安装成功！请在手机上开启「Testory 无障碍服务」', 'ok');
                setTimeout(function () {
                    apiJson('/api/mobile/assistant/status?udid=' + encodeURIComponent(state.udid))
                        .then(function (s) { state.assistantConnected = !!(s && s.plugin_ready); updateAssistantBadge(); })
                        .catch(function () {});
                }, 2500);
            } else { setStatus('安装失败: ' + (data.error || ''), 'err'); }
        } catch (e) { setStatus('安装失败: ' + e.message, 'err'); }
    }

    // ─── 项目/用例 ─────────────────────────────────────────

    async function loadProjects() {
        try {
            var data = await apiJson('/api/projects');
            var sel = $('msProjectSelect'); if (!sel) return;
            sel.innerHTML = '<option value="">— 选择项目 —</option>';
            (data.projects || []).forEach(function (p) { sel.innerHTML += '<option value="' + p.id + '">' + escapeHtml(p.name || '') + '</option>'; });
        } catch (e) {}
    }

    async function loadCases() {
        var pid = state.projectId || ($('msProjectSelect') && $('msProjectSelect').value) || '';
        if (!pid) return; state.projectId = parseInt(pid, 10);
        try {
            var data = await apiJson('/api/cases?project_id=' + pid);
            var sel = $('msCaseSelect'); if (!sel) return;
            sel.innerHTML = '<option value="">— 选择用例 —</option>';
            (data.test_cases || []).forEach(function (c) { sel.innerHTML += '<option value="' + c.id + '">' + escapeHtml(c.case_name || '') + '</option>'; });
        } catch (e) {}
    }

    async function loadSteps() {
        var cid = state.caseId || ($('msCaseSelect') && $('msCaseSelect').value) || '';
        if (!cid) return; state.caseId = parseInt(cid, 10);
        try {
            var data = await apiJson('/api/cases/' + state.caseId + '/steps');
            state.steps = data.steps || [];
            state.liveSteps = state.steps.map(function (s, i) { return { index: i, step: s }; });
            renderLiveSteps();
            refreshAllButtons();
        } catch (e) {}
    }

    // ─── 录制 ──────────────────────────────────────────────

    // 录制不再要求先选用例，连接设备即可录制（步骤保存在临时会话中）
    async function startRecording() {
        if (!state.udid) { setStatus('请先连接设备', 'warn'); return; }
        setStatus('正在返回桌面，准备录制…', 'warn');
        try {
            var data = await apiJson('/api/mobile/recording/start', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ udid: state.udid, screenshot_per_step: false }),
            });
            if (!data || data.success === false) {
                throw new Error((data && data.error) || '录制启动失败');
            }
            state.recording = true; state.recordingPaused = false;
            state.liveSteps = [];
            refreshAllButtons(); startAssistantPolling(); connectRecordingWs();
            setStatus((data && data.message) || '录制就绪，请在手机上操作', 'ok');
        } catch (e) { setStatus('启动录制失败: ' + e.message, 'err'); refreshAllButtons(); return; }
    }

    async function syncRecordingEndedFromDevice() {
        state.recording = false;
        state.recordingPaused = false;
        stopAssistantPolling();
        disconnectRecordingWs();
        try {
            var data = await apiJson('/api/mobile/recording/steps?udid=' + encodeURIComponent(state.udid) + '&case_id=' + (state.caseId || 0));
            var steps = data.live_steps || data.steps || [];
            if (steps.length) {
                state.liveSteps = steps.map(function (s, i) { return { index: i, step: s }; });
            }
        } catch (e) {}
        refreshAllButtons();
        renderLiveSteps();
        if (state.caseId) await loadSteps();
        setStatus('手机端已结束录制，共 ' + state.liveSteps.length + ' 步', 'ok');
    }

    function connectRecordingWs() {
        disconnectRecordingWs();
        var wsUrl = state.agentWsUrl || (state.bootstrap && state.bootstrap.agent_ws_url) || '';
        if (!wsUrl) return;
        try {
            var ws = new WebSocket(wsUrl);
            state.recordingWs = ws;
            ws.onmessage = function (ev) {
                if (!state.recording) return;
                try {
                    var msg = JSON.parse(ev.data);
                    if (msg.type === 'step' && msg.payload && msg.payload.step) {
                        var step = msg.payload.step;
                        if (msg.payload.udid && state.udid && msg.payload.udid !== state.udid) return;
                        state.liveSteps.push({ index: state.liveSteps.length, step: step });
                        renderLiveSteps(); refreshAllButtons();
                    } else if (msg.type === 'recording_stopped') {
                        syncRecordingEndedFromDevice();
                    }
                } catch (e) {}
            };
            ws.onclose = function () { if (state.recording) setTimeout(connectRecordingWs, 1500); };
        } catch (e) {}
    }

    function disconnectRecordingWs() {
        if (state.recordingWs) {
            try { state.recordingWs.close(); } catch (e) {}
            state.recordingWs = null;
        }
    }

    async function stopRecording() {
        try { await apiJson('/api/mobile/recording/stop', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ udid: state.udid }) }); } catch (e) {}
        state.recording = false; state.recordingPaused = false;
        stopAssistantPolling(); disconnectRecordingWs(); refreshAllButtons();
        await pollSteps();
        if (state.caseId) await loadSteps();
        setStatus('录制已停止，共 ' + state.liveSteps.length + ' 步', 'ok');
    }

    async function pauseRecording() {
        try { await apiJson('/api/mobile/recording/pause', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ udid: state.udid }) }); } catch (e) {}
        state.recordingPaused = true; refreshAllButtons(); setStatus('已暂停', 'warn');
    }

    async function resumeRecording() {
        try { await apiJson('/api/mobile/recording/resume', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ udid: state.udid }) }); } catch (e) {}
        state.recordingPaused = false; refreshAllButtons(); setStatus('继续录制', 'ok');
    }

    // ─── 步骤轮询 ──────────────────────────────────────────

    var _pollTimer = null;
    function startAssistantPolling() { stopAssistantPolling(); pollSteps(); _pollTimer = setInterval(pollSteps, 150); }
    function stopAssistantPolling() { if (_pollTimer) { clearInterval(_pollTimer); _pollTimer = null; } }

    async function pollSteps() {
        if (!state.connected || !state.udid) return;
        if (!state.recording && !state.liveSteps.length) return;
        try {
            var data = await apiJson('/api/mobile/recording/steps?udid=' + encodeURIComponent(state.udid) + '&case_id=' + (state.caseId || 0));
            if (state.recording && data.recording_active === false) {
                await syncRecordingEndedFromDevice();
                return;
            }
            if (!state.recording) return;
            var steps = data.live_steps || data.steps || [];
            if (steps.length > state.liveSteps.length) {
                state.liveSteps = steps.map(function (s, i) { return { index: i, step: s }; });
                renderLiveSteps(); refreshAllButtons();
            }
        } catch (e) {
            if (state.recording) setStatus('步骤同步异常: ' + e.message, 'warn');
        }
    }

    // ─── 步骤渲染 ──────────────────────────────────────────

    function renderLiveSteps() {
        var el = $('msLiveSteps'); var cnt = $('msLiveStepCount'); if (!el) return;
        if (state.liveSteps.length === 0) {
            el.innerHTML = '<div class="ms-empty-hint"><i class="fas fa-hand-pointer"></i>连接设备并开始录制后<br>操作步骤将显示在这里</div>';
        } else {
            var html = '';
            state.liveSteps.forEach(function (item, idx) {
                var s = item.step || {};
                var action = s.action || 'tap';
                var desc = s.description || fmtStep(s);
                var cls = 'ms-step-live' + (idx === state.selectedLiveIndex ? ' ms-step-live--active' : '');
                html += '<div class="' + cls + '" data-idx="' + idx + '"><strong>' + (idx + 1) + '.</strong> <span class="ms-step-action-tag">' + escapeHtml(action) + '</span> ' + escapeHtml(desc.substring(0, 50)) + '</div>';
            });
            el.innerHTML = html;
        }
        if (cnt) cnt.textContent = String(state.liveSteps.length);
        el.querySelectorAll('[data-idx]').forEach(function (n) { n.addEventListener('click', function () { selectLiveStep(parseInt(n.getAttribute('data-idx'), 10)); }); });
        refreshAllButtons();
    }

    function fmtStep(s) {
        var ms = s.mobile_spec || {}; if (typeof ms === 'string') try { ms = JSON.parse(ms); } catch (e) { ms = {}; }
        switch (s.action) {
            case 'tap': return '点击 (' + (ms.x || ms.viewport_coord?.x || '?') + ', ' + (ms.y || ms.viewport_coord?.y || '?') + ')';
            case 'swipe': return '滑动 (' + (ms.x1 || '?') + ',' + (ms.y1 || '?') + ') → (' + (ms.x2 || '?') + ',' + (ms.y2 || '?') + ')';
            case 'input_text': return '输入 "' + (s.input_value || '') + '"';
            case 'long_press': return '长按 (' + (ms.x || '?') + ', ' + (ms.y || '?') + ')';
            case 'wait': return '等待 ' + (s.wait_ms || 1000) + 'ms';
            default: return s.action || '未知';
        }
    }

    function selectLiveStep(idx) {
        state.selectedLiveIndex = idx; renderLiveSteps(); showStepDetail(idx);
    }

    // ─── 步骤编辑器 ────────────────────────────────────────

    function showStepDetail(idx) {
        var empty = $('msStepDetailEmpty'), panel = $('msStepDetailPanel');
        if (idx < 0 || idx >= state.liveSteps.length) { if (empty) empty.style.display = ''; if (panel) panel.style.display = 'none'; return; }
        if (empty) empty.style.display = 'none';
        if (panel) panel.style.display = '';
        var s = (state.liveSteps[idx] || {}).step || {};
        var ms = s.mobile_spec || {}; if (typeof ms === 'string') try { ms = JSON.parse(ms); } catch (e) { ms = {}; }
        var vc = ms.viewport_coord || {};
        setVal('msStepEditAction', s.action || 'tap');
        setVal('msStepEditX', ms.x || vc.x || '');
        setVal('msStepEditY', ms.y || vc.y || '');
        setVal('msStepEditX2', ms.x2 || '');
        setVal('msStepEditY2', ms.y2 || '');
        setVal('msStepEditInput', s.input_value || '');
        setVal('msStepEditWait', s.wait_ms || 1000);
        setVal('msStepEditDesc', s.description || '');
        toggleFields(s.action || 'tap');
    }

    function setVal(id, v) { var e = $(id); if (e) e.value = v; }
    function toggleFields(a) {
        var c = $('msStepCoordGroup'), sw = $('msStepSwipeGroup'), inp = $('msStepInputGroup'), w = $('msStepWaitGroup');
        if (c) c.style.display = (a === 'tap' || a === 'long_press' || a === 'swipe') ? '' : 'none';
        if (sw) sw.style.display = (a === 'swipe') ? '' : 'none';
        if (inp) inp.style.display = (a === 'input_text') ? '' : 'none';
        if (w) w.style.display = (a === 'wait') ? '' : 'none';
    }

    async function saveStepEdit() {
        if (state.selectedLiveIndex < 0) return;
        var s = (state.liveSteps[state.selectedLiveIndex] || {}).step || {};
        var ms = s.mobile_spec || {}; if (typeof ms === 'string') ms = {};
        s.action = ($('msStepEditAction') && $('msStepEditAction').value) || 'tap';
        ms.x = parseInt(($('msStepEditX') && $('msStepEditX').value) || 0);
        ms.y = parseInt(($('msStepEditY') && $('msStepEditY').value) || 0);
        if (s.action === 'swipe') { ms.x1 = ms.x; ms.y1 = ms.y; ms.x2 = parseInt(($('msStepEditX2') && $('msStepEditX2').value) || 0); ms.y2 = parseInt(($('msStepEditY2') && $('msStepEditY2').value) || 0); }
        s.mobile_spec = ms; s.input_value = ($('msStepEditInput') && $('msStepEditInput').value) || '';
        s.wait_ms = parseInt(($('msStepEditWait') && $('msStepEditWait').value) || 1000);
        s.description = ($('msStepEditDesc') && $('msStepEditDesc').value) || '';
        state.liveSteps[state.selectedLiveIndex].step = s;
        try {
            await apiJson('/api/mobile/recording/steps/update', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ index: state.selectedLiveIndex, step: s, case_id: state.caseId }) });
            renderLiveSteps(); setStatus('已保存', 'ok');
        } catch (e) { setStatus('保存失败: ' + e.message, 'err'); }
    }

    async function deleteStep() {
        if (state.selectedLiveIndex < 0) return;
        try {
            await apiJson('/api/mobile/recording/steps/delete', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ index: state.selectedLiveIndex, case_id: state.caseId }) });
            state.liveSteps.splice(state.selectedLiveIndex, 1); state.selectedLiveIndex = -1;
            renderLiveSteps(); showStepDetail(-1); setStatus('已删除', 'ok');
        } catch (e) { setStatus('删除失败: ' + e.message, 'err'); }
    }

    // ─── 回放 ──────────────────────────────────────────────

    async function replayAll() {
        if (!state.connected || state.liveSteps.length === 0) { setStatus('无步骤可回放', 'warn'); return; }
        setStatus('正在返回桌面，准备执行…', 'warn');
        try {
            var data = await apiJson('/api/mobile/replay-actions', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    udid: state.udid, steps: state.liveSteps.map(function (it) { return it.step; }),
                    handle_dialogs: state.replayHandleDialogs, cross_resolution: state.replayCrossResolution,
                    step_timeout_ms: (state.replayStepTimeout || 30) * 1000,
                    device_width: state.deviceWidth, device_height: state.deviceHeight,
                }),
            });
            setStatus(data.success ? '回放完成 ✓' : '回放失败: ' + (data.error || '第' + data.failed_at + '步出错'), data.success ? 'ok' : 'err');
        } catch (e) { setStatus('回放出错: ' + e.message, 'err'); }
    }

    async function replayFrom() {
        if (state.selectedLiveIndex < 0) { setStatus('请先选择起始步骤', 'warn'); return; }
        var steps = state.liveSteps.slice(state.selectedLiveIndex);
        setStatus('正在返回桌面，准备执行…', 'warn');
        try {
            var data = await apiJson('/api/mobile/replay-actions', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    udid: state.udid, steps: steps.map(function (it) { return it.step; }),
                    handle_dialogs: state.replayHandleDialogs, cross_resolution: state.replayCrossResolution,
                    step_timeout_ms: (state.replayStepTimeout || 30) * 1000,
                    device_width: state.deviceWidth, device_height: state.deviceHeight,
                }),
            });
            setStatus(data.success ? '回放完成 ✓' : '失败: ' + (data.error || ''), data.success ? 'ok' : 'err');
        } catch (e) { setStatus('出错: ' + e.message, 'err'); }
    }

    function clearSteps() {
        state.liveSteps = []; state.selectedLiveIndex = -1;
        renderLiveSteps(); showStepDetail(-1); setStatus('已清空', 'ok');
    }

    async function runCase() {
        if (!state.caseId) { setStatus('请先选择用例', 'warn'); return; }
        setStatus('正在执行用例…', 'warn');
        try {
            var data = await apiJson('/api/cases/' + state.caseId + '/run', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ udid: state.udid, platform: 'android' }),
            });
            setStatus(data.success ? '执行完成 ✓' : '执行失败: ' + (data.error || ''), data.success ? 'ok' : 'err');
        } catch (e) { setStatus('出错: ' + e.message, 'err'); }
    }

    // ─── 配对码 ────────────────────────────────────────────

    var _pairExpiryTimer = null;

    function updatePairExpiry(expiresAt) {
        var el = $('msPairExpiry');
        if (!el) return;
        if (_pairExpiryTimer) { clearInterval(_pairExpiryTimer); _pairExpiryTimer = null; }
        if (!expiresAt) { el.textContent = ''; return; }
        function tick() {
            var left = Math.max(0, Math.floor(expiresAt - Date.now() / 1000));
            el.textContent = left > 0 ? ('剩余 ' + left + 's') : '已过期，请刷新';
            if (left <= 0 && _pairExpiryTimer) { clearInterval(_pairExpiryTimer); _pairExpiryTimer = null; }
        }
        tick();
        _pairExpiryTimer = setInterval(tick, 1000);
    }

    async function refreshPairCode() {
        try {
            var data = await apiJson('/api/mobile/sync/pair/init', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({}) });
            var code = data.pair_code || '';
            var el = $('msPairCode'); if (el) el.textContent = code;
            var box = $('msPairBox'); if (box && code) box.style.display = '';
            updatePairExpiry(data.expires_at || (data.expires_in ? (Date.now() / 1000 + data.expires_in) : 0));
            if (code) toast('配对码: ' + code + '（' + (data.expires_in || 120) + 's 有效）', 'ok');
        } catch (e) {
            try {
                var fallback = await apiJson('/api/mobile/device/pair', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({}) });
                var code2 = fallback.pair_code || '';
                var el2 = $('msPairCode'); if (el2) el2.textContent = code2;
                var box2 = $('msPairBox'); if (box2 && code2) box2.style.display = '';
                updatePairExpiry(fallback.expires_at || (fallback.expires_in ? (Date.now() / 1000 + fallback.expires_in) : 0));
                if (code2) toast('配对码: ' + code2, 'ok');
            } catch (e2) { setStatus('生成配对码失败', 'err'); }
        }
    }

    async function checkEnv() {
        setStatus('检测中…', 'warn');
        try {
            var data = await apiJson('/api/mobile/check-env');
            var el = $('msEnvStatus'); if (!el) return;
            el.textContent = data.ready ? ('就绪 — ADB 已安装, ' + data.device_count + ' 台设备') : ('异常 — ' + (data.reason || '请检查 ADB'));
            el.className = 'ms-env-status ms-env-status--' + (data.ready ? 'ok' : 'err');
            setStatus(data.ready ? '环境就绪' : '环境异常', data.ready ? 'ok' : 'err');
            if (data.ready) refreshDeviceList();
        } catch (e) { setStatus('检测失败: ' + e.message, 'err'); }
    }

    // ─── UI 绑定 ────────────────────────────────────────────

    function wireUi() {
        // 标签切换
        ['msTabReal', 'msTabWireless'].forEach(function (id) {
            var btn = $(id); if (!btn) return;
            btn.addEventListener('click', function () {
                state.deviceTab = btn.getAttribute('data-tab');
                document.querySelectorAll('.ms-device-tab').forEach(function (t) { t.classList.remove('ms-device-tab--active'); });
                btn.classList.add('ms-device-tab--active');
                document.querySelectorAll('.ms-device-panel').forEach(function (p) { p.classList.remove('ms-device-panel--active'); });
                var panel = $(state.deviceTab === 'real' ? 'msPanelReal' : 'msPanelWireless');
                if (panel) panel.classList.add('ms-device-panel--active');
            });
        });

        $('msBtnEnvCheck').addEventListener('click', checkEnv);
        $('msBtnRefreshDevices').addEventListener('click', refreshDeviceList);
        $('msBtnConnect').addEventListener('click', connectDevice);
        $('msBtnDisconnect').addEventListener('click', disconnectDevice);
        $('msBtnWirelessConnect').addEventListener('click', wirelessConnect);
        $('msBtnInstallPlugin').addEventListener('click', installAssistant);
        $('msBtnStartRecord').addEventListener('click', startRecording);
        $('msBtnReplaySession').addEventListener('click', replayAll);
        $('msBtnReplayFrom').addEventListener('click', replayFrom);
        $('msBtnClearSession').addEventListener('click', clearSteps);
        $('msBtnRun').addEventListener('click', runCase);
        $('msBtnRefreshPair').addEventListener('click', refreshPairCode);
        $('msBtnSaveStep').addEventListener('click', saveStepEdit);
        $('msBtnDeleteStep').addEventListener('click', deleteStep);
        $('msBtnOpenSteps').addEventListener('click', function () { if (state.caseId) window.open('/list_steps?case_id=' + state.caseId, '_blank'); });

        var projSel = $('msProjectSelect');
        if (projSel) projSel.addEventListener('change', function () { state.projectId = parseInt(projSel.value || '0', 10); loadCases(); });
        var caseSel = $('msCaseSelect');
        if (caseSel) caseSel.addEventListener('change', function () { state.caseId = parseInt(caseSel.value || '0', 10); loadSteps(); });

        var actionSel = $('msStepEditAction');
        if (actionSel) actionSel.addEventListener('change', function () { toggleFields(actionSel.value); });

        var chk1 = $('msChkHandleDialogs'); if (chk1) chk1.addEventListener('change', function () { state.replayHandleDialogs = !!chk1.checked; });
        var chk2 = $('msChkCrossResolution'); if (chk2) chk2.addEventListener('change', function () { state.replayCrossResolution = !!chk2.checked; });
        var timeoutInp = $('msReplayTimeout'); if (timeoutInp) timeoutInp.addEventListener('change', function () { state.replayStepTimeout = parseInt(timeoutInp.value, 10) || 30; });
    }

    // ─── 初始化 ────────────────────────────────────────────

    async function init() {
        if (document.getElementById('__mobileStudioDisabledJson')) return;
        wireUi();
        var urlHint = $('msSyncUrlHint'); if (urlHint) urlHint.textContent = window.location.origin;
        await loadProjects();
        refreshDeviceList().catch(function () {});
        refreshPairCode().catch(function () {});

        // 启动时检测环境
        try {
            var cfg = await apiJson('/api/mobile/env-config');
            state.bootstrap = cfg || {};
            state.agentWsUrl = (cfg && cfg.agent_ws_url) || state.agentWsUrl || '';
            if (cfg && cfg.auto_connect_default && (cfg.real_devices || []).some(function (d) { return d.state === 'device'; })) {
                setTimeout(function () { connectDevice().catch(function () {}); }, 500);
            }
        } catch (e) {}

        var params = new URLSearchParams(window.location.search);
        var urlCaseId = parseInt(params.get('case_id') || '', 10);
        if (urlCaseId > 0) {
            state.caseId = urlCaseId;
            var cs = $('msCaseSelect'); if (cs) cs.value = String(urlCaseId);
            await loadSteps();
        }
    }

    global.MobileTesting = { init: init, state: state, loadSteps: loadSteps, setStatus: setStatus, getUdid: function () { return state.udid || ''; } };
    global.MobileStudio = global.MobileTesting;
    document.addEventListener('DOMContentLoaded', function () { init().catch(function (e) { setStatus('初始化失败: ' + (e.message || e), 'err'); }); });
})(window);