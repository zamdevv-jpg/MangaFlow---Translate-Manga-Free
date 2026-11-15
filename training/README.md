# Melatih NLLB-200 agar lebih mirip DeepL

Folder ini berisi alur **distilasi DeepL → LoRA NLLB-200 → CTranslate2 int8** untuk dialog manga Jepang → Indonesia.

Tujuannya bukan menyalin DeepL, tetapi memakai DeepL sebagai **guru** agar NLLB belajar:
- pilihan kata yang natural,
- gaya bahasa santai khas manga,
- kalimat pendek yang muat di balon,
- konsistensi istilah dan nama.

> **Status saat ini:** toolkit sudah siap, tetapi model latihan **belum dibuat**. Komputer yang dipakai mengembangkan MangaFlow belum punya GPU, `torch`/`transformers`/`peft`, dataset berpasangan, atau kunci DeepL. Tanpa itu, melatih model 600M/1.3B tidak realistis. Mesin **NLLB-200 manga (1.3B)** tetap bisa dipakai dengan model 1.3B dasar; UI menandainya “belum fine-tune”.
>
> Jika kamu tidak punya GPU lokal, buka **[`colab_finetune.ipynb`](colab_finetune.ipynb)** di Google Colab. Notebook itu menjalankan seluruh alur (kumpulkan data → LoRA → ekspor CT2 → unduh model) di GPU T4 gratis.

## Yang dibutuhkan

| Kebutuhan | Keterangan |
| --- | --- |
| GPU NVIDIA | 8 GB VRAM atau lebih disarankan. 6 GB bisa dengan batch kecil. |
| Python 3.10+ | Sama seperti server MangaFlow. |
| DeepL API | Akun gratis sudah cukup untuk percobaan (kuota karakter terbatas). |
| Dataset | Minimal 500–1.000 pasangan untuk uji coba; 3.000–20.000 untuk hasil stabil. |
| Waktu | Beberapa jam di GPU; bisa berhari-hari di CPU. |

Pasang dependensi latihan di environment terpisah agar tidak mengganggu server:

```bash
python -m venv .venv-train
# Windows
.venv-train\Scripts\activate
# Linux/macOS
source .venv-train/bin/activate

pip install -r training/requirements-train.txt
```

`torch` dengan CUDA biasanya perlu dipasang dari indeks PyTorch sesuai versi driver. Lihat <https://pytorch.org/get-started/locally/>.

## Tanpa GPU lokal: Google Colab

1. Buat ZIP folder `manga-translator` (tanpa `data/models/` agar kecil).
2. Buka <https://colab.research.google.com>, pilih **Upload**, lalu unggah `training/colab_finetune.ipynb`.
3. Di menu **Runtime → Change runtime type**, pilih **T4 GPU**.
4. Jalankan sel dari atas ke bawah. Notebook akan meminta ZIP project dan (opsional) kunci DeepL.
5. Setelah selesai, unduh `nllb-manga-ct2-int8.zip` dan ekstrak ke `data/models/`.

Notebook memakai guru `deepl` secara default. Jika tidak punya kunci DeepL, ubah `TEACHER = "google"` atau `"mymemory"` di sel pengaturan. Kualitas guru gratis tidak sebaik DeepL, tetapi cukup untuk percobaan awal. Endpoint Google gratis sering membalas `429`; MyMemory biasanya lebih stabil untuk dataset kecil, meskipun kualitasnya lebih rendah.

## 1. Kumpulkan dialog dari job MangaFlow

MangaFlow sudah menyimpan hasil OCR di `data/jobs/*/pages/*/regions.json`. Skrip berikut mengambil **teks Jepang asli**, menerjemahkannya dengan guru pilihan, lalu menulis JSONL.

```bash
set DEEPL_API_KEY=xxxxxxxx:fx
python training/collect_deepl.py --dry-run
python training/collect_deepl.py --max-lines 3000

:: Tanpa kunci DeepL (gratis, kualitas lebih rendah)
python training/collect_deepl.py --teacher google --max-lines 1000 --delay 0.3
python training/collect_deepl.py --teacher mymemory --max-lines 500 --delay 0.5 --src-lang ja
```

