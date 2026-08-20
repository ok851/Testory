import pathlib

p = pathlib.Path(r"D:\mkst_baixiang\Python_Code\NewUITestPlatform\NewUITestPlatform\templates\mobile_testing.html")
text = p.read_text(encoding="utf-8")

# Find the udid check block and add loading state after it
old_marker = "if(backend!=='scrcpy_ws' || (!wsUrl && !streamUrl)){"
# We need to insert loading state BEFORE the backend check, AFTER the udid check's return
search = "                return;\n            }\n            if(backend!=='scrcpy_ws' || (!wsUrl && !streamUrl)){"

replace = """                return;
            }
            // device connected but mirror not ready yet — show loading
            canvas.style.display='none';
            placeholder.style.display='flex';
            placeholder.innerHTML='<i class="fas fa-spinner fa-spin fa-2x mb-3"></i><p style="font-size:14px;">正在初始化投屏...</p>';
            if(modeBadge){ modeBadge.textContent='初始化中'; modeBadge.className='ms-mirror-mode ms-mirror-mode--idle'; }
            if(backend!=='scrcpy_ws' || (!wsUrl && !streamUrl)){"""

if search in text:
    text = text.replace(search, replace, 1)
    p.write_text(text, encoding="utf-8")
    print("OK: added loading state")
else:
    # Try with \r\n
    search_crlf = search.replace("\n", "\r\n")
    if search_crlf in text:
        replace_crlf = replace.replace("\n", "\r\n")
        text = text.replace(search_crlf, replace_crlf, 1)
        p.write_text(text, encoding="utf-8")
        print("OK (CRLF): added loading state")
    else:
        # Debug
        idx = text.find("return;")
        count = 0
        while idx >= 0 and count < 10:
            ctx = text[max(0,idx-20):idx+100]
            if "udid" in ctx or "backend" in ctx:
                print(f"Found at {idx}: {repr(ctx[:80])}")
            idx = text.find("return;", idx + 1)
            count += 1
        print("---")
        idx2 = text.find("if(backend!==")
        if idx2 >= 0:
            print(f"backend check at {idx2}: {repr(text[idx2-100:idx2+60])}")
