/* ============================================================
   MangaFlow — popup
   ============================================================ */

const DEFAULT_SERVER = "http://127.0.0.1:8765";
const SETTINGS_KEY = "mf_settings";
const LAST_JOB_KEY = "mf_last_job";
const PENDING_SHOT_KEY = "mf_pending_shot";
const REPO = "https://github.com/zamdevv-jpg/MangaFlow---Translate-Manga-Free";
const UPDATES_URL = `${REPO}/commits/main`;
const ISSUES_URL = `${REPO}/issues`;

const DEFAULT_SETTINGS = {
  serverUrl: DEFAULT_SERVER,
  autoTranslate: false,
  targetLang: "id",
  sourceLang: "auto",
  engine: "local",
  tone: "santai",
};

const FALLBACK_TARGETS = [
  { code: "id", name: "Indonesia", popular: true },
  { code: "en", name: "Inggris", popular: true },
  { code: "ja", name: "Jepang", popular: true },
  { code: "ko", name: "Korea", popular: true },
  { code: "zh", name: "Tionghoa (Sederhana)", popular: false },
  { code: "ms", name: "Melayu", popular: true },
  { code: "es", name: "Spanyol", popular: true },
  { code: "fr", name: "Prancis", popular: true },
  { code: "de", name: "Jerman", popular: true },
  { code: "pt", name: "Portugis", popular: true },
  { code: "ru", name: "Rusia", popular: false },
  { code: "ar", name: "Arab", popular: true },
];

const FALLBACK_SOURCES = [
  { code: "auto", name: "Deteksi otomatis" },
  { code: "ja", name: "Jepang" },
  { code: "ko", name: "Korea" },
  { code: "zh", name: "Tionghoa (Sederhana)" },
  { code: "en", name: "Inggris" },
  { code: "id", name: "Indonesia" },
];

const FALLBACK_ENGINES = [
  { id: "local", name: "Lokal (tanpa limit)", available: true, note: "Argos/OPUS + NLLB-200" },
  { id: "nllb", name: "NLLB-200 (lokal)", available: true, note: "Cepat" },
  { id: "nllb-tuned", name: "NLLB-200 manga (1.3B)", available: true, note: "Kualitas lebih baik" },
  { id: "google", name: "Google Translate", available: true, note: "Online" },
  { id: "deepl", name: "DeepL", available: false, note: "Butuh kunci" },
];

const PREVIEW_CONFIG = {
  version: "1.0.0",
  languages: {
    targets: [
      { code: "id", name: "Indonesia", popular: true },
      { code: "en", name: "Inggris", popular: true },
      { code: "ja", name: "Jepang", popular: true },
      { code: "ko", name: "Korea", popular: true },
      { code: "ms", name: "Melayu", popular: true },
      { code: "es", name: "Spanyol", popular: true },
      { code: "fr", name: "Prancis", popular: true },
      { code: "de", name: "Jerman", popular: true },
      { code: "pt", name: "Portugis", popular: true },
      { code: "ru", name: "Rusia", popular: false },
      { code: "ar", name: "Arab", popular: true },
      { code: "zh", name: "Tionghoa (Sederhana)", popular: false },
    ],
    sources: FALLBACK_SOURCES,
  },
  engines: [
    { id: "local", name: "Lokal (tanpa limit)", available: true, note: "Argos/OPUS · CJK otomatis ke NLLB-200 1.3B" },
    { id: "nllb", name: "NLLB-200 (lokal)", available: true, note: "NLLB-200 600M · cepat" },
    { id: "nllb-tuned", name: "NLLB-200 manga (1.3B)", available: true, note: "NLLB-200 1.3B + kamus manga" },
    { id: "google", name: "Google Translate", available: true, note: "Online · bisa kena rate limit" },
    { id: "deepl", name: "DeepL", available: false, note: "Atur kunci DeepL" },
  ],
  defaults: { ...DEFAULT_SETTINGS },
};

const PARAMS = new URLSearchParams(location.search);
const IS_EXT =
  typeof chrome !== "undefined" &&
  Boolean(chrome.runtime?.id) &&
  Boolean(chrome.tabs);
const PREVIEW = !IS_EXT || PARAMS.has("preview");

const $ = (id) => document.getElementById(id);

const state = {
  settings: { ...DEFAULT_SETTINGS },
  config: null,
  info: null,
  serverOnline: false,
  serverError: "",
  refreshing: false,
  tab: null,
  pageState: "checking",
  pageError: "",
  candidate: null,
  pageBusy: false,
  pageProgress: 0,
  pageStage: "",
  pageTimer: 0,
  lastJob: null,
  shot: null,
  shotJob: null,
  shotBusy: false,
  toastTimer: 0,
};

/* -------------------------------------------------------------- helpers */

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function setSwitch(el, on) {
  if (!el) return;
  el.setAttribute("aria-checked", on ? "true" : "false");
}

