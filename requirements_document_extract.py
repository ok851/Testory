"""
Extract plain text from requirement uploads (UTF-8 text, PDF, Word).
"""
from __future__ import annotations

from typing import List, Tuple


def extract_text_from_bytes(filename: str, raw: bytes) -> Tuple[str, List[str]]:
    """
    Returns (text, warnings). On failure returns ("", [error]).
    """
    warns: List[str] = []
    fn = (filename or "").lower().strip()
    raw = raw or b""

    if fn.endswith(".pdf"):
        try:
            from pypdf import PdfReader  # type: ignore
        except ImportError:
            return "", ["未安装 pypdf，无法读取 PDF（请 pip install pypdf）"]
        try:
            import io

            reader = PdfReader(io.BytesIO(raw))
            parts: List[str] = []
            for page in reader.pages:
                try:
                    t = page.extract_text() or ""
                except Exception:
                    t = ""
                if t.strip():
                    parts.append(t)
            text = "\n\n".join(parts).strip()
            if not text:
                warns.append("PDF 未提取到可见文本（可能为扫描件，请粘贴正文）")
            return text, warns
        except Exception as e:
            return "", [f"PDF 解析失败: {e}"]

    if fn.endswith(".docx"):
        try:
            import io

            from docx import Document  # type: ignore
        except ImportError:
            return "", ["未安装 python-docx，无法读取 Word（请 pip install python-docx）"]
        try:
            doc = Document(io.BytesIO(raw))
            paras = [p.text for p in doc.paragraphs if p.text and p.text.strip()]
            text = "\n".join(paras).strip()
            return text, warns
        except Exception as e:
            return "", [f"Word 解析失败: {e}"]

    # .md .txt .json or unknown — treat as utf-8 text
    try:
        text = raw.decode("utf-8", errors="replace").strip()
    except Exception as e:
        return "", [f"文本解码失败: {e}"]
    if not fn.endswith((".txt", ".md", ".markdown", ".json", ".yaml", ".yml")) and fn:
        warns.append("按 UTF-8 文本解析；若为二进制格式请改用 .pdf / .docx 或导出为 .txt")
    return text, warns
