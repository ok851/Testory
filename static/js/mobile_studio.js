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
        mirrorAbort: null,
        mirrorBackend: 'screencap',
        mirrorWsUrl: '',
        mirrorStreamUrl: '',
        scrcpyPlayer: null,
        pointerDown: null,
        interactionMode: INTERACTION_CONTROL,
        alsoTapOnRecord: true,
        selectedModelId: 'pixel_7',
        autoStartAttempted: false,
        modelSwitchBusy: false,
        emulatorPollToken: null,
        diagnosticsCache: null,
        diagnosticsCacheAt: 0,
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
        state.mirrorRunning = false;
        if (state.scrcpyPlayer) {
            state.scrcpyPlayer.stop();
            state.scrcpyPlayer = null;
        }
        if (state.mirrorAbort) {
            state.mirrorAbort.abort();
            state.mirrorAbort = null;
        }
        if (state.mirrorTimer) {
            clearTimeout(state.mirrorTimer);
            state.mirrorTimer = null;
        }
    }

    function resetMirrorUi() {
        const canvas = $('msMirrorCanvas');
        const ph = $('msMirrorPlaceholder');
        if (canvas) {
            canvas.style.display = 'none';
            try {
                const ctx = canvas.getContext('2d');
                if (ctx) ctx.clearRect(0, 0, canvas.width || 0, canvas.height || 0);
            } catch (e) { /* ignore */ }
        }
        if (ph) ph.style.display = '';
        const dim = $('msDeviceDim');
        if (dim) {
            dim.textContent = '分辨率 —';
            dim.removeAttribute('data-live-fps');
        }
        updateMirrorModeBadge('idle', '');
    }

    function updateMirrorModeBadge(mode, detail) {
        const badge = $('msMirrorMode');
        if (!badge) return;
        const m = mode || 'idle';
        badge.className = 'ms-mirror-mode ms-mirror-mode--' + m;
        if (m === 'scrcpy') {
            badge.textContent = '高帧率 scrcpy';
            badge.title = detail || 'H.264 硬件视频流（WebCodecs）';
        } else if (m === 'screencap') {
            badge.textContent = '截图投屏';
            badge.title = detail || 'adb screencap 轮询，帧率较低';
        } else {
            badge.textContent = '未连接';
            badge.title = detail || '启动模拟器或连接真机后显示投屏方式';
        }
    }

    function showMirrorCanvas() {
        const canvas = $('msMirrorCanvas');
        const ph = $('msMirrorPlaceholder');
        if (canvas) canvas.style.display = 'block';
        if (ph) ph.style.display = 'none';
    }

    function isEmulatorUdid(udid) {
        return (udid || '').indexOf('emulator-') === 0;
    }

    function scrcpyMirrorFailed(msg) {
        updateMirrorModeBadge('screencap', msg || '高帧率投屏失败');
        if (isEmulatorUdid(state.udid)) {
            setStatus(
                (msg || '高帧率投屏失败') + '。请点「停止」→「启动模拟器」重试；或在插件市场安装 scrcpy。',
                'err'
            );
            return;
        }
        setStatus((msg || 'scrcpy 无画面') + '，降级为截图模式', 'warn');
        startScreencapMirror();
    }

    function buildScrcpyPlayer() {
        const canvas = $('msMirrorCanvas');
        if (!canvas || !global.ScrcpyMirrorPlayer) return null;
        return new global.ScrcpyMirrorPlayer({
            canvas: canvas,
            wsUrl: state.mirrorWsUrl,
            maxReconnect: 2,
            noFrameTimeoutMs: 8000,
            onFirstFrame: function () {
                showMirrorCanvas();
                updateMirrorModeBadge('scrcpy', 'H.264 硬件视频流');
                const c = $('msMirrorCanvas');
                if (c && c.width && c.height) {
                    state.deviceWidth = c.width;
                    state.deviceHeight = c.height;
                    syncPhoneAspectFromDevice();
                }
            },
            onFps: function (fps) {
                const dim = $('msDeviceDim');
                if (!dim) return;
                const base = state.deviceWidth + ' × ' + state.deviceHeight;
                dim.textContent = base + ' · ' + fps + ' fps · scrcpy';
            },
            onReconnect: function (attempt, max) {
                setStatus('投屏短暂中断，正在重连 (' + attempt + '/' + max + ')…', 'warn');
            },
            onFallback: function (msg) {
                if (!state.mirrorRunning) return;
                if (state.scrcpyPlayer) {
                    state.scrcpyPlayer.stop();
                    state.scrcpyPlayer = null;
                }
                scrcpyMirrorFailed(msg);
            },
            onError: function (msg) {
                if (state.mirrorRunning) setStatus('投屏: ' + msg, 'warn');
            },
        });
    }

    function startScrcpyHttpMirror() {
        if (!state.mirrorStreamUrl) {
            startScrcpyWsMirror();
            return;
        }
        state.mirrorRunning = true;
        state.scrcpyPlayer = buildScrcpyPlayer();
        if (!state.scrcpyPlayer) {
            scrcpyMirrorFailed('浏览器不支持 WebCodecs');
            return;
        }
        setStatus('高帧率投屏连接中（HTTP 流）…', '');
        state.scrcpyPlayer.startHttp(state.mirrorStreamUrl).catch(function (e) {
            if (!state.mirrorRunning) return;
            state.scrcpyPlayer = null;
            if (state.mirrorWsUrl) {
                setStatus('HTTP 流失败，尝试 WebSocket…', 'warn');
                startScrcpyWsMirror();
                return;
            }
            scrcpyMirrorFailed(e.message || String(e));
        });
    }

    function startScrcpyWsMirror() {
        if (!state.mirrorWsUrl || !global.ScrcpyMirrorPlayer) {
            scrcpyMirrorFailed('缺少 scrcpy WebSocket 地址');
            return;
        }
        state.mirrorRunning = true;
        state.scrcpyPlayer = buildScrcpyPlayer();
        if (!state.scrcpyPlayer) {
            scrcpyMirrorFailed('浏览器不支持 WebCodecs');
            return;
        }
        setStatus('高帧率投屏连接中（WebSocket）…', '');
        state.scrcpyPlayer.start().catch(function (e) {
            if (!state.mirrorRunning) return;
            state.scrcpyPlayer = null;
            scrcpyMirrorFailed(e.message || String(e));
        });
    }

    function startScreencapMirror() {
        const canvas = $('msMirrorCanvas');
        if (!canvas || !state.mirrorUrl) return;
        const ctx = canvas.getContext('2d');
        if (!ctx) return;
        const targetFps = (state.bootstrap && state.bootstrap.mirror_fps) || 8;
        const minInterval = Math.max(16, Math.floor(1000 / Math.max(1, targetFps)));
        state.mirrorRunning = true;
        state.mirrorBusy = false;
        state.mirrorFrameCount = 0;
        state.mirrorLastFpsAt = Date.now();

        function updateFpsLabel() {
            const dim = $('msDeviceDim');
            if (!dim || !state.deviceWidth) return;
            const now = Date.now();
            const elapsed = (now - state.mirrorLastFpsAt) / 1000;
            if (elapsed >= 2) {
                const fps = Math.round(state.mirrorFrameCount / elapsed);
                state.mirrorFrameCount = 0;
                state.mirrorLastFpsAt = now;
                dim.setAttribute('data-live-fps', String(fps));
            }
            const live = dim.getAttribute('data-live-fps');
            const base = state.deviceWidth + ' × ' + state.deviceHeight;
            dim.textContent = live ? base + ' · ' + live + ' fps' : base;
        }

        async function tick() {
            if (!state.mirrorRunning) return;
            if (state.mirrorBusy) {
                if (state.mirrorRunning) {
                    state.mirrorTimer = setTimeout(tick, minInterval);
                }
                return;
            }
            const started = Date.now();
            state.mirrorBusy = true;
            const abort = new AbortController();
            state.mirrorAbort = abort;
            try {
                const r = await fetch(state.mirrorUrl, {
                    credentials: 'same-origin',
                    signal: abort.signal,
                });
                const data = await r.json().catch(function () { return {}; });
                if (!state.mirrorRunning) return;
                if (!r.ok || !data.success || !data.data) return;
                const mime = (data.format === 'jpeg' || data.format === 'jpg') ? 'jpeg' : 'png';
                const img = new Image();
                await new Promise(function (resolve, reject) {
                    img.onload = resolve;
                    img.onerror = reject;
                    img.src = 'data:image/' + mime + ';base64,' + data.data;
                });
                if (!state.mirrorRunning) return;
                if (canvas.width !== img.width || canvas.height !== img.height) {
                    canvas.width = img.width;
                    canvas.height = img.height;
                }
                ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
                canvas.style.display = 'block';
                const ph = $('msMirrorPlaceholder');
                if (ph) ph.style.display = 'none';
                state.deviceWidth = img.width;
                state.deviceHeight = img.height;
                syncPhoneAspectFromDevice();
                state.mirrorFrameCount += 1;
                updateFpsLabel();
            } catch (e) {
                if (e && e.name === 'AbortError') return;
            } finally {
                if (state.mirrorAbort === abort) state.mirrorAbort = null;
                state.mirrorBusy = false;
                if (!state.mirrorRunning) return;
                const elapsed = Date.now() - started;
                const delay = Math.max(0, minInterval - elapsed);
                state.mirrorTimer = setTimeout(tick, delay);
            }
        }
        tick();
    }

    function startMirror() {
        stopMirror();
        if (!state.mirrorUrl && !state.mirrorStreamUrl && !state.mirrorWsUrl) return;
        if (state.mirrorBackend === 'scrcpy_ws') {
            if (state.mirrorStreamUrl) {
                startScrcpyHttpMirror();
                return;
            }
            if (state.mirrorWsUrl) {
                startScrcpyWsMirror();
                return;
            }
        }
        if (isEmulatorUdid(state.udid)) {
            scrcpyMirrorFailed(state.mirror_fallback_reason || '模拟器未启用高帧率投屏');
            return;
        }
        startScreencapMirror();
    }

    function isRealDevice(d) {
        const udid = ((d && d.udid) || '').trim();
        return udid && udid.indexOf('emulator-') !== 0;
    }

    function filterRealDevices(devices) {
        return (devices || []).filter(isRealDevice);
    }

    function syncPhoneAspectFromDevice() {
        if (!state.deviceWidth || !state.deviceHeight) return;
        const shell = $('msPhoneShell');
        if (shell) {
            shell.style.setProperty('--ms-phone-ar', state.deviceWidth + ' / ' + state.deviceHeight);
        }
    }

    function mapCanvasToDevice(clientX, clientY) {
        const canvas = $('msMirrorCanvas');
        if (!canvas || !state.deviceWidth || !state.deviceHeight) return { x: 0, y: 0 };
        const rect = canvas.getBoundingClientRect();
        const dw = state.deviceWidth;
        const dh = state.deviceHeight;
        const scale = Math.min(rect.width / dw, rect.height / dh);
        const renderW = dw * scale;
        const renderH = dh * scale;
        const offsetX = rect.left + (rect.width - renderW) / 2;
        const offsetY = rect.top + (rect.height - renderH) / 2;
        const x = Math.round((clientX - offsetX) * (dw / Math.max(1, renderW)));
        const y = Math.round((clientY - offsetY) * (dh / Math.max(1, renderH)));
        return {
            x: Math.max(0, Math.min(dw, x)),
            y: Math.max(0, Math.min(dh, y)),
        };
    }

    async function tapAt(clientX, clientY) {
        const pt = mapCanvasToDevice(clientX, clientY);
        if (
            state.mirrorBackend === 'scrcpy_ws'
            && state.scrcpyPlayer
            && state.scrcpyPlayer.sendTap
            && state.scrcpyPlayer.sendTap(pt.x, pt.y, state.deviceWidth, state.deviceHeight)
        ) {
            setStatus('点击 (' + pt.x + ', ' + pt.y + ')', 'ok');
            return;
        }
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
        if (
            state.mirrorBackend === 'scrcpy_ws'
            && state.scrcpyPlayer
            && state.scrcpyPlayer.sendSwipe
            && state.scrcpyPlayer.sendSwipe(
                p1.x, p1.y, p2.x, p2.y, state.deviceWidth, state.deviceHeight
            )
        ) {
            setStatus('滑动', 'ok');
            return;
        }
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
            if (!state.connected || !state.udid) return;
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
        shell.style.setProperty('--ms-phone-ar', p.frame_width + ' / ' + p.frame_height);
        shell.style.width = '';
        shell.style.maxWidth = '';
        shell.style.aspectRatio = '';
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
        const list = filterRealDevices(devices);
        if (!list.length) {
            devSel.innerHTML = '<option value="">（未发现真机，请 USB/无线连接）</option>';
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
        const list = filterRealDevices(devices);
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
            var lines = [];
            if (boot.adb_path) {
                lines.push('adb 已就绪：');
                lines.push(boot.adb_path);
            }
            lines.push('未发现真机：请 USB 连接或在下方「真机兼容」中无线配对。模拟器请用上方「启动模拟器」。');
            el.textContent = lines.join('\n');
            return;
        }
        if (!authorized.length) {
            el.textContent =
                '设备状态异常（非 device）：请换线/换口、重装手机驱动，或在命令行执行 adb kill-server && adb start-server 后刷新。';
            return;
        }
        el.textContent = '已识别 ' + authorized.length + ' 台设备，点击「连接真机」后在右侧画面用鼠标操作。';
    }

    function modelStatusSuffix(model) {
        if (!model) return '';
        if (model.running) return ' · 运行中';
        if (model.avd_exists) return ' · 已就绪';
        return ' · 待创建';
    }

    function renderDeviceModels(models) {
        const sel = $('msDeviceModelSelect');
        if (!sel) return;
        const list = models || [];
        if (!list.length) {
            sel.innerHTML = '<option value="">（请先安装 SDK）</option>';
            return;
        }
        sel.innerHTML = list.map(function (m) {
            const label = (m.label || m.id || '') + modelStatusSuffix(m);
            return '<option value="' + m.id + '">' + label + '</option>';
        }).join('');
        const preferred = state.selectedModelId || 'pixel_7';
        const running = list.find(function (m) { return m.running; });
        if (running && !state.modelSwitchBusy) {
            sel.value = running.id;
            state.selectedModelId = running.id;
        } else if (list.some(function (m) { return m.id === preferred; })) {
            sel.value = preferred;
        } else {
            sel.value = list[0].id;
            state.selectedModelId = list[0].id;
        }
        state.selectedModelId = sel.value;
        const current = list.find(function (m) { return m.id === sel.value; });
        if (current && current.frame_preset_id) {
            applyFramePreset(current.frame_preset_id);
        }
    }

    function renderEnvStatus(diag) {
        const statusEl = $('msEnvStatus');
        const blockEl = $('msEnvBlocking');
        if (!statusEl) return;
        if (!diag) {
            statusEl.textContent = '尚未检测，请点击「检测环境」';
            statusEl.className = 'ms-env-status ms-env-status--idle';
            if (blockEl) blockEl.style.display = 'none';
            return;
        }
        const ready = diag.ready === true || (
            !diag.blocking_reason &&
            Array.isArray(diag.checks) &&
            diag.checks.length &&
            diag.checks.every(function (c) { return c.ok || c.optional; })
        );
        if (ready) {
            statusEl.textContent = '✓ 环境已就绪，可以启动模拟器';
            statusEl.className = 'ms-env-status ms-env-status--ready';
            if (blockEl) {
                blockEl.textContent = '';
                blockEl.style.display = 'none';
            }
        } else {
            const reason = (diag.blocking_reason || '环境未就绪').trim();
            statusEl.textContent = '✕ ' + reason;
            statusEl.className = 'ms-env-status ms-env-status--fail';
            if (blockEl) {
                blockEl.textContent = reason.indexOf('插件市场') >= 0
                    ? reason
                    : ('请处理：' + reason + '。仍可直接点「启动模拟器」尝试自动修复。');
                blockEl.style.display = '';
            }
        }
    }

    async function checkEnvironment() {
        const btn = $('msBtnEnvCheck');
        if (btn) btn.disabled = true;
        const statusEl = $('msEnvStatus');
        if (statusEl) {
            statusEl.textContent = '正在检测环境…';
            statusEl.className = 'ms-env-status ms-env-status--idle';
        }
        try {
            const data = await apiJson('/api/mobile/emulator/diagnostics');
            state.diagnosticsCache = data;
            state.diagnosticsCacheAt = Date.now();
            renderEnvStatus(data);
            if (data.ready) {
                setStatus('环境已就绪', 'ok');
            } else {
                setStatus(data.blocking_reason || '环境未就绪', 'warn');
            }
            return data;
        } catch (e) {
            renderEnvStatus({ ready: false, blocking_reason: (e && e.message) || '检测失败' });
            setStatus((e && e.message) || '环境检测失败', 'err');
            return null;
        } finally {
            if (btn) btn.disabled = false;
        }
    }

    async function loadDiagnostics(force) {
        if (!force && state.diagnosticsCache) {
            renderEnvStatus(state.diagnosticsCache);
            return state.diagnosticsCache;
        }
        return checkEnvironment();
    }

    function renderEnvPanel(diag) {
        renderEnvStatus(diag);
    }

    async function repairEnvironment() {
        const btn = $('msBtnEnvRepair');
        if (btn) btn.disabled = true;
        setStatus('正在修复模拟器环境（可能需数分钟）…', '');
        try {
            const data = await apiJson('/api/mobile/emulator/repair', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({}),
            });
            if (!data.success) {
                throw new Error(data.error || data.message || '修复失败');
            }
            setStatus(data.message || '环境已修复', 'ok');
            await bootstrap({ skipDiagnostics: true });
            await loadDiagnostics(true);
        } catch (e) {
            setStatus((e && e.message) || '修复失败', 'err');
            await loadDiagnostics(true);
        } finally {
            if (btn) btn.disabled = false;
        }
    }

    function renderEmulatorPanel(emu) {
        const help = $('msEmulatorHelp');
        renderDeviceModels((emu && emu.models) || []);
        if (help) {
            help.className = 'ms-muted ms-emulator-help';
            if (emu && emu.emulator_available) {
                var running = ((emu.running && emu.running.length) || 0);
                help.textContent = running > 0
                    ? ('模拟器运行中 · ' + running + ' 台')
                    : '环境就绪后可一键启动';
            } else {
                help.textContent = (emu && emu.emulator_message) ||
                    '请先在插件市场安装「Android 模拟器 SDK」';
            }
        }
    }

    function parseEmuJobResponse(body) {
        if (!body || body.ok === false) {
            return { missing: true, error: (body && body.error) || '任务不存在' };
        }
        var job = body.job || {};
        return {
            missing: false,
            state: job.state || '',
            percent: job.percent || 0,
            label: job.label || '',
            ok: job.ok,
            error: job.error || '',
            message: job.message || '',
            result: job.result || {},
        };
    }

    function sleepMs(ms) {
        return new Promise(function (resolve) { setTimeout(resolve, ms); });
    }

    async function pollEmulatorJob(jobId, labelPrefix) {
        if (state.emulatorPollToken) {
            state.emulatorPollToken.cancelled = true;
        }
        const token = { cancelled: false };
        state.emulatorPollToken = token;
        let delayMs = 700;
        const maxDelayMs = 3500;
        const maxPolls = 120;
        let lastPercent = -1;
        let stalePolls = 0;
        for (let i = 0; i < maxPolls; i++) {
            if (token.cancelled) {
                throw new Error('操作已取消');
            }
            const hidden = typeof document.hidden !== 'undefined' && document.hidden;
            const r = await fetch('/api/mobile/emulator/start/job/' + encodeURIComponent(jobId), {
                credentials: 'same-origin',
            });
            const body = await r.json().catch(function () { return {}; });
            const info = parseEmuJobResponse(body);
            if (info.missing) {
                throw new Error(info.error);
            }
            const prefix = labelPrefix || info.label || '处理中…';
            setStatus(prefix + ' (' + (info.percent || 0) + '%)', '');
            if (info.state === 'done' && info.ok !== false) {
                if (state.emulatorPollToken === token) state.emulatorPollToken = null;
                return info;
            }
            if (info.state === 'failed' || (info.state === 'done' && info.ok === false)) {
                if (state.emulatorPollToken === token) state.emulatorPollToken = null;
                let err = (info.error || info.message || '操作失败').trim();
                const logPath = info.result && info.result.log_path;
                if (logPath && err.indexOf(logPath) < 0) {
                    err += '\n诊断日志：' + logPath;
                }
                const emu = state.bootstrap && state.bootstrap.emulator;
                const hint = emu && (emu.setup_hint || '').trim();
                if (hint && err.indexOf(hint) < 0) {
                    err += '\n' + hint;
                }
                if (err.indexOf('停止') < 0) {
                    err += '\n可先点「停止」清理任务管理器中的 qemu/emulator 后重试。';
                }
                throw new Error(err);
            }
            if (info.percent === lastPercent) {
                stalePolls += 1;
            } else {
                stalePolls = 0;
                lastPercent = info.percent;
            }
            const waitMs = hidden
                ? Math.max(4000, delayMs)
                : (stalePolls >= 3 ? Math.min(maxDelayMs, delayMs + 500) : delayMs);
            await sleepMs(waitMs);
            if (!hidden) {
                delayMs = Math.min(maxDelayMs, Math.floor(delayMs * 1.25));
            }
        }
        if (state.emulatorPollToken === token) state.emulatorPollToken = null;
        throw new Error('操作超时，请点「停止」清理后重试');
    }

    async function pollEmulatorStartJob(jobId) {
        return pollEmulatorJob(jobId, '启动中…');
    }

    async function connectMirrorWithRetry(maxAttempts) {
        maxAttempts = maxAttempts || 4;
        let lastErr = null;
        for (let i = 0; i < maxAttempts; i++) {
            try {
                stopMirror();
                await autoConnect();
                return;
            } catch (e) {
                lastErr = e;
                if (i >= maxAttempts - 1) break;
                setStatus('正在连接画布，重试 ' + (i + 2) + '/' + maxAttempts + '…', 'warn');
                await sleepMs(1200 * (i + 1));
            }
        }
        throw lastErr || new Error('连接失败');
    }

    function resolveMirrorWsUrl(raw) {
        if (!raw) return '';
        try {
            var u = new URL(raw, window.location.href);
            u.hostname = window.location.hostname;
            u.protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            return u.toString();
        } catch (e) {
            return raw;
        }
    }

    function applyConnectPayload(data) {
        state.udid = data.udid || '';
        state.sessionId = data.session_id || '';
        state.mirrorBackend = data.mirror_backend || 'screencap';
        state.mirrorWsUrl = resolveMirrorWsUrl(data.mirror_ws_url || '');
        state.mirrorStreamUrl = data.mirror_stream_url || '';
        state.mirrorUrl = data.mirror_frame_url || '';
        state.mirror_fallback_reason = data.mirror_fallback_reason || '';
        state.connected = true;
        if (data.device) {
            state.deviceWidth = data.device.width || 1080;
            state.deviceHeight = data.device.height || 1920;
        }
        const preset = (data.frame_preset && data.frame_preset.id) || currentFramePresetId();
        if (state.deviceWidth && state.deviceHeight) {
            syncPhoneAspectFromDevice();
        } else {
            applyFramePreset(preset);
        }
        const appSel = $('msAppSelect');
        if (appSel && data.apps) {
            appSel.innerHTML = data.apps.map(function (a) {
                const sel = a.package === data.suggested_app_package ? ' selected' : '';
                return '<option value="' + a.package + '"' + sel + '>' + a.label + ' (' + a.package + ')</option>';
            }).join('');
        }
        const badge = $('msConnectBadge');
        if (badge) {
            badge.textContent = '已连接';
            badge.className = 'ms-connect-badge ms-connect-badge--on';
        }
        if (state.mirrorBackend === 'scrcpy_ws') {
            updateMirrorModeBadge('scrcpy', 'H.264 硬件视频流');
        } else {
            updateMirrorModeBadge(
                'screencap',
                data.mirror_fallback_reason || 'adb screencap 截图轮询'
            );
        }
        let msg = '已连接 ' + (data.device && data.device.model ? data.device.model : state.udid);
        if (data.is_emulator && state.mirrorBackend === 'scrcpy_ws') msg += ' · 高帧率 scrcpy 投屏';
        else if (data.is_emulator) msg += ' · 模拟器（截图投屏）';
        else msg += ' · 真机预览';
        if (data.mirror_fallback_reason) msg += ' · ' + data.mirror_fallback_reason;
        if (data.appium_error) msg += ' · Appium: ' + data.appium_error;
        else if (data.appium_connected) msg += ' · Appium 已就绪';
        setStatus(msg, 'ok');
        startMirror();
    }

    async function prepareEmulatorMirrorSwitch() {
        if (state.emulatorPollToken) {
            state.emulatorPollToken.cancelled = true;
        }
        stopMirror();
        if (state.connected && state.udid && state.udid.indexOf('emulator-') === 0) {
            try { await disconnect(); } catch (e) { /* ignore */ }
        }
    }

    async function launchEmulator(presetId, opts) {
        opts = opts || {};
        const pid = (presetId || ($('msDeviceModelSelect') || {}).value || '').trim();
        if (!pid) {
            setStatus('请选择设备型号', 'warn');
            return;
        }
        if (state.modelSwitchBusy) return;
        state.modelSwitchBusy = true;
        state.selectedModelId = pid;
        const models = (state.bootstrap && state.bootstrap.emulator && state.bootstrap.emulator.models) || [];
        const model = models.find(function (m) { return m.id === pid; });
        const label = (model && model.label) || pid;
        const btnStart = $('msBtnEmulatorStart');
        const btnStop = $('msBtnEmulatorStop');
        if (btnStart) btnStart.disabled = true;
        if (btnStop) btnStop.disabled = true;
        const forceRestart = opts.forceRestart === true;
        setStatus('正在启动 ' + label + '（环境检查、模拟器、投屏一步完成）…', '');
        try {
            await prepareEmulatorMirrorSwitch();
            if (model && model.frame_preset_id) {
                applyFramePreset(model.frame_preset_id);
            }
            const startResp = await fetch('/api/mobile/emulator/launch', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'same-origin',
                body: JSON.stringify({
                    preset_id: pid,
                    no_window: true,
                    async: true,
                    force_restart: forceRestart,
                    try_appium: false,
                }),
            });
            const startBody = await startResp.json().catch(function () { return {}; });
            if (startResp.status === 409) {
                throw new Error(startBody.error || '已有启动任务进行中，请勿重复点击');
            }
            if (!startResp.ok || !startBody.success) {
                throw new Error(startBody.error || '启动失败');
            }
            let jobResult = {};
            if (startBody.async && startBody.job_id) {
                const done = await pollEmulatorJob(startBody.job_id, '正在启动 ' + label);
                jobResult = done.result || {};
            } else {
                jobResult = startBody;
            }
            const serial = jobResult.serial || jobResult.udid || '';
            await bootstrap({ skipDiagnostics: true });
            if (!opts.skipConnect) {
                stopMirror();
                applyConnectPayload(jobResult);
            } else {
                setStatus((jobResult.message || ('已启动 ' + label)), 'ok');
            }
            loadDiagnostics(true).catch(function () {});
        } catch (e) {
            setStatus((e && e.message) || '启动失败', 'err');
            await bootstrap({ skipDiagnostics: true });
        } finally {
            state.modelSwitchBusy = false;
            if (btnStart) btnStart.disabled = false;
            if (btnStop) btnStop.disabled = false;
        }
    }

    async function startEmulator() {
        const presetId = ($('msDeviceModelSelect') || {}).value || state.selectedModelId || 'pixel_7';
        await launchEmulator(presetId, { forceRestart: false });
    }

    async function stopEmulator() {
        setStatus('正在停止模拟器…', '');
        if (state.connected && state.udid && state.udid.indexOf('emulator-') === 0) {
            await disconnect();
        }
        await apiJson('/api/mobile/emulator/stop-all', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: '{}',
        });
        await bootstrap();
        setStatus('模拟器已停止', '');
    }

    async function maybeAutoStartEmulator(data) {
        return;
    }

    async function bootstrap(opts) {
        opts = opts || {};
        const data = await apiJson('/api/mobile/testing/bootstrap');
        state.bootstrap = data;
        const devices = (data.health && data.health.devices) || data.devices || [];
        renderDeviceList(devices);
        updateDeviceHelp(data.health, devices);
        renderEmulatorPanel(data.emulator);
        if (data.emulator && data.emulator.emulator_available && !(devices && devices.length)) {
            setStatus('选择设备型号后点击「启动模拟器」', 'ok');
        } else if (data.default_device && data.default_device.state === 'device') {
            setStatus('检测到设备，可点击「连接真机」', 'ok');
        } else if (data.health && data.health.adb_ok && data.adb_plugin_installed && data.adb_path) {
            setStatus('adb 已就绪', 'ok');
        } else if (!data.health || !data.health.adb_ok) {
            setStatus((data.health && data.health.adb_message) || '请先配置 ADB', 'warn');
        } else if (devices.some(function (d) { return d.state === 'unauthorized'; })) {
            setStatus('请在手机上允许 USB 调试', 'warn');
        } else if (!devices.length) {
            setStatus('未检测到 USB 设备，请检查连接与开发者选项', 'warn');
        }
    }

    async function wirelessConnect() {
        const host = (($('msWirelessIp') || {}).value || '').trim();
        const pairPort = (($('msWirelessPairPort') || {}).value || '').trim();
        const code = (($('msWirelessCode') || {}).value || '').trim();
        const connectPort = (($('msWirelessConnectPort') || {}).value || '').trim();
        if (!host) {
            setStatus('请填写手机 IP', 'warn');
            return;
        }
        if (!connectPort) {
            setStatus('请填写调试端口（无线调试页「IP 地址和端口」）', 'warn');
            return;
        }
        setStatus('正在无线配对/连接…', '');
        const payload = { host: host, connect_port: connectPort };
        if (code) {
            if (!pairPort) {
                setStatus('填写配对码时需同时填写配对端口', 'warn');
                return;
            }
            payload.pair_port = parseInt(pairPort, 10);
            payload.pairing_code = code;
        }
        const data = await apiJson('/api/mobile/wireless/connect', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        await bootstrap();
        const devSel = $('msDeviceSelect');
        if (devSel && data.udid) {
            devSel.value = data.udid;
        }
        setStatus(data.message || ('已连接 ' + data.udid), 'ok');
        await autoConnect();
    }

    function currentFramePresetId() {
        const models = (state.bootstrap && state.bootstrap.emulator && state.bootstrap.emulator.models) || [];
        const pid = state.selectedModelId || ($('msDeviceModelSelect') || {}).value || '';
        const model = models.find(function (m) { return m.id === pid; });
        return (model && model.frame_preset_id) || state.framePreset || 'generic_19_9';
    }

    async function autoConnect() {
        setStatus('正在连接真机…', '');
        const preset = currentFramePresetId();
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
        applyConnectPayload(data);
    }

    async function disconnect() {
        stopMirror();
        resetMirrorUi();
        try {
            await apiJson('/api/mobile/disconnect', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ session_id: state.sessionId, udid: state.udid }),
            });
        } catch (e) {
            setStatus((e && e.message) || '断开失败', 'err');
            return;
        }
        state.connected = false;
        state.sessionId = '';
        state.mirrorUrl = '';
        state.mirrorWsUrl = '';
        state.mirrorStreamUrl = '';
        state.mirrorBackend = 'screencap';
        state.mirror_fallback_reason = '';
        state.udid = '';
        const badge = $('msConnectBadge');
        if (badge) {
            badge.textContent = '未连接';
            badge.className = 'ms-connect-badge';
        }
        setStatus('已断开', '');
        bootstrap().catch(function () { /* ignore refresh errors */ });
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
        const btnWireless = $('msBtnWirelessConnect');
        if (btnWireless) {
            btnWireless.addEventListener('click', function () {
                wirelessConnect().catch(function (e) { setStatus(e.message, 'err'); });
            });
        }
        const btnEmuStart = $('msBtnEmulatorStart');
        if (btnEmuStart) {
            btnEmuStart.addEventListener('click', function () {
                startEmulator().catch(function (e) { setStatus(e.message, 'err'); });
            });
        }
        const btnEmuStop = $('msBtnEmulatorStop');
        if (btnEmuStop) {
            btnEmuStop.addEventListener('click', function () {
                stopEmulator().catch(function (e) { setStatus(e.message, 'err'); });
            });
        }
        const modelSel = $('msDeviceModelSelect');
        if (modelSel) {
            modelSel.addEventListener('change', function () {
                if (state.modelSwitchBusy) return;
                const newId = this.value;
                if (!newId || newId === state.selectedModelId) return;
                state.selectedModelId = newId;
                launchEmulator(newId, { forceRestart: false }).catch(function (e) {
                    setStatus(e.message, 'err');
                });
            });
        }
        const btnEnvCheck = $('msBtnEnvCheck');
        if (btnEnvCheck) {
            btnEnvCheck.addEventListener('click', function () {
                checkEnvironment().catch(function (e) { setStatus(e.message, 'err'); });
            });
        }
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
        window.addEventListener('beforeunload', function () {
            if (!state.bootstrap || !state.bootstrap.emulator) return;
            var running = (state.bootstrap.emulator.running && state.bootstrap.emulator.running.length) || 0;
            if (!running) return;
            try {
                fetch('/api/mobile/emulator/stop-all', {
                    method: 'POST',
                    credentials: 'same-origin',
                    keepalive: true,
                    headers: { 'Content-Type': 'application/json' },
                    body: '{}',
                });
            } catch (e) { /* ignore */ }
        });
    }

    async function init() {
        var cfgEl = document.getElementById('__mobileStudioDisabledJson');
        if (cfgEl) {
            try {
                var cfg = JSON.parse(cfgEl.textContent);
                global.__MOBILE_STUDIO_DISABLED__ = !!cfg.disabled;
                global.__MOBILE_STUDIO_DISABLED_REASON__ = cfg.reason || '';
            } catch (e) {}
        }
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
