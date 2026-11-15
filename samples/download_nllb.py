"""Unduh model NLLB-200 distilled 600M (CT2 int8) sekali saja.

Jalankan dari folder proyek:
    python samples/download_nllb.py
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
REPO = "JustFrederik/nllb-200-distilled-600M-ct2-int8"
OUT = Path(os.environ.get("MANGA_NLLB_DIR", ROOT / "data" / "models" / "nllb-200-distilled-600M-ct2-int8"))
FILES = {
    "model.bin": 500 * 1024 * 1024,
    "sentencepiece.bpe.model": 1024 * 1024,
    "shared_vocabulary.txt": 512 * 1024,
    "config.json": 100,
}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, min_size in FILES.items():
        path = OUT / name
        if path.exists() and path.stat().st_size >= min_size:
            print("exists", name, f"{path.stat().st_size / 1e6:.1f} MB", flush=True)
            continue
        url = f"https://huggingface.co/{REPO}/resolve/main/{name}?download=true"
        started = time.time()
        print("download", name, flush=True)
        with requests.get(url, stream=True, timeout=(20, 300)) as response:
            response.raise_for_status()
            total = int(response.headers.get("content-length") or 0)
            done = 0
            tmp = path.with_suffix(path.suffix + ".part")
            with open(tmp, "wb") as handle:
                for chunk in response.iter_content(1024 * 1024):
                    if not chunk:
                        continue
                    handle.write(chunk)
                    done += len(chunk)
                    if total and done % (20 * 1024 * 1024) < 1024 * 1024:
                        print(f"{name}: {done / 1e6:.0f}/{total / 1e6:.0f} MB", flush=True)
        os.replace(tmp, path)
        print("done", name, f"{path.stat().st_size / 1e6:.1f} MB in {time.time() - started:.0f}s", flush=True)
    print(f"\nNLLB siap di {OUT}", flush=True)


if __name__ == "__main__":
    main()
