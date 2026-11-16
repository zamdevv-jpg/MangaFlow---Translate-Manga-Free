/* ============================================================
   MangaFlow — background service worker
   Semua permintaan ke server lokal lewat sini agar halaman web
   tidak perlu berurusan dengan CORS.
   ============================================================ */

const DEFAULT_SERVER = "http://127.0.0.1:8765";
const SETTINGS_KEY = "mf_settings";
const LAST_JOB_KEY = "mf_last_job";
const PENDING_SHOT_KEY = "mf_pending_shot";
const JOB_RE = /^j_[a-f0-9]{12}$/;
const MAX_BATCH_ITEMS = 40;
const MAX_UPLOAD_BYTES = 450 * 1024 * 1024;

const DEFAULT_SETTINGS = {
  serverUrl: DEFAULT_SERVER,
  autoTranslate: false,
  targetLang: "id",
  sourceLang: "auto",
  engine: "local",
  tone: "santai",
};

function normalizeServer(value) {
  const raw = String(value || DEFAULT_SERVER).trim();
  let url;
  try {
    url = new URL(/^[a-z]+:\/\//i.test(raw) ? raw : `http://${raw}`);
  } catch {
    throw new Error("Alamat server tidak valid.");
  }
  if (!["http:", "https:"].includes(url.protocol)) {
    throw new Error("Alamat server harus diawali http:// atau https://.");
  }
  const path = url.pathname.replace(/\/+$/, "");
  return `${url.origin}${path === "/" ? "" : path}`;
}

async function getSettings() {
  const stored = await chrome.storage.sync.get(SETTINGS_KEY);
  return { ...DEFAULT_SETTINGS, ...(stored[SETTINGS_KEY] || {}) };
}

function friendlyError(error) {
  const text = String(error?.message || error || "Terjadi kesalahan.");
  if (/failed to fetch|networkerror|load failed|network request failed/i.test(text)) {
    return "Server MangaFlow tidak bisa dihubungi. Pastikan server lokal sudah berjalan.";
  }
  if (/extension context invalidated/i.test(text)) {
    return "Ekstensi baru dimuat ulang. Tutup lalu buka kembali popup MangaFlow.";
  }
  return text;
}

async function apiJson(server, path, options = {}) {
  const res = await fetch(`${server}${path}`, { cache: "no-store", ...options });
  if (!res.ok) {
    let detail = `Server membalas ${res.status}.`;
    try {
      const data = await res.json();
      detail = data.detail || data.message || detail;
    } catch {
      /* respons bukan JSON */
    }
    throw new Error(detail);
  }
  return res.json();
}

async function fetchWithTimeout(url, options = {}, ms = 8000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), ms);
  try {
    return await fetch(url, { ...options, signal: controller.signal });
  } catch (error) {
    if (error?.name === "AbortError") {
      throw new Error("Server terlalu lama merespons.");
    }
    throw error;
  } finally {
    clearTimeout(timer);
  }
}

/* ------------------------------------------------------------------ server */

async function serverInfo(serverOverride) {
  const settings = await getSettings();
  const server = normalizeServer(serverOverride || settings.serverUrl);
  const health = await apiJson(server, "/api/health");
  let config = null;
  try {
    config = await apiJson(server, "/api/config");
  } catch {
    config = null;
  }
  return { ok: true, server, health, config, settings };
}

/* -------------------------------------------------------------------- job */

function filenameFor(url, mime, fallback = "mangaflow-page.png") {
  try {
    const path = new URL(url).pathname;
    const base = decodeURIComponent(path.split("/").pop() || "");
    if (/\.(png|jpe?g|webp|bmp|tiff?|gif)$/i.test(base)) {
      return base.slice(0, 120);
    }
  } catch {
    /* URL tidak valid */
  }
  const ext =
    {
      "image/jpeg": "jpg",
      "image/webp": "webp",
      "image/gif": "gif",
      "image/bmp": "bmp",
      "image/tiff": "tiff",
      "image/png": "png",
    }[String(mime || "").toLowerCase()] || "png";
  return fallback.replace(/\.[a-z0-9]+$/i, `.${ext}`);
}

