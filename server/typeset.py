from __future__ import annotations

import math
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

import numpy as np

from .config import WEB_DIR

FONT_DIR = WEB_DIR / "fonts"
CJK_FONT_CANDIDATES = [
    "C:/Windows/Fonts/YuGothB.ttc",
    "C:/Windows/Fonts/meiryob.ttc",
    "C:/Windows/Fonts/msgothic.ttc",
    "C:/Windows/Fonts/msyhbd.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/malgunbd.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
]
SANS_FONT_CANDIDATES = [
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/segoeuib.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]
SERIF_FONT_CANDIDATES = [
    "C:/Windows/Fonts/georgiab.ttf",
    "C:/Windows/Fonts/timesbd.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
]
CJK_TARGETS = {"ja", "ko", "zh", "zh-TW"}

_FONT_CACHE: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}


def _first_existing(paths: list[str]) -> str | None:
    for p in paths:
        if Path(p).exists():
            return p
    return None


def font_path(kind: str, target: str) -> str:
    if target in CJK_TARGETS:
        p = _first_existing(CJK_FONT_CANDIDATES)
        if p:
            return p
    if kind == "sans":
        p = _first_existing(SANS_FONT_CANDIDATES)
        if p:
            return p
    if kind == "serif":
        p = _first_existing(SERIF_FONT_CANDIDATES)
        if p:
            return p
    if kind == "cjk":
        p = _first_existing(CJK_FONT_CANDIDATES)
        if p:
            return p
    comic = FONT_DIR / "ComicNeue-Bold.ttf"
    if comic.exists():
        return str(comic)
    p = _first_existing(SANS_FONT_CANDIDATES)
    if p:
        return p
    return str(comic)


def get_font(kind: str, target: str, size: int):
    path = font_path(kind, target)
    key = (path, size)
    if key not in _FONT_CACHE:
        _FONT_CACHE[key] = ImageFont.truetype(path, max(4, size))
    return _FONT_CACHE[key]


