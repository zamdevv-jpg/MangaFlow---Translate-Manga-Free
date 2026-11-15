from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("MANGA_DATA_DIR", BASE_DIR / "data"))
JOBS_DIR = DATA_DIR / "jobs"
MODELS_DIR = DATA_DIR / "models"
ARGOS_DIR = MODELS_DIR / "argos"
OCR_DIR = MODELS_DIR / "rapidocr"
NLLB_MODEL_DIR = Path(os.environ.get("MANGA_NLLB_DIR", MODELS_DIR / "nllb-200-distilled-600M-ct2-int8"))
NLLB_LARGE_MODEL_DIR = Path(
    os.environ.get(
        "MANGA_NLLB_LARGE_DIR",
        MODELS_DIR / "nllb-200-distilled-1.3B-ct2-int8",
    )
)
NLLB_TUNED_MODEL_DIR = Path(os.environ.get("MANGA_NLLB_TUNED_DIR", MODELS_DIR / "nllb-200-manga-ct2-int8"))
WEB_DIR = BASE_DIR / "web"
ARGOS_INDEX_FILE = Path(os.environ.get("MANGA_ARGOS_INDEX", BASE_DIR / "server" / "argos_index.json"))

for _p in (DATA_DIR, JOBS_DIR, MODELS_DIR, ARGOS_DIR, OCR_DIR):
    _p.mkdir(parents=True, exist_ok=True)

HOST = os.environ.get("MANGA_HOST", "127.0.0.1")
PORT = int(os.environ.get("MANGA_PORT", "8765"))

MAX_FILES = int(os.environ.get("MANGA_MAX_FILES", "300"))
MAX_UPLOAD_MB = int(os.environ.get("MANGA_MAX_UPLOAD_MB", "500"))
MAX_IMAGE_SIDE = int(os.environ.get("MANGA_MAX_IMAGE_SIDE", "20000"))

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:7b")
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
LIBRETRANSLATE_URL = os.environ.get("LIBRETRANSLATE_URL", "").rstrip("/")
LIBRETRANSLATE_API_KEY = os.environ.get("LIBRETRANSLATE_API_KEY", "")
DEEPL_API_KEY = os.environ.get("DEEPL_API_KEY", "")
DEEPL_API_URL = os.environ.get("DEEPL_API_URL", "").rstrip("/")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
AZURE_TRANSLATOR_KEY = os.environ.get("AZURE_TRANSLATOR_KEY", "")
AZURE_TRANSLATOR_REGION = os.environ.get("AZURE_TRANSLATOR_REGION", "")
AZURE_TRANSLATOR_ENDPOINT = os.environ.get("AZURE_TRANSLATOR_ENDPOINT", "https://api.cognitive.microsofttranslator.com").rstrip("/")

SUPPORTED_IMAGE_EXT = {
    ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".gif",
}
SUPPORTED_ARCHIVE_EXT = {".zip", ".cbz"}

APP_VERSION = "1.0.0"
