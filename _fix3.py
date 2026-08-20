import pathlib, re

p = pathlib.Path(r"D:\mkst_baixiang\Python_Code\NewUITestPlatform\NewUITestPlatform\mobile_scrcpy_bridge.py")
text = p.read_text(encoding="utf-8")

# Find the function and replace it entirely using line-based approach
lines = text.split("\n")
start_idx = None
end_idx = None
for i, line in enumerate(lines):
    if "def _version_candidates()" in line:
        start_idx = i
    elif start_idx is not None and line and not line.startswith(" ") and not line.startswith("\t"):
        end_idx = i
        break

if start_idx is not None:
    if end_idx is None:
        end_idx = len(lines)
    
    new_func = [
        'def _version_candidates() -> list[str]:',
        '    """返回版本候选列表。只返回检测到的真实版本，避免版本不匹配导致 server 崩溃。"""',
        '    primary = _scrcpy_server_version()',
        '    return [primary] if primary else ["2.4"]',
        '',
    ]
    
    lines[start_idx:end_idx] = new_func
    text = "\n".join(lines)
    p.write_text(text, encoding="utf-8")
    print(f"OK: replaced _version_candidates (lines {start_idx+1}-{end_idx})")
else:
    print("NOT FOUND")
