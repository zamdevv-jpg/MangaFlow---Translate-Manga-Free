from __future__ import annotations

import cv2
import numpy as np

from .ocr import TextGroup


def _poly_mask(shape, lines, expand: int = 2) -> np.ndarray:
    mask = np.zeros(shape[:2], np.uint8)
    for line in lines:
        pts = np.array(line.points, dtype=np.int32)
        cv2.fillPoly(mask, [pts], 255)
    if expand > 0:
        mask = cv2.dilate(mask, np.ones((3, 3), np.uint8), iterations=expand)
    return mask


def _frame_mask(gray, box_w: int, box_h: int) -> np.ndarray:
    """Find long, thin ink lines that belong to a caption/panel frame.

    Kotak caption dan panel sering tepat bersinggungan dengan kotak OCR.
    Tanpa topeng ini, pengisian warna latar ikut menghapus garis pemisah dan
    dua caption yang tadinya terpisah terlihat menyatu.
    """
    dark = (gray < 175).astype(np.uint8)
    kernel = max(41, min(81, int(min(box_w, box_h) * 0.5)))
    horizontal = cv2.morphologyEx(
        dark, cv2.MORPH_OPEN, np.ones((1, kernel), np.uint8)
    )
    vertical = cv2.morphologyEx(
        dark, cv2.MORPH_OPEN, np.ones((kernel, 1), np.uint8)
    )
    lines = cv2.bitwise_or(horizontal, vertical)
    lines = (lines > 0).astype(np.uint8) * 255
    if lines.any():
        lines = cv2.dilate(lines, np.ones((3, 3), np.uint8), iterations=1)
    return lines


