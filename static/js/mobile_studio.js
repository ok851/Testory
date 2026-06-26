/**
 * Testory 移动端测试：设备连接、镜像录制捕获、用例执行
 */
(function (global) {
    'use strict';

    var INTERACTION_RECORD = 'record';
    var INTERACTION_CAPTURE = 'capture_element';
    var INTERACTION_OPERATE = 'operate';
    var FRAME_INTERVAL_MS = 900;
    var PEEK_INTERVAL_MS = 320;
    var PEEK_MIN_PX = 6;

    var state = {
        bootstrap: null,
        projectId: null,
        caseId: null,
        steps: [],
        udid: '',
        deviceWidth: 1080,
        deviceHeight: 1920,
        connected: false,
        pointerDown: null,
        interactionMode: INTERACTION_OPERATE,
        alsoTapOnRecord: true,
        diagnosticsCache: null,
        runningStepId: null,
        recordingSession: [],
        deviceTab: 'real',
        realDevices: [],
        deviceWarnings: [],
        frameTimer: null,
        assistantPollTimer: null,
        peekBusy: false,
        lastPeekX: -1,
        lastPeekY: -1,
        lastPeekTs: 0,
        highlightBounds: null,
        assistantInstalled: false,
        assistantConnected: false,
        appiumConnected: false,
        requiresAppium: false,
        liveSteps: [],
        recording: false,
        agentWs: null,
        agentWsUrl: '',
        selectedLiveIndex: -1,
        sessionId: '',
        mirrorUrl: '',
        mirrorBackend: 'screencap',
        mirrorWsUrl: '',
        mirrorStreamUrl: '',
        mirrorRunning: false,
        mirrorTimer: null,
        mirrorAbort: null,
        mirrorBusy: false,
        mirrorFrameCount: 0,
        mirrorLastFpsAt: 0,
        scrcpyPlayer: null,
        scrcpyGotFrame: false,
        mirror_fallback_reason: '',
    };

    function $(id) {
        return document.getElementById(id);
    }

    async function apiJson(url, opts) {
        var r = await fetch(url, Object.assign({ credentials: 'same-origin' }, opts || {}));
        var data = await r.json().catch(function () { return {}; });
        if (!r.ok) throw new Error(data.error || ('HTTP ' + r.status));
        if (data.success === false && data.error) throw new Error(data.error);
        return data;
    }

    function setStatus(msg, kind) {
        var el = $('msStatus');
        if (!el) return;
        el.textContent = msg || '';
        el.className = 'ms-status' + (kind ? ' ms-status--' + kind : '');
    }

    function isEmulatorUdid(udid) {
        var u = (udid || '').trim();
        if (u.indexOf('emulator-') === 0) return true;
        return /^127\.0\.0\.1:\d+$/i.test(u) || /^localhost:\d+$/i.test(u);
    }

    function shouldTryAppium() {
        // 录制/捕获走 ADB，连接设备时不强制 Appium；仅 appium 驱动模式或显式启动时需要
        return state.driverMode === 'appium';
    }

    function updateAppiumHint(data) {
        var el = $('msAppiumHint');
        if (!el) return;
        if (!state.connected) {
            el.style.display = 'none';
            return;
        }
        el.style.display = '';
        if (state.appiumConnected) {
            el.textContent = 'Appium 已就绪，可回放元素定位步骤。';
            el.style.color = '#047857';
        } else if (state.driverMode === 'appium') {
            el.textContent = '需要 Appium：' + ((data && data.appium_error) || '请点「启动 Appium」');
            el.style.color = '#b45309';
        } else {
            el.textContent = 'ADB 已连接。录制/捕获无需 Appium；回放含元素定位的步骤时请点「启动 Appium」。';
            el.style.color = '#64748b';
        }
    }
    function updateDeviceDimLabel() {
        var dim = $('msDeviceDim');
        if (!dim) return;
        if (!state.connected || !state.deviceWidth) {
            dim.textContent = '分辨率 —';
            return;
        }
        dim.textContent = state.deviceWidth + ' × ' + state.deviceHeight;
    }

    function updateAssistantBadge() {
        var badge = $('msAssistantBadge');
        if (!badge) return;
        if (state.assistantConnected) {
            badge.textContent = '助手 已连接';
            badge.className = 'ms-assistant-badge ms-assistant-badge--on';
        } else if (state.assistantInstalled) {
            badge.textContent = '助手 已安装';
            badge.className = 'ms-assistant-badge ms-assistant-badge--installed';
        } else {
            badge.textContent = '助手 —';
            badge.className = 'ms-assistant-badge';
        }
    }

    function layoutMirrorCanvas() {
        var shell = $('msPhoneShell');
        var canvas = $('msMirrorCanvas');
        var overlay = $('msMirrorOverlay');
        var placeholder = $('msMirrorPlaceholder');
        if (!shell || !canvas) return;
        if (state.deviceWidth && state.deviceHeight) {
            shell.style.setProperty('--ms-phone-ar', state.deviceWidth + ' / ' + state.deviceHeight);
        }
        if (state.connected) {
            canvas.style.display = 'block';
            canvas.classList.add('ms-mirror-canvas--active');
            if (overlay) overlay.style.display = 'block';
            if (placeholder) placeholder.style.display = 'none';
        } else {
            canvas.style.display = 'none';
            canvas.classList.remove('ms-mirror-canvas--active');
            if (overlay) overlay.style.display = 'none';
            if (placeholder) placeholder.style.display = 'flex';
        }
        updateDeviceDimLabel();
    }

    function mapCanvasToDevice(clientX, clientY) {
        var canvas = $('msMirrorCanvas');
        if (!canvas || !state.deviceWidth || !state.deviceHeight) return { x: 0, y: 0 };
        var rect = canvas.getBoundingClientRect();
        var dw = state.deviceWidth;
        var dh = state.deviceHeight;
        var scale = Math.min(rect.width / dw, rect.height / dh);
        var renderW = dw * scale;
        var renderH = dh * scale;
        var offsetX = rect.left + (rect.width - renderW) / 2;
        var offsetY = rect.top + (rect.height - renderH) / 2;
        var x = Math.round((clientX - offsetX) * (dw / Math.max(1, renderW)));
        var y = Math.round((clientY - offsetY) * (dh / Math.max(1, renderH)));
        return {
            x: Math.max(0, Math.min(dw, x)),
            y: Math.max(0, Math.min(dh, y)),
        };
    }

    function deviceToCanvasRect(bounds) {
        var canvas = $('msMirrorCanvas');
        if (!canvas || !bounds || bounds.length < 4) return null;
        var rect = canvas.getBoundingClientRect();
        var dw = state.deviceWidth;
        var dh = state.deviceHeight;
        var scale = Math.min(rect.width / dw, rect.height / dh);
        var renderW = dw * scale;
        var renderH = dh * scale;
        var offsetX = (rect.width - renderW) / 2;
        var offsetY = (rect.height - renderH) / 2;
        var l = bounds[0] * scale / dw * rect.width + offsetX;
        var t = bounds[1] * scale / dh * rect.height + offsetY;
        var w = (bounds[2] - bounds[0]) * scale / dw * rect.width;
        var h = (bounds[3] - bounds[1]) * scale / dh * rect.height;
        return { left: l, top: t, width: w, height: h };
    }

    function drawHighlight(bounds) {
        var overlay = $('msMirrorOverlay');
        var canvas = $('msMirrorCanvas');
        if (!overlay || !canvas) return;
        overlay.width = canvas.clientWidth;
        overlay.height = canvas.clientHeight;
        var ctx = overlay.getContext('2d');
        if (!ctx) return;
        ctx.clearRect(0, 0, overlay.width, overlay.height);
        if (!bounds || bounds.length < 4) return;
        var r = deviceToCanvasRect(bounds);
        if (!r) return;
        ctx.strokeStyle = '#16a34a';
        ctx.lineWidth = 2;
        ctx.fillStyle = 'rgba(187,247,208,0.35)';
        ctx.fillRect(r.left, r.top, r.width, r.height);
        ctx.strokeRect(r.left, r.top, r.width, r.height);
    }

    function stopMirror() {
        state.mirrorRunning = false;
        state._mirrorEpoch = (state._mirrorEpoch || 0) + 1;
        state.scrcpyGotFrame = false;
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

    function stopFramePolling() {
        stopMirror();
    }

    function resetMirrorUi() {
        var canvas = $('msMirrorCanvas');
        var ph = $('msMirrorPlaceholder');
        if (canvas) {
            canvas.style.display = 'none';
            try {
                var ctx = canvas.getContext('2d');
                if (ctx) ctx.clearRect(0, 0, canvas.width || 0, canvas.height || 0);
            } catch (e) { /* ignore */ }
        }
        if (ph) ph.style.display = '';
        var dim = $('msDeviceDim');
        if (dim) {
            dim.textContent = '分辨率 —';
            dim.removeAttribute('data-live-fps');
        }
        updateMirrorModeBadge('idle', '');
    }

    function updateMirrorModeBadge(mode, detail) {
        var badge = $('msMirrorMode');
        if (!badge) return;
        var m = mode || 'idle';
        badge.className = 'ms-mirror-mode ms-mirror-mode--' + m;
        if (m === 'scrcpy') {
            badge.textContent = '高帧率 scrcpy';
            badge.title = detail || 'H.264 硬件视频流（需 WebView/浏览器支持 WebCodecs）';
        } else if (m === 'connecting') {
            badge.textContent = '投屏连接中…';
            badge.title = detail || '正在建立 scrcpy 视频流';
        } else if (m === 'screencap') {
            badge.textContent = '截图投屏';
            badge.title = detail || 'adb screencap 轮询，帧率较低';
        } else {
            badge.textContent = '未连接';
            badge.title = detail || '连接设备后显示投屏方式';
        }
    }

    function showMirrorCanvas() {
        var canvas = $('msMirrorCanvas');
        var ph = $('msMirrorPlaceholder');
        if (canvas) canvas.style.display = 'block';
        if (ph) ph.style.display = 'none';
    }

    function stopScreencapMirrorOnly() {
        if (state.mirrorTimer) {
            clearTimeout(state.mirrorTimer);
            state.mirrorTimer = null;
        }
        if (state.mirrorAbort) {
            try { state.mirrorAbort.abort(); } catch (e) { /* ignore */ }
            state.mirrorAbort = null;
        }
        state.mirrorBusy = false;
    }

    function isMirrorAbortError(err) {
        if (!err) return false;
        if (err.name === 'AbortError') return true;
        var msg = String(err.message || err);
        return /aborted/i.test(msg);
    }

    function scrcpyAutoFallbackEnabled() {
        var b = state.bootstrap;
        return !!(b && b.scrcpy_auto_fallback);
    }

    function webCodecsSupported() {
        return !!(global.ScrcpyMirrorPlayer && global.ScrcpyMirrorPlayer.webCodecsSupported && global.ScrcpyMirrorPlayer.webCodecsSupported());
    }

    function webCodecsUnavailableMessage() {
        if (global.ScrcpyMirrorPlayer && global.ScrcpyMirrorPlayer.getWebCodecsUnavailableMessage) {
            return global.ScrcpyMirrorPlayer.getWebCodecsUnavailableMessage();
        }
        return '当前显示环境不支持 H.264 硬件解码（WebCodecs）';
    }

    function h264UnavailableMessage() {
        if (global.ScrcpyMirrorPlayer && global.ScrcpyMirrorPlayer.getH264UnavailableMessage) {
            return global.ScrcpyMirrorPlayer.getH264UnavailableMessage();
        }
        return webCodecsUnavailableMessage();
    }

    function isWebCodecsRelatedError(msg) {
        if (global.ScrcpyMirrorPlayer && global.ScrcpyMirrorPlayer.isWebCodecsRelatedError) {
            return global.ScrcpyMirrorPlayer.isWebCodecsRelatedError(msg);
        }
        return /WebCodecs|VideoDecoder|硬件解码|avc1|H\.264/i.test(msg || '');
    }

    function fallbackToScreencapForWebCodecs(customHint) {
        var hint = customHint || webCodecsUnavailableMessage();
        if (state.scrcpyPlayer) {
            state.scrcpyPlayer.stop();
            state.scrcpyPlayer = null;
        }
        updateMirrorModeBadge('screencap', hint);
        setStatus(hint + '，已自动切换截图投屏', 'warn');
        startScreencapMirror();
    }

    function fallbackToScreencapForH264() {
        fallbackToScreencapForWebCodecs(h264UnavailableMessage());
    }

    function ensureWebCodecsForScrcpyMirror() {
        return new Promise(function (resolve) {
            if (!global.ScrcpyMirrorPlayer) {
                fallbackToScreencapForWebCodecs('缺少 scrcpy 播放器组件');
                resolve(false);
                return;
            }
            if (!webCodecsSupported()) {
                fallbackToScreencapForWebCodecs();
                resolve(false);
                return;
            }
            /* VideoDecoder API 存在即尝试 scrcpy；isConfigSupported 误报时不提前阻断 */
            resolve(true);
        });
    }

    function retryScrcpyAlternateTransport(reason) {
        if (state._scrcpyAltTried) return false;
        var wasHttp = !!(state.scrcpyPlayer && state.scrcpyPlayer._httpAbort);
        var wasWs = !!(state.scrcpyPlayer && state.scrcpyPlayer._ws);
        state._mirrorEpoch = (state._mirrorEpoch || 0) + 1;
        if (state.scrcpyPlayer) {
            state.scrcpyPlayer.stop();
            state.scrcpyPlayer = null;
        }
        state._scrcpyAltTried = true;
        state.mirrorRunning = true;
        state.scrcpyGotFrame = false;
        if (wasWs && state.mirrorStreamUrl) {
            setStatus((reason || 'scrcpy 重试') + '，切换 HTTP 通道…', 'warn');
            startScrcpyHttpMirror(true);
            return true;
        }
        if (wasHttp && state.mirrorWsUrl) {
            setStatus((reason || 'scrcpy 重试') + '，切换 WebSocket…', 'warn');
            startScrcpyWsMirror(true);
            return true;
        }
        return false;
    }

    function scrcpyMirrorFailed(msg) {
        if (isMirrorAbortError({ message: msg })) return;
        if (isWebCodecsRelatedError(msg)) {
            fallbackToScreencapForWebCodecs();
            return;
        }
        var p = state.scrcpyPlayer;
        if (state.scrcpyGotFrame || (p && (p._gotFrame || p._notifiedFrame))) {
            return;
        }
        if (state.mirrorBackend === 'scrcpy_ws') {
            if (retryScrcpyAlternateTransport(msg)) return;
            if (!scrcpyAutoFallbackEnabled()) {
                // 判断是否是流断开（非初始化失败）
                var isStreamLost = /流已结束|已断开|连接失败/i.test(msg || '');
                if (isStreamLost) {
                    updateMirrorModeBadge('connecting', 'scrcpy 等待重连');
                    setStatus('scrcpy 通道已断开且无法自动恢复，请检查设备连接后点击「一键连接」重新投屏', 'warn');
                } else {
                    updateMirrorModeBadge('connecting', 'scrcpy 等待视频帧');
                    setStatus((msg || 'scrcpy 无画面') + '。仍保持 scrcpy 通道，请断开重连或查看日志', 'warn');
                }
                return;
            }
        }
        updateMirrorModeBadge('screencap', msg || '高帧率投屏失败');
        setStatus((msg || 'scrcpy 无画面') + '，降级为截图模式', 'warn');
        startScreencapMirror();
    }

    function buildScrcpyPlayer() {
        var canvas = $('msMirrorCanvas');
        if (!canvas || !global.ScrcpyMirrorPlayer) return null;
        if (!webCodecsSupported()) return null;
        return new global.ScrcpyMirrorPlayer({
            canvas: canvas,
            wsUrl: state.mirrorWsUrl,
            maxReconnect: 2,
            noFrameTimeoutMs: 25000,
            decodeTimeoutMs: 45000,
            onFirstFrame: function () {
                state.scrcpyGotFrame = true;
                stopScreencapMirrorOnly();
                showMirrorCanvas();
                updateMirrorModeBadge('scrcpy', 'H.264 硬件视频流');
                var c = $('msMirrorCanvas');
                if (c && c.width && c.height) {
                    state.deviceWidth = c.width;
                    state.deviceHeight = c.height;
                    layoutMirrorCanvas();
                }
                setStatus('高帧率 scrcpy 投屏已就绪', 'ok');
            },
            onFps: function (fps) {
                var dim = $('msDeviceDim');
                if (!dim) return;
                var base = state.deviceWidth + ' × ' + state.deviceHeight;
                dim.textContent = base + ' · ' + fps + ' fps · scrcpy';
                if (state.mirrorBackend === 'scrcpy_ws' && fps < 10) {
                    state._lowFpsCount = (state._lowFpsCount || 0) + 1;
                    if (state._lowFpsCount >= 3) {
                        setStatus('投屏帧率偏低（' + fps + ' fps），请确认 scrcpy 已安装且手机已解锁', 'warn');
                        state._lowFpsCount = 0;
                    }
                } else {
                    state._lowFpsCount = 0;
                }
            },
            onReconnect: function (attempt, max) {
                setStatus('投屏短暂中断，正在重连 (' + attempt + '/' + max + ')…', 'warn');
            },
            onFallback: function (msg) {
                if (!state.mirrorRunning) return;
                var p = state.scrcpyPlayer;
                if (state.scrcpyGotFrame || (p && (p._gotFrame || p._notifiedFrame))) {
                    return;
                }
                if (retryScrcpyAlternateTransport(msg)) return;
                if (state.mirrorBackend === 'scrcpy_ws' && !scrcpyAutoFallbackEnabled()) {
                    updateMirrorModeBadge('connecting', 'scrcpy 等待视频帧');
                    setStatus('scrcpy 视频流建立中，请稍候…', '');
                    return;
                }
                if (state.scrcpyPlayer) {
                    state.scrcpyPlayer.stop();
                    state.scrcpyPlayer = null;
                }
                scrcpyMirrorFailed(msg);
            },
            onError: function (msg) {
                if (!state.mirrorRunning) return;
                if (state.scrcpyGotFrame) return;
                var p = state.scrcpyPlayer;
                if (p && (p._gotFrame || p._notifiedFrame)) return;
                var fatal = /不支持|WebSocket|已断开|长时间|WebCodecs/i.test(msg || '');
                if (!fatal) return;
                if (state.scrcpyPlayer) {
                    state.scrcpyPlayer.stop();
                    state.scrcpyPlayer = null;
                }
                scrcpyMirrorFailed(msg);
            },
        });
    }

    function startScrcpyHttpMirror(skipWsFallback, skipProbe) {
        function run() {
            if (!state.mirrorStreamUrl) {
                startScrcpyWsMirror(skipWsFallback, true);
                return;
            }
            var epoch = (state._mirrorEpoch = (state._mirrorEpoch || 0) + 1);
            state.mirrorRunning = true;
            state.scrcpyPlayer = buildScrcpyPlayer();
            if (state.scrcpyPlayer) {
                state.scrcpyPlayer._mirrorEpoch = epoch;
            }
            if (!state.scrcpyPlayer) {
                fallbackToScreencapForH264();
                return;
            }
            setStatus('高帧率投屏连接中（HTTP）…', '');
            state.scrcpyPlayer.startHttp(state.mirrorStreamUrl).catch(function (e) {
                if (!state.mirrorRunning || epoch !== state._mirrorEpoch) return;
                if (isMirrorAbortError(e)) return;
                if (state.scrcpyPlayer && state.scrcpyPlayer._mirrorEpoch === epoch) {
                    state.scrcpyPlayer = null;
                }
                if (!skipWsFallback && state.mirrorWsUrl) {
                    setStatus('HTTP 投屏失败，尝试 WebSocket…', 'warn');
                    startScrcpyWsMirror(true, true);
                    return;
                }
                scrcpyMirrorFailed(e.message || String(e));
            });
        }
        if (skipProbe) {
            run();
            return;
        }
        ensureWebCodecsForScrcpyMirror().then(function (ok) {
            if (ok) run();
        });
    }

    function startScrcpyWsMirror(skipHttpFallback, skipProbe) {
        function run() {
            if (!state.mirrorWsUrl || !global.ScrcpyMirrorPlayer) {
                if (!skipHttpFallback && state.mirrorStreamUrl) {
                    startScrcpyHttpMirror(true, true);
                    return;
                }
                scrcpyMirrorFailed('缺少 scrcpy WebSocket 地址');
                return;
            }
            var epoch = (state._mirrorEpoch = (state._mirrorEpoch || 0) + 1);
            state.mirrorRunning = true;
            state.scrcpyPlayer = buildScrcpyPlayer();
            if (state.scrcpyPlayer) {
                state.scrcpyPlayer._mirrorEpoch = epoch;
            }
            if (!state.scrcpyPlayer) {
                fallbackToScreencapForH264();
                return;
            }
            setStatus('高帧率投屏连接中（WebSocket）…', '');
            state.scrcpyPlayer.start().catch(function (e) {
                if (!state.mirrorRunning || epoch !== state._mirrorEpoch) return;
                if (isMirrorAbortError(e)) return;
                if (state.scrcpyPlayer && state.scrcpyPlayer._mirrorEpoch === epoch) {
                    state.scrcpyPlayer = null;
                }
                if (!skipHttpFallback && state.mirrorStreamUrl) {
                    setStatus('WebSocket 投屏失败，尝试 HTTP…', 'warn');
                    startScrcpyHttpMirror(true, true);
                    return;
                }
                scrcpyMirrorFailed(e.message || String(e));
            });
        }
        if (skipProbe) {
            run();
            return;
        }
        ensureWebCodecsForScrcpyMirror().then(function (ok) {
            if (ok) run();
        });
    }

    function startScreencapMirror() {
        var canvas = $('msMirrorCanvas');
        if (!canvas || !state.mirrorUrl) return;
        var ctx = canvas.getContext('2d');
        if (!ctx) return;
        var targetFps = (state.bootstrap && state.bootstrap.mirror_fps) || 8;
        var minInterval = Math.max(16, Math.floor(1000 / Math.max(1, targetFps)));
        state.mirrorRunning = true;
        state.mirrorBusy = false;
        state.mirrorFrameCount = 0;
        state.mirrorLastFpsAt = Date.now();

        function updateFpsLabel() {
            var dim = $('msDeviceDim');
            if (!dim || !state.deviceWidth) return;
            var now = Date.now();
            var elapsed = (now - state.mirrorLastFpsAt) / 1000;
            if (elapsed >= 2) {
                var fps = Math.round(state.mirrorFrameCount / elapsed);
                state.mirrorFrameCount = 0;
                state.mirrorLastFpsAt = now;
                dim.setAttribute('data-live-fps', String(fps));
            }
            var live = dim.getAttribute('data-live-fps');
            var base = state.deviceWidth + ' × ' + state.deviceHeight;
            dim.textContent = base + ' · ' + (live || '…') + ' fps · 截图';
        }

        function tick() {
            if (!state.mirrorRunning) return;
            if (state.mirrorBusy) {
                if (state.mirrorRunning) state.mirrorTimer = setTimeout(tick, minInterval);
                return;
            }
            var started = Date.now();
            state.mirrorBusy = true;
            var abort = new AbortController();
            state.mirrorAbort = abort;
            fetch(state.mirrorUrl, { credentials: 'same-origin', signal: abort.signal })
                .then(function (r) { return r.json(); })
                .then(function (data) {
                    if (!state.mirrorRunning || !data.success || !data.data) return;
                    var mime = (data.format === 'jpeg' || data.format === 'jpg') ? 'jpeg' : 'png';
                    var img = new Image();
                    return new Promise(function (resolve, reject) {
                        img.onload = resolve;
                        img.onerror = reject;
                        img.src = 'data:image/' + mime + ';base64,' + data.data;
                    }).then(function () {
                        if (!state.mirrorRunning) return;
                        if (canvas.width !== img.width || canvas.height !== img.height) {
                            canvas.width = img.width;
                            canvas.height = img.height;
                        }
                        ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
                        showMirrorCanvas();
                        state.deviceWidth = img.width;
                        state.deviceHeight = img.height;
                        layoutMirrorCanvas();
                        state.mirrorFrameCount += 1;
                        updateFpsLabel();
                    });
                })
                .catch(function (e) {
                    if (e && e.name === 'AbortError') return;
                })
                .finally(function () {
                    if (state.mirrorAbort === abort) state.mirrorAbort = null;
                    state.mirrorBusy = false;
                    if (!state.mirrorRunning) return;
                    var elapsed = Date.now() - started;
                    var delay = Math.max(0, minInterval - elapsed);
                    state.mirrorTimer = setTimeout(tick, delay);
                });
        }
        tick();
    }

    function startMirror() {
        stopMirror();
        if (!state.mirrorUrl && !state.mirrorStreamUrl && !state.mirrorWsUrl) return;
        if (state.mirrorBackend === 'scrcpy_ws' && (state.mirrorStreamUrl || state.mirrorWsUrl)) {
            state._scrcpyAltTried = false;
            ensureWebCodecsForScrcpyMirror().then(function (ok) {
                if (!ok) return;
                if (state.mirrorStreamUrl && global.ScrcpyMirrorPlayer) {
                    startScrcpyHttpMirror(false, true);
                } else if (state.mirrorWsUrl && global.ScrcpyMirrorPlayer) {
                    startScrcpyWsMirror(false, true);
                }
            });
            return;
        }
        startScreencapMirror();
    }

    function fetchFrame() {
        /* 已由 startMirror 统一处理 */
    }

    function startFramePolling() {
        startMirror();
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

    function updateRecordButtons() {
        var startBtn = $('msBtnStartRecord');
        var stopBtn = $('msBtnStopRecord');
        var can = state.connected && state.udid;
        if (startBtn) startBtn.disabled = !can || state.recording;
        if (stopBtn) stopBtn.disabled = !can || !state.recording;
    }

    function showKeyframe(b64, fmt, bounds) {
        var img = $('msKeyframeImg');
        var ph = $('msKeyframePlaceholder');
        if (!img) return;
        if (!b64) {
            img.style.display = 'none';
            if (ph) ph.style.display = '';
            return;
        }
        img.src = 'data:image/' + (fmt || 'jpeg') + ';base64,' + b64;
        img.style.display = 'block';
        if (ph) ph.style.display = 'none';
    }

    function renderLiveSteps() {
        var el = $('msLiveSteps') || $('msStepsList');
        var cnt = $('msLiveStepCount');
        if (!el) return;
        var html = '';
        state.liveSteps.forEach(function (item, idx) {
            var s = item.step || {};
            var cls = 'ms-step-live' + (idx === state.selectedLiveIndex ? ' ms-step-live--active' : '');
            html += '<div class="' + cls + '" data-idx="' + idx + '">' +
                '<strong>' + (idx + 1) + '.</strong> ' +
                (s.description || s.action || '步骤') + '</div>';
        });
        el.innerHTML = html || '<p class="ms-muted">暂无实时步骤</p>';
        if (cnt) cnt.textContent = String(state.liveSteps.length);
        el.querySelectorAll('[data-idx]').forEach(function (node) {
            node.addEventListener('click', function () {
                var i = parseInt(node.getAttribute('data-idx'), 10);
                state.selectedLiveIndex = i;
                var item = state.liveSteps[i];
                if (item) {
                    showKeyframe(item.screenshot_base64, item.format, null);
                    var tree = $('msTreeJson');
                    if (tree && item.step) tree.textContent = JSON.stringify(item.step, null, 2);
                }
                renderLiveSteps();
            });
        });
    }

    async function persistLiveStep(item) {
        if (!state.caseId || !item || !item.step) return;
        try {
            await apiJson('/api/mobile/assistant/event', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    event: item.raw || item.step,
                    case_id: state.caseId,
                    persist: true,
                }),
            });
            await loadSteps();
        } catch (e) { /* ignore */ }
    }

    function handleAgentEvent(msg) {
        if (!msg || !msg.type) return;
        if (msg.type === 'step') {
            var p = msg.payload || {};
            var item = {
                step: p.step,
                raw: p.raw,
                screenshot_base64: p.screenshot_base64 || '',
                format: (p.step && p.step.mobile_spec && p.step.mobile_spec.screenshot_format) || 'jpeg',
            };
            state.liveSteps.push(item);
            state.selectedLiveIndex = state.liveSteps.length - 1;
            renderLiveSteps();
            showKeyframe(item.screenshot_base64, item.format);
            persistLiveStep(item);
            setStatus('已捕获步骤 #' + state.liveSteps.length, 'ok');
        } else if (msg.type === 'plugin_disconnected') {
            state.recording = false;
            updateRecordButtons();
            var banner = $('msRecordingBanner');
            if (banner) {
                banner.style.display = '';
                banner.textContent = (msg.payload && msg.payload.message) || '插件通信中断';
                banner.className = 'ms-recording-banner ms-recording-banner--on';
            }
            setStatus('录制已暂停：插件断连', 'err');
        } else if (msg.type === 'recording_started') {
            state.recording = true;
            updateRecordButtons();
        } else if (msg.type === 'recording_stopped') {
            state.recording = false;
            updateRecordButtons();
        }
    }

    function connectAgentWs() {
        if (state.agentWs) {
            try { state.agentWs.close(); } catch (e) { /* ignore */ }
            state.agentWs = null;
        }
        var url = state.agentWsUrl || (state.bootstrap && state.bootstrap.agent_ws_url) || '';
        if (!url) return;
        try {
            var ws = new WebSocket(url);
            ws.onmessage = function (ev) {
                try { handleAgentEvent(JSON.parse(ev.data)); } catch (e) { /* ignore */ }
            };
            ws.onclose = function () {
                if (state.connected) setTimeout(connectAgentWs, 3000);
            };
            state.agentWs = ws;
        } catch (e) { /* ignore */ }
    }

    async function startRecording() {
        if (!state.connected) return;
        state.liveSteps = [];
        renderLiveSteps();
        var shot = ($('msScreenshotPerStep') || {}).checked !== false;
        await apiJson('/api/mobile/arm', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ udid: state.udid, screenshot_per_step: shot }),
        });
        state.recording = true;
        updateRecordButtons();
        var banner = $('msRecordingBanner');
        if (banner) {
            banner.style.display = '';
            banner.textContent = '录制中 — 请在手机端操作';
            banner.className = 'ms-recording-banner ms-recording-banner--on';
        }
        setStatus('录制已开始，请在手机上操作', 'ok');
    }

    async function stopRecording() {
        await apiJson('/api/mobile/disarm', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ udid: state.udid }),
        });
        state.recording = false;
        updateRecordButtons();
        var banner = $('msRecordingBanner');
        if (banner) banner.style.display = 'none';
        setStatus('录制已停止，共 ' + state.liveSteps.length + ' 步', 'ok');
    }

    async function peekAtHover(clientX, clientY) {
        if (!state.connected || state.interactionMode !== INTERACTION_CAPTURE) return;
        var now = Date.now();
        if (now - state.lastPeekTs < PEEK_INTERVAL_MS) return;
        if (Math.abs(clientX - state.lastPeekX) < PEEK_MIN_PX &&
            Math.abs(clientY - state.lastPeekY) < PEEK_MIN_PX) return;
        if (state.peekBusy) return;
        state.lastPeekTs = now;
        state.lastPeekX = clientX;
        state.lastPeekY = clientY;
        state.peekBusy = true;
        var pt = mapCanvasToDevice(clientX, clientY);
        try {
            var data = await apiJson('/api/mobile/peek-at', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ x: pt.x, y: pt.y, udid: state.udid }),
            });
            var bounds = data.bounds || (data.node && data.node.bounds);
            state.highlightBounds = bounds;
            drawHighlight(bounds);
        } catch (e) {
            state.highlightBounds = null;
            drawHighlight(null);
        } finally {
            state.peekBusy = false;
        }
    }

    async function armCurrentMode() {
        /* 由 startRecording 显式触发 */
    }

    async function disarmMode() {
        try {
            await apiJson('/api/mobile/disarm', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ notify_assistant: true }),
            });
        } catch (e) { /* ignore */ }
    }

    function wireMirrorInteraction() {
        var canvas = $('msMirrorCanvas');
        if (!canvas) return;
        canvas.onmousedown = function (ev) {
            if (!state.connected || !state.udid) return;
            state.pointerDown = { x: ev.clientX, y: ev.clientY, t: Date.now() };
        };
        canvas.onmousemove = function (ev) {
            if (state.interactionMode === INTERACTION_CAPTURE) {
                peekAtHover(ev.clientX, ev.clientY);
            }
        };
        canvas.onmouseup = function (ev) {
            if (!state.connected || !state.pointerDown) return;
            var dx = Math.abs(ev.clientX - state.pointerDown.x);
            var dy = Math.abs(ev.clientY - state.pointerDown.y);
            if (dx < 8 && dy < 8) {
                if (state.interactionMode === INTERACTION_CAPTURE) {
                    captureElementAt(ev.clientX, ev.clientY).catch(function (e) {
                        setStatus(e.message || String(e), 'err');
                    });
                } else {
                    operateTapAt(ev.clientX, ev.clientY).catch(function (e) {
                        setStatus(e.message || String(e), 'err');
                    });
                }
            } else if (state.interactionMode === INTERACTION_CAPTURE) {
                setStatus('捕获元素模式下请单击，勿拖拽', 'warn');
            } else {
                operateSwipeAt(state.pointerDown.x, state.pointerDown.y, ev.clientX, ev.clientY)
                    .catch(function (e) { setStatus(e.message || String(e), 'err'); });
            }
            state.pointerDown = null;
        };
        canvas.onmouseleave = function () {
            state.pointerDown = null;
            state.highlightBounds = null;
            drawHighlight(null);
        };
    }

    function applyConnectPayload(data) {
        state.udid = data.udid || '';
        state.sessionId = data.session_id || '';
        state.mirrorBackend = data.mirror_backend || 'screencap';
        state.mirrorWsUrl = resolveMirrorWsUrl(data.mirror_ws_url || '');
        state.mirrorStreamUrl = data.mirror_stream_url || '';
        state.mirrorUrl = data.mirror_frame_url || '';
        state.mirror_fallback_reason = data.mirror_fallback_reason || '';
        state.scrcpyGotFrame = false;
        state._scrcpyAltTried = false;
        state.connected = true;
        state.appiumConnected = !!data.appium_connected;
        if (data.device) {
            state.deviceWidth = data.device.width || 1080;
            state.deviceHeight = data.device.height || 1920;
        }
        // 保存 warm 诊断信息（含设备预检 + 参数档位）
        state._scrcpyWarmDiag = data.scrcpy_warm_diagnostics || null;
        if (state._scrcpyWarmDiag && state._scrcpyWarmDiag.device_diagnostics) {
            state._deviceDiag = state._scrcpyWarmDiag.device_diagnostics;
        } else {
            state._deviceDiag = data.device_diagnostics || null;
        }
        // 保存当前使用的参数档位信息
        if (data.scrcpy_current_profile) {
            state._scrcpyProfile = {
                name: data.scrcpy_current_profile,
                fps: data.scrcpy_current_fps,
                bitrate: data.scrcpy_current_bitrate,
                maxSize: data.scrcpy_current_max_size,
            };
        }
        layoutMirrorCanvas();
        startAssistantPolling();
        connectAgentWs();
        updateRecordButtons();
        var badge = $('msConnectBadge');
        if (badge) {
            badge.textContent = '已连接';
            badge.className = 'ms-connect-badge ms-connect-badge--on';
        }
        if (state.mirrorBackend === 'scrcpy_ws') {
            updateMirrorModeBadge('connecting', '正在建立 scrcpy 视频流');
        } else {
            var fallbackMsg = data.mirror_fallback_reason || 'adb screencap 截图轮询';
            updateMirrorModeBadge('screencap', fallbackMsg);
            // 如果有设备诊断信息，在状态栏展示关键提示
            if (state._deviceDiag && state._deviceDiag.warnings && state._deviceDiag.warnings.length > 0) {
                fallbackMsg += ' | ⚠ ' + state._deviceDiag.warnings[0];
            }
            if (state._scrcpyWarmDiag && state._scrcpyWarmDiag.scrcpy_server_version) {
                fallbackMsg += ' (scrcpy-server ' + state._scrcpyWarmDiag.scrcpy_server_version + ')';
            }
        }
        if (data.client_mirror_hint && state.mirrorBackend === 'scrcpy_ws') {
            state.clientMirrorHint = data.client_mirror_hint;
        }
        if (data.scrcpy_warmed && state.mirrorBackend === 'scrcpy_ws') {
            state.scrcpyWarmed = true;
        }
        var msg = '已连接 ' + (data.device && data.device.model ? data.device.model : state.udid);
        if (data.is_emulator || isEmulatorUdid(state.udid)) msg += '（模拟器）';
        if (state.mirrorBackend === 'scrcpy_ws') msg += ' · 投屏连接中';
        else if (data.mirror_fallback_reason) msg += ' · ' + data.mirror_fallback_reason;
        if (data.scrcpy_server_version && state.mirrorBackend === 'scrcpy_ws') {
            msg += ' · scrcpy-server ' + data.scrcpy_server_version;
        }
        if (data.plugin_ready) msg += ' · 插件就绪';
        else if (data.assistant_needs_install) {
            var devVer = data.assistant_version_on_device || 0;
            var expName = data.assistant_version_name_expected || '';
            msg += ' · 助手需升级（设备 versionCode=' + devVer + ' → v' + expName + '）';
        }
        if (data.assistant_install_hint) {
            setStatus(data.assistant_install_hint, 'warn');
        } else {
            setStatus(msg, 'ok');
        }
        startMirror();
    }

    function renderSteps() {
        var box = $('msStepsList');
        var openBtn = $('msBtnOpenSteps');
        if (openBtn) openBtn.disabled = !state.caseId;
        if (!box) return;
        if (!state.steps.length) {
            box.innerHTML = '<p class="ms-muted">暂无用例步骤。请录制、捕获或让 Agent 生成。</p>';
            return;
        }
        box.innerHTML = state.steps.map(function (st, i) {
            var layer = (st.automation_layer || 'web').toLowerCase();
            var badge = layer === 'android' ? 'Android' : layer;
            var act = st.action || '';
            var shortSel = (st.selector_value || '').length > 36
                ? (st.selector_value || '').slice(0, 36) + '…'
                : (st.selector_value || '');
            var running = state.runningStepId && String(st.id) === String(state.runningStepId);
            return (
                '<div class="ms-step-item' + (running ? ' ms-step-item--running' : '') + '" data-step-id="' + st.id + '">' +
                '<span class="ms-step-ord">' + (st.step_order || i + 1) + '</span>' +
                '<span class="ms-step-act">' + act + '</span>' +
                '<span class="ms-step-badge">' + badge + '</span>' +
                '<span class="ms-step-desc">' + (st.description || shortSel || '') + '</span>' +
                '<span class="ms-step-actions"><button type="button" class="ms-step-del" data-del-step="' + st.id + '">删</button></span>' +
                '</div>'
            );
        }).join('');
        box.querySelectorAll('[data-del-step]').forEach(function (btn) {
            btn.addEventListener('click', function () {
                var sid = btn.getAttribute('data-del-step');
                deleteStep(sid).catch(function (e) { setStatus(e.message, 'err'); });
            });
        });
    }

    async function deleteStep(stepId) {
        if (!stepId) return;
        if (!window.confirm('删除该步骤？')) return;
        await apiJson('/api/steps/' + stepId, { method: 'DELETE' });
        await loadSteps();
        setStatus('已删除步骤', 'ok');
    }

    function setInteractionMode(mode) {
        state.interactionMode = mode || INTERACTION_OPERATE;
        document.querySelectorAll('.ms-mode-btn').forEach(function (btn) {
            var on = btn.getAttribute('data-mode') === state.interactionMode;
            btn.classList.toggle('ms-mode-btn--active', on);
        });
        var meta = $('msRecordMeta');
        var probeEl = $('msProbeInfo');
        if (state.interactionMode === INTERACTION_CAPTURE) {
            if (meta) meta.textContent = '当前：捕获元素（悬停高亮，单击写入定位）';
        } else if (state.interactionMode === INTERACTION_OPERATE) {
            if (meta) meta.textContent = '当前：直接操作（单击点击、拖拽滑动，同步到手机）';
        } else {
            if (meta) meta.textContent = '当前：录制（请在手机上操作并使用「开始录制」）';
            if (probeEl) probeEl.style.display = 'none';
            state.highlightBounds = null;
            drawHighlight(null);
        }
        if (state.connected) armCurrentMode().catch(function () {});
    }

    function pushSessionAction(action) {
        state.recordingSession.push(Object.assign({ at: Date.now() }, action));
        renderRecordingTimeline();
    }

    function renderRecordingTimeline() {
        var meta = $('msRecordSessionMeta');
        var box = $('msRecordTimeline');
        var count = state.recordingSession.length;
        if (meta) meta.textContent = '本次会话：' + count + ' 个动作';
        if (!box) return;
        if (!count) {
            box.innerHTML = '<span>暂无录制动作</span>';
            return;
        }
        box.innerHTML = state.recordingSession.map(function (act, i) {
            if (act.type === 'capture') return (i + 1) + '. 捕获 ' + (act.label || act.selector_value || '');
            if (act.type === 'swipe') {
                return (i + 1) + '. 滑动 (' + act.x1 + ',' + act.y1 + ')→(' + act.x2 + ',' + act.y2 + ')';
            }
            return (i + 1) + '. 点击 (' + act.x + ',' + act.y + ')';
        }).join('<br>');
    }

    function clearRecordingSession() {
        state.recordingSession = [];
        renderRecordingTimeline();
        setStatus('已清空录制会话', '');
    }

    async function replayRecordingSession() {
        if (!state.recordingSession.length) {
            setStatus('当前没有可回放的动作', 'warn');
            return;
        }
        if (!state.connected || !state.udid) {
            setStatus('请先连接设备', 'warn');
            return;
        }
        setStatus('正在回放 ' + state.recordingSession.length + ' 个动作…', '');
        var data = await apiJson('/api/mobile/replay-actions', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                udid: state.udid,
                actions: state.recordingSession.filter(function (a) { return a.type !== 'capture'; }),
                delay_ms: 600,
            }),
        });
        if (data.success) setStatus('回放完成（' + (data.total || 0) + ' 步）', 'ok');
        else setStatus('回放结束，' + (data.failed || 0) + ' 步失败', 'err');
    }

    async function operateTapAt(clientX, clientY) {
        if (!state.connected || !state.udid) return;
        var pt = mapCanvasToDevice(clientX, clientY);
        var player = state.scrcpyPlayer;
        if (player && state.scrcpyGotFrame && player.sendTap) {
            if (player.sendTap(pt.x, pt.y, state.deviceWidth, state.deviceHeight)) {
                return;
            }
        }
        await apiJson('/api/mobile/tap-at', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ x: pt.x, y: pt.y, udid: state.udid }),
        });
    }

    async function operateSwipeAt(x1, y1, x2, y2) {
        if (!state.connected || !state.udid) return;
        var p1 = mapCanvasToDevice(x1, y1);
        var p2 = mapCanvasToDevice(x2, y2);
        var player = state.scrcpyPlayer;
        if (player && state.scrcpyGotFrame && player.sendSwipe) {
            if (player.sendSwipe(p1.x, p1.y, p2.x, p2.y, state.deviceWidth, state.deviceHeight)) {
                return;
            }
        }
        await apiJson('/api/mobile/swipe-at', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                x1: p1.x,
                y1: p1.y,
                x2: p2.x,
                y2: p2.y,
                udid: state.udid,
            }),
        });
    }

    async function recordTapAt(clientX, clientY) {
        var pt = mapCanvasToDevice(clientX, clientY);
        await operateTapAt(clientX, clientY);
        if (!state.caseId) {
            setStatus('已点击 (' + pt.x + ',' + pt.y + ')', 'ok');
            return;
        }
        setStatus('画布点选录制已停用，请点击已同步到设备；步骤请在手机上录制或使用 Playground', 'warn');
    }

    async function recordSwipeAt(x1, y1, x2, y2) {
        await operateSwipeAt(x1, y1, x2, y2);
        if (!state.caseId) {
            setStatus('已滑动', 'ok');
            return;
        }
        setStatus('画布滑动录制已停用，操作已同步到设备', 'warn');
    }

    async function captureElementAt(clientX, clientY) {
        if (!state.caseId) {
            setStatus('请先选择用例再捕获元素', 'warn');
            return;
        }
        var pt = mapCanvasToDevice(clientX, clientY);
        setStatus('正在捕获元素…', '');
        var data = await apiJson('/api/mobile/record-step', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                case_id: state.caseId,
                x: pt.x,
                y: pt.y,
                udid: state.udid,
                kind: 'element',
                also_tap: !!state.alsoTapOnRecord,
                source: 'mirror',
            }),
        });
        var st = (data.step || {});
        pushSessionAction({
            type: 'capture',
            x: pt.x,
            y: pt.y,
            selector_value: st.selector_value || data.suggested_selector_value,
            label: st.description,
        });
        await loadSteps();
        showProbeFromPick(data.picked || {}, data);
        setStatus((data.step && data.step.description) || '已捕获元素', 'ok');
        fetchFrame();
    }

    function showProbeFromPick(picked, pickData) {
        var el = (picked && picked.node) || (pickData && pickData.element) || {};
        var probeEl = $('msProbeInfo');
        if (!probeEl) return;
        var lines = [];
        if (el.resource_id) lines.push('id: ' + el.resource_id);
        if (el.content_desc) lines.push('content-desc: ' + el.content_desc);
        if (el.text) lines.push('text: ' + el.text);
        var st = (pickData && pickData.step) || {};
        if (st.selector_type) lines.push('定位: ' + st.selector_type + ' = ' + (st.selector_value || ''));
        probeEl.innerHTML = lines.length
            ? lines.join('<br>') + '<br><button type="button" class="ms-btn ms-btn--ghost ms-btn--sm" id="msBtnSaveElement">加入元素库</button>'
            : '未识别到控件';
        probeEl.style.display = '';
        var btn = $('msBtnSaveElement');
        if (btn && (st.selector_value || pickData.suggested_selector_value)) {
            btn.onclick = function () {
                saveProbedElement({
                    suggested_selector_type: st.selector_type || pickData.suggested_selector_type,
                    suggested_selector_value: st.selector_value || pickData.suggested_selector_value,
                    element: el,
                }).catch(function (e) { setStatus(e.message, 'err'); });
            };
        }
    }

    async function saveProbedElement(pickData) {
        if (!state.projectId) {
            setStatus('请先选择项目', 'warn');
            return;
        }
        var alias = window.prompt('元素别名', pickData.suggested_selector_value || 'element');
        if (!alias || !alias.trim()) return;
        await apiJson('/api/element-repository', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                project_id: state.projectId,
                alias: alias.trim(),
                platform: 'android',
                selector_type: pickData.suggested_selector_type || 'accessibility_id',
                selector_value: pickData.suggested_selector_value || '',
                attributes: pickData.element || {},
            }),
        });
        setStatus('已加入元素库：' + alias.trim(), 'ok');
    }

    async function loadProjects() {
        var data = await apiJson('/api/projects');
        var sel = $('msProjectSelect');
        if (!sel) return;
        var list = data.projects || data.data || [];
        sel.innerHTML = '<option value="">选择项目</option>' + list.map(function (p) {
            return '<option value="' + p.id + '">' + (p.name || ('#' + p.id)) + '</option>';
        }).join('');
    }

    async function loadCases() {
        var sel = $('msCaseSelect');
        if (!sel || !state.projectId) {
            if (sel) sel.innerHTML = '<option value="">先选择项目</option>';
            return;
        }
        var data = await apiJson('/api/projects/' + state.projectId + '/cases');
        var cases = data.cases || [];
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
        var data = await apiJson('/api/mobile/cases/' + state.caseId + '/steps');
        state.steps = data.steps || [];
        renderSteps();
        var androidCount = state.steps.filter(function (s) {
            return (s.automation_layer || 'web') === 'android';
        }).length;
        var meta = $('msCaseMeta');
        if (meta) {
            meta.textContent = state.steps.length + ' 步' +
                (androidCount ? '（' + androidCount + ' 步 Android）' : '（建议将步骤设为 Android 层）');
        }
    }

    function deviceStateLabel(st) {
        var s = (st || '').toLowerCase();
        if (s === 'device') return '已授权';
        if (s === 'unauthorized') return '需授权';
        if (s === 'offline') return '离线';
        return st || '未知';
    }

    function scoreDevicePriority(d) {
        var udid = d.udid || '';
        var st = (d.state || '').toLowerCase();
        var score = 0;
        if (st === 'device') score += 1000;
        else if (st === 'unauthorized') score += 200;
        else if (st === 'offline') score += 50;
        if (isEmulatorUdid(udid)) score -= 500;
        if (/^PQ/i.test(udid)) score -= 800;
        if (/_adb-tls|_tcp/i.test(udid)) score -= 900;
        if (/^\d{1,3}(?:\.\d{1,3}){3}:\d+$/.test(udid)) score += 100;
        else if (udid && !isEmulatorUdid(udid)) score += 300;
        return score;
    }

    function sortDevicesForUi(list) {
        return (list || []).slice().sort(function (a, b) {
            return scoreDevicePriority(b) - scoreDevicePriority(a);
        });
    }

    function getRealDeviceList() {
        if (state.realDevices && state.realDevices.length) {
            return state.realDevices;
        }
        var all = (state.bootstrap && state.bootstrap.health && state.bootstrap.health.devices) || [];
        return sortDevicesForUi(all.filter(function (d) {
            return !isEmulatorUdid(d.udid);
        }));
    }

    function getSelectedRealDevice() {
        var udid = ($('msDeviceSelect') || {}).value || '';
        return getRealDeviceList().find(function (d) { return d.udid === udid; });
    }

    function renderDeviceList(devices) {
        var devSel = $('msDeviceSelect');
        if (!devSel) return;
        var list = sortDevicesForUi(devices || []);
        if (!list.length) {
            devSel.innerHTML = '<option value="">（未发现设备）</option>';
            return;
        }
        devSel.innerHTML = list.map(function (d) {
            var tag = (d.is_emulator || isEmulatorUdid(d.udid)) ? '模拟器' : '设备';
            var stateLbl = deviceStateLabel(d.state);
            var label = (d.display_name || d.udid) + ' [' + tag + '] (' + stateLbl + ')';
            var disabled = (d.state || '') !== 'device' ? ' disabled' : '';
            return '<option value="' + d.udid + '"' + disabled + ' data-state="' + (d.state || '') + '">' + label + '</option>';
        }).join('');
        var ready = list.find(function (d) { return d.state === 'device'; });
        if (ready) devSel.value = ready.udid;
    }

    function renderEmulatorList(emulators) {
        var sel = $('msEmulatorSelect');
        if (!sel) return;
        var list = emulators || [];
        if (!list.length) {
            sel.innerHTML = '<option value="">（未发现模拟器，请启动后刷新）</option>';
            return;
        }
        sel.innerHTML = list.map(function (d) {
            return '<option value="' + d.udid + '">' + (d.display_name || d.udid) + ' (' + (d.state || '') + ')</option>';
        }).join('');
        var ready = list.find(function (d) { return d.state === 'device'; });
        if (ready) sel.value = ready.udid;
    }

    async function loadEmulators() {
        var data = await apiJson('/api/mobile/emulators');
        renderEmulatorList(data.emulators || []);
        return data.emulators || [];
    }

    function switchDeviceTab(tabName) {
        state.deviceTab = tabName || 'real';
        document.querySelectorAll('.ms-device-tab').forEach(function (btn) {
            btn.classList.toggle('ms-device-tab--active', btn.getAttribute('data-tab') === state.deviceTab);
        });
        var realPanel = $('msPanelReal');
        var emuPanel = $('msPanelEmulator');
        if (realPanel) realPanel.classList.toggle('ms-device-panel--active', state.deviceTab === 'real');
        if (emuPanel) emuPanel.classList.toggle('ms-device-panel--active', state.deviceTab === 'emulator');
        if (state.deviceTab === 'emulator') loadEmulators().catch(function () {});
    }

    function updateDeviceHelp(health, devices) {
        var el = $('msDeviceHelp');
        if (!el) return;
        var list = devices || [];
        var authorized = list.filter(function (d) { return d.state === 'device'; });
        var unauthorized = list.filter(function (d) { return d.state === 'unauthorized'; });
        if (!health || !health.adb_ok) {
            el.innerHTML = '<strong>ADB 不可用：</strong>请配置 <code>ADB_PATH</code> 或安装 Platform-Tools。';
            return;
        }
        if (!list.length) {
            el.textContent = '未发现设备。请 USB 连接真机、开启 USB 调试，或使用下方无线配对。';
            return;
        }
        if (!authorized.length && unauthorized.length) {
            el.textContent = '已发现 ' + unauthorized.length + ' 台设备，但尚未授权。请在手机上点击「允许 USB 调试」后刷新列表。';
            return;
        }
        if (!authorized.length) {
            el.textContent = '未发现已授权设备。请启动模拟器、USB 连接真机或无线配对后重试。';
            return;
        }
        var extra = (state.deviceWarnings || []).join(' ');
        el.textContent = '已识别 ' + authorized.length + ' 台可用设备。' + (extra ? ' ' + extra : '') +
            ' 未装助手时在下方画面区操作；装助手后可在外部模拟器窗口操作。';
    }

    function renderEnvStatus(diag) {
        var statusEl = $('msEnvStatus');
        var blockEl = $('msEnvBlocking');
        if (!statusEl) return;
        if (!diag) {
            statusEl.textContent = '尚未检测，请点击「检测环境」';
            statusEl.className = 'ms-env-status ms-env-status--idle';
            if (blockEl) blockEl.style.display = 'none';
            return;
        }
        var ready = diag.ready === true;
        if (ready) {
            statusEl.textContent = '✓ 环境已就绪';
            statusEl.className = 'ms-env-status ms-env-status--ready';
            if (blockEl) blockEl.style.display = 'none';
        } else {
            statusEl.textContent = '✕ ' + (diag.blocking_reason || '环境未就绪');
            statusEl.className = 'ms-env-status ms-env-status--fail';
            if (blockEl) {
                blockEl.textContent = diag.blocking_reason || '';
                blockEl.style.display = '';
            }
        }
    }

    async function checkEnvironment() {
        var btn = $('msBtnEnvCheck');
        if (btn) btn.disabled = true;
        try {
            var data = await apiJson('/api/mobile/diagnostics');
            state.diagnosticsCache = data;
            renderEnvStatus(data);
            return data;
        } finally {
            if (btn) btn.disabled = false;
        }
    }

    async function refreshAssistantStatus() {
        if (!state.udid) return;
        try {
            var data = await apiJson('/api/mobile/assistant/status?udid=' + encodeURIComponent(state.udid));
            state.assistantInstalled = !!data.assistant_installed;
            state.assistantConnected = !!data.assistant_connected;
            updateAssistantBadge();
        } catch (e) { /* ignore */ }
    }

    async function pollAssistantEvents() {
        if (!state.connected || !state.udid || !state.caseId) return;
        if (!state.assistantConnected && !state.assistantInstalled) return;
        try {
            var data = await apiJson('/api/mobile/assistant/events?limit=20');
            var events = data.events || [];
            if (!events.length) return;
            for (var i = 0; i < events.length; i++) {
                var ev = events[i];
                var res = await apiJson('/api/mobile/assistant/event', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        event: ev,
                        case_id: state.caseId,
                        persist: true,
                    }),
                });
                if (res.step_preview) {
                    pushSessionAction({
                        type: 'capture',
                        label: (res.step_preview.description || res.step_preview.action || '助手'),
                        selector_value: res.step_preview.selector_value,
                    });
                }
            }
            await loadSteps();
            setStatus('助手回传 ' + events.length + ' 个动作，已写入用例', 'ok');
        } catch (e) { /* ignore poll errors */ }
    }

    function startAssistantPolling() {
        stopAssistantPolling();
        refreshAssistantStatus();
        state.assistantPollTimer = setInterval(function () {
            refreshAssistantStatus();
        }, 5000);
    }

    function stopAssistantPolling() {
        if (state.assistantPollTimer) {
            clearInterval(state.assistantPollTimer);
            state.assistantPollTimer = null;
        }
    }

    async function installAssistant() {
        setStatus('正在安装助手…', '');
        var body = {};
        if (state.connected && state.udid) body.udid = state.udid;
        var data = await apiJson('/api/mobile/assistant/install', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        if (data.success) {
            var msg = data.message || '助手已就绪';
            if (data.expected_version) {
                msg += '（目标 v' + data.expected_version + '）';
            }
            if (data.assistant_version_on_device) {
                msg += ' 设备 versionCode=' + data.assistant_version_on_device;
            }
            if (data.device_push_pending && !state.connected) {
                msg = '安装包已准备。请连接设备后将自动推送到手机。';
            }
            setStatus(msg, data.device_push_pending ? 'warn' : 'ok');
            state.assistantInstalled = !!data.assistant_on_device || !!data.device_push_pending;
            updateAssistantBadge();
        } else {
            var hint = data.error || '安装失败';
            if (data.need_device) {
                hint += ' 也可先在插件市场安装（无需设备），连接后再推送。';
            } else if (hint.indexOf('未找到助手 APK') >= 0) {
                hint += '。请到插件市场安装「Testory 移动端助手」，或编译 mobile_assistant_apk 工程。';
            }
            if (hint.indexOf('无障碍') >= 0) {
                hint += ' 请在手机「设置 → 无障碍」中启用 Testory 助手。';
            }
            setStatus(hint, 'err');
        }
    }

    async function startAppium() {
        if (!state.connected || !state.udid) {
            setStatus('请先连接设备', 'warn');
            return;
        }
        setStatus('正在启动并连接 Appium…', '');
        var data = await apiJson('/api/mobile/appium/start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ udid: state.udid }),
        });
        state.appiumConnected = !!data.appium_connected;
        updateAppiumHint(data);
        if (data.success && state.appiumConnected) {
            setStatus('Appium 已启动并连接', 'ok');
        } else {
            setStatus(data.error || data.appium_error || 'Appium 连接失败', 'err');
        }
    }

    async function bootstrap() {
        var data = await apiJson('/api/mobile/testing/bootstrap');
        state.bootstrap = data;
        state.realDevices = data.real_devices || [];
        state.deviceWarnings = data.device_warnings || [];
        state.requiresAppium = !!data.requires_appium;
        state.driverMode = (data.driver_mode || 'auto').toLowerCase();
        state.agentWsUrl = data.agent_ws_url || '';
        state.assistantInstalled = !!data.assistant_installed;
        state.assistantConnected = !!data.assistant_connected;
        updateAssistantBadge();
        connectAgentWs();
        var realDevices = getRealDeviceList();
        renderDeviceList(realDevices);
        renderEmulatorList(data.emulators || []);
        updateDeviceHelp(data.health, realDevices);
        layoutMirrorCanvas();
    }

    async function refreshDevices() {
        try {
            await apiJson('/api/mobile/devices/prune', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: '{}',
            });
        } catch (e) { /* prune 失败仍继续刷新列表 */ }
        await bootstrap();
    }

    function getWirelessFormValues() {
        return {
            host: (($('msWirelessIp') || {}).value || '').trim(),
            port: (($('msWirelessPort') || {}).value || '').trim(),
            code: (($('msWirelessCode') || {}).value || '').trim(),
        };
    }

    function isWirelessFormComplete() {
        var v = getWirelessFormValues();
        var portNum = parseInt(v.port, 10);
        return !!v.host && !!v.port && portNum > 0 && portNum <= 65535
            && v.code.length === 6 && /^\d{6}$/.test(v.code);
    }

    function updateWirelessButtonState() {
        var btn = $('msBtnWirelessConnect');
        if (!btn) return;
        var ready = isWirelessFormComplete();
        btn.disabled = !ready;
        btn.classList.toggle('ms-btn--success', ready);
        btn.classList.toggle('ms-btn--ghost', !ready);
    }

    async function wirelessConnect() {
        var v = getWirelessFormValues();
        if (!isWirelessFormComplete()) {
            setStatus('请填写完整的无线配对信息', 'warn');
            return;
        }
        setStatus('正在无线配对/连接…', '');
        var data = await apiJson('/api/mobile/wireless/connect', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                host: v.host,
                port: parseInt(v.port, 10),
                pairing_code: v.code,
            }),
        });
        await bootstrap();
        var devSel = $('msDeviceSelect');
        if (devSel && data.udid) devSel.value = data.udid;
        await connectDevice({ tryAppium: shouldTryAppium() });
        setStatus(data.message || ('已连接 ' + data.udid), 'ok');
    }

    async function connectDevice(opts) {
        opts = opts || {};
        setStatus('正在连接设备…', '');
        var udid = '';
        if (state.deviceTab === 'emulator') {
            udid = ($('msEmulatorSelect') || {}).value || '';
        } else {
            udid = ($('msDeviceSelect') || {}).value || '';
            var selected = getSelectedRealDevice();
            if (selected && selected.state === 'unauthorized') {
                setStatus('设备尚未授权 USB 调试。请在手机上点击「允许」并勾选「始终允许」后刷新列表。', 'warn');
                return;
            }
            if (selected && selected.state && selected.state !== 'device') {
                setStatus('设备状态为「' + deviceStateLabel(selected.state) + '」，无法连接。', 'warn');
                return;
            }
        }
        var tryAppium = opts.tryAppium;
        if (tryAppium === undefined) tryAppium = shouldTryAppium();
        var url = state.deviceTab === 'emulator' ? '/api/mobile/emulator/connect' : '/api/mobile/auto-connect';
        var data = await apiJson(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ udid: udid, try_appium: tryAppium }),
        });
        applyConnectPayload(data);
    }

    async function connectEmulator() {
        state.deviceTab = 'emulator';
        await connectDevice({ tryAppium: shouldTryAppium() });
    }

    async function disconnect() {
        try {
            await apiJson('/api/mobile/disconnect', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ udid: state.udid }),
            });
        } catch (e) {
            setStatus((e && e.message) || '断开失败', 'err');
            return;
        }
        await disarmMode();
        stopMirror();
        stopAssistantPolling();
        state.connected = false;
        state.udid = '';
        state.sessionId = '';
        state.appiumConnected = false;
        resetMirrorUi();
        layoutMirrorCanvas();
        var badge = $('msConnectBadge');
        if (badge) {
            badge.textContent = '未连接';
            badge.className = 'ms-connect-badge';
        }
        var modeEl = $('msMirrorMode');
        if (modeEl) {
            modeEl.textContent = '未连接';
            modeEl.className = 'ms-mirror-mode ms-mirror-mode--idle';
        }
        setStatus('已断开', '');
        bootstrap().catch(function () {});
    }

    async function pollRunProgress() {
        try {
            var st = await apiJson('/api/cases/current-run/status');
            if (st && st.active) {
                var hit = state.steps.find(function (s) {
                    return Number(s.step_order) === Number(st.current_step_order);
                });
                state.runningStepId = hit ? hit.id : null;
                renderSteps();
                setStatus(st.message || '执行中…', '');
                return true;
            }
        } catch (e) { /* ignore */ }
        return false;
    }

    async function runCase() {
        if (!state.caseId) {
            setStatus('请先选择用例', 'warn');
            return;
        }
        if (!state.connected || !state.udid) {
            setStatus('请先连接设备', 'warn');
            return;
        }
        setStatus('正在执行用例…', '');
        state.runningStepId = null;
        renderSteps();
        var pollTimer = setInterval(function () { pollRunProgress().catch(function () {}); }, 800);
        try {
            var data = await apiJson('/api/mobile/run', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ case_id: state.caseId, udid: state.udid, use_sync: true }),
            });
            if (data.success && data.sync_job_id) {
                setStatus(data.message || '已下发到手机执行', 'ok');
                return;
            }
            if (data.success) {
                setStatus('执行成功，耗时 ' + (data.duration || '') + 's', 'ok');
                return;
            }
            setStatus(data.error || '执行失败', 'err');
        } catch (syncErr) {
            try {
                var data2 = await apiJson('/api/mobile/run', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ case_id: state.caseId, udid: state.udid }),
                });
                if (data2.success) setStatus('执行成功（网关回放），耗时 ' + (data2.duration || '') + 's', 'ok');
                else setStatus(data2.error || syncErr.message || '执行失败', 'err');
            } catch (e2) {
                setStatus((e2 && e2.message) || syncErr.message || '执行失败', 'err');
            }
        } finally {
            clearInterval(pollTimer);
            state.runningStepId = null;
            renderSteps();
        }
    }

    async function initDevicePair() {
        setStatus('正在生成配对码…', '');
        var data = await apiJson('/api/mobile/sync/pair/init', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({}),
        });
        if (!data.success || !data.pair_code) {
            setStatus(data.error || '配对码生成失败', 'err');
            return;
        }
        showPairCode(data.pair_code, data.expires_in || 600);
    }

    function showPairCode(code, expiresIn) {
        state.pairCode = code;
        var box = $('msPairBox');
        var el = $('msPairCode');
        var exp = $('msPairExpiry');
        var copyBtn = $('msBtnCopyPairCode');
        var urlHint = $('msSyncUrlHint');
        if (urlHint) urlHint.textContent = window.location.origin;
        if (box) box.style.display = '';
        if (el) el.textContent = code;
        if (exp) {
            var mins = Math.max(1, Math.round((expiresIn || 600) / 60));
            exp.textContent = '有效期约 ' + mins + ' 分钟，请在手机 App 输入';
        }
        if (copyBtn) copyBtn.style.display = '';
        setStatus('配对码已生成：' + code + '（请在手机 Testory Assistant 输入）', 'ok');
    }

    function wireUi() {
        $('msBtnConnect').addEventListener('click', function () {
            connectDevice().catch(function (e) { setStatus(e.message, 'err'); });
        });
        $('msBtnDisconnect').addEventListener('click', disconnect);
        $('msBtnRefreshDevices').addEventListener('click', function () {
            refreshDevices().catch(function (e) { setStatus(e.message, 'err'); });
        });
        $('msBtnRefreshEmulators').addEventListener('click', function () {
            loadEmulators().catch(function (e) { setStatus(e.message, 'err'); });
        });
        $('msBtnEmulatorConnect').addEventListener('click', function () {
            connectEmulator().catch(function (e) { setStatus(e.message, 'err'); });
        });
        document.querySelectorAll('.ms-device-tab').forEach(function (btn) {
            btn.addEventListener('click', function () {
                switchDeviceTab(btn.getAttribute('data-tab'));
            });
        });
        var btnWireless = $('msBtnWirelessConnect');
        if (btnWireless) {
            btnWireless.addEventListener('click', function () {
                if (!btnWireless.disabled) wirelessConnect().catch(function (e) { setStatus(e.message, 'err'); });
            });
        }
        ['msWirelessIp', 'msWirelessPort', 'msWirelessCode'].forEach(function (id) {
            var el = $(id);
            if (el) {
                el.addEventListener('input', updateWirelessButtonState);
                el.addEventListener('change', updateWirelessButtonState);
            }
        });
        updateWirelessButtonState();
        $('msBtnEnvCheck').addEventListener('click', function () {
            checkEnvironment().catch(function (e) { setStatus(e.message, 'err'); });
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
            if (state.connected) armCurrentMode().catch(function () {});
        });
        $('msBtnRun').addEventListener('click', function () {
            runCase().catch(function (e) { setStatus(e.message, 'err'); });
        });
        $('msBtnOpenSteps').addEventListener('click', function () {
            if (state.caseId) window.open('/list_steps?case_id=' + state.caseId, '_blank');
        });
        $('msBtnInstallAssistant').addEventListener('click', function () {
            installAssistant().catch(function (e) { setStatus(e.message, 'err'); });
        });
        var btnStart = $('msBtnStartRecord');
        if (btnStart) {
            btnStart.addEventListener('click', function () {
                startRecording().catch(function (e) { setStatus(e.message, 'err'); });
            });
        }
        var btnStop = $('msBtnStopRecord');
        if (btnStop) {
            btnStop.addEventListener('click', function () {
                stopRecording().catch(function (e) { setStatus(e.message, 'err'); });
            });
        }
        var btnPair = $('msBtnDevicePair');
        if (btnPair) {
            btnPair.addEventListener('click', function () {
                initDevicePair().catch(function (e) { setStatus(e.message, 'err'); });
            });
        }
        var btnCopyPair = $('msBtnCopyPairCode');
        if (btnCopyPair) {
            btnCopyPair.addEventListener('click', function () {
                if (!state.pairCode) return;
                var text = state.pairCode;
                if (navigator.clipboard && navigator.clipboard.writeText) {
                    navigator.clipboard.writeText(text).then(function () {
                        setStatus('配对码已复制', 'ok');
                    }).catch(function () { setStatus(text, 'ok'); });
                } else {
                    setStatus('配对码：' + text, 'ok');
                }
            });
        }
        var btnReplayFrom = $('msBtnReplayFrom');
        if (btnReplayFrom) {
            btnReplayFrom.addEventListener('click', function () {
                if (state.selectedLiveIndex < 0) {
                    setStatus('请先选择步骤', 'warn');
                    return;
                }
                apiJson('/api/mobile/replay-actions', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        udid: state.udid,
                        from_index: state.selectedLiveIndex,
                        actions: state.liveSteps.map(function (it) {
                            var s = it.step || {};
                            if (s.action === 'swipe') {
                                var ms = s.mobile_spec || {};
                                return { type: 'swipe', x1: ms.x1, y1: ms.y1, x2: ms.x2, y2: ms.y2 };
                            }
                            var vc = (s.mobile_spec && s.mobile_spec.viewport_coord) || {};
                            return { type: 'tap', x: vc.x || 0, y: vc.y || 0 };
                        }),
                    }),
                }).then(function () { setStatus('从此步重放已提交', 'ok'); })
                    .catch(function (e) { setStatus(e.message, 'err'); });
            });
        }
        var btnAppium = $('msBtnStartAppium');
        if (btnAppium) {
            btnAppium.addEventListener('click', function () {
                startAppium().catch(function (e) { setStatus(e.message, 'err'); });
            });
        }
        ['msModeRecord', 'msModeCapture'].forEach(function (id) {
            var btn = $(id);
            if (btn) {
                btn.addEventListener('click', function () {
                    setInteractionMode(btn.getAttribute('data-mode'));
                });
            }
        });
        var btnReplaySession = $('msBtnReplaySession');
        if (btnReplaySession) {
            btnReplaySession.addEventListener('click', function () {
                replayRecordingSession().catch(function (e) { setStatus(e.message, 'err'); });
            });
        }
        var btnClearSession = $('msBtnClearSession');
        if (btnClearSession) {
            btnClearSession.addEventListener('click', clearRecordingSession);
        }
        var tapChk = $('msAlsoTapVisible');
        if (tapChk) {
            tapChk.addEventListener('change', function () {
                state.alsoTapOnRecord = !!tapChk.checked;
            });
            state.alsoTapOnRecord = !!tapChk.checked;
        }
        renderRecordingTimeline();
        setInteractionMode(INTERACTION_OPERATE);
        /* mirror 画布交互已移除 */
    }

    async function selectCaseById(caseId) {
        if (!caseId) return;
        var data = await apiJson('/api/cases/' + caseId);
        var tc = data.test_case;
        if (!tc) return;
        state.projectId = tc.project_id;
        var projSel = $('msProjectSelect');
        if (projSel) projSel.value = String(tc.project_id);
        await loadCases();
        state.caseId = caseId;
        var caseSel = $('msCaseSelect');
        if (caseSel) caseSel.value = String(caseId);
        await loadSteps();
    }

    async function init() {
        var cfgEl = document.getElementById('__mobileStudioDisabledJson');
        if (cfgEl) return;
        wireUi();
        var urlHint = $('msSyncUrlHint');
        if (urlHint) urlHint.textContent = window.location.origin;
        await loadProjects();
        await bootstrap();
        initDevicePair().catch(function () { /* 配对码可选 */ });
        var params = new URLSearchParams(window.location.search);
        var urlCaseId = parseInt(params.get('case_id') || '', 10);
        if (urlCaseId > 0) await selectCaseById(urlCaseId);
        if (state.bootstrap && state.bootstrap.auto_connect_default) {
            var devs = getRealDeviceList();
            if (devs.some(function (d) { return d.state === 'device'; })) {
                connectDevice({ tryAppium: false }).catch(function () {});
            }
        }
    }

    global.MobileTesting = {
        init: init,
        state: state,
        loadSteps: loadSteps,
        setStatus: setStatus,
        getUdid: function () { return state.udid || ''; },
        refreshMirror: function () {
            if (state.connected) startMirror();
        },
    };
    global.MobileStudio = global.MobileTesting;
    document.addEventListener('DOMContentLoaded', function () {
        init().catch(function (e) {
            setStatus('初始化失败: ' + (e.message || e), 'err');
        });
    });
})(window);