def _is_cjk(ch: str) -> bool:
    return bool(re.match(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af\uff00-\uffef]", ch))


def _wrap(
    text: str,
    font,
    max_width: float,
    draw: ImageDraw.ImageDraw,
    cjk: bool,
    break_words: bool = False,
) -> list[str]:
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []
    if cjk and not re.search(r"\s", text):
        lines: list[str] = []
        cur = ""
        for ch in text:
            trial = cur + ch
            if draw.textlength(trial, font=font) <= max_width or not cur:
                cur = trial
            else:
                lines.append(cur)
                cur = ch
        if cur:
            lines.append(cur)
        return lines

    words = text.split(" ")
    lines = []
    cur = ""
    for word in words:
        trial = (cur + " " + word).strip()
        trial_w = draw.textlength(trial, font=font)
        if trial_w <= max_width:
            cur = trial
        elif not cur:
            # Kata tunggal yang terlalu lebar. Jangan pecah di tengah kata;
            # biarkan pemanggil mengecilkan huruf sampai kata itu muat.
            if break_words:
                chunk = ""
                for ch in trial:
                    if (
                        draw.textlength(chunk + ch, font=font) <= max_width
                        or not chunk
                    ):
                        chunk += ch
                    else:
                        lines.append(chunk)
                        chunk = ch
                cur = chunk
            else:
                cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    if break_words:
        # Jalan terakhir untuk kotak yang sangat sempit: pecah baris yang
        # masih terlalu lebar agar teks tidak keluar dari kotak.
        broken: list[str] = []
        for line in lines:
            if not line:
                continue
            if draw.textlength(line, font=font) <= max_width:
                broken.append(line)
                continue
            chunk = ""
            for ch in line:
                if (
                    draw.textlength(chunk + ch, font=font) <= max_width
                    or not chunk
                ):
                    chunk += ch
                else:
                    broken.append(chunk)
                    chunk = ch
            if chunk:
                broken.append(chunk)
        lines = broken
    return [l for l in lines if l]


def _fit(draw, text, kind, target, region_w, region_h, scale, ellipse=False, cap=None, hint=None, hard_cap=64, mask=None, coverage_min=0.0):
    cjk = target in CJK_TARGETS
    # Keep a real margin inside the detected bubble/caption box. The old 5 px
    # margin let bold lettering touch the border, especially on tight bubbles.
    inset = max(3, min(14, int(min(region_w, region_h) * 0.07)))
    max_w = max(8, region_w - 2 * inset)
    max_h = max(8, region_h - 2 * inset)
    if ellipse:
        # Speech bubbles are usually detected slightly larger than their ink
        # area; tighten a bit more so text never crosses the curved border.
        max_w = max(8, int(max_w * 0.94))
        max_h = max(8, int(max_h * 0.94))

    short_side = min(region_w, region_h)
    # Do not let a short line become huge just because the bubble is large.
    # Caption boxes may pass `cap` explicitly; teks Latin tetap horizontal.
    size_cap = cap if cap is not None else short_side * 0.24
    # Jangan membuat teks jauh lebih besar dari huruf aslinya. Ini mencegah
    # satu balon pendek berisi huruf raksasa, terutama pada panel vertikal.
    if hint:
        size_cap = min(size_cap, float(hint) * 1.05)
    max_size = int(min(max_h, max_w, size_cap, hard_cap) * scale)
    max_size = max(6, min(96, max_size))
    # The old floor was 10 px for most bubbles, which forced text to overflow
    # instead of shrinking. Five pixels is still readable on a 1–2k page.
    min_size = 5

    def attempt(size: int):
        font = get_font(kind, target, size)
        lines = _wrap(text, font, max_w, draw, cjk)
        if not lines:
            return None
        widths = [draw.textlength(line, font=font) for line in lines]
        asc, desc = font.getmetrics()
        gap = max(1, int(size * 0.04))
        line_h = max(asc + desc, int(size * 1.12)) + gap
        total_h = line_h * len(lines) - gap
        if max(widths, default=0) > max_w + 1 or total_h > max_h + 1:
            return None
        if ellipse and not _fits_ellipse(widths, line_h, total_h, max_w, max_h):
            return None
        if mask is not None:
            coverage = _glyph_coverage(
                mask, font, lines, (0, 0, region_w, region_h), total_h, line_h
            )
            if coverage < coverage_min:
                return None
        return font, lines, line_h, total_h, size, True

    for size in range(max_size, min_size - 1, -1):
        best = attempt(size)
        if best:
            return best

    # Last resort for tiny regions: use the smallest legible size and let the
    # caller know the fit is tight. Most real bubbles never reach this path.
    font = get_font(kind, target, min_size)
    lines = _wrap(text, font, max_w, draw, cjk, break_words=True)
    asc, desc = font.getmetrics()
    gap = max(1, int(min_size * 0.04))
    line_h = max(asc + desc, int(min_size * 1.12)) + gap
    total_h = line_h * len(lines) - gap if lines else 0
    return font, lines, line_h, total_h, min_size, False


def _fits_ellipse(widths: list[float], line_h: float, total_h: float, box_w: int, box_h: int) -> bool:
    """Keep every line inside an ellipse inscribed in the region (speech bubbles)."""
    if total_h > box_h * 0.92:
        return False
    top = (box_h - total_h) / 2
    radius_x = box_w / 2
    radius_y = box_h / 2
    for i, width in enumerate(widths):
        y_top = top + line_h * i
        y_bottom = y_top + line_h
        for y in (y_top, y_bottom):
            rel = (y - radius_y) / radius_y
            chord = radius_x * (max(0.0, 1.0 - rel * rel) ** 0.5)
            if width / 2 > chord - 2:
                return False
    return True


def _background_mask(image: Image.Image, box: tuple[int, int, int, int]) -> np.ndarray | None:
    """Topeng area latar yang benar-benar putih/gelap di dalam region.

    Region balon hanya berupa kotak pembatas. Pada balon berbentuk angka
    delapan atau kotak caption bertakik, sebagian kotak itu bukan area balon.
    Topeng ini dipakai untuk memastikan blok teks tidak melewati lekukan.
    """
    x0, y0, x1, y1 = [int(v) for v in box]
    x0 = max(0, x0)
    y0 = max(0, y0)
    x1 = min(image.width, x1)
    y1 = min(image.height, y1)
    if x1 - x0 < 8 or y1 - y0 < 8:
        return None
    crop = np.asarray(image.crop((x0, y0, x1, y1)).convert("RGB"), dtype=np.int16)
    if crop.size == 0:
        return None
    median = np.median(crop.reshape(-1, 3), axis=0)
    mask = np.max(np.abs(crop - median), axis=2) <= 38
    # Erosi 2 px: sisakan margin agar teks tidak menempel garis balon.
    for _ in range(2):
        eroded = mask.copy()
        eroded[1:-1, 1:-1] &= (
            mask[:-2, 1:-1] & mask[2:, 1:-1] & mask[1:-1, :-2] & mask[1:-1, 2:]
        )
        mask = eroded
    if not mask.any():
        return None
    return mask


def _largest_rect(mask: np.ndarray) -> tuple[int, int, int, int] | None:
    """Cari persegi panjang terbesar yang seluruhnya berada di dalam topeng."""
    h, w = mask.shape
    heights = np.zeros(w, dtype=np.int32)
    best_area = 0
    best = None
    for y in range(h):
        heights = np.where(mask[y], heights + 1, 0)
        stack: list[tuple[int, int]] = []
        for x in range(w + 1):
            cur = int(heights[x]) if x < w else 0
            start = x
            while stack and stack[-1][1] > cur:
                idx, ht = stack.pop()
                area = ht * (x - idx)
                if area > best_area:
                    best_area = area
                    best = (idx, y - ht + 1, x, y + 1)
                start = idx
            if not stack or stack[-1][1] < cur:
                stack.append((start, cur))
    return best


def _mask_coverage(mask: np.ndarray, rect: tuple[float, float, float, float]) -> float:
    x0, y0, x1, y1 = [int(round(v)) for v in rect]
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(mask.shape[1], x1), min(mask.shape[0], y1)
    if x1 <= x0 or y1 <= y0:
        return 0.0
    part = mask[y0:y1, x0:x1]
    return float(part.mean()) if part.size else 0.0


def _lines_mask_coverage(
    mask: np.ndarray,
    draw: ImageDraw.ImageDraw,
    font,
    lines: list[str],
    box: tuple[int, int, int, int],
    total_h: float,
    line_h: float,
) -> float:
    """Coverage terburuk dari kotak tiap baris, bukan kotak gabungan.

    Kotak gabungan punya sudut kosong di atas/bawah sehingga bisa terlihat
    keluar balon padahal baris teksnya masih di dalam.
    """
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    top = y0 + (h - total_h) / 2
    worst = 1.0
    for i, line in enumerate(lines):
        lw = float(draw.textlength(line, font=font))
        lx = x0 + (w - lw) / 2
        ly = top + i * line_h
        coverage = _mask_coverage(
            mask, (lx - x0, ly - y0, lx - x0 + lw, ly - y0 + line_h)
        )
        if coverage < worst:
            worst = coverage
    return worst


def _glyph_coverage(
    mask: np.ndarray,
    font,
    lines: list[str],
    box: tuple[int, int, int, int],
    total_h: float,
    line_h: float,
) -> float:
    """Persentase piksel huruf yang benar-benar berada di area latar.

    Kotak baris masih menyertakan ruang kosong di sekitar huruf, sehingga
    balon yang sempit bisa terlihat "meluber" padahal hurufnya masih di dalam.
    Pemeriksaan ini memakai bentuk huruf aslinya.
    """
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    if w < 4 or h < 4 or not lines:
        return 0.0
    layer = Image.new("L", (w, h), 0)
    layer_draw = ImageDraw.Draw(layer)
    top = (h - total_h) / 2
    for i, line in enumerate(lines):
        tw = layer_draw.textlength(line, font=font)
        layer_draw.text(
            ((w - tw) / 2, top + i * line_h), line, font=font, fill=255
        )
    glyph = np.asarray(layer) > 64
    if not glyph.any():
        return 0.0
    return float(mask[glyph].mean())


def _integral(mask: np.ndarray) -> np.ndarray:
    ii = mask.astype(np.int32)
    return np.pad(ii.cumsum(0).cumsum(1), ((1, 0), (1, 0)))


def _fit_block(
    draw,
    text,
    kind,
    target,
    region_w,
    region_h,
    scale,
    mask: np.ndarray,
    cap=None,
    hint=None,
    hard_cap=64,
    pad=2,
):
    """Cari ukuran huruf terbesar yang blok teksnya muat di area latar.

    Berbeda dari ``_fit`` yang selalu menaruh teks di tengah kotak, fungsi ini
    mencari posisi bebas di dalam topeng. Balon berbentuk S atau angka delapan
    jadi bisa memakai lobus yang lebih lebar, bukan hanya persegi panjang
    terbesar yang sempit.
    """
    cjk = target in CJK_TARGETS
    inset = max(3, min(14, int(min(region_w, region_h) * 0.07)))
    max_w = max(8, region_w - 2 * inset)
    max_h = max(8, region_h - 2 * inset)
    short_side = min(region_w, region_h)
    size_cap = cap if cap is not None else short_side * 0.24
    if hint:
        size_cap = min(size_cap, float(hint) * 1.05)
    max_size = int(min(max_h, max_w, size_cap, hard_cap) * scale)
    max_size = max(5, min(96, max_size))
    min_size = 5
    ii = _integral(mask)
    height, width = mask.shape
    for size in range(max_size, min_size - 1, -1):
        font = get_font(kind, target, size)
        lines = _wrap(text, font, max_w, draw, cjk)
        if not lines:
            continue
        widths = [draw.textlength(line, font=font) for line in lines]
        asc, desc = font.getmetrics()
        gap = max(1, int(size * 0.04))
        line_h = max(asc + desc, int(size * 1.12)) + gap
        total_h = line_h * len(lines) - gap
        block_w = max(widths, default=0)
        bw = int(math.ceil(block_w)) + 2 * pad
        bh = int(math.ceil(total_h)) + 2 * pad
        if bw > width or bh > height:
            continue
        sums = (
            ii[bh:, bw:]
            - ii[:-bh, bw:]
            - ii[bh:, :-bw]
            + ii[:-bh, :-bw]
        )
        valid = np.argwhere(sums == bw * bh)
        if not valid.size:
            continue
        cx = (width - bw) / 2
        cy = (height - bh) / 2
        dist = (valid[:, 0] - cy) ** 2 + (valid[:, 1] - cx) ** 2
        y, x = valid[int(np.argmin(dist))]
        return (
            font,
            lines,
            line_h,
            total_h,
            size,
            (int(x) + pad, int(y) + pad),
            True,
        )
    return None





def _pick_text_color(image: Image.Image, box: tuple[int, int, int, int]) -> tuple[tuple[int, int, int], bool]:
    """Pick black/white lettering from the actual clean background.

    Kotak deteksi bisa berisi artwork gelap dan area terang sekaligus. Warna
    rata-rata lama sering memilih hitam di panel gelap sehingga teks hilang.
    Di sini warna dipilih dari median area tempat teks benar-benar digambar,
    dan latar campuran ditandai agar diberi garis tepi kontras.
    """
    x0, y0, x1, y1 = [int(v) for v in box]
    x0 = max(0, x0)
    y0 = max(0, y0)
    x1 = min(image.width, x1)
    y1 = min(image.height, y1)
    if x1 - x0 < 2 or y1 - y0 < 2:
        return (17, 17, 21), False
    gray = np.asarray(image.crop((x0, y0, x1, y1)).convert("L"), dtype=np.uint8)
    median = float(np.median(gray))
    dark = float(np.mean(gray < 100))
    light = float(np.mean(gray > 180))
    if median >= 145:
        color = (17, 17, 21)
    elif median <= 100:
        color = (248, 247, 242)
    else:
        color = (248, 247, 242) if dark >= light else (17, 17, 21)
    mixed = dark > 0.18 and light > 0.18
    return color, mixed


def render_page(clean: Image.Image, regions: list[dict], style: dict, out_path: Path) -> dict:
    image = clean.copy()
    draw = ImageDraw.Draw(image)
    kind = style.get("font", "comic")
    target = style.get("target_lang", "id")
    scale = float(style.get("font_scale", 1.0) or 1.0)
    scale = max(0.6, min(1.6, scale))
    color_mode = style.get("text_color", "auto")
    outline = bool(style.get("outline", False))
    widths = []

    for region in regions:
        if region.get("skipped"):
            continue
        text = (region.get("translated") or "").strip()
        if not text:
            continue
        source_box = region.get("box")
        bubble = bool(region.get("bubble"))
        # Caption/sidebar di luar balon harus tetap berada di kotak OCR-nya.
        # Region hasil deteksi balon sering jauh lebih besar dan membuat teks
        # meluber ke artwork di sekitarnya.
        if source_box and not bubble and region.get("region_auto", True):
            box = source_box
        else:
            box = region.get("region") or source_box
        if not box:
            continue
        x0, y0, x1, y1 = [int(v) for v in box]
        ellipse_flag = bool(region.get("ellipse")) and bubble
        if region.get("region_auto", True) and source_box and len(source_box) == 4:
            try:
                sx0, sy0, sx1, sy1 = [int(v) for v in source_box]
                sw, sh = sx1 - sx0, sy1 - sy0
                rw, rh = x1 - x0, y1 - y0
                # Bubble detection sometimes expands a small label into a huge
                # region that covers unrelated artwork. Fall back to the OCR
                # box with a small margin when the expansion is extreme.
                if sw > 0 and sh > 0 and rw * rh > sw * sh * 8:
                    pad_x = max(4, int(sw * 0.08))
                    pad_y = max(4, int(sh * 0.08))
                    x0, y0, x1, y1 = sx0 - pad_x, sy0 - pad_y, sx1 + pad_x, sy1 + pad_y
                    ellipse_flag = False
            except Exception:  # noqa: BLE001
                pass
        x0 = max(0, x0)
        y0 = max(0, y0)
        x1 = min(image.width, x1)
        y1 = min(image.height, y1)
        if x1 - x0 < 8 or y1 - y0 < 8:
            continue

        w = x1 - x0
        h = y1 - y0
        # Teks Latin selalu horizontal. Membaca Indonesia secara vertikal
        # dengan huruf diputar 90° sulit dan bukan gaya tata huruf manga
        # Indonesia. Untuk kotak sempit, teks dibuat beberapa baris.
        font, lines, line_h, total_h, size, fits = _fit(
            draw, text, kind, target, w, h, scale,
            ellipse=ellipse_flag,
            hint=int(region.get("font_hint") or 0),
        )

        # Region balon hanya kotak pembatas. Kalau bentuk aslinya berlekuk
        # (balon angka delapan atau caption bertakik), blok teks bisa keluar
        # dari area putih. Periksa topeng latar, lalu pilih penempatan dengan
        # huruf terbesar di antara beberapa kandidat:
        #   1. tetap di tengah region, huruf dikecilkan sampai masuk topeng;
        #   2. blok teks utuh di posisi bebas mana pun yang muat di topeng;
        #   3. persegi panjang terbesar di dalam topeng (cadangan).
        # Kandidat 2 penting untuk balon berbentuk S: lobus yang lebar bisa
        # memuat huruf jauh lebih besar daripada persegi panjang sempit.
        if bubble:
            mask = _background_mask(image, (x0, y0, x1, y1))
            if mask is not None and lines:
                coverage = _glyph_coverage(
                    mask, font, lines, (x0, y0, x1, y1), total_h, line_h
                )
                if coverage < 0.99:
                    hint = int(region.get("font_hint") or 0)
                    base_size = size
                    centered = _fit(
                        draw, text, kind, target, w, h, scale,
                        ellipse=ellipse_flag, hint=hint,
                        mask=mask, coverage_min=0.99,
                    )
                    if (
                        not centered[5]
                        or centered[4] < max(5, int(base_size * 0.55))
                    ):
                        centered = None
                    block = _fit_block(
                        draw, text, kind, target, w, h, scale, mask, hint=hint
                    )
                    shifted = None
                    safe = _largest_rect(mask)
                    if safe:
                        sx0, sy0, sx1, sy1 = safe
                        sw, sh = sx1 - sx0, sy1 - sy0
                        if (
                            sw >= 24
                            and sh >= 24
                            and sw >= w * 0.25
                            and sh >= h * 0.2
                        ):
                            shifted = _fit(
                                draw, text, kind, target, sw, sh, scale,
                                ellipse=False, hint=hint,
                                mask=mask[sy0:sy1, sx0:sx1],
                                coverage_min=0.99,
                            )
                            if not shifted[5]:
                                shifted = None
                    chosen = None
                    origin = None
                    block_place = None
                    # Pilih kandidat dengan huruf terbesar, bukan hanya
                    # membandingkan block dan centered. Balon angka delapan
                    # sering punya lobus sempit; kandidat "shifted" bisa
                    # memuat huruf jauh lebih besar daripada dua lainnya.
                    if block:
                        chosen, block_place = block, block[5]
                    if centered and (chosen is None or centered[4] >= chosen[4]):
                        chosen, block_place, origin = centered, None, None
                    if shifted and (
                        chosen is None
                        or shifted[4] > chosen[4]
                        or (shifted[4] == chosen[4] and chosen is block)
                    ):
                        chosen, origin, block_place = shifted, safe, None
                    if chosen is not None:
                        font, lines, line_h, total_h, size = chosen[:5]
                        fits = bool(chosen[-1])
                        if block_place:
                            bx, by = block_place
                            bw = int(math.ceil(
                                max(draw.textlength(line, font=font) for line in lines)
                            ))
                            x0, y0 = x0 + bx, y0 + by
                            x1, y1 = x0 + bw, y0 + int(math.ceil(total_h))
                            w, h = x1 - x0, y1 - y0
                            ellipse_flag = False
                        elif origin:
                            sx0, sy0, sx1, sy1 = origin
                            x0, y0 = x0 + sx0, y0 + sy0
                            x1, y1 = x0 + (sx1 - sx0), y0 + (sy1 - sy0)
                            w, h = x1 - x0, y1 - y0
                            ellipse_flag = False

        total_w = max(draw.textlength(line, font=font) for line in lines) if lines else 0
        widths.append(total_w)

        mixed_bg = False
        if color_mode == "black":
            color = (17, 17, 21)
        elif color_mode == "white":
            color = (248, 247, 242)
        else:
            sample_w = max(4, int(total_w) + 4)
            sample_h = max(4, int(total_h) + 4)
            sx = x0 + int((w - sample_w) / 2)
            sy = y0 + int((h - sample_h) / 2)
            color, mixed_bg = _pick_text_color(
                image, (sx, sy, sx + sample_w, sy + sample_h)
            )

        stroke_w = 0
        stroke_fill = None
        if outline or (color_mode == "auto" and mixed_bg):
            stroke_w = max(1, size // 16)
            stroke_fill = (250, 248, 240) if color[0] < 128 else (12, 12, 15)

        ty = y0 + (h - total_h) / 2
        for line in lines:
            tw = draw.textlength(line, font=font)
            tx = x0 + (w - tw) / 2
            draw.text(
                (tx, ty), line, font=font, fill=color,
                stroke_width=stroke_w, stroke_fill=stroke_fill,
            )
            ty += line_h

        region["rendered_size"] = size
        region["rendered_fit"] = bool(fits)
        region["rendered_vertical"] = False
        region["rendered_color"] = "#111115" if color[0] < 128 else "#f8f7f2"

    image.save(out_path, "PNG", optimize=True)
    return {
        "width": image.width,
        "height": image.height,
        "max_line_width": max(widths) if widths else 0,
    }