function switchValue(el) {
  return el?.getAttribute("aria-checked") === "true";
}

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function normalizeServer(value) {
  const raw = String(value || "").trim();
  let url;
  try {
    url = new URL(/^[a-z]+:\/\//i.test(raw) ? raw : `http://${raw}`);
  } catch {
    throw new Error("Alamat server tidak valid.");
  }
  if (!["http:", "https:"].includes(url.protocol)) {
    throw new Error("Alamat server harus http:// atau https://.");
  }
  const path = url.pathname.replace(/\/+$/, "");
  return `${url.origin}${path === "/" ? "" : path}`;
}

function hostLabel(server) {
  try {
    const url = new URL(server);
    const path = url.pathname.replace(/\/+$/, "");
    return `${url.host}${path === "/" ? "" : path}`;
  } catch {
    return server;
  }
}

function toast(message, type = "") {
  const el = $("toast");
  if (!el) return;
  clearTimeout(state.toastTimer);
  el.textContent = message;
  el.className = `toast show ${type === "error" ? "err" : type === "ok" ? "ok" : ""}`;
  state.toastTimer = setTimeout(() => {
    el.className = "toast";
  }, 3600);
}

async function loadSettings() {
  if (!IS_EXT) {
    state.settings = { ...DEFAULT_SETTINGS };
    return;
  }
  try {
    const stored = await chrome.storage.sync.get(SETTINGS_KEY);
    state.settings = { ...DEFAULT_SETTINGS, ...(stored[SETTINGS_KEY] || {}) };
  } catch {
    state.settings = { ...DEFAULT_SETTINGS };
  }
}

async function saveSettings(patch, { silent = false } = {}) {
  state.settings = { ...state.settings, ...patch };
  if (IS_EXT) {
    try {
      await chrome.storage.sync.set({ [SETTINGS_KEY]: state.settings });
    } catch {
      if (!silent) toast("Pengaturan gagal disimpan.", "error");
    }
  }
}

async function sendBg(message) {
  if (!IS_EXT) throw new Error("Mode pratinjau tidak terhubung ke server.");
  let response;
  try {
    response = await chrome.runtime.sendMessage(message);
  } catch {
    throw new Error("Ekstensi perlu dimuat ulang. Tutup popup lalu buka lagi.");
  }
  if (!response || response.ok === false) {
    throw new Error(response?.error || "Gagal menghubungi MangaFlow.");
  }
  return response;
}

function openExternal(url) {
  if (!/^https?:\/\//i.test(url)) return;
  if (!IS_EXT) {
    window.open(url, "_blank", "noopener");
    return;
  }
  sendBg({ type: "mf:openUrl", payload: { url } }).catch((error) => {
    toast(error.message, "error");
  });
}

/* ------------------------------------------------------------- language */

function targetRows() {
  const rows = state.config?.languages?.targets;
  return Array.isArray(rows) && rows.length ? rows : FALLBACK_TARGETS;
}

function sourceRows() {
  const rows = state.config?.languages?.sources;
  return Array.isArray(rows) && rows.length ? rows : FALLBACK_SOURCES;
}

function engineRows() {
  const rows = state.config?.engines;
  return Array.isArray(rows) && rows.length ? rows : FALLBACK_ENGINES;
}

function targetName(code) {
  return targetRows().find((row) => row.code === code)?.name || code || "—";
}

function engineInfo(id) {
  return engineRows().find((row) => row.id === id) || null;
}

function engineName(id) {
  return engineInfo(id)?.name || id || "—";
}

function fillSelect(select, rows, preferred) {
  if (!select) return;
  const list = rows.map((row) => ({
    value: String(row.value ?? row.code ?? row.id ?? ""),
    label: String(row.label ?? row.name ?? row.value ?? ""),
    disabled: Boolean(row.disabled),
  }));
  const wanted = String(preferred ?? select.value ?? "");
  const seen = new Set(list.map((row) => row.value));
  if (wanted && !seen.has(wanted)) {
    list.unshift({ value: wanted, label: wanted, disabled: false });
  }
  select.replaceChildren(
    ...list.map((row) => {
      const option = document.createElement("option");
      option.value = row.value;
      option.textContent = row.label;
      option.disabled = row.disabled;
      return option;
    })
  );
  if (wanted && list.some((row) => row.value === wanted)) {
    select.value = wanted;
  } else if (list.length) {
    select.value = list[0].value;
  }
}

function populateOptions() {
  const target = targetRows();
  const engines = engineRows();
  const sources = sourceRows();

  fillSelect($("targetLang"), target, state.settings.targetLang);
  fillSelect(
    $("targetLang2"),
    target,
    $("targetLang2")?.value || state.settings.targetLang
  );

  fillSelect($("sourceLang"), sources, state.settings.sourceLang);
  fillSelect(
    $("engine"),
    engines.map((row) => ({
      value: row.id,
      label: row.name + (row.available ? "" : " · belum siap"),
      disabled: !row.available && row.id !== state.settings.engine,
    })),
    state.settings.engine
  );
}

function applySettingsToUI() {
  const serverInput = $("serverUrl");
  if (serverInput) serverInput.value = state.settings.serverUrl;
  setSwitch($("autoToggle"), Boolean(state.settings.autoTranslate));
  setSwitch($("autoToggleSettings"), Boolean(state.settings.autoTranslate));

  const tone = $("tone");
  if (tone) tone.value = state.settings.tone || "santai";

  populateOptions();
  if ($("targetLang")) $("targetLang").value = state.settings.targetLang;
  if ($("targetLang2")) $("targetLang2").value = state.settings.targetLang;
  if ($("sourceLang")) $("sourceLang").value = state.settings.sourceLang;
  if ($("engine")) $("engine").value = state.settings.engine;

  renderStatus();
}

/* ---------------------------------------------------------------- status */

function setStatusChecking() {
  const card = $("statusCard");
  card?.classList.remove("is-online", "is-offline");
  card?.classList.add("is-checking");
  if ($("statusLabel")) $("statusLabel").textContent = "Menghubungkan…";
  if ($("statusHeadline")) $("statusHeadline").textContent = "Menghubungkan server…";
  if ($("statusSub")) {
    $("statusSub").textContent = "Mencari MangaFlow di alamat server.";
  }
  updateTranslateButton();
  renderPageHint();
}

function renderStatus() {
  const card = $("statusCard");
  if (!card) return;
  card.classList.remove("is-checking", "is-online", "is-offline");
  card.classList.add(state.serverOnline ? "is-online" : "is-offline");

  const version = state.info?.health?.version || state.config?.version || "";
  if ($("statusLabel")) {
    $("statusLabel").textContent = state.serverOnline
      ? "Server lokal aktif"
      : "Server belum aktif";
  }
  if ($("statusHeadline")) {
    $("statusHeadline").textContent = state.serverOnline
      ? "Siap menerjemahkan"
      : "Nyalakan MangaFlow";
  }
  if ($("statusSub")) {
    $("statusSub").textContent = state.serverOnline
      ? "OCR + terjemahan lokal · tanpa kredit"
      : state.serverError
        ? state.serverError
        : "Jalankan run.bat atau python app.py, lalu cek ulang.";
  }
  if ($("statEngine")) $("statEngine").textContent = engineName(state.settings.engine);
  if ($("statTarget")) $("statTarget").textContent = targetName(state.settings.targetLang);
  if ($("statVersion")) $("statVersion").textContent = version ? `v${version}` : "—";

  if ($("accountServer")) $("accountServer").textContent = hostLabel(state.settings.serverUrl);
  if ($("accountEngine")) $("accountEngine").textContent = engineName(state.settings.engine);
  if ($("accountTarget")) $("accountTarget").textContent = targetName(state.settings.targetLang);
  if ($("accountVersion")) $("accountVersion").textContent = version ? `v${version}` : "v1.0.0";

  updateTranslateButton();
  renderPageHint();
}

function updateTranslateButton() {
  const btn = $("translatePageBtn");
  const label = $("translatePageLabel");
  if (!btn || !label) return;
  const ready =
    state.serverOnline &&
    state.pageState === "ready" &&
    Boolean(state.candidate) &&
    !state.pageBusy;
  btn.disabled = !ready;
  btn.classList.toggle("is-busy", state.pageBusy);
  if (state.pageBusy) {
    label.textContent = state.pageProgress
      ? `Menerjemahkan ${Math.min(99, state.pageProgress)}%`
      : "Menerjemahkan…";
  } else if (state.candidate?.translated) {
    label.textContent = "Lihat hasil";
  } else {
    label.textContent = "Terjemahkan halaman";
  }
  btn.title = ready
    ? ""
    : !state.serverOnline
      ? "Server MangaFlow belum aktif."
      : "Tidak ada gambar manga yang bisa diterjemahkan di tab ini.";
}

function renderPageHint() {
  const hint = $("pageHint");
  if (!hint) return;
  let text = "";
  let type = "";

  if (state.pageBusy) {
    text = state.pageStage
      ? `Menerjemahkan · ${state.pageStage}`
      : "Menerjemahkan halaman…";
  } else if (!state.serverOnline) {
    text = state.serverError
      ? state.serverError
      : "Server lokal belum aktif. Jalankan MangaFlow lalu cek ulang.";
    type = "is-error";
  } else if (state.pageState === "server") {
    text = "Ini halaman MangaFlow. Buka halaman manga di tab lain.";
  } else if (state.pageState === "restricted") {
    text = "Halaman browser ini tidak bisa diterjemahkan.";
  } else if (state.pageState === "error") {
    text = state.pageError || "Halaman tidak bisa dipindai. Muat ulang lalu coba lagi.";
    type = "is-error";
  } else if (state.pageState === "none") {
    text = "Tidak ada gambar manga besar. Coba Screenshot.";
  } else if (state.pageState === "checking") {
    text = "Memeriksa halaman aktif…";
  } else if (state.candidate?.translated) {
    text = "Halaman ini sudah diterjemahkan. Buka editor untuk koreksi.";
    type = "is-ok";
  } else if (state.candidate) {
    text = `Gambar ${state.candidate.width}×${state.candidate.height} siap diterjemahkan.`;
    type = "is-ok";
  } else {
    text = "Buka halaman manga lalu klik terjemahkan.";
  }

  hint.textContent = text;
  hint.classList.toggle("is-error", type === "is-error");
  hint.classList.toggle("is-ok", type === "is-ok");
}

/* --------------------------------------------------------------- server */

async function refreshServer({ quiet = false } = {}) {
  if (state.refreshing) return;
  state.refreshing = true;
  if (!quiet) setStatusChecking();

  try {
    if (PREVIEW) {
      await sleep(320);
      if (PARAMS.get("preview") === "offline") {
        throw new Error("Server lokal belum aktif (pratinjau).");
      }
      state.serverOnline = true;
      state.serverError = "";
      state.info = {
        health: { ok: true, version: "1.0.0" },
        config: PREVIEW_CONFIG,
        server: state.settings.serverUrl,
      };
      state.config = PREVIEW_CONFIG;
      populateOptions();
      if ($("serverHint")) {
        $("serverHint").textContent = "Pratinjau antarmuka · data server disimulasikan.";
        $("serverHint").className = "field-hint";
      }
      renderStatus();
      return;
    }

    const info = await sendBg({ type: "mf:serverInfo" });
    state.serverOnline = true;
    state.serverError = "";
    state.info = info;
    state.config = info.config || null;
    state.settings.serverUrl = info.server || state.settings.serverUrl;
    populateOptions();
    if ($("serverHint")) {
      $("serverHint").textContent = `Terhubung · v${info.health?.version || "?"}`;
      $("serverHint").className = "field-hint is-ok";
    }
    renderStatus();
  } catch (error) {
    state.serverOnline = false;
    state.serverError = error.message || "Server tidak bisa dihubungi.";
    state.info = null;
    if ($("serverHint")) {
      $("serverHint").textContent = "Tidak terhubung. Jalankan MangaFlow lalu klik Tes.";
      $("serverHint").className = "field-hint is-error";
    }
    renderStatus();
  } finally {
    state.refreshing = false;
  }
}

async function testServer() {
  const input = $("serverUrl");
  const hint = $("serverHint");
  if (!input || !hint) return;
  let server;
  try {
    server = normalizeServer(input.value);
  } catch (error) {
    hint.textContent = error.message;
    hint.className = "field-hint is-error";
    return;
  }
  input.value = server;
  hint.textContent = "Menghubungkan…";
  hint.className = "field-hint";

  if (!IS_EXT) {
    await sleep(300);
    hint.textContent = "Pratinjau antarmuka · server disimulasikan.";
    return;
  }

  try {
    const info = await sendBg({
      type: "mf:serverInfo",
      payload: { server },
    });
    hint.textContent = `Terhubung · v${info.health?.version || "?"}`;
    hint.className = "field-hint is-ok";
    toast("Server MangaFlow terhubung.", "ok");
  } catch (error) {
    hint.textContent = error.message;
    hint.className = "field-hint is-error";
  }
}

/* ---------------------------------------------------------------- page */

function isServerPage(url) {
  try {
    const tab = new URL(url);
    const server = new URL(state.settings.serverUrl);
    return tab.origin === server.origin;
  } catch {
    return false;
  }
}

async function detectPage() {
  if (PREVIEW) {
    state.tab = { title: "Halaman manga contoh", url: "https://contoh.test/manga/1" };
    state.pageState = "ready";
    state.candidate = { width: 1351, height: 1920, translated: false };
    state.pageError = "";
    renderStatus();
    return;
  }

  if (!IS_EXT) return;
  state.pageState = "checking";
  state.candidate = null;
  state.pageError = "";
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    state.tab = tab || null;
    const url = tab?.url || "";
    if (!tab || /^(chrome|edge|about|devtools|chrome-extension|view-source):/i.test(url)) {
      state.pageState = "restricted";
      renderStatus();
      return;
    }
    if (isServerPage(url)) {
      state.pageState = "server";
      renderStatus();
      return;
    }
    try {
      const response = await chrome.tabs.sendMessage(tab.id, { type: "mf:detect" });
      if (!response?.ok) throw new Error(response?.error || "Halaman tidak merespons.");
      state.candidate = response.candidate || null;
      state.pageState = state.candidate ? "ready" : "none";
    } catch {
      state.pageState = "error";
      state.pageError = "Muat ulang halaman ini sekali agar ekstensi bisa memindai gambar.";
    }
  } catch (error) {
    state.pageState = "error";
    state.pageError = error.message || "Halaman aktif tidak bisa dibaca.";
  }
  renderStatus();
}

function clearPageTimer() {
  if (state.pageTimer) {
    clearTimeout(state.pageTimer);
    state.pageTimer = 0;
  }
}

async function translatePage() {
  if (!IS_EXT) {
    toast("Mode pratinjau: buka ekstensi di Chrome untuk menerjemahkan.", "error");
    return;
  }
  if (!state.serverOnline) {
    toast("Server MangaFlow belum aktif.", "error");
    return;
  }
  if (!state.candidate || !state.tab) {
    toast("Tidak ada gambar manga di tab ini.", "error");
    return;
  }
  state.pageBusy = true;
  state.pageProgress = 0;
  state.pageStage = "Menyiapkan";
  renderStatus();
  clearPageTimer();
  state.pageTimer = setTimeout(() => {
    if (!state.pageBusy) return;
    state.pageBusy = false;
    state.pageStage = "";
    renderStatus();
    toast("Halaman tidak merespons. Muat ulang lalu coba lagi.", "error");
  }, 60000);

  try {
    const response = await chrome.tabs.sendMessage(state.tab.id, { type: "mf:translate" });
    if (!response?.ok) throw new Error(response?.error || "Halaman menolak permintaan.");
    if (response.alreadyTranslated) {
      clearPageTimer();
      state.pageBusy = false;
      renderStatus();
      toast("Halaman ini sudah diterjemahkan. Panel MangaFlow dibuka di halaman.", "ok");
    }
  } catch (error) {
    clearPageTimer();
    state.pageBusy = false;
    state.pageStage = "";
    renderStatus();
    toast(error.message || "Gagal memulai terjemahan.", "error");
  }
}

function handlePageProgress(message, sender) {
  if (!state.tab || sender?.tab?.id !== state.tab.id) return;
  if (!sender?.tab) return;
  if (message.state === "working") {
    clearPageTimer();
    state.pageBusy = true;
    state.pageProgress = Number(message.progress) || 0;
    state.pageStage = message.detail || "OCR & terjemahan";
    renderStatus();
    return;
  }
  if (message.state === "done") {
    clearPageTimer();
    state.pageBusy = false;
    state.pageProgress = 100;
    state.pageStage = "";
    if (state.candidate) state.candidate.translated = true;
    state.pageState = "ready";
    renderStatus();
    toast("Halaman selesai diterjemahkan.", "ok");
    setTimeout(loadLastJob, 700);
    return;
  }
  if (message.state === "error") {
    clearPageTimer();
    state.pageBusy = false;
    state.pageStage = "";
    renderStatus();
    toast(message.error || "Terjemahan halaman gagal.", "error");
  }
}

/* -------------------------------------------------------------- last job */

function statusLabel(status) {
  return (
    {
      queued: "menunggu",
      running: "diproses",
      done: "selesai",
      error: "gagal",
      cancelled: "dibatalkan",
    }[status] || status || "—"
  );
}

function renderLastJob() {
  const card = $("lastJobCard");
  if (!card) return;
  const job = state.lastJob;
  card.classList.toggle("hidden", !job);
  if (!job) return;

  if ($("lastTitle")) $("lastTitle").textContent = job.title || "Terjemahan MangaFlow";
  if ($("lastMeta")) {
    $("lastMeta").textContent = `${targetName(job.tgt || state.settings.targetLang)} · ${statusLabel(job.status)}`;
  }

  const thumb = $("lastThumb");
  const url = job.thumbUrl || job.translatedUrl;
  if (thumb) {
    if (url && !url.startsWith("data:image/svg")) {
      const img = document.createElement("img");
      img.alt = "";
      img.loading = "lazy";
      img.src = url;
      img.addEventListener("error", () => {
        thumb.replaceChildren(document.createTextNode("M"));
      });
      thumb.replaceChildren(img);
    } else {
      thumb.replaceChildren(document.createTextNode("M"));
    }
  }
}

async function persistLastJob(job) {
  state.lastJob = job;
  renderLastJob();
  if (!IS_EXT) return;
  try {
    await chrome.storage.local.set({ [LAST_JOB_KEY]: job });
  } catch {
    /* penyimpanan lokal penuh */
  }
}

async function loadLastJob() {
  if (!IS_EXT) {
    if (PARAMS.get("last") === "1") {
      state.lastJob = {
        jobId: "j_efd0007ac934",
        pageId: "p003",
        title: "yasei01 · contoh",
        tgt: "id",
        status: "done",
        progress: 100,
        thumbUrl: "../web/demo/after.jpg",
        translatedUrl: "../web/demo/after.jpg",
        editorUrl: `${state.settings.serverUrl}/#/job/j_efd0007ac934/page/p003`,
        server: state.settings.serverUrl,
      };
      renderLastJob();
    }
    return;
  }
  try {
    const stored = await chrome.storage.local.get(LAST_JOB_KEY);
    state.lastJob = stored[LAST_JOB_KEY] || null;
    renderLastJob();
    if (state.lastJob && !["done", "error", "cancelled"].includes(state.lastJob.status)) {
      refreshLastJobStatus();
    }
  } catch {
    /* abaikan */
  }
}

async function refreshLastJobStatus() {
  const job = state.lastJob;
  if (!job?.jobId || !IS_EXT) return;
  try {
    const response = await sendBg({
      type: "mf:jobStatus",
      payload: {
        jobId: job.jobId,
        pageId: job.pageId,
        server: job.server || state.settings.serverUrl,
      },
    });
    const fresh = response.job;
    if (fresh.status === "done" && fresh.translatedUrl) {
      await persistLastJob({
        ...job,
        status: "done",
        progress: 100,
        title: fresh.title || job.title,
        translatedUrl: fresh.translatedUrl,
        thumbUrl: fresh.thumbUrl,
        originalUrl: fresh.originalUrl,
        editorUrl: fresh.editorUrl,
        downloadUrl: fresh.downloadUrl,
        tgt: fresh.options?.tgt_lang || job.tgt,
        server: fresh.server || job.server,
      });
    } else if (["error", "cancelled"].includes(fresh.status)) {
      await persistLastJob({ ...job, status: fresh.status, progress: fresh.progress || 0 });
    }
  } catch {
    /* server mungkin sedang mati; biarkan status terakhir */
  }
}

function openLastJob() {
  const job = state.lastJob;
  if (!job?.jobId) return;
  if (!IS_EXT) {
    toast("Mode pratinjau: hasil tidak bisa dibuka.", "error");
    return;
  }
  sendBg({
    type: "mf:openViewer",
    payload: {
      jobId: job.jobId,
      pageId: job.pageId,
      server: job.server || state.settings.serverUrl,
    },
  }).catch((error) => {
    if (job.editorUrl) openExternal(job.editorUrl);
    else toast(error.message, "error");
  });
}

/* ------------------------------------------------------------- screenshot */

function openShot(shot) {
  const sheet = $("shotSheet");
  const img = $("shotImage");
  if (!sheet || !img) return;

  state.shot = { ...shot, ready: false, selection: null, drag: null };
  state.shotJob = null;
  state.shotBusy = false;

  sheet.hidden = false;
  sheet.classList.remove("done");
  $("shotLoading").hidden = false;
  $("shotResult").hidden = true;
  $("shotResultImg").removeAttribute("src");
  $("shotProgress").hidden = true;
  $("shotOpenBtn").hidden = true;
  const translateLabel = $("shotTranslateBtn")?.querySelector("span");
  if (translateLabel) translateLabel.textContent = "Terjemahkan area";
  $("shotTranslateBtn").disabled = true;
  $("shotStepLabel").textContent = "Pilih area yang ingin diterjemahkan";
  $("shotTip").textContent = "Tarik pada gambar untuk memilih area. Area penuh dipilih otomatis.";
  $("shotBar").style.width = "0%";
  $("shotPercent").textContent = "0%";
  $("shotStatus").textContent = "Membuat job…";
  $("shotSelection").style.cssText = "";
  $("shotFrame").classList.remove("is-cropping");

  img.onload = () => {
    if (!state.shot) return;
    fitShotFrame();
    state.shot.ready = true;
    $("shotLoading").hidden = true;
    resetShotSelection();
    $("shotTranslateBtn").disabled = false;
  };
  img.onerror = () => {
    $("shotLoading").hidden = true;
    $("shotTip").textContent = "Tangkapan layar tidak bisa dimuat. Coba lagi.";
    toast("Tangkapan layar tidak bisa dimuat.", "error");
  };
  img.src = shot.dataUrl;
  const body = document.querySelector(".sheet-body");
  if (body) body.scrollTop = 0;
}

async function startCapture() {
  if (PREVIEW) {
    openShot({
      dataUrl: "../web/demo/before.jpg",
      tab: { title: "Demo manga", url: "https://contoh.test/manga/1" },
    });
    return;
  }
  try {
    const shot = await sendBg({ type: "mf:capture" });
    openShot(shot);
  } catch (error) {
    toast(error.message || "Screenshot gagal.", "error");
  }
}

function fitShotFrame(maxHeight = 302) {
  const stage = $("shotStage");
  const frame = $("shotFrame");
  const img = $("shotImage");
  if (!stage || !frame || !img?.naturalWidth || !img?.naturalHeight) return;
  const available = Math.max(120, stage.clientWidth - 12);
  const scale = Math.min(
    1,
    available / img.naturalWidth,
    maxHeight / img.naturalHeight
  );
  const width = Math.max(1, Math.round(img.naturalWidth * scale));
  const height = Math.max(1, Math.round(img.naturalHeight * scale));
  frame.style.width = `${width}px`;
  frame.style.height = `${height}px`;
  img.style.width = `${width}px`;
  img.style.height = `${height}px`;
}

function fullShotSelection() {
  const frame = $("shotFrame");
  const width = frame?.clientWidth || 0;
  const height = frame?.clientHeight || 0;
  if (!width || !height) return null;
  return { x: 0, y: 0, w: width, h: height };
}

function resetShotSelection() {
  if (!state.shot?.ready) return;
  state.shot.selection = fullShotSelection();
  renderShotSelection();
}

function renderShotSelection() {
  const selection = state.shot?.selection;
  const el = $("shotSelection");
  if (!selection || !el) return;
  el.style.left = `${selection.x}px`;
  el.style.top = `${selection.y}px`;
  el.style.width = `${selection.w}px`;
  el.style.height = `${selection.h}px`;
}

function updateShotTip() {
  const selection = state.shot?.selection;
  const frame = $("shotFrame");
  const img = $("shotImage");
  if (!selection || !frame || !img) return;
  const scaleX = img.naturalWidth / (frame.clientWidth || 1);
  const scaleY = img.naturalHeight / (frame.clientHeight || 1);
  const width = Math.max(1, Math.round(selection.w * scaleX));
  const height = Math.max(1, Math.round(selection.h * scaleY));
  $("shotTip").textContent = `Area ${width}×${height} px · tarik pada gambar untuk mengubah.`;
  $("shotStepLabel").textContent = `Area ${width}×${height} px`;
}

function onShotPointerDown(event) {
  if (!state.shot?.ready || state.shotBusy) return;
  if (event.button !== undefined && event.button !== 0) return;
  event.preventDefault();
  const frame = $("shotFrame");
  const rect = frame.getBoundingClientRect();
  const startX = clamp(event.clientX - rect.left, 0, rect.width);
  const startY = clamp(event.clientY - rect.top, 0, rect.height);
  state.shot.drag = { rect, startX, startY };
  state.shot.selection = { x: startX, y: startY, w: 0, h: 0 };
  renderShotSelection();
  $("shotSelection").classList.add("is-dragging");
  try {
    frame.setPointerCapture(event.pointerId);
  } catch {
    /* abaikan */
  }
}

function onShotPointerMove(event) {
  const drag = state.shot?.drag;
  if (!drag) return;
  event.preventDefault();
  const x = clamp(event.clientX - drag.rect.left, 0, drag.rect.width);
  const y = clamp(event.clientY - drag.rect.top, 0, drag.rect.height);
  state.shot.selection = {
    x: Math.min(drag.startX, x),
    y: Math.min(drag.startY, y),
    w: Math.abs(x - drag.startX),
    h: Math.abs(y - drag.startY),
  };
  renderShotSelection();
}

function onShotPointerUp(event) {
  const drag = state.shot?.drag;
  if (!drag) return;
  state.shot.drag = null;
  $("shotSelection").classList.remove("is-dragging");
  const frame = $("shotFrame");
  try {
    frame.releasePointerCapture(event.pointerId);
  } catch {
    /* abaikan */
  }
  if (!state.shot.selection || state.shot.selection.w < 12 || state.shot.selection.h < 12) {
    resetShotSelection();
  }
  updateShotTip();
}

function cropSelection() {
  const img = $("shotImage");
  const frame = $("shotFrame");
  const selection = state.shot?.selection;
  if (!img?.naturalWidth || !selection) throw new Error("Area belum dipilih.");
  const scaleX = img.naturalWidth / (frame.clientWidth || 1);
  const scaleY = img.naturalHeight / (frame.clientHeight || 1);
  const width = Math.max(1, Math.round(selection.w * scaleX));
  const height = Math.max(1, Math.round(selection.h * scaleY));
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext("2d");
  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = "high";
  ctx.drawImage(
    img,
    Math.round(selection.x * scaleX),
    Math.round(selection.y * scaleY),
    Math.round(selection.w * scaleX),
    Math.round(selection.h * scaleY),
    0,
    0,
    width,
    height
  );
  return canvas.toDataURL("image/png");
}

function updateShotProgress(progress, label) {
  const value = clamp(Math.round(progress || 0), 0, 100);
  $("shotProgress").hidden = false;
  $("shotBar").style.width = `${value}%`;
  $("shotPercent").textContent = `${value}%`;
  if (label) $("shotStatus").textContent = label;
}

async function translateShot() {
  if (!state.shot?.ready || state.shotBusy) return;
  const label = $("shotTranslateBtn")?.querySelector("span");
  state.shotBusy = true;
  state.shotJob = null;
  $("shotTranslateBtn").disabled = true;
  $("shotOpenBtn").hidden = true;
  $("shotResult").hidden = true;
  $("shotSheet").classList.remove("done");
  $("shotProgress").hidden = false;
  if (label) label.textContent = "Menerjemahkan…";
  updateShotProgress(2, "Menyiapkan area…");

  if (PREVIEW) {
    for (const value of [8, 22, 41, 63, 82, 100]) {
      await sleep(180);
      updateShotProgress(value, value < 100 ? "OCR & terjemahan berjalan…" : "Selesai");
    }
    finishShot({
      id: "preview",
      pageId: "p001",
      translatedUrl: "../web/demo/after.jpg",
      thumbUrl: "../web/demo/after.jpg",
      title: "Screenshot · demo",
      server: state.settings.serverUrl,
      options: { tgt_lang: state.settings.targetLang },
    });
    return;
  }

  let dataUrl;
  try {
    dataUrl = cropSelection();
  } catch (error) {
    state.shotBusy = false;
    $("shotTranslateBtn").disabled = false;
    toast(error.message, "error");
    return;
  }

  const title = `Screenshot · ${state.shot.tab?.title || "halaman"}`;
  let started;
  try {
    updateShotProgress(5, "Mengunggah area ke server lokal…");
    started = await sendBg({
      type: "mf:startJob",
      payload: {
        dataUrl,
        filename: `screenshot-${Date.now()}.png`,
        title,
        settings: state.settings,
        server: state.settings.serverUrl,
      },
    });
    state.shotJob = {
      id: started.jobId,
      pageId: started.pageId,
      server: started.server,
      title,
    };
    await persistLastJob({
      jobId: started.jobId,
      pageId: started.pageId,
      title,
      status: "running",
      progress: 0,
      tgt: state.settings.targetLang,
      server: started.server,
      translatedUrl: null,
      thumbUrl: null,
      createdAt: Date.now(),
    });
    updateShotProgress(8, "OCR & terjemahan berjalan di server lokal…");
  } catch (error) {
    state.shotBusy = false;
    $("shotTranslateBtn").disabled = false;
    if (label) label.textContent = "Terjemahkan area";
    toast(error.message || "Job gagal dibuat.", "error");
    $("shotProgress").hidden = true;
    return;
  }

  try {
    let job = null;
    for (let i = 0; i < 600; i += 1) {
      await sleep(i === 0 ? 900 : 1500);
      const response = await sendBg({
        type: "mf:jobStatus",
        payload: {
          jobId: started.jobId,
          pageId: started.pageId,
          server: started.server,
        },
      });
      job = response.job;
      const progress = Math.max(8, Math.min(96, Number(job.progress) || 0));
      updateShotProgress(progress, job.stage || "OCR & terjemahan…");
      if (job.status === "done" && job.translatedUrl) break;
      if (["error", "cancelled"].includes(job.status)) {
        throw new Error(job.error || "Server menghentikan job.");
      }
      if (job.status === "done" && !job.translatedUrl) {
        throw new Error("Hasil terjemahan tidak tersedia.");
      }
    }
    if (!job?.translatedUrl) {
      throw new Error("Terjemahan terlalu lama. Cek server MangaFlow.");
    }
    finishShot(job);
  } catch (error) {
    state.shotBusy = false;
    $("shotTranslateBtn").disabled = false;
    if (label) label.textContent = "Coba lagi";
    $("shotStatus").textContent = error.message || "Terjemahan gagal.";
    $("shotPercent").textContent = "—";
    const current = state.lastJob;
    if (current?.status === "running") {
      persistLastJob({ ...current, status: "error" });
    }
    toast(error.message || "Terjemahan screenshot gagal.", "error");
  }
}

function finishShot(job) {
  state.shotBusy = false;
  state.shotJob = job;
  $("shotSheet").classList.add("done");
  fitShotFrame(148);
  // Frame mengecil di state hasil; samakan lagi area pilihan dengan ukuran
  // baru supaya "Terjemahkan ulang" tidak memotong area yang salah.
  resetShotSelection();
  $("shotTranslateBtn").disabled = false;
  const label = $("shotTranslateBtn")?.querySelector("span");
  if (label) label.textContent = "Terjemahkan ulang";
  $("shotOpenBtn").hidden = false;
  $("shotResultImg").src = job.translatedUrl;
  $("shotResult").hidden = false;
  $("shotProgress").hidden = false;
  updateShotProgress(100, "Selesai");
  $("shotStepLabel").textContent = "Selesai · hasil siap";
  $("shotTip").textContent = "Bandingkan hasilnya di viewer, atau unduh dari MangaFlow.";
  $("shotFrame").classList.remove("is-cropping");

  if (state.lastJob?.jobId === job.id) {
    persistLastJob({
      ...state.lastJob,
      status: "done",
      progress: 100,
      title: job.title || state.lastJob.title,
      pageId: job.pageId,
      translatedUrl: job.translatedUrl,
      thumbUrl: job.thumbUrl,
      originalUrl: job.originalUrl,
      editorUrl: job.editorUrl,
      downloadUrl: job.downloadUrl,
      tgt: job.options?.tgt_lang || state.settings.targetLang,
      server: job.server || state.settings.serverUrl,
    });
  } else {
    persistLastJob({
      jobId: job.id,
      pageId: job.pageId,
      title: job.title || `Screenshot · ${state.shot?.tab?.title || "halaman"}`,
      status: "done",
      progress: 100,
      tgt: job.options?.tgt_lang || state.settings.targetLang,
      server: job.server || state.settings.serverUrl,
      translatedUrl: job.translatedUrl,
      thumbUrl: job.thumbUrl,
      originalUrl: job.originalUrl,
      editorUrl: job.editorUrl,
      downloadUrl: job.downloadUrl,
      createdAt: Date.now(),
    });
  }
  toast("Screenshot selesai diterjemahkan.", "ok");
}

function openShotResult() {
  if (!IS_EXT) {
    toast("Mode pratinjau: hasil tidak bisa dibuka.", "error");
    return;
  }
  const job = state.shotJob;
  if (!job?.id) {
    openLastJob();
    return;
  }
  sendBg({
    type: "mf:openViewer",
    payload: { jobId: job.id, pageId: job.pageId, server: job.server },
  }).catch((error) => {
    if (job.editorUrl) openExternal(job.editorUrl);
    else toast(error.message, "error");
  });
}

/* ------------------------------------------------------------------- tabs */

function showView(name) {
  document.querySelectorAll(".view").forEach((view) => {
    view.classList.toggle("is-active", view.dataset.view === name);
  });
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.classList.toggle("is-active", tab.dataset.tab === name);
  });
  const views = $("views");
  if (views) views.scrollTop = 0;
}

