#!/usr/bin/env python3
"""Kumpulkan pasangan latih Jepang -> Indonesia dari hasil OCR.

Guru default adalah DeepL karena paling dekat dengan gaya yang ingin ditiru.
Tanpa kunci DeepL, pakai --teacher google atau --teacher mymemory (gratis,
tetapi kualitas dan batas permintaannya berbeda).

Contoh:
    python training/collect_deepl.py --dry-run
    python training/collect_deepl.py --max-lines 3000
    python training/collect_deepl.py --teacher google --max-lines 1000
    python training/collect_deepl.py --jobs data/jobs --out training/data/pairs.jsonl

Kunci API dibaca dari DEEPL_API_KEY (atau --api-key). Hasil berupa JSONL:
    {"src": "俺は...", "tgt": "Aku..."}

Skrip ini hanya mengumpulkan teks dari job MangaFlow di komputer sendiri.
Tidak mengunggah gambar; hanya baris teks yang dikirim ke guru terjemahan.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import time
from pathlib import Path

import requests

TEACHERS = ("deepl", "google", "mymemory")
GOOGLE_URL = "https://translate.googleapis.com/translate_a/single"
MYMEMORY_URL = "https://api.mymemory.translated.net/get"

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_JOBS = ROOT / "data" / "jobs"
DEFAULT_OUT = ROOT / "training" / "data" / "pairs.jsonl"

JAPANESE_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff]")
NOISE_RE = re.compile(r"^[\W_]+$", re.UNICODE)

DEEPL_CODES = {
    "ja": "JA",
    "id": "ID",
    "en": "EN",
    "ko": "KO",
    "zh": "ZH",
    "zh-TW": "ZH",
}


def deepl_url(api_key: str) -> str:
    override = os.environ.get("DEEPL_API_URL", "").rstrip("/")
    if override:
        return override
    if api_key.endswith(":fx"):
        return "https://api-free.deepl.com/v2/translate"
    return "https://api.deepl.com/v2/translate"


def _job_entries(jobs_dir: Path) -> list[dict]:
    """Baca metadata job dan urutkan dari yang terbaru."""
    entries: list[dict] = []
    for job_file in jobs_dir.glob("j_*/job.json"):
        try:
            meta = json.loads(job_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        pages = {
            str(page.get("id")): str(page.get("name") or page.get("id"))
            for page in meta.get("pages") or []
            if page.get("id")
        }
        entries.append({
            "created": str(meta.get("created_at") or ""),
            "status": str(meta.get("status") or ""),
            "dir": job_file.parent,
            "pages": pages,
        })
    for job_dir in jobs_dir.glob("j_*"):
        if not (job_dir / "job.json").exists() and (job_dir / "pages").is_dir():
            entries.append({
                "created": "",
                "status": "",
                "dir": job_dir,
                "pages": {},
            })
    entries.sort(key=lambda item: item["created"], reverse=True)
    return entries


def iter_job_lines(jobs_dir: Path, src_lang: str):
    """Ambil teks asli dari regions.json, utamakan OCR dari job terbaru.

    Job lama sering menyimpan OCR yang sudah diperbaiki di job baru. Dengan
    memproses job terbaru lebih dulu dan melewati region yang sama, dataset
    tidak menerima dua versi teks untuk halaman yang sama.
    """
    seen_regions: set[tuple[str, str]] = set()
    for entry in _job_entries(jobs_dir):
        for meta_path in sorted((entry["dir"] / "pages").glob("p*/regions.json")):
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            source = meta.get("src_lang") or "auto"
            if source != src_lang:
                continue
            page_id = meta_path.parent.name
            page_name = entry["pages"].get(page_id)
            for region in meta.get("regions") or []:
                if region.get("skipped"):
                    continue
                region_id = str(region.get("id") or "")
                if page_name and region_id:
                    key = (page_name, region_id)
                    if key in seen_regions:
                        continue
                    seen_regions.add(key)
                text = " ".join(str(region.get("original") or "").split())
                if text:
                    yield text


def load_seed(path: Path | None) -> list[dict]:
    if not path:
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        if row.get("src") and row.get("tgt"):
            rows.append({"src": str(row["src"]).strip(), "tgt": str(row["tgt"]).strip()})
    return rows


def is_useful(text: str, min_chars: int, max_chars: int) -> bool:
    if len(text) < min_chars or len(text) > max_chars:
        return False
    if NOISE_RE.match(text):
        return False
    return bool(JAPANESE_RE.search(text))


def translate_deepl_batch(
    lines: list[str],
    api_key: str,
    src_lang: str,
    tgt_lang: str,
    timeout: int = 90,
) -> list[str]:
    payload = {
        "text": lines,
        "source_lang": DEEPL_CODES.get(src_lang, src_lang.upper()),
        "target_lang": DEEPL_CODES.get(tgt_lang, tgt_lang.upper()),
        "context": "Manga dialogue. Natural, casual Indonesian that fits a speech bubble.",
    }
    headers = {"Authorization": f"DeepL-Auth-Key {api_key}"}
    last_error = ""
    for attempt in range(4):
        try:
            r = requests.post(deepl_url(api_key), data=payload, headers=headers, timeout=timeout)
            if r.status_code == 429 or r.status_code >= 500:
                last_error = f"HTTP {r.status_code}"
                time.sleep(2 ** attempt)
                continue
            if r.status_code in (403, 456):
                raise SystemExit("Kunci DeepL tidak valid atau kuota karakter habis.")
            if r.status_code >= 400:
                raise SystemExit(f"DeepL error {r.status_code}: {r.text[:300]}")
            rows = (r.json().get("translations") or [])
            if len(rows) != len(lines):
                raise SystemExit("Jumlah hasil DeepL tidak cocok dengan jumlah baris.")
            return [str(row.get("text") or "").strip() for row in rows]
        except requests.RequestException as exc:
            last_error = str(exc)
            time.sleep(2 ** attempt)
    raise SystemExit(f"DeepL gagal setelah beberapa percobaan: {last_error}")


def translate_google_batch(
    lines: list[str],
    src_lang: str,
    tgt_lang: str,
    timeout: int = 30,
    delay: float = 0.0,
) -> list[str]:
    session = requests.Session()
    out: list[str] = []
    for text in lines:
        params = {
            "client": "gtx",
            "sl": "auto" if src_lang == "auto" else src_lang,
            "tl": tgt_lang,
            "dt": "t",
            "q": text,
        }
        last_error = ""
        for attempt in range(4):
            try:
                r = session.get(GOOGLE_URL, params=params, timeout=timeout)
                if r.status_code == 429 or r.status_code >= 500:
                    last_error = f"HTTP {r.status_code}"
                    time.sleep(2 ** attempt)
                    continue
                if r.status_code >= 400:
                    raise SystemExit(f"Google error {r.status_code}: {r.text[:300]}")
                data = r.json()
                translated = "".join(
                    segment[0] for segment in (data[0] or []) if segment and segment[0]
                )
                if not translated:
                    raise SystemExit("Google tidak mengembalikan hasil.")
                out.append(translated)
                break
            except requests.RequestException as exc:
                last_error = str(exc)
                time.sleep(2 ** attempt)
        else:
            raise SystemExit(f"Google gagal setelah beberapa percobaan: {last_error}")
        if delay:
            time.sleep(delay)
    return out


def translate_mymemory_batch(
    lines: list[str],
    src_lang: str,
    tgt_lang: str,
    timeout: int = 30,
    delay: float = 0.0,
) -> list[str]:
    if src_lang == "auto":
        raise SystemExit("MyMemory tidak mendukung deteksi otomatis; set --src-lang ja.")
    session = requests.Session()
    out: list[str] = []
    for text in lines:
        last_error = ""
        for attempt in range(4):
            try:
                r = session.get(
                    MYMEMORY_URL,
                    params={"q": text, "langpair": f"{src_lang}|{tgt_lang}"},
                    timeout=timeout,
                )
                if r.status_code == 429 or r.status_code >= 500:
                    last_error = f"HTTP {r.status_code}"
                    time.sleep(2 ** attempt)
                    continue
                if r.status_code >= 400:
                    raise SystemExit(f"MyMemory error {r.status_code}: {r.text[:300]}")
                data = r.json()
                translated = html.unescape(
                    (data.get("responseData") or {}).get("translatedText") or ""
                )
                if "MYMEMORY WARNING" in translated.upper() or data.get("quotaFinished"):
                    raise SystemExit("Kuota MyMemory habis. Coba --teacher google atau DeepL.")
                if not translated:
                    raise SystemExit("MyMemory tidak mengembalikan hasil.")
                out.append(translated)
                break
            except requests.RequestException as exc:
                last_error = str(exc)
                time.sleep(2 ** attempt)
        else:
            raise SystemExit(f"MyMemory gagal setelah beberapa percobaan: {last_error}")
        if delay:
            time.sleep(delay)
    return out


def translate_batch(
    lines: list[str],
    teacher: str,
    api_key: str,
    src_lang: str,
    tgt_lang: str,
    delay: float = 0.0,
) -> list[str]:
    if teacher == "deepl":
        return translate_deepl_batch(lines, api_key, src_lang, tgt_lang)
    if teacher == "google":
        return translate_google_batch(lines, src_lang, tgt_lang, delay=delay)
    if teacher == "mymemory":
        return translate_mymemory_batch(lines, src_lang, tgt_lang, delay=delay)
    raise SystemExit(f"Guru terjemahan tidak dikenal: {teacher}")


def write_rows(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    tmp.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--jobs", type=Path, default=DEFAULT_JOBS, help="Folder data/jobs")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="File JSONL hasil")
    parser.add_argument("--seed", type=Path, default=None, help="JSONL pasangan manual yang ikut digabung")
    parser.add_argument("--teacher", choices=TEACHERS, default="deepl", help="Guru terjemahan: deepl (paling mirip target), google, atau mymemory (gratis)")
    parser.add_argument("--api-key", default=os.environ.get("DEEPL_API_KEY", ""), help="Kunci DeepL; hanya dipakai untuk --teacher deepl")
    parser.add_argument("--src-lang", default="ja")
    parser.add_argument("--tgt-lang", default="id")
    parser.add_argument("--min-chars", type=int, default=2)
    parser.add_argument("--max-chars", type=int, default=220)
    parser.add_argument("--max-lines", type=int, default=5000, help="Batas biaya; 0 = tanpa batas")
    parser.add_argument("--budget-chars", type=int, default=400_000, help="Perkiraan batas karakter; 0 = tanpa batas")
    parser.add_argument("--delay", type=float, default=0.0, help="Jeda antar baris untuk guru gratis, mis. 0.3 detik")
    parser.add_argument("--dry-run", action="store_true", help="Hanya tampilkan statistik, tanpa memanggil guru")
    parser.add_argument("--append", action="store_true", help="Gabung dengan file output yang sudah ada")
    parser.add_argument("--seed-only", action="store_true", help="Hanya gabungkan --seed/--append tanpa menerjemahkan OCR baru")
    args = parser.parse_args()

    seed_rows = load_seed(args.seed)
    existing: list[dict] = []
    if args.append and args.out.exists():
        existing = load_seed(args.out)

    seen: dict[str, str] = {}
    for row in seed_rows + existing:
        seen.setdefault(row["src"], row["tgt"])

    candidates: list[str] = []
    if not args.seed_only:
        for text in iter_job_lines(args.jobs, args.src_lang):
            if is_useful(text, args.min_chars, args.max_chars) and text not in seen:
                seen[text] = ""
                candidates.append(text)

    if args.max_lines > 0:
        candidates = candidates[: args.max_lines]
    total_chars = sum(len(text) for text in candidates)

    print(f"Guru terjemahan           : {args.teacher}")
    print(f"Pasangan manual/tersimpan : {len(seed_rows) + len(existing)}")
    print(f"Baris OCR baru            : {len(candidates)}")
    print(f"Perkiraan karakter        : {total_chars:,}")
    if len(candidates) < 500:
        print(
            f"PERINGATAN: hanya {len(candidates)} baris baru. Fine-tuning sebaiknya "
            "memakai minimal 500 pasangan; 3.000+ lebih stabil. Kumpulkan lebih banyak halaman.",
            file=sys.stderr,
        )
    if args.dry_run:
        for text in candidates[:15]:
            print("  -", text)
        return 0
    if not candidates:
        rows = [{"src": row["src"], "tgt": row["tgt"]} for row in seed_rows + existing]
        if rows:
            write_rows(args.out, rows)
            print(f"Tidak ada baris baru; {len(rows)} pasangan tersimpan -> {args.out}")
        else:
            print("Tidak ada baris baru. Tambahkan job terjemahan atau pakai --seed.")
        return 0
    if args.teacher == "deepl" and not args.api_key:
        print("DEEPL_API_KEY belum diatur. Set environment variable, pakai --api-key, atau pilih --teacher google/mymemory.", file=sys.stderr)
        return 2
    if args.budget_chars and total_chars > args.budget_chars:
        print(
            f"Perkiraan {total_chars:,} karakter melebihi --budget-chars {args.budget_chars:,}. "
            "Perkecil --max-lines atau naikkan budget.",
            file=sys.stderr,
        )
        return 2

    rows = [{"src": row["src"], "tgt": row["tgt"]} for row in seed_rows + existing]
    written = 0
    # DeepL bisa 50 baris sekali request. Guru gratis memproses per baris,
    # jadi pakai batch kecil agar hasil yang sudah didapat lebih sering tersimpan.
    chunk_size = 50 if args.teacher == "deepl" else 10
    for start in range(0, len(candidates), chunk_size):
        chunk = candidates[start:start + chunk_size]
        translated = translate_batch(
            chunk, args.teacher, args.api_key, args.src_lang, args.tgt_lang, delay=args.delay,
        )
        for source, target in zip(chunk, translated):
            target = " ".join(target.split())
            if not target or target.casefold() == source.casefold():
                continue
            rows.append({"src": source, "tgt": target})
            written += 1
        # Simpan per batch supaya hasil yang sudah didapat tidak hilang jika
        # provider gratis tiba-tiba membatasi permintaan.
        write_rows(args.out, rows)
        print(f"  {min(start + chunk_size, len(candidates))}/{len(candidates)} baris")

    print(f"Selesai: {written} pasangan baru, total {len(rows)} -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
