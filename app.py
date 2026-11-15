from __future__ import annotations

import asyncio
import json
import re
import shutil
import threading
import time
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from server import config, jobs, languages, settings_store
from server.translate import probe_engines, test_engine

app = FastAPI(title="MangaFlow", version=config.APP_VERSION)

settings_store.load()

JOB_RE = re.compile(r"^j_[a-f0-9]{12}$")
PAGE_RE = re.compile(r"^p\d{3}$")

CONTENT_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}

app.mount("/fonts", StaticFiles(directory=config.WEB_DIR / "fonts"), name="fonts")
app.mount("/assets", StaticFiles(directory=config.WEB_DIR / "assets"), name="assets")
app.mount("/demo", StaticFiles(directory=config.WEB_DIR / "demo"), name="demo")
app.mount("/files", StaticFiles(directory=config.JOBS_DIR), name="files")


@app.middleware("http")
async def _no_cache_static(request: Request, call_next):
    """Local app: always revalidate UI assets and generated files after an update."""
    response = await call_next(request)
    path = request.url.path
    if path == "/" or path.startswith(("/assets/", "/demo/", "/fonts/", "/files/")):
        response.headers["Cache-Control"] = "no-cache"
    return response


def _check_job_id(job_id: str) -> None:
    if not JOB_RE.match(job_id):
        raise HTTPException(404, "Job tidak ditemukan.")


def _check_page_id(page_id: str) -> None:
    if not PAGE_RE.match(page_id):
        raise HTTPException(404, "Halaman tidak ditemukan.")


def _validate_options(raw: dict) -> dict:
    defaults = {
        "src_lang": "auto",
        "tgt_lang": "id",
        "engine": "local",
        "font": "comic",
        "font_scale": 1.0,
        "text_color": "auto",
        "outline": False,
        "min_score": 0.5,
        "fallback_local": True,
        "tone": "santai",
    }
    opts = {**defaults, **(raw or {})}
    if not languages.is_supported_source(opts["src_lang"]):
        raise HTTPException(400, f"Bahasa sumber tidak didukung: {opts['src_lang']}")
    if not languages.is_supported_target(opts["tgt_lang"]):
        raise HTTPException(400, f"Bahasa target tidak didukung: {opts['tgt_lang']}")
    if opts["engine"] not in {"local", "nllb", "nllb-tuned", "google", "mymemory", "deepl", "gemini", "microsoft", "libretranslate", "ollama", "openai"}:
        raise HTTPException(400, "Mesin terjemahan tidak dikenal.")
    if opts["tone"] not in {"santai", "netral", "baku"}:
        opts["tone"] = "santai"
    if opts["font"] not in {"comic", "sans", "serif", "cjk"}:
        opts["font"] = "comic"
    if opts["text_color"] not in {"auto", "black", "white"}:
        opts["text_color"] = "auto"
    try:
        opts["font_scale"] = max(0.6, min(1.6, float(opts["font_scale"])))
    except Exception:
        opts["font_scale"] = 1.0
    try:
        opts["min_score"] = max(0.3, min(0.9, float(opts["min_score"])))
    except Exception:
        opts["min_score"] = 0.5
    opts["outline"] = bool(opts["outline"])
    opts["fallback_local"] = bool(opts["fallback_local"])
    return opts


@app.get("/", response_class=HTMLResponse)
async def index():
    html = (config.WEB_DIR / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


@app.get("/api/health")
async def health():
    return {"ok": True, "version": config.APP_VERSION}


@app.get("/api/config")
async def get_config():
    cached_targets = []
    for row in languages.target_languages():
        if languages.model_pair_cached("en", row["code"]):
            cached_targets.append(row["code"])
    return {
        "version": config.APP_VERSION,
        "languages": {
            "targets": languages.target_languages(),
            "sources": languages.source_languages(),
        },
        "engines": probe_engines(),
        "defaults": {
            "src_lang": "auto",
            "tgt_lang": "id",
            "engine": "local",
            "font": "comic",
            "font_scale": 1.0,
            "text_color": "auto",
            "outline": False,
            "min_score": 0.5,
            "fallback_local": True,
            "tone": "santai",
        },
        "limits": {
            "max_files": config.MAX_FILES,
            "max_upload_mb": config.MAX_UPLOAD_MB,
            "max_image_side": config.MAX_IMAGE_SIDE,
        },
        "cached_targets": cached_targets,
        "features": {
            "local_translation": True,
            "cbz": True,
            "editor": True,
            "batch": True,
        },
    }


@app.get("/api/engines")
async def engines():
    return {"engines": probe_engines()}


class SettingsRequest(BaseModel):
    values: dict = Field(default_factory=dict)


@app.get("/api/settings")
async def get_settings():
    return {"settings": settings_store.public(), "engines": probe_engines()}


@app.post("/api/settings")
async def save_settings(body: SettingsRequest):
    try:
        saved = await asyncio.to_thread(settings_store.save, body.values)
    except RuntimeError as exc:
        raise HTTPException(500, str(exc))
    return {"settings": saved, "engines": probe_engines()}


@app.post("/api/settings/test/{engine}")
async def test_settings(engine: str):
    allowed = {"local", "nllb", "nllb-tuned", "google", "mymemory", "deepl", "gemini", "microsoft", "libretranslate", "ollama", "openai"}
    if engine not in allowed:
        raise HTTPException(404, "Mesin terjemahan tidak dikenal.")
    return await asyncio.to_thread(test_engine, engine)


@app.post("/api/jobs")
async def create_job(
    files: list[UploadFile] = File(...),
    options: str = Form("{}"),
    title: str = Form(""),
):
    if len(files) > config.MAX_FILES + 20:
        raise HTTPException(400, "Terlalu banyak file.")
    try:
        raw_options = json.loads(options or "{}")
    except json.JSONDecodeError:
        raise HTTPException(400, "Opsi job tidak valid.")
    opts = _validate_options(raw_options)

    uploads: list[tuple[str, bytes]] = []
    total = 0
    for f in files:
        name = (f.filename or "file").replace("\\", "/").rsplit("/", 1)[-1][:180]
        data = await f.read()
        total += len(data)
        if total > config.MAX_UPLOAD_MB * 1024 * 1024:
            raise HTTPException(400, f"Ukuran upload melebihi {config.MAX_UPLOAD_MB} MB.")
        uploads.append((name, data))
    try:
        job = await asyncio.to_thread(jobs.create_job, uploads, opts, title.strip()[:120] or None)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"Gagal membuat job: {str(exc)[:300]}")
    return job


