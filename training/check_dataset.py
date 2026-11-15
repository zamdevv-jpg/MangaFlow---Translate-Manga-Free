#!/usr/bin/env python3
"""Periksa kualitas dataset JSONL sebelum fine-tuning NLLB.

Contoh:
    python training/check_dataset.py
    python training/check_dataset.py --data training/data/pairs.jsonl --show 20

Skrip ini tidak mengubah data. Ia menghitung statistik dan menandai baris yang
mungkin bermasalah, misalnya OCR sampah, sumber tanpa aksara CJK, atau target
yang jauh lebih panjang dari sumber.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA = ROOT / "training" / "data" / "pairs.jsonl"
CJK_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]")
LATIN_WORD_RE = re.compile(r"[A-Za-z]{2,}")


def load_rows(path: Path) -> list[dict]:
    rows: list[dict] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            print(f"Baris {number}: JSON tidak valid ({exc})", file=sys.stderr)
            continue
        src = str(row.get("src") or "").strip()
        tgt = str(row.get("tgt") or "").strip()
        if src and tgt:
            rows.append({"src": src, "tgt": tgt})
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--show", type=int, default=12, help="Jumlah baris mencurigakan yang ditampilkan")
    args = parser.parse_args()

    if not args.data.exists():
        print(f"Dataset tidak ditemukan: {args.data}", file=sys.stderr)
        print("Jalankan training/collect_deepl.py dulu.", file=sys.stderr)
        return 2

    rows = load_rows(args.data)
    if not rows:
        print("Dataset kosong setelah baris kosong/tidak valid dibuang.", file=sys.stderr)
        return 2

    sources = [row["src"] for row in rows]
    duplicates = [src for src, count in Counter(sources).items() if count > 1]
    avg_src = sum(len(row["src"]) for row in rows) / len(rows)
    avg_tgt = sum(len(row["tgt"]) for row in rows) / len(rows)

    suspicious: list[tuple[int, list[str], str, str]] = []
    for index, row in enumerate(rows, 1):
        src, tgt = row["src"], row["tgt"]
        reasons: list[str] = []
        if not CJK_RE.search(src):
            reasons.append("sumber tanpa aksara CJK")
        if src.casefold() == tgt.casefold():
            reasons.append("sumber = target")
        if LATIN_WORD_RE.search(src):
            reasons.append("ada kata Latin di sumber (mungkin OCR sampah)")
        if len(tgt) < 2:
            reasons.append("target terlalu pendek")
        if len(tgt) > max(40, len(src) * 3):
            reasons.append("target jauh lebih panjang dari sumber")
        if reasons:
            suspicious.append((index, reasons, src, tgt))

    print(f"File                 : {args.data}")
    print(f"Pasangan valid       : {len(rows):,}")
    print(f"Panjang sumber rata-rata : {avg_src:.1f} karakter")
    print(f"Panjang target rata-rata : {avg_tgt:.1f} karakter")
    print(f"Sumber duplikat      : {len(duplicates):,}")
    print(f"Baris mencurigakan   : {len(suspicious):,}")

    if len(rows) < 500:
        print(
            f"\nPERINGATAN: hanya {len(rows):,} pasangan. Fine-tuning sebaiknya memakai "
            "minimal 500 pasangan; 3.000+ lebih stabil.",
            file=sys.stderr,
        )
    if duplicates:
        print(f"\nContoh sumber duplikat ({min(3, len(duplicates))}):", file=sys.stderr)
        for src in duplicates[:3]:
            print(f"  - {src}", file=sys.stderr)
    if suspicious:
        print(f"\nContoh baris mencurigakan ({min(args.show, len(suspicious))}):", file=sys.stderr)
        for index, reasons, src, tgt in suspicious[: args.show]:
            print(f"  baris {index}: {', '.join(reasons)}", file=sys.stderr)
            print(f"    src: {src}", file=sys.stderr)
            print(f"    tgt: {tgt}", file=sys.stderr)

    if suspicious:
        print(
            "\nPeriksa baris mencurigakan di atas secara manual. Buang OCR sampah dan "
            "koreksi terjemahan yang jelas salah sebelum melatih.",
            file=sys.stderr,
        )
    elif len(rows) < 500:
        print("\nTambah lebih banyak halaman sebelum melatih agar model tidak menghafal.", file=sys.stderr)
    else:
        print("\nDataset terlihat siap untuk percobaan fine-tuning.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