def clean_page(image: np.ndarray, groups: list[TextGroup]) -> tuple[np.ndarray, list[dict]]:
    """Remove original lettering while keeping the artwork and bubble outlines."""
    out = image.copy()
    h, w = image.shape[:2]
    infos: list[dict] = []

    for group in groups:
        gx0, gy0, gx1, gy1 = group.box
        bubble_mask = group.extra.get("bubble_mask")
        use_bubble_fill = bool(group.bubble and group.region and bubble_mask is not None)
        if use_bubble_fill:
            # Seluruh interior balon dibersihkan sekaligus, bukan hanya kotak
            # OCR. Ini mencegah tambalan persegi dan sisa tekstur di balon
            # semi-transparan.
            rx0, ry0, rx1, ry1 = [int(v) for v in group.region]
            rx0, ry0 = max(0, rx0 - 2), max(0, ry0 - 2)
            rx1, ry1 = min(w, rx1 + 2), min(h, ry1 + 2)
        else:
            pad = 8
            rx0, ry0 = max(0, gx0 - pad), max(0, gy0 - pad)
            rx1, ry1 = min(w, gx1 + pad), min(h, gy1 + pad)
        if rx1 - rx0 < 4 or ry1 - ry0 < 4:
            infos.append({"bubble": False, "bg_lum": 255, "fill": [255, 255, 255]})
            continue

        roi = out[ry0:ry1, rx0:rx1]
        gray = cv2.cvtColor(roi, cv2.COLOR_RGB2GRAY)

        local_lines = []
        for line in group.lines:
            pts = [[p[0] - rx0, p[1] - ry0] for p in line.points]
            local = type("L", (), {})()
            local.points = pts
            local_lines.append(local)
        line_mask = _poly_mask(roi.shape, local_lines, expand=1)
        frame = _frame_mask(gray, gx1 - gx0, gy1 - gy0)
        if frame.any():
            # Di dalam kotak teks, garis panjang adalah goresan huruf dan
            # harus ikut dibersihkan. Yang dilindungi hanya garis di tepi
            # atau di luar kotak, tempat border caption/panel berada.
            tx0 = max(0, gx0 - rx0)
            ty0 = max(0, gy0 - ry0)
            tx1 = min(roi.shape[1], gx1 - rx0)
            ty1 = min(roi.shape[0], gy1 - ry0)
            interior = np.zeros(roi.shape[:2], np.uint8)
            interior[ty0:ty1, tx0:tx1] = 255
            frame = cv2.bitwise_and(frame, cv2.bitwise_not(interior))

        if use_bubble_fill:
            bm = np.asarray(bubble_mask)[ry0:ry1, rx0:rx1]
            if bm.shape[:2] != roi.shape[:2]:
                bm = np.zeros(roi.shape[:2], bool)
            bm_u8 = (bm > 0).astype(np.uint8)
            contours, _ = cv2.findContours(
                bm_u8, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
            )
            bubble_area = np.zeros(roi.shape[:2], np.uint8)
            if contours:
                cv2.drawContours(bubble_area, contours, -1, 255, -1)
            # Jangan mengisi kontur terluar dari komponen putih. Komponen itu
            # bisa berbentuk cincin atau L yang mengelilingi caption lain,
            # artwork, atau balon sebelah. Mengisi konturnya akan menghapus
            # garis pemisah dan membuat dua kotak caption menyatu.
            #
            # Balon normal yang padat boleh diratakan seluruh interiornya agar
            # tidak ada tambalan persegi di dalam balon. Komponen yang menyatu
            # dengan artwork (soliditas rendah) tetap dibatasi ke kotak teks.
            pad = 3
            lx0 = max(0, gx0 - rx0 - pad)
            ly0 = max(0, gy0 - ry0 - pad)
            lx1 = min(roi.shape[1], gx1 - rx0 + pad)
            ly1 = min(roi.shape[0], gy1 - ry0 + pad)
            local = np.zeros(roi.shape[:2], np.uint8)
            local[ly0:ly1, lx0:lx1] = 255
            bubble_pixels = int((bm_u8 > 0).sum())
            contour_pixels = int((bubble_area > 0).sum())
            region_area = max(1, (rx1 - rx0) * (ry1 - ry0))
            solidity = bubble_pixels / max(1, contour_pixels)
            coverage = bubble_pixels / region_area
            whole_bubble = solidity >= 0.84 and coverage >= 0.6
            if whole_bubble:
                # Balon padat: ratakan seluruh interior, termasuk sisa artwork
                # atau garis jendela yang tembus di balon transparan. Erosi
                # kecil menjaga garis tepi balon tetap utuh.
                fill_mask = cv2.erode(
                    bubble_area, np.ones((3, 3), np.uint8), iterations=2
                )
            else:
                text_holes = cv2.dilate(
                    line_mask, np.ones((3, 3), np.uint8), iterations=1
                )
                text_holes = cv2.bitwise_and(
                    text_holes, cv2.bitwise_not(frame)
                )
                fill_mask = cv2.bitwise_or(bm_u8 * 255, text_holes)
                # Huruf besar kadang punya goresan di luar poligon OCR. Di
                # dalam kotak teks, piksel gelap adalah huruf; border tetap
                # dilindungi oleh topeng frame di atas.
                local_dark = (
                    (gray < 190) & (local > 0) & (bubble_area > 0)
                ).astype(np.uint8) * 255
                fill_mask = cv2.bitwise_or(fill_mask, local_dark)
                fill_mask = cv2.bitwise_and(fill_mask, local)
                fill_mask = cv2.bitwise_and(
                    fill_mask, cv2.bitwise_not(frame)
                )
            sample_bm = bm_u8 > 0
            if not whole_bubble:
                sample_bm = sample_bm & (local > 0)
            mask_area = int(sample_bm.sum())
            box_area = max(1, (gx1 - gx0) * (gy1 - gy0))
            if mask_area >= max(64, box_area * 0.2):
                bg_pixels = roi[sample_bm]
                if bg_pixels.size == 0:
                    bg_pixels = roi.reshape(-1, 3)
                bg_gray = cv2.cvtColor(
                    bg_pixels.reshape(-1, 1, 3), cv2.COLOR_RGB2GRAY
                ).reshape(-1)
                bg_lum = float(np.median(bg_gray))
                # Balon terang biasanya putih; ambil persentil atas agar
                # tekstur bangunan di belakang balon tidak membuat isinya abu.
                if bg_lum >= 170:
                    fill = np.percentile(bg_pixels, 75, axis=0).astype(np.uint8)
                else:
                    fill = np.median(bg_pixels, axis=0).astype(np.uint8)
                roi[fill_mask > 0] = fill
                infos.append({
                    "bubble": True,
                    "bg_lum": bg_lum,
                    "fill": [int(x) for x in fill],
                })
                continue

        vals = gray[line_mask > 0]
        if vals.size == 0:
            vals = gray.reshape(-1)

        dark_frac = float(np.mean(vals < 125))
        light_frac = float(np.mean(vals > 180))
        median = float(np.median(vals))
        # Kotak OCR untuk teks vertikal sering mencakup seluruh kolom, bukan
        # hanya goresan huruf. Karena itu polaritas teks ditentukan dari
        # dominasi latar: latar gelap berarti teks terang, dan sebaliknya.
        # Aturan lama salah menganggap teks putih di panel gelap sebagai teks
        # gelap, lalu menghapus hampir seluruh artwork di dalam kotaknya.
        if dark_frac > max(0.25, light_frac * 1.05):
            dark_text = False
        elif light_frac > 0.45:
            dark_text = True
        else:
            dark_text = median > 140

        # Kotak OCR untuk teks vertikal mencakup seluruh kolom, bukan hanya
        # goresan huruf. Kalau kotak itu dipakai sebagai mask untuk teks gelap,
        # shading dan artwork di dalam balon ikut terhapus dan menyisakan
        # tambalan putih persegi. Untuk teks gelap, batasi mask ke poligon
        # baris OCR. Kotak penuh hanya dipakai untuk teks terang di panel
        # gelap, tempat top-hat membutuhkan area pencarian lebih luas.
        if group.bubble:
            bw, bh = gx1 - gx0, gy1 - gy0
            ex = max(4, int(bw * 0.10))
            ey = max(4, int(bh * 0.10))
            bx0, by0 = gx0 - ex, gy0 - ey
            bx1, by1 = gx1 + ex, gy1 + ey
            if group.region:
                rx0r, ry0r, rx1r, ry1r = group.region
                bx0, by0 = max(bx0, rx0r + 3), max(by0, ry0r + 3)
                bx1, by1 = min(bx1, rx1r - 3), min(by1, ry1r - 3)
            if dark_text:
                mask_base = line_mask
            else:
                mask_base = np.zeros(roi.shape[:2], np.uint8)
                cv2.rectangle(
                    mask_base,
                    (max(0, bx0 - rx0), max(0, by0 - ry0)),
                    (min(roi.shape[1] - 1, bx1 - rx0), min(roi.shape[0] - 1, by1 - ry0)),
                    255,
                    -1,
                )
        else:
            mask_base = line_mask

        if dark_text:
            text_mask = ((gray < 185) & (mask_base > 0)).astype(np.uint8) * 255
        else:
            # Teks putih di atas artwork gelap. Ambang global ikut menghapus
            # highlight artwork yang luas, sedangkan kontras lokal bisa
            # meninggalkan huruf yang menempel pada glow terang. White top-hat
            # dengan kernel seukuran huruf hanya menyisakan goresan tipis.
            hint = int(group.extra.get("font_hint") or 24)
            k = max(9, min(151, hint * 2 + 1))
            if k % 2 == 0:
                k += 1
            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
            tophat = cv2.morphologyEx(gray, cv2.MORPH_TOPHAT, kernel)
            text_mask = (
                (tophat > 28) & (gray > 120) & (mask_base > 0)
            ).astype(np.uint8) * 255
            text_mask = cv2.morphologyEx(
                text_mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8)
            )
        text_mask = cv2.dilate(text_mask, np.ones((3, 3), np.uint8), iterations=3)
        # Garis kotak/panel tidak boleh ikut terhapus meskipun kotak OCR
        # bersinggungan dengan tepi caption.
        text_mask[frame > 0] = 0

        # Characterise the bubble background using pixels that are not lettering.
        bg_pixels = roi[(text_mask == 0)]
        if bg_pixels.size == 0:
            bg_pixels = roi.reshape(-1, 3)
        bg_gray = cv2.cvtColor(bg_pixels.reshape(-1, 1, 3), cv2.COLOR_RGB2GRAY).reshape(-1)
        bg_median = np.median(bg_pixels, axis=0)
        bg_lum = float(np.median(bg_gray))
        uniformity = float(np.mean(bg_gray > 165)) if dark_text else float(np.mean(bg_gray < 90))
        spread = float(np.std(bg_gray))

        # Flat bubbles look best with a clean fill; artwork text uses inpainting.
        clean_looking = uniformity > 0.72 and spread < 46
        # Teks putih di panel gelap: inpainting pada goresan lebar menarik
        # piksel putih dari huruf sebelahnya dan menyisakan kabut. Isi dengan
        # warna panel dominan agar huruf benar-benar hilang.
        if not dark_text and bg_lum < 120 and uniformity > 0.45:
            clean_looking = True
        if clean_looking:
            fill = np.median(bg_pixels, axis=0).astype(np.uint8)
            filled = roi.copy()
            filled[text_mask > 0] = fill
            alpha = cv2.GaussianBlur(text_mask.astype(np.float32) / 255.0, (0, 0), 1.1)[..., None]
            roi[:] = (filled * alpha + roi * (1 - alpha)).astype(np.uint8)
        else:
            roi[:] = cv2.inpaint(roi, text_mask, 4, cv2.INPAINT_TELEA)

        infos.append({
            "bubble": bool(clean_looking and dark_text),
            "bg_lum": bg_lum,
            "fill": [int(x) for x in bg_median],
        })

    return out, infos
