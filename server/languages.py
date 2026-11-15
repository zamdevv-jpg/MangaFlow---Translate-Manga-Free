from __future__ import annotations

import json
import re
from functools import lru_cache

from .config import ARGOS_INDEX_FILE

# Canonical UI code -> Argos package code
TO_ARGOS = {
    "zh": "zh",
    "zh-TW": "zt",
    "pt-BR": "pb",
}
FROM_ARGOS = {v: k for k, v in TO_ARGOS.items()}

LANGUAGE_NAMES = {
    "id": "Indonesia",
    "en": "Inggris",
    "ms": "Melayu",
    "ja": "Jepang",
    "ko": "Korea",
    "zh": "Tionghoa (Sederhana)",
    "zh-TW": "Tionghoa (Tradisional)",
    "es": "Spanyol",
    "fr": "Prancis",
    "pt": "Portugis",
    "pt-BR": "Portugis (Brasil)",
    "de": "Jerman",
    "it": "Italia",
    "ru": "Rusia",
    "ar": "Arab",
    "th": "Thailand",
    "vi": "Vietnam",
    "tl": "Tagalog",
    "tr": "Turki",
    "pl": "Polandia",
    "nl": "Belanda",
    "sv": "Swedia",
    "da": "Denmark",
    "fi": "Finlandia",
    "nb": "Norwegia",
    "cs": "Ceko",
    "sk": "Slovakia",
    "sl": "Slovenia",
    "hu": "Hungaria",
    "ro": "Rumania",
    "bg": "Bulgaria",
    "el": "Yunani",
    "uk": "Ukraina",
    "he": "Ibrani",
    "hi": "Hindi",
    "bn": "Bengali",
    "ur": "Urdu",
    "fa": "Persia",
    "sq": "Albania",
    "az": "Azerbaijan",
    "eu": "Basque",
    "ca": "Katalan",
    "gl": "Galisia",
    "et": "Estonia",
    "lv": "Latvia",
    "lt": "Lituania",
    "ky": "Kyrgyz",
    "sw": "Swahili",
    "eo": "Esperanto",
    "ga": "Irlandia",
}

# Languages we can read from artwork without asking the user.
OCR_SOURCE_LANGS = ["auto", "en", "ja", "ko", "zh", "zh-TW", "es", "fr", "de", "pt", "id", "ms", "it", "vi", "tl"]

# RapidOCR recognition model to use for each source language.
OCR_MODEL_LANG = {
    "ja": "japan",
    "ko": "korean",
    "zh": "ch",
    "zh-TW": "chinese_cht",
}

POPULAR_TARGETS = ["id", "en", "ms", "ja", "ko", "zh", "es", "fr", "pt", "de", "ar", "ru", "th", "vi", "tl"]

# FLORES-200 / NLLB language codes for every language exposed by the UI.
NLLB_CODES = {
    "id": "ind_Latn",
    "en": "eng_Latn",
    "ms": "zsm_Latn",
    "ja": "jpn_Jpan",
    "ko": "kor_Hang",
    "zh": "zho_Hans",
    "zh-TW": "zho_Hant",
    "es": "spa_Latn",
    "fr": "fra_Latn",
    "pt": "por_Latn",
    "pt-BR": "por_Latn",
    "de": "deu_Latn",
    "it": "ita_Latn",
    "ru": "rus_Cyrl",
    "ar": "arb_Arab",
    "th": "tha_Thai",
    "vi": "vie_Latn",
    "tl": "tgl_Latn",
    "tr": "tur_Latn",
    "pl": "pol_Latn",
    "nl": "nld_Latn",
    "sv": "swe_Latn",
    "da": "dan_Latn",
    "fi": "fin_Latn",
    "nb": "nob_Latn",
    "cs": "ces_Latn",
    "sk": "slk_Latn",
    "sl": "slv_Latn",
    "hu": "hun_Latn",
    "ro": "ron_Latn",
    "bg": "bul_Cyrl",
    "el": "ell_Grek",
    "uk": "ukr_Cyrl",
    "he": "heb_Hebr",
    "hi": "hin_Deva",
    "bn": "ben_Beng",
    "ur": "urd_Arab",
    "fa": "pes_Arab",
    "sq": "als_Latn",
    "az": "azj_Latn",
    "eu": "eus_Latn",
    "ca": "cat_Latn",
    "gl": "glg_Latn",
    "et": "est_Latn",
    "lv": "lvs_Latn",
    "lt": "lit_Latn",
    "ky": "kir_Cyrl",
    "sw": "swh_Latn",
    "eo": "epo_Latn",
    "ga": "gle_Latn",
}


