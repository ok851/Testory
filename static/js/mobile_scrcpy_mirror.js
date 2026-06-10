/**
 * scrcpy H.264 WebSocket → WebCodecs → Canvas（模拟器高帧率投屏）
 * scrcpy-server 输出 Annex-B H.264，需转为 AVCC 并正确标记 key/delta 帧。
 */
(function (global) {
    'use strict';

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

    function isKeyAccessUnit(nals) {
        var i;
        for (i = 0; i < nals.length; i++) {
            var t = nalUnitType(nals[i]);
            if (t === 5) return true;
        }
        return false;
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
        this._noFrameTimeoutMs = typeof options.noFrameTimeoutMs === 'number' ? options.noFrameTimeoutMs : 10000;
    }

    ScrcpyMirrorPlayer.prototype.stop = function () {
        this._running = false;
        this._reconnectAttempt = 0;
        if (this._httpAbort) {
            try { this._httpAbort.abort(); } catch (e) { /* ignore */ }
            this._httpAbort = null;
        }
        this._stash = new Uint8Array(0);
        if (this._noFrameTimer) {
            clearTimeout(this._noFrameTimer);
            this._noFrameTimer = null;
        }
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
    };

    ScrcpyMirrorPlayer.prototype._resetDecoder = function () {
        if (this._decoder) {
            try { this._decoder.close(); } catch (e) { /* ignore */ }
        }
        this._decoder = null;
        this._configured = false;
        this._sps = null;
        this._pps = null;
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
                if (self._noFrameTimer) {
                    clearTimeout(self._noFrameTimer);
                    self._noFrameTimer = null;
                }
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
                self.onError(e.message || String(e));
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

    ScrcpyMirrorPlayer.prototype._handlePacket = function (data) {
        var packet;
        if (data instanceof Uint8Array) {
            packet = data;
        } else if (data instanceof ArrayBuffer) {
            packet = new Uint8Array(data);
        } else {
            return;
        }
        if (!packet.length) return;
        this._ensureDecoder();
        if (!this._decoder) {
            this.onError('当前浏览器不支持 WebCodecs');
            return;
        }

        var nals = splitAnnexBNals(packet);
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

        var avcc = annexBToAvcc(vcl);
        var chunk = new EncodedVideoChunk({
            type: isKeyAccessUnit(vcl) ? 'key' : 'delta',
            timestamp: performance.now() * 1000,
            data: avcc,
        });
        try {
            this._decoder.decode(chunk);
        } catch (e) {
            this.onError(e.message || String(e));
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
                if (self._noFrameTimer) clearTimeout(self._noFrameTimer);
                self._noFrameTimer = setTimeout(function () {
                    if (self._running && !self._gotFrame) {
                        self.onFallback('scrcpy 长时间无画面');
                    }
                }, self._noFrameTimeoutMs);
                done(null);
            };
            ws.onmessage = function (ev) {
                if (!self._running) return;
                if (typeof ev.data === 'string') return;
                self._handlePacket(ev.data);
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
        var merged = new Uint8Array(stash.length + incoming.length);
        merged.set(stash);
        merged.set(incoming, stash.length);
        var off = 0;
        while (off + 4 <= merged.length) {
            var len = (
                (merged[off] << 24) |
                (merged[off + 1] << 16) |
                (merged[off + 2] << 8) |
                merged[off + 3]
            ) >>> 0;
            if (!len || len > 10000000) {
                break;
            }
            if (off + 4 + len > merged.length) {
                break;
            }
            onPacket(merged.subarray(off + 4, off + 4 + len));
            off += 4 + len;
        }
        return merged.subarray(off);
    }

    ScrcpyMirrorPlayer.prototype.start = function () {
        this.stop();
        this._running = true;
        this._frameCount = 0;
        this._lastFpsAt = performance.now();
        this._reconnectAttempt = 0;
        return this._connectOnce();
    };

    ScrcpyMirrorPlayer.prototype.startHttp = function (streamUrl) {
        var self = this;
        this.stop();
        this._running = true;
        this._frameCount = 0;
        this._lastFpsAt = performance.now();
        this._httpAbort = new AbortController();
        this._stash = new Uint8Array(0);
        if (self._noFrameTimer) clearTimeout(self._noFrameTimer);
        self._noFrameTimer = setTimeout(function () {
            if (self._running && !self._gotFrame) {
                self.onFallback('scrcpy HTTP 流长时间无画面');
            }
        }, self._noFrameTimeoutMs);
        return fetch(streamUrl, {
            credentials: 'same-origin',
            signal: self._httpAbort.signal,
        }).then(function (resp) {
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
                        throw new Error('scrcpy HTTP 流已结束');
                    }
                    self._stash = feedLengthPrefixed(self._stash, chunk.value, function (packet) {
                        self._handlePacket(packet);
                    });
                    return pump();
                });
            }
            return pump();
        });
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

    global.ScrcpyMirrorPlayer = ScrcpyMirrorPlayer;
})(window);
