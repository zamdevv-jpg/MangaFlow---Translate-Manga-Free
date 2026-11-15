from __future__ import annotations

import math
import re
import threading
import unicodedata
from dataclasses import dataclass, field

import cv2
import numpy as np
from rapidocr import RapidOCR

from .config import OCR_DIR
from .languages import OCR_MODEL_LANG

_OCR_LOCK = threading.Lock()
_OCR_ENGINES: dict[str, RapidOCR] = {}


@dataclass
class TextLine:
    points: list[list[float]]
    box: tuple[int, int, int, int]
    text: str
    score: float
    vertical: bool = False

    @property
    def width(self) -> int:
        return self.box[2] - self.box[0]

    @property
    def height(self) -> int:
        return self.box[3] - self.box[1]

    @property
    def area(self) -> int:
        return max(1, self.width) * max(1, self.height)


@dataclass
class TextGroup:
    lines: list[TextLine]
    box: tuple[int, int, int, int]
    text: str
    vertical: bool = False
    region: tuple[int, int, int, int] | None = None
    bubble: bool = False
    extra: dict = field(default_factory=dict)


def _get_engine(lang: str) -> RapidOCR:
    key = OCR_MODEL_LANG.get(lang, "en")
    with _OCR_LOCK:
        if key not in _OCR_ENGINES:
            params = {
                "Global.model_root_dir": str(OCR_DIR),
                "Global.log_level": "error",
                "Global.max_side_len": 2000,
                "Global.min_side_len": 30,
                "Global.text_score": 0.5,
                "Det.limit_side_len": 736,
                "Det.limit_type": "min",
                "Det.thresh": 0.3,
                "Det.box_thresh": 0.5,
                "Det.unclip_ratio": 1.6,
                "Det.use_dilation": True,
                "Rec.lang_type": key,
                "Rec.rec_batch_num": 8,
            }
            _OCR_ENGINES[key] = RapidOCR(params=params)
        return _OCR_ENGINES[key]


def _normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = text.replace("\u00ad", "").replace("\u200b", "").replace("\ufeff", "")
    text = re.sub(r"[ \t\u3000]+", " ", text)
    return text.strip()


def _poly_to_box(points) -> tuple[int, int, int, int]:
    xs = [float(p[0]) for p in points]
    ys = [float(p[1]) for p in points]
    return (int(math.floor(min(xs))), int(math.floor(min(ys))), int(math.ceil(max(xs))), int(math.ceil(max(ys))))


def _is_noise(text: str, box: tuple[int, int, int, int], page_w: int, page_h: int) -> bool:
    t = text.strip()
    if not t:
        return True
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    if w < 5 or h < 6:
        return True
    if w * h < 45:
        return True
    if w > page_w * 0.92 and h > page_h * 0.55:
        return True
    if not re.search(r"[\w\u3040-\u30ff\u4e00-\u9fff]", t, re.UNICODE):
        return True
    letters = re.findall(r"[A-Za-z]", t)
    if len("".join(letters)) <= 1 and not re.search(r"[\u3040-\u30ff\u4e00-\u9fff]", t):
        return True
    latin = re.findall(r"[A-Za-z]", t)
    if len(latin) >= 5 and len(latin) == len(re.sub(r"[\s\W\d_]", "", t)):
        low = "".join(latin).lower()
        vowels = sum(1 for c in low if c in "aeiouy")
        interjections = {"hmm", "mmm", "shh", "psst", "tsk", "grr", "brr", "tch", "pff", "zzz"}
        if vowels == 0 and low not in interjections:
            return True
    if re.fullmatch(r"(.)\1{7,}", t.replace(" ", "")):
        return True
    return False


def _run_ocr(engine: RapidOCR, image: np.ndarray, offset_x: int, offset_y: int) -> list[TextLine]:
    result = engine(image)
    boxes = getattr(result, "boxes", None)
    txts = getattr(result, "txts", None)
    scores = getattr(result, "scores", None)
    if boxes is None or txts is None:
        return []
    lines: list[TextLine] = []
    for i, (box, txt) in enumerate(zip(boxes, txts)):
        score = float(scores[i]) if scores is not None and i < len(scores) else 0.0
        pts = [[float(p[0]) + offset_x, float(p[1]) + offset_y] for p in box]
        text = _normalize_text(str(txt))
        bbox = _poly_to_box(pts)
        if _is_noise(text, bbox, image.shape[1] + offset_x, image.shape[0] + offset_y):
            continue
        w = bbox[2] - bbox[0]
        h = bbox[3] - bbox[1]
        lines.append(TextLine(points=pts, box=bbox, text=text, score=score, vertical=h > w * 1.55))
    return lines


