/**
 * scrcpy H.264 WebSocket → WebCodecs → Canvas（高帧率投屏）
 * scrcpy-server 输出 Annex-B H.264，需转为 AVCC 并正确标记 key/delta 帧。
 * 桌面壳（Tauri / pywebview + WebView2）与系统浏览器均通过内嵌 Chromium 解码，非独立 scrcpy 窗口。
 */
(function (global) {
    'use strict';

    function getMirrorClientEnv() {
        if (global.__TAURI__ || global.__TAURI_INTERNALS__) {
            return 'tauri';
        }
        if (document.body && document.body.classList.contains('testory-desktop-client')) {
            return 'desktop';
        }
        return 'browser';
    }

    function webCodecsSupported() {
        try {
            return (
                typeof VideoDecoder !== 'undefined' &&
                typeof EncodedVideoChunk !== 'undefined' &&
                typeof VideoFrame !== 'undefined'
            );
        } catch (e) {
            return false;
        }
    }

    var _h264SupportCache = null;
    var H264_CODEC_CANDIDATES = ['avc1.42E01E', 'avc1.4D401E', 'avc1.640028', 'avc1.64001E'];

    function h264WebCodecsSupported() {
        if (!webCodecsSupported()) {
            return Promise.resolve(false);
        }
        if (_h264SupportCache !== null) {
            return Promise.resolve(_h264SupportCache);
        }
        if (typeof VideoDecoder === 'undefined' || typeof VideoDecoder.isConfigSupported !== 'function') {
            _h264SupportCache = true;
            return Promise.resolve(true);
        }
        var idx = 0;
        function tryNext() {
            if (idx >= H264_CODEC_CANDIDATES.length) {
                /* 部分浏览器 isConfigSupported 误报；仍尝试 scrcpy，由解码超时再降级 */
                _h264SupportCache = true;
                return Promise.resolve(true);
            }
            var codec = H264_CODEC_CANDIDATES[idx++];
            return VideoDecoder.isConfigSupported({ codec: codec, optimizeForLatency: true })
                .then(function (result) {
                    if (result && result.supported) {
                        _h264SupportCache = true;
                        return true;
                    }
                    return tryNext();
                })
                .catch(function () {
                    return tryNext();
                });
        }
        return tryNext();
    }

    function h264WebCodecsStrictlyUnsupported() {
        return h264WebCodecsSupported().then(function (ok) {
            return !ok;
        });
    }

    function getH264UnavailableMessage() {
        if (!webCodecsSupported()) {
            return getWebCodecsUnavailableMessage();
        }
        var env = getMirrorClientEnv();
        var origin = '';
        try {
            origin = global.location && global.location.origin ? global.location.origin : '';
        } catch (e) { /* ignore */ }
        if (env === 'desktop' || env === 'tauri') {
            return (
                '当前内嵌 WebView 不支持 H.264 硬件解码（WebCodecs avc1），与 scrcpy 插件安装无关。' +
                '请升级 Microsoft Edge WebView2 运行库至最新版' +
                (origin ? '，或在 Chrome/Edge 中打开 ' + origin + '/mobile-testing' : '')
            );
        }
        return '当前浏览器不支持 H.264 硬件解码（WebCodecs avc1），请使用 Chrome / Edge 94+ 打开本平台';
    }

    function getWebCodecsUnavailableMessage() {
        var env = getMirrorClientEnv();
        var origin = '';
        try {
            origin = global.location && global.location.origin ? global.location.origin : '';
        } catch (e) { /* ignore */ }
        if (env === 'desktop' || env === 'tauri') {
            return (
                '当前桌面软件窗口的内嵌 WebView 不支持 H.264 硬件解码（WebCodecs），与 scrcpy 插件是否安装无关。' +
                '请升级 Microsoft Edge WebView2 运行库至最新版' +
                (origin ? '，或在 Chrome/Edge 中打开 ' + origin + '/mobile-testing 使用高帧投屏' : '')
            );
        }
        return '当前显示环境不支持 H.264 硬件解码（WebCodecs），请使用 Chrome / Edge 94 及以上版本打开本平台';
    }

    function isWebCodecsRelatedError(msg) {
        return /WebCodecs|VideoDecoder|硬件解码|ReadableStream|avc1|H\.264/i.test(msg || '');
    }

    function byteToHex(b) {
        return ('0' + (b & 0xff).toString(16).toUpperCase()).slice(-2);
    }

    function splitAnnexBNals(data) {
        var nals = [];
        var i = 0;
        var len = data.length;

        function startCodeLen(pos) {
            if (pos + 3 >= len) return 0;
            if (data[pos] === 0 && data[pos + 1] === 0) {
                if (data[pos + 2] === 1) return 3;
                if (pos + 3 < len && data[pos + 2] === 0 && data[pos + 3] === 1) return 4;
            }
            return 0;
        }

        function findStart(pos) {
            while (pos < len - 2) {
                if (startCodeLen(pos)) return pos;
                pos += 1;
            }
            return -1;
        }

        var start = findStart(0);
        if (start < 0 && len > 0) {
            nals.push(data);
            return nals;
        }
        while (start >= 0 && start < len) {
            var sc = startCodeLen(start);
            var nalStart = start + sc;
            var next = findStart(nalStart);
            var nalEnd = next >= 0 ? next : len;
            if (nalEnd > nalStart) {
                nals.push(data.subarray(nalStart, nalEnd));
            }
            start = next;
        }
        return nals;
    }

    function nalUnitType(nal) {
        return nal.length ? (nal[0] & 0x1f) : 0;
    }

    function annexBToAvcc(nals) {
        var total = 0;
        var i;
        for (i = 0; i < nals.length; i++) {
            total += 4 + nals[i].length;
        }
        var out = new Uint8Array(total);
        var off = 0;
        for (i = 0; i < nals.length; i++) {
            var n = nals[i];
            out[off++] = (n.length >>> 24) & 0xff;
            out[off++] = (n.length >>> 16) & 0xff;
            out[off++] = (n.length >>> 8) & 0xff;
            out[off++] = n.length & 0xff;
            out.set(n, off);
            off += n.length;
        }
        return out;
    }

    function buildAvcC(sps, pps) {
        var profile = sps[1];
        var compat = sps[2];
        var level = sps[3];
        var avcc = new Uint8Array(11 + sps.length + pps.length);
        var p = 0;
        avcc[p++] = 1;
        avcc[p++] = profile;
        avcc[p++] = compat;
        avcc[p++] = level;
        avcc[p++] = 0xfc | 3;
        avcc[p++] = 0xe1;
        avcc[p++] = (sps.length >>> 8) & 0xff;
        avcc[p++] = sps.length & 0xff;
        avcc.set(sps, p);
        p += sps.length;
        avcc[p++] = 1;
        avcc[p++] = (pps.length >>> 8) & 0xff;
        avcc[p++] = pps.length & 0xff;
        avcc.set(pps, p);
        return {
            avcc: avcc,
            codec: 'avc1.' + byteToHex(profile) + byteToHex(compat) + byteToHex(level),
        };
    }

    function splitAvccNals(data) {
        var nals = [];
        var off = 0;
        var len = data.length;
        while (off + 4 <= len) {
            var nalLen = (
                (data[off] << 24) |
                (data[off + 1] << 16) |
                (data[off + 2] << 8) |
                data[off + 3]
            ) >>> 0;
            off += 4;
            if (!nalLen || off + nalLen > len) {
                break;
            }
            nals.push(data.subarray(off, off + nalLen));
            off += nalLen;
        }
        return nals;
    }

    function extractNals(packet) {
        if (!packet || !packet.length) return [];
        var annex = splitAnnexBNals(packet);
        var hasStartCode = false;
        var i;
        for (i = 0; i < Math.min(packet.length - 3, 64); i++) {
            if (packet[i] === 0 && packet[i + 1] === 0 && (packet[i + 2] === 1 || (packet[i + 2] === 0 && packet[i + 3] === 1))) {
                hasStartCode = true;
                break;
            }
        }
        if (hasStartCode && annex.length) {
            return annex;
        }
        var avcc = splitAvccNals(packet);
        if (avcc.length) {
            return avcc;
        }
        return annex;
    }

    function isKeyframeNals(nals) {
        var i;
        for (i = 0; i < nals.length; i++) {
            var t = nalUnitType(nals[i]);
            if (t === 5 || t === 1) return true;
        }
        return false;
    }

    function looksLikeAvccConfig(packet) {
        return packet.length >= 7 && packet[0] === 1 && (packet[4] & 0x03) === 3;
    }

    function parseAvccDecoderConfig(packet) {
        if (!looksLikeAvccConfig(packet)) return null;
        var off = 5;
        var numSps = packet[off] & 0x1f;
        off += 1;
        var sps = null;
        var pps = null;
        if (numSps > 0 && off + 2 <= packet.length) {
            var spsLen = (packet[off] << 8) | packet[off + 1];
            off += 2;
            if (spsLen > 0 && off + spsLen <= packet.length) {
                sps = packet.subarray(off, off + spsLen);
                off += spsLen;
            }
        }
        if (off < packet.length) {
            var numPps = packet[off];
            off += 1;
            if (numPps > 0 && off + 2 <= packet.length) {
                var ppsLen = (packet[off] << 8) | packet[off + 1];
                off += 2;
                if (ppsLen > 0 && off + ppsLen <= packet.length) {
                    pps = packet.subarray(off, off + ppsLen);
                }
            }
        }
        if (sps && pps) return { sps: sps, pps: pps };
        return null;
    }

    function tryExtractSpsPps(packet) {
        var avcc = parseAvccDecoderConfig(packet);
        if (avcc) return avcc;
        var nals = extractNals(packet);
        var sps = null;
        var pps = null;
        var i;
        for (i = 0; i < nals.length; i++) {
            var t = nalUnitType(nals[i]);
            if (t === 7) sps = nals[i];
            if (t === 8) pps = nals[i];
        }
        if (sps && pps) return { sps: sps, pps: pps };
        return null;
    }

    function feedFramed(stash, incoming, onFrame) {
        var merged = new Uint8Array(stash.length + incoming.length);
        merged.set(stash);
        merged.set(incoming, stash.length);
        var off = 0;
        while (off + 5 <= merged.length) {
            var meta = merged[off];
            if (meta > 2) {
                break;
            }
            var len = (
                (merged[off + 1] << 24) |
                (merged[off + 2] << 16) |
                (merged[off + 3] << 8) |
                merged[off + 4]
            ) >>> 0;
            if (!len || len > 10000000) {
                break;
            }
            if (off + 5 + len > merged.length) {
                break;
            }
            onFrame(meta, merged.subarray(off + 5, off + 5 + len));
            off += 5 + len;
        }
        while (off + 4 <= merged.length) {
            var len4 = (
                (merged[off] << 24) |
                (merged[off + 1] << 16) |
                (merged[off + 2] << 8) |
                merged[off + 3]
            ) >>> 0;
            if (!len4 || len4 > 10000000) {
                break;
            }
            if (off + 4 + len4 > merged.length) {
                break;
            }
            onFrame(0, merged.subarray(off + 4, off + 4 + len4));
            off += 4 + len4;
        }
        return merged.subarray(off);
    }

    function ScrcpyMirrorPlayer(options) {
        this.canvas = options.canvas;
        this.wsUrl = options.wsUrl;
        this.onFps = options.onFps || function () {};
        this.onError = options.onError || function () {};
        this.onReconnect = options.onReconnect || function () {};
        this.onFallback = options.onFallback || function () {};
        this.onFirstFrame = options.onFirstFrame || function () {};
        this._ws = null;
        this._decoder = null;
        this._configured = false;
        this._running = false;
        this._frameCount = 0;
        this._lastFpsAt = performance.now();
        this._ctx = this.canvas ? this.canvas.getContext('2d') : null;
        this._reconnectAttempt = 0;
        this._maxReconnect = typeof options.maxReconnect === 'number' ? options.maxReconnect : 5;
        this._gotFrame = false;
        this._notifiedFrame = false;
        this._sps = null;
        this._pps = null;
        this._codecStr = 'avc1.42E01E';
        this._noFrameTimer = null;
        this._noFrameTimeoutMs = typeof options.noFrameTimeoutMs === 'number' ? options.noFrameTimeoutMs : 25000;
        this._decodeTimeoutMs = typeof options.decodeTimeoutMs === 'number' ? options.decodeTimeoutMs : 45000;
        this._needKeyframe = true;
        this._decodeErrors = 0;
        this._packetCount = 0;
        this._decodeTimerArmed = false;
        this._stash = new Uint8Array(0);
    }

    ScrcpyMirrorPlayer.prototype._clearNoFrameTimer = function () {
        if (this._noFrameTimer) {
            clearTimeout(this._noFrameTimer);
            this._noFrameTimer = null;
        }
    };

    ScrcpyMirrorPlayer.prototype._armConnectTimer = function () {
        var self = this;
        this._clearNoFrameTimer();
        if (!this._running || this._gotFrame || this._notifiedFrame || this._packetCount > 0) {
            return;
        }
        this._noFrameTimer = setTimeout(function () {
            if (self._running && !self._gotFrame && !self._notifiedFrame && self._packetCount === 0) {
                self.onFallback('scrcpy 长时间无数据');
            }
        }, this._noFrameTimeoutMs);
    };

    ScrcpyMirrorPlayer.prototype._armDecodeTimer = function () {
        var self = this;
        this._clearNoFrameTimer();
        if (!this._running || this._gotFrame || this._notifiedFrame) {
            return;
        }
        this._decodeTimerArmed = true;
        this._noFrameTimer = setTimeout(function () {
            if (self._running && !self._gotFrame && !self._notifiedFrame && self._packetCount > 0) {
                self._resetDecoder();
                self._decodeTimerArmed = false;
                self.onFallback('scrcpy 视频解码超时');
            }
        }, this._decodeTimeoutMs);
    };

    ScrcpyMirrorPlayer.prototype._onStreamActivity = function () {
        if (this._gotFrame) {
            this._clearNoFrameTimer();
            return;
        }
        if (this._packetCount === 1) {
            this._armDecodeTimer();
        }
    };

    ScrcpyMirrorPlayer.prototype.stop = function () {
        this._running = false;
        this._stoppingIntentionally = true;
        this._reconnectAttempt = 0;
        if (this._httpAbort) {
            try { this._httpAbort.abort(); } catch (e) { /* ignore */ }
            this._httpAbort = null;
        }
        this._stash = new Uint8Array(0);
        this._clearNoFrameTimer();
        if (this._ws) {
            try { this._ws.close(); } catch (e) { /* ignore */ }
            this._ws = null;
        }
        if (this._decoder) {
            try { this._decoder.close(); } catch (e) { /* ignore */ }
            this._decoder = null;
        }
        this._configured = false;
        this._gotFrame = false;
        this._notifiedFrame = false;
        this._sps = null;
        this._pps = null;
        this._needKeyframe = true;
        this._decodeErrors = 0;
        this._packetCount = 0;
        this._decodeTimerArmed = false;
        this._stash = new Uint8Array(0);
        this._stoppingIntentionally = false;
    };

    ScrcpyMirrorPlayer.prototype._resetDecoder = function () {
        if (this._decoder) {
            try { this._decoder.close(); } catch (e) { /* ignore */ }
        }
        this._decoder = null;
        this._configured = false;
        this._sps = null;
        this._pps = null;
        this._needKeyframe = true;
        this._decodeErrors = 0;
    };

    ScrcpyMirrorPlayer.prototype._ensureDecoder = function () {
        if (this._decoder || typeof VideoDecoder === 'undefined') return;
        var self = this;
        this._decoder = new VideoDecoder({
            output: function (frame) {
                if (!self._running || !self._ctx || !self.canvas) {
                    frame.close();
                    return;
                }
                self._gotFrame = true;
                if (!self._notifiedFrame) {
                    self._notifiedFrame = true;
                    self.onFirstFrame();
                }
                self._clearNoFrameTimer();
                if (self.canvas.width !== frame.displayWidth || self.canvas.height !== frame.displayHeight) {
                    self.canvas.width = frame.displayWidth;
                    self.canvas.height = frame.displayHeight;
                }
                self._ctx.drawImage(frame, 0, 0);
                frame.close();
                self._frameCount++;
                var now = performance.now();
                if (now - self._lastFpsAt >= 2000) {
                    var fps = Math.round(self._frameCount / ((now - self._lastFpsAt) / 1000));
                    self._frameCount = 0;
                    self._lastFpsAt = now;
                    self.onFps(fps);
                }
            },
            error: function (e) {
                self._decodeErrors += 1;
                if (self._decodeErrors >= 12) {
                    self.onError(e.message || String(e));
                }
            },
        });
    };

    ScrcpyMirrorPlayer.prototype._configureDecoder = function () {
        if (this._configured || !this._decoder || !this._sps || !this._pps) return;
        var cfg = buildAvcC(this._sps, this._pps);
        this._codecStr = cfg.codec;
        this._decoder.configure({
            codec: cfg.codec,
            description: cfg.avcc,
            optimizeForLatency: true,
        });
        this._configured = true;
    };

    ScrcpyMirrorPlayer.prototype._applyParameterSets = function (sps, pps) {
        if (!sps || !pps) return;
        this._sps = sps;
        this._pps = pps;
        this._ensureDecoder();
        if (!this._configured) {
            this._configureDecoder();
        }
    };

    ScrcpyMirrorPlayer.prototype._updateParameterSets = function (nals) {
        var i;
        var changed = false;
        for (i = 0; i < nals.length; i++) {
            var t = nalUnitType(nals[i]);
            if (t === 7) {
                this._sps = nals[i];
                changed = true;
            } else if (t === 8) {
                this._pps = nals[i];
                changed = true;
            }
        }
        if (changed && this._configured && this._sps && this._pps) {
            this._resetDecoder();
            this._ensureDecoder();
            this._configureDecoder();
        }
    };

    ScrcpyMirrorPlayer.prototype._handlePacket = function (data, meta) {
        meta = meta || 0;
        var packet;
        if (data instanceof Uint8Array) {
            packet = data;
        } else if (data instanceof ArrayBuffer) {
            packet = new Uint8Array(data);
        } else {
            return;
        }
        if (!packet.length) return;
        this._packetCount += 1;
        this._onStreamActivity();
        this._ensureDecoder();
        if (!this._decoder) {
            this.onError(getWebCodecsUnavailableMessage());
            return;
        }

        if (meta === 1) {
            var cfgOnly = tryExtractSpsPps(packet);
            if (cfgOnly) {
                this._applyParameterSets(cfgOnly.sps, cfgOnly.pps);
            }
            return;
        }

        var cfgInline = tryExtractSpsPps(packet);
        if (cfgInline && (!this._sps || !this._pps)) {
            this._applyParameterSets(cfgInline.sps, cfgInline.pps);
        }

        var nals = extractNals(packet);
        if (!nals.length) return;

        this._updateParameterSets(nals);
        if (!this._sps || !this._pps) return;

        if (!this._configured) {
            this._configureDecoder();
            if (!this._configured) return;
        }

        var vcl = [];
        var i;
        for (i = 0; i < nals.length; i++) {
            var t = nalUnitType(nals[i]);
            if (t !== 7 && t !== 8) {
                vcl.push(nals[i]);
            }
        }
        if (!vcl.length) return;

        var isKey = meta === 2 || isKeyframeNals(vcl);
        if (this._needKeyframe && !isKey && this._packetCount < 48) {
            return;
        }

        var avcc = annexBToAvcc(vcl);
        var chunk = new EncodedVideoChunk({
            type: isKey ? 'key' : 'delta',
            timestamp: performance.now() * 1000,
            data: avcc,
        });
        try {
            this._decoder.decode(chunk);
            if (isKey) {
                this._needKeyframe = false;
            }
        } catch (e) {
            this._decodeErrors += 1;
            if (this._decodeErrors >= 12) {
                this.onError(e.message || String(e));
            }
        }
    };

    ScrcpyMirrorPlayer.prototype._connectOnce = function () {
        var self = this;
        return new Promise(function (resolve, reject) {
            var ws = new WebSocket(self.wsUrl);
            ws.binaryType = 'arraybuffer';
            self._ws = ws;
            var settled = false;
            function done(err) {
                if (settled) return;
                settled = true;
                if (err) reject(err);
                else resolve();
            }
            ws.onopen = function () {
                self._reconnectAttempt = 0;
                self._armConnectTimer();
                done(null);
            };
            ws.onmessage = function (ev) {
                if (!self._running) return;
                if (typeof ev.data === 'string') return;
                var chunk = ev.data instanceof ArrayBuffer ? new Uint8Array(ev.data) : ev.data;
                self._stash = feedFramed(self._stash, chunk, function (m, pkt) {
                    self._handlePacket(pkt, m);
                });
            };
            ws.onerror = function () {
                done(new Error('scrcpy WebSocket 连接失败'));
            };
            ws.onclose = function () {
                if (!self._running) return;
                if (self._reconnectAttempt < self._maxReconnect) {
                    self._reconnectAttempt += 1;
                    self.onReconnect(self._reconnectAttempt, self._maxReconnect);
                    self._resetDecoder();
                    self._gotFrame = false;
                    self._notifiedFrame = false;
                    self._packetCount = 0;
                    setTimeout(function () {
                        self._connectOnce().then(resolve).catch(reject);
                    }, Math.min(4000, 800 * self._reconnectAttempt));
                    return;
                }
                self.onError('scrcpy 流已断开，请点「一键连接」重试');
                done(new Error('scrcpy 流已断开'));
            };
        });
    };

    function feedLengthPrefixed(stash, incoming, onPacket) {
        return feedFramed(stash, incoming, function (meta, packet) {
            onPacket(packet, meta);
        });
    }

    ScrcpyMirrorPlayer.prototype.start = function () {
        if (!webCodecsSupported()) {
            return Promise.reject(new Error(getWebCodecsUnavailableMessage()));
        }
        this.stop();
        this._running = true;
        this._frameCount = 0;
        this._lastFpsAt = performance.now();
        this._reconnectAttempt = 0;
        return this._connectOnce();
    };

    ScrcpyMirrorPlayer.prototype.startHttp = function (streamUrl) {
        var self = this;
        if (!webCodecsSupported()) {
            return Promise.reject(new Error(getWebCodecsUnavailableMessage()));
        }
        this.stop();
        this._running = true;
        this._stoppingIntentionally = false;
        this._frameCount = 0;
        this._lastFpsAt = performance.now();
        this._httpAbort = new AbortController();
        this._stash = new Uint8Array(0);
        this._packetCount = 0;
        this._decodeTimerArmed = false;
        this._reconnectAttempt = 0;
        this._maxHttpReconnect = 3;
        this._armConnectTimer();

        function httpConnectOnce() {
            self._httpAbort = new AbortController();
            self._armConnectTimer();
            return fetch(streamUrl, {
                credentials: 'same-origin',
                signal: self._httpAbort.signal,
            }).then(function (resp) {
                if (!self._running) return;
                if (!resp.ok) {
                    throw new Error('scrcpy HTTP ' + resp.status);
                }
                if (!resp.body || !resp.body.getReader) {
                    throw new Error('浏览器不支持 ReadableStream');
                }
                var reader = resp.body.getReader();
                function pump() {
                    if (!self._running) return Promise.resolve();
                    return reader.read().then(function (chunk) {
                        if (!self._running) return;
                        if (chunk.done) {
                            if (self._stoppingIntentionally) return;
                            throw new Error('scrcpy HTTP 流已结束');
                        }
                        self._stash = feedFramed(self._stash, chunk.value, function (meta, packet) {
                            self._handlePacket(packet, meta);
                        });
                        return pump();
                    });
                }
                return pump();
            }).catch(function (err) {
                if (!self._running || self._stoppingIntentionally) {
                    return;
                }
                if (err && err.name === 'AbortError') {
                    return;
                }
                var msg = err && err.message ? String(err.message) : String(err);
                if (/aborted/i.test(msg)) {
                    return;
                }
                // 流意外结束：自动重连（给后端 relay 会话重启留出时间）
                if (/流已结束/.test(msg) && self._reconnectAttempt < self._maxHttpReconnect) {
                    self._reconnectAttempt += 1;
                    if (self.onReconnect) {
                        self.onReconnect(self._reconnectAttempt, self._maxHttpReconnect);
                    }
                    self._resetDecoder();
                    self._gotFrame = false;
                    self._notifiedFrame = false;
                    self._packetCount = 0;
                    return new Promise(function (resolve, reject) {
                        setTimeout(function () {
                            httpConnectOnce().then(resolve, reject);
                        }, Math.min(4000, 1000 * self._reconnectAttempt));
                    });
                }
                throw err;
            });
        }
        return httpConnectOnce();
    };

    ScrcpyMirrorPlayer.prototype._sendControl = function (payload) {
        if (!this._ws || this._ws.readyState !== WebSocket.OPEN) return false;
        try {
            this._ws.send(JSON.stringify(payload));
            return true;
        } catch (e) {
            return false;
        }
    };

    ScrcpyMirrorPlayer.prototype.sendTap = function (x, y, screenW, screenH) {
        return this._sendControl({
            type: 'tap',
            x: x,
            y: y,
            screen_width: screenW,
            screen_height: screenH,
        });
    };

    ScrcpyMirrorPlayer.prototype.sendSwipe = function (x1, y1, x2, y2, screenW, screenH) {
        return this._sendControl({
            type: 'swipe',
            x1: x1,
            y1: y1,
            x2: x2,
            y2: y2,
            screen_width: screenW,
            screen_height: screenH,
        });
    };

    ScrcpyMirrorPlayer.webCodecsSupported = webCodecsSupported;
    ScrcpyMirrorPlayer.h264WebCodecsSupported = h264WebCodecsSupported;
    ScrcpyMirrorPlayer.h264WebCodecsStrictlyUnsupported = h264WebCodecsStrictlyUnsupported;
    ScrcpyMirrorPlayer.getMirrorClientEnv = getMirrorClientEnv;
    ScrcpyMirrorPlayer.getWebCodecsUnavailableMessage = getWebCodecsUnavailableMessage;
    ScrcpyMirrorPlayer.getH264UnavailableMessage = getH264UnavailableMessage;
    ScrcpyMirrorPlayer.isWebCodecsRelatedError = isWebCodecsRelatedError;

    global.ScrcpyMirrorPlayer = ScrcpyMirrorPlayer;
})(window);