async function imageBlob(payload) {
  if (payload.dataUrl) {
    const res = await fetch(payload.dataUrl);
    if (!res.ok) throw new Error("Gambar tangkapan layar tidak bisa dibaca.");
    return { blob: await res.blob(), filename: payload.filename || "screenshot.png" };
  }
  if (!payload.srcUrl) throw new Error("Gambar halaman tidak ditemukan.");
  const res = await fetchWithTimeout(payload.srcUrl, {
    credentials: "include",
    cache: "no-store",
    referrerPolicy: "no-referrer",
  }, 20000);
  if (!res.ok) {
    throw new Error(`Gambar tidak bisa diambil (HTTP ${res.status}).`);
  }
  const blob = await res.blob();
  if (!blob.size) throw new Error("Gambar kosong atau tidak bisa diambil.");
  return {
    blob,
    filename: payload.filename || filenameFor(payload.srcUrl, blob.type),
  };
}

async function startJob(payload = {}) {
  const settings = { ...(await getSettings()), ...(payload.settings || {}) };
  const server = normalizeServer(payload.server || settings.serverUrl);
  const items =
    Array.isArray(payload.items) && payload.items.length ? payload.items : [payload];
  if (items.length > MAX_BATCH_ITEMS) {
    throw new Error(`Maksimal ${MAX_BATCH_ITEMS} gambar per terjemahan.`);
  }

  const options = {
    src_lang: payload.srcLang || settings.sourceLang || "auto",
    tgt_lang: payload.tgtLang || settings.targetLang || "id",
    engine: payload.engine || settings.engine || "local",
    font: "comic",
    font_scale: 1.0,
    text_color: "auto",
    outline: false,
    min_score: 0.5,
    fallback_local: true,
    tone: payload.tone || settings.tone || "santai",
  };

  const title = String(payload.title || "Ekstensi MangaFlow").trim().slice(0, 120);
  const form = new FormData();
  let totalBytes = 0;
  for (const item of items) {
    const { blob, filename } = await imageBlob(item);
    totalBytes += blob.size;
    if (totalBytes > MAX_UPLOAD_BYTES) {
      throw new Error("Total gambar terlalu besar untuk satu job. Coba beberapa gambar saja.");
    }
    form.append("files", blob, filename);
  }
  form.append("options", JSON.stringify(options));
  form.append("title", title || "Ekstensi MangaFlow");

  const job = await apiJson(server, "/api/jobs", { method: "POST", body: form });
  const pages = (Array.isArray(job.pages) ? job.pages : []).map((page) => ({
    id: page.id,
    status: page.status,
  }));
  return {
    ok: true,
    server,
    jobId: job.id,
    pageId: pages[0]?.id || "p001",
    pages,
    job,
  };
}

function pageView(server, jobId, page) {
  const done = page.status === "done";
  const version = page.v ? `?v=${encodeURIComponent(page.v)}` : "";
  return {
    id: page.id,
    index: page.index,
    name: page.name,
    status: page.status,
    stage: page.stage,
    progress: page.progress,
    error: page.error || null,
    width: page.width,
    height: page.height,
    v: page.v,
    translatedUrl: done
      ? `${server}/files/${jobId}/pages/${page.id}/translated.png${version}`
      : null,
    originalUrl:
      page.status !== "queued"
        ? `${server}/files/${jobId}/pages/${page.id}/original.png${version}`
        : null,
    thumbUrl:
      page.status !== "queued"
        ? `${server}/files/${jobId}/pages/${page.id}/translated_thumb.jpg${version}`
        : null,
    editorUrl: `${server}/#/job/${jobId}/page/${page.id}`,
    downloadUrl: `${server}/api/jobs/${jobId}/pages/${page.id}/download`,
  };
}

