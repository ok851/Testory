/**
 * Testory 移动端同步 — PC 仅负责配对与已同步用例/步骤管理。
 * 录制、回放、运行均在手机 Testory 助手 App 内完成。
 */
(function (global) {
    'use strict';

    var state = {
        projectId: null,
        caseId: null,
        stepCount: 0,
        udid: '',
        connected: false,
        deviceTab: 'real',
        realDevices: [],
        assistantInstalled: false,
        assistantConnected: false,
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
        setBtn('msBtnConnect', !con);
        setBtn('msBtnDisconnect', con);
        setBtn('msBtnInstallPlugin', con && !state.assistantInstalled);
        setBtn('msBtnOpenSteps', hasCase);
    }

    function setBtn(id, enabled) {
        var el = $(id); if (el) el.disabled = !enabled;
    }

    function updateCaseMeta() {
        var el = $('msCaseMeta'); if (!el) return;
        if (!state.caseId) {
            el.textContent = '选择项目与用例后可查看步骤数量';
            return;
        }
        el.textContent = '用例 #' + state.caseId + ' · 共 ' + state.stepCount + ' 步（手机同步后在此编辑）';
    }

    async function refreshDeviceList() {
        try {
            var data = await apiJson('/api/mobile/devices');
            state.realDevices = data.devices || [];
            var sel = $('msDeviceSelect'); if (!sel) return;
            sel.innerHTML = '<option value="">— 选择设备 —</option>';
            state.realDevices.forEach(function (d) {
                sel.innerHTML += '<option value="' + escapeHtml(d.udid || '') + '">' + escapeHtml((d.model || d.udid || '') + ' [' + (d.state || '') + ']') + '</option>';
            });
        } catch (e) { setStatus('获取设备失败: ' + e.message, 'err'); }
    }

    async function connectDevice() {
        var sel = $('msDeviceSelect');
        var udid = sel ? sel.value : '';
        if (!udid) { setStatus('请先选择设备', 'warn'); return; }
        setStatus('正在连接…', 'warn');
        try {
            var data = await apiJson('/api/mobile/connect', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ udid: udid }),
            });
            state.connected = true; state.udid = udid;
            state.assistantInstalled = !!data.assistant_installed;
            state.assistantConnected = !!data.assistant_connected;
            updateConnectBadge(); updateAssistantBadge(); refreshAllButtons();
            setStatus('设备已连接，可安装助手 APK', 'ok');
        } catch (e) {
            state.connected = false; updateConnectBadge(); refreshAllButtons();
            setStatus('连接失败: ' + e.message, 'err');
        }
    }

    async function disconnectDevice() {
        if (!state.udid) return;
        try {
            await apiJson('/api/mobile/disconnect', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ udid: state.udid }),
            });
        } catch (e) {}
        state.connected = false; state.udid = ''; state.assistantConnected = false;
        updateConnectBadge(); updateAssistantBadge(); refreshAllButtons();
        setStatus('设备已断开', 'ok');
    }

    async function wirelessConnect() {
        var ip = ($('msWirelessIp') && $('msWirelessIp').value || '').trim();
        var port = ($('msWirelessPort') && $('msWirelessPort').value || '5555').trim();
        var code = ($('msWirelessCode') && $('msWirelessCode').value || '').trim();
        if (!ip) { setStatus('请输入手机 IP', 'warn'); return; }
        setStatus('正在无线连接…', 'warn');
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
        setStatus('正在安装助手…', 'warn');
        try {
            var data = await apiJson('/api/mobile/assistant/install', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ udid: state.udid }),
            });
            if (data.success) {
                state.assistantInstalled = true;
                updateAssistantBadge(); refreshAllButtons();
                setStatus('安装成功！请在手机开启 Testory 助手无障碍', 'ok');
            } else {
                setStatus('安装失败: ' + (data.error || ''), 'err');
            }
        } catch (e) { setStatus('安装失败: ' + e.message, 'err'); }
    }

    async function loadProjects() {
        try {
            var data = await apiJson('/api/projects');
            var sel = $('msProjectSelect'); if (!sel) return;
            sel.innerHTML = '<option value="">— 选择项目 —</option>';
            (data.projects || []).forEach(function (p) {
                sel.innerHTML += '<option value="' + p.id + '">' + escapeHtml(p.name || '') + '</option>';
            });
        } catch (e) {}
    }

    async function loadCases() {
        var pid = state.projectId || ($('msProjectSelect') && $('msProjectSelect').value) || '';
        if (!pid) return;
        state.projectId = parseInt(pid, 10);
        try {
            var data = await apiJson('/api/cases?project_id=' + pid);
            var sel = $('msCaseSelect'); if (!sel) return;
            sel.innerHTML = '<option value="">— 选择用例 —</option>';
            (data.test_cases || []).forEach(function (c) {
                sel.innerHTML += '<option value="' + c.id + '">' + escapeHtml(c.case_name || '') + '</option>';
            });
        } catch (e) {}
    }

    async function loadStepCount() {
        var cid = state.caseId || ($('msCaseSelect') && $('msCaseSelect').value) || '';
        if (!cid) { state.stepCount = 0; updateCaseMeta(); refreshAllButtons(); return; }
        state.caseId = parseInt(cid, 10);
        try {
            var data = await apiJson('/api/cases/' + state.caseId + '/steps');
            state.stepCount = (data.steps || []).length;
        } catch (e) {
            state.stepCount = 0;
        }
        updateCaseMeta(); refreshAllButtons();
    }

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
            var data = await apiJson('/api/mobile/sync/pair/init', {
                method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({}),
            });
            var code = data.pair_code || '';
            var el = $('msPairCode'); if (el) el.textContent = code;
            var box = $('msPairBox'); if (box && code) box.style.display = '';
            updatePairExpiry(data.expires_at || (data.expires_in ? (Date.now() / 1000 + data.expires_in) : 0));
            if (code) toast('配对码: ' + code, 'ok');
        } catch (e) {
            try {
                var fallback = await apiJson('/api/mobile/device/pair', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({}),
                });
                var code2 = fallback.pair_code || '';
                var el2 = $('msPairCode'); if (el2) el2.textContent = code2;
                var box2 = $('msPairBox'); if (box2 && code2) box2.style.display = '';
                updatePairExpiry(fallback.expires_at || 0);
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
            if (data.ready) refreshDeviceList();
        } catch (e) { setStatus('检测失败: ' + e.message, 'err'); }
    }

    function wireUi() {
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
        $('msBtnRefreshPair').addEventListener('click', refreshPairCode);
        $('msBtnRefreshCases').addEventListener('click', function () { loadCases().then(loadStepCount); });
        $('msBtnOpenSteps').addEventListener('click', function () {
            if (state.caseId) window.open('/list_steps?case_id=' + state.caseId, '_blank');
        });

        var projSel = $('msProjectSelect');
        if (projSel) projSel.addEventListener('change', function () {
            state.projectId = parseInt(projSel.value || '0', 10);
            state.caseId = null;
            loadCases();
        });
        var caseSel = $('msCaseSelect');
        if (caseSel) caseSel.addEventListener('change', loadStepCount);
    }

    async function init() {
        if (document.getElementById('__mobileStudioDisabledJson')) return;
        wireUi();
        var urlHint = $('msSyncUrlHint'); if (urlHint) urlHint.textContent = window.location.origin;
        await loadProjects();
        refreshDeviceList().catch(function () {});
        refreshPairCode().catch(function () {});

        var params = new URLSearchParams(window.location.search);
        var urlCaseId = parseInt(params.get('case_id') || '', 10);
        if (urlCaseId > 0) {
            state.caseId = urlCaseId;
            var cs = $('msCaseSelect'); if (cs) cs.value = String(urlCaseId);
            await loadStepCount();
        }
    }

    global.MobileTesting = { init: init, state: state, loadStepCount: loadStepCount, setStatus: setStatus };
    global.MobileStudio = global.MobileTesting;
    document.addEventListener('DOMContentLoaded', function () {
        init().catch(function (e) { setStatus('初始化失败: ' + (e.message || e), 'err'); });
    });
})(window);