Hasil default: `training/data/pairs.jsonl`.

Opsi penting:

| Opsi | Fungsi |
| --- | --- |
| `--jobs data/jobs` | Folder job yang dibaca. |
| `--teacher deepl\|google\|mymemory` | Guru terjemahan. DeepL paling dekat dengan target; Google/MyMemory gratis. |
| `--seed training/data/sample_pairs.jsonl` | Gabungkan pasangan manual/contoh. |
| `--seed-only` | Hanya gabungkan `--seed`/`--append`, tanpa menerjemahkan OCR baru. |
| `--max-lines 3000` | Batas jumlah baris baru (kontrol biaya/rate limit). |
| `--budget-chars 400000` | Batas perkiraan karakter. |
| `--delay 0.3` | Jeda antar baris untuk guru gratis agar tidak kena rate limit. |
| `--append` | Tambahkan ke file output yang sudah ada. |
| `--dry-run` | Lihat statistik tanpa memanggil guru. |

**Target data:** minimal 500 pasangan; 3.000+ lebih stabil. Satu halaman manga biasanya menghasilkan 3–8 baris dialog, jadi 500 pasangan berarti sekitar 80–150 halaman. Skrip memberi peringatan jika datanya masih di bawah 500.

Skrip memproses job terbaru lebih dulu dan melewati region yang sama dari job lama, jadi perbaikan OCR di job baru otomatis dipakai dan teks lama yang salah tidak ikut masuk.

Tips data:
- Gunakan halaman yang bersih dan OCR-nya benar. **Sampah masuk, sampah keluar.**
- Buang SFX, tanda baca saja, dan baris yang bukan dialog.
- Periksa nama karakter dan istilah secara manual; DeepL bisa tidak konsisten.
- Jangan hanya memakai terjemahan mesin mentah tanpa ditinjau. Koreksi manual beberapa ratus baris memberi peningkatan besar.
- Simpan data uji terpisah, misalnya `training/data/test.jsonl`, agar skor tidak menipu.

### Periksa dataset

Sebelum melatih, jalankan pemeriksa kualitas:

```bash
python training/check_dataset.py --data training/data/pairs.jsonl --show 20
```

Skrip menampilkan jumlah pasangan, duplikat, panjang rata-rata, dan baris mencurigakan (misalnya OCR sampah atau target yang jauh lebih panjang). Ia tidak mengubah data; buang/koreksi manual baris yang ditandai.

Format JSONL:

```json
{"src": "お前、何者だ？", "tgt": "Kau siapa?"}
{"src": "待って！まだ話は終わってない！", "tgt": "Tunggu! Ceritanya belum selesai!"}
```

## 2. Fine-tune LoRA

```bash
python training/finetune_nllb.py \
  --data training/data/pairs.jsonl \
  --output training/out/nllb-manga-lora \
  --epochs 4 \
  --batch-size 4 \
  --grad-accum 4 \
  --lr 1e-4 \
  --fp16 \
  --val-out training/data/test.jsonl
```

Yang dilakukan skrip:
- memuat `facebook/nllb-200-distilled-600M`,
- memasang LoRA pada attention dan feed-forward,
- melatih dengan `jpn_Jpan` → `ind_Latn`,
- menyimpan adapter + tokenizer + `training_meta.json`,
- menyimpan split validasi ke `training/data/test.jsonl` (jika `--val-out` diisi) agar bisa dipakai `evaluate.py`.

Jika VRAM kurang, kecilkan `--batch-size 2` dan naikkan `--grad-accum 8`, atau turunkan `--max-length 128`. Jika hasil terlalu menghafal data, turunkan `--epochs` atau tambah data. Dataset di bawah 500 pasangan biasanya hanya menghafal; jangan harap model generalisasi.

## 3. Gabungkan dan ekspor ke CTranslate2

```bash
python training/export_ct2.py --adapter training/out/nllb-manga-lora --force
```

Hasil default: `data/models/nllb-200-manga-ct2-int8`.

Skrip akan:
1. menggabungkan adapter LoRA ke model penuh,
2. mengonversi ke CTranslate2 `int8`,
3. menyalin `sentencepiece.bpe.model`, `shared_vocabulary.txt`, dan file tokenizer,
4. memeriksa kelengkapan model.

