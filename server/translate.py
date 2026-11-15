from __future__ import annotations

import html
import json
import re
import threading
import time
import zipfile
from pathlib import Path

import ctranslate2
import requests
import sentencepiece as spm
import subword_nmt.apply_bpe as apply_bpe

from . import config
from .languages import argos_code, argos_index, detect_script, nllb_code, pairs_for

_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()
_MODEL_CACHE: dict[tuple[str, str], tuple[object, ctranslate2.Translator]] = {}
_TRANSLATION_CACHE: dict[tuple[str, str, str, str], tuple[str, str]] = {}
_CACHE_GUARD = threading.Lock()
_CACHE_LIMIT = 4000
_NLLB_LOCK = threading.Lock()
_NLLB_CACHE: dict[str, tuple[spm.SentencePieceProcessor, ctranslate2.Translator]] = {}
CJK_LANGS = {"ja", "ko", "zh", "zh-TW"}
ENGINE_LABELS = {
    "local": "Argos/OPUS",
    "nllb": "NLLB-200",
    "nllb-tuned": "NLLB-200 manga",
    "google": "Google Translate",
    "mymemory": "MyMemory",
    "libretranslate": "LibreTranslate",
    "ollama": "Ollama",
    "openai": "OpenAI-compatible",
    "deepl": "DeepL",
    "gemini": "Gemini",
    "microsoft": "Microsoft Translator",
}

# Light Indonesian style pass. NLLB is accurate but tends to write formal
# textbook Indonesian; manga dialogue sounds much more natural with these
# common swaps. It is deliberately conservative and only touches whole words.
TONE_RULES_SANTAI = [
    ("mulai ditayangkannya", "mulai tayang"),
    ("mulai ditayangkan", "mulai tayang"),
    ("siaran dimulai", "mulai tayang"),
    ("terima kasih", "makasih"),
    ("benar-benar", "bener-bener"),
    ("saat ini", "sekarang"),
    ("saya rasa", "kayaknya"),
    ("saya pikir", "kayaknya"),
    ("menurut saya", "menurutku"),
    ("bagi saya", "buatku"),
    ("untuk saya", "buatku"),
    ("maafkan saya", "maafin aku"),
    ("saya", "aku"),
    ("anda", "kamu"),
    ("beliau", "dia"),
    ("saudara", "kamu"),
    ("tidak", "nggak"),
    ("sudah", "udah"),
    ("telah", "udah"),
    ("sangat", "banget"),
    ("amat", "banget"),
    ("ingin", "pengen"),
    ("hendak", "mau"),
    ("dapat", "bisa"),
    ("tetapi", "tapi"),
    ("namun", "tapi"),
    ("melainkan", "tapi"),
    ("bagaimanakah", "gimana"),
    ("bagaimana", "gimana"),
    ("mengapa", "kenapa"),
    ("apakah", "apa"),
    ("siapakah", "siapa"),
    ("kapankah", "kapan"),
    ("dapatkah", "bisa"),
    ("bisakah", "bisa"),
    ("akankah", "akan"),
    ("haruskah", "harus"),
    ("inginkah", "mau"),
    ("maukah", "mau"),
    ("tidakkah", "nggak"),
    ("bukankah", "bukan"),
    ("sepertinya", "kayaknya"),
    ("sungguh", "beneran"),
    ("benarkah", "beneran"),
    ("benar", "bener"),
    ("kepada", "ke"),
    ("mengenai", "soal"),
    ("merupakan", "itu"),
    ("adalah", "itu"),
    ("tersebut", "itu"),
    ("yaitu", "itu"),
    ("melakukan", "lakuin"),
    ("mengetahui", "tahu"),
    ("melihat", "lihat"),
    ("mendengar", "dengar"),
    ("memberikan", "kasih"),
    ("mengatakan", "bilang"),
    ("berbicara", "ngomong"),
    ("memikirkan", "mikirin"),
    ("mengerti", "ngerti"),
    ("memahami", "paham"),
    ("membuat", "bikin"),
    ("membeli", "beli"),
    ("menjual", "jual"),
    ("mencari", "cari"),
    ("menemukan", "nemu"),
    ("mendapatkan", "dapet"),
    ("menggunakan", "pakai"),
    ("membutuhkan", "butuh"),
    ("memerlukan", "butuh"),
    ("menunggu", "nunggu"),
    ("memulai", "mulai"),
    ("menolong", "nolong"),
    ("membantu", "bantu"),
    ("maafkan", "maafin"),
    ("menyesal", "nyesel"),
    ("kemudian", "terus"),
    ("kini", "sekarang"),
    ("ketika", "pas"),
    ("saat", "waktu"),
    ("apabila", "kalau"),
    ("jikalau", "kalau"),
    ("jika", "kalau"),
    ("bila", "kalau"),
    ("meskipun", "walaupun"),
    ("agar", "biar"),
    ("supaya", "biar"),
    ("sebab", "soalnya"),
    ("sehingga", "sampai"),
    ("bagaikan", "kayak"),
    ("seperti", "kayak"),
    ("layaknya", "kayak"),
    ("ibarat", "kayak"),
    ("ditayangkan", "tayang"),
    ("sedang", "lagi"),
]

TONE_RULES_NETRAL = [
    ("terima kasih", "terima kasih"),
    ("saya", "aku"),
    ("anda", "kamu"),
    ("beliau", "dia"),
    ("saudara", "kamu"),
    ("tetapi", "tapi"),
    ("namun", "tapi"),
    ("melainkan", "tapi"),
    ("ingin", "mau"),
    ("hendak", "mau"),
    ("dapat", "bisa"),
    ("telah", "sudah"),
    ("mengenai", "tentang"),
    ("bagaimana", "bagaimana"),
    ("mengapa", "kenapa"),
    ("apakah", "apa"),
    ("siapakah", "siapa"),
    ("kemudian", "lalu"),
    ("meskipun", "walaupun"),
    ("agar", "supaya"),
    ("ditayangkan", "tayang"),
]


def _match_case(source: str, replacement: str) -> str:
    if source.isupper():
        return replacement.upper()
    if source[:1].isupper():
        return replacement[:1].upper() + replacement[1:]
    return replacement


def _casual_intensifier(match: re.Match) -> str:
    adjective = _match_case(match.group(2), match.group(2).lower())
    out = f"{adjective} banget"
    before = match.string[:match.start()].rstrip()
    if not before or before.endswith((".", "!", "?")):
        out = out[:1].upper() + out[1:]
    return out


