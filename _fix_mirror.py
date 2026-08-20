import pathlib

p = pathlib.Path(r"D:\mkst_baixiang\Python_Code\NewUITestPlatform\NewUITestPlatform\templates\mobile_testing.html")
text = p.read_text(encoding="utf-8")

old_text = "            var backend=data.mirror_backend||'';\n            var wsUrl=data.bridge && data.bridge.ws_url ? data.bridge.ws_url : null;\n            var streamUrl=data.mirror_stream_url||'';\n            if(backend!=='scrcpy_ws' || (!wsUrl && !streamUrl)){"

new_text = """            var backend=data.mirror_backend||'';
            var wsUrl=data.bridge && data.bridge.ws_url ? data.bridge.ws_url : null;
            var streamUrl=data.mirror_stream_url||'';
            var udid=data.udid||'';
            if(!udid){
                canvas.style.display='none';
                placeholder.style.display='flex';
                placeholder.innerHTML='<i class="fas fa-mobile-alt fa-2x mb-3 opacity-40"></i><p style="font-size:14px;">请先连接设备以启用投屏</p>';
                if(modeBadge){ modeBadge.textContent='\u672a\u8fde\u63a5'; modeBadge.className='ms-mirror-mode ms-mirror-mode--idle'; }
                return;
            }
            if(backend!=='scrcpy_ws' || (!wsUrl && !streamUrl)){"""

if old_text in text:
    text = text.replace(old_text, new_text, 1)
    p.write_text(text, encoding="utf-8")
    print("OK: added udid check")
else:
    print("NOT FOUND")
