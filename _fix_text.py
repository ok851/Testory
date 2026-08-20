import pathlib

p = pathlib.Path(r"D:\mkst_baixiang\Python_Code\NewUITestPlatform\NewUITestPlatform\templates\mobile_testing.html")
text = p.read_text(encoding="utf-8")

# Remove the icon and text from placeholder
old = """                    <div id="msMirrorPlaceholder" class="ms-mirror-placeholder" style="display:flex;flex-direction:column;align-items:center;justify-content:center;min-width:280px;min-height:400px;background:transparent;border:none;border-radius:16px;color:#64748b;text-align:center;padding:2rem;">
                        <i class="fas fa-mobile-alt fa-3x mb-4 opacity-30"></i>
                        <p style="font-size:14px;margin-bottom:4px;">请先连接设备</p>
                        <p style="font-size:12px;opacity:0.6;">安装 scrcpy 插件后启用投屏</p>
                    </div>"""

new = """                    <div id="msMirrorPlaceholder" class="ms-mirror-placeholder" style="display:flex;flex-direction:column;align-items:center;justify-content:center;min-width:280px;min-height:400px;background:transparent;border:none;border-radius:16px;color:#64748b;text-align:center;padding:2rem;">
                    </div>"""

if old in text:
    text = text.replace(old, new, 1)
    p.write_text(text, encoding="utf-8")
    print("OK: removed placeholder text and icon")
else:
    old_c = old.replace("\n", "\r\n")
    if old_c in text:
        new_c = new.replace("\n", "\r\n")
        text = text.replace(old_c, new_c, 1)
        p.write_text(text, encoding="utf-8")
        print("OK (CRLF): removed placeholder text and icon")
    else:
        print("NOT FOUND")