def _lower_mid_sentence_pronouns(text: str) -> str:
    """Indonesian only capitalizes pronouns at the start of a sentence."""
    pronouns = r"(Aku|Kamu|Kau|Dia|Anda|Saya)"
    text = re.sub(
        rf"([,;:]\s+){pronouns}\b",
        lambda m: m.group(1) + m.group(2).lower(),
        text,
    )
    text = re.sub(
        rf"\b(Dan|Tapi|Karena|Kalau|Jika|Saat|Ketika|Waktu|Lalu|Terus|Namun)\s+{pronouns}\b",
        lambda m: m.group(1) + " " + m.group(2).lower(),
        text,
        flags=re.IGNORECASE,
    )
    return text


# Istilah yang sering salah diterjemahkan NLLB karena tidak ada konteks serial.
# Urutan penting: istilah yang lebih spesifik harus diperiksa lebih dulu.
JAPANESE_TERM_FIXES = [
    (
        "魔神族七曜",
        (
            "dewa setan, minggu",
            "dewa setan minggu",
            "dewa-dewi minggu",
        ),
        "Tujuh Dewa Iblis",
    ),
    (
        "魔神族",
        ("dewa-dewi", "dewa sihir", "dewa setan", "keluarga setan", "bangsa setan", "para dewa"),
        "klan iblis",
    ),
    # Nama dan istilah khusus serial. NLLB tidak mengenalnya, jadi hasilnya
    # sering berupa transliterasi acak ("Offucus", "Serpent Retrieval") atau
    # kata yang salah total ("Tunnel" untuk 霸道).
    (
        "オフューカス",
        ("offucus", "ofocus", "offukasu", "opuchus", "ophiuchus", "ophiucus", "o'focus"),
        "Ophiuchus",
    ),
    (
        "蛇遣い",
        (
            "serpent retrieval",
            "snake retrieval",
            "snake squad",
            "serpent bearer",
            "snake charmer",
            "of the serpent",
        ),
        "Pemegang Ular",
    ),
    (
        "十三星",
        (
            "the thirteen stars",
            "thirteen stars",
            "the thirteenth star",
            "thirteenth star",
            "13 stars",
            "13 star",
            "tiga belas bintang",
        ),
        "Tiga Belas Bintang",
    ),
    (
        "霸道",
        ("tunnel", "terowongan", "supreme way", "hado", "jalan agung"),
        "Jalan Agung",
    ),
]


def _apply_source_terms(source: str, translated: str) -> str:
    """Perbaiki istilah dunia yang maknanya sering salah di NLLB.

    NLLB tidak punya konteks serial, jadi 魔神族 bisa menjadi "dewa-dewi".
    Penggantian hanya dilakukan bila istilah sumbernya memang ada.
    """
    if not source or not translated:
        return translated
    for term, aliases, replacement in JAPANESE_TERM_FIXES:
        if term not in source:
            continue
        for alias in aliases:
            translated = re.sub(
                r"(?<!\w)" + re.escape(alias) + r"(?!\w)",
                replacement,
                translated,
                flags=re.IGNORECASE,
            )
    translated = re.sub(r"\s+([,.;:!?])", r"\1", translated)
    translated = re.sub(r",\s*,", ",", translated)
    translated = re.sub(r"\s{2,}", " ", translated)
    return translated


def _apply_tone(text: str, tgt: str, tone: str) -> str:
    text = (text or "").strip()
    if not text or tgt != "id" or tone == "baku":
        return text
    if tone == "netral":
        rules = TONE_RULES_NETRAL
    else:
        rules = TONE_RULES_SANTAI
        # Indonesian places the intensifier after the adjective in casual speech:
        # "sangat baik" -> "baik banget", not "banget baik".
        text = re.sub(
            r"\b(sangat|amat)\s+([A-Za-zÀ-ÿ]+)",
            _casual_intensifier,
            text,
            flags=re.IGNORECASE,
        )
        # Informal Indonesian usually drops these connectors entirely.
        text = re.sub(r"^bahwa\s+", "", text, flags=re.IGNORECASE)
        text = re.sub(r"(?<=\w)\s+bahwa\s+", " ", text, flags=re.IGNORECASE)
        text = re.sub(r"(?<=\w)\s+secara\s+", " ", text, flags=re.IGNORECASE)

    for pattern, replacement in rules:
        if pattern == replacement:
            continue
        regex = re.compile(
            rf"(?<![\w-]){re.escape(pattern)}(?![\w-])",
            flags=re.IGNORECASE,
        )
        text = regex.sub(lambda m: _match_case(m.group(0), replacement), text)

    text = _lower_mid_sentence_pronouns(text)
    text = re.sub(r"\s{2,}", " ", text).strip()
    return text


def _engine_label(engine: str) -> str:
    return ENGINE_LABELS.get(engine, engine)


class TranslationError(RuntimeError):
    pass


def _pair_lock(pair: str) -> threading.Lock:
    with _LOCKS_GUARD:
        if pair not in _LOCKS:
            _LOCKS[pair] = threading.Lock()
        return _LOCKS[pair]


def _find_model_dir(base: Path) -> Path | None:
    def valid(d: Path) -> bool:
        return (d / "model").is_dir() and ((d / "bpe.model").exists() or (d / "sentencepiece.model").exists())

    if valid(base):
        return base
    if not base.exists():
        return None
    for candidate in base.iterdir():
        if not candidate.is_dir():
            continue
        if valid(candidate):
            return candidate
        nested = _find_model_dir(candidate)
        if nested:
            return nested
    return None


