/**
 * Testory 移动端测试（投屏、点屏录步骤、图像识别）
 */
(function (global) {
    'use strict';

    const INTERACTION_CONTROL = 'control';
    const INTERACTION_ELEMENT = 'record_element';
    const INTERACTION_IMAGE = 'record_image';

    const state = {
        bootstrap: null,
        projectId: null,
        caseId: null,
        steps: [],
        udid: '',
        sessionId: '',
        mirrorUrl: '',
        deviceWidth: 1080,
        deviceHeight: 1920,
        framePreset: 'generic_19_9',
        connected: false,
        mirrorTimer: null,
        pointerDown: null,
        interactionMode: INTERACTION_CONTROL,
        alsoTapOnRecord: true,
    };

    function $(id) {
        return document.getElementById(id);
    }

    async function apiJson(url, opts) {
        const r = await fetch(url, Object.assign({ credentials: 'same-origin' }, opts || {}));
        const data = await r.json().catch(function () { return {}; });
        if (!r.ok && data.error) throw new Error(data.error);
        return data;
    }

    function setStatus(msg, kind) {
        const el = $('msStatus');
        if (!el) return;
        el.textContent = msg || '';
        el.className = 'ms-status' + (kind ? ' ms-status--' + kind : '');
    }

    function stopMirror() {
        if (state.mirrorTimer) {
            clearInterval(state.mirrorTimer);
            state.mirrorTimer = null;
        }
    }

    function startMirror() {
        stopMirror();
        if (!state.mirrorUrl) return;
        const canvas = $('msMirrorCanvas');
        if (!canvas) return;
        const ctx = canvas.getContext('2d');
        const fps = (state.bootstrap && state.bootstrap.mirror_fps) || 8;
        const interval = Math.max(80, Math.floor(1000 / fps));
        state.mirrorTimer = setInterval(async function () {
            try {
                const data = await apiJson(state.mirrorUrl);
                if (!data.success || !data.data) return;
                const img = new Image();
                img.onload = function () {
                    state.deviceWidth = img.width;
                    state.deviceHeight = img.height;
                    canvas.width = img.width;
                    canvas.height = img.height;
                    ctx.drawImage(img, 0, 0);
                    canvas.style.display = 'block';
                    const ph = $('msMirrorPlaceholder');
                    if (ph) ph.style.display = 'none';
                    const dim = $('msDeviceDim');
                    if (dim) dim.textContent = img.width + ' × ' + img.height;
                };
                img.src = 'data:image/png;base64,' + data.data;
            } catch (e) { /* ignore frame errors */ }
        }, interval);
    }

    function mapCanvasToDevice(clientX, clientY) {
        const canvas = $('msMirrorCanvas');
        const wrap = $('msScreenInner');
        if (!canvas || !wrap) return { x: 0, y: 0 };
        const rect = canvas.getBoundingClientRect();
        const scaleX = state.deviceWidth / Math.max(1, rect.width);
        const scaleY = state.deviceHeight / Math.max(1, rect.height);
        const x = Math.round((clientX - rect.left) * scaleX);
        const y = Math.round((clientY - rect.top) * scaleY);
        return {
            x: Math.max(0, Math.min(state.deviceWidth, x)),
            y: Math.max(0, Math.min(state.deviceHeight, y)),
        };
    }

    async function tapAt(clientX, clientY) {
        const pt = mapCanvasToDevice(clientX, clientY);
        await apiJson('/api/mobile/tap-at', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ x: pt.x, y: pt.y, udid: state.udid }),
        });
        setStatus('点击 (' + pt.x + ', ' + pt.y + ')', 'ok');
    }

    async function swipeAt(x1, y1, x2, y2) {
        const p1 = mapCanvasToDevice(x1, y1);
        const p2 = mapCanvasToDevice(x2, y2);
        await apiJson('/api/mobile/swipe-at', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                x1: p1.x, y1: p1.y, x2: p2.x, y2: p2.y, udid: state.udid,
            }),
        });
        setStatus('滑动', 'ok');
    }

    function wireMirrorInteraction() {
        const canvas = $('msMirrorCanvas');
        if (!canvas) return;
        canvas.onmousedown = function (ev) {
            if (!state.connected) return;
            state.pointerDown = { x: ev.clientX, y: ev.clientY, t: Date.now() };
        };
        canvas.onmouseup = function (ev) {
            if (!state.connected || !state.pointerDown) return;
            const dx = Math.abs(ev.clientX - state.pointerDown.x);
            const dy = Math.abs(ev.clientY - state.pointerDown.y);
            if (dx < 8 && dy < 8) {
                if (state.interactionMode === INTERACTION_ELEMENT || state.interactionMode === INTERACTION_IMAGE) {
                    recordStepAt(ev.clientX, ev.clientY).catch(function (e) {
                        setStatus(e.message || String(e), 'err');
                    });
                } else {
                    tapAt(ev.clientX, ev.clientY).catch(function (e) {
                        setStatus(e.message || String(e), 'err');
                    });
                }
            } else if (state.interactionMode !== INTERACTION_CONTROL) {
                setStatus('录制模式下请单击，勿拖拽', 'warn');
            } else {
                swipeAt(
                    state.pointerDown.x,
                    state.pointerDown.y,
                    ev.clientX,
                    ev.clientY
                ).catch(function (e) {
                    setStatus(e.message || String(e), 'err');
                });
            }
            state.pointerDown = null;
        };
        canvas.onmouseleave = function () {
            state.pointerDown = null;
        };
    }

    function applyFramePreset(presetId) {
        state.framePreset = presetId || 'generic_19_9';
        const presets = (state.bootstrap && state.bootstrap.frame_presets) || [];
        const p = presets.find(function (x) { return x.id === state.framePreset; }) || presets[0];
        const shell = $('msPhoneShell');
        if (!shell || !p) return;
        const ar = p.frame_height / p.frame_width;
        shell.style.maxWidth = Math.min(420, p.frame_width) + 'px';
        shell.style.aspectRatio = p.frame_width + ' / ' + p.frame_height;
        shell.style.borderRadius = (p.corner_radius || 24) + 'px';
        const notch = $('msPhoneNotch');
        if (notch) {
            notch.style.display = p.notch === 'none' ? 'none' : 'block';
            notch.className = 'ms-phone-notch ms-phone-notch--' + (p.notch || 'punch');
        }
    }

    function renderSteps() {
        const box = $('msStepsList');
        if (!box) return;
        if (!state.steps.length) {
            box.innerHTML = '<p class="ms-muted">暂无用例步骤，可从 AI 生成或「添加步骤」。</p>';
            return;
        }
        box.innerHTML = state.steps.map(function (st, i) {
            const layer = (st.automation_layer || 'web').toLowerCase();
            const badge = layer === 'android' ? 'Android' : layer;
            const act = st.action || '';
            const shortSel = (st.selector_value || '').length > 48
                ? (st.selector_value || '').slice(0, 48) + '…'
                : (st.selector_value || '');
            return (
                '<div class="ms-step-item" data-step-id="' + st.id + '">' +
                '<span class="ms-step-ord">' + (st.step_order || i + 1) + '</span>' +
                '<span class="ms-step-act">' + act + '</span>' +
                '<span class="ms-step-badge">' + badge + '</span>' +
                '<span class="ms-step-desc">' + (st.description || shortSel || '') + '</span>' +
                '</div>'
            );
        }).join('');
    }

    function setInteractionMode(mode) {
        state.interactionMode = mode || INTERACTION_CONTROL;
        document.querySelectorAll('.ms-mode-btn').forEach(function (btn) {
            const on = btn.getAttribute('data-mode') === state.interactionMode;
            btn.classList.toggle('ms-mode-btn--active', on);
        });
        const meta = $('msRecordMeta');
        if (!meta) return;
        if (state.interactionMode === INTERACTION_ELEMENT) {
            meta.textContent = '当前：元素录制（点屏写入 tap + UiAutomator 定位）';
        } else if (state.interactionMode === INTERACTION_IMAGE) {
            meta.textContent = '当前：图像录制（点屏写入 tap_image + 模板）';
        } else {
            meta.textContent = '当前：操控模式（单击点击、拖拽滑动）';
        }
    }

    async function recordStepAt(clientX, clientY) {
        if (!state.caseId) {
            setStatus('请先选择用例再录制步骤', 'warn');
            return;
        }
        const pt = mapCanvasToDevice(clientX, clientY);
        const kind = state.interactionMode === INTERACTION_IMAGE ? 'image' : 'element';
        setStatus('正在录制步骤…', '');
        const data = await apiJson('/api/mobile/record-step', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                case_id: state.caseId,
                x: pt.x,
                y: pt.y,
                udid: state.udid,
                kind: kind,
                also_tap: !!state.alsoTapOnRecord,
            }),
        });
        await loadSteps();
        const desc = (data.step && data.step.description) || ('已录制 (' + pt.x + ',' + pt.y + ')');
        setStatus(desc, 'ok');
    }

    async function loadProjects() {
        const data = await apiJson('/api/projects');
        const sel = $('msProjectSelect');
        if (!sel) return;
        const list = data.projects || data.data || [];
        sel.innerHTML = '<option value="">选择项目</option>' + list.map(function (p) {
            return '<option value="' + p.id + '">' + (p.name || ('#' + p.id)) + '</option>';
        }).join('');
    }

    async function loadCases() {
        const sel = $('msCaseSelect');
        if (!sel || !state.projectId) {
            if (sel) sel.innerHTML = '<option value="">先选择项目</option>';
            return;
        }
        const data = await apiJson('/api/projects/' + state.projectId + '/cases');
        const cases = data.cases || [];
        sel.innerHTML = '<option value="">选择用例</option>' + cases
            .filter(function (c) { return (c.case_type || 'ui') !== 'api'; })
            .map(function (c) {
                return '<option value="' + c.id + '">' + (c.name || ('用例#' + c.id)) + '</option>';
            }).join('');
    }

    async function loadSteps() {
        if (!state.caseId) {
            state.steps = [];
            renderSteps();
            return;
        }
        const data = await apiJson('/api/mobile/cases/' + state.caseId + '/steps');
        state.steps = data.steps || [];
        renderSteps();
        const androidCount = state.steps.filter(function (s) {
            return (s.automation_layer || 'web') === 'android';
        }).length;
        $('msCaseMeta').textContent = state.steps.length + ' 步' +
            (androidCount ? '（' + androidCount + ' 步 Android）' : '（建议将步骤设为 Android 层）');
    }

    function renderDeviceList(devices) {
        const devSel = $('msDeviceSelect');
        if (!devSel) return;
        const list = devices || [];
        if (!list.length) {
            devSel.innerHTML = '<option value="">（未发现设备）</option>';
            return;
        }
        devSel.innerHTML = list.map(function (d) {
            const label = (d.display_name || d.udid) + ' (' + (d.state || '') + ')';
            return '<option value="' + d.udid + '">' + label + '</option>';
        }).join('');
        const ready = list.find(function (d) { return d.state === 'device'; });
        if (ready) devSel.value = ready.udid;
    }

    function updateDeviceHelp(health, devices) {
        const el = $('msDeviceHelp');
        if (!el) return;
        const boot = state.bootstrap || {};
        const list = devices || [];
        const authorized = list.filter(function (d) { return d.state === 'device'; });
        const unauthorized = list.filter(function (d) { return d.state === 'unauthorized'; });
        if (!health || !health.adb_ok) {
            el.innerHTML =
                '<strong>ADB 不可用：</strong>' + (health && health.adb_message ? health.adb_message : '未检测到 adb') +
                '。请到右上角用户菜单 → <a href="/plugin-market" class="text-emerald-600 underline">插件市场</a> 安装「Android Platform-Tools (adb)」，安装后点 ⟳ 刷新本页即可，无需手改 .env。';
            return;
        }
        if (unauthorized.length) {
            el.textContent =
                '手机已连接但未授权：请在手机上开启「USB 调试」并点「允许此计算机调试」，然后点 ⟳ 刷新。';
            return;
        }
        if (!list.length) {
            var adbHint = (boot.adb_path ? 'adb 已就绪（' + boot.adb_path + '）。' : '');
            el.textContent = adbHint + '未发现手机：请 USB 连接、选文件传输/MTP、开启 USB 调试后点 ⟳ 刷新。';
            return;
        }
        if (!authorized.length) {
            el.textContent =
                '设备状态异常（非 device）：请换线/换口、重装手机驱动，或在命令行执行 adb kill-server && adb start-server 后刷新。';
            return;
        }
        el.textContent = '已识别 ' + authorized.length + ' 台设备，点击「一键连接」后在右侧画面用鼠标操作。';
    }

    async function bootstrap() {
        const data = await apiJson('/api/mobile/testing/bootstrap');
        state.bootstrap = data;
        const presetSel = $('msFramePreset');
        if (presetSel && data.frame_presets) {
            presetSel.innerHTML = data.frame_presets.map(function (p) {
                return '<option value="' + p.id + '">' + p.label + '</option>';
            }).join('');
            presetSel.value = 'generic_19_9';
            applyFramePreset('generic_19_9');
        }
        const devices = (data.health && data.health.devices) || data.devices || [];
        renderDeviceList(devices);
        updateDeviceHelp(data.health, devices);
        if (data.health && data.health.adb_ok && data.adb_plugin_installed && data.adb_path) {
            setStatus('adb 已自动配置（插件市场），连接手机后点「一键连接」', 'ok');
        }
        if (data.default_device && data.default_device.state === 'device') {
            setStatus('检测到设备，可点击「一键连接」', 'ok');
        } else if (!data.health || !data.health.adb_ok) {
            setStatus((data.health && data.health.adb_message) || '请先配置 ADB', 'warn');
        } else if (devices.some(function (d) { return d.state === 'unauthorized'; })) {
            setStatus('请在手机上允许 USB 调试', 'warn');
        } else if (!devices.length) {
            setStatus('未检测到 USB 设备，请检查连接与开发者选项', 'warn');
        }
    }

    async function autoConnect() {
        setStatus('正在连接…', '');
        const preset = ($('msFramePreset') || {}).value;
        const udid = ($('msDeviceSelect') || {}).value || '';
        const data = await apiJson('/api/mobile/auto-connect', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                udid: udid,
                frame_preset: preset,
                try_appium: true,
            }),
        });
        state.udid = data.udid || '';
        state.sessionId = data.session_id || '';
        state.mirrorUrl = data.mirror_frame_url || '';
        state.connected = true;
        if (data.device) {
            state.deviceWidth = data.device.width || 1080;
            state.deviceHeight = data.device.height || 1920;
        }
        applyFramePreset(preset);
        const appSel = $('msAppSelect');
        if (appSel && data.apps) {
            appSel.innerHTML = data.apps.map(function (a) {
                const sel = a.package === data.suggested_app_package ? ' selected' : '';
                return '<option value="' + a.package + '"' + sel + '>' + a.label + ' (' + a.package + ')</option>';
            }).join('');
        }
        $('msConnectBadge').textContent = '已连接';
        $('msConnectBadge').className = 'ms-connect-badge ms-connect-badge--on';
        let msg = '已连接 ' + (data.device && data.device.model ? data.device.model : state.udid);
        if (data.scrcpy_started) msg += ' · scrcpy 窗口已打开';
        if (data.appium_error) msg += ' · Appium: ' + data.appium_error;
        else if (data.appium_connected) msg += ' · Appium 已就绪';
        setStatus(msg, 'ok');
        startMirror();
    }

    async function disconnect() {
        stopMirror();
        try {
            await apiJson('/api/mobile/disconnect', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ session_id: state.sessionId }),
            });
        } catch (e) { /* ignore */ }
        state.connected = false;
        state.sessionId = '';
        state.mirrorUrl = '';
        $('msConnectBadge').textContent = '未连接';
        $('msConnectBadge').className = 'ms-connect-badge';
        setStatus('已断开', '');
    }

    async function runCase() {
        if (!state.caseId) {
            setStatus('请先选择用例', 'warn');
            return;
        }
        setStatus('正在执行用例…', '');
        const data = await apiJson('/api/mobile/run', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ case_id: state.caseId, udid: state.udid }),
        });
        if (data.success) {
            setStatus('执行成功，耗时 ' + (data.duration || '') + 's', 'ok');
        } else {
            setStatus(data.error || '执行失败', 'err');
        }
    }

    async function aiGenerate() {
        const goal = ($('msAiGoal') || {}).value || '';
        if (!goal.trim()) {
            setStatus('请输入测试目标', 'warn');
            return;
        }
        setStatus('AI 生成中…', '');
        const data = await apiJson('/api/ai/task/plan', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                goal: goal,
                project_name: ($('msProjectSelect') || {}).selectedOptions[0].text || '',
                platform_type: 'android',
            }),
        });
        if (!data.success || !data.plan) {
            setStatus(data.error || '生成失败', 'err');
            return;
        }
        const preview = $('msAiPreview');
        if (preview) {
            preview.value = JSON.stringify(data.plan, null, 2);
            preview.style.display = 'block';
        }
        if (state.caseId && data.plan.steps && data.plan.steps.length) {
            for (let i = 0; i < data.plan.steps.length; i++) {
                const st = data.plan.steps[i];
                await apiJson('/api/steps', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        case_id: state.caseId,
                        action: st.action,
                        selector_type: st.strategy || st.selector_type || 'accessibility_id',
                        selector_value: st.selector_value || '',
                        input_value: st.input_value || '',
                        description: st.description || '',
                        automation_layer: 'android',
                        mobile_spec: typeof st.mobile_spec === 'string' ? st.mobile_spec : JSON.stringify(st.mobile_spec || {}),
                    }),
                });
            }
            await loadSteps();
            setStatus('已生成并追加 ' + data.plan.steps.length + ' 个 Android 步骤', 'ok');
        } else {
            setStatus('已生成用例预览（请选择用例后再次生成以写入步骤）', 'ok');
        }
    }

    function wireUi() {
        $('msBtnConnect').addEventListener('click', function () {
            autoConnect().catch(function (e) { setStatus(e.message, 'err'); });
        });
        $('msBtnDisconnect').addEventListener('click', function () {
            disconnect();
        });
        $('msBtnRefreshDevices').addEventListener('click', function () {
            bootstrap().catch(function (e) { setStatus(e.message, 'err'); });
        });
        $('msFramePreset').addEventListener('change', function () {
            applyFramePreset(this.value);
        });
        $('msProjectSelect').addEventListener('change', function () {
            state.projectId = this.value ? parseInt(this.value, 10) : null;
            state.caseId = null;
            loadCases().catch(function () {});
            loadSteps();
        });
        $('msCaseSelect').addEventListener('change', function () {
            state.caseId = this.value ? parseInt(this.value, 10) : null;
            loadSteps().catch(function (e) { setStatus(e.message, 'err'); });
        });
        $('msBtnRun').addEventListener('click', function () {
            runCase().catch(function (e) { setStatus(e.message, 'err'); });
        });
        $('msBtnAi').addEventListener('click', function () {
            aiGenerate().catch(function (e) { setStatus(e.message, 'err'); });
        });
        $('msBtnOpenSteps').addEventListener('click', function () {
            if (state.caseId) {
                window.open('/list_steps?case_id=' + state.caseId, '_blank');
            }
        });
        ['msModeControl', 'msModeElement', 'msModeImage'].forEach(function (id) {
            const btn = $(id);
            if (!btn) return;
            btn.addEventListener('click', function () {
                setInteractionMode(btn.getAttribute('data-mode'));
            });
        });
        const tapChk = $('msAlsoTapVisible');
        if (tapChk) {
            tapChk.addEventListener('change', function () {
                state.alsoTapOnRecord = !!tapChk.checked;
            });
            state.alsoTapOnRecord = !!tapChk.checked;
        }
        setInteractionMode(INTERACTION_CONTROL);
        wireMirrorInteraction();
    }

    async function init() {
        if (global.__MOBILE_STUDIO_DISABLED__) {
            setStatus(global.__MOBILE_STUDIO_DISABLED_REASON__ || '移动端未启用', 'warn');
            return;
        }
        wireUi();
        await loadProjects();
        await bootstrap();
    }

    global.MobileTesting = { init: init, state: state };
    global.MobileStudio = global.MobileTesting;
    document.addEventListener('DOMContentLoaded', function () {
        init().catch(function (e) {
            setStatus('初始化失败: ' + (e.message || e), 'err');
        });
    });
})(window);