Jika ada peringatan file yang belum lengkap, salin manual dari model dasar:

```text
data/models/nllb-200-distilled-600M-ct2-int8
```

## 4. Pakai di MangaFlow

Secara default MangaFlow mencari model latihan di:

```text
data/models/nllb-200-manga-ct2-int8
```

Atau atur lewat environment variable:

```bash
set MANGA_NLLB_TUNED_DIR=D:\model\nllb-manga-ct2-int8
python app.py
```

Setelah server restart:
- mesin **NLLB-200 manga (1.3B)** akan memakai model latihan ini lebih dulu daripada model 1.3B dasar,
- fallback CJK dan mesin **NLLB-200** otomatis memilih model latihan lebih dulu,
- pilihan **Gaya bahasa → Santai** tetap diterapkan setelah terjemahan.

## 5. Evaluasi

Siapkan data uji yang tidak dipakai saat latihan:

```bash
python training/evaluate.py \
  --data training/data/test.jsonl \
  --model data/models/nllb-200-distilled-600M-ct2-int8 \
  --compare data/models/nllb-200-manga-ct2-int8 \
  --limit 150
```

Skrip menampilkan BLEU, chrF, dan contoh berdampingan. Angka lebih tinggi belum tentu lebih enak dibaca; untuk manga, **baca manual** tetap penting. Perhatikan:
- apakah kalimat terasa seperti bahasa sehari-hari,
- apakah panjangnya muat di balon,
- apakah nama dan istilah konsisten,
- apakah ada halusinasi atau pengulangan.

## Cara kerja dan batasannya

- DeepL adalah guru, bukan target yang bisa disamai persis. Model 600M dengan data kecil biasanya mendekati gaya DeepL, bukan menyamainya.
- LoRA hanya melatih sebagian kecil parameter, sehingga cepat dan hemat VRAM, tetapi tidak menambah pengetahuan bahasa baru.
- Kualitas sangat bergantung pada domain data. Dialog manga action berbeda dengan romance atau komedi.
- Jangan melatih pada bahasa target selain Indonesia dengan satu adapter campur; buat dataset dan adapter terpisah per pasangan bahasa.
- Perhatikan syarat layanan DeepL dan hak cipta manga. Gunakan scan yang kamu berhak proses, dan jangan menyebarkan dataset berhak cipta.

## Troubleshooting

**Google/MyMemory kena 429 atau kuota habis**  
Naikkan `--delay` (mis. `0.5` atau `1.0`), perkecil `--max-lines`, lalu lanjutkan dengan `--append`. Hasil per batch sudah disimpan, jadi tidak perlu mengulang dari nol. Untuk data serius, pakai DeepL.

**Colab terputus di tengah training**  
Dataset dan adapter tersimpan di `/content`, tetapi runtime Colab bisa hilang. Jalankan ulang dari sel upload, lalu gunakan `--append` untuk data dan `--resume` untuk training.

**Dataset terlalu kecil**  
Skrip memberi peringatan di bawah 500 pasangan. Tambah halaman manga atau koreksi manual. Fine-tuning dengan 50–100 baris hanya akan menghafal contoh, bukan memperbaiki gaya secara umum.

**`Dependensi latihan belum terpasang`**  
Aktifkan venv latihan, lalu `pip install -r training/requirements-train.txt`.

**CUDA out of memory**  
Turunkan `--batch-size`, naikkan `--grad-accum`, atau pakai `--max-length 128`.

**Hasil latihan buruk / berulang**  
Tambahkan data, periksa kualitas pasangan, turunkan epoch, atau naikkan `--lora-r` sedikit. Pastikan data uji tidak bocor ke data latih.

**Model tidak muncul di UI**  
Pastikan empat file ini ada di folder model: `model.bin`, `sentencepiece.bpe.model`, `shared_vocabulary.txt`, `config.json`. Lalu restart server.

**Ingin kembali ke model dasar**  
Pilih mesin **NLLB-200 (lokal)** dan hapus/rename folder model latihan, atau set `MANGA_NLLB_TUNED_DIR` ke folder kosong.