/* ---------------------------------------------------------------- events */

function bindEvents() {
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => showView(tab.dataset.tab));
  });

  $("updatesBtn")?.addEventListener("click", () => openExternal(UPDATES_URL));
  $("accountGithubBtn")?.addEventListener("click", () => openExternal(REPO));
  $("accountIssueBtn")?.addEventListener("click", () => openExternal(ISSUES_URL));

  const openWeb = () => {
    if (PREVIEW) {
      toast("Mode pratinjau: server tidak dibuka.");
      return;
    }
    openExternal(state.settings.serverUrl);
  };
  $("openWebBtn")?.addEventListener("click", openWeb);
  $("accountWebBtn")?.addEventListener("click", openWeb);

  const toggleAuto = async (el) => {
    const next = !switchValue(el);
    setSwitch($("autoToggle"), next);
    setSwitch($("autoToggleSettings"), next);
    await saveSettings({ autoTranslate: next });
    toast(
      next
        ? "Auto translate aktif. Halaman manga akan diproses otomatis."
        : "Auto translate nonaktif.",
      "ok"
    );
  };
  $("autoToggle")?.addEventListener("click", () => toggleAuto($("autoToggle")));
  $("autoToggleSettings")?.addEventListener("click", () =>
    toggleAuto($("autoToggleSettings"))
  );

  $("targetLang")?.addEventListener("change", async () => {
    const code = $("targetLang").value;
    if ($("targetLang2")) $("targetLang2").value = code;
    await saveSettings({ targetLang: code }, { silent: true });
    renderStatus();
  });
  $("targetLang2")?.addEventListener("change", async () => {
    const code = $("targetLang2").value;
    if ($("targetLang")) $("targetLang").value = code;
    await saveSettings({ targetLang: code }, { silent: true });
    renderStatus();
  });

  $("testServerBtn")?.addEventListener("click", testServer);
  $("serverUrl")?.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      testServer();
    }
  });

  $("saveSettingsBtn")?.addEventListener("click", async () => {
    let server;
    try {
      server = normalizeServer($("serverUrl").value);
    } catch (error) {
      $("serverHint").textContent = error.message;
      $("serverHint").className = "field-hint is-error";
      toast(error.message, "error");
      return;
    }
    const next = {
      serverUrl: server,
      engine: $("engine").value,
      tone: $("tone").value,
      sourceLang: $("sourceLang").value,
      targetLang: $("targetLang2").value,
      autoTranslate: switchValue($("autoToggleSettings")),
    };
    await saveSettings(next);
    applySettingsToUI();
    await refreshServer();
    await detectPage();
    toast("Pengaturan disimpan.", "ok");
  });

  $("retryBtn")?.addEventListener("click", async () => {
    const icon = $("retryBtn");
    icon?.classList.add("is-spinning");
    await refreshServer();
    await detectPage();
    setTimeout(() => icon?.classList.remove("is-spinning"), 350);
  });

  $("translatePageBtn")?.addEventListener("click", translatePage);
  $("screenshotBtn")?.addEventListener("click", startCapture);
  $("lastOpen")?.addEventListener("click", openLastJob);

  $("shotClose")?.addEventListener("click", () => {
    $("shotSheet").hidden = true;
    state.shot = null;
    state.shotJob = null;
    state.shotBusy = false;
    clearPageTimer();
  });
  $("shotReset")?.addEventListener("click", () => {
    if (state.shotBusy) return;
    resetShotSelection();
    updateShotTip();
  });
  $("shotTranslateBtn")?.addEventListener("click", translateShot);
  $("shotOpenBtn")?.addEventListener("click", openShotResult);

  const frame = $("shotFrame");
  frame?.addEventListener("pointerdown", onShotPointerDown);
  frame?.addEventListener("pointermove", onShotPointerMove);
  frame?.addEventListener("pointerup", onShotPointerUp);
  frame?.addEventListener("pointercancel", onShotPointerUp);

  window.addEventListener("unload", () => {
    clearPageTimer();
    clearTimeout(state.toastTimer);
  });
}

