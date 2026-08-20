import pathlib

p = pathlib.Path(r"D:\mkst_baixiang\Python_Code\NewUITestPlatform\NewUITestPlatform\mobile_scrcpy_bridge.py")
lines = p.read_text(encoding="utf-8").split("\n")

# Fix lines 717-719 (0-indexed: 717, 718, 719)
# Current (broken):
# 717:         ]
# 718:                 if _version_major(version) >= 3:
# 719:             args.append(f"scid={scid}") if self.max_size > 0:

# Should be:
# 717:         ]
# 718:         if _version_major(version) >= 3:
# 719:             args.append(f"scid={scid}")
# 720:         if self.max_size > 0:

for i, line in enumerate(lines):
    if "if _version_major(version) >= 3:" in line and "args.append" in lines[i+1]:
        # Fix the indentation and split the merged line
        lines[i] = "        if _version_major(version) >= 3:"
        # Check if next line has merged content
        next_line = lines[i+1]
        if "if self.max_size" in next_line:
            # Split merged line
            lines[i+1] = '            args.append(f"scid={scid}")'
            lines.insert(i+2, "        if self.max_size > 0:")
            # Remove the old merged part
            # Actually need to check what's after
            print(f"Fixed at lines {i+1}-{i+3}")
        else:
            lines[i+1] = '            args.append(f"scid={scid}")'
            print(f"Fixed at lines {i+1}-{i+2}")
        break

p.write_text("\n".join(lines), encoding="utf-8")
print("OK")