def _text_key(text: str) -> str:
    return re.sub(r"[\s\W_]+", "", text.lower(), flags=re.UNICODE)


def _iou(a, b) -> float:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix = max(0, min(ax1, bx1) - max(ax0, bx0))
    iy = max(0, min(ay1, by1) - max(ay0, by0))
    inter = ix * iy
    if inter <= 0:
        return 0.0
    area_a = max(1, (ax1 - ax0) * (ay1 - ay0))
    area_b = max(1, (bx1 - bx0) * (by1 - by0))
    return inter / (area_a + area_b - inter)


def _merge_duplicates(lines: list[TextLine]) -> list[TextLine]:
    lines = sorted(lines, key=lambda l: l.score, reverse=True)
    kept: list[TextLine] = []
    for line in lines:
        dup = False
        for k in kept:
            if _iou(line.box, k.box) > 0.35:
                a, b = _text_key(line.text), _text_key(k.text)
                if a == b or (a and b and (a in b or b in a)) or _iou(line.box, k.box) > 0.72:
                    dup = True
                    break
        if not dup:
            kept.append(line)
    return kept


def _merge_same_row(lines: list[TextLine], page_w: int) -> list[TextLine]:
    """Glue OCR fragments that belong to the same visual line."""
    lines = sorted(lines, key=lambda l: (l.box[1], l.box[0]))
    changed = True
    rounds = 0
    while changed and rounds < 4:
        changed = False
        rounds += 1
        out: list[TextLine] = []
        for line in lines:
            merged = False
            for other in out:
                if line.vertical or other.vertical:
                    continue
                ox0, oy0, ox1, oy1 = other.box
                x0, y0, x1, y1 = line.box
                oh, nh = oy1 - oy0, y1 - y0
                if min(oh, nh) / max(oh, nh) < 0.55:
                    continue
                v_overlap = max(0, min(oy1, y1) - max(oy0, y0))
                if v_overlap < 0.55 * min(oh, nh):
                    continue
                gap = max(0, max(x0, ox0) - min(x1, ox1))
                if gap > 0.55 * max(oh, nh):
                    continue
                if min(x0, ox0) < -2 or max(x1, ox1) > page_w + 2:
                    continue
                other.box = (min(ox0, x0), min(oy0, y0), max(ox1, x1), max(oy1, y1))
                other.points = [
                    [other.box[0], other.box[1]], [other.box[2], other.box[1]],
                    [other.box[2], other.box[3]], [other.box[0], other.box[3]],
                ]
                if other.text and line.text:
                    sep = " " if (other.text[-1].isalnum() and line.text[0].isalnum()) else ""
                    # Fragments on a tilted line can be visited right-to-left
                    # because of their y offset. Keep the visual left-to-right
                    # reading order instead of append order.
                    if x0 < ox0:
                        other.text = line.text + sep + other.text
                    else:
                        other.text = other.text + sep + line.text
                other.score = max(other.score, line.score)
                merged = True
                changed = True
                break
            if not merged:
                out.append(line)
        lines = out
    return lines


def _union_box(lines: list[TextLine]) -> tuple[int, int, int, int]:
    return (
        min(l.box[0] for l in lines),
        min(l.box[1] for l in lines),
        max(l.box[2] for l in lines),
        max(l.box[3] for l in lines),
    )