/* ------------------------------------------------------------------ boot */

function setShortcutLabel() {
  const mac = /Mac|iPhone|iPad|iPod/.test(navigator.platform || navigator.userAgent);
  const el = $("shortcutLabel");
  if (el) el.textContent = mac ? "⌘⇧S" : "Ctrl+Shift+S";
}

async function checkPendingShot() {
  if (!IS_EXT) return;
  try {
    const stored = await chrome.storage.session.get(PENDING_SHOT_KEY);
    const shot = stored[PENDING_SHOT_KEY];
    if (shot?.dataUrl) {
      await chrome.storage.session.remove(PENDING_SHOT_KEY);
      openShot(shot);
    }
  } catch {
    /* storage.session tidak tersedia */
  }
}

async function init() {
  setShortcutLabel();
  bindEvents();

  if (IS_EXT) {
    chrome.runtime.onMessage.addListener((message, sender) => {
      if (message?.type === "mf:pageProgress") {
        handlePageProgress(message, sender);
      }
      return false;
    });
  }

  await loadSettings();
  applySettingsToUI();
  renderLastJob();

  await refreshServer();
  await detectPage();
  await loadLastJob();
  await checkPendingShot();

  if (PREVIEW && ["home", "settings", "account"].includes(PARAMS.get("tab"))) {
    showView(PARAMS.get("tab"));
  }

  if (PREVIEW && PARAMS.get("shot") === "1") {
    setTimeout(() => {
      openShot({
        dataUrl: "../web/demo/before.jpg",
        tab: { title: "Demo manga", url: "https://contoh.test/manga/1" },
      });
    }, 250);
  }
}

init();
