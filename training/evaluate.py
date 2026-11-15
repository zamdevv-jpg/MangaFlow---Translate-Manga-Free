#!/usr/bin/env python3
"""Bandingkan model NLLB CT2 dasar vs hasil latihan pada data uji JSONL.

Contoh:
    python training/evaluate.py --data training/data/test.jsonl \
        --model data/models/nllb-200-distilled-600M-ct2-int8 \
        --compare data/models/nllb-200-manga-ct2-int8

Metrik memakai sacrebleu jika terpasang (pip install sacrebleu).
"""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

import ctranslate2
import sentencepiece as spm

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BASE = ROOT / "data" / "models" / "nllb-200-distilled-600M-ct2-int8"
DEFAULT_TUNED = ROOT / "data" / "models" / "nllb-200-manga-ct2-int8"


def load_rows(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        if row.get("src") and row.get("tgt"):
            rows.append({"src": str(row["src"]), "tgt": str(row["tgt"])})
    return rows


def translate(model_dir: Path, rows: list[dict], src_code: str, tgt_code: str, beam: int) -> list[str]:
    sp = spm.SentencePieceProcessor()
    sp.Load(str(model_dir / "sentencepiece.bpe.model"))
    translator = ctranslate2.Translator(str(model_dir), device="cpu", compute_type="int8")
    encoded = [[src_code] + sp.encode(row["src"], out_type=str) + ["</s>"] for row in rows]
    outputs = translator.translate_batch(
        encoded,
        target_prefix=[[tgt_code] for _ in rows],
        beam_size=beam,
        max_decoding_length=192,
        max_batch_size=16,
        batch_type="examples",
        no_repeat_ngram_size=3,
        repetition_penalty=1.10,
    )
    result = []
    for item in outputs:
        tokens = item.hypotheses[0] if item.hypotheses else []
        if tokens and tokens[0] == tgt_code:
            tokens = tokens[1:]
        result.append(sp.DecodePieces(tokens))
    return result


def score(name: str, hypotheses: list[str], references: list[str]) -> None:
    try:
        import sacrebleu
    except ImportError:
        print(f"{name}: (pasang sacrebleu untuk BLEU/chrF)")
        return
    bleu = sacrebleu.corpus_bleu(hypotheses, [references])
    chrf = sacrebleu.corpus_chrf(hypotheses, [references])
    print(f"{name}: BLEU {bleu.score:.2f} · chrF {chrf.score:.2f}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--model", type=Path, default=DEFAULT_BASE)
    parser.add_argument("--compare", type=Path, default=None)
    parser.add_argument("--src-lang", default="jpn_Jpan")
    parser.add_argument("--tgt-lang", default="ind_Latn")
    parser.add_argument("--limit", type=int, default=150)
    parser.add_argument("--beam", type=int, default=5)
    parser.add_argument("--samples", type=int, default=8)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()

    rows = load_rows(args.data)
    if not rows:
        raise SystemExit("Data uji kosong.")
    random.Random(args.seed).shuffle(rows)
    rows = rows[: args.limit]
    references = [row["tgt"] for row in rows]

    results = {}
    for label, folder in (("dasar", args.model), ("latihan", args.compare)):
        if folder is None:
            continue
        if not (folder / "model.bin").exists():
            raise SystemExit(f"Model tidak ditemukan: {folder}")
        print(f"Menerjemahkan {len(rows)} baris dengan model {label} ...")
        results[label] = translate(folder, rows, args.src_lang, args.tgt_lang, args.beam)
        score(label, results[label], references)

    if len(results) == 2:
        print(f"\nContoh (maks. {args.samples}):")
        for row, base, tuned in list(zip(rows, results["dasar"], results["latihan"]))[: args.samples]:
            print("\n  Asli   :", row["src"])
            print("  Acuan  :", row["tgt"])
            print("  Dasar  :", base)
            print("  Latihan:", tuned)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