def _join_text(lines: list[TextLine], vertical: bool) -> str:
    if not lines:
        return ""
    if vertical:
        cols: list[list[TextLine]] = []
        for line in sorted(lines, key=lambda l: -l.box[0]):
            placed = False
            for col in cols:
                cx = sum((l.box[0] + l.box[2]) / 2 for l in col) / len(col)
                lx = (line.box[0] + line.box[2]) / 2
                if abs(cx - lx) < max(line.width, col[0].width) * 0.8:
                    col.append(line)
                    placed = True
                    break
            if not placed:
                cols.append([line])
        parts = []
        for col in cols:
            col.sort(key=lambda l: l.box[1])
            parts.append("".join(l.text for l in col))
        return " ".join(p for p in parts if p).strip()
    ordered = sorted(lines, key=lambda l: (l.box[1], l.box[0]))
    text = ""
    for line in ordered:
        if not text:
            text = line.text
        elif text.endswith("-") and line.text[:1].islower():
            text = text[:-1] + line.text
        else:
            text += " " + line.text
    return text.strip()


def _refine_group_text(image, engine, group: TextGroup, page_w: int, page_h: int):
    """Re-read a wide caption from an upscaled crop.

    Small stylized captions are often only partially detected on the full
    page. A zoomed crop usually reads them correctly. The new reading is only
    accepted when it keeps most original characters and overlaps the old box,
    so a bad pass cannot replace a good group with unrelated artwork text.
    """
    x0, y0, x1, y1 = group.box
    w, h = x1 - x0, y1 - y0
    if w < 40 or h < 20 or w <= h * 1.25:
        return None
    old_key = _text_key(group.text)
    if len(old_key) < 2 or len(old_key) > 14:
        return None

    ex = max(8, int(w * 0.10))
    ey = max(8, int(h * 0.14))
    cx0, cy0 = max(0, x0 - ex), max(0, y0 - ey)
    cx1, cy1 = min(page_w, x1 + ex), min(page_h, y1 + ey)
    crop = image[cy0:cy1, cx0:cx1]
    if crop.size == 0 or min(crop.shape[:2]) < 24:
        return None
    scale = 2.0
    up = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    lines = _run_ocr(engine, up, 0, 0)
    if not lines:
        return None

    mapped: list[TextLine] = []
    for line in lines:
        pts = [[p[0] / scale + cx0, p[1] / scale + cy0] for p in line.points]
        box = _poly_to_box(pts)
        line.points = pts
        line.box = box
        line.vertical = line.height > line.width * 1.55
        mapped.append(line)
    mapped = [l for l in mapped if l.score >= 0.5]
    if not mapped:
        return None
    mapped = _merge_duplicates(mapped)
    mapped = _merge_same_row(mapped, page_w)
    mapped = [l for l in mapped if not _is_noise(l.text, l.box, page_w, page_h)]
    if not mapped:
        return None

    box = _union_box(mapped)
    if _iou(box, group.box) < 0.35:
        return None
    wide = (box[2] - box[0]) > (box[3] - box[1]) * 1.25
    ordered = (
        sorted(mapped, key=lambda l: (l.box[1], l.box[0]))
        if wide
        else sorted(mapped, key=lambda l: (-(l.box[0] + l.box[2]), l.box[1]))
    )
    text = _join_text(ordered, vertical=not wide)
    new_key = _text_key(text)
    if len(new_key) < len(old_key) + 2:
        return None
    covered = sum(min(old_key.count(ch), new_key.count(ch)) for ch in set(old_key))
    if covered < len(old_key) * 0.6:
        return None
    return mapped, box, text, not wide


