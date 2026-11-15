from __future__ import annotations

import json
import re
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from .inpaint import clean_page
from .languages import detect_script
from .ocr import detect_text
from .translate import CJK_LANGS, best_nllb_dir, translate_texts
from .typeset import render_page

STAGES = {
    "ocr": "Membaca teks",
    "translate": "Menerjemahkan",
    "clean": "Membersihkan balon",
    "render": "Menata huruf",
}


def _load_rgb(path: Path) -> np.ndarray:
    data = np.fromfile(str(path), dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if img is None:
        raise RuntimeError("Gambar tidak bisa dibaca.")
    return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)


def _thumb(src: Path, dst: Path, width: int = 420) -> None:
    with Image.open(src) as im:
        im = im.convert("RGB")
        ratio = width / max(1, im.width)
        size = (width, max(1, int(im.height * ratio)))
        im.thumbnail((width, 10000), Image.Resampling.LANCZOS)
        im.save(dst, "JPEG", quality=86, optimize=True)


def process_page(
    page_dir: Path,
    options: dict,
    progress=None,
) -> dict:
    """Run the full pipeline for one page. Returns serialisable metadata."""
    src_path = page_dir / "original.png"
    image = _load_rgb(src_path)
    h, w = image.shape[:2]

    if progress:
        progress(4, STAGES["ocr"])
    groups, detected = detect_text(
        cv2.cvtColor(image, cv2.COLOR_RGB2BGR),
        options.get("src_lang", "auto"),
        float(options.get("min_score", 0.5)),
    )

    src_used = detected if options.get("src_lang", "auto") == "auto" else options["src_lang"]
    target = options["tgt_lang"]
    texts = [g.text for g in groups]

    if progress:
        progress(35, STAGES["translate"])
    warnings: list[str] = []
    engine_requested = options.get("engine", "local")
    engine_used = engine_requested
    if texts and src_used != target:
        engine_for_page = engine_requested
        nllb_available = best_nllb_dir() is not None
        auto_nllb = (
            engine_requested == "local"
            and options.get("src_lang", "auto") == "auto"
            and src_used in CJK_LANGS
            and bool(options.get("fallback_local", True))
            and nllb_available
        )
        if auto_nllb:
            engine_for_page = "nllb"
        elif engine_requested == "local" and options.get("src_lang", "auto") == "auto" and src_used in CJK_LANGS and not nllb_available:
            warnings.append(
                "Teks CJK terdeteksi, tetapi model NLLB-200 belum tersedia. "
                "Argos/OPUS bisa kurang akurat untuk CJK; jalankan samples/download_nllb.py."
            )

        def tp(pct, message):
            if progress:
                progress(36 + int(pct * 0.24), message)

        translations, engine_warnings, engine_used = translate_texts(
            texts, src_used, target, engine_for_page,
            progress=tp, fallback_local=bool(options.get("fallback_local", True)),
            tone=options.get("tone", "santai"),
        )
        if auto_nllb and engine_used == "nllb":
            warnings.append("Teks CJK terdeteksi · otomatis memakai NLLB-200 agar hasil lebih akurat.")
        for warning in engine_warnings:
            if warning not in warnings:
                warnings.append(warning)
    else:
        translations = list(texts)

    active: list = []
    active_translations: list[str] = []
    for group, translated in zip(groups, translations):
        clean_translation = (translated or "").strip()
        original = group.text.strip()
        if not clean_translation:
            continue
        # If the engine could not change it (SFX, proper nouns), leave the original artwork alone.
        if re.sub(r"\W+", "", clean_translation).lower() == re.sub(r"\W+", "", original).lower():
            continue
        group.extra["translated"] = clean_translation
        active.append(group)
        active_translations.append(clean_translation)

    # Re-detect the script on the actual OCR output when the user chose auto.
    if options.get("src_lang") == "auto" and active:
        detected = detect_script(" ".join(g.text for g in active[:40]))
        src_used = detected

    if progress:
        progress(62, STAGES["clean"])
    clean, infos = clean_page(image, active)

    regions = []
    for group, info, translated in zip(active, infos, active_translations):
        regions.append({
            "id": group.extra.get("id", "r"),
            "box": [int(v) for v in group.box],
            "region": [int(v) for v in (group.region or group.box)],
            "region_auto": True,
            "ellipse": bool(group.bubble),
            "original": group.text,
            "translated": translated,
            "score": group.extra.get("confidence", 0),
            "font_hint": int(group.extra.get("font_hint") or 0),
            "vertical": bool(group.vertical),
            "bg_lum": round(float(info.get("bg_lum", 235)), 1),
            "bubble": bool(group.bubble or info.get("bubble", False)),
            "skipped": False,
        })

    clean_path = page_dir / "clean.png"
    Image.fromarray(clean).save(clean_path, "PNG", optimize=True)

    style = {
        "font": options.get("font", "comic"),
        "font_scale": float(options.get("font_scale", 1.0)),
        "text_color": options.get("text_color", "auto"),
        "outline": bool(options.get("outline", False)),
        "target_lang": target,
    }
    output_path = page_dir / "translated.png"
    if progress:
        progress(84, STAGES["render"])
    render_page(Image.fromarray(clean), regions, style, output_path)
    _thumb(output_path, page_dir / "translated_thumb.jpg")

    meta = {
        "width": w,
        "height": h,
        "src_lang": src_used,
        "engine": engine_used,
        "engine_requested": engine_requested,
        "tone": options.get("tone", "santai"),
        "warnings": warnings,
        "style": style,
        "regions": regions,
        "stats": {
            "detected": len(groups),
            "translated": len(regions),
        },
    }
    (page_dir / "regions.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    if progress:
        progress(100, "Selesai")
    return meta


def rerender_page(page_dir: Path, regions: list[dict], style: dict) -> dict:
    meta_path = page_dir / "regions.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    incoming_by_id = {
        str(item.get("id")): item
        for item in regions
        if item.get("id")
    }
    # Pertahankan semua region yang sudah ada. Permintaan render dari editor
    # hanya mengirim region yang berubah; region lain tidak boleh hilang dari
    # metadata karena teksnya sudah terhapus dari clean.png.
    merged = []
    for base in meta.get("regions", []):
        item = dict(base)
        incoming = incoming_by_id.get(str(base.get("id")))
        if incoming is not None:
            item["translated"] = str(
                incoming.get("translated", base.get("translated", ""))
            )[:2000]
            item["skipped"] = bool(incoming.get("skipped", False))
            if "region" in incoming:
                try:
                    box = [int(v) for v in incoming["region"]]
                    if len(box) == 4:
                        item["region"] = box
                        item["region_auto"] = False
                except Exception:  # noqa: BLE001
                    pass
        merged.append(item)
    # Region baru (misalnya dari alat tambah region) tetap diterima.
    known_ids = {str(r.get("id")) for r in merged}
    for incoming in regions:
        rid = str(incoming.get("id") or "")
        if not rid or rid in known_ids:
            continue
        item = {
            "id": rid,
            "translated": str(incoming.get("translated", ""))[:2000],
            "skipped": bool(incoming.get("skipped", False)),
        }
        if "region" in incoming:
            try:
                box = [int(v) for v in incoming["region"]]
                if len(box) == 4:
                    item["region"] = box
            except Exception:  # noqa: BLE001
                pass
        merged.append(item)
    meta["regions"] = merged
    meta["style"] = style
    clean = Image.open(page_dir / "clean.png").convert("RGB")
    out = page_dir / "translated.png"
    render_page(clean, merged, style, out)
    _thumb(out, page_dir / "translated_thumb.jpg")
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return meta
