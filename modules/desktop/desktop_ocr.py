"""OCR 基础包装器：PaddleOCR → Tesseract（pytesseract 或系统 CLI）→ ddddocr。

提供两个核心 API：
- extract_text(screenshot_path) → str
- find_text_location(screenshot_path, keyword) → (left, top, right, bottom) or None
- extract_text_blocks(png_bytes) → 结构化块列表

优先加载 PaddleOCR（中文更准）；其次系统 Tesseract（无需 pytesseract 包）；
再次 ddddocr（项目验证码/定位常用，已装则可兜底）。
"""

from __future__ import annotations

import importlib
import os
import re
import shutil
import subprocess
import tempfile
from io import BytesIO
from typing import Any, Dict, List, Optional, Tuple


_ocr_engine: Optional[str] = None
_paddle_instance: Any = None
_tesseract_bin: Optional[str] = None
_ddddocr_ocr: Any = None
_ddddocr_det: Any = None


def _find_tesseract_bin() -> str:
    global _tesseract_bin
    if _tesseract_bin is not None:
        return _tesseract_bin
    bin_path = (shutil.which("tesseract") or "").strip()
    if not bin_path:
        # 常见 Windows 安装路径
        for cand in (
            r"C:\Program Files\Tesseract-OCR\tesseract.exe",
            r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        ):
            if os.path.isfile(cand):
                bin_path = cand
                break
    _tesseract_bin = bin_path
    return _tesseract_bin


def _has_pytesseract() -> bool:
    try:
        importlib.import_module("pytesseract")
        return True
    except Exception:
        return False


def _has_tesseract() -> bool:
    """pytesseract 可导入，或系统 tesseract.exe 在 PATH/常见路径。"""
    if _has_pytesseract():
        return True
    return bool(_find_tesseract_bin())


def _has_paddle() -> bool:
    try:
        importlib.import_module("paddleocr")
        return True
    except Exception:
        return False


def _has_ddddocr() -> bool:
    try:
        importlib.import_module("ddddocr")
        return True
    except Exception:
        return False


def _init_engine() -> str:
    global _ocr_engine, _paddle_instance
    if _ocr_engine:
        return _ocr_engine
    if _has_paddle():
        try:
            from paddleocr import PaddleOCR
            _paddle_instance = PaddleOCR(lang="ch", use_angle_cls=False, show_log=False)
            _ocr_engine = "paddle"
            return _ocr_engine
        except Exception:
            pass
    if _has_tesseract():
        _ocr_engine = "tesseract"
        return _ocr_engine
    if _has_ddddocr():
        _ocr_engine = "ddddocr"
        return _ocr_engine
    _ocr_engine = "none"
    return _ocr_engine


def _tesseract_cli_tsv(png_bytes: bytes, lang: str, *, psm: int = 6) -> List[Dict[str, Any]]:
    """不依赖 pytesseract：直接调 tesseract CLI 输出 TSV。"""
    tess = _find_tesseract_bin()
    if not tess or not png_bytes:
        return []
    path = ""
    try:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(png_bytes)
            path = f.name
        # tsv: level page_num block_num par_num line_num word_num left top width height conf text
        proc = subprocess.run(
            [tess, path, "stdout", "-l", lang, "--psm", str(int(psm)), "tsv"],
            capture_output=True,
            timeout=20,
            check=False,
        )
        out = (proc.stdout or b"").decode("utf-8", errors="replace")
        results: List[Dict[str, Any]] = []
        for i, line in enumerate(out.splitlines()):
            if i == 0 and line.lower().startswith("level"):
                continue
            parts = line.split("\t")
            if len(parts) < 12:
                continue
            try:
                conf_raw = float(parts[10])
            except Exception:
                conf_raw = -1.0
            if conf_raw < 0:
                continue
            text = (parts[11] or "").strip()
            if not text:
                continue
            try:
                left, top, w, h = int(parts[6]), int(parts[7]), int(parts[8]), int(parts[9])
            except Exception:
                continue
            results.append(
                {
                    "text": text,
                    "bbox": [left, top, left + w, top + h],
                    "confidence": round(min(1.0, max(0.0, conf_raw / 100.0)), 4),
                }
            )
        return results
    except Exception:
        return []
    finally:
        if path:
            try:
                os.unlink(path)
            except Exception:
                pass


def _tesseract_cli_string(png_bytes: bytes, lang: str) -> str:
    tess = _find_tesseract_bin()
    if not tess or not png_bytes:
        return ""
    path = ""
    try:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(png_bytes)
            path = f.name
        proc = subprocess.run(
            [tess, path, "stdout", "-l", lang, "--psm", "6"],
            capture_output=True,
            timeout=20,
            check=False,
        )
        return (proc.stdout or b"").decode("utf-8", errors="replace").strip()
    except Exception:
        return ""
    finally:
        if path:
            try:
                os.unlink(path)
            except Exception:
                pass