def _download_model(src: str, tgt: str, progress=None) -> Path:
    pair = f"{src}_{tgt}"
    base = config.ARGOS_DIR / pair
    model_dir = _find_model_dir(base)
    if model_dir:
        return model_dir

    info = argos_index().get((src, tgt))
    if not info:
        raise TranslationError(f"Model lokal untuk {src} → {tgt} tidak tersedia.")
    if progress:
        progress(0, f"Mengunduh model {src} → {tgt}")

    url = info["url"]
    tmp = base.with_suffix(".part")
    base.mkdir(parents=True, exist_ok=True)
    try:
        with requests.get(url, stream=True, timeout=(15, 300)) as r:
            r.raise_for_status()
            total = int(r.headers.get("content-length") or 0)
            done = 0
            with open(tmp, "wb") as fh:
                for chunk in r.iter_content(chunk_size=1024 * 512):
                    if not chunk:
                        continue
                    fh.write(chunk)
                    done += len(chunk)
                    if progress and total:
                        progress(min(95, int(done * 95 / total)), f"Mengunduh model {src} → {tgt} · {done / 1e6:.0f}/{total / 1e6:.0f} MB")
        with zipfile.ZipFile(tmp) as zf:
            bad = zf.testzip()
            if bad:
                raise TranslationError(f"Arsip model rusak: {bad}")
            zf.extractall(base)
        tmp.unlink(missing_ok=True)
        model_dir = _find_model_dir(base)
        if not model_dir:
            raise TranslationError("Model terunduh, tetapi struktur paket tidak dikenali.")
        if progress:
            progress(100, f"Model {src} → {tgt} siap")
        return model_dir
    except requests.RequestException as exc:
        tmp.unlink(missing_ok=True)
        raise TranslationError(
            f"Gagal mengunduh model {src}→{tgt}. Periksa koneksi internet sekali saja, "
            "atau pilih mesin terjemahan lain."
        ) from exc
    except zipfile.BadZipFile as exc:
        tmp.unlink(missing_ok=True)
        raise TranslationError("Unduhan model tidak valid (bukan ZIP). Coba lagi.") from exc


def _load_pair(src: str, tgt: str, progress=None):
    key = (src, tgt)
    if key in _MODEL_CACHE:
        return _MODEL_CACHE[key]
    with _pair_lock(f"{src}_{tgt}"):
        if key in _MODEL_CACHE:
            return _MODEL_CACHE[key]
        model_dir = _download_model(src, tgt, progress=progress)
        try:
            if (model_dir / "bpe.model").exists():
                bpe = apply_bpe.BPE(open(model_dir / "bpe.model", encoding="utf-8"))

                class _BpeTok:
                    def encode(self, text):
                        return bpe.process_line(text).split()

                    def decode(self, tokens):
                        return " ".join(tokens).replace("@@ ", "")

                tokenizer = _BpeTok()
            else:
                sp = spm.SentencePieceProcessor()
                sp.Load(str(model_dir / "sentencepiece.model"))

                class _SpTok:
                    def encode(self, text):
                        return sp.EncodeAsPieces(text)

                    def decode(self, tokens):
                        return sp.DecodePieces(tokens)

                tokenizer = _SpTok()
            translator = ctranslate2.Translator(str(model_dir / "model"), device="cpu")
        except Exception as exc:  # noqa: BLE001
            raise TranslationError(f"Model {src}→{tgt} gagal dimuat: {exc}") from exc
        _MODEL_CACHE[key] = (tokenizer, translator)
        return tokenizer, translator


def _clean_translation(text: str, src: str, tgt: str) -> str:
    text = (text or "").strip()
    text = re.sub(r"\s+([,.;:!?%)\]}”’])", r"\1", text)
    text = re.sub(r"([(\[{“‘])\s+", r"\1", text)
    text = re.sub(r"\s+([，。！？；：])", r"\1", text)
    text = re.sub(r"([（【「『])\s+", r"\1", text)
    text = re.sub(r"\.{2,}", "...", text)
    text = re.sub(r"\s{2,}", " ", text).strip()
    if tgt in ("zh", "zh-TW", "ja"):
        text = re.sub(r"(?<=[\u4e00-\u9fff。！？、，])\s+(?=[\u4e00-\u9fff])", "", text)
    if text:
        m = re.search(r"[A-Za-zÀ-ÿ]", text)
        if m:
            i = m.start()
            text = text[:i] + text[i].upper() + text[i + 1:]
    return text.strip()


def _translate_pair(texts: list[str], src: str, tgt: str, progress=None) -> list[str]:
    tokenizer, translator = _load_pair(src, tgt, progress=progress)
    encoded = [tokenizer.encode(text) for text in texts]
    results: list[str] = []
    batch_size = 24
    for start in range(0, len(encoded), batch_size):
        batch = encoded[start:start + batch_size]
        try:
            out = translator.translate_batch(
                batch,
                beam_size=2,
                max_decoding_length=256,
                max_batch_size=24,
                batch_type="examples",
            )
        except Exception as exc:  # noqa: BLE001
            raise TranslationError(f"Mesin lokal gagal menerjemahkan: {exc}") from exc
        for item in out:
            tokens = item.hypotheses[0] if item.hypotheses else []
            results.append(tokenizer.decode(tokens))
    return results


def local_translate(texts: list[str], src: str, tgt: str, progress=None) -> list[str]:
    src, tgt = argos_code(src), argos_code(tgt)
    if src == tgt:
        return list(texts)
    hops = pairs_for(src, tgt)
    current = list(texts)
    total_hops = max(1, len(hops))
    for idx, (a, b) in enumerate(hops):
        def hop_progress(pct: int, message: str, idx=idx):
            if progress:
                overall = int(((idx + pct / 100) / total_hops) * 100)
                progress(overall, message)

        current = _translate_pair(current, a, b, progress=hop_progress)
    return [_clean_translation(t, src, tgt) for t in current]


def _min_model_bytes(base: Path) -> int:
    try:
        if Path(base).resolve() == Path(config.NLLB_LARGE_MODEL_DIR).resolve():
            return 1_200_000_000
    except OSError:
        pass
    return 300_000_000


def nllb_ready(model_dir: Path | None = None) -> bool:
    base = Path(model_dir) if model_dir else config.NLLB_MODEL_DIR
    model_bin = base / "model.bin"
    try:
        if model_bin.stat().st_size < _min_model_bytes(base):
            return False
    except OSError:
        return False
    return all((base / name).exists() for name in ("sentencepiece.bpe.model", "config.json")) and (
        (base / "shared_vocabulary.txt").exists()
        or (base / "shared_vocabulary.json").exists()
    )


def nllb_tuned_ready() -> bool:
    return nllb_ready(config.NLLB_TUNED_MODEL_DIR)


def nllb_large_ready() -> bool:
    return nllb_ready(config.NLLB_LARGE_MODEL_DIR)


def manga_nllb_dir() -> Path | None:
    """Model manga: hasil latihan kalau ada, lalu NLLB-200 1.3B."""
    if nllb_tuned_ready():
        return config.NLLB_TUNED_MODEL_DIR
    if nllb_large_ready():
        return config.NLLB_LARGE_MODEL_DIR
    return None


def best_nllb_dir() -> Path | None:
    """Model kualitas terbaik yang tersedia untuk fallback lokal CJK."""
    manga = manga_nllb_dir()
    if manga is not None:
        return manga
    if nllb_ready():
        return config.NLLB_MODEL_DIR
    return None


