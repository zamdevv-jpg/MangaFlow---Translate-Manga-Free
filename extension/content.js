/* ============================================================
   MangaFlow — content script
   Mendeteksi gambar manga terbesar di halaman, menerjemahkannya
   lewat background service worker, lalu mengganti gambar asli.
   ============================================================ */

(() => {
  "use strict";

  const DEFAULT_SETTINGS = {
    serverUrl: "http://127.0.0.1:8765",
    autoTranslate: false,
    targetLang: "id",
    sourceLang: "auto",
    engine: "local",
    tone: "santai",
  };
  const AUTO_PREFIX = "mf:auto:";
  const TERMINAL_ERROR = new Set(["error", "cancelled"]);
  const MAX_POLLS = 600;

  let host = null;
  let shadow = null;
  let panel = null;
  let badge = null;
  let currentImg = null;
  let currentJob = null;
  let uiState = "idle";
  let autoObserver = null;
  let observerTimer = null;
  let lastHref = location.href;
  let urlTimer = null;

  /* ------------------------------------------------------------- utilities */

  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  async function loadSettings() {
    try {
      const stored = await chrome.storage.sync.get("mf_settings");
      return { ...DEFAULT_SETTINGS, ...(stored.mf_settings || {}) };
    } catch {
      return { ...DEFAULT_SETTINGS };
    }
  }

  async function bg(message) {
    let response;
    try {
      response = await chrome.runtime.sendMessage(message);
    } catch {
      throw new Error(
        "Koneksi ke ekstensi terputus. Muat ulang halaman lalu coba lagi."
      );
    }
    if (!response || response.ok === false) {
      throw new Error(response?.error || "Gagal menghubungi MangaFlow.");
    }
    return response;
  }

  function report(state, data = {}) {
    try {
      const pending = chrome.runtime.sendMessage({
        type: "mf:pageProgress",
        state,
        ...data,
      });
      if (pending && typeof pending.catch === "function") pending.catch(() => {});
    } catch {
      /* popup mungkin sudah ditutup */
    }
  }

  function imageUrl(img) {
    return (
      img.currentSrc ||
      img.src ||
      img.dataset.src ||
      img.dataset.original ||
      img.dataset.lazySrc ||
      ""
    );
  }

  function looksLikeLogo(img) {
    const label = [
      img.id,
      img.className && typeof img.className === "string" ? img.className : "",
      img.alt,
      img.getAttribute("aria-label"),
    ]
      .join(" ")
      .toLowerCase();
    return /(^|[\s_-])(logo|avatar|icon|emoji|sprite|banner|thumbnail|thumb|badge)([\s_-]|$)/.test(
      label
    );
  }

  function candidateScore(img) {
    if (!(img instanceof HTMLImageElement)) return -1;
    if (img.dataset.mfIgnore === "1") return -1;
    if (!img.complete || !img.naturalWidth || !img.naturalHeight) return -1;
    if (img.naturalWidth < 280 || img.naturalHeight < 280) return -1;

    const rect = img.getBoundingClientRect();
    if (!rect.width || !rect.height) return -1;
    if (rect.width < 220 || rect.height < 220) return -1;
    if (img.hidden) return -1;

    const style = getComputedStyle(img);
    if (style.display === "none" || style.visibility === "hidden") return -1;

    const url = imageUrl(img);
    if (!/^(https?:|data:|blob:)/i.test(url)) return -1;

    const ratio = img.naturalHeight / img.naturalWidth;
    let score = Math.min(rect.width, 1400) * Math.min(rect.height, 2200);
    if (img.naturalHeight >= 700) score *= 1.25;
    if (ratio >= 1.15) score *= 1.8;
    if (ratio < 0.6) score *= 0.55;
    if (looksLikeLogo(img)) score *= 0.18;
    if (img.closest("header, footer, nav, aside")) score *= 0.45;
    if (img.dataset.mfTranslated === "1") score *= 0.8;
    return score;
  }

  function findCandidate({ portraitOnly = false } = {}) {
    let best = null;
    let bestScore = 0;
    for (const img of document.images) {
      const score = candidateScore(img);
      if (score <= bestScore) continue;
      if (portraitOnly) {
        if (img.naturalWidth < 480) continue;
        if (img.naturalHeight / img.naturalWidth < 1.12) continue;
      }
      best = img;
      bestScore = score;
    }
    return best;
  }

  function preload(url, timeout = 15000) {
    return new Promise((resolve, reject) => {
      const probe = new Image();
      const timer = setTimeout(() => {
        probe.onload = probe.onerror = null;
        reject(new Error("Gambar hasil terlalu lama dimuat."));
      }, timeout);
      probe.onload = () => {
        clearTimeout(timer);
        resolve();
      };
      probe.onerror = () => {
        clearTimeout(timer);
        reject(new Error("Gambar hasil tidak bisa ditampilkan."));
      };
      probe.src = url;
    });
  }

  function blobToDataUrl(blob) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result);
      reader.onerror = () => reject(new Error("Gambar tidak bisa dibaca."));
      reader.readAsDataURL(blob);
    });
  }

  /* -------------------------------------------------------------- page UI */

  const UI_CSS = `
    :host { all: initial; }
    * { box-sizing: border-box; }
    .root {
      position: fixed; right: 18px; bottom: 18px; z-index: 2147483647;
      display: flex; flex-direction: column; align-items: flex-end; gap: 10px;
      font-family: "Segoe UI", system-ui, -apple-system, sans-serif;
      color: #f4f1ff; -webkit-font-smoothing: antialiased;
    }
    .badge {
      width: 46px; height: 46px; border: 0; border-radius: 15px; cursor: pointer;
      background: linear-gradient(145deg, #7c5cff, #ff4d6d);
      box-shadow: 0 12px 30px rgba(19, 15, 45, .42), inset 0 1px 0 rgba(255,255,255,.28);
      color: #fff; font: 800 22px/46px "Segoe UI", system-ui, sans-serif;
      text-align: center; padding: 0; position: relative; transition: transform .18s ease;
    }
    .badge:hover { transform: translateY(-2px) scale(1.03); }
    .badge[hidden] { display: none; }
    .badge .dot {
      position: absolute; right: -2px; top: -2px; width: 12px; height: 12px;
      border-radius: 50%; background: #a78bfa; border: 2px solid #11121f;
    }
    .badge[data-state="working"] .dot { background: #fbbf24; animation: pulse 1.2s infinite; }
    .badge[data-state="done"] .dot { background: #34d399; }
    .badge[data-state="error"] .dot { background: #fb7185; }
    .panel {
      width: 302px; padding: 15px; border-radius: 18px;
      background: linear-gradient(165deg, #1b1c31 0%, #121321 100%);
      border: 1px solid rgba(255,255,255,.09);
      box-shadow: 0 22px 56px rgba(4, 5, 15, .5), inset 0 1px 0 rgba(255,255,255,.06);
    }
    .panel[hidden] { display: none; }
    .head { display: flex; align-items: center; gap: 9px; margin-bottom: 10px; }
    .mark {
      width: 30px; height: 30px; border-radius: 10px; flex: 0 0 auto;
      background: linear-gradient(145deg, #7c5cff, #ff4d6d);
      font: 800 15px/30px "Segoe UI", system-ui, sans-serif; text-align: center;
    }
    .head-copy { min-width: 0; flex: 1; }
    .eyebrow {
      font-size: 9px; letter-spacing: .14em; text-transform: uppercase;
      color: #9d99b8; font-weight: 700;
    }
    .title { font-size: 13px; font-weight: 700; line-height: 1.25; margin-top: 1px; }
    .close {
      border: 0; background: rgba(255,255,255,.07); color: #bcb8d8;
      width: 26px; height: 26px; border-radius: 9px; cursor: pointer; font-size: 15px;
      line-height: 1;
    }
    .close:hover { background: rgba(255,255,255,.13); color: #fff; }
    .spinner {
      width: 34px; height: 34px; border-radius: 50%; margin: 10px auto 12px;
      border: 3px solid rgba(167,139,250,.22); border-top-color: #a78bfa;
      animation: spin .85s linear infinite;
    }
    .state-icon {
      width: 38px; height: 38px; border-radius: 50%; margin: 8px auto 11px;
      display: grid; place-items: center; font-size: 19px; font-weight: 800;
    }
    .state-icon.ok { background: rgba(52,211,153,.14); color: #34d399; }
    .state-icon.err { background: rgba(251,113,133,.14); color: #fb7185; }
    .message { font-size: 12.5px; line-height: 1.5; color: #c9c5e2; text-align: center; margin: 0 0 10px; }
    .stage { font-size: 11px; color: #8d89a8; text-align: center; margin: -3px 0 10px; min-height: 15px; }
    .bar { height: 5px; border-radius: 99px; background: rgba(255,255,255,.08); overflow: hidden; margin: 4px 0 12px; }
    .bar > i {
      display: block; height: 100%; width: 0; border-radius: inherit;
      background: linear-gradient(90deg, #7c5cff, #c084fc, #ff4d6d);
      transition: width .35s ease;
    }
    .actions { display: flex; gap: 7px; }
    .btn {
      flex: 1; min-height: 32px; border-radius: 9px; border: 1px solid transparent;
      font: 700 11.5px/1 "Segoe UI", system-ui, sans-serif; cursor: pointer;
      padding: 8px 9px;
    }
    .btn.primary {
      background: linear-gradient(135deg, #7c5cff, #a855f7);
      color: #fff; box-shadow: 0 7px 18px rgba(124,92,255,.28);
    }
    .btn.ghost { background: rgba(255,255,255,.06); color: #d8d4ee; border-color: rgba(255,255,255,.09); }
    .btn:hover { filter: brightness(1.08); }
    .foot { margin-top: 9px; font-size: 9.5px; color: #76728f; text-align: center; }
    @keyframes spin { to { transform: rotate(360deg); } }
    @keyframes pulse { 0%,100% { opacity: 1; transform: scale(1); } 50% { opacity: .5; transform: scale(.8); } }
  `;

  function ensureUI() {
    if (host?.isConnected) return;
    host = document.createElement("div");
    host.id = "mangaflow-extension-host";
    host.dataset.mfIgnore = "1";
    document.documentElement.appendChild(host);
    shadow = host.attachShadow({ mode: "open" });
    shadow.innerHTML = `
      <style>${UI_CSS}</style>
      <div class="root">
        <div class="panel" id="panel" hidden>
          <div class="head">
            <div class="mark">M</div>
            <div class="head-copy">
              <div class="eyebrow">MangaFlow</div>
              <div class="title" id="title">Menerjemahkan halaman</div>
            </div>
            <button class="close" id="close" type="button" title="Tutup">×</button>
          </div>
          <div class="spinner" id="spinner"></div>
          <div class="state-icon ok" id="okIcon" hidden>✓</div>
          <div class="state-icon err" id="errIcon" hidden>!</div>
          <p class="message" id="message">Menyiapkan gambar…</p>
          <p class="stage" id="stage"></p>
          <div class="bar" id="barWrap"><i id="bar"></i></div>
          <div class="actions" id="actions"></div>
          <div class="foot">Diproses lokal oleh server MangaFlow</div>
        </div>
        <button class="badge" id="badge" type="button" hidden title="MangaFlow">
          M<span class="dot"></span>
        </button>
      </div>
    `;
    panel = shadow.getElementById("panel");
    badge = shadow.getElementById("badge");
    badge.addEventListener("click", () => {
      panel.hidden = !panel.hidden;
    });
    shadow.getElementById("close").addEventListener("click", () => {
      panel.hidden = true;
    });
  }

  function setActions(buttons) {
    ensureUI();
    const wrap = shadow.getElementById("actions");
    wrap.replaceChildren();
    for (const item of buttons) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = `btn ${item.kind || "ghost"}`;
      btn.textContent = item.label;
      btn.addEventListener("click", item.onClick);
      wrap.appendChild(btn);
    }
  }

  function showWorking(progress, stage) {
    ensureUI();
    uiState = "working";
    panel.hidden = false;
    badge.hidden = false;
    badge.dataset.state = "working";
    shadow.getElementById("title").textContent = "Menerjemahkan halaman";
    shadow.getElementById("spinner").hidden = false;
    shadow.getElementById("okIcon").hidden = true;
    shadow.getElementById("errIcon").hidden = true;
    shadow.getElementById("message").textContent =
      "OCR, terjemahan, dan redraw berjalan di server lokal.";
    shadow.getElementById("stage").textContent = stage || "";
    shadow.getElementById("barWrap").hidden = false;
    shadow.getElementById("bar").style.width = `${Math.max(3, Math.min(96, progress || 0))}%`;
    setActions([]);
  }

  function showSuccess(job, auto = false) {
    ensureUI();
    uiState = "done";
    panel.hidden = false;
    badge.hidden = false;
    badge.dataset.state = "done";
    shadow.getElementById("title").textContent = "Halaman diterjemahkan";
    shadow.getElementById("spinner").hidden = true;
    shadow.getElementById("okIcon").hidden = false;
    shadow.getElementById("errIcon").hidden = true;
    shadow.getElementById("message").textContent = auto
      ? "Gambar manga otomatis diterjemahkan. Periksa hasilnya sebelum membaca."
      : "Gambar di halaman sudah diganti dengan versi terjemahan.";
    shadow.getElementById("stage").textContent = "";
    shadow.getElementById("barWrap").hidden = true;
    setActions([
      {
        label: "Buka editor",
        kind: "primary",
        onClick: () => {
          if (job?.editorUrl) openExternal(job.editorUrl);
        },
      },
      {
        label: "Pulihkan asli",
        onClick: () => {
          if (currentImg) restoreImage(currentImg);
          hidePanel();
          showBadgeNotice("Gambar asli dipulihkan.", "done");
        },
      },
    ]);
  }

  function showError(message) {
    ensureUI();
    uiState = "error";
    panel.hidden = false;
    badge.hidden = false;
    badge.dataset.state = "error";
    shadow.getElementById("title").textContent = "Terjemahan gagal";
    shadow.getElementById("spinner").hidden = true;
    shadow.getElementById("okIcon").hidden = true;
    shadow.getElementById("errIcon").hidden = false;
    shadow.getElementById("message").textContent = message || "Terjadi kesalahan.";
    shadow.getElementById("stage").textContent = "";
    shadow.getElementById("barWrap").hidden = true;
    setActions([
      {
        label: "Coba lagi",
        kind: "primary",
        onClick: () => {
          if (currentImg) {
            delete currentImg.dataset.mfTranslated;
            translateImage(currentImg, { auto: true }).catch(() => {});
          }
        },
      },
      { label: "Tutup", onClick: () => hidePanel() },
    ]);
  }

  function showBadgeNotice(message, state = "done") {
    ensureUI();
    badge.hidden = false;
    badge.dataset.state = state;
    panel.hidden = false;
    shadow.getElementById("title").textContent =
      state === "error" ? "Terjemahan gagal" : "MangaFlow";
    shadow.getElementById("spinner").hidden = true;
    shadow.getElementById("okIcon").hidden = state === "error";
    shadow.getElementById("errIcon").hidden = state !== "error";
    shadow.getElementById("message").textContent = message;
    shadow.getElementById("stage").textContent = "";
    shadow.getElementById("barWrap").hidden = true;
    setActions([{ label: "Tutup", onClick: () => hidePanel() }]);
  }

  function hidePanel() {
    if (panel) panel.hidden = true;
    if (uiState === "error" || uiState === "idle") {
      if (badge) badge.hidden = true;
    }
  }

  function openExternal(url) {
    if (!/^https?:\/\//i.test(url)) return;
    bg({ type: "mf:openUrl", payload: { url } }).catch(() => {
      window.open(url, "_blank", "noopener");
    });
  }

  /* -------------------------------------------------------- image replace */

  async function applyTranslatedImage(img, job) {
    const original = imageUrl(img);
    if (!img.dataset.mfOriginal) img.dataset.mfOriginal = original;
    if (img.dataset.mfSrcset === undefined && img.getAttribute("srcset")) {
      img.dataset.mfSrcset = img.getAttribute("srcset");
    }
    img.removeAttribute("srcset");

    const picture = img.closest("picture");
    if (picture) {
      for (const source of picture.querySelectorAll("source")) {
        if (source.dataset.mfSrcset === undefined) {
          source.dataset.mfSrcset = source.getAttribute("srcset") || "";
        }
        source.removeAttribute("srcset");
      }
    }

    let url = job.translatedUrl;
    try {
      await preload(url);
    } catch {
      // Sebagian situs memblokir gambar dari localhost lewat CSP img-src.
      // Data URL sering masih lolos; kalau tidak, tampilkan pesan jelas.
      const fallback = await bg({
        type: "mf:fetchDataUrl",
        payload: { url: job.translatedUrl },
      });
      try {
        await preload(fallback.dataUrl);
      } catch {
        throw new Error(
          "Situs ini memblokir gambar hasil (CSP). Buka hasilnya di tab MangaFlow."
        );
      }
      url = fallback.dataUrl;
    }

    img.src = url;
    img.dataset.mfTranslated = "1";
    img.dataset.mfJob = job.id;
    img.dataset.mfPage = job.pageId || "";
    img.dataset.mfEditor = job.editorUrl || "";
    img.dataset.mfDownload = job.downloadUrl || "";
    currentImg = img;
    currentJob = job;
  }

  function restoreImage(img) {
    const original = img.dataset.mfOriginal;
    if (!original) return;
    img.src = original;
    if (img.dataset.mfSrcset !== undefined) {
      if (img.dataset.mfSrcset) img.setAttribute("srcset", img.dataset.mfSrcset);
      else img.removeAttribute("srcset");
    }
    const picture = img.closest("picture");
    if (picture) {
      for (const source of picture.querySelectorAll("source")) {
        if (source.dataset.mfSrcset !== undefined) {
          if (source.dataset.mfSrcset) source.setAttribute("srcset", source.dataset.mfSrcset);
          else source.removeAttribute("srcset");
        }
      }
    }
    delete img.dataset.mfTranslated;
    delete img.dataset.mfJob;
    delete img.dataset.mfPage;
    delete img.dataset.mfEditor;
    delete img.dataset.mfDownload;
    if (badge) badge.dataset.state = "idle";
  }

  /* ------------------------------------------------------------- job flow */

  async function beginJob(img) {
    const settings = await loadSettings();
    const src = imageUrl(img);
    if (!src) throw new Error("Gambar manga tidak ditemukan.");

    const payload = {
      title: document.title || "Halaman manga",
      pageUrl: location.href,
      settings,
      server: settings.serverUrl,
    };

    if (/^(blob:|data:)/i.test(src)) {
      const blob = await (await fetch(src)).blob();
      payload.dataUrl = await blobToDataUrl(blob);
      payload.filename = "halaman.png";
    } else if (/^https?:/i.test(src)) {
      payload.srcUrl = src;
    } else {
      throw new Error("Format gambar halaman tidak didukung.");
    }

    const started = await bg({ type: "mf:startJob", payload });
    currentJob = {
      id: started.jobId,
      pageId: started.pageId,
      server: started.server,
    };
    return started;
  }

  async function waitForJob(started, onProgress) {
    let lastStage = "";
    for (let i = 0; i < MAX_POLLS; i += 1) {
      await sleep(i === 0 ? 900 : 1500);
      const response = await bg({
        type: "mf:jobStatus",
        payload: {
          jobId: started.jobId,
          pageId: started.pageId,
          server: started.server,
        },
      });
      const job = response.job;
      if (job.stage && job.stage !== lastStage) lastStage = job.stage;
      const progress = Number(job.progress) || 0;
      if (onProgress) onProgress(Math.max(8, Math.min(96, progress)), lastStage);

      if (job.status === "done" && job.translatedUrl) return job;
      if (TERMINAL_ERROR.has(job.status)) {
        throw new Error(job.error || "Server menghentikan job terjemahan.");
      }
      if (job.status === "done" && !job.translatedUrl) {
        throw new Error("Halaman selesai tetapi gambar hasil tidak tersedia.");
      }
    }
    throw new Error("Terjemahan terlalu lama. Periksa server MangaFlow.");
  }

  async function translateImage(img, { auto = false } = {}) {
    if (!img || img.dataset.mfBusy === "1") return null;
    if (img.dataset.mfTranslated === "1") {
      currentImg = img;
      showSuccess(
        {
          editorUrl: img.dataset.mfEditor,
          id: img.dataset.mfJob,
          pageId: img.dataset.mfPage,
          downloadUrl: img.dataset.mfDownload,
        },
        auto
      );
      return { alreadyTranslated: true };
    }

    img.dataset.mfBusy = "1";
    currentImg = img;
    showWorking(3, "Menyiapkan gambar…");
    report("working", { progress: 3, detail: "Menyiapkan gambar" });

    try {
      const started = await beginJob(img);
      showWorking(8, "Mengunggah ke server lokal…");
      report("working", { progress: 8, detail: "Mengunggah ke server lokal" });

      const job = await waitForJob(started, (progress, stage) => {
        showWorking(progress, stage || "OCR & terjemahan…");
        report("working", { progress, detail: stage || "OCR & terjemahan" });
      });

      await applyTranslatedImage(img, { ...job, pageId: job.pageId || started.pageId });
      showSuccess(job, auto);
      report("done", {
        progress: 100,
        jobId: job.id,
        pageId: job.pageId,
        title: job.title,
        tgt: job.options?.tgt_lang || "",
        server: job.server,
        translatedUrl: job.translatedUrl,
        thumbUrl: job.thumbUrl,
        originalUrl: job.originalUrl,
        editorUrl: job.editorUrl,
        downloadUrl: job.downloadUrl,
      });

      bg({
        type: "mf:saveLastJob",
        payload: {
          job: {
            jobId: job.id,
            pageId: job.pageId,
            title: job.title,
            status: "done",
            progress: 100,
            tgt: job.options?.tgt_lang || "",
            server: job.server,
            translatedUrl: job.translatedUrl,
            thumbUrl: job.thumbUrl,
            originalUrl: job.originalUrl,
            editorUrl: job.editorUrl,
            downloadUrl: job.downloadUrl,
            createdAt: Date.now(),
          },
        },
      }).catch(() => {});
      return job;
    } catch (error) {
      const message = error?.message || "Terjemahan gagal.";
      showError(message);
      report("error", { error: message });
      throw error;
    } finally {
      delete img.dataset.mfBusy;
    }
  }

  /* --------------------------------------------------------------- auto */

  function stopAutoObserver() {
    if (autoObserver) {
      autoObserver.disconnect();
      autoObserver = null;
    }
    if (observerTimer) {
      clearTimeout(observerTimer);
      observerTimer = null;
    }
  }

  function watchForImage() {
    if (autoObserver) return;
    autoObserver = new MutationObserver(() => {
      const img = findCandidate({ portraitOnly: true });
      if (img) {
        stopAutoObserver();
        const key = `${AUTO_PREFIX}${location.href}`;
        if (sessionStorage.getItem(key)) return;
        sessionStorage.setItem(key, "1");
        translateImage(img, { auto: true }).catch(() => {});
      }
    });
    autoObserver.observe(document.documentElement, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ["src", "srcset", "style", "class"],
    });
    observerTimer = setTimeout(stopAutoObserver, 30000);
  }

  async function maybeAutoTranslate() {
    const settings = await loadSettings();
    if (!settings.autoTranslate) return;
    try {
      const server = new URL(settings.serverUrl);
      if (server.origin === location.origin) return;
    } catch {
      /* URL server diatur di popup */
    }
    const key = `${AUTO_PREFIX}${location.href}`;
    if (sessionStorage.getItem(key)) return;
    const img = findCandidate({ portraitOnly: true });
    if (!img) {
      watchForImage();
      return;
    }
    sessionStorage.setItem(key, "1");
    translateImage(img, { auto: true }).catch(() => {});
  }

  function watchUrlChanges() {
    if (urlTimer) clearInterval(urlTimer);
    urlTimer = setInterval(() => {
      if (location.href === lastHref) return;
      lastHref = location.href;
      stopAutoObserver();
      setTimeout(() => {
        maybeAutoTranslate().catch(() => {});
      }, 1400);
    }, 1600);
  }

  /* ------------------------------------------------------------- messages */

  chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    const type = message?.type;
    if (type === "mf:detect") {
      const img = findCandidate();
      sendResponse({
        ok: true,
        pageTitle: document.title,
        candidate: img
          ? {
              width: img.naturalWidth,
              height: img.naturalHeight,
              alt: (img.alt || "").slice(0, 120),
              translated: img.dataset.mfTranslated === "1",
            }
          : null,
      });
      return false;
    }

    if (type === "mf:translate") {
      const img = findCandidate();
      if (!img) {
        sendResponse({
          ok: false,
          error:
            "Tidak ada gambar manga besar di halaman ini. Buka halaman manga lalu coba lagi, atau pakai Screenshot.",
        });
        return false;
      }
      if (img.dataset.mfTranslated === "1") {
        currentImg = img;
        showSuccess(
          {
            editorUrl: img.dataset.mfEditor,
            id: img.dataset.mfJob,
            pageId: img.dataset.mfPage,
            downloadUrl: img.dataset.mfDownload,
          },
          false
        );
        sendResponse({ ok: true, alreadyTranslated: true });
        return false;
      }
      translateImage(img, { auto: false }).catch(() => {});
      sendResponse({ ok: true, started: true });
      return false;
    }

    if (type === "mf:restore") {
      if (currentImg) restoreImage(currentImg);
      hidePanel();
      sendResponse({ ok: true });
      return false;
    }

    return false;
  });

  /* ---------------------------------------------------------------- start */

  async function init() {
    const settings = await loadSettings();
    try {
      const server = new URL(settings.serverUrl);
      if (server.origin === location.origin) return; // jangan inject di UI MangaFlow
    } catch {
      /* biarkan */
    }
    if (/^(chrome|edge|about|devtools|view-source):/i.test(location.href)) return;

    watchUrlChanges();
    setTimeout(() => maybeAutoTranslate().catch(() => {}), 1700);

    chrome.storage.onChanged.addListener((changes, area) => {
      if (area !== "sync" || !changes.mf_settings) return;
      const next = changes.mf_settings.newValue || {};
      if (next.autoTranslate) setTimeout(() => maybeAutoTranslate().catch(() => {}), 400);
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init, { once: true });
  } else {
    init();
  }
})();
