#!/usr/bin/env python3
"""LoRA fine-tuning NLLB-200 untuk dialog manga Jepang -> Indonesia.

Contoh:
    pip install -r training/requirements-train.txt
    python training/finetune_nllb.py \
        --data training/data/pairs.jsonl \
        --output training/out/nllb-manga-lora \
        --epochs 4

Butuh GPU NVIDIA (8 GB+ disarankan). Di CPU proses ini bisa sangat lama.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA = ROOT / "training" / "data" / "pairs.jsonl"
DEFAULT_OUT = ROOT / "training" / "out" / "nllb-manga-lora"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--model", default="facebook/nllb-200-distilled-600M")
    parser.add_argument("--src-lang", default="jpn_Jpan", help="Kode FLORES-200 sumber")
    parser.add_argument("--tgt-lang", default="ind_Latn", help="Kode FLORES-200 target")
    parser.add_argument("--epochs", type=float, default=4.0)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--grad-accum", type=int, default=4)
    parser.add_argument("--lr", type=float, default=1e-4)
    parser.add_argument("--max-length", type=int, default=192)
    parser.add_argument("--max-samples", type=int, default=0, help="0 = semua baris")
    parser.add_argument("--val-ratio", type=float, default=0.08)
    parser.add_argument("--val-out", type=Path, default=None, help="Simpan split validasi ke JSONL untuk evaluate.py")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--lora-r", type=int, default=16)
    parser.add_argument("--lora-alpha", type=int, default=32)
    parser.add_argument("--lora-dropout", type=float, default=0.05)
    parser.add_argument("--fp16", action="store_true", help="Gunakan fp16 di GPU")
    parser.add_argument("--bf16", action="store_true", help="Gunakan bf16 di GPU (Ampere+)")
    parser.add_argument("--resume", action="store_true", help="Lanjut dari checkpoint terakhir")
    return parser.parse_args()


def require_training_stack():
    try:
        import torch  # noqa: F401
        import transformers  # noqa: F401
        import datasets  # noqa: F401
        import peft  # noqa: F401
    except ImportError as exc:
        raise SystemExit(
            f"Dependensi latihan belum terpasang ({exc.name}). "
            "Jalankan: pip install -r training/requirements-train.txt"
        ) from exc


def main() -> int:
    args = parse_args()
    require_training_stack()

    import torch
    from datasets import load_dataset
    from peft import LoraConfig, TaskType, get_peft_model
    from transformers import (
        AutoModelForSeq2SeqLM,
        AutoTokenizer,
        DataCollatorForSeq2Seq,
        Seq2SeqTrainer,
        Seq2SeqTrainingArguments,
    )

    if not args.data.exists():
        raise SystemExit(f"Dataset tidak ditemukan: {args.data}. Jalankan collect_deepl.py dulu.")
    if args.fp16 and args.bf16:
        raise SystemExit("Pilih salah satu: --fp16 atau --bf16.")

    use_cuda = torch.cuda.is_available()
    if not use_cuda:
        print(
            "PERINGATAN: GPU tidak terdeteksi. LoRA NLLB-600M di CPU bisa memakan "
            "berjam-jam sampai berhari-hari. Gunakan GPU atau kecilkan --max-samples.",
            file=sys.stderr,
        )
    if args.fp16 and not use_cuda:
        print("--fp16 diabaikan karena tidak ada GPU.", file=sys.stderr)
        args.fp16 = False
    if args.bf16 and not use_cuda:
        print("--bf16 diabaikan karena tidak ada GPU.", file=sys.stderr)
        args.bf16 = False

    raw = load_dataset("json", data_files=str(args.data), split="train")
    raw = raw.filter(lambda row: bool(str(row.get("src", "")).strip()) and bool(str(row.get("tgt", "")).strip()))
    if args.max_samples > 0:
        raw = raw.select(range(min(args.max_samples, len(raw))))
    if len(raw) < 10:
        raise SystemExit("Dataset terlalu kecil (<10 pasangan). Kumpulkan lebih banyak dialog manga.")
    if len(raw) < 500:
        print(
            f"PERINGATAN: dataset hanya {len(raw):,} pasangan. Hasil LoRA bisa menghafal "
            "data dan tidak generalisasi. Idealnya 3.000+ pasangan dialog manga.",
            file=sys.stderr,
        )

    split = raw.train_test_split(test_size=args.val_ratio, seed=args.seed) if args.val_ratio > 0 else None
    train_data = split["train"] if split else raw
    eval_data = split["test"] if split else None
    print(f"Data latih: {len(train_data):,} pasangan" + (f" · validasi: {len(eval_data):,}" if eval_data else ""))

    if args.val_out and eval_data is not None:
        args.val_out.parent.mkdir(parents=True, exist_ok=True)
        with args.val_out.open("w", encoding="utf-8", newline="\n") as handle:
            for row in eval_data:
                handle.write(json.dumps({"src": row["src"], "tgt": row["tgt"]}, ensure_ascii=False) + "\n")
        print(f"Data uji disimpan: {args.val_out}")
    elif args.val_out:
        print("--val-out diabaikan karena --val-ratio 0.", file=sys.stderr)

    tokenizer = AutoTokenizer.from_pretrained(args.model, src_lang=args.src_lang, tgt_lang=args.tgt_lang)

    def preprocess(batch):
        tokenizer.src_lang = args.src_lang
        model_inputs = tokenizer(
            batch["src"],
            max_length=args.max_length,
            truncation=True,
        )
        labels = tokenizer(
            text_target=batch["tgt"],
            max_length=args.max_length,
            truncation=True,
        )
        model_inputs["labels"] = labels["input_ids"]
        return model_inputs

    train_data = train_data.map(preprocess, batched=True, remove_columns=train_data.column_names)
    if eval_data is not None:
        eval_data = eval_data.map(preprocess, batched=True, remove_columns=eval_data.column_names)

    dtype = torch.bfloat16 if args.bf16 else None
    model = AutoModelForSeq2SeqLM.from_pretrained(args.model, torch_dtype=dtype)
    model.config.use_cache = False
    model.gradient_checkpointing_enable()

    lora = LoraConfig(
        task_type=TaskType.SEQ_2_SEQ_LM,
        inference_mode=False,
        r=args.lora_r,
        lora_alpha=args.lora_alpha,
        lora_dropout=args.lora_dropout,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "fc1", "fc2"],
        bias="none",
    )
    model = get_peft_model(model, lora)
    model.enable_input_require_grads()
    model.print_trainable_parameters()

    args.output.mkdir(parents=True, exist_ok=True)
    train_kwargs = dict(
        output_dir=str(args.output),
        num_train_epochs=args.epochs,
        per_device_train_batch_size=args.batch_size,
        per_device_eval_batch_size=max(1, args.batch_size),
        gradient_accumulation_steps=args.grad_accum,
        learning_rate=args.lr,
        warmup_ratio=0.05,
        lr_scheduler_type="cosine",
        logging_steps=10,
        save_strategy="epoch",
        save_total_limit=3,
        report_to="none",
        seed=args.seed,
        fp16=args.fp16,
        bf16=args.bf16,
        gradient_checkpointing=True,
        remove_unused_columns=False,
    )
    if eval_data is not None:
        train_kwargs.update(
            eval_strategy="epoch",
            do_eval=True,
            load_best_model_at_end=True,
            metric_for_best_model="eval_loss",
            greater_is_better=False,
        )
    training_args = Seq2SeqTrainingArguments(**train_kwargs)

    trainer_kwargs = dict(
        model=model,
        args=training_args,
        train_dataset=train_data,
        eval_dataset=eval_data,
        data_collator=DataCollatorForSeq2Seq(
            tokenizer=tokenizer,
            model=model,
            label_pad_token_id=-100,
            pad_to_multiple_of=8,
        ),
    )
    try:
        trainer = Seq2SeqTrainer(processing_class=tokenizer, **trainer_kwargs)
    except TypeError as exc:
        if "processing_class" not in str(exc):
            raise
        trainer = Seq2SeqTrainer(tokenizer=tokenizer, **trainer_kwargs)

    checkpoint = args.resume and any(args.output.glob("checkpoint-*"))
    trainer.train(resume_from_checkpoint=checkpoint or None)
    trainer.save_model(str(args.output))
    tokenizer.save_pretrained(str(args.output))

    meta = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "base_model": args.model,
        "src_lang": args.src_lang,
        "tgt_lang": args.tgt_lang,
        "pairs": len(raw),
        "epochs": args.epochs,
        "lora_r": args.lora_r,
        "lora_alpha": args.lora_alpha,
        "lr": args.lr,
        "max_length": args.max_length,
        "cuda": use_cuda,
        "gpu": torch.cuda.get_device_name(0) if use_cuda else None,
    }
    (args.output / "training_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Adapter LoRA tersimpan di {args.output}")
    print("Langkah berikutnya: python training/export_ct2.py --adapter", args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
