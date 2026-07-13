"""OCR 基础包装器：Tesseract + PaddleOCR 可选增强。

提供两个核心 API：
- extract_text(screenshot_path) → str
- find_text_location(screenshot_path, keyword) → (left, top, right, bottom) or None

优先加载 PaddleOCR（中文更准），不可用时回退 Tesseract。
"""

from __future__ import annotations

import importlib
import os
import re
from io import BytesIO
from typing import Any, Dict, List, Optional, Tuple


_ocr_engine: Optional[str] = None
_paddle_instance: Any = None


def _has_tesseract() -> bool:
    try:
        importlib.import_module("pytesseract")
        return True
    except Exception:
        return False


def _has_paddle() -> bool:
    try:
        importlib.import_module("paddleocr")
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
    _ocr_engine = "none"
    return _ocr_engine


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
            pyt = importlib.import_module("pytesseract")
            from PIL import Image
            img = Image.open(screenshot_path)
            return pyt.image_to_string(img, lang=lang).strip()
        except Exception:
            return ""

    return ""


def extract_text_from_bytes(png_bytes: bytes, lang: str = "chi_sim+eng") -> str:
    engine = _init_engine()
    if engine == "paddle" and _paddle_instance is not None:
        try:
            from PIL import Image
            import numpy as np
            img = Image.open(BytesIO(png_bytes)).convert("RGB")
            arr = np.array(img)
            result = _paddle_instance.ocr(arr, cls=False)
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
            pyt = importlib.import_module("pytesseract")
            from PIL import Image
            img = Image.open(BytesIO(png_bytes))
            return pyt.image_to_string(img, lang=lang).strip()
        except Exception:
            return ""

    return ""


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
    return _init_engine() != "none"


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