def _ring_bg_lum(image: np.ndarray, box: tuple[int, int, int, int]) -> float:
    """Perkiraan luminans latar dari isi kotak baris OCR.

    Goresan huruf biasanya menutupi kurang dari separuh kotak, sehingga nilai
    median mewakili latar: putih untuk teks hitam di kotak putih, gelap untuk
    teks putih di panel gelap.
    """
    h, w = image.shape[:2]
    x0, y0, x1, y1 = [int(v) for v in box]
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(w, x1), min(h, y1)
    if x1 - x0 < 4 or y1 - y0 < 4:
        return 255.0
    gray = cv2.cvtColor(image[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
    return float(np.median(gray))


def _gap_bg_lum(
    image: np.ndarray,
    a: tuple[int, int, int, int],
    b: tuple[int, int, int, int],
) -> float:
    """Luminans latar di celah antara dua kotak baris OCR.

    Dipakai untuk membedakan dua balon hitam yang saling menempel dari dua
    kolom teks di dalam satu balon. Balon terpisah biasanya dipisahkan garis
    putih tipis, sedangkan bagian dalam satu balon tetap gelap.
    """
    h, w = image.shape[:2]
    ax0, ay0, ax1, ay1 = [int(v) for v in a]
    bx0, by0, bx1, by1 = [int(v) for v in b]
    y0 = max(0, max(ay0, by0))
    y1 = min(h, min(ay1, by1))
    if y1 - y0 < 6:
        return 255.0
    gx0 = min(ax1, bx1)
    gx1 = max(ax0, bx0)
    if gx1 - gx0 < 4:
        # Kotak berimpit: ambil pita tipis di sekitar batas kolom.
        mid = (gx0 + gx1) // 2
        gx0, gx1 = mid - 3, mid + 3
    gx0, gx1 = max(0, gx0), min(w, gx1)
    if gx1 - gx0 < 3:
        return 255.0
    gray = cv2.cvtColor(image[y0:y1, gx0:gx1], cv2.COLOR_BGR2GRAY)
    return float(np.median(gray))


def _group_lines(lines: list[TextLine], page_w: int, page_h: int, vertical_mode: bool, image: np.ndarray | None = None) -> list[TextGroup]:
    groups: list[TextGroup] = []
    if vertical_mode:
        lines = [l for l in lines if l.vertical or (l.height > 35 and l.width < 130)]
        lines.sort(key=lambda l: (-(l.box[0] + l.box[2]), l.box[1]))
        bg_by_line: dict[int, float] = {}
        if image is not None:
            for l in lines:
                bg_by_line[id(l)] = _ring_bg_lum(image, l.box)
        for line in lines:
            line_chars = max(1, len(re.sub(r"\s+", "", line.text)))
            line_size = line.height / line_chars
            best = None
            best_gap = 1e9
            for g in groups:
                gx0, gy0, gx1, gy1 = g.box
                lx0, ly0, lx1, ly1 = line.box
                xgap = max(0, max(gx0, lx0) - min(gx1, lx1))
                yoverlap = max(0, min(gy1, ly1) - max(gy0, ly0))
                min_h = min(gy1 - gy0, ly1 - ly0)
                if yoverlap < min_h * 0.25:
                    continue
                if xgap > max(60, (gx1 - gx0 + lx1 - lx0) * 0.55):
                    continue
                # Jangan gabungkan kolom dengan ukuran huruf yang jauh
                # berbeda. Satu kolom judul besar sering menempel ke dialog
                # kecil di sebelahnya dan membuat kotak OCR raksasa.
                group_sizes = sorted(
                    l.height / max(1, len(re.sub(r"\s+", "", l.text)))
                    for l in g.lines
                )
                group_size = group_sizes[len(group_sizes) // 2]
                if min(line_size, group_size) / max(line_size, group_size) < 0.55:
                    continue
                # Kotak putih dan narasi putih di panel gelap bisa berdampingan
                # rapat. Jangan gabungkan baris yang luminans latarnya jauh
                # berbeda, supaya teks tidak tercampur dan pembersihan tidak
                # menambal area yang salah.
                if bg_by_line:
                    line_bg = bg_by_line.get(id(line), 255.0)
                    group_bg = float(np.median(
                        [bg_by_line.get(id(l), 255.0) for l in g.lines]
                    ))
                    if (line_bg > 200 and group_bg < 100) or (
                        line_bg < 100 and group_bg > 200
                    ):
                        continue
                    # Kolom teks putih di panel gelap sering berasal dari
                    # balon terpisah yang saling menempel. Jangan gabungkan
                    # bila ada jarak horizontal yang jelas.
                    if line_bg < 120 and xgap > 24:
                        continue
                    # Kalau dua kolom gelap berimpit, periksa celahnya. Garis
                    # putih tipis menandakan dua balon berbeda, bukan satu
                    # balon dengan dua kolom.
                    if (
                        line_bg < 120
                        and image is not None
                        and _gap_bg_lum(image, g.box, line.box) > 140
                    ):
                        continue
                if xgap < best_gap:
                    best_gap = xgap
                    best = g
            if best is not None and len(best.lines) < 8:
                best.lines.append(line)
                best.box = _union_box(best.lines)
            else:
                groups.append(TextGroup(lines=[line], box=line.box, text="", vertical=True))
        for g in groups:
            g.lines.sort(key=lambda l: (-(l.box[0] + l.box[2]), l.box[1]))
            g.text = _join_text(g.lines, vertical=True)
        return groups

    lines.sort(key=lambda l: (l.box[1], l.box[0]))
    for line in lines:
        lh = max(1, line.height)
        best = None
        best_score = 1e18
        for g in groups:
            gx0, gy0, gx1, gy1 = g.box
            gh = max(1, gy1 - gy0)
            x0, y0, x1, y1 = line.box
            vgap = max(0, y0 - gy1, gy0 - y1)
            overlap = max(0, min(x1, gx1) - max(x0, gx0))
            min_w = max(1, min(x1 - x0, gx1 - gx0))
            gap_limit = 0.9 * max(lh, gh / max(1, len(g.lines)))
            if vgap > gap_limit:
                continue
            if overlap < 0.22 * min_w and abs((x0 + x1) / 2 - (gx0 + gx1) / 2) > 0.55 * max(x1 - x0, gx1 - gx0):
                continue
            if min(lh, gh / len(g.lines)) / max(lh, gh / len(g.lines)) < 0.38:
                continue
            score = vgap + abs((x0 + x1) / 2 - (gx0 + gx1) / 2) * 0.15
            if score < best_score:
                best_score = score
                best = g
        if best is not None and len(best.lines) < 10:
            best.lines.append(line)
            best.box = _union_box(best.lines)
        else:
            groups.append(TextGroup(lines=[line], box=line.box, text="", vertical=False))
    out: list[TextGroup] = []
    for g in groups:
        if len(g.lines) == 1 and g.lines[0].vertical and g.box[3] - g.box[1] > 120:
            g.vertical = True
        g.text = _join_text(g.lines, g.vertical)
        if g.text:
            out.append(g)
    return out


def _region_overlap_ratio(a, b) -> float:
    ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
    ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    inter = (ix1 - ix0) * (iy1 - iy0)
    area_a = max(1, (a[2] - a[0]) * (a[3] - a[1]))
    area_b = max(1, (b[2] - b[0]) * (b[3] - b[1]))
    return inter / min(area_a, area_b)


def _contains_box(region, box) -> bool:
    return (
        region[0] <= box[0] + 4 and region[1] <= box[1] + 4
        and region[2] >= box[2] - 4 and region[3] >= box[3] - 4
    )


def _merge_bubble_groups(groups: list[TextGroup]) -> list[TextGroup]:
    """Gabungkan potongan kolom vertikal yang berada di balon yang sama.

    OCR kadang memecah satu balon menjadi dua grup karena ukuran huruf tiap
    kolom berbeda. Kalau keduanya menunjuk region balon yang sama dan kotaknya
    bersebelahan, keduanya adalah satu kalimat dan harus ditata sekali saja.
    """
    out: list[TextGroup] = []
    for g in groups:
        target = None
        for h in out:
            if not (g.vertical and h.vertical):
                continue
            if not (g.bubble or h.bubble):
                continue
            # Region balon bisa belum ada saat penggabungan: _find_bubble_regions
            # hanya menandai grup yang balonnya benar-benar terdeteksi. Untuk
            # grup tanpa region, pakai kotak OCR sebagai perkiraan area.
            g_region = g.region or g.box
            h_region = h.region or h.box
            same_region = bool(
                g.region and h.region
                and all(abs(g.region[i] - h.region[i]) <= 8 for i in range(4))
            )
            if g.region and h.region:
                # Dua balon yang saling menempel tetap punya region berbeda.
                # Hanya region yang hampir identik yang boleh digabung;
                # kalau tidak, teks akan keluar dari salah satu balon.
                same_bubble = same_region or _region_overlap_ratio(
                    g.region, h.region
                ) >= 0.9
            else:
                # Salah satu grup tidak punya region balon (OCR gagal
                # mendeteksinya). Gabungkan hanya bila kotaknya berada di
                # dalam region balon grup lain.
                same_bubble = (
                    _contains_box(g_region, h.box)
                    or _contains_box(h_region, g.box)
                )
            if not same_bubble:
                continue
            gx0, gy0, gx1, gy1 = g.box
            hx0, hy0, hx1, hy1 = h.box
            xgap = max(0, max(gx0, hx0) - min(gx1, hx1))
            ygap = max(0, max(gy0, hy0) - min(gy1, hy1))
            yoverlap = max(0, min(gy1, hy1) - max(gy0, hy0))
            min_h = max(1, min(gy1 - gy0, hy1 - hy0))
            if xgap > max(80, (gx1 - gx0 + hx1 - hx0) * 0.5):
                continue
            if ygap > 90 and yoverlap < min_h * 0.25:
                continue
            target = h
            break
        if target is None:
            out.append(g)
            continue
        target.lines.extend(g.lines)
        target.box = _union_box(target.lines)
        target.vertical = target.vertical or g.vertical
        target.bubble = bool(target.bubble or g.bubble)
        mask_a = target.extra.get("bubble_mask")
        mask_b = g.extra.get("bubble_mask")
        if mask_b is not None:
            target.extra["bubble_mask"] = mask_b if mask_a is None else (mask_a | mask_b)
        target.text = _join_text(target.lines, target.vertical)
        if g.region and target.region:
            target.region = (
                min(target.region[0], g.region[0]),
                min(target.region[1], g.region[1]),
                max(target.region[2], g.region[2]),
                max(target.region[3], g.region[3]),
            )
    return out


def _find_bubble_regions(image: np.ndarray, groups: list[TextGroup]) -> None:
    """Use the ink outlines as flood-fill barriers to find the bubble around a group."""
    h, w = image.shape[:2]
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    page_area = h * w
    try:
        barrier = (gray < 132).astype(np.uint8) * 255
        barrier = cv2.dilate(barrier, np.ones((3, 3), np.uint8), iterations=1)
        free = 255 - barrier
        num, labels, stats, _ = cv2.connectedComponentsWithStats(free, 4)
    except cv2.error:
        return

    for g in groups:
        gx0, gy0, gx1, gy1 = g.box
        gw, gh = max(1, gx1 - gx0), max(1, gy1 - gy0)
        counts: dict[int, int] = {}
        samples: list[tuple[int, int]] = []
        for line in g.lines:
            x0, y0, x1, y1 = line.box
            for pad in (4, 9, 14):
                samples.extend([
                    (x0 - pad, (y0 + y1) // 2), (x1 + pad, (y0 + y1) // 2),
                    ((x0 + x1) // 2, y0 - pad), ((x0 + x1) // 2, y1 + pad),
                    (x0 - pad, y0 - pad), (x1 + pad, y1 + pad),
                ])
        for x, y in samples:
            if 0 <= x < w and 0 <= y < h:
                lab = int(labels[y, x])
                if lab:
                    counts[lab] = counts.get(lab, 0) + 1
        candidates = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:10]
        best = None
        best_lab = 0
        best_score = 1e18
        for lab, _votes in candidates:
            x, y, bw, bh, area = (int(v) for v in stats[lab][:5])
            if area > page_area * 0.55 or area < gw * gh * 0.7:
                continue
            # The page background always touches the page border; speech bubbles do not.
            if x <= 2 or y <= 2 or x + bw >= w - 2 or y + bh >= h - 2:
                continue
            if not (x - 3 <= gx0 and y - 3 <= gy0 and x + bw + 3 >= gx1 and y + bh + 3 >= gy1):
                # Lettering may slightly overflow the bubble (vertical columns often do).
                ix0, iy0 = max(x, gx0), max(y, gy0)
                ix1, iy1 = min(x + bw, gx1), min(y + bh, gy1)
                covered = max(0, ix1 - ix0) * max(0, iy1 - iy0)
                if covered < gw * gh * 0.8:
                    continue
            if bw < gw * 0.95 or bh < gh * 0.95:
                continue
            margin_x = (bw - gw) / 2
            margin_y = (bh - gh) / 2
            if margin_x > max(320, gw * 4) or margin_y > max(240, gh * 0.7):
                continue
            score = area / max(1, gw * gh)
            if score < best_score:
                best_score = score
                best = (x, y, x + bw, y + bh)
                best_lab = int(lab)
        if best:
            inset = 2
            region = (best[0] + inset, best[1] + inset, best[2] - inset, best[3] - inset)
            region = (
                max(0, region[0]), max(0, region[1]),
                min(w, region[2]), min(h, region[3]),
            )
            if region[2] - region[0] >= gw and region[3] - region[1] >= gh:
                g.region = region
                g.bubble = True
                # Simpan topeng interior balon (lubang huruf belum diisi).
                # Dipakai untuk mengisi seluruh balon dengan warna latar saat
                # pembersihan, sehingga tidak ada tambalan persegi atau sisa
                # artwork yang membuat balon terlihat kotor.
                g.extra["bubble_mask"] = labels == best_lab


def detect_text(image: np.ndarray, src_lang: str, min_score: float = 0.5) -> tuple[list[TextGroup], str]:
    """Detect, merge, group and localise text. Returns groups and detected language."""
    engine = _get_engine(src_lang)
    h, w = image.shape[:2]
    long_side = max(h, w)
    lines: list[TextLine] = []

    if long_side <= 1950:
        lines = _run_ocr(engine, image, 0, 0)
    else:
        tile = 1400
        overlap = 140
        step = tile - overlap
        ys = list(range(0, max(1, h - overlap), step))
        xs = list(range(0, max(1, w - overlap), step))
        if not ys:
            ys = [0]
        if not xs:
            xs = [0]
        for y in ys:
            for x in xs:
                crop = image[y:min(h, y + tile), x:min(w, x + tile)]
                if crop.shape[0] < 20 or crop.shape[1] < 20:
                    continue
                if float(crop.std()) < 2.0:
                    continue
                lines.extend(_run_ocr(engine, crop, x, y))

    lines = [l for l in lines if l.score >= min_score]
    lines = _merge_duplicates(lines)
    lines = _merge_same_row(lines, w)
    lines = [l for l in lines if not _is_noise(l.text, l.box, w, h)]

    all_text = " ".join(l.text for l in lines[:80])
    from .languages import detect_script

    detected = detect_script(all_text)
    effective = detected if src_lang == "auto" else src_lang

    vertical_count = sum(1 for l in lines if l.vertical)
    vertical_mode = effective in ("ja", "zh", "zh-TW") and vertical_count >= max(2, len(lines) * 0.35)
    groups = _group_lines(lines, w, h, vertical_mode, image)
    groups = [g for g in groups if g.text and len(g.text.strip()) >= 1]
    if effective in ("ja", "zh", "zh-TW", "ko"):
        refined = 0
        for g in groups:
            if refined >= 8:
                break
            result = _refine_group_text(image, engine, g, w, h)
            if result:
                g.lines, g.box, g.text, g.vertical = result
                refined += 1
    _find_bubble_regions(image, groups)
    groups = _merge_bubble_groups(groups)

    for i, g in enumerate(groups):
        g.extra["id"] = f"r{i + 1:03d}"
        g.extra["detected_lang"] = effective
        g.extra["confidence"] = round(sum(l.score for l in g.lines) / max(1, len(g.lines)), 3)
        # Perkiraan ukuran huruf asli. Untuk teks vertikal, tinggi garis
        # dibagi jumlah karakter; untuk horizontal, tinggi baris itu sendiri.
        # Nilai median dipakai agar satu baris OCR yang meleset tidak
        # membuat teks terjemahan membesar berlebihan.
        sizes: list[float] = []
        for line in g.lines:
            if g.vertical:
                chars = max(1, len(re.sub(r"\s+", "", line.text)))
                sizes.append(line.height / chars)
            else:
                sizes.append(float(line.height))
        if sizes:
            sizes.sort()
            hint = int(round(sizes[len(sizes) // 2]))
            g.extra["font_hint"] = max(6, min(160, hint))
        if g.region is None:
            pad = max(6, int(min(g.box[2] - g.box[0], g.box[3] - g.box[1]) * 0.12))
            g.region = (
                max(0, g.box[0] - pad), max(0, g.box[1] - pad),
                min(w, g.box[2] + pad), min(h, g.box[3] + pad),
            )
    return groups, effective
