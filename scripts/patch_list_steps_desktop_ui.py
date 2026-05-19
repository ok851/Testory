# -*- coding: utf-8 -*-
from pathlib import Path

p = Path(__file__).resolve().parent.parent / "templates" / "list_steps.html"
t = p.read_text(encoding="utf-8")

CREATE_OLD = """            <div class="form-group" id="stepDesktopEnvHint" style="display:none;">
                <small id="stepDesktopEnvHintText" style="color:#0369a1;display:block;font-size:12px;line-height:1.5;"></small>
                <button type="button" class="btn btn-sm btn-secondary" style="margin-top:8px;" onclick="desktopPickWindow('create')">从当前打开的窗口选择…</button>
            </motion>
            <input type="hidden" id="stepDesktopSpec" value="">""".replace(
    "</motion>", "</div>"
)

CREATE_NEW = """            <div class="form-group" id="stepDesktopSpecGroup" style="display:none;">
                <input type="hidden" id="stepDesktopSpec" value="">
                <label style="font-size:13px;color:#374151;">Windows 桌面</label>
                <p id="stepDesktopWindowSummary" style="font-size:12px;color:#64748b;margin:6px 0 10px;">窗口：未选择（请先打开被测程序，再点下方按钮）</p>
                <div style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:6px;">
                    <button type="button" class="btn btn-sm btn-secondary" onclick="desktopPickWindow('create')">① 选择当前窗口</button>
                    <button type="button" class="btn btn-sm btn-primary" id="stepDesktopPickControlBtn" onclick="desktopPickControl('create')">② 拾取桌面控件</button>
                </div>
                <small style="color:#666;font-size:12px;line-height:1.5;">无需 JSON / .env；「启动应用」可填 notepad.exe 或留空自动打开记事本。</small>
            </div>"""

EDIT_OLD = """            <div class="form-group" id="editStepDesktopEnvHint" style="display:none;">
                <small id="editStepDesktopEnvHintText" style="color:#0369a1;display:block;font-size:12px;line-height:1.5;"></small>
                <button type="button" class="btn btn-sm btn-secondary" style="margin-top:8px;" onclick="desktopPickWindow('edit')">从当前打开的窗口选择…</button>
            </div>
            <details class="form-group" id="editStepDesktopSpecGroup" style="display:none;">
                <summary style="cursor:pointer;color:#555;font-size:13px;">高级：desktop_spec JSON（一般无需填写）</summary>
                <textarea id="editStepDesktopSpec" rows="3" style="width:100%;box-sizing:border-box;font-family:monospace;font-size:12px;margin-top:8px;"></textarea>
                <button type="button" class="btn btn-sm btn-info" style="margin-top:6px;" onclick="desktopInspectFromForm('edit')">探测当前窗口控件树</button>
            </details>"""

EDIT_NEW = """            <div class="form-group" id="editStepDesktopSpecGroup" style="display:none;">
                <input type="hidden" id="editStepDesktopSpec" value="">
                <label style="font-size:13px;color:#374151;">Windows 桌面</label>
                <p id="editDesktopWindowSummary" style="font-size:12px;color:#64748b;margin:6px 0 10px;">窗口：未选择</p>
                <div style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:6px;">
                    <button type="button" class="btn btn-sm btn-secondary" onclick="desktopPickWindow('edit')">① 选择当前窗口</button>
                    <button type="button" class="btn btn-sm btn-primary" id="editDesktopPickControlBtn" onclick="desktopPickControl('edit')">② 拾取桌面控件</button>
                </div>
                <small style="color:#666;font-size:12px;line-height:1.5;">无需 JSON / .env</small>
            </div>"""

# fix CREATE_OLD - I accidentally used motion in replace
CREATE_OLD = """            <div class="form-group" id="stepDesktopEnvHint" style="display:none;">
                <small id="stepDesktopEnvHintText" style="color:#0369a1;display:block;font-size:12px;line-height:1.5;"></small>
                <button type="button" class="btn btn-sm btn-secondary" style="margin-top:8px;" onclick="desktopPickWindow('create')">从当前打开的窗口选择…</button>
            </div>
            <input type="hidden" id="stepDesktopSpec" value="">"""

if CREATE_OLD not in t:
    raise SystemExit("create block not found")
if EDIT_OLD not in t:
    raise SystemExit("edit block not found")
t = t.replace(CREATE_OLD, CREATE_NEW, 1).replace(EDIT_OLD, EDIT_NEW, 1)
p.write_text(t, encoding="utf-8")
print("ok")
