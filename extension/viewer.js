/* ============================================================
   MangaFlow — viewer hasil terjemahan
   ============================================================ */

const DEFAULT_SERVER = "http://127.0.0.1:8765";
const REPO = "https://github.com/zamdevv-jpg/MangaFlow---Translate-Manga-Free";
const JOB_RE = /^j_[a-f0-9]{12}$/;
const PAGE_RE = /^p\d{3}$/;
const TERMINAL = new Set(["done", "error", "cancelled"]);

const PARAMS = new URLSearchParams(location.search);
const DEMO = PARAMS.get("demo") === "1";
const IS_EXT =
  typeof chrome !== "undefined" &&
  Boolean(chrome.runtime?.id) &&
  Boolean(chrome.storage);

const $ = (id) => document.getElementById(id);

const LANG_NAMES = {
  id: "Indonesia",
  en: "Inggris",
  ja: "Jepang",
  ko: "Korea",
  zh: "Tionghoa (Sederhana)",
  "zh-TW": "Tionghoa (Tradisional)",
  ms: "Melayu",
  es: "Spanyol",
  fr: "Prancis",
  de: "Jerman",
  pt: "Portugis",
  ru: "Rusia",
  ar: "Arab",
  it: "Italia",
  th: "Thai",
  vi: "Vietnam",
  tr: "Turki",
  hi: "Hindi",
};

const ENGINE_NAMES = {
  local: "Lokal (Argos/OPUS + NLLB)",
  nllb: "NLLB-200 600M",
  "nllb-tuned": "NLLB-200 manga 1.3B",
  google: "Google Translate",
  mymemory: "MyMemory",
  deepl: "DeepL",
  gemini: "Gemini",
  microsoft: "Microsoft Translator",
  libretranslate: "LibreTranslate",
  ollama: "Ollama",
  openai: "OpenAI-compatible",
};

const DEMO_JOB = {
  id: "j_efd0007ac934",
  created_at: "2026-11-15T12:00:00+00:00",
  status: "done",
  stage: "Selesai",
  progress: 100,
  title: "yasei01 · contoh terjemahan",
  options: { engine: "local", tgt_lang: "id", tone: "santai" },
  page_count: 1,
  done_count: 1,
  pages: [
    {
      id: "p003",
      index: 3,
      name: "yasei03.jpg",
      width: 1351,
      height: 1920,
      status: "done",
      region_count: 5,
      v: 1,
    },
  ],
};

const state = {
  server: DEFAULT_SERVER,
  job: null,
  page: null,
  pollTimer: 0,
  dragging: false,
  compare: 50,
  demo: DEMO,
};

