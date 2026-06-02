/**
 * scrcpy H.264 WebSocket → WebCodecs → Canvas（模拟器高帧率投屏）
 */
(function (global) {
    'use strict';

    function ScrcpyMirrorPlayer(options) {
        this.canvas = options.canvas;
        this.wsUrl = options.wsUrl;
        this.onFps = options.onFps || function () {};
        this.onError = options.onError || function () {};
        this._ws = null;
        this._decoder = null;
        this._configured = false;
        this._running = false;
        this._frameCount = 0;
        this._lastFpsAt = performance.now();
        this._ctx = this.canvas ? this.canvas.getContext('2d') : null;
    }

    ScrcpyMirrorPlayer.prototype.stop = function () {
        this._running = false;
        if (this._ws) {
            try { this._ws.close(); } catch (e) { /* ignore */ }
            this._ws = null;
        }
        if (this._decoder) {
            try { this._decoder.close(); } catch (e) { /* ignore */ }
            this._decoder = null;
        }
        this._configured = false;
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

    ScrcpyMirrorPlayer.prototype._configureDecoder = function (packet) {
        if (this._configured || !this._decoder) return;
        var desc = packet.slice(0, Math.min(packet.byteLength, 128));
        this._decoder.configure({
            codec: 'avc1.42E01E',
            description: desc,
            optimizeForLatency: true,
        });
        this._configured = true;
    };

    ScrcpyMirrorPlayer.prototype._handlePacket = function (data) {
        if (!(data instanceof ArrayBuffer)) return;
        var packet = new Uint8Array(data);
        if (!packet.length) return;
        this._ensureDecoder();
        if (!this._decoder) {
            this.onError('当前浏览器不支持 WebCodecs');
            return;
        }
        if (!this._configured) {
            this._configureDecoder(packet);
            return;
        }
        var chunk = new EncodedVideoChunk({
            type: 'delta',
            timestamp: performance.now() * 1000,
            data: packet,
        });
        try {
            this._decoder.decode(chunk);
        } catch (e) {
            this.onError(e.message || String(e));
        }
    };

    ScrcpyMirrorPlayer.prototype.start = function () {
        var self = this;
        this.stop();
        this._running = true;
        this._frameCount = 0;
        this._lastFpsAt = performance.now();
        var ws = new WebSocket(this.wsUrl);
        ws.binaryType = 'arraybuffer';
        this._ws = ws;
        ws.onmessage = function (ev) {
            if (!self._running) return;
            if (typeof ev.data === 'string') return;
            self._handlePacket(ev.data);
        };
        ws.onerror = function () {
            self.onError('scrcpy WebSocket 连接失败');
        };
        ws.onclose = function () {
            if (self._running) self.onError('scrcpy 流已断开');
        };
        return new Promise(function (resolve, reject) {
            ws.onopen = function () { resolve(); };
            ws.onerror = function () { reject(new Error('WebSocket 连接失败')); };
        });
    };

    global.ScrcpyMirrorPlayer = ScrcpyMirrorPlayer;
})(window);