@app.get("/api/jobs")
async def api_list_jobs():
    return {"jobs": jobs.list_jobs()}


@app.get("/api/jobs/{job_id}")
async def api_get_job(job_id: str):
    _check_job_id(job_id)
    job = jobs.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job tidak ditemukan.")
    return jobs.serialize_job(job)


@app.post("/api/jobs/{job_id}/cancel")
async def api_cancel_job(job_id: str):
    _check_job_id(job_id)
    with jobs.JOB_LOCK:
        job = jobs.JOBS.get(job_id)
        if not job:
            raise HTTPException(404, "Job tidak ditemukan.")
        job["cancel"] = True
        jobs._persist(job, force=True)  # noqa: SLF001
    return {"ok": True}


@app.delete("/api/jobs/{job_id}")
async def api_delete_job(job_id: str):
    _check_job_id(job_id)
    try:
        ok = await asyncio.to_thread(jobs.delete_job, job_id)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc))
    if not ok:
        raise HTTPException(404, "Job tidak ditemukan.")
    return {"ok": True}


@app.get("/api/jobs/{job_id}/pages/{page_id}")
async def api_get_page(job_id: str, page_id: str):
    _check_job_id(job_id)
    _check_page_id(page_id)
    page = jobs.get_page(job_id, page_id)
    if not page:
        raise HTTPException(404, "Halaman tidak ditemukan.")
    return page


class RenderRequest(BaseModel):
    regions: list[dict] = Field(default_factory=list)
    style: dict = Field(default_factory=dict)


@app.post("/api/jobs/{job_id}/pages/{page_id}/render")
async def api_render_page(job_id: str, page_id: str, body: RenderRequest):
    _check_job_id(job_id)
    _check_page_id(page_id)
    job = jobs.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job tidak ditemukan.")
    page = jobs.get_page(job_id, page_id)
    if not page or page.get("status") != "done":
        raise HTTPException(409, "Halaman belum selesai diproses.")
    style = {
        "font": body.style.get("font", "comic"),
        "font_scale": max(0.6, min(1.6, float(body.style.get("font_scale", 1.0)))),
        "text_color": body.style.get("text_color", "auto"),
        "outline": bool(body.style.get("outline", False)),
        "target_lang": job["options"].get("tgt_lang", "id"),
    }
    if style["font"] not in {"comic", "sans", "serif", "cjk"}:
        style["font"] = "comic"
    if style["text_color"] not in {"auto", "black", "white"}:
        style["text_color"] = "auto"
    try:
        updated = await asyncio.to_thread(jobs.update_page, job_id, page_id, body.regions, style)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"Render ulang gagal: {str(exc)[:300]}")
    if not updated:
        raise HTTPException(404, "Halaman tidak ditemukan.")
    return updated


@app.get("/api/jobs/{job_id}/pages/{page_id}/download")
async def api_download_page(job_id: str, page_id: str):
    _check_job_id(job_id)
    _check_page_id(page_id)
    page = jobs.get_page(job_id, page_id)
    if not page or not page.get("translated"):
        raise HTTPException(404, "Hasil halaman belum tersedia.")
    path = jobs.page_dir_for(job_id, page_id) / "translated.png"
    if not path.exists():
        raise HTTPException(404, "File tidak ditemukan.")
    name = f"{page['index']:03d}_{Path(page['name']).stem}.png"
    return FileResponse(path, media_type="image/png", filename=name)


@app.get("/api/jobs/{job_id}/download")
async def api_download_job(job_id: str):
    _check_job_id(job_id)
    job = jobs.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job tidak ditemukan.")
    if job["status"] not in ("done",):
        raise HTTPException(409, "Job belum selesai.")
    try:
        path, filename = await asyncio.to_thread(jobs.make_download, job_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc))
    return FileResponse(
        path,
        media_type="application/zip",
        filename=filename,
        background=BackgroundTask(lambda p=path: p.unlink(missing_ok=True)),
    )


@app.exception_handler(404)
async def not_found(request: Request, exc):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": "Tidak ditemukan."}, status_code=404)
    return JSONResponse({"detail": "Tidak ditemukan."}, status_code=404)


def run():
    import uvicorn

    uvicorn.run("app:app", host=config.HOST, port=config.PORT, reload=False, log_level="info")


if __name__ == "__main__":
    run()