def _ddddocr_blocks(png_bytes: bytes) -> List[Dict[str, Any]]:
    """ddddocr 整图识别 + 简易检测框（无精细 bbox 时给全图框）。"""
    global _ddddocr_ocr, _ddddocr_det
    if not png_bytes:
        return []
    try:
        import ddddocr
    except Exception:
        return []

    results: List[Dict[str, Any]] = []
    try:
        if _ddddocr_ocr is None:
            _ddddocr_ocr = ddddocr.DdddOcr(show_ad=False)
        text = (_ddddocr_ocr.classification(png_bytes) or "").strip()
        # classification 常返回连在一起的字，尽量按空白/标点拆
        if text:
            # 尝试拆成可见片段
            parts = re.findall(r"[\u4e00-\u9fffA-Za-z0-9]{1,12}", text)
            if not parts:
                parts = [text]
            # 无逐字坐标时均匀铺在图像上，便于 Agent 至少拿到文字列表
            try:
                from PIL import Image

                img = Image.open(BytesIO(png_bytes))
                w, h = img.size
            except Exception:
                w, h = 800, 600
            n = max(1, len(parts))
            for i, p in enumerate(parts):
                # 粗略纵向排布
                y0 = int(h * 0.1 + (h * 0.8) * i / n)
                y1 = min(h - 1, y0 + max(18, h // (n + 2)))
                results.append(
                    {
                        "text": p,
                        "bbox": [int(w * 0.05), y0, int(w * 0.95), y1],
                        "confidence": 0.55,
                    }
                )
    except Exception:
        pass

    # 检测框（无文字时仍可返回区域）
    try:
        if _ddddocr_det is None:
            _ddddocr_det = ddddocr.DdddOcr(det=True, ocr=False, show_ad=False)
        from PIL import Image
        import numpy as np

        img = Image.open(BytesIO(png_bytes)).convert("RGB")
        arr = np.array(img)
        boxes = _ddddocr_det.detection(arr) or []
        if boxes and not results:
            for box in boxes[:40]:
                pts = np.array(box, dtype=np.int32)
                x_coords = pts[:, 0]
                y_coords = pts[:, 1]
                l, t, r, b = int(min(x_coords)), int(min(y_coords)), int(max(x_coords)), int(max(y_coords))
                results.append(
                    {
                        "text": "",
                        "bbox": [l, t, r, b],
                        "confidence": 0.4,
                    }
                )
    except Exception:
        pass

    return [r for r in results if (r.get("text") or "").strip()]


def extract_text(screenshot_path: str, lang: str = "chi_sim+eng") -> str:
    engine = _init_engine()
    if engine == "paddle" and _paddle_instance is not None:
        try:
            result = _paddle_instance.ocr(screenshot_path, cls=False)
            if not result or not result[0]:
                return ""
            lines = []
            for line_info in result[0]:
                text = line_info[1][0] if len(line_info) >= 2 else ""
                if text:
                    lines.append(text)
            return "\n".join(lines)
        except Exception:
            pass

    if engine == "tesseract":
        try:
            if _has_pytesseract():
                pyt = importlib.import_module("pytesseract")
                from PIL import Image
                img = Image.open(screenshot_path)
                return pyt.image_to_string(img, lang=lang).strip()
        except Exception:
            pass
        try:
            with open(screenshot_path, "rb") as f:
                return _tesseract_cli_string(f.read(), lang)
        except Exception:
            return ""

    if engine == "ddddocr":
        try:
            with open(screenshot_path, "rb") as f:
                blocks = _ddddocr_blocks(f.read())
            return "\n".join(b["text"] for b in blocks if b.get("text"))
        except Exception:
            return ""

    return ""


def extract_text_from_bytes(png_bytes: bytes, lang: str = "chi_sim+eng") -> str:
    blocks = extract_text_blocks(png_bytes, lang=lang, min_confidence=0.0)
    return "\n".join(b["text"] for b in blocks if b.get("text"))


def extract_text_blocks(
    png_bytes: bytes,
    lang: str = "chi_sim+eng",
    *,
    min_confidence: float = 0.0,
) -> List[Dict[str, Any]]:
    """结构化 OCR：返回 [{text, bbox:[l,t,r,b], confidence:0..1}, ...]。"""
    if not png_bytes:
        return []
    engine = _init_engine()
    results: List[Dict[str, Any]] = []

    if engine == "paddle" and _paddle_instance is not None:
        try:
            from PIL import Image
            import numpy as np

            img = Image.open(BytesIO(png_bytes)).convert("RGB")
            arr = np.array(img)
            result = _paddle_instance.ocr(arr, cls=False)
            if result and result[0]:
                for line_info in result[0]:
                    if len(line_info) < 2:
                        continue
                    text = (line_info[1][0] or "").strip()
                    if not text:
                        continue
                    conf = float(line_info[1][1]) if len(line_info[1]) > 1 else 0.85
                    # paddle 分数通常已是 0..1
                    if conf > 1.0:
                        conf = conf / 100.0
                    box = line_info[0]
                    x_coords = [p[0] for p in box]
                    y_coords = [p[1] for p in box]
                    l, t, r, b = min(x_coords), min(y_coords), max(x_coords), max(y_coords)
                    if conf < min_confidence:
                        continue
                    results.append(
                        {
                            "text": text,
                            "bbox": [int(l), int(t), int(r), int(b)],
                            "confidence": round(conf, 4),
                        }
                    )
                if results:
                    return results
        except Exception:
            pass

    if engine == "tesseract":
        # 优先 pytesseract；失败则 CLI（本机常有 tesseract.exe 但 pytesseract 依赖坏了）
        if _has_pytesseract():
            try:
                pyt = importlib.import_module("pytesseract")
                from PIL import Image

                img = Image.open(BytesIO(png_bytes))
                data = pyt.image_to_data(img, lang=lang, output_type=pyt.Output.DICT)
                n = len(data.get("text", []))
                for i in range(n):
                    txt = (data["text"][i] or "").strip()
                    if not txt:
                        continue
                    conf_raw = int(data.get("conf", [0])[i] if i < len(data.get("conf", [])) else 0)
                    if conf_raw < 0:
                        continue
                    conf = conf_raw / 100.0
                    if conf < min_confidence:
                        continue
                    x = int(data["left"][i])
                    y = int(data["top"][i])
                    w = int(data["width"][i])
                    h = int(data["height"][i])
                    results.append(
                        {
                            "text": txt,
                            "bbox": [x, y, x + w, y + h],
                            "confidence": round(conf, 4),
                        }
                    )
                if results:
                    return results
            except Exception:
                results = []
        cli = _tesseract_cli_tsv(png_bytes, lang, psm=6)
        results = [b for b in cli if float(b.get("confidence") or 0) >= min_confidence]
        if not results:
            # 稀疏 UI / 小字：再试 PSM 11（稀疏文本）与 7（单行）
            merged: List[Dict[str, Any]] = []
            seen = set()
            for psm in (11, 7):
                for b in _tesseract_cli_tsv(png_bytes, lang, psm=psm):
                    if float(b.get("confidence") or 0) < min_confidence:
                        continue
                    key = (b.get("text"), tuple(b.get("bbox") or []))
                    if key in seen:
                        continue
                    seen.add(key)
                    merged.append(b)
            results = merged
        if results:
            return results
        # tesseract 失败时再试 ddddocr
        if _has_ddddocr():
            return [
                b
                for b in _ddddocr_blocks(png_bytes)
                if float(b.get("confidence") or 0) >= min_confidence
            ]
        return []

    if engine == "ddddocr":
        return [
            b
            for b in _ddddocr_blocks(png_bytes)
            if float(b.get("confidence") or 0) >= min_confidence
        ]

    # engine none：仍尝试 CLI / ddddocr（避免缓存了错误的 none）
    if _find_tesseract_bin():
        cli = _tesseract_cli_tsv(png_bytes, lang)
        results = [b for b in cli if float(b.get("confidence") or 0) >= min_confidence]
        if results:
            return results
    if _has_ddddocr():
        return [
            b
            for b in _ddddocr_blocks(png_bytes)
            if float(b.get("confidence") or 0) >= min_confidence
        ]
    return []


def find_text_location(
    screenshot_path: str,
    keyword: str,
    lang: str = "chi_sim+eng",
) -> Optional[Tuple[int, int, int, int]]:
    engine = _init_engine()

    if engine == "paddle" and _paddle_instance is not None:
        try:
            result = _paddle_instance.ocr(screenshot_path, cls=False)
            if not result or not result[0]:
                return None
            best = None
            for line_info in result[0]:
                if len(line_info) < 2:
                    continue
                text = line_info[1][0]
                if keyword.lower() in text.lower():
                    box = line_info[0]
                    x_coords = [p[0] for p in box]
                    y_coords = [p[1] for p in box]
                    l, t, r, b = min(x_coords), min(y_coords), max(x_coords), max(y_coords)
                    area = (r - l) * (b - t)
                    if best is None or area > best[4]:
                        best = (l, t, r, b, area)
            if best:
                return (int(best[0]), int(best[1]), int(best[2]), int(best[3]))
        except Exception:
            pass

    if engine == "tesseract":
        try:
            pyt = importlib.import_module("pytesseract")
            from PIL import Image
            img = Image.open(screenshot_path)
            data = pyt.image_to_data(img, lang=lang, output_type=pyt.Output.DICT)
            n = len(data.get("text", []))
            for i in range(n):
                txt = (data["text"][i] or "").strip()
                if keyword.lower() in txt.lower():
                    x = int(data["left"][i])
                    y = int(data["top"][i])
                    w = int(data["width"][i])
                    h = int(data["height"][i])
                    return (x, y, x + w, y + h)
        except Exception:
            return None

    return None


def find_text_location_in_bytes(
    png_bytes: bytes,
    keyword: str,
    lang: str = "chi_sim+eng",
) -> Optional[Tuple[int, int, int, int]]:
    engine = _init_engine()
    if engine == "paddle" and _paddle_instance is not None:
        try:
            from PIL import Image
            import numpy as np
            img = Image.open(BytesIO(png_bytes)).convert("RGB")
            arr = np.array(img)
            result = _paddle_instance.ocr(arr, cls=False)
            if not result or not result[0]:
                return None
            best = None
            for line_info in result[0]:
                if len(line_info) < 2:
                    continue
                text = line_info[1][0]
                if keyword.lower() in text.lower():
                    box = line_info[0]
                    x_coords = [p[0] for p in box]
                    y_coords = [p[1] for p in box]
                    l, t, r, b = min(x_coords), min(y_coords), max(x_coords), max(y_coords)
                    area = (r - l) * (b - t)
                    if best is None or area > best[4]:
                        best = (l, t, r, b, area)
            if best:
                return (int(best[0]), int(best[1]), int(best[2]), int(best[3]))
        except Exception:
            pass

    if engine == "tesseract":
        try:
            pyt = importlib.import_module("pytesseract")
            from PIL import Image
            img = Image.open(BytesIO(png_bytes))
            data = pyt.image_to_data(img, lang=lang, output_type=pyt.Output.DICT)
            n = len(data.get("text", []))
            for i in range(n):
                txt = (data["text"][i] or "").strip()
                if keyword.lower() in txt.lower():
                    x = int(data["left"][i])
                    y = int(data["top"][i])
                    w = int(data["width"][i])
                    h = int(data["height"][i])
                    return (x, y, x + w, y + h)
        except Exception:
            return None

    return None


def engine_name() -> str:
    _init_engine()
    return _ocr_engine or "none"


def ocr_available() -> bool:
    eng = _init_engine()
    if eng == "none":
        return False
    if eng == "tesseract":
        return _has_pytesseract() or bool(_find_tesseract_bin())
    if eng == "ddddocr":
        return _has_ddddocr()
    return True


def reset_ocr_engine_cache() -> None:
    """测试/热重载：清除引擎探测缓存。"""
    global _ocr_engine, _paddle_instance, _tesseract_bin, _ddddocr_ocr, _ddddocr_det
    _ocr_engine = None
    _paddle_instance = None
    _tesseract_bin = None
    _ddddocr_ocr = None
    _ddddocr_det = None


def extract_text_blocks_roi(
    png_bytes: bytes,
    *,
    left_ratio: float = 0.0,
    top_ratio: float = 0.0,
    right_ratio: float = 1.0,
    bottom_ratio: float = 1.0,
    scale: float = 2.0,
    lang: str = "chi_sim+eng",
    min_confidence: float = 0.25,
) -> List[Dict[str, Any]]:
    """对截图 ROI 放大后再 OCR，坐标映射回原图像素（提高小灰字命中率）。"""
    if not png_bytes:
        return []
    try:
        from PIL import Image

        img = Image.open(BytesIO(png_bytes)).convert("RGB")
        w, h = img.size
        l = max(0, int(w * float(left_ratio)))
        t = max(0, int(h * float(top_ratio)))
        r = min(w, int(w * float(right_ratio)))
        b = min(h, int(h * float(bottom_ratio)))
        if r - l < 8 or b - t < 8:
            return []
        crop = img.crop((l, t, r, b))
        sc = max(1.0, float(scale))
        if sc > 1.01:
            crop = crop.resize(
                (max(1, int(crop.width * sc)), max(1, int(crop.height * sc))),
                Image.Resampling.LANCZOS if hasattr(Image, "Resampling") else Image.LANCZOS,
            )
        buf = BytesIO()
        crop.save(buf, format="PNG")
        blocks = extract_text_blocks(buf.getvalue(), lang=lang, min_confidence=min_confidence)
        out: List[Dict[str, Any]] = []
        for blk in blocks:
            bb = blk.get("bbox") or [0, 0, 0, 0]
            if len(bb) < 4:
                continue
            # 放大坐标 → 原图
            x0 = int(bb[0] / sc) + l
            y0 = int(bb[1] / sc) + t
            x1 = int(bb[2] / sc) + l
            y1 = int(bb[3] / sc) + t
            out.append(
                {
                    "text": blk.get("text") or "",
                    "bbox": [x0, y0, x1, y1],
                    "confidence": blk.get("confidence"),
                }
            )
        return out
    except Exception:
        return []


def extract_text_from_region_b64(b64_png: str, lang: str = "chi_sim+eng") -> List[Dict[str, Any]]:
    import base64

    try:
        png_bytes = base64.b64decode(b64_png)
    except Exception:
        return []

    engine = _init_engine()
    results: List[Dict[str, Any]] = []

    if engine == "paddle" and _paddle_instance is not None:
        try:
            from PIL import Image
            import numpy as np
            img = Image.open(BytesIO(png_bytes)).convert("RGB")
            arr = np.array(img)
            result = _paddle_instance.ocr(arr, cls=False)
            if result and result[0]:
                for line_info in result[0]:
                    if len(line_info) < 2:
                        continue
                    text = line_info[1][0]
                    confidence = line_info[1][1]
                    box = line_info[0]
                    x_coords = [p[0] for p in box]
                    y_coords = [p[1] for p in box]
                    l, t, r, b = min(x_coords), min(y_coords), max(x_coords), max(y_coords)
                    area = (r - l) * (b - t)
                    results.append({
                        "text": text,
                        "confidence": float(confidence),
                        "box": (int(l), int(t), int(r), int(b)),
                        "area": int(area),
                    })
                return results
        except Exception:
            pass

    if engine == "tesseract":
        try:
            pyt = importlib.import_module("pytesseract")
            from PIL import Image
            img = Image.open(BytesIO(png_bytes))
            data = pyt.image_to_data(img, lang=lang, output_type=pyt.Output.DICT)
            n = len(data.get("text", []))
            for i in range(n):
                txt = (data["text"][i] or "").strip()
                if not txt:
                    continue
                conf = int(data.get("conf", [0])[i] if i < len(data.get("conf", [])) else 0)
                if conf < 30:
                    continue
                x = int(data["left"][i])
                y = int(data["top"][i])
                w = int(data["width"][i])
                h = int(data["height"][i])
                results.append({
                    "text": txt,
                    "confidence": float(conf),
                    "box": (x, y, x + w, y + h),
                    "area": w * h,
                })
        except Exception:
            pass

    return results


_NOISE_TEXT_PATTERNS = (
    re.compile(r"^\d{4}[-/]\d{2}[-/]\d{2}$"),
    re.compile(r"^\d+[:：]\d+$"),
    re.compile(r"^\d+(\.\d+)?\s*(KB|MB|GB|字节|px|像素)$"),
    re.compile(r"^[#]?[0-9A-Fa-f]{6}$"),
    re.compile(r"^[\\/|_\-=*]+$"),
)


def _is_noise_text(text: str) -> bool:
    t = text.strip()
    if not t:
        return True
    if len(t) <= 1:
        return True
    for pat in _NOISE_TEXT_PATTERNS:
        if pat.match(t):
            return True
    return False


def extract_primary_text(
    b64_png: str, lang: str = "chi_sim+eng",
) -> str:
    results = extract_text_from_region_b64(b64_png, lang=lang)
    if not results:
        return ""

    filtered = [r for r in results if not _is_noise_text(r["text"])]
    if not filtered:
        filtered = results

    filtered.sort(key=lambda r: r.get("confidence", 0), reverse=True)
    best = filtered[0]

    center_x = (best["box"][0] + best["box"][2]) // 2
    center_y = (best["box"][1] + best["box"][3]) // 2

    near_center = sorted(
        filtered,
        key=lambda r: (
            (r["box"][0] + r["box"][2]) // 2 - center_x) ** 2
            + ((r["box"][1] + r["box"][3]) // 2 - center_y) ** 2,
    )
    if near_center and near_center[0]["text"] != best["text"]:
        combined = near_center[0]
        if combined["confidence"] > best["confidence"] * 0.5:
            return combined["text"].strip()

    return best["text"].strip()
