import pathlib

p = pathlib.Path(r"D:\mkst_baixiang\Python_Code\NewUITestPlatform\NewUITestPlatform\templates\mobile_testing.html")
text = p.read_text(encoding="utf-8")

# 1. Remove "请先连接设备以启用投屏" placeholder in initMirror (no device case)
text = text.replace(
    "placeholder.innerHTML='<i class=\"fas fa-mobile-alt fa-2x mb-3 opacity-40\"></i><p style=\"font-size:14px;\">请先连接设备以启用投屏</p>';",
    "placeholder.innerHTML='';"
)

# 2. Remove icon from "投屏未就绪" error state
text = text.replace(
    "placeholder.innerHTML='<i class=\"fas fa-mobile-alt fa-2x mb-3 opacity-40\"></i><p style=\"font-size:14px;color:#f59e0b;\">'",
    "placeholder.innerHTML='<p style=\"font-size:14px;color:#f59e0b;\">'"
)

# 3. Remove "安装 scrcpy 插件后自动启动投屏" from usage instructions
text = text.replace(
    "<li>安装 scrcpy 插件后自动启动投屏</li>",
    ""
)

# 4. Remove "安装 scrcpy 插件后启用投屏" (already removed from placeholder, but check)
text = text.replace(
    "安装 scrcpy 插件后启用投屏",
    ""
)

p.write_text(text, encoding="utf-8")
print("OK: cleaned up all scrcpy text references")
