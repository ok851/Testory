import os

filepath = r"D:\mkst_baixiang\Python_Code\NewUITestPlatform\NewUITestPlatform\static\js\mobile_studio.js"
with open(filepath, "r", encoding="utf-8") as f:
    content = f.read()

patches = 0

# === FIX 1: restore wireMirrorInteraction() call ===
old_comment = "        /* mirror 画布交互已移除 */"
new_call = "        wireMirrorInteraction();"
if old_comment in content:
    content = content.replace(old_comment, new_call)
    patches += 1
    print("FIX 1: wireMirrorInteraction restored")
else:
    print("FIX 1: wireMirrorInteraction already present or not found")

# === FIX 2: Update updateMirrorModeBadge to remove scrcpy wording ===
old_badge = """    function updateMirrorModeBadge(mode, detail) {
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
    }"""

new_badge = """    function updateMirrorModeBadge(mode, detail) {
        // [投屏已下线] 仅显示截图模式或未连接
        var badge = $('msMirrorMode');
        if (!badge) return;
        var m = mode || 'idle';
        if (m === 'scrcpy' || m === 'connecting') m = 'screencap';
        badge.className = 'ms-mirror-mode ms-mirror-mode--' + m;
        if (m === 'screencap') {
            badge.textContent = '截图投屏';
            badge.title = detail || '高帧率投屏已下线，使用截图模式';
        } else {
            badge.textContent = '未连接';
            badge.title = detail || '连接设备后此处显示投屏方式';
        }
    }"""

if old_badge in content:
    content = content.replace(old_badge, new_badge)
    patches += 1
    print("FIX 2: updateMirrorModeBadge simplified")
else:
    print("FIX 2: updateMirrorModeBadge not found (already patched?)")

# === FIX 3: Always return screencap backend in startMirror ===
old_start_mirror = """    function startMirror() {
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
    }"""

new_start_mirror = """    function startMirror() {
        // [投屏已下线] 统一使用截图投屏
        stopMirror();
        if (!state.mirrorUrl) return;
        startScreencapMirror();
    }"""

if old_start_mirror in content:
    content = content.replace(old_start_mirror, new_start_mirror)
    patches += 1
    print("FIX 3: startMirror simplified")
else:
    print("FIX 3: startMirror not found (checking...)")
    # Might have been modified by earlier patch
    idx = content.find("function startMirror()")
    if idx > 0:
        snippet = content[idx:idx+200]
        print(f"  Found at {idx}: {snippet[:150]}")

# === FIX 4: Remove ScrcpyMirrorPlayer usage from operateTapAt ===
old_tap = """    async function operateTapAt(clientX, clientY) {
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
    }"""

new_tap = """    async function operateTapAt(clientX, clientY) {
        // [投屏已下线] 直接走 API tap
        if (!state.connected || !state.udid) return;
        var pt = mapCanvasToDevice(clientX, clientY);
        await apiJson('/api/mobile/tap-at', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ x: pt.x, y: pt.y, udid: state.udid }),
        });
    }"""

if old_tap in content:
    content = content.replace(old_tap, new_tap)
    patches += 1
    print("FIX 4: operateTapAt simplified")
else:
    print("FIX 4: operateTapAt not found")

with open(filepath, "w", encoding="utf-8") as f:
    f.write(content)
print(f"\n{patches}/4 patches applied")