/* -------------------------------------------------------------- helpers */

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function normalizeServer(value) {
  const raw = String(value || "").trim();
  if (!raw) return DEFAULT_SERVER;
  try {
    const url = new URL(/^[a-z]+:\/\//i.test(raw) ? raw : `http://${raw}`);
    if (!["http:", "https:"].includes(url.protocol)) return DEFAULT_SERVER;
    const path = url.pathname.replace(/\/+$/, "");
    return `${url.origin}${path === "/" ? "" : path}`;
  } catch {
    return DEFAULT_SERVER;
  }
}

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function languageName(code) {
  return LANG_NAMES[code] || code || "—";
}

function engineName(id) {
  return ENGINE_NAMES[id] || id || "—";
}

function formatDate(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString("id-ID", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function setStatus(kind, text) {
  const chip = $("statusChip");
  if (!chip) return;
  chip.className = "status-chip";
  if (kind === "done") chip.classList.add("is-done");
  if (kind === "error") chip.classList.add("is-error");
  chip.replaceChildren(document.createElement("i"), document.createTextNode(text));
}

function showCover(text, isError = false) {
  const cover = $("stageCover");
  const stage = $("stage");
  if (!cover) return;
  if (stage) {
    stage.style.minHeight = "220px";
    stage.style.minWidth = "220px";
  }
  cover.hidden = false;
  cover.classList.toggle("is-error", isError);
  const spinner = cover.querySelector(".spinner");
  if (spinner) spinner.hidden = isError;
  if ($("stageCoverText")) $("stageCoverText").textContent = text;
}

function hideCover() {
  if ($("stageCover")) $("stageCover").hidden = true;
  const stage = $("stage");
  if (stage) {
    stage.style.minHeight = "";
    stage.style.minWidth = "";
  }
}

function showFatal(message) {
  setStatus("error", "Gagal");
  if ($("title")) $("title").textContent = "Hasil tidak bisa dimuat";
  if ($("subtitle")) $("subtitle").textContent = message;
  showCover(message, true);
  if ($("pagesCard")) $("pagesCard").hidden = true;
}

/* ---------------------------------------------------------------- server */

async function loadSettings() {
  if (!IS_EXT) return;
  try {
    const stored = await chrome.storage.sync.get("mf_settings");
    const settings = stored.mf_settings || {};
    state.server = normalizeServer(
      PARAMS.get("server") || settings.serverUrl || DEFAULT_SERVER
    );
  } catch {
    state.server = normalizeServer(PARAMS.get("server") || DEFAULT_SERVER);
  }
}

async function fetchJob() {
  if (state.demo) {
    await sleep(280);
    return DEMO_JOB;
  }
  const jobId = PARAMS.get("job") || "";
  if (!JOB_RE.test(jobId)) {
    throw new Error("ID job tidak valid. Buka viewer dari ekstensi MangaFlow.");
  }
  let response;
  try {
    response = await fetch(`${state.server}/api/jobs/${jobId}`, { cache: "no-store" });
  } catch {
    throw new Error(
      `Server MangaFlow di ${state.server} tidak bisa dihubungi. Pastikan server masih berjalan.`
    );
  }
  if (!response.ok) {
    throw new Error(
      response.status === 404
        ? "Job tidak ditemukan di server MangaFlow."
        : `Server membalas ${response.status}.`
    );
  }
  return response.json();
}

/* --------------------------------------------------------------- compare */

function setCompare(value) {
  const next = clamp(Math.round(Number(value) || 0), 0, 100);
  state.compare = next;
  if ($("compareRange")) $("compareRange").value = String(next);
  if ($("afterImg")) {
    $("afterImg").style.clipPath = `inset(0 0 0 ${next}%)`;
  }
  if ($("divider")) $("divider").style.left = `${next}%`;
  document.querySelectorAll("[data-compare]").forEach((button) => {
    const target = Number(button.dataset.compare);
    const active =
      Math.abs(target - next) <= 1 ||
      (target === 50 && next > 2 && next < 98);
    button.classList.toggle("is-active", active);
  });
}

function compareFromPointer(clientX) {
  const stage = $("stage");
  if (!stage) return;
  const rect = stage.getBoundingClientRect();
  if (!rect.width) return;
  setCompare(((clientX - rect.left) / rect.width) * 100);
}

function bindCompare() {
  const stage = $("stage");
  const range = $("compareRange");
  if (!stage || !range) return;

  const start = (event) => {
    if (event.button !== undefined && event.button !== 0) return;
    state.dragging = true;
    compareFromPointer(event.clientX);
    try {
      stage.setPointerCapture(event.pointerId);
    } catch {
      /* abaikan */
    }
  };
  stage.addEventListener("pointerdown", start);
  stage.addEventListener("pointermove", (event) => {
    if (!state.dragging) return;
    compareFromPointer(event.clientX);
  });
  const end = () => {
    state.dragging = false;
  };
  stage.addEventListener("pointerup", end);
  stage.addEventListener("pointercancel", end);
  range.addEventListener("input", () => setCompare(range.value));
  document.querySelectorAll("[data-compare]").forEach((button) => {
    button.addEventListener("click", () => setCompare(button.dataset.compare));
  });
}

/* ------------------------------------------------------------------ job */

function pageUrl(page, kind) {
  const version = page?.v ? `?v=${encodeURIComponent(page.v)}` : "";
  return `${state.server}/files/${state.job.id}/pages/${page.id}/${kind}${version}`;
}

function renderPages(job) {
  const card = $("pagesCard");
  const list = $("pageList");
  if (!card || !list) return;
  const pages = Array.isArray(job.pages) ? job.pages : [];
  card.hidden = pages.length <= 1;
  if (pages.length <= 1) return;

  if ($("pagesCount")) {
    $("pagesCount").textContent = `${job.done_count || 0} dari ${job.page_count || pages.length} selesai`;
  }

  list.replaceChildren(
    ...pages.map((page) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "page-thumb";
      button.dataset.pageId = page.id;

      const img = document.createElement("img");
      img.alt = "";
      img.loading = "lazy";
      const thumbKind =
        page.status === "done" ? "translated_thumb.jpg" : "thumb.jpg";
      img.src = pageUrl(page, thumbKind);
      img.addEventListener("error", () => {
        img.style.visibility = "hidden";
      });

      const label = document.createElement("span");
      label.textContent = `Hal. ${page.index}`;

      button.append(img, label);
      button.addEventListener("click", () => openPage(page.id));
      return button;
    })
  );
}

function highlightPage(pageId) {
  document.querySelectorAll(".page-thumb").forEach((button) => {
    button.classList.toggle("is-active", button.dataset.pageId === pageId);
  });
}

function openPage(pageId) {
  const job = state.job;
  if (!job) return;
  const pages = Array.isArray(job.pages) ? job.pages : [];
  const page =
    pages.find((item) => item.id === pageId) ||
    pages.find((item) => item.status === "done") ||
    pages[0];
  if (!page) {
    showFatal("Job ini tidak memiliki halaman.");
    return;
  }
  state.page = page;

  if ($("title")) $("title").textContent = job.title || `Halaman ${page.index}`;
  if ($("subtitle")) {
    $("subtitle").textContent = `${page.name || `Halaman ${page.index}`} · ${job.page_count || pages.length} halaman · ${languageName(job.options?.tgt_lang)}`;
  }

  const done = page.status === "done";
  if (done) {
    setStatus(
      job.status === "error" ? "error" : "done",
      job.status === "error" ? "Gagal" : "Selesai"
    );
  } else {
    setStatus("waiting", `Diproses · ${page.progress || 0}%`);
  }

  if ($("metaEngine")) $("metaEngine").textContent = engineName(job.options?.engine);
  if ($("metaTarget")) $("metaTarget").textContent = languageName(job.options?.tgt_lang);
  if ($("metaRegions")) $("metaRegions").textContent = page.region_count ?? "—";
  if ($("metaSize")) $("metaSize").textContent = page.width && page.height ? `${page.width} × ${page.height}` : "—";
  if ($("metaPages")) $("metaPages").textContent = `${job.done_count || 0} / ${job.page_count || pages.length}`;
  if ($("metaCreated")) $("metaCreated").textContent = formatDate(job.created_at);

  if ($("downloadPage")) {
    $("downloadPage").href = `${state.server}/api/jobs/${job.id}/pages/${page.id}/download`;
  }
  if ($("openEditor")) {
    $("openEditor").href = `${state.server}/#/job/${job.id}/page/${page.id}`;
    $("openEditor").classList.toggle("is-disabled", !done);
  }
  if ($("openWeb")) $("openWeb").href = state.server;
  if ($("brandHome")) $("brandHome").href = state.server;

  const params = new URLSearchParams(PARAMS);
  params.set("job", job.id);
  params.set("page", page.id);
  if (state.demo) params.set("demo", "1");
  else params.delete("demo");
  params.set("server", state.server);
  history.replaceState(null, "", `?${params.toString()}`);

  highlightPage(page.id);
  setCompare(50);
  showCover(done ? "Memuat halaman…" : "Halaman sedang diproses…", false);

  if (!done) {
    $("beforeImg").removeAttribute("src");
    $("afterImg").removeAttribute("src");
    return;
  }

  const before = $("beforeImg");
  const after = $("afterImg");

  before.onload = () => {
    hideCover();
  };
  before.onerror = () => {
    showCover("Halaman asli tidak bisa dimuat. Pastikan server MangaFlow aktif.", true);
    setStatus("error", "Gagal");
  };
  after.onload = () => {
    if (after.naturalWidth) hideCover();
  };
  after.onerror = () => {
    setStatus("error", "Hasil belum siap");
  };

  const suffix = page.v ? `?v=${encodeURIComponent(page.v)}` : "";
  if (state.demo) {
    before.src = "../web/demo/before.jpg";
    after.src = "../web/demo/after.jpg";
  } else {
    before.src = `${state.server}/files/${job.id}/pages/${page.id}/original.png${suffix}`;
    after.src = `${state.server}/files/${job.id}/pages/${page.id}/translated.png${suffix}`;
  }
}

function renderJob(job) {
  state.job = job;
  renderPages(job);
  const preferred = PAGE_RE.test(PARAMS.get("page") || "") ? PARAMS.get("page") : null;
  const pages = Array.isArray(job.pages) ? job.pages : [];
  const page =
    pages.find((item) => item.id === preferred) ||
    pages.find((item) => item.status === "done") ||
    pages[0];
  if (!page) {
    showFatal("Job ini belum memiliki halaman.");
    return;
  }
  openPage(page.id);
  if (!TERMINAL.has(job.status)) startPolling();
}

function startPolling() {
  stopPolling();
  state.pollTimer = setInterval(async () => {
    if (!state.job || state.demo) return;
    try {
      const job = await fetchJob();
      const changed = job.status !== state.job?.status;
      state.job = job;
      if (changed || job.status === "done") {
        renderPages(job);
        if (job.status === "done") {
          setStatus("done", "Selesai");
          const pageId = state.page?.id;
          const page = job.pages?.find((item) => item.id === pageId);
          if (page?.status === "done" && state.page?.status !== "done") {
            openPage(pageId);
          }
        } else if (job.status === "error") {
          setStatus("error", "Gagal");
          showCover(job.error || "Terjemahan gagal diproses.", true);
        }
      }
      if (TERMINAL.has(job.status)) stopPolling();
    } catch {
      /* coba lagi pada interval berikutnya */
    }
  }, 2500);
}

function stopPolling() {
  if (state.pollTimer) {
    clearInterval(state.pollTimer);
    state.pollTimer = 0;
  }
}

/* ----------------------------------------------------------------- boot */

async function init() {
  bindCompare();
  setCompare(50);
  if ($("repoLink")) $("repoLink").href = REPO;

  await loadSettings();
  if ($("openWeb")) $("openWeb").href = state.server;
  if ($("brandHome")) $("brandHome").href = state.server;

  if (!state.demo && !JOB_RE.test(PARAMS.get("job") || "")) {
    showFatal("Buka viewer ini dari ekstensi MangaFlow setelah menerjemahkan halaman atau screenshot.");
    return;
  }

  try {
    const job = await fetchJob();
    renderJob(job);
  } catch (error) {
    showFatal(error.message || "Hasil tidak bisa dimuat.");
  }
}

window.addEventListener("beforeunload", stopPolling);
init();