def _nllb_components(model_dir: Path | None = None, progress=None):
    base = Path(model_dir) if model_dir else (best_nllb_dir() or config.NLLB_MODEL_DIR)
    key = str(base.resolve())
    if key in _NLLB_CACHE:
        return _NLLB_CACHE[key]
    with _NLLB_LOCK:
        if key in _NLLB_CACHE:
            return _NLLB_CACHE[key]
        if not nllb_ready(base):
            raise TranslationError(
                "Model NLLB-200 belum terunduh. Jalankan samples/download_nllb.py sekali, "
                "atau pilih mesin lokal Argos."
            )
        label = "NLLB-200 hasil latihan" if base == config.NLLB_TUNED_MODEL_DIR else (
            "NLLB-200 1.3B" if base == config.NLLB_LARGE_MODEL_DIR else "NLLB-200"
        )
        if progress:
            progress(2, f"Memuat {label}…")
        try:
            sp = spm.SentencePieceProcessor()
            sp.Load(str(base / "sentencepiece.bpe.model"))
            translator = ctranslate2.Translator(str(base), device="cpu", compute_type="int8")
        except Exception as exc:  # noqa: BLE001
            raise TranslationError(f"Model {label} gagal dimuat: {exc}") from exc
        _NLLB_CACHE[key] = (sp, translator)
        if progress:
            progress(8, f"{label} siap")
        return _NLLB_CACHE[key]


def _fix_japanese_source(text: str) -> str:
    """Perbaiki istilah Jepang yang sering terpotong atau salah dibaca OCR."""
    if not text:
        return text
    # Vokal panjang pada kata serapan kadang hilang (アバター -> アバタ).
    # NLLB lalu membaca アバタ sebagai "pockmark" dan menghasilkan arti salah.
    text = re.sub(r"アバタ(?!ー)", "アバター", text)
    # NLLB tidak mengenal nama Ophiuchus dan sering menghasilkan "Offucus"
    # atau kalimat acak. Bentuk Latin membuatnya disalin apa adanya.
    text = text.replace("オフューカス", "Ophiuchus")
    # Ejaan 蛇遣い kurang umum di tokenizer NLLB; 蛇使い lebih mudah dibaca.
    text = text.replace("蛇遣い", "蛇使い")
    return text


def nllb_translate(texts: list[str], src: str, tgt: str, progress=None, model_dir: Path | None = None) -> list[str]:
    if src == "auto":
        src = detect_script(" ".join(texts[:20]))
    src_code, tgt_code = nllb_code(src), nllb_code(tgt)
    if not src_code or not tgt_code:
        raise TranslationError(f"NLLB-200 belum mendukung pasangan {src} → {tgt}.")
    if src_code == tgt_code:
        return list(texts)

    base = Path(model_dir) if model_dir else best_nllb_dir()
    if base is None:
        raise TranslationError(
            "Model NLLB-200 belum terunduh. Jalankan samples/download_nllb.py sekali, "
            "atau pilih mesin lokal Argos."
        )
    sp, translator = _nllb_components(model_dir=base, progress=progress)
    indexed = [(i, text) for i, text in enumerate(texts) if (text or "").strip()]
    if not indexed:
        return ["" for _ in texts]

    results: list[str] = ["" for _ in texts]
    batch_size = 8
    total = len(indexed)
    for start in range(0, total, batch_size):
        batch = indexed[start:start + batch_size]
        encoded = [[src_code] + sp.encode(_fix_japanese_source(text), out_type=str) + ["</s>"] for _, text in batch]
        if progress:
            done = start
            progress(10 + int(done / total * 88), f"NLLB · {done}/{total} baris")
        try:
            outputs = translator.translate_batch(
                encoded,
                target_prefix=[[tgt_code] for _ in batch],
                beam_size=5,
                max_decoding_length=192,
                max_batch_size=batch_size,
                batch_type="examples",
                no_repeat_ngram_size=3,
                repetition_penalty=1.10,
            )
        except Exception as exc:  # noqa: BLE001
            raise TranslationError(f"NLLB-200 gagal menerjemahkan: {exc}") from exc
        for (index, _text), item in zip(batch, outputs):
            tokens = item.hypotheses[0] if item.hypotheses else []
            if tokens and tokens[0] == tgt_code:
                tokens = tokens[1:]
            results[index] = sp.DecodePieces(tokens)
    if progress:
        progress(100, "NLLB · selesai")
    return [_clean_translation(t, src, tgt) for t in results]


def _google(texts: list[str], src: str, tgt: str, session: requests.Session) -> list[str]:
    out = []
    for text in texts:
        params = {
            "client": "gtx",
            "sl": "auto" if src == "auto" else src,
            "tl": tgt,
            "dt": "t",
            "q": text,
        }
        r = session.get("https://translate.googleapis.com/translate_a/single", params=params, timeout=20)
        if r.status_code == 429:
            raise TranslationError("Google Translate sedang membatasi permintaan (429).")
        r.raise_for_status()
        data = r.json()
        translated = "".join(segment[0] for segment in (data[0] or []) if segment and segment[0])
        if not translated:
            raise TranslationError("Google Translate tidak mengembalikan hasil.")
        out.append(translated)
    return out


def _mymemory(texts: list[str], src: str, tgt: str, session: requests.Session) -> list[str]:
    out = []
    for text in texts:
        r = session.get(
            "https://api.mymemory.translated.net/get",
            params={"q": text, "langpair": f"{src}|{tgt}"},
            timeout=20,
        )
        r.raise_for_status()
        data = r.json()
        translated = (data.get("responseData") or {}).get("translatedText") or ""
        translated = html.unescape(translated)
        if "MYMEMORY WARNING" in translated.upper() or data.get("quotaFinished"):
            raise TranslationError("Kuota MyMemory habis. Gunakan mesin lokal tanpa limit.")
        if not translated:
            raise TranslationError("MyMemory tidak mengembalikan hasil.")
        out.append(translated)
    return out


DEEPL_SOURCE = {
    "id": "ID", "en": "EN", "ja": "JA", "ko": "KO", "zh": "ZH", "zh-TW": "ZH",
    "es": "ES", "fr": "FR", "pt": "PT", "pt-BR": "PT", "de": "DE", "it": "IT",
    "ru": "RU", "ar": "AR", "tr": "TR", "pl": "PL", "nl": "NL", "sv": "SV",
    "da": "DA", "fi": "FI", "nb": "NB", "cs": "CS", "sk": "SK", "sl": "SL",
    "hu": "HU", "ro": "RO", "bg": "BG", "el": "EL", "uk": "UK", "et": "ET",
    "lv": "LV", "lt": "LT",
}
DEEPL_TARGET = {
    **DEEPL_SOURCE,
    "en": "EN-US",
    "pt": "PT-PT",
    "pt-BR": "PT-BR",
    "zh": "ZH-HANS",
    "zh-TW": "ZH-HANT",
}

