from __future__ import annotations

import copy
import io
import json
import os
import re
import shutil
import threading
import time
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageOps

from . import config
from .pipeline import process_page, rerender_page

JOB_LOCK = threading.RLock()
JOBS: dict[str, dict] = {}
EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="manga-worker")
_PERSIST_AT: dict[str, float] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def natural_key(value: str):
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", value)]


def _atomic_write(path: Path, data: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def _persist(job: dict, force: bool = False) -> None:
    jid = job["id"]
    now = time.time()
    if not force and now - _PERSIST_AT.get(jid, 0) < 0.6:
        return
    _PERSIST_AT[jid] = now
    job["updated_at"] = _now()
    try:
        _atomic_write(config.JOBS_DIR / jid / "job.json", job)
    except Exception:  # noqa: BLE001
        pass


def _page_urls(job_id: str, page: dict) -> dict:
    base = f"/files/{job_id}/pages/{page['id']}"
    v = page.get("v", 0)
    urls = {
        "original_url": f"{base}/original.png",
        "thumb_url": f"{base}/thumb.jpg?v={v}",
    }
    if page.get("translated"):
        urls["translated_url"] = f"{base}/translated.png?v={v}"
        urls["translated_thumb_url"] = f"{base}/translated_thumb.jpg?v={v}"
    if page.get("clean"):
        urls["clean_url"] = f"{base}/clean.png"
    return urls


def serialize_job(job: dict, include_pages: bool = True) -> dict:
    out = copy.deepcopy(job)
    if include_pages:
        pages = []
        for page in out.get("pages", []):
            page = dict(page)
            page.update(_page_urls(job["id"], page))
            pages.append(page)
        out["pages"] = pages
    else:
        out.pop("pages", None)
    return out


def _normalize_image(data: bytes) -> tuple[Image.Image, str]:
    im = Image.open(io.BytesIO(data))
    im.seek(0)
    im = ImageOps.exif_transpose(im)
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
        im = Image.alpha_composite(bg, im).convert("RGB")
    else:
        im = im.convert("RGB")
    if max(im.size) > config.MAX_IMAGE_SIDE:
        ratio = config.MAX_IMAGE_SIDE / max(im.size)
        im = im.resize((max(1, int(im.width * ratio)), max(1, int(im.height * ratio))), Image.Resampling.LANCZOS)
    return im, "PNG"


def _archive_images(data: bytes) -> list[tuple[str, bytes]]:
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ValueError("Arsip ZIP/CBZ tidak valid.") from exc
    entries = []
    with zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = info.filename.replace("\\", "/")
            if name.startswith("__MACOSX/") or Path(name).name.startswith("."):
                continue
            ext = Path(name).suffix.lower()
            if ext not in config.SUPPORTED_IMAGE_EXT:
                continue
            entries.append((name, info))
        entries.sort(key=lambda item: natural_key(item[0]))
        out = []
        for name, info in entries:
            if info.file_size > 80 * 1024 * 1024:
                continue
            out.append((Path(name).name, zf.read(info)))
        return out


def _save_page(job_dir: Path, index: int, name: str, data: bytes) -> dict:
    page_id = f"p{index:03d}"
    page_dir = job_dir / "pages" / page_id
    page_dir.mkdir(parents=True, exist_ok=True)
    im, _ = _normalize_image(data)
    im.save(page_dir / "original.png", "PNG", compress_level=3)
    thumb = im.copy()
    thumb.thumbnail((420, 10000), Image.Resampling.LANCZOS)
    thumb.save(page_dir / "thumb.jpg", "JPEG", quality=86, optimize=True)
    return {
        "id": page_id,
        "index": index,
        "name": name,
        "width": im.width,
        "height": im.height,
        "status": "queued",
        "stage": "Menunggu",
        "progress": 0,
        "message": "",
        "error": None,
        "src_lang": None,
        "region_count": 0,
        "translated": False,
        "v": 0,
    }


def create_job(uploads: list[tuple[str, bytes]], options: dict, title: str | None = None) -> dict:
    if not uploads:
        raise ValueError("Tidak ada file yang diunggah.")
    total = sum(len(data) for _, data in uploads)
    if total > config.MAX_UPLOAD_MB * 1024 * 1024:
        raise ValueError(f"Ukuran upload melebihi {config.MAX_UPLOAD_MB} MB.")

    job_id = "j_" + uuid.uuid4().hex[:12]
    job_dir = config.JOBS_DIR / job_id
    (job_dir / "pages").mkdir(parents=True, exist_ok=True)

    images: list[tuple[str, bytes]] = []
    archive_title = None
    for filename, data in uploads:
        ext = Path(filename).suffix.lower()
        if ext in config.SUPPORTED_ARCHIVE_EXT:
            if archive_title is None:
                archive_title = Path(filename).stem
            images.extend(_archive_images(data))
        elif ext in config.SUPPORTED_IMAGE_EXT:
            images.append((filename, data))
    if not images:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise ValueError("Tidak ada gambar di dalam upload (PNG, JPG, WEBP, ZIP, atau CBZ).")
    if len(images) > config.MAX_FILES:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise ValueError(f"Maksimal {config.MAX_FILES} halaman per job.")

    pages = []
    try:
        for i, (name, data) in enumerate(images, start=1):
            pages.append(_save_page(job_dir, i, name, data))
    except Exception:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise

    job = {
        "id": job_id,
        "created_at": _now(),
        "updated_at": _now(),
        "status": "queued",
        "stage": "Menunggu antrean",
        "message": "",
        "progress": 0,
        "error": None,
        "options": options,
        "title": title or archive_title or "Manga tanpa judul",
        "page_count": len(pages),
        "done_count": 0,
        "warnings": [],
        "pages": pages,
    }
    with JOB_LOCK:
        JOBS[job_id] = job
        _persist(job, force=True)
    EXECUTOR.submit(_run_job, job_id)
    return serialize_job(job)


def _run_job(job_id: str) -> None:
    with JOB_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return
        job["status"] = "running"
        job["stage"] = "Memulai"
        _persist(job, force=True)
    total = len(job["pages"])
    done = 0

    for idx, page in enumerate(job["pages"], start=1):
        with JOB_LOCK:
            if job.get("cancel"):
                job["status"] = "cancelled"
                job["stage"] = "Dibatalkan"
                _persist(job, force=True)
                return
            page["status"] = "running"
            page["stage"] = "Membaca teks"
            page["progress"] = 2
            page["message"] = ""
            job["stage"] = f"Halaman {idx}/{total}"
            _persist(job, force=True)

        last = {"t": 0.0}

        def progress(pct: int, message: str, page=page, idx=idx, last=last):
            with JOB_LOCK:
                page["progress"] = max(page.get("progress", 0), min(100, int(pct)))
                page["stage"] = message
                if message:
                    page["message"] = message
                job["progress"] = int(sum(p.get("progress", 0) for p in job["pages"]) / max(1, total))
                job["stage"] = f"Halaman {idx}/{total}"
                job["message"] = message
                now = time.time()
                if now - last["t"] > 0.7:
                    last["t"] = now
                    _persist(job)

        try:
            page_dir = config.JOBS_DIR / job_id / "pages" / page["id"]
            meta = process_page(page_dir, job["options"], progress=progress)
            with JOB_LOCK:
                page["status"] = "done"
                page["stage"] = "Selesai"
                page["progress"] = 100
                page["message"] = ""
                page["translated"] = True
                page["src_lang"] = meta.get("src_lang")
                page["region_count"] = meta.get("stats", {}).get("translated", 0)
                page["width"] = meta.get("width", page.get("width"))
                page["height"] = meta.get("height", page.get("height"))
                page["v"] = int(time.time() * 1000)
                for warning in meta.get("warnings", []):
                    if warning not in job["warnings"]:
                        job["warnings"].append(f"{page['name']}: {warning}")
                _persist(job, force=True)
            done += 1
        except Exception as exc:  # noqa: BLE001
            with JOB_LOCK:
                page["status"] = "error"
                page["stage"] = "Gagal"
                page["error"] = str(exc)[:800]
                page["progress"] = 100
                page["message"] = ""
                _persist(job, force=True)

        with JOB_LOCK:
            job["done_count"] = done
            job["progress"] = int(sum(p.get("progress", 0) for p in job["pages"]) / max(1, total))

    with JOB_LOCK:
        job["status"] = "done" if any(p["status"] == "done" for p in job["pages"]) else "error"
        job["stage"] = "Selesai" if job["status"] == "done" else "Gagal"
        job["message"] = ""
        job["progress"] = 100
        if job["status"] == "error":
            job["error"] = "Semua halaman gagal diproses."
        _persist(job, force=True)


def get_job(job_id: str) -> dict | None:
    with JOB_LOCK:
        job = JOBS.get(job_id)
        return copy.deepcopy(job) if job else None


def list_jobs(limit: int = 20) -> list[dict]:
    with JOB_LOCK:
        rows = sorted(JOBS.values(), key=lambda j: j.get("created_at", ""), reverse=True)[:limit]
        return [
            {
                "id": j["id"],
                "title": j.get("title"),
                "status": j.get("status"),
                "progress": j.get("progress"),
                "page_count": j.get("page_count"),
                "done_count": j.get("done_count"),
                "created_at": j.get("created_at"),
            }
            for j in rows
        ]


def page_dir_for(job_id: str, page_id: str) -> Path:
    return config.JOBS_DIR / job_id / "pages" / page_id


def get_page(job_id: str, page_id: str) -> dict | None:
    with JOB_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return None
        page = next((p for p in job["pages"] if p["id"] == page_id), None)
        if not page:
            return None
        page = copy.deepcopy(page)
    page.update(_page_urls(job_id, page))
    meta_path = page_dir_for(job_id, page_id) / "regions.json"
    if meta_path.exists():
        try:
            page["regions"] = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            page["regions"] = None
    return page


def update_page(job_id: str, page_id: str, regions: list[dict], style: dict) -> dict | None:
    with JOB_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return None
        page = next((p for p in job["pages"] if p["id"] == page_id), None)
        if not page or page["status"] != "done":
            return None
    meta = rerender_page(page_dir_for(job_id, page_id), regions, style)
    with JOB_LOCK:
        page["v"] = int(time.time() * 1000)
        page["region_count"] = len(meta.get("regions", []))
        _persist(job, force=True)
    return get_page(job_id, page_id)


def delete_job(job_id: str) -> bool:
    with JOB_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return False
        if job.get("status") in ("queued", "running"):
            raise RuntimeError("Job masih berjalan. Tunggu sampai selesai.")
        JOBS.pop(job_id, None)
    shutil.rmtree(config.JOBS_DIR / job_id, ignore_errors=True)
    return True


def load_existing() -> None:
    if not config.JOBS_DIR.exists():
        return
    for job_dir in config.JOBS_DIR.iterdir():
        if not job_dir.is_dir():
            continue
        path = job_dir / "job.json"
        if not path.exists():
            continue
        try:
            job = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if job.get("status") in ("queued", "running"):
            job["status"] = "error"
            job["error"] = "Server berhenti saat job berjalan. Silakan unggah ulang."
            job["stage"] = "Terhenti"
            for page in job.get("pages", []):
                if page.get("status") in ("queued", "running"):
                    page["status"] = "error"
                    page["error"] = "Terhenti karena server dimatikan."
                    page["progress"] = 100
        JOBS[job["id"]] = job


def make_download(job_id: str) -> tuple[Path, str]:
    job = get_job(job_id)
    if not job:
        raise FileNotFoundError("Job tidak ditemukan.")
    tmp_dir = config.JOBS_DIR / job_id / "downloads"
    tmp_dir.mkdir(exist_ok=True)
    out = tmp_dir / f"mangaflow_{job_id}.zip"
    stem = re.sub(r"[^A-Za-z0-9 _-]+", "", job.get("title") or "manga").strip() or "manga"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        dialog = []
        for page in job["pages"]:
            pdir = config.JOBS_DIR / job_id / "pages" / page["id"]
            if page.get("status") == "done" and (pdir / "translated.png").exists():
                arc = f"{page['index']:03d}_{Path(page['name']).stem}.png"
                zf.write(pdir / "translated.png", arc)
                meta_path = pdir / "regions.json"
                if meta_path.exists():
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                    dialog.append(f"=== {page['index']:03d} · {page['name']} ===")
                    for region in meta.get("regions", []):
                        if region.get("skipped"):
                            continue
                        dialog.append(f"- {region.get('original', '')}")
                        dialog.append(f"+ {region.get('translated', '')}")
                    dialog.append("")
        if dialog:
            zf.writestr("dialog.txt", "\n".join(dialog).encode("utf-8"))
    return out, f"{stem}_translated.zip"


load_existing()