async function jobStatus(payload = {}) {
  if (!JOB_RE.test(String(payload.jobId || ""))) {
    throw new Error("ID job tidak valid.");
  }
  const settings = await getSettings();
  const server = normalizeServer(payload.server || settings.serverUrl);
  const job = await apiJson(server, `/api/jobs/${payload.jobId}`);
  const pages = (Array.isArray(job.pages) ? job.pages : []).map((page) =>
    pageView(server, job.id, page)
  );
  const page =
    pages.find((row) => row.id === payload.pageId) ||
    pages.find((row) => row.status === "done") ||
    pages[0] ||
    null;
  return {
    ok: true,
    job: {
      id: job.id,
      status: job.status,
      stage: job.stage,
      progress: job.progress,
      message: job.message,
      error: job.error,
      title: job.title,
      pageCount: job.page_count,
      doneCount: job.done_count,
      options: job.options,
      server,
      pageId: page?.id || null,
      pageStatus: page?.status || null,
      translatedUrl: page?.translatedUrl || null,
      originalUrl: page?.originalUrl || null,
      thumbUrl: page?.thumbUrl || null,
      editorUrl: page?.editorUrl || `${server}/#/job/${job.id}`,
      downloadUrl: page?.downloadUrl || `${server}/api/jobs/${job.id}/download`,
      zipUrl: `${server}/api/jobs/${job.id}/download`,
      pages,
    },
  };
}

async function fetchDataUrl(payload = {}) {
  if (!payload.url) throw new Error("URL gambar tidak diberikan.");
  const res = await fetchWithTimeout(payload.url, {
    credentials: "include",
    cache: "no-store",
  }, 20000);
  if (!res.ok) throw new Error(`Gambar tidak bisa diambil (HTTP ${res.status}).`);
  const blob = await res.blob();
  const dataUrl = await new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(new Error("Gambar gagal dikonversi."));
    reader.readAsDataURL(blob);
  });
  return { ok: true, dataUrl, mime: blob.type || "image/png" };
}

/* ----------------------------------------------------------------- helpers */

async function captureVisible() {
  const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  if (!tab) throw new Error("Tab aktif tidak ditemukan.");
  const url = String(tab.url || "");
  if (
    !url ||
    /^(chrome|edge|about|devtools|view-source|chrome-extension):/i.test(url)
  ) {
    throw new Error("Halaman browser ini tidak bisa ditangkap. Buka halaman web biasa.");
  }
  const dataUrl = await chrome.tabs.captureVisibleTab(tab.windowId, {
    format: "png",
  });
  return {
    ok: true,
    dataUrl,
    tab: {
      id: tab.id,
      title: tab.title || "",
      url,
    },
  };
}

async function saveLastJob(payload = {}) {
  const record = payload.job || payload;
  if (!record || !JOB_RE.test(String(record.jobId || ""))) {
    throw new Error("Data job terakhir tidak valid.");
  }
  const stored = {
    jobId: record.jobId,
    pageId: record.pageId || null,
    title: String(record.title || "Terjemahan MangaFlow").slice(0, 140),
    status: record.status || "queued",
    progress: Number(record.progress) || 0,
    tgt: record.tgt || "",
    server: record.server || normalizeServer((await getSettings()).serverUrl),
    translatedUrl: record.translatedUrl || null,
    thumbUrl: record.thumbUrl || null,
    originalUrl: record.originalUrl || null,
    editorUrl: record.editorUrl || null,
    downloadUrl: record.downloadUrl || null,
    createdAt: record.createdAt || Date.now(),
  };
  await chrome.storage.local.set({ [LAST_JOB_KEY]: stored });
  return { ok: true, job: stored };
}

async function getLastJob() {
  const stored = await chrome.storage.local.get(LAST_JOB_KEY);
  return { ok: true, job: stored[LAST_JOB_KEY] || null };
}

