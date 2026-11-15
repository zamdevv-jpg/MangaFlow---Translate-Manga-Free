/* MangaFlow — front-end controller */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const STATUS = {
    queued: "Antre",
    running: "Memproses",
    done: "Selesai",
    error: "Gagal",
    cancelled: "Dibatalkan",
  };
  const TERMINAL = new Set(["done", "error", "cancelled"]);
  const IMAGE_RE = /\.(png|jpe?g|webp|bmp|tiff?|gif)$/i;
  const ARCHIVE_RE = /\.(zip|cbz)$/i;
  const fallbackConfig = {
    languages: {
      sources: [
        { code: "auto", name: "Deteksi otomatis" },
        { code: "en", name: "Inggris" },
        { code: "ja", name: "Jepang" },
        { code: "ko", name: "Korea" },
        { code: "zh", name: "Tionghoa (Sederhana)" },
        { code: "zh-TW", name: "Tionghoa (Tradisional)" },
      ],
      targets: [
        { code: "id", name: "Indonesia", popular: true },
        { code: "en", name: "Inggris", popular: true },
        { code: "ms", name: "Melayu", popular: true },
        { code: "ja", name: "Jepang", popular: true },
        { code: "ko", name: "Korea", popular: true },
        { code: "zh", name: "Tionghoa (Sederhana)", popular: true },
      ],
    },
    engines: [{ id: "local", name: "Lokal (tanpa limit)", available: true, note: "Argos/OPUS" }],
    defaults: {
      src_lang: "auto",
      tgt_lang: "id",
      engine: "local",
      font: "comic",
      font_scale: 1,
      text_color: "auto",
      outline: false,
      min_score: 0.5,
      fallback_local: true,
      tone: "santai",
    },
    limits: { max_files: 300, max_upload_mb: 500 },
    cached_targets: ["id"],
  };

  const state = {
    config: null,
    files: [],
    job: null,
    jobId: null,
    page: null,
    pageId: null,
    regions: [],
    uploading: false,
    rendering: false,
    pollToken: 0,
    pollTimer: 0,
    routeSeq: 0,
    zoom: 1,
    fitWidth: 620,
    compare: 50,
    styleTouched: false,
    historyOpen: false,
    settingsOpen: false,
  };

  /* ------------------------------------------------------------------ utils */

  function esc(value) {
    return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[ch]));
  }

  function clamp(value, min, max) {
    return Math.max(min, Math.min(max, value));
  }

  function prettyBytes(bytes) {
    if (!bytes) return "0 B";
    const units = ["B", "KB", "MB", "GB"];
    const i = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
    return `${(bytes / Math.pow(1024, i)).toFixed(i ? 1 : 0)} ${units[i]}`;
  }

  function formatDate(iso) {
    if (!iso) return "—";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return "—";
    return d.toLocaleString("id-ID", { dateStyle: "medium", timeStyle: "short" });
  }

  function stem(name) {
    return String(name || "manga").replace(/\.[^.]+$/, "") || "manga";
  }

  function isImage(file) {
    return IMAGE_RE.test(file.name) || /^image\//.test(file.type || "");
  }

  function isArchive(file) {
    return ARCHIVE_RE.test(file.name);
  }

  function toast(message, type = "", duration = 3400) {
    const el = $("toast");
    if (!el) return;
    clearTimeout(toast.timer);
    el.textContent = message;
    el.className = `toast show${type ? " " + type : ""}`;
    toast.timer = setTimeout(() => {
      el.className = "toast";
    }, duration);
  }

  async function api(url, options = {}) {
    const res = await fetch(url, { cache: "no-store", ...options });
    const type = res.headers.get("content-type") || "";
    let data;
    if (type.includes("application/json")) {
      data = await res.json().catch(() => null);
    } else {
      data = await res.text().catch(() => "");
    }
    if (!res.ok) {
      let message = "Permintaan gagal.";
      if (data && typeof data === "object") {
        if (Array.isArray(data.detail)) {
          message = data.detail.map((d) => d.msg || JSON.stringify(d)).join(", ");
        } else if (data.detail) {
          message = data.detail;
        }
      } else if (typeof data === "string" && data.trim()) {
        message = data.trim().slice(0, 240);
      }
      const err = new Error(message);
      err.status = res.status;
      throw err;
    }
    return data;
  }

  function readPrefs() {
    try {
      return JSON.parse(localStorage.getItem("mangaflow.prefs") || "{}") || {};
    } catch {
      return {};
    }
  }

  function savePrefs() {
    try {
      localStorage.setItem("mangaflow.prefs", JSON.stringify({
        src: $("srcLang").value,
        tgt: $("tgtLang").value,
        engine: $("engine").value,
        font: $("fontStyle").value,
        scale: $("fontScale").value,
        color: $("textColor").value,
        outline: $("outlineText").checked,
        tone: $("tone").value,
        fallback: $("fallbackLocal").checked,
      }));
    } catch {
      /* ignore */
    }
  }

  /* ------------------------------------------------------------ views/routes */

  function showView(name) {
    $("home-view").classList.toggle("hidden", name !== "home");
    $("job-view").classList.toggle("hidden", name !== "job");
    $("viewer-view").classList.toggle("hidden", name !== "viewer");
    document.body.classList.toggle("viewer-mode", name === "viewer");
    if (name !== "home") window.scrollTo({ top: 0, behavior: "auto" });
  }

  function goHome(scrollTarget = "") {
    const hash = scrollTarget ? `#${scrollTarget}` : "#/";
    if (location.hash === hash) {
      handleRoute();
    } else {
      location.hash = hash;
    }
  }

  function goToJob(jobId) {
    const hash = `#/job/${jobId}`;
    if (location.hash === hash) handleRoute();
    else location.hash = hash;
  }

  function goToPage(pageId) {
    if (!state.jobId) return;
    location.hash = `#/job/${state.jobId}/page/${pageId}`;
  }

  function currentRoute() {
    const hash = location.hash || "#/";
    const page = hash.match(/^#\/job\/(j_[a-f0-9]{12})\/page\/(p\d{3})$/);
    if (page) return { view: "viewer", jobId: page[1], pageId: page[2] };
    const job = hash.match(/^#\/job\/(j_[a-f0-9]{12})$/);
    if (job) return { view: "job", jobId: job[1] };
    return { view: "home", anchor: hash.startsWith("#/") ? "" : hash.slice(1) };
  }

  let routeTimer = 0;
  function onHashChange() {
    clearTimeout(routeTimer);
    routeTimer = setTimeout(handleRoute, 0);
  }

  async function handleRoute() {
    const seq = ++state.routeSeq;
    const route = currentRoute();
    stopPolling();

    if (route.view === "home") {
      state.page = null;
      showView("home");
      document.title = "MangaFlow — Penerjemah Manga Tanpa Limit";
      if (route.anchor) {
        const target = document.getElementById(route.anchor);
        if (target) {
          requestAnimationFrame(() => target.scrollIntoView({ behavior: "smooth", block: "start" }));
        }
      } else if (location.hash === "#/" || !location.hash) {
        window.scrollTo({ top: 0, behavior: "auto" });
      }
      return;
    }

    if (route.view === "job") {
      showView("job");
      try {
        const job = await loadJob(route.jobId);
        if (seq !== state.routeSeq) return;
        renderJob(job);
        if (!TERMINAL.has(job.status)) startPolling(route.jobId);
      } catch (err) {
        if (seq !== state.routeSeq) return;
        toast(err.message || "Job tidak ditemukan.", "error");
        goHome();
      }
      return;
    }

    if (route.view === "viewer") {
      showView("viewer");
      try {
        if (!state.job || state.jobId !== route.jobId) {
          await loadJob(route.jobId);
        }
        if (seq !== state.routeSeq) return;
        await openPage(route.pageId, seq);
      } catch (err) {
        if (seq !== state.routeSeq) return;
        toast(err.message || "Halaman tidak ditemukan.", "error");
        goToJob(route.jobId);
      }
    }
  }

  /* ------------------------------------------------------------------ config */

  async function loadConfig() {
    try {
      state.config = await api("/api/config");
    } catch (err) {
      state.config = fallbackConfig;
      toast("Konfigurasi server tidak terbaca. Memakai pengaturan dasar.", "error", 5000);
    }
    applyConfig();
  }

  function applyConfig() {
    const cfg = state.config || fallbackConfig;
    const defaults = cfg.defaults || fallbackConfig.defaults;
    const prefs = readPrefs();

    const sources = (cfg.languages && cfg.languages.sources) || fallbackConfig.languages.sources;
    const targets = (cfg.languages && cfg.languages.targets) || fallbackConfig.languages.targets;
    const cached = new Set(cfg.cached_targets || []);

    const srcSel = $("srcLang");
    srcSel.innerHTML = "";
    sources.forEach((lang) => {
      const opt = document.createElement("option");
      opt.value = lang.code;
      opt.textContent = lang.name;
      srcSel.appendChild(opt);
    });
    const preferredSource = prefs.src && sources.some((l) => l.code === prefs.src)
      ? prefs.src
      : (sources.some((l) => l.code === defaults.src_lang) ? defaults.src_lang : "auto");
    srcSel.value = preferredSource;

    const tgtSel = $("tgtLang");
    tgtSel.innerHTML = "";
    targets.forEach((lang) => {
      const opt = document.createElement("option");
      opt.value = lang.code;
      opt.textContent = `${lang.name}${cached.has(lang.code) ? " · siap offline" : ""}`;
      opt.dataset.name = lang.name;
      tgtSel.appendChild(opt);
    });
    const preferredTarget = prefs.tgt && targets.some((l) => l.code === prefs.tgt)
      ? prefs.tgt
      : (targets.some((l) => l.code === defaults.tgt_lang) ? defaults.tgt_lang : (targets[0] || {}).code);
    if (preferredTarget) tgtSel.value = preferredTarget;

    const engineSel = $("engine");
    const engines = cfg.engines || fallbackConfig.engines;
    const pendingLabel = {
      deepl: "butuh kunci",
      gemini: "butuh kunci",
      microsoft: "butuh kunci",
      openai: "butuh kunci/endpoint",
      libretranslate: "butuh URL",
      ollama: "tidak aktif",
      nllb: "model belum ada",
      "nllb-tuned": "model belum ada",
    };
    engineSel.innerHTML = "";
    engines.forEach((engine) => {
      const opt = document.createElement("option");
      opt.value = engine.id;
      const pending = engine.available === false
        ? ` — ${pendingLabel[engine.id] || "belum siap"}`
        : "";
      opt.textContent = `${engine.name}${pending}`;
      opt.disabled = engine.available === false;
      opt.dataset.note = engine.note || "";
      engineSel.appendChild(opt);
    });
    const preferredEngine = prefs.engine && engines.some((e) => e.id === prefs.engine && e.available !== false)
      ? prefs.engine
      : (engines.some((e) => e.id === defaults.engine && e.available !== false) ? defaults.engine : "local");
    engineSel.value = preferredEngine;
    if (!engineSel.value && engineSel.options.length) engineSel.selectedIndex = 0;

    const font = prefs.font || defaults.font || "comic";
    if (["comic", "sans", "serif", "cjk"].includes(font)) $("fontStyle").value = font;
    const scale = Number(prefs.scale || defaults.font_scale * 100 || 100);
    $("fontScale").value = String(clamp(Math.round(scale), 70, 140));
    $("scaleOut").textContent = `${$("fontScale").value}%`;
    const color = prefs.color || defaults.text_color || "auto";
    if (["auto", "black", "white"].includes(color)) $("textColor").value = color;
    $("outlineText").checked = prefs.outline != null ? Boolean(prefs.outline) : Boolean(defaults.outline);
    $("fallbackLocal").checked = prefs.fallback != null ? Boolean(prefs.fallback) : defaults.fallback_local !== false;
    const tone = prefs.tone || defaults.tone || "santai";
    if (["santai", "netral", "baku"].includes(tone)) $("tone").value = tone;
    const minScore = Number(defaults.min_score || 0.5) * 100;
    $("minScore").value = String(clamp(Math.round(minScore), 30, 80));
    $("scoreOut").textContent = (Number($("minScore").value) / 100).toFixed(2);

    const limits = cfg.limits || fallbackConfig.limits;
    $("dropHint").textContent =
      `PNG, JPG, WEBP, ZIP, CBZ · maks. ${limits.max_files || 300} halaman · ${limits.max_upload_mb || 500} MB`;
    $("fileInput").setAttribute("accept", ".png,.jpg,.jpeg,.webp,.bmp,.tif,.tiff,.gif,.zip,.cbz");

    renderLanguageChips();
    updateEngineInfo();
  }

  function renderLanguageChips() {
    const cfg = state.config || fallbackConfig;
    const targets = (cfg.languages && cfg.languages.targets) || [];
    const cached = new Set(cfg.cached_targets || []);
    let popular = targets.filter((l) => l.popular);
    if (!popular.length) popular = targets.slice(0, 14);
    const box = $("languageChips");
    box.innerHTML = "";
    popular.forEach((lang) => {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = `chip${cached.has(lang.code) ? " ready" : ""}`;
      chip.textContent = lang.name;
      chip.title = cached.has(lang.code) ? "Model sudah tersimpan" : "Model akan diunduh saat dipakai";
      chip.addEventListener("click", () => {
        $("tgtLang").value = lang.code;
        updateEngineInfo();
        savePrefs();
        toast(`Target: ${lang.name}`);
      });
      box.appendChild(chip);
    });
  }

  function engineMeta(id) {
    const engines = (state.config && state.config.engines) || [];
    return engines.find((e) => e.id === id) || { id, name: id, note: "" };
  }

  function updateEngineInfo() {
    const cfg = state.config || fallbackConfig;
    const engineId = $("engine").value;
    const targetCode = $("tgtLang").value;
    const target = ((cfg.languages && cfg.languages.targets) || []).find((l) => l.code === targetCode);
    const targetName = target ? target.name : targetCode;
    const cached = new Set(cfg.cached_targets || []);
    const meta = engineMeta(engineId);
    const local = engineId === "local";
    const nllb = engineId === "nllb" || engineId === "nllb-tuned";
    const tuned = engineId === "nllb-tuned";
    const sourceCode = $("srcLang").value;
    const autoSource = sourceCode === "auto";
    const cjkSource = ["ja", "ko", "zh", "zh-TW"].includes(sourceCode);
    const nllbMeta = ((cfg.engines || [])).find((e) => e.id === "nllb");
    const tunedMeta = ((cfg.engines || [])).find((e) => e.id === "nllb-tuned");
    const nllbReady = Boolean(
      (nllbMeta && nllbMeta.available !== false) ||
      (tunedMeta && tunedMeta.available !== false)
    );
    const autoNllb = local && autoSource && $("fallbackLocal").checked && nllbReady;
    let note;
    if (local) {
      const baseNote = cached.has(targetCode)
        ? `Model ${targetName} tersimpan · berjalan offline`
        : `Model ${targetName} akan diunduh sekali · lalu tanpa limit`;
      note = autoNllb ? `${baseNote} · Jepang/Korea/Tionghoa otomatis NLLB-200` : baseNote;
    } else if (nllb) {
      note = meta.available === false
        ? (tuned ? "Model manga belum tersedia · buka Pengaturan mesin" : "Model NLLB-200 belum terunduh")
        : (tuned ? `${meta.note || "NLLB-200 manga siap"} · ${targetName} offline` : `NLLB-200 siap · ${targetName} offline · tanpa limit`);
    } else {
      note = meta.note || "Mesin online opsional";
      if (meta.available === false) note = `${note} · buka Pengaturan mesin`;
    }
    let help;
    if (local) {
      help = autoNllb
        ? "Argos/OPUS lokal · CJK otomatis ke NLLB-200"
        : "Argos/OPUS lokal · cepat & tanpa limit";
    } else if (nllb) {
      help = tuned
        ? (meta.note || "NLLB-200 manga · kamus manga · offline")
        : "NLLB-200 lokal · 200 bahasa · CPU lebih lambat";
    } else if (meta.available === false) {
      help = "Mesin sudah terpasang · isi kunci/URL di Pengaturan mesin";
    } else {
      help = meta.name || engineId;
    }
    if (local && cjkSource && !autoNllb && nllbReady) {
      help += " · NLLB lebih akurat untuk CJK";
    }
    $("engineHelp").textContent = help;
    $("engineNote").innerHTML = `<span class="pulse-dot"></span><span></span>`;
    const span = $("engineNote").querySelector("span:last-child");
    if (span) span.textContent = note;
  }

  /* --------------------------------------------------------- engine settings */

  const PROVIDER_SECTIONS = [
    {
      engine: "deepl",
      title: "DeepL",
      pending: "Perlu kunci",
      desc: "Online · kualitas natural. Kunci gratis berakhiran :fx.",
      fields: [
        { key: "deepl_api_key", label: "API key", type: "password", wide: true },
        { key: "deepl_api_url", label: "URL API", type: "text", wide: true, hint: "Kosongkan untuk otomatis memilih server gratis/pro." },
      ],
    },
    {
      engine: "gemini",
      title: "Gemini",
      pending: "Perlu kunci",
      desc: "Online · LLM untuk dialog yang lebih natural.",
      fields: [
        { key: "gemini_api_key", label: "API key", type: "password", wide: true },
        { key: "gemini_model", label: "Model", type: "text", hint: "Contoh: gemini-2.5-flash" },
      ],
    },
    {
      engine: "microsoft",
      title: "Microsoft Translator",
      pending: "Perlu kunci",
      desc: "Online · Azure Translator.",
      fields: [
        { key: "azure_translator_key", label: "API key", type: "password", wide: true },
        { key: "azure_translator_region", label: "Region", type: "text", hint: "Contoh: eastasia" },
        { key: "azure_translator_endpoint", label: "Endpoint", type: "text", wide: true },
      ],
    },
    {
      engine: "openai",
      title: "OpenAI-compatible",
      pending: "Perlu kunci/endpoint",
      desc: "Online atau endpoint lokal (LM Studio, vLLM, dsb.).",
      fields: [
        { key: "openai_base_url", label: "Base URL", type: "text", wide: true },
        { key: "openai_api_key", label: "API key", type: "password", wide: true, optional: true },
        { key: "openai_model", label: "Model", type: "text", wide: true },
      ],
    },
    {
      engine: "libretranslate",
      title: "LibreTranslate",
      pending: "Perlu URL",
      desc: "Server terjemahan mandiri · tanpa kunci pihak ketiga.",
      fields: [
        { key: "libretranslate_url", label: "URL server", type: "text", wide: true },
        { key: "libretranslate_api_key", label: "API key", type: "password", wide: true, optional: true },
      ],
    },
    {
      engine: "ollama",
      title: "Ollama",
      pending: "Tidak aktif",
      desc: "LLM lokal · tanpa internet setelah model di-pull.",
      fields: [
        { key: "ollama_url", label: "URL server", type: "text", wide: true },
        { key: "ollama_model", label: "Model", type: "text", wide: true, hint: "Contoh: qwen2.5:7b" },
      ],
    },
  ];

  function settingsField(spec, values, configured, envLocked) {
    const locked = Boolean(envLocked[spec.key]);
    const secret = spec.type === "password";
    const value = secret ? "" : (values[spec.key] ?? "");
    let placeholder = spec.placeholder || "";
    if (secret) {
      placeholder = locked
        ? "Diatur dari environment"
        : configured[spec.key]
          ? "Tersimpan · kosongkan untuk mempertahankan"
          : "Masukkan kunci…";
    }
    const opt = spec.optional ? ' <em>(opsional)</em>' : "";
    return `<label class="field ${spec.wide ? "wide" : ""}">
      <span>${esc(spec.label)}${opt}</span>
      <input type="${secret ? "password" : "text"}" data-setting="${esc(spec.key)}"
        value="${esc(value)}" placeholder="${esc(placeholder)}"
        autocomplete="off" spellcheck="false"${locked ? " disabled" : ""}>
      ${spec.hint ? `<small>${esc(spec.hint)}</small>` : ""}
    </label>`;
  }

  function settingsProviderSection(provider, data) {
    const values = data.values || {};
    const configured = data.configured || {};
    const envLocked = data.env_locked || {};
    const meta = engineMeta(provider.engine);
    const isReady = meta.available !== false;
    const section = document.createElement("section");
    section.className = "settings-section";
    const secretFields = provider.fields.filter((f) => f.type === "password" && configured[f.key]);
    section.innerHTML = `
      <div class="settings-section-head">
        <div><h3>${esc(provider.title)}</h3><small>${esc(provider.desc)}</small></div>
        <span class="settings-badge ${isReady ? "ok" : "bad"}">${isReady ? "Siap" : esc(provider.pending || "Belum")}</span>
      </div>
      <div class="settings-grid">${provider.fields.map((f) => settingsField(f, values, configured, envLocked)).join("")}</div>
      <div class="settings-test-row">
        <button class="btn btn-ghost btn-sm" type="button" data-test-engine="${esc(provider.engine)}">Tes koneksi</button>
        ${secretFields.map((f) => `<button class="link-btn danger" type="button" data-clear-setting="${esc(f.key)}">Hapus kunci</button>`).join("")}
        <span class="settings-result" data-result-for="${esc(provider.engine)}"></span>
      </div>`;
    return section;
  }

  function settingsModelSection(data) {
    const info = data.nllb || {};
    const rows = [
      ["NLLB-200 600M", info.base],
      ["NLLB-200 1.3B", info.large],
      ["NLLB-200 manga (latihan)", info.tuned],
    ];
    const section = document.createElement("section");
    section.className = "settings-section";
    const ready = Boolean((info.large || {}).ready || (info.tuned || {}).ready);
    section.innerHTML = `
      <div class="settings-section-head">
        <div><h3>Model terjemahan lokal</h3><small>NLLB-200 berjalan offline di CPU. Model 1.3B lebih akurat, tetapi lebih lambat.</small></div>
        <span class="settings-badge ${ready ? "ok" : "bad"}">${ready ? "Siap" : "Belum"}</span>
      </div>
      ${rows.map(([label, row]) => {
        const r = row || {};
        return `<div class="settings-model-row">
          <strong>${esc(label)}</strong>
          <span class="path" title="${esc(r.path || "")}">${esc(r.path || "—")}</span>
          <span>${r.size_mb ? `${r.size_mb} MB` : ""}</span>
          <span class="settings-badge ${r.ready ? "ok" : "bad"}">${r.ready ? "Siap" : "Belum"}</span>
        </div>`;
      }).join("")}
      <div class="settings-test-row">
        <button class="btn btn-ghost btn-sm" type="button" data-test-engine="nllb-tuned">Tes model NLLB</button>
        <span class="settings-result" data-result-for="nllb-tuned"></span>
      </div>
      <small class="settings-hint">Mengunduh model 1.3B: <code>python samples/download_nllb_large.py</code></small>`;
    return section;
  }

  function renderSettings(data) {
    const body = $("settingsBody");
    if (!body) return;
    body.innerHTML = "";
    body.appendChild(settingsModelSection(data));
    PROVIDER_SECTIONS.forEach((provider) => body.appendChild(settingsProviderSection(provider, data)));
  }

  function setSettingsStatus(message, kind = "") {
    const el = $("settingsStatus");
    if (!el) return;
    el.textContent = message;
    el.className = `settings-status${kind ? " " + kind : ""}`;
  }

  async function openSettings() {
    state.settingsOpen = true;
    $("settingsModal").classList.remove("hidden");
    document.body.classList.add("modal-open");
    $("settingsBody").innerHTML = `<div class="history-empty">Memuat pengaturan…</div>`;
    setSettingsStatus("Memuat pengaturan…");
    try {
      const data = await api("/api/settings");
      state.config = { ...(state.config || {}), engines: data.engines || (state.config || {}).engines };
      renderSettings(data.settings || {});
      setSettingsStatus("Perubahan langsung aktif setelah disimpan.");
    } catch (err) {
      $("settingsBody").innerHTML = `<div class="history-empty">${esc(err.message || "Gagal memuat pengaturan.")}</div>`;
      setSettingsStatus(err.message || "Gagal memuat pengaturan.", "error");
    }
  }

  function closeSettings() {
    state.settingsOpen = false;
    $("settingsModal").classList.add("hidden");
    if (!state.historyOpen) document.body.classList.remove("modal-open");
  }

  function collectSettings() {
    const values = {};
    $$("#settingsBody [data-setting]").forEach((input) => {
      const key = input.dataset.setting;
      if (input.type === "password") {
        if (input.value.trim()) values[key] = input.value.trim();
        else if (input.dataset.clear === "1") values[key] = null;
      } else {
        values[key] = input.value.trim();
      }
    });
    return values;
  }

  async function saveSettings(showToast = true) {
    setSettingsStatus("Menyimpan…");
    try {
      const data = await api("/api/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ values: collectSettings() }),
      });
      state.config = { ...(state.config || {}), engines: data.engines || (state.config || {}).engines };
      applyConfig();
      renderSettings(data.settings || {});
      setSettingsStatus("Tersimpan. Daftar mesin sudah diperbarui.", "ok");
      if (showToast) toast("Pengaturan mesin disimpan.");
      return data;
    } catch (err) {
      setSettingsStatus(err.message || "Gagal menyimpan pengaturan.", "error");
      if (showToast) toast(err.message || "Gagal menyimpan pengaturan.", "error");
      throw err;
    }
  }

  async function testSettingsEngine(engine) {
    const resultEl = document.querySelector(`[data-result-for="${engine}"]`);
    if (resultEl) {
      resultEl.textContent = "Menyimpan & menguji…";
      resultEl.className = "settings-result";
    }
    try {
      await saveSettings(false);
      const result = await api(`/api/settings/test/${engine}`, { method: "POST" });
      const target = document.querySelector(`[data-result-for="${engine}"]`);
      if (target) {
        target.textContent = result.message || (result.ok ? "Siap." : "Gagal.");
        target.className = `settings-result ${result.ok ? "ok" : "error"}`;
      }
      setSettingsStatus(result.message || "Tes selesai.", result.ok ? "ok" : "error");
    } catch (err) {
      const target = document.querySelector(`[data-result-for="${engine}"]`);
      if (target) {
        target.textContent = err.message || "Tes gagal.";
        target.className = "settings-result error";
      }
    }
  }

  async function clearSetting(key) {
    if (!window.confirm("Hapus kunci/URL ini dari pengaturan?")) return;
    setSettingsStatus("Menghapus…");
    try {
      const data = await api("/api/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ values: { [key]: null } }),
      });
      state.config = { ...(state.config || {}), engines: data.engines || (state.config || {}).engines };
      applyConfig();
      renderSettings(data.settings || {});
      setSettingsStatus("Nilai dihapus.", "ok");
    } catch (err) {
      setSettingsStatus(err.message || "Gagal menghapus.", "error");
    }
  }

  /* ------------------------------------------------------------------ upload */

  function addFiles(fileList) {
    const incoming = Array.from(fileList || []);
    if (!incoming.length) return;
    let rejected = 0;
    const maxFiles = (state.config && state.config.limits && state.config.limits.max_files) || 300;

    incoming.forEach((file) => {
      if (!isImage(file) && !isArchive(file)) {
        rejected += 1;
        return;
      }
      const duplicate = state.files.some((entry) => entry.file.name === file.name && entry.file.size === file.size);
      if (duplicate) return;
      state.files.push({
        file,
        id: `f_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 7)}`,
        url: isImage(file) ? URL.createObjectURL(file) : "",
        archive: isArchive(file),
      });
    });

    if (state.files.length > maxFiles) {
      state.files = state.files.slice(0, maxFiles);
      toast(`Maksimal ${maxFiles} file per job.`, "error");
    }
    if (rejected) toast(`${rejected} file dilewati (format tidak didukung).`, "error");
    renderFiles();
    $("fileInput").value = "";
  }

  function removeFile(id) {
    const idx = state.files.findIndex((f) => f.id === id);
    if (idx < 0) return;
    const [entry] = state.files.splice(idx, 1);
    if (entry.url) URL.revokeObjectURL(entry.url);
    renderFiles();
  }

  function clearFiles() {
    state.files.forEach((entry) => entry.url && URL.revokeObjectURL(entry.url));
    state.files = [];
    $("fileInput").value = "";
    renderFiles();
  }

  function renderFiles() {
    const list = $("fileList");
    const grid = $("fileGrid");
    const count = $("fileCount");
    list.classList.toggle("hidden", state.files.length === 0);
    count.textContent = `${state.files.length} file siap diunggah`;
    grid.innerHTML = "";
    state.files.forEach((entry, index) => {
      const card = document.createElement("div");
      card.className = "file-card";
      if (entry.archive) {
        const archive = document.createElement("div");
        archive.className = "archive-thumb";
        archive.innerHTML = `<svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M4 7h16v13H4zM4 7l2-3h12l2 3M10 11h4M10 15h4"/></svg><span>ZIP / CBZ</span>`;
        card.appendChild(archive);
      } else {
        const img = document.createElement("img");
        img.alt = entry.file.name;
        img.src = entry.url;
        img.loading = "lazy";
        card.appendChild(img);
      }
      const idx = document.createElement("span");
      idx.className = "file-index";
      idx.textContent = String(index + 1).padStart(2, "0");
      card.appendChild(idx);
      const name = document.createElement("span");
      name.className = "file-name";
      name.textContent = `${entry.file.name} · ${prettyBytes(entry.file.size)}`;
      card.appendChild(name);
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "file-remove";
      remove.setAttribute("aria-label", `Hapus ${entry.file.name}`);
      remove.textContent = "×";
      remove.addEventListener("click", (event) => {
        event.stopPropagation();
        removeFile(entry.id);
      });
      card.appendChild(remove);
      grid.appendChild(card);
    });
  }

  function collectOptions() {
    return {
      src_lang: $("srcLang").value || "auto",
      tgt_lang: $("tgtLang").value || "id",
      engine: $("engine").value || "local",
      font: $("fontStyle").value || "comic",
      font_scale: Number($("fontScale").value || 100) / 100,
      text_color: $("textColor").value || "auto",
      outline: $("outlineText").checked,
      min_score: Number($("minScore").value || 50) / 100,
      fallback_local: $("fallbackLocal").checked,
      tone: $("tone").value || "santai",
    };
  }

  async function submitJob() {
    if (state.uploading) return;
    if (!state.files.length) {
      toast("Pilih halaman manga terlebih dahulu.", "error");
      $("dropzone").focus();
      return;
    }
    const opts = collectOptions();
    if (opts.src_lang !== "auto" && opts.src_lang === opts.tgt_lang) {
      toast("Bahasa sumber dan target sama. Pilih salah satu yang berbeda.", "error");
      return;
    }

    state.uploading = true;
    const btn = $("translateBtn");
    const label = btn.querySelector(".btn-label");
    btn.classList.add("loading");
    btn.disabled = true;
    if (label) label.textContent = "Mengunggah…";

    try {
      const form = new FormData();
      state.files.forEach((entry) => form.append("files", entry.file, entry.file.name));
      form.append("options", JSON.stringify(opts));
      form.append("title", stem(state.files[0].file.name).replace(/[_-]+/g, " ").slice(0, 120));
      const job = await api("/api/jobs", { method: "POST", body: form });
      clearFiles();
      state.job = job;
      state.jobId = job.id;
      savePrefs();
      toast("Job dibuat. Memproses halaman…");
      goToJob(job.id);
    } catch (err) {
      toast(err.message || "Gagal mengunggah file.", "error", 6000);
    } finally {
      state.uploading = false;
      btn.classList.remove("loading");
      btn.disabled = false;
      if (label) label.textContent = "Terjemahkan sekarang";
    }
  }

  async function makeSampleFile() {
    const res = await fetch("/demo/sample.jpg", { cache: "no-store" });
    if (!res.ok) throw new Error("Contoh halaman tidak tersedia.");
    const blob = await res.blob();
    return new File([blob], "Pepper-and-Carrot_E01P01.jpg", { type: blob.type || "image/jpeg" });
  }

  async function useSample(start) {
    if (state.uploading) return;
    if (state.files.length && !window.confirm("Ganti file yang sudah dipilih dengan halaman contoh?")) return;
    const buttons = [$("sampleBtn"), $("heroSampleBtn")].filter(Boolean);
    buttons.forEach((b) => { b.disabled = true; });
    try {
      const file = await makeSampleFile();
      clearFiles();
      addFiles([file]);
      if (start) {
        await submitJob();
      } else {
        goHome("translate");
        setTimeout(() => {
          $("dropzone").scrollIntoView({ behavior: "smooth", block: "center" });
          toast("Halaman contoh siap. Klik “Terjemahkan sekarang”.");
        }, 180);
      }
    } catch (err) {
      toast(err.message || "Gagal memuat contoh.", "error");
    } finally {
      buttons.forEach((b) => { b.disabled = false; });
    }
  }

  /* --------------------------------------------------------------- job view */

  async function loadJob(jobId) {
    const job = await api(`/api/jobs/${jobId}`);
    state.job = job;
    state.jobId = job.id;
    return job;
  }

  function startPolling(jobId) {
    stopPolling();
    const token = ++state.pollToken;
    const tick = async () => {
      if (token !== state.pollToken) return;
      try {
        const job = await api(`/api/jobs/${jobId}`);
        if (token !== state.pollToken) return;
        state.job = job;
        state.jobId = job.id;
        renderJob(job);
        if (TERMINAL.has(job.status)) {
          if (job.status === "done") toast("Semua halaman selesai. Hasil siap diunduh.");
          if (job.status === "error") toast(job.error || "Job gagal diproses.", "error", 5000);
          return;
        }
      } catch (err) {
        if (token !== state.pollToken) return;
        toast(err.message || "Koneksi ke server terputus.", "error");
      }
      if (token === state.pollToken) {
        state.pollTimer = setTimeout(tick, 1000);
      }
    };
    tick();
  }

  function stopPolling() {
    state.pollToken += 1;
    clearTimeout(state.pollTimer);
    state.pollTimer = 0;
  }

  function renderJob(job) {
    if (!job) return;
    document.title = `${job.title || "Job"} — MangaFlow`;
    $("jobTitle").textContent = job.title || "Manga tanpa judul";
    const progress = clamp(Number(job.progress || 0), 0, 100);
    $("jobStage").textContent = job.stage || STATUS[job.status] || "Memproses";
    $("jobPercent").textContent = `${progress}%`;
    $("jobBar").style.width = `${progress}%`;
    $("jobMessage").textContent = job.message || (job.status === "done" ? "Semua halaman selesai." : "—");
    const pages = job.pages || [];
    const finished = pages.filter((p) => p.status === "done" || p.status === "error").length;
    $("jobCount").textContent = `${finished} / ${job.page_count || pages.length} halaman`;

    const warnings = $("jobWarnings");
    const warningList = job.warnings || [];
    warnings.classList.toggle("hidden", warningList.length === 0);
    warnings.innerHTML = warningList.length
      ? `<strong>Catatan:</strong>${warningList.map((w) => `<p>${esc(w)}</p>`).join("")}`
      : "";

    renderPageGrid(pages);

    const cancel = $("cancelJob");
    const running = job.status === "queued" || job.status === "running";
    cancel.classList.toggle("hidden", !running);

    const download = $("downloadAll");
    const canDownload = job.status === "done" && pages.some((p) => p.status === "done");
    download.classList.toggle("disabled", !canDownload);
    download.setAttribute("aria-disabled", String(!canDownload));
    if (canDownload) {
      download.href = `/api/jobs/${job.id}/download`;
      download.removeAttribute("tabindex");
    } else {
      download.href = "#";
      download.setAttribute("tabindex", "-1");
    }

    const firstDone = pages.find((p) => p.status === "done");
    const openFirst = $("openFirst");
    if (firstDone) {
      openFirst.href = `#/job/${job.id}/page/${firstDone.id}`;
      openFirst.classList.remove("disabled");
      openFirst.removeAttribute("tabindex");
    } else {
      openFirst.href = "#";
      openFirst.classList.add("disabled");
      openFirst.setAttribute("tabindex", "-1");
    }
  }

  function pageStatusClass(status) {
    return ["done", "error", "running"].includes(status) ? status : "";
  }

  function renderPageGrid(pages) {
    const grid = $("pageGrid");
    if (!pages || !pages.length) {
      grid.innerHTML = `<div class="region-empty">Belum ada halaman.</div>`;
      return;
    }
    grid.innerHTML = pages.map((page) => {
      const cls = pageStatusClass(page.status);
      const status = STATUS[page.status] || page.status;
      const thumb = page.status === "done" && page.translated_thumb_url ? page.translated_thumb_url : (page.thumb_url || "");
      const progress = clamp(Number(page.progress || 0), 0, 100);
      const foot = page.status === "done"
        ? `${page.region_count || 0} balon · ${page.src_lang ? String(page.src_lang).toUpperCase() : "—"}`
        : (page.stage || status);
      const overlay = page.status === "queued" || page.status === "running"
        ? `<div class="page-overlay"><span class="spinner"></span></div>` : "";
      const error = page.status === "error" && page.error
        ? `<div class="page-error">${esc(page.error)}</div>` : "";
      const bar = page.status === "running"
        ? `<div class="page-progress"><i style="width:${progress}%"></i></div>` : "";
      return `<button type="button" class="page-card ${cls}" data-page-id="${esc(page.id)}" aria-label="Halaman ${page.index}: ${esc(status)}">
        <div class="page-thumb-wrap">
          ${thumb ? `<img class="page-thumb" src="${esc(thumb)}" alt="" loading="lazy">` : `<div class="page-thumb"></div>`}
          <span class="page-status ${cls}">${esc(status)}</span>
          ${overlay}
        </div>
        ${bar}
        <div class="page-card-foot">
          <strong>${String(page.index).padStart(3, "0")} · ${esc(page.name || "halaman")}</strong>
          <span>${esc(foot)}</span>
        </div>
        ${error}
      </button>`;
    }).join("");
  }

  /* ---------------------------------------------------------------- viewer */

  async function openPage(pageId, seq) {
    if (!state.jobId) return;
    const page = await api(`/api/jobs/${state.jobId}/pages/${pageId}`);
    if (seq != null && seq !== state.routeSeq) return;
    if (page.status !== "done") {
      toast("Halaman ini belum selesai diproses.", "error");
      goToJob(state.jobId);
      return;
    }
    state.pageId = pageId;
    state.page = page;
    state.regions = (page.regions && page.regions.regions) ? page.regions.regions.slice() : [];
    state.styleTouched = false;
    setupViewer({ reset: true });
  }

  function setupViewer({ reset }) {
    if (!state.page) return;
    const page = state.page;
    const job = state.job || {};
    document.title = `${job.title || "Manga"} · Halaman ${page.index} — MangaFlow`;
    $("viewerTitle").textContent = job.title || "Manga tanpa judul";
    $("viewerPageNo").textContent = `HALAMAN ${page.index}`;
    const pages = job.pages || [];
    const pageIndex = pages.findIndex((p) => p.id === page.id);
    $("pageCounter").textContent = `${pageIndex >= 0 ? pageIndex + 1 : page.index} / ${pages.length || job.page_count || 1}`;
    $("prevPage").disabled = pageIndex <= 0;
    $("nextPage").disabled = pageIndex < 0 || pageIndex >= pages.length - 1;

    const download = $("downloadPage");
    download.href = `/api/jobs/${state.jobId}/pages/${page.id}/download`;

    const original = $("originalImg");
    const translated = $("translatedImg");
    if (original.getAttribute("src") !== page.original_url) original.src = page.original_url;
    if (page.translated_url && translated.getAttribute("src") !== page.translated_url) translated.src = page.translated_url;
    translated.alt = `Halaman ${page.index} terjemahan`;

    renderRail(pages, page.id);
    renderRegions();
    initViewerStyle();
    if (reset) {
      state.compare = 50;
      $("compareRange").value = "50";
      setCompare(50);
      state.zoom = 1;
      requestAnimationFrame(() => fitViewer(false));
    } else {
      setCompare(Number($("compareRange").value || 50));
      applyZoom();
    }
    renderPins();
  }

  function renderRail(pages, activeId) {
    const rail = $("pageRail");
    rail.innerHTML = "";
    pages.forEach((page) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = `rail-item${page.id === activeId ? " active" : ""}`;
      btn.title = `${page.index}. ${page.name || ""}`;
      btn.setAttribute("aria-label", `Halaman ${page.index}`);
      if (page.thumb_url) {
        const img = document.createElement("img");
        img.src = page.thumb_url;
        img.loading = "lazy";
        img.alt = "";
        btn.appendChild(img);
      }
      const num = document.createElement("span");
      num.textContent = String(page.index).padStart(2, "0");
      btn.appendChild(num);
      btn.addEventListener("click", () => {
        if (page.status === "done") goToPage(page.id);
        else toast("Halaman ini belum selesai.", "error");
      });
      rail.appendChild(btn);
    });
    const active = rail.querySelector(".rail-item.active");
    if (active) active.scrollIntoView({ block: "nearest" });
  }

  function regionLabel(index, region) {
    return `${String(index + 1).padStart(2, "0")} · ${region.bubble ? "balon" : "teks"}${region.vertical ? " vertikal" : ""}`;
  }

  function renderRegions() {
    const list = $("regionList");
    const summary = $("regionSummary");
    list.innerHTML = "";
    const regions = state.regions || [];
    const active = regions.filter((r) => !r.skipped).length;
    summary.textContent = `${active} balon aktif${regions.length !== active ? ` · ${regions.length - active} dilewati` : ""}`;
    if (!regions.length) {
      list.innerHTML = `<div class="region-empty">Tidak ada dialog yang terdeteksi di halaman ini.</div>`;
      return;
    }
    regions.forEach((region, index) => {
      const card = document.createElement("div");
      card.className = "region-card";
      card.dataset.id = region.id;

      const head = document.createElement("div");
      head.className = "region-card-head";
      const label = document.createElement("span");
      label.innerHTML = `<span class="region-num">${index + 1}</span>${esc(regionLabel(index, region).split("· ")[1] || "")}`;
      const skip = document.createElement("label");
      skip.className = "region-skip";
      skip.title = "Jangan render dialog ini";
      const check = document.createElement("input");
      check.type = "checkbox";
      check.checked = Boolean(region.skipped);
      check.dataset.role = "skip";
      check.addEventListener("change", () => {
        region.skipped = check.checked;
        updateRegionSummary();
      });
      skip.appendChild(check);
      skip.appendChild(document.createTextNode("Lewati"));
      head.appendChild(label);
      head.appendChild(skip);
      card.appendChild(head);

      if (region.original) {
        const original = document.createElement("div");
        original.className = "region-original";
        original.textContent = region.original;
        card.appendChild(original);
      }

      const area = document.createElement("textarea");
      area.value = region.translated || "";
      area.rows = 2;
      area.dataset.role = "translated";
      area.setAttribute("aria-label", `Terjemahan dialog ${index + 1}`);
      area.addEventListener("input", () => {
        region.translated = area.value;
      });
      area.addEventListener("focus", () => {
        card.classList.add("highlight");
        setCompare(100);
      });
      area.addEventListener("blur", () => card.classList.remove("highlight"));
      card.appendChild(area);

      card.addEventListener("mouseenter", () => highlightPin(region.id, true));
      card.addEventListener("mouseleave", () => highlightPin(region.id, false));
      list.appendChild(card);
    });
  }

  function updateRegionSummary() {
    const regions = state.regions || [];
    const active = regions.filter((r) => !r.skipped).length;
    $("regionSummary").textContent = `${active} balon aktif${regions.length !== active ? ` · ${regions.length - active} dilewati` : ""}`;
    renderPins();
  }

  function renderPins() {
    const layer = $("regionLayer");
    const canvas = $("compareCanvas");
    layer.innerHTML = "";
    if (!state.page || !state.regions) return;
    const w = state.page.width || 1;
    const h = state.page.height || 1;
    state.regions.forEach((region, index) => {
      if (region.skipped) return;
      const box = region.region || region.box;
      if (!box || box.length !== 4) return;
      const pin = document.createElement("button");
      pin.type = "button";
      pin.className = "region-pin";
      pin.dataset.regionId = region.id;
      pin.textContent = String(index + 1);
      pin.title = region.translated || region.original || "";
      pin.style.left = `${clamp(((box[0] + box[2]) / 2 / w) * 100, 1, 99)}%`;
      pin.style.top = `${clamp(((box[1] + box[3]) / 2 / h) * 100, 1, 99)}%`;
      pin.addEventListener("click", () => {
        setCompare(100);
        const card = $("regionList").querySelector(`.region-card[data-id="${CSS.escape(region.id)}"]`);
        if (card) {
          card.classList.add("highlight");
          card.scrollIntoView({ behavior: "smooth", block: "center" });
          const area = card.querySelector("textarea");
          if (area) setTimeout(() => area.focus({ preventScroll: true }), 220);
        }
      });
      layer.appendChild(pin);
    });
    canvas.classList.add("show-pins");
  }

  function highlightPin(regionId, active) {
    const pin = $("regionLayer").querySelector(`.region-pin[data-region-id="${CSS.escape(regionId)}"]`);
    if (pin) pin.style.transform = active ? "translate(-50%, -50%) scale(1.25)" : "";
  }

  function initViewerStyle() {
    const meta = state.page && state.page.regions ? state.page.regions : null;
    const style = (meta && meta.style) || {};
    $("styleFont").value = ["comic", "sans", "serif", "cjk"].includes(style.font) ? style.font : "comic";
    const scale = Math.round(Number(style.font_scale || 1) * 100);
    $("styleScale").value = String(clamp(scale, 70, 140));
    $("styleScaleOut").textContent = `${$("styleScale").value}%`;
    $("styleColor").value = ["auto", "black", "white"].includes(style.text_color) ? style.text_color : "auto";
    $("styleOutline").checked = Boolean(style.outline);
    state.styleTouched = false;
  }

  function collectViewerStyle() {
    return {
      font: $("styleFont").value || "comic",
      font_scale: Number($("styleScale").value || 100) / 100,
      text_color: $("styleColor").value || "auto",
      outline: $("styleOutline").checked,
    };
  }

  async function rerenderPage() {
    if (state.rendering || !state.page || !state.jobId) return;
    state.rendering = true;
    const buttons = [$("rerenderBtn"), $("rerenderStyleBtn")].filter(Boolean);
    const labels = buttons.map((b) => b.textContent);
    buttons.forEach((b) => {
      b.disabled = true;
      b.classList.add("loading");
    });
    if ($("rerenderBtn")) $("rerenderBtn").textContent = "Merender…";
    try {
      const regions = state.regions.map((region) => ({
        id: region.id,
        translated: region.translated || "",
        skipped: Boolean(region.skipped),
        region: region.region,
      }));
      const page = await api(`/api/jobs/${state.jobId}/pages/${state.pageId}/render`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ regions, style: collectViewerStyle() }),
      });
      state.page = page;
      state.regions = (page.regions && page.regions.regions) ? page.regions.regions.slice() : state.regions;
      const jobPage = (state.job && state.job.pages || []).find((p) => p.id === page.id);
      if (jobPage) Object.assign(jobPage, page);
      setupViewer({ reset: false });
      toast("Halaman berhasil dirender ulang.");
    } catch (err) {
      toast(err.message || "Gagal render ulang.", "error", 6000);
    } finally {
      state.rendering = false;
      buttons.forEach((b, i) => {
        b.disabled = false;
        b.classList.remove("loading");
        b.textContent = labels[i];
      });
    }
  }

  function setCompare(value) {
    const pct = clamp(Number(value) || 0, 0, 100);
    state.compare = pct;
    $("compareRange").value = String(pct);
    $("compareCanvas").style.setProperty("--pos", `${pct}%`);
  }

  function setDemoCompare(value) {
    const pct = clamp(Number(value) || 0, 0, 100);
    $("demoCompare").style.setProperty("--pos", `${pct}%`);
    $("demoRange").value = String(pct);
  }

  function fitViewer(updateRange = true) {
    if (!state.page) return;
    const scroll = $("readerScroll");
    const available = Math.max(280, scroll.clientWidth - 48);
    const natural = state.page.width || $("originalImg").naturalWidth || 900;
    state.fitWidth = Math.min(available, natural);
    if (updateRange) state.zoom = 1;
    applyZoom();
  }

  function applyZoom() {
    const width = clamp(state.fitWidth * state.zoom, 280, state.fitWidth * 3.2);
    $("compareCanvas").style.setProperty("--zoom-width", `${Math.round(width)}px`);
    $("zoomLabel").textContent = `${Math.round(state.zoom * 100)}%`;
  }

  function zoomBy(factor) {
    state.zoom = clamp(state.zoom * factor, 0.6, 3);
    applyZoom();
  }

  function navigatePage(delta) {
    if (!state.job || !state.page) return;
    const pages = state.job.pages || [];
    const idx = pages.findIndex((p) => p.id === state.page.id);
    const next = pages[idx + delta];
    if (!next) return;
    if (next.status !== "done") {
      toast("Halaman berikutnya belum selesai.", "error");
      return;
    }
    goToPage(next.id);
  }

  /* ---------------------------------------------------------------- history */

  async function openHistory() {
    state.historyOpen = true;
    $("historyModal").classList.remove("hidden");
    document.body.classList.add("modal-open");
    await refreshHistory();
  }

  function closeHistory() {
    state.historyOpen = false;
    $("historyModal").classList.add("hidden");
    document.body.classList.remove("modal-open");
  }

  async function refreshHistory() {
    const list = $("historyList");
    list.innerHTML = `<div class="history-empty">Memuat riwayat…</div>`;
    try {
      const data = await api("/api/jobs");
      const rows = data.jobs || [];
      if (!rows.length) {
        list.innerHTML = `<div class="history-empty">Belum ada job. Terjemahkan halaman pertamamu.</div>`;
        return;
      }
      list.innerHTML = "";
      rows.forEach((job) => {
        const row = document.createElement("div");
        row.className = "history-row";
        row.tabIndex = 0;
        row.setAttribute("role", "button");
        const info = document.createElement("div");
        info.innerHTML = `<strong>${esc(job.title || "Manga tanpa judul")}</strong>
          <span>${esc(formatDate(job.created_at))} · ${job.page_count || 0} halaman</span>`;
        row.appendChild(info);
        const status = document.createElement("span");
        status.className = `history-status ${job.status === "done" ? "done" : ""}`;
        status.textContent = STATUS[job.status] || job.status;
        row.appendChild(status);
        if (job.status !== "running" && job.status !== "queued") {
          const del = document.createElement("button");
          del.type = "button";
          del.className = "icon-btn history-delete";
          del.title = "Hapus job";
          del.setAttribute("aria-label", "Hapus job");
          del.textContent = "×";
          del.addEventListener("click", async (event) => {
            event.stopPropagation();
            if (!window.confirm(`Hapus job “${job.title || "Manga"}”?`)) return;
            try {
              await api(`/api/jobs/${job.id}`, { method: "DELETE" });
              if (state.jobId === job.id) {
                state.job = null;
                state.jobId = null;
              }
              toast("Job dihapus.");
              refreshHistory();
            } catch (err) {
              toast(err.message || "Gagal menghapus job.", "error");
            }
          });
          row.appendChild(del);
        }
        const open = () => {
          closeHistory();
          goToJob(job.id);
        };
        row.addEventListener("click", open);
        row.addEventListener("keydown", (event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            open();
          }
        });
        list.appendChild(row);
      });
    } catch (err) {
      list.innerHTML = `<div class="history-empty">${esc(err.message || "Gagal memuat riwayat.")}</div>`;
    }
  }

  /* ----------------------------------------------------------------- events */

  function bindEvents() {
    $("uploadForm").addEventListener("submit", (event) => {
      event.preventDefault();
      submitJob();
    });

    $("dropzone").addEventListener("click", (event) => {
      if (event.target.closest("button") || event.target === $("fileInput")) return;
      $("fileInput").click();
    });
    $("dropzone").addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        $("fileInput").click();
      }
    });
    $("browseBtn").addEventListener("click", (event) => {
      event.stopPropagation();
      $("fileInput").click();
    });
    $("fileInput").addEventListener("change", (event) => addFiles(event.target.files));
    $("clearFiles").addEventListener("click", clearFiles);

    ["dragenter", "dragover"].forEach((type) => {
      $("dropzone").addEventListener(type, (event) => {
        event.preventDefault();
        $("dropzone").classList.add("dragover");
      });
    });
    ["dragleave", "dragend"].forEach((type) => {
      $("dropzone").addEventListener(type, (event) => {
        if (event.target === $("dropzone") || !$("dropzone").contains(event.relatedTarget)) {
          $("dropzone").classList.remove("dragover");
        }
      });
    });
    $("dropzone").addEventListener("drop", (event) => {
      event.preventDefault();
      $("dropzone").classList.remove("dragover");
      addFiles(event.dataTransfer.files);
    });
    window.addEventListener("dragover", (event) => {
      if (!event.target.closest || !event.target.closest("#dropzone")) event.preventDefault();
    });
    window.addEventListener("drop", (event) => {
      if (!event.target.closest || !event.target.closest("#dropzone")) event.preventDefault();
    });

    $("sampleBtn").addEventListener("click", () => useSample(true));
    $("heroSampleBtn").addEventListener("click", () => useSample(false));
    $("brandHome").addEventListener("click", (event) => {
      if (location.hash === "#/" || !location.hash) {
        event.preventDefault();
        window.scrollTo({ top: 0, behavior: "smooth" });
      }
    });

    $("fontScale").addEventListener("input", () => {
      $("scaleOut").textContent = `${$("fontScale").value}%`;
      savePrefs();
    });
    $("minScore").addEventListener("input", () => {
      $("scoreOut").textContent = (Number($("minScore").value) / 100).toFixed(2);
    });
    $("srcLang").addEventListener("change", () => { updateEngineInfo(); savePrefs(); });
    $("tgtLang").addEventListener("change", () => { updateEngineInfo(); savePrefs(); });
    $("engine").addEventListener("change", () => { updateEngineInfo(); savePrefs(); });
    $("tone").addEventListener("change", savePrefs);
    $("fallbackLocal").addEventListener("change", () => { updateEngineInfo(); savePrefs(); });
    $("fontStyle").addEventListener("change", savePrefs);
    $("textColor").addEventListener("change", savePrefs);
    $("outlineText").addEventListener("change", savePrefs);

    $("jobBack").addEventListener("click", () => goHome());
    $("cancelJob").addEventListener("click", async () => {
      if (!state.jobId) return;
      try {
        await api(`/api/jobs/${state.jobId}/cancel`, { method: "POST" });
        toast("Permintaan pembatalan dikirim.");
        if (state.job) renderJob(state.job);
      } catch (err) {
        toast(err.message || "Gagal membatalkan job.", "error");
      }
    });
    $("pageGrid").addEventListener("click", (event) => {
      const card = event.target.closest("[data-page-id]");
      if (!card || !state.job) return;
      const page = (state.job.pages || []).find((p) => p.id === card.dataset.pageId);
      if (!page) return;
      if (page.status === "done") goToPage(page.id);
      else if (page.status === "error") toast(page.error || "Halaman gagal diproses.", "error", 5000);
      else toast("Halaman masih diproses.");
    });

    $("historyBtn").addEventListener("click", openHistory);
    $("historyClose").addEventListener("click", closeHistory);
    $("historyModal").addEventListener("click", (event) => {
      if (event.target === $("historyModal")) closeHistory();
    });

    $("engineSettingsBtn").addEventListener("click", openSettings);
    $("settingsClose").addEventListener("click", closeSettings);
    $("settingsReload").addEventListener("click", openSettings);
    $("settingsSave").addEventListener("click", () => saveSettings(true));
    $("settingsModal").addEventListener("click", (event) => {
      if (event.target === $("settingsModal")) closeSettings();
    });
    $("settingsBody").addEventListener("click", (event) => {
      const testBtn = event.target.closest("[data-test-engine]");
      if (testBtn) {
        testSettingsEngine(testBtn.dataset.testEngine);
        return;
      }
      const clearBtn = event.target.closest("[data-clear-setting]");
      if (clearBtn) clearSetting(clearBtn.dataset.clearSetting);
    });

    $("viewerBack").addEventListener("click", () => goToJob(state.jobId));
    $("prevPage").addEventListener("click", () => navigatePage(-1));
    $("nextPage").addEventListener("click", () => navigatePage(1));
    $("compareRange").addEventListener("input", (event) => setCompare(event.target.value));
    $("zoomIn").addEventListener("click", () => zoomBy(1.15));
    $("zoomOut").addEventListener("click", () => zoomBy(1 / 1.15));
    $("resetZoom").addEventListener("click", () => fitViewer(true));
    $("rerenderBtn").addEventListener("click", rerenderPage);
    if ($("rerenderStyleBtn")) $("rerenderStyleBtn").addEventListener("click", rerenderPage);
    $("styleScale").addEventListener("input", () => {
      $("styleScaleOut").textContent = `${$("styleScale").value}%`;
      state.styleTouched = true;
    });
    ["styleFont", "styleColor", "styleOutline"].forEach((id) => {
      $(id).addEventListener("change", () => { state.styleTouched = true; });
    });
    $$(".editor-tab").forEach((tab) => {
      tab.addEventListener("click", () => {
        $$(".editor-tab").forEach((t) => t.classList.toggle("active", t === tab));
        $("tabText").classList.toggle("hidden", tab.dataset.tab !== "text");
        $("tabStyle").classList.toggle("hidden", tab.dataset.tab !== "style");
      });
    });

    $("demoRange").addEventListener("input", (event) => setDemoCompare(event.target.value));
    $("originalImg").addEventListener("load", () => {
      if (state.page) fitViewer(false);
    });
    $("originalImg").addEventListener("error", () => toast("Gambar asli gagal dimuat.", "error"));

    let resizeTimer = 0;
    window.addEventListener("resize", () => {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(() => {
        if (!$("viewer-view").classList.contains("hidden") && state.zoom === 1) fitViewer(false);
      }, 140);
    });

    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && state.settingsOpen) closeSettings();
      if (event.key === "Escape" && state.historyOpen) closeHistory();
      if ($("viewer-view").classList.contains("hidden")) return;
      const tag = (event.target.tagName || "").toLowerCase();
      if (["input", "textarea", "select", "button"].includes(tag) || event.target.isContentEditable) return;
      if (event.key === "ArrowLeft") {
        event.preventDefault();
        navigatePage(-1);
      } else if (event.key === "ArrowRight") {
        event.preventDefault();
        navigatePage(1);
      }
    });

    window.addEventListener("hashchange", onHashChange);
    window.addEventListener("beforeunload", () => {
      state.files.forEach((entry) => entry.url && URL.revokeObjectURL(entry.url));
    });
  }

  async function init() {
    bindEvents();
    setDemoCompare(52);
    await loadConfig();
    await handleRoute();
  }

  init();
})();