AZURE_LANG = {
    "zh": "zh-Hans",
    "zh-TW": "zh-Hant",
    "pt-BR": "pt",
}


def _deepl(texts: list[str], src: str, tgt: str, session: requests.Session) -> list[str]:
    if not config.DEEPL_API_KEY:
        raise TranslationError("DEEPL_API_KEY belum diatur.")
    source = DEEPL_SOURCE.get(src)
    target = DEEPL_TARGET.get(tgt)
    if src != "auto" and not source:
        raise TranslationError(f"DeepL belum mendukung bahasa sumber {src}.")
    if not target:
        raise TranslationError(f"DeepL belum mendukung bahasa target {tgt}.")
    url = config.DEEPL_API_URL
    if not url:
        url = "https://api-free.deepl.com/v2/translate" if config.DEEPL_API_KEY.endswith(":fx") else "https://api.deepl.com/v2/translate"
    headers = {"Authorization": f"DeepL-Auth-Key {config.DEEPL_API_KEY}"}
    out: list[str] = []
    for start in range(0, len(texts), 50):
        chunk = texts[start:start + 50]
        payload: dict = {
            "text": chunk,
            "target_lang": target,
            "context": "Manga dialogue. Natural, concise, keeps the speaker's tone.",
        }
        if source:
            payload["source_lang"] = source
        r = session.post(url, data=payload, headers=headers, timeout=90)
        if r.status_code in (403, 456):
            raise TranslationError("Kunci DeepL tidak valid atau kuota karakter habis.")
        if r.status_code == 429:
            raise TranslationError("DeepL sedang membatasi permintaan (429).")
        if r.status_code >= 400:
            raise TranslationError(f"DeepL error {r.status_code}: {r.text[:200]}")
        data = r.json()
        rows = data.get("translations") or []
        if len(rows) != len(chunk):
            raise TranslationError("Jumlah hasil DeepL tidak cocok dengan jumlah baris.")
        out.extend(str(row.get("text") or "") for row in rows)
    return out


def _microsoft(texts: list[str], src: str, tgt: str, session: requests.Session) -> list[str]:
    if not config.AZURE_TRANSLATOR_KEY:
        raise TranslationError("AZURE_TRANSLATOR_KEY belum diatur.")
    params = {"api-version": "3.0", "to": AZURE_LANG.get(tgt, tgt)}
    if src != "auto":
        params["from"] = AZURE_LANG.get(src, src)
    headers = {
        "Ocp-Apim-Subscription-Key": config.AZURE_TRANSLATOR_KEY,
        "Content-Type": "application/json",
    }
    if config.AZURE_TRANSLATOR_REGION:
        headers["Ocp-Apim-Subscription-Region"] = config.AZURE_TRANSLATOR_REGION
    out: list[str] = []
    for start in range(0, len(texts), 50):
        chunk = texts[start:start + 50]
        r = session.post(
            config.AZURE_TRANSLATOR_ENDPOINT + "/translate",
            params=params,
            headers=headers,
            json=[{"text": text} for text in chunk],
            timeout=90,
        )
        if r.status_code in (401, 403):
            raise TranslationError("Kunci Microsoft Translator tidak valid.")
        if r.status_code == 429:
            raise TranslationError("Microsoft Translator sedang membatasi permintaan (429).")
        if r.status_code >= 400:
            raise TranslationError(f"Microsoft Translator error {r.status_code}: {r.text[:200]}")
        rows = r.json()
        if not isinstance(rows, list) or len(rows) != len(chunk):
            raise TranslationError("Respons Microsoft Translator tidak dikenali.")
        out.extend(str((row.get("translations") or [{}])[0].get("text") or "") for row in rows)
    return out


def _libretranslate(texts: list[str], src: str, tgt: str, session: requests.Session) -> list[str]:
    if not config.LIBRETRANSLATE_URL:
        raise TranslationError("LIBRETRANSLATE_URL belum diatur.")
    payload = {
        "q": texts,
        "source": src,
        "target": tgt,
        "format": "text",
    }
    if config.LIBRETRANSLATE_API_KEY:
        payload["api_key"] = config.LIBRETRANSLATE_API_KEY
    r = session.post(config.LIBRETRANSLATE_URL + "/translate", json=payload, timeout=90)
    if r.status_code >= 400:
        raise TranslationError(f"LibreTranslate error {r.status_code}: {r.text[:200]}")
    data = r.json()
    if isinstance(data, dict):
        data = data.get("translatedText")
    if isinstance(data, str):
        return [data]
    if not isinstance(data, list) or len(data) != len(texts):
        raise TranslationError("Respons LibreTranslate tidak dikenali.")
    return data


LLM_SYSTEM = (
    "You are a professional manga letterer and translator. Translate each line of dialogue "
    "naturally, keeping the speaker's tone, slang, and comic timing. Keep translations short "
    "enough to fit a speech bubble. Never add commentary. Return ONLY a JSON array of strings "
    "with exactly the same number of items and the same order as the input array."
)


def _parse_json_array(content: str, expected: int) -> list[str] | None:
    content = content.strip()
    content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.MULTILINE).strip()
    candidates = [content]
    m = re.search(r"\[[\s\S]*\]", content)
    if m:
        candidates.append(m.group(0))
    for cand in candidates:
        try:
            data = json.loads(cand)
            if isinstance(data, list) and len(data) == expected:
                return [str(x) for x in data]
        except Exception:  # noqa: BLE001
            continue
    return None


def _style_hint(tgt: str, tone: str) -> str:
    if tgt != "id":
        return "Keep the translation natural and idiomatic for the target language."
    if tone == "santai":
        return (
            "Write casual, everyday Indonesian as spoken by young people in manga. "
            "Do not use formal or textbook wording (no 'saya', 'tidak', 'sudah', 'sangat', "
            "'tersebut', 'merupakan'). Prefer 'aku', 'nggak', 'udah', 'banget'. "
            "Keep it short enough for a speech bubble."
        )
    if tone == "netral":
        return "Write clear, neutral Indonesian that is not stiff or overly formal."
    return "Write formal, standard Indonesian (bahasa baku)."