async function openViewer(payload = {}) {
  if (!JOB_RE.test(String(payload.jobId || ""))) {
    throw new Error("ID job tidak valid.");
  }
  const params = new URLSearchParams({ job: payload.jobId });
  if (payload.pageId) params.set("page", payload.pageId);
  if (payload.server) params.set("server", normalizeServer(payload.server));
  await chrome.tabs.create({
    url: chrome.runtime.getURL(`viewer.html?${params.toString()}`),
  });
  return { ok: true };
}

async function openUrl(payload = {}) {
  const url = String(payload.url || "");
  if (!/^https?:\/\//i.test(url)) {
    throw new Error("Hanya alamat http/https yang bisa dibuka.");
  }
  await chrome.tabs.create({ url });
  return { ok: true };
}

function setBadge(tabId, text, color = "#7c5cff") {
  if (typeof tabId !== "number") return;
  chrome.action.setBadgeBackgroundColor({ tabId, color }).catch(() => {});
  chrome.action.setBadgeText({ tabId, text }).catch(() => {});
}

/* ---------------------------------------------------------------- messages */

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  const type = message?.type;
  const handlers = {
    "mf:capture": () => captureVisible(),
    "mf:serverInfo": () => serverInfo(message.payload?.server),
    "mf:startJob": () => startJob(message.payload || {}),
    "mf:jobStatus": () => jobStatus(message.payload || {}),
    "mf:fetchDataUrl": () => fetchDataUrl(message.payload || {}),
    "mf:saveLastJob": () => saveLastJob(message.payload || {}),
    "mf:getLastJob": () => getLastJob(),
    "mf:openViewer": () => openViewer(message.payload || {}),
    "mf:openUrl": () => openUrl(message.payload || {}),
  };

  if (type === "mf:pageProgress" && sender.tab?.id != null) {
    const state = message.state;
    const tabId = sender.tab.id;
    if (state === "working") {
      setBadge(tabId, message.progress ? `${Math.min(99, message.progress)}%` : "…");
    } else if (state === "done") {
      setBadge(tabId, "✓", "#22c55e");
    } else if (state === "error") {
      setBadge(tabId, "!", "#ef4444");
    }
    return false;
  }

  if (!handlers[type]) return false;

  handlers[type]()
    .then((result) => sendResponse(result))
    .catch((error) => {
      console.warn("[MangaFlow]", type, error);
      sendResponse({ ok: false, error: friendlyError(error) });
    });
  return true;
});

chrome.tabs.onUpdated.addListener((tabId, info) => {
  if (info.status === "loading") {
    chrome.action.setBadgeText({ tabId, text: "" }).catch(() => {});
  }
});

/* --------------------------------------------------------------- lifecycle */

chrome.runtime.onInstalled.addListener(async () => {
  const stored = await chrome.storage.sync.get(SETTINGS_KEY);
  if (!stored[SETTINGS_KEY]) {
    await chrome.storage.sync.set({ [SETTINGS_KEY]: DEFAULT_SETTINGS });
  }
  chrome.action.setBadgeBackgroundColor({ color: "#7c5cff" }).catch(() => {});
});

chrome.commands.onCommand.addListener(async (command) => {
  if (command !== "capture-screenshot") return;
  try {
    const shot = await captureVisible();
    await chrome.storage.session.set({
      [PENDING_SHOT_KEY]: { ...shot, ts: Date.now() },
    });
    try {
      await chrome.action.openPopup();
    } catch {
      await chrome.windows.create({
        url: chrome.runtime.getURL("popup.html?mode=shot"),
        type: "popup",
        width: 362,
        height: 642,
      });
    }
  } catch (error) {
    console.warn("[MangaFlow] screenshot gagal", error);
    const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
    setBadge(tab?.id, "!", "#ef4444");
  }
});

/* Ekspor untuk memudahkan pengujian manual di console service worker. */
globalThis.MangaFlowBackground = {
  DEFAULT_SERVER,
  normalizeServer,
  startJob,
  jobStatus,
  serverInfo,
};
