"""Penyimpanan pengaturan mesin terjemahan dari UI.

Kunci API yang diisi lewat panel pengaturan disimpan di
``data/settings.json`` dan langsung dipakai tanpa restart. Variabel
lingkungan tetap menang agar deployment bisa mengunci konfigurasi.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from . import config

SETTINGS_FILE = config.DATA_DIR / "settings.json"
LOCK = threading.RLock()

# nama field UI -> (atribut config, nama env, rahasia)
FIELDS: dict[str, tuple[str, str, bool]] = {
    "deepl_api_key": ("DEEPL_API_KEY", "DEEPL_API_KEY", True),
    "deepl_api_url": ("DEEPL_API_URL", "DEEPL_API_URL", False),
    "gemini_api_key": ("GEMINI_API_KEY", "GEMINI_API_KEY", True),
    "gemini_model": ("GEMINI_MODEL", "GEMINI_MODEL", False),
    "azure_translator_key": ("AZURE_TRANSLATOR_KEY", "AZURE_TRANSLATOR_KEY", True),
    "azure_translator_region": ("AZURE_TRANSLATOR_REGION", "AZURE_TRANSLATOR_REGION", False),
    "azure_translator_endpoint": ("AZURE_TRANSLATOR_ENDPOINT", "AZURE_TRANSLATOR_ENDPOINT", False),
    "libretranslate_url": ("LIBRETRANSLATE_URL", "LIBRETRANSLATE_URL", False),
    "libretranslate_api_key": ("LIBRETRANSLATE_API_KEY", "LIBRETRANSLATE_API_KEY", True),
    "openai_api_key": ("OPENAI_API_KEY", "OPENAI_API_KEY", True),
    "openai_base_url": ("OPENAI_BASE_URL", "OPENAI_BASE_URL", False),
    "openai_model": ("OPENAI_MODEL", "OPENAI_MODEL", False),
    "ollama_url": ("OLLAMA_URL", "OLLAMA_URL", False),
    "ollama_model": ("OLLAMA_MODEL", "OLLAMA_MODEL", False),
}

_URL_FIELDS = {
    "deepl_api_url",
    "azure_translator_endpoint",
    "libretranslate_url",
    "openai_base_url",
    "ollama_url",
}


def _normalise(field: str, value: str) -> str:
    value = value.strip()
    if field in _URL_FIELDS:
        value = value.rstrip("/")
    return value


def _apply(field: str, value: str) -> None:
    attr, _env, _secret = FIELDS[field]
    setattr(config, attr, _normalise(field, value))


def _read() -> dict:
    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception:  # noqa: BLE001
        return {}


def load() -> None:
    """Terapkan settings.json saat aplikasi mulai (env tetap prioritas)."""
    data = _read()
    with LOCK:
        for field, (attr, env_name, _secret) in FIELDS.items():
            if env_name in os.environ:
                continue
            if field in data and isinstance(data[field], str):
                setattr(config, attr, _normalise(field, data[field]))


def env_locked(field: str) -> bool:
    _attr, env_name, _secret = FIELDS[field]
    return env_name in os.environ


def public() -> dict:
    """Nilai aman untuk UI: kunci rahasia tidak pernah dikirim balik."""
    with LOCK:
        values = {}
        configured = {}
        for field, (attr, _env, secret) in FIELDS.items():
            current = str(getattr(config, attr, "") or "")
            values[field] = "" if secret else current
            configured[field] = bool(current)
        return {
            "values": values,
            "configured": configured,
            "env_locked": {field: env_locked(field) for field in FIELDS},
            "file": str(SETTINGS_FILE),
            "nllb": {
                "base": nllb_status(config.NLLB_MODEL_DIR),
                "large": nllb_status(config.NLLB_LARGE_MODEL_DIR),
                "tuned": nllb_status(config.NLLB_TUNED_MODEL_DIR),
            },
        }


def save(payload: dict) -> dict:
    """Simpan field yang dikirim. Kunci rahasia kosong = pertahankan nilai lama."""
    payload = payload if isinstance(payload, dict) else {}
    with LOCK:
        stored = _read()
        for field in FIELDS:
            if field not in payload:
                continue
            value = payload[field]
            _attr, _env, secret = FIELDS[field]
            if secret:
                if value is None:
                    stored[field] = ""
                    _apply(field, "")
                elif str(value).strip():
                    stored[field] = str(value).strip()
                    _apply(field, str(value).strip())
                # string kosong: jangan hapus kunci yang sudah tersimpan
            else:
                text = "" if value is None else str(value)
                stored[field] = text
                _apply(field, text)
        try:
            SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
            SETTINGS_FILE.write_text(
                json.dumps(stored, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"Gagal menyimpan pengaturan: {exc}") from exc
    return public()


def nllb_status(path) -> dict:
    path = Path(path)
    names = ("model.bin", "sentencepiece.bpe.model", "config.json")
    ready = all((path / name).exists() for name in names) and (
        (path / "shared_vocabulary.txt").exists()
        or (path / "shared_vocabulary.json").exists()
    )
    size = 0
    model_bin = path / "model.bin"
    if model_bin.exists():
        try:
            size = model_bin.stat().st_size
        except OSError:
            size = 0
    if size < (1_200_000_000 if path.resolve() == config.NLLB_LARGE_MODEL_DIR.resolve() else 300_000_000):
        ready = False
    return {
        "path": str(path),
        "ready": ready,
        "size_mb": round(size / 1_000_000, 1),
    }