def _llm_user_prompt(texts: list[str], src: str, tgt: str, tone: str) -> str:
    src_name = "the source language" if src == "auto" else src
    return (
        f"Source language: {src_name}. Target language: {tgt}.\n"
        f"Style: {_style_hint(tgt, tone)}\n"
        f"Lines:\n{json.dumps(texts, ensure_ascii=False)}"
    )


def _gemini(texts: list[str], src: str, tgt: str, tone: str, session: requests.Session) -> list[str]:
    if not config.GEMINI_API_KEY:
        raise TranslationError("GEMINI_API_KEY belum diatur.")
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{config.GEMINI_MODEL}:generateContent"
    )
    payload = {
        "systemInstruction": {"parts": [{"text": LLM_SYSTEM}]},
        "contents": [{"role": "user", "parts": [{"text": _llm_user_prompt(texts, src, tgt, tone)}]}],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 4096},
    }
    r = session.post(url, params={"key": config.GEMINI_API_KEY}, json=payload, timeout=120)
    if r.status_code in (401, 403):
        raise TranslationError("Kunci Gemini tidak valid.")
    if r.status_code == 429:
        raise TranslationError("Kuota Gemini sedang habis atau dibatasi (429).")
    if r.status_code >= 400:
        raise TranslationError(f"Gemini error {r.status_code}: {r.text[:240]}")
    data = r.json()
    candidates = data.get("candidates") or []
    parts = ((candidates[0].get("content") or {}).get("parts") or []) if candidates else []
    content = "".join(str(part.get("text") or "") for part in parts).strip()
    parsed = _parse_json_array(content, len(texts))
    if parsed is not None:
        return parsed
    if len(texts) > 1:
        out = []
        for text in texts:
            out.extend(_gemini([text], src, tgt, tone, session))
        return out
    if not content:
        raise TranslationError("Gemini tidak mengembalikan hasil.")
    return [content.strip().strip('"')]


