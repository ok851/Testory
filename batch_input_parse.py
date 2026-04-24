"""多行「选择器 + 值」解析：用于 batch_input 步骤（Tab 或首逗号分隔，# 为注释）。"""

from __future__ import annotations

import re
from typing import List, Tuple


def parse_batch_input_lines(text: str) -> List[Tuple[str, str]]:
    """
    每行一条：选择器 与 填充文本 用 Tab 或行内首个英文/中文逗号分隔。
    空行、仅空白、以 # 开头（去空白后）的行忽略。
    无 Tab 且未找到逗号时整行忽略。
    """
    if not text or not str(text).strip():
        return []
    out: List[Tuple[str, str]] = []
    for raw in str(text).splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "\t" in line:
            a, b = line.split("\t", 1)
        else:
            m = re.search(r"[,，]", line)
            if not m:
                continue
            a, b = line[: m.start()], line[m.end() :]
        sel, val = a.strip(), b.strip()
        if sel:
            out.append((sel, val))
    return out
