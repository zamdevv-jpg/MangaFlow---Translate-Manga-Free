#!/usr/bin/env python3
"""Gabungkan adapter LoRA NLLB ke model penuh, lalu ekspor ke CTranslate2 int8.

Contoh:
    python training/export_ct2.py --adapter training/out/nllb-manga-lora

Hasil default: data/models/nllb-200-manga-ct2-int8
Setelah selesai, MangaFlow otomatis memakai model ini untuk mesin
"NLLB-200 manga (1.3B)" dan untuk fallback CJK jika tersedia.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ADAPTER = ROOT / "training" / "out" / "nllb-manga-lora"
DEFAULT_MERGED = ROOT / "training" / "out" / "nllb-manga-merged"
DEFAULT_OUTPUT = ROOT / "data" / "models" / "nllb-200-manga-ct2-int8"
DEFAULT_BASE_CT2 = ROOT / "data" / "models" / "nllb-200-distilled-600M-ct2-int8"

COPY_FILES = ("sentencepiece.bpe.model", "shared_vocabulary.txt", "tokenizer.json", "tokenizer_config.json", "special_tokens_map.json")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--adapter", type=Path, default=DEFAULT_ADAPTER)
    parser.add_argument("--base", default="facebook/nllb-200-distilled-600M")
    parser.add_argument("--merged", type=Path, default=DEFAULT_MERGED)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--base-ct2", type=Path, default=Path(os.environ.get("MANGA_NLLB_DIR", DEFAULT_BASE_CT2)))
    parser.add_argument("--quantization", default="int8", choices=["int8", "int8_float32", "float16", "float32"])
    parser.add_argument("--force", action="store_true", help="Timpa folder output yang sudah ada")
    return parser.parse_args()


def merge_adapter(args) -> None:
    try:
        import torch
        from peft import PeftModel
        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
    except ImportError as exc:
        raise SystemExit(
            f"Dependensi belum terpasang ({exc.name}). "
            "Jalankan: pip install -r training/requirements-train.txt"
        ) from exc

    if not (args.adapter / "adapter_config.json").exists():
        raise SystemExit(f"Adapter LoRA tidak ditemukan di {args.adapter}")
    print(f"Memuat base model {args.base} ...")
    base = AutoModelForSeq2SeqLM.from_pretrained(args.base, torch_dtype=torch.float32)
    print(f"Memuat adapter {args.adapter} ...")
    model = PeftModel.from_pretrained(base, str(args.adapter))
    model = model.merge_and_unload()
    args.merged.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(str(args.merged), safe_serialization=True)
    tokenizer = AutoTokenizer.from_pretrained(args.base)
    tokenizer.save_pretrained(str(args.merged))
    print(f"Model gabungan: {args.merged}")


def convert(args) -> None:
    if args.output.exists():
        if not args.force:
            raise SystemExit(f"{args.output} sudah ada. Pakai --force untuk menimpa.")
        shutil.rmtree(args.output)
    args.output.parent.mkdir(parents=True, exist_ok=True)

    converter = shutil.which("ct2-transformers-converter")
    if converter:
        cmd = [converter]
    else:
        cmd = [sys.executable, "-m", "ctranslate2.converters.transformers"]
    cmd += [
        "--model", str(args.merged),
        "--output_dir", str(args.output),
        "--quantization", args.quantization,
        "--force",
    ]
    print("Menjalankan:", " ".join(cmd))
    result = subprocess.run(cmd)
    if result.returncode != 0:
        raise SystemExit("Konversi CTranslate2 gagal. Pastikan ctranslate2 versi terbaru terpasang.")


def copy_support_files(args) -> None:
    sources = [args.merged, args.adapter, args.base_ct2]
    for name in COPY_FILES:
        target = args.output / name
        if target.exists():
            continue
        for folder in sources:
            candidate = folder / name
            if candidate.exists():
                shutil.copy2(candidate, target)
                print(f"  salin {name} dari {folder}")
                break
    required = ("model.bin", "sentencepiece.bpe.model", "shared_vocabulary.txt", "config.json")
    missing = [name for name in required if not (args.output / name).exists()]
    if missing:
        print(
            "PERINGATAN: file belum lengkap: " + ", ".join(missing) + "\n"
            f"Salin manual dari model CT2 dasar ({args.base_ct2}) atau dari folder model HF. "
            "Tanpa file ini MangaFlow tidak bisa memuat model latihan.",
            file=sys.stderr,
        )
    else:
        print("Model CT2 latihan lengkap dan siap dipakai.")


def main() -> int:
    args = parse_args()
    merge_adapter(args)
    convert(args)
    copy_support_files(args)

    config_path = args.output / "config.json"
    if config_path.exists():
        try:
            meta = json.loads(config_path.read_text(encoding="utf-8"))
            meta["mangaflow_tuned"] = True
            meta["base_model"] = args.base
            config_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    print("\nSelesai. Jalankan ulang server MangaFlow:")
    print("  python app.py")
    print("Lalu pilih mesin 'NLLB-200 manga (1.3B)' di UI.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