def _llm_translate(texts: list[str], src: str, tgt: str, kind: str, session: requests.Session, tone: str = "santai") -> list[str]:
    user = _llm_user_prompt(texts, src, tgt, tone)
    if kind == "ollama":
        payload = {
            "model": config.OLLAMA_MODEL,
            "messages": [
                {"role": "system", "content": LLM_SYSTEM},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "options": {"temperature": 0.15},
        }
        r = session.post(config.OLLAMA_URL + "/api/chat", json=payload, timeout=180)
    else:
        headers = {"Authorization": f"Bearer {config.OPENAI_API_KEY}"}
        payload = {
            "model": config.OPENAI_MODEL,
            "messages": [
                {"role": "system", "content": LLM_SYSTEM},
                {"role": "user", "content": user},
            ],
            "temperature": 0.15,
        }
        r = session.post(config.OPENAI_BASE_URL + "/chat/completions", json=payload, headers=headers, timeout=180)
    if r.status_code >= 400:
        raise TranslationError(f"{kind} error {r.status_code}: {r.text[:240]}")
    data = r.json()
    if kind == "ollama":
        content = ((data.get("message") or {}).get("content") or "").strip()
    else:
        content = (((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
    parsed = _parse_json_array(content, len(texts))
    if parsed is not None:
        return parsed
    # Fallback: translate one by one if the model did not honour the JSON contract.
    if len(texts) > 1:
        out = []
        for text in texts:
            out.extend(_llm_translate([text], src, tgt, kind, session, tone=tone))
        return out
    return [content.strip().strip('"')]


def _local_fallback(texts: list[str], src: str, tgt: str, progress=None, prefer_nllb: bool = True) -> tuple[list[str], str]:
    """Local fallback. NLLB is much better for CJK than the Argos English pivot."""
    source = detect_script(" ".join(texts[:20])) if src == "auto" else src
    model_dir = best_nllb_dir()
    if prefer_nllb and source in CJK_LANGS and model_dir is not None:
        try:
            return nllb_translate(texts, source, tgt, progress=progress, model_dir=model_dir), "nllb"
        except TranslationError:
            pass
    return local_translate(texts, source, tgt, progress=progress), "local"


def translate_texts(
    texts: list[str],
    src: str,
    tgt: str,
    engine: str = "local",
    progress=None,
    fallback_local: bool = True,
    tone: str = "santai",
) -> tuple[list[str], list[str], str]:
    """Translate a page. Returns (translations, warnings, engine actually used)."""
    warnings: list[str] = []
    if not texts:
        return [], warnings, engine
    if src == tgt:
        return list(texts), warnings, engine
    if tone not in {"santai", "netral", "baku"}:
        tone = "santai"

    # Cache exact repeats (very common in manga chapters).
    cache_keys = [(engine, src, tgt, tone, text) for text in texts]
    with _CACHE_GUARD:
        cached = [_TRANSLATION_CACHE.get(k) for k in cache_keys]
    missing = [i for i, c in enumerate(cached) if c is None]
    if not missing:
        used = {c[1] for c in cached}
        return [str(c[0]) for c in cached], warnings, (used.pop() if len(used) == 1 else engine)

    session = requests.Session()
    session.headers.update({"User-Agent": "MangaFlow/1.0 (+local manga translator)"})
    result: list[str] = []
    engine_used = engine
    try:
        if engine == "local":
            result = local_translate([texts[i] for i in missing], src, tgt, progress=progress)
        elif engine == "nllb":
            result = nllb_translate(
                [texts[i] for i in missing], src, tgt,
                progress=progress,
                model_dir=config.NLLB_MODEL_DIR if nllb_ready() else best_nllb_dir(),
            )
        elif engine == "nllb-tuned":
            manga = manga_nllb_dir()
            if manga is None:
                raise TranslationError(
                    "Model NLLB-200 manga belum tersedia. Unduh model 1.3B dengan "
                    "samples/download_nllb_large.py atau latih model di training/README.md."
                )
            result = nllb_translate(
                [texts[i] for i in missing], src, tgt,
                progress=progress, model_dir=manga,
            )
        else:
            chunk = [texts[i] for i in missing]
            if engine == "google":
                try:
                    result = _google(chunk, src, tgt, session)
                except TranslationError as exc:
                    message = str(exc).strip().rstrip(".")
                    warnings.append(f"Google gagal: {message}. Beralih ke MyMemory.")
                    if progress:
                        progress(5, "Google gagal · beralih ke MyMemory…")
                    result = _mymemory(chunk, src, tgt, session)
                    engine_used = "mymemory"
            elif engine == "mymemory":
                result = _mymemory(chunk, src, tgt, session)
            elif engine == "libretranslate":
                result = _libretranslate(chunk, src, tgt, session)
            elif engine == "deepl":
                result = _deepl(chunk, src, tgt, session)
            elif engine == "microsoft":
                result = _microsoft(chunk, src, tgt, session)
            elif engine == "gemini":
                result = _gemini(chunk, src, tgt, tone, session)
            elif engine in ("ollama", "openai"):
                result = _llm_translate(chunk, src, tgt, engine, session, tone=tone)
            else:
                raise TranslationError(f"Mesin terjemahan tidak dikenal: {engine}")
            result = [_clean_translation(t, src, tgt) for t in result]
    except (TranslationError, requests.RequestException) as exc:
        if engine != "local" and fallback_local:
            message = str(exc).strip().rstrip(".")
            warnings.append(
                f"{_engine_label(engine)} gagal: {message}. Dialihkan ke mesin lokal tanpa limit."
            )
            if progress:
                progress(5, "Beralih ke mesin lokal...")
            result, engine_used = _local_fallback(
                [texts[i] for i in missing], src, tgt,
                progress=progress, prefer_nllb=(engine != "nllb"),
            )
            if engine_used == "nllb":
                warnings.append("Fallback lokal memakai NLLB-200 karena teks CJK terdeteksi.")
        else:
            raise

    if len(result) != len(missing):
        raise TranslationError("Jumlah hasil terjemahan tidak cocok dengan jumlah baris.")

    result = [_apply_source_terms(texts[i], t) for i, t in zip(missing, result)]
    result = [_apply_tone(t, tgt, tone) for t in result]
    for idx, translated in zip(missing, result):
        cached[idx] = (translated, engine_used)
    final = [str(c[0]) for c in cached]
    with _CACHE_GUARD:
        for k, v in zip(cache_keys, cached):
            if len(_TRANSLATION_CACHE) < _CACHE_LIMIT:
                _TRANSLATION_CACHE[k] = v
    return final, warnings, engine_used


def preload_for(src: str, tgt: str, progress=None) -> list[str]:
    """Download every model needed for a pair. Returns human-readable notes."""
    notes = []
    for a, b in pairs_for(src, tgt):
        _load_pair(argos_code(a), argos_code(b), progress=progress)
        notes.append(f"{a} → {b}")
    return notes


def probe_engines() -> list[dict]:
    base_ready = nllb_ready()
    large_ready = nllb_large_ready()
    tuned_ready = nllb_tuned_ready()
    any_nllb = base_ready or large_ready or tuned_ready
    if tuned_ready:
        nllb_note = "Model hasil latihan manga siap"
        manga_note = "Model hasil latihan manga siap"
        manga_name = "NLLB-200 manga (latihan)"
    elif large_ready:
        nllb_note = "NLLB-200 600M · cepat" if base_ready else "NLLB-200 1.3B · kualitas lebih baik"
        manga_note = "NLLB-200 1.3B + kamus manga · belum fine-tune"
        manga_name = "NLLB-200 manga (1.3B)"
    else:
        nllb_note = "NLLB-200 600M · cepat" if base_ready else "Model belum diunduh (lihat README)"
        manga_note = "Belum tersedia · unduh model 1.3B (lihat README)"
        manga_name = "NLLB-200 manga (1.3B)"
    local_note = (
        "Argos/OPUS · CJK otomatis ke NLLB-200 1.3B"
        if large_ready or tuned_ready
        else ("Argos/OPUS · CJK otomatis ke NLLB-200" if base_ready else "Argos/OPUS · offline setelah model terunduh")
    )
    openai_custom = config.OPENAI_BASE_URL.rstrip("/") != "https://api.openai.com/v1"
    engines = [
        {"id": "local", "name": "Lokal (tanpa limit)", "available": True, "note": local_note},
        {"id": "nllb", "name": "NLLB-200 (lokal)", "available": any_nllb, "note": nllb_note},
        {"id": "nllb-tuned", "name": manga_name, "available": (tuned_ready or large_ready), "note": manga_note},
        {"id": "google", "name": "Google Translate", "available": True, "note": "Online · bisa kena rate limit"},
        {"id": "mymemory", "name": "MyMemory", "available": True, "note": "Online · kuota harian"},
        {"id": "deepl", "name": "DeepL", "available": bool(config.DEEPL_API_KEY), "note": "Online · kualitas natural" if config.DEEPL_API_KEY else "Atur kunci DeepL di Pengaturan mesin"},
        {"id": "gemini", "name": "Gemini", "available": bool(config.GEMINI_API_KEY), "note": f"Online · {config.GEMINI_MODEL}" if config.GEMINI_API_KEY else "Atur kunci Gemini di Pengaturan mesin"},
        {"id": "microsoft", "name": "Microsoft Translator", "available": bool(config.AZURE_TRANSLATOR_KEY), "note": "Online · Azure Translator" if config.AZURE_TRANSLATOR_KEY else "Atur kunci Azure di Pengaturan mesin"},
        {"id": "libretranslate", "name": "LibreTranslate", "available": bool(config.LIBRETRANSLATE_URL), "note": config.LIBRETRANSLATE_URL or "Atur URL LibreTranslate di Pengaturan mesin"},
        {"id": "ollama", "name": "Ollama (LLM lokal)", "available": False, "note": "Tidak terdeteksi"},
        {
            "id": "openai",
            "name": "OpenAI-compatible",
            "available": bool(config.OPENAI_API_KEY) or openai_custom,
            "note": (
                f"{config.OPENAI_MODEL} · {config.OPENAI_BASE_URL}"
                if config.OPENAI_API_KEY
                else ("Endpoint lokal tanpa kunci" if openai_custom else "Atur kunci/endpoint di Pengaturan mesin")
            ),
        },
    ]
    try:
        r = requests.get(config.OLLAMA_URL + "/api/tags", timeout=1.2)
        if r.ok:
            models = [m.get("name", "") for m in (r.json().get("models") or [])]
            ok = any(config.OLLAMA_MODEL.split(":")[0] == m.split(":")[0] for m in models)
            for i, engine in enumerate(engines):
                if engine["id"] == "ollama":
                    engines[i] = {
                        "id": "ollama",
                        "name": "Ollama (LLM lokal)",
                        "available": True,
                        "note": f"{len(models)} model · target {config.OLLAMA_MODEL}" + ("" if ok else " (belum di-pull)"),
                    }
                    break
    except Exception:  # noqa: BLE001
        pass
    return engines


def test_engine(engine: str) -> dict:
    """Tes koneksi/kesiapan satu mesin untuk tombol "Tes" di UI."""
    engine = str(engine or "").strip()
    session = requests.Session()
    session.headers.update({"User-Agent": "MangaFlow/1.0 (+local manga translator)"})

    def result(ok: bool, message: str, detail=None) -> dict:
        return {"ok": bool(ok), "engine": engine, "message": message, "detail": detail}

    try:
        if engine == "local":
            return result(True, "Argos/OPUS siap. Model bahasa diunduh sekali saat dipakai.")
        if engine == "nllb":
            if nllb_ready():
                return result(True, f"NLLB-200 600M siap · {config.NLLB_MODEL_DIR}")
            if nllb_large_ready() or nllb_tuned_ready():
                return result(True, f"NLLB-200 manga siap · {manga_nllb_dir()}")
            return result(False, "Model NLLB-200 belum diunduh. Jalankan samples/download_nllb.py.")
        if engine == "nllb-tuned":
            manga = manga_nllb_dir()
            if manga is None:
                return result(False, "Model NLLB-200 manga belum tersedia. Jalankan samples/download_nllb_large.py.")
            label = "hasil latihan" if nllb_tuned_ready() else "1.3B (belum fine-tune)"
            return result(True, f"NLLB-200 manga {label} siap · {manga}")
        if engine == "deepl":
            if not config.DEEPL_API_KEY:
                return result(False, "Kunci DeepL belum diisi.")
            if config.DEEPL_API_URL:
                usage_url = config.DEEPL_API_URL.replace("/translate", "/usage")
            else:
                base = "https://api-free.deepl.com" if config.DEEPL_API_KEY.endswith(":fx") else "https://api.deepl.com"
                usage_url = base + "/v2/usage"
            r = session.get(
                usage_url,
                headers={"Authorization": f"DeepL-Auth-Key {config.DEEPL_API_KEY}"},
                timeout=20,
            )
            if r.status_code in (401, 403):
                return result(False, "Kunci DeepL ditolak (401/403).")
            if r.status_code >= 400:
                return result(False, f"DeepL error {r.status_code}: {r.text[:160]}")
            data = r.json()
            used = data.get("character_count")
            limit = data.get("character_limit")
            extra = f" · {used:,}/{limit:,} karakter" if isinstance(used, int) and isinstance(limit, int) else ""
            return result(True, f"Kunci DeepL valid{extra}")
        if engine == "gemini":
            if not config.GEMINI_API_KEY:
                return result(False, "Kunci Gemini belum diisi.")
            r = session.get(
                "https://generativelanguage.googleapis.com/v1beta/models",
                params={"key": config.GEMINI_API_KEY},
                timeout=20,
            )
            if r.status_code in (401, 403):
                return result(False, "Kunci Gemini ditolak (401/403).")
            if r.status_code >= 400:
                return result(False, f"Gemini error {r.status_code}: {r.text[:160]}")
            models = [m.get("name", "") for m in (r.json().get("models") or [])]
            target = f"models/{config.GEMINI_MODEL}"
            found = any(m == target or m.endswith("/" + config.GEMINI_MODEL) for m in models)
            return result(True, f"Kunci Gemini valid · {len(models)} model" + ("" if found else f" · {config.GEMINI_MODEL} tidak terdaftar"))
        if engine == "microsoft":
            if not config.AZURE_TRANSLATOR_KEY:
                return result(False, "Kunci Azure Translator belum diisi.")
            headers = {"Ocp-Apim-Subscription-Key": config.AZURE_TRANSLATOR_KEY}
            if config.AZURE_TRANSLATOR_REGION:
                headers["Ocp-Apim-Subscription-Region"] = config.AZURE_TRANSLATOR_REGION
            r = session.get(
                config.AZURE_TRANSLATOR_ENDPOINT + "/languages",
                params={"api-version": "3.0"},
                headers=headers,
                timeout=20,
            )
            if r.status_code in (401, 403):
                return result(False, "Kunci Azure ditolak (401/403). Periksa region juga.")
            if r.status_code >= 400:
                return result(False, f"Azure error {r.status_code}: {r.text[:160]}")
            return result(True, "Kunci Azure Translator valid.")
        if engine == "libretranslate":
            if not config.LIBRETRANSLATE_URL:
                return result(False, "URL LibreTranslate belum diisi.")
            r = session.get(config.LIBRETRANSLATE_URL + "/languages", timeout=20)
            if r.status_code >= 400:
                return result(False, f"LibreTranslate error {r.status_code}: {r.text[:160]}")
            return result(True, "LibreTranslate merespons.")
        if engine == "ollama":
            r = session.get(config.OLLAMA_URL + "/api/tags", timeout=5)
            if r.status_code >= 400:
                return result(False, f"Ollama error {r.status_code}.")
            models = [m.get("name", "") for m in (r.json().get("models") or [])]
            name = config.OLLAMA_MODEL
            found = any(m == name or m.split(":")[0] == name.split(":")[0] for m in models)
            if not found:
                return result(False, f"Ollama aktif, tetapi model {name} belum di-pull. Jalankan: ollama pull {name}")
            return result(True, f"Ollama siap · {name} · {len(models)} model")
        if engine == "openai":
            custom = config.OPENAI_BASE_URL.rstrip("/") != "https://api.openai.com/v1"
            if not config.OPENAI_API_KEY and not custom:
                return result(False, "Kunci OpenAI belum diisi.")
            headers = {}
            if config.OPENAI_API_KEY:
                headers["Authorization"] = f"Bearer {config.OPENAI_API_KEY}"
            r = session.get(config.OPENAI_BASE_URL + "/models", headers=headers, timeout=20)
            if r.status_code in (401, 403):
                return result(False, "Kunci/endpoint OpenAI ditolak (401/403).")
            if r.status_code >= 400:
                return result(False, f"Endpoint error {r.status_code}: {r.text[:160]}")
            models = r.json().get("data") if isinstance(r.json(), dict) else None
            count = len(models) if isinstance(models, list) else 0
            return result(True, f"Endpoint OpenAI-compatible merespons · {count} model")
        return result(False, f"Tes untuk mesin {engine} belum tersedia.")
    except requests.RequestException as exc:
        return result(False, f"Koneksi gagal: {str(exc)[:220]}")
    except Exception as exc:  # noqa: BLE001
        return result(False, f"Tes gagal: {str(exc)[:220]}")
