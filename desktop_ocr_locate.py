import numpy as np
from typing import Tuple, Optional, Dict, List


def locate_element_via_ocr(
    x: int,
    y: int,
    search_radius: int = 128,
) -> Optional[Dict]:
    try:
        from desktop_precise_locator import capture_rect_preview_b64

        l = max(0, x - search_radius)
        t = max(0, y - search_radius)
        r = x + search_radius
        b = y + search_radius

        preview = capture_rect_preview_b64(l, t, r, b, padding=0)
        if not preview:
            return None

        from PIL import Image
        from io import BytesIO
        import base64

        if isinstance(preview, str):
            raw = base64.b64decode(preview)
        else:
            raw = preview
        img = Image.open(BytesIO(raw)).convert("RGB")
        arr = np.array(img)

        ocr_results = _run_ocr(arr)
        if not ocr_results:
            return None

        closest = _find_closest_text(ocr_results, search_radius, search_radius)
        if not closest:
            return None

        tl, tt, tr, tb, text = closest

        el, et, er, eb = _detect_control_boundary(arr, (tl, tt, tr, tb), text)

        return {
            'rect': (l + el, t + et, l + er, t + eb),
            'text': text,
            'control_type': _infer_control_type(text),
        }

    except Exception:
        import traceback
        traceback.print_exc()
        return None


def _run_ocr(arr: np.ndarray) -> List[Tuple[int, int, int, int, str]]:
    results = []

    try:
        from desktop_ocr import _init_engine, _paddle_instance, _ocr_engine
        _init_engine()
        if _ocr_engine == "paddle" and _paddle_instance is not None:
            result = _paddle_instance.ocr(arr, cls=False)
            if result and result[0]:
                for line_info in result[0]:
                    if len(line_info) >= 2:
                        text = line_info[1][0]
                        if not text:
                            continue
                        box = line_info[0]
                        x_coords = [p[0] for p in box]
                        y_coords = [p[1] for p in box]
                        tl, tt, tr, tb = min(x_coords), min(y_coords), max(x_coords), max(y_coords)
                        results.append((int(tl), int(tt), int(tr), int(tb), text))
            return results
    except Exception:
        pass

    try:
        import pytesseract
        from PIL import Image

        img = Image.fromarray(arr)
        data = pytesseract.image_to_data(img, lang="chi_sim+eng", output_type=pytesseract.Output.DICT)
        n = len(data.get("text", []))
        for i in range(n):
            txt = (data["text"][i] or "").strip()
            if not txt:
                continue
            left = int(data["left"][i])
            top = int(data["top"][i])
            w = int(data["width"][i])
            h = int(data["height"][i])
            results.append((left, top, left + w, top + h, txt))
        return results
    except Exception:
        pass

    try:
        import ddddocr
        det = ddddocr.DdddOcr(det=True, ocr=True, show_ad=False)
        boxes = det.detection(arr)
        for box in boxes:
            if len(box) >= 4:
                pts = np.array(box, dtype=np.int32)
                x_coords = pts[:, 0]
                y_coords = pts[:, 1]
                tl, tt, tr, tb = min(x_coords), min(y_coords), max(x_coords), max(y_coords)
                results.append((tl, tt, tr, tb, "button"))
        return results
    except Exception:
        pass

    return results


def _find_closest_text(
    ocr_results: List[Tuple[int, int, int, int, str]],
    cx: int,
    cy: int,
) -> Optional[Tuple[int, int, int, int, str]]:
    best_dist = float("inf")
    best_result = None

    for tl, tt, tr, tb, text in ocr_results:
        tc_x = (tl + tr) / 2
        tc_y = (tt + tb) / 2
        dist = ((tc_x - cx) ** 2 + (tc_y - cy) ** 2) ** 0.5
        if dist < best_dist:
            best_dist = dist
            best_result = (tl, tt, tr, tb, text)

    if best_result and best_dist <= 80:
        return best_result
    return None


def _detect_control_boundary(
    arr: np.ndarray,
    text_box: Tuple[int, int, int, int],
    text: str,
) -> Tuple[int, int, int, int]:
    tl, tt, tr, tb = text_box
    tw = tr - tl
    th = tb - tt
    img_h, img_w = arr.shape[:2]

    button_keywords = {"登录", "确定", "取消", "发送", "提交", "保存", "关闭", "返回", "下一步", "完成", "确认"}
    is_button = any(kw in text for kw in button_keywords)

    if is_button:
        return _detect_button_boundary(arr, text_box, tw, th, img_w, img_h)

    input_keywords = {"输入", "搜索", "请", "账号", "密码", "用户名"}
    if any(kw in text for kw in input_keywords):
        return _detect_input_boundary(arr, text_box, tw, th, img_w, img_h)

    return _infer_element_boundary(text_box, img_w, img_h)


