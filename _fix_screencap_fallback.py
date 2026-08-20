import pathlib

p = pathlib.Path(r"D:\mkst_baixiang\Python_Code\NewUITestPlatform\NewUITestPlatform\templates\mobile_testing.html")
text = p.read_text(encoding="utf-8")

# Replace the player creation and start section with fallback logic
old_player = """            if(typeof ScrcpyMirrorPlayer==='undefined') return;
            var player=new ScrcpyMirrorPlayer({canvas:canvas, wsUrl:wsUrl||'', streamUrl:streamUrl}); _mirrorPlayer=player;
            player.start().catch(function(err){ canvas.style.display='none'; placeholder.style.display='flex'; placeholder.innerHTML='<p style="color:#dc2626;">'+(err&&err.message?escHtml(err.message):'scrcpy 投屏启动失败')+'</p>'; if(modeBadge){modeBadge.textContent='投屏异常';modeBadge.className='ms-mirror-mode ms-mirror-mode--idle';} });"""

new_player = """            // Try scrcpy WebSocket + WebCodecs first, fall back to screencap
            var screencapUrl='/api/mobile/mirror/screencap?udid='+encodeURIComponent(udid);
            function startScreencapFallback(){
                if(modeBadge){ modeBadge.textContent='screencap 投屏'; modeBadge.className='ms-mirror-mode ms-mirror-mode--connected'; }
                var img=new Image(); img.crossOrigin='anonymous';
                var capInterval=setInterval(function(){
                    if(!_mirrorPlayer||!_mirrorPlayer._screencap){clearInterval(capInterval);return;}
                    var ts=new Date().getTime();
                    img.onload=function(){
                        if(!canvas||!_mirrorPlayer||!_mirrorPlayer._screencap){return;}
                        canvas.width=img.naturalWidth; canvas.height=img.naturalHeight;
                        var ctx=canvas.getContext('2d'); if(ctx)ctx.drawImage(img,0,0);
                    };
                    img.onerror=function(){};
                    img.src=screencapUrl+'&_t='+ts;
                },500);
                _mirrorPlayer={_screencap:true,stop:function(){this._screencap=false;}};
            }
            if(typeof ScrcpyMirrorPlayer!=='undefined' && ScrcpyMirrorPlayer.webCodecsSupported && ScrcpyMirrorPlayer.webCodecsSupported()){
                var player=new ScrcpyMirrorPlayer({canvas:canvas, wsUrl:wsUrl||'', streamUrl:streamUrl}); _mirrorPlayer=player;
                player.start().catch(function(err){
                    // WebSocket failed, fall back to screencap
                    startScreencapFallback();
                });
            } else {
                // WebCodecs not available, use screencap directly
                startScreencapFallback();
            }"""

if old_player in text:
    text = text.replace(old_player, new_player, 1)
    p.write_text(text, encoding="utf-8")
    print("OK: added screencap fallback")
else:
    old_c = old_player.replace("\n", "\r\n")
    if old_c in text:
        new_c = new_player.replace("\n", "\r\n")
        text = text.replace(old_c, new_c, 1)
        p.write_text(text, encoding="utf-8")
        print("OK (CRLF): added screencap fallback")
    else:
        print("NOT FOUND - debug:")
        idx = text.find("ScrcpyMirrorPlayer")
        if idx >= 0:
            print(repr(text[idx:idx+100]))
