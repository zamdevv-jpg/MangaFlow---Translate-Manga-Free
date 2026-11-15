"""Unduh model NLLB-200 distilled 1.3B (CT2 int8) untuk mesin manga.

Model ini lebih akurat daripada 600M dan dipakai oleh mesin
"NLLB-200 manga" bila model hasil latihan belum tersedia.

Unduhan bisa dilanjutkan: kalau koneksi putus di tengah jalan, jalankan
perintah yang sama lagi dan skrip akan memakai HTTP Range.

Jalankan dari folder proyek:
    python samples/download_nllb_large.py
"""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
REPO = "OpenNMT/nllb-200-distilled-1.3B-ct2-int8"
SP_REPO = "facebook/nllb-200-distilled-1.3B"
OUT = Path(
    os.environ.get(
        "MANGA_NLLB_LARGE_DIR",
        ROOT / "data" / "models" / "nllb-200-distilled-1.3B-ct2-int8",
    )
)
BASE = Path(os.environ.get("MANGA_NLLB_DIR", ROOT / "data" / "models" / "nllb-200-distilled-600M-ct2-int8"))
FILES = {
    "model.bin": 1300 * 1024 * 1024,
    "shared_vocabulary.json": 5 * 1024 * 1024,
    "tokenizer.json": 15 * 1024 * 1024,
    "config.json": 100,
}
EXPECTED = {"model.bin": 1_377_755_738}
SP_FILE = "sentencepiece.bpe.model"


def _complete(path: Path, min_size: int, expected: int | None) -> bool:
    try:
        size = path.stat().st_size
    except OSError:
        return False
    if expected is not None:
        return size == expected
    return size >= min_size


def download(name: str, min_size: int, repo: str = REPO, expected: int | None = None) -> None:
    path = OUT / name
    if _complete(path, min_size, expected):
        print("exists", name, f"{path.stat().st_size / 1e6:.1f} MB", flush=True)
        return

    url = f"https://huggingface.co/{repo}/resolve/main/{name}?download=true"
    tmp = path.with_suffix(path.suffix + ".part")
    if path.exists() and not tmp.exists():
        path.replace(tmp)
    started = time.time()
    print("download", name, flush=True)
    attempts = 0
    while True:
        current = tmp.stat().st_size if tmp.exists() else 0
        if expected is not None and current >= expected:
            break
        attempts += 1
        if attempts > 40:
            raise RuntimeError(f"Gagal mengunduh {name} setelah 40 percobaan")
        headers = {"Range": f"bytes={current}-"} if current else {}
        try:
            with requests.get(url, headers=headers, stream=True, timeout=(20, 120)) as response:
                if current and response.status_code == 206:
                    content_range = response.headers.get("Content-Range", "")
                    if not content_range.startswith(f"bytes {current}-"):
                        print("range tidak cocok:", content_range, flush=True)
                        current = 0
                elif response.status_code == 200:
                    current = 0
                elif response.status_code == 416:
                    break
                else:
                    response.raise_for_status()
                written = 0
                with open(tmp, "ab" if current else "wb") as handle:
                    for chunk in response.iter_content(1024 * 1024):
                        if not chunk:
                            continue
                        handle.write(chunk)
                        written += len(chunk)
                        if written and written % (64 * 1024 * 1024) < 1024 * 1024:
                            done = (current + written) / 1e6
                            print(f"  {name}: {done:.0f} MB", flush=True)
                print(f"  koneksi berakhir setelah +{written / 1e6:.1f} MB", flush=True)
        except requests.RequestException as exc:
            print("  koneksi gagal:", str(exc)[:180], flush=True)
        time.sleep(2)

    size = tmp.stat().st_size
    if expected is not None and size != expected:
        raise RuntimeError(f"Ukuran {name} tidak sesuai: {size} != {expected}")
    if expected is None and size < min_size:
        raise RuntimeError(f"Ukuran {name} terlalu kecil: {size}")
    os.replace(tmp, path)
    print("done", name, f"{path.stat().st_size / 1e6:.1f} MB in {time.time() - started:.0f}s", flush=True)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, min_size in FILES.items():
        download(name, min_size, expected=EXPECTED.get(name))
    for name in ("generation_config.json", "special_tokens_map.json", "tokenizer_config.json"):
        download(name, 10)

    # Tokenizer NLLB-200 sama untuk semua ukuran model. Pakai salinan lokal
    # bila sudah ada supaya tidak mengunduh 5 MB lagi.
    sp_path = OUT / SP_FILE
    if not sp_path.exists():
        source = BASE / SP_FILE
        if source.exists():
            shutil.copyfile(source, sp_path)
            print("copy", SP_FILE, "dari model 600M", flush=True)
        else:
            download(SP_FILE, 4 * 1024 * 1024, repo=SP_REPO)

    print(f"\nNLLB-200 1.3B siap di {OUT}", flush=True)


if __name__ == "__main__":
    main()