def nllb_code(code: str) -> str | None:
    return NLLB_CODES.get(canonical_code(code))


@lru_cache(maxsize=1)
def argos_index() -> dict[tuple[str, str], dict]:
    data = json.loads(ARGOS_INDEX_FILE.read_text(encoding="utf-8"))
    out: dict[tuple[str, str], dict] = {}
    for row in data:
        out[(row["from"], row["to"])] = row
    return out


def argos_code(code: str) -> str:
    return TO_ARGOS.get(code, code)


def canonical_code(code: str) -> str:
    return FROM_ARGOS.get(code, code)


def language_name(code: str) -> str:
    return LANGUAGE_NAMES.get(code, LANGUAGE_NAMES.get(canonical_code(code), code))


@lru_cache(maxsize=1)
def target_languages() -> list[dict]:
    idx = argos_index()
    codes = sorted({canonical_code(to) for (src, to) in idx if src == "en"}, key=lambda c: language_name(c))
    rows = [{"code": c, "name": language_name(c), "popular": c in POPULAR_TARGETS} for c in codes]
    rows.sort(key=lambda r: (not r["popular"], r["name"]))
    return rows


@lru_cache(maxsize=1)
def source_languages() -> list[dict]:
    rows = [{"code": c, "name": "Deteksi otomatis" if c == "auto" else language_name(c)} for c in OCR_SOURCE_LANGS]
    return rows


def pairs_for(src: str, tgt: str) -> list[tuple[str, str]]:
    """Return Argos hops. Everything pivots through English."""
    src, tgt = argos_code(src), argos_code(tgt)
    if src == tgt:
        return []
    if src == "en":
        return [("en", tgt)]
    if tgt == "en":
        return [(src, "en")]
    return [(src, "en"), ("en", tgt)]


def pair_info(src: str, tgt: str) -> dict | None:
    return argos_index().get((argos_code(src), argos_code(tgt)))


def ocr_lang_for(src: str, text: str = "") -> str:
    if src == "auto":
        src = detect_script(text)
    return OCR_MODEL_LANG.get(src, "en")


def detect_script(text: str) -> str:
    """Best-effort language guess for manga dialogue (only scripts we read)."""
    if not text:
        return "en"
    kana = len(re.findall(r"[\u3040-\u30ff]", text))
    hangul = len(re.findall(r"[\uac00-\ud7af\u1100-\u11ff]", text))
    han = len(re.findall(r"[\u4e00-\u9fff]", text))
    latin = len(re.findall(r"[A-Za-zÀ-ÿ]", text))
    total = kana + hangul + han + latin
    if total == 0:
        return "en"
    if kana / total > 0.12:
        return "ja"
    if hangul / total > 0.35:
        return "ko"
    if han / total > 0.5:
        return "zh"
    return "en"


def is_supported_target(code: str) -> bool:
    return any(row["code"] == code for row in target_languages())


def is_supported_source(code: str) -> bool:
    return code in OCR_SOURCE_LANGS


def model_pair_cached(src: str, tgt: str) -> bool:
    """Cheap check used by the UI before a job starts."""
    from pathlib import Path

    from .config import ARGOS_DIR

    for a, b in pairs_for(src, tgt):
        d = ARGOS_DIR / f"{a}_{b}"
        if not d.exists():
            return False
    return True