def _detect_button_boundary(
    arr: np.ndarray,
    text_box: Tuple[int, int, int, int],
    tw: int,
    th: int,
    img_w: int,
    img_h: int,
) -> Tuple[int, int, int, int]:
    tl, tt, tr, tb = text_box

    margin_w = max(12, int(tw * 0.4))
    margin_h = max(10, int(th * 0.6))

    search_l = max(0, tl - margin_w)
    search_t = max(0, tt - margin_h)
    search_r = min(img_w, tr + margin_w)
    search_b = min(img_h, tb + margin_h)

    search_region = arr[search_t:search_b, search_l:search_r]

    gray = np.mean(search_region, axis=2)
    edges = _simple_edge_detection(gray)

    h_proj = np.sum(edges, axis=1)
    v_proj = np.sum(edges, axis=0)

    padding = 2
    el = max(0, tl - margin_w + padding)
    et = max(0, tt - margin_h + padding)
    er = min(img_w, tr + margin_w - padding)
    eb = min(img_h, tb + margin_h - padding)

    min_w = 40
    min_h = 24
    if er - el < min_w:
        diff = min_w - (er - el)
        el = max(0, el - diff // 2)
        er = min(img_w, er + (diff - diff // 2))
    if eb - et < min_h:
        diff = min_h - (eb - et)
        et = max(0, et - diff // 2)
        eb = min(img_h, eb + (diff - diff // 2))

    return (el, et, er, eb)


def _detect_input_boundary(
    arr: np.ndarray,
    text_box: Tuple[int, int, int, int],
    tw: int,
    th: int,
    img_w: int,
    img_h: int,
) -> Tuple[int, int, int, int]:
    tl, tt, tr, tb = text_box

    margin_w = max(20, int(tw * 0.5))
    margin_h = max(10, int(th * 0.5))

    search_l = max(0, tl - margin_w)
    search_t = max(0, tt - margin_h)
    search_r = min(img_w, tr + margin_w)
    search_b = min(img_h, tb + margin_h)

    search_region = arr[search_t:search_b, search_l:search_r]

    gray = np.mean(search_region, axis=2)
    edges = _simple_edge_detection(gray)

    contours = _find_rectangular_contours(edges)
    if contours:
        best_contour = max(contours, key=lambda c: (c[2] - c[0]) * (c[3] - c[1]))
        return (
            max(0, search_l + best_contour[0] - 2),
            max(0, search_t + best_contour[1] - 2),
            min(img_w, search_l + best_contour[2] + 2),
            min(img_h, search_t + best_contour[3] + 2),
        )

    return _infer_element_boundary(text_box, img_w, img_h)


def _simple_edge_detection(gray: np.ndarray) -> np.ndarray:
    sobel_x = np.zeros_like(gray, dtype=np.float32)
    sobel_y = np.zeros_like(gray, dtype=np.float32)

    sobel_x[1:-1, 1:-1] = (
        gray[1:-1, 2:] - gray[1:-1, :-2] +
        2 * (gray[2:, 2:] - gray[2:, :-2]) +
        2 * (gray[:-2, 2:] - gray[:-2, :-2])
    )
    sobel_y[1:-1, 1:-1] = (
        gray[2:, 1:-1] - gray[:-2, 1:-1] +
        2 * (gray[2:, 2:] - gray[:-2, 2:]) +
        2 * (gray[2:, :-2] - gray[:-2, :-2])
    )

    edges = np.sqrt(sobel_x ** 2 + sobel_y ** 2)
    edges = (edges > np.percentile(edges, 85)).astype(np.uint8)

    return edges


def _find_rectangular_contours(edges: np.ndarray) -> List[Tuple[int, int, int, int]]:
    contours = []
    h, w = edges.shape
    visited = np.zeros((h, w), dtype=bool)

    for i in range(h):
        for j in range(w):
            if edges[i, j] == 1 and not visited[i, j]:
                stack = [(i, j)]
                visited[i, j] = True
                min_r, min_c, max_r, max_c = i, j, i, j

                while stack:
                    r, c = stack.pop()
                    min_r, min_c = min(min_r, r), min(min_c, c)
                    max_r, max_c = max(max_r, r), max(max_c, c)

                    for dr, dc in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
                        nr, nc = r + dr, c + dc
                        if 0 <= nr < h and 0 <= nc < w and edges[nr, nc] == 1 and not visited[nr, nc]:
                            visited[nr, nc] = True
                            stack.append((nr, nc))

                contour_w = max_c - min_c + 1
                contour_h = max_r - min_r + 1
                if contour_w >= 20 and contour_h >= 15:
                    contours.append((min_c, min_r, max_c, max_r))

    return contours


def _infer_element_boundary(
    text_box: Tuple[int, int, int, int],
    img_w: int,
    img_h: int,
) -> Tuple[int, int, int, int]:
    tl, tt, tr, tb = text_box
    tw = tr - tl
    th = tb - tt

    margin_w = max(8, int(tw * 0.3))
    margin_h = max(6, int(th * 0.4))

    el = max(0, tl - margin_w)
    et = max(0, tt - margin_h)
    er = min(img_w, tr + margin_w)
    eb = min(img_h, tb + margin_h)

    min_w = 30
    min_h = 20
    if er - el < min_w:
        diff = min_w - (er - el)
        el = max(0, el - diff // 2)
        er = min(img_w, er + (diff - diff // 2))
    if eb - et < min_h:
        diff = min_h - (eb - et)
        et = max(0, et - diff // 2)
        eb = min(img_h, eb + (diff - diff // 2))

    return (el, et, er, eb)


def _infer_control_type(text: str) -> str:
    text_lower = text.lower()

    button_keywords = {"登录", "确定", "取消", "发送", "提交", "保存", "关闭", "返回", "下一步", "完成", "确认", "搜索", "添加", "删除", "编辑"}
    if any(kw in text for kw in button_keywords):
        return "Button"

    input_keywords = {"输入", "搜索", "请", "账号", "密码", "用户名", "手机号", "邮箱", "验证码"}
    if any(kw in text for kw in input_keywords):
        return "Edit"

    list_keywords = {"消息", "联系人", "好友", "文件", "列表", "选项"}
    if any(kw in text for kw in list_keywords):
        return "ListItem"

    link_keywords = {"点击", "链接", "查看", "了解", "详情"}
    if any(kw in text for kw in link_keywords):
        return "Hyperlink"

    return "Text"
