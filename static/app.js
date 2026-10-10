(() => {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

  /* ---------- Nastavení ---------- */
  const DEFAULTS = {
    mode: "auto", quality: "best", container: "mp4",
    audioFormat: "mp3", audioBitrate: "192",
    embedCover: false, autoSave: true, instant: false,
    cookiesFrom: "", theme: "system", accent: "amber",
  };

  function loadSettings() {
    try {
      return { ...DEFAULTS, ...JSON.parse(localStorage.getItem("lumen.settings") || "{}") };
    } catch {
      return { ...DEFAULTS };
    }
  }
  let S = loadSettings();

  const darkQuery = matchMedia("(prefers-color-scheme: dark)");
  function applyTheme() {
    const theme = S.theme === "system" ? (darkQuery.matches ? "dark" : "light") : S.theme;
    document.documentElement.dataset.theme = theme;
    document.documentElement.dataset.accent = S.accent;
  }
  function saveSettings() {
    try { localStorage.setItem("lumen.settings", JSON.stringify(S)); } catch { /* soukromé okno */ }
    applyTheme();
  }
  darkQuery.addEventListener("change", applyTheme);

  /* ---------- Pomocné funkce ---------- */
  async function api(path, { method = "GET", body } = {}) {
    const init = { method };
    if (body !== undefined) {
      init.headers = { "Content-Type": "application/json" };
      init.body = JSON.stringify(body);
    }
    const res = await fetch(path, init);
    if (!res.ok) {
      let msg = res.statusText || "Chyba serveru";
      try {
        const data = await res.json();
        if (data.detail) msg = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
      } catch { /* odpověď nebyla JSON */ }
      throw new Error(msg);
    }
    return res.json();
  }

  const fmtBytes = (n) => {
    if (n == null) return "";
    const units = ["B", "kB", "MB", "GB"];
    let i = 0;
    while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
    return n.toFixed(i ? 1 : 0).replace(".", ",") + " " + units[i];
  };
  const fmtTime = (s) => {
    if (s == null) return "";
    s = Math.round(s);
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
    return (h ? h + ":" + String(m).padStart(2, "0") : m) + ":" + String(sec).padStart(2, "0");
  };

  /* ---------- Přehrávač ---------- */
  const AUDIO_EXT = /\.(mp3|m4a|opus|flac|wav|ogg|aac)$/i;
  function openPlayer(id, filename) {
    const dlg = $("#player");
    const box = $("#playerBox");
    box.innerHTML = "";
    const media = document.createElement(AUDIO_EXT.test(filename) ? "audio" : "video");
    media.controls = true;
    media.autoplay = true;
    media.playsInline = true;
    media.src = "/api/jobs/" + id + "/stream";
    media.addEventListener("error", () => toast("Tenhle formát prohlížeč nepřehraje, použij Uložit.", true));
    box.append(media);
    $("#playerTitle").textContent = filename;
    dlg.showModal();
  }
  function closePlayer() {
    const dlg = $("#player");
    if (dlg.open) dlg.close();
    $("#playerBox").innerHTML = "";
  }
  document.addEventListener("click", (e) => {
    if (e.target.closest && e.target.closest("#playerClose")) closePlayer();
    else if (e.target.id === "player") closePlayer();
  });
  document.addEventListener("close", (e) => { if (e.target.id === "player") $("#playerBox").innerHTML = ""; }, true);

  let toastTimer;
  function toast(message, isError = false) {
    const el = $("#toast");
    el.textContent = message;
    el.classList.toggle("error", isError);
    el.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove("show"), isError ? 6500 : 2800);
  }

  /* ---------- Navigace ---------- */
  const ROUTES = ["download", "remux", "sites", "settings"];
  function route() {
    const name = location.hash.replace(/^#\/?/, "") || "download";
    const current = ROUTES.includes(name) ? name : "download";
    $$(".view").forEach((v) => v.classList.toggle("active", v.dataset.view === current));
    $$(".rail a[data-route]").forEach((a) => {
      const on = a.dataset.route === current;
      a.classList.toggle("active", on);
      if (on) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
    });
    if (current === "sites") loadSites();
  }
  addEventListener("hashchange", route);

  /* ---------- Stahování ---------- */
  const input = $("#urlInput");
  const optA = $("#optA");
  const optB = $("#optB");
  let current = null; // { url, info }

  const HINTS = {
    auto: "Nejlepší dostupná kvalita. U webů jen se zvukem se uloží zvuk.",
    video: "Video se zvukem. Kvalitu a formát vybereš po načtení odkazu.",
    audio: "Jen zvuk ve zvoleném formátu.",
  };
  function updateHint() { $("#modeHint").textContent = HINTS[S.mode] || ""; }
  function updateSubmitLabel() { $("#submitBtn").textContent = S.instant ? "Stáhnout" : "Načíst"; }

  function fillSelect(sel, items, value) {
    sel.innerHTML = "";
    for (const [v, label] of items) {
      const o = document.createElement("option");
      o.value = v;
      o.textContent = label;
      sel.append(o);
    }
    sel.value = items.some(([v]) => v === value) ? value : items[0][0];
  }

  function updateOptions() {
    if (S.mode === "audio") {
      $("#labA").textContent = "Bitrate";
      fillSelect(optA, [["128", "128 kb/s"], ["192", "192 kb/s"], ["256", "256 kb/s"], ["320", "320 kb/s"]], S.audioBitrate);
      fillSelect(optB, ["mp3", "m4a", "opus", "flac", "wav"].map((x) => [x, x.toUpperCase()]), S.audioFormat);
      optA.disabled = ["flac", "wav"].includes(optB.value);
    } else {
      $("#labA").textContent = "Kvalita";
      const heights = current?.info?.heights?.length ? current.info.heights : [2160, 1440, 1080, 720, 480, 360];
      fillSelect(optA, [["best", "Nejlepší dostupná"], ...heights.map((h) => [String(h), h + "p"])], S.quality);
      fillSelect(optB, [["mp4", "MP4"], ["mkv", "MKV"], ["webm", "WebM"]], S.container);
      optA.disabled = false;
    }
  }

  optA.addEventListener("change", () => {
    if (S.mode === "audio") S.audioBitrate = optA.value; else S.quality = optA.value;
    saveSettings();
  });
  optB.addEventListener("change", () => {
    if (S.mode === "audio") {
      S.audioFormat = optB.value;
      optA.disabled = ["flac", "wav"].includes(optB.value);
    } else {
      S.container = optB.value;
    }
    saveSettings();
  });

  function setGlow(url) {
    const glow = $("#glow"), img = $("#glowImg");
    if (!url) { glow.classList.remove("on"); return; }
    img.onload = () => glow.classList.add("on");
    img.onerror = () => glow.classList.remove("on");
    img.src = url;
  }

  function youtubeId(url) {
    try {
      const u = new URL(url);
      const h = u.hostname.replace(/^www\.|^m\./, "");
      let id = null;
      if (h === "youtu.be") id = u.pathname.slice(1).split("/")[0];
      else if (h === "youtube.com" || h === "music.youtube.com") {
        if (u.pathname === "/watch") id = u.searchParams.get("v");
        else {
          const m = u.pathname.match(/^\/(shorts|embed|live)\/([^/?]+)/);
          if (m) id = m[2];
        }
      }
      return id && /^[\w-]{6,20}$/.test(id) ? id : null;
    } catch { return null; }
  }

  function resetMedia() {
    $$("#pvMedia .pv-frame").forEach((el) => el.remove());
    const btn = $("#pvPlay");
    btn.disabled = false;
    $("span", btn).textContent = "Přehrát náhled";
    $("#pvThumb").hidden = !(current && current.info && current.info.thumbnail);
  }

  function youtubeFallback(frameParent, id) {
    const frame = document.createElement("iframe");
    frame.className = "pv-frame";
    frame.src = "https://www.youtube-nocookie.com/embed/" + id + "?autoplay=1&rel=0";
    frame.allow = "autoplay; encrypted-media; picture-in-picture; fullscreen";
    frame.allowFullscreen = true;
    frame.referrerPolicy = "strict-origin-when-cross-origin";
    frame.title = "Náhled videa";
    frameParent.append(frame);
  }

  $("#pvPlay").addEventListener("click", async () => {
    if (!current) return;
    const url = current.url;
    const btn = $("#pvPlay");
    btn.disabled = true;
    $("span", btn).textContent = "Načítám náhled…";
    const video = document.createElement("video");
    video.className = "pv-frame";
    video.controls = true;
    video.autoplay = true;
    video.playsInline = true;
    video.addEventListener("playing", () => { btn.hidden = true; $("#pvThumb").hidden = true; }, { once: true });
    video.addEventListener("error", async () => {
      video.remove();
      let msg = "Náhled se nepodařilo načíst.";
      try {
        const r = await fetch(video.dataset.src, { headers: { Range: "bytes=0-0" } });
        if (!r.ok) { const d = await r.json(); if (d.detail) msg = d.detail; }
      } catch { /* necháme obecnou hlášku */ }
      toast(msg, true);
      const id = youtubeId(url);
      if (id) {
        btn.hidden = true;
        $("#pvThumb").hidden = true;
        youtubeFallback($("#pvMedia"), id);
      } else {
        btn.disabled = false;
        $("span", btn).textContent = "Přehrát náhled";
      }
    }, { once: true });
    video.dataset.src = "/api/preview?url=" + encodeURIComponent(url);
    video.src = video.dataset.src;
    $("#pvMedia").append(video);
  });

  function showPreview(url, info) {
    current = { url, info };
    resetMedia();
    $("#pvPlay").hidden = false;
    $("#pvThumb").src = info.thumbnail || "";
    $("#pvThumb").hidden = !info.thumbnail;
    $("#pvTitle").textContent = info.title || url;
    const meta = [info.uploader, info.duration ? fmtTime(info.duration) : null].filter(Boolean).join(", ");
    $("#pvMeta").textContent = meta;
    $("#preview").hidden = false;
    updateOptions();
    setGlow(info.thumbnail);
  }

  function hidePreview() {
    resetMedia();
    $("#pvPlay").hidden = true;
    current = null;
    $("#preview").hidden = true;
    setGlow(null);
  }

  function setBusy(busy) {
    const btn = $("#submitBtn");
    btn.disabled = busy;
    if (busy) btn.textContent = "Načítám…"; else updateSubmitLabel();
  }

  async function startDownload({ url, title = null, thumbnail = null }) {
    const res = await api("/api/download", {
      method: "POST",
      body: {
        url, title, thumbnail,
        mode: S.mode,
        quality: S.quality,
        container: S.container,
        audio_format: S.audioFormat,
        audio_bitrate: S.audioBitrate,
        embed_cover: S.embedCover,
        cookies_from: S.cookiesFrom || null,
      },
    });
    seenActive.add(res.id);
    toast("Přidáno do fronty");
    loadQuota();
    input.value = "";
    hidePreview();
    poll();
  }

  $("#urlForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const url = input.value.trim();
    if (!url) { toast("Nejdřív vlož odkaz.", true); input.focus(); return; }
    setBusy(true);
    try {
      if (S.instant) {
        await startDownload({ url });
      } else {
        const info = await api("/api/info", { method: "POST", body: { url, cookies_from: S.cookiesFrom || null } });
        showPreview(url, info);
      }
    } catch (err) {
      toast(err.message, true);
    } finally {
      setBusy(false);
    }
  });

  $("#dlBtn").addEventListener("click", async () => {
    if (!current) return;
    const btn = $("#dlBtn");
    btn.disabled = true;
    try {
      await startDownload({ url: current.url, title: current.info.title, thumbnail: current.info.thumbnail });
    } catch (err) {
      toast(err.message, true);
    } finally {
      btn.disabled = false;
    }
  });

  $("#pasteBtn").addEventListener("click", async () => {
    try {
      const text = await navigator.clipboard.readText();
      input.value = text.trim();
      input.focus();
    } catch {
      toast("Prohlížeč nepovolil čtení schránky. Vlož odkaz klávesami Ctrl+V.", true);
      input.focus();
    }
  });

  input.addEventListener("input", () => {
    if (current && input.value.trim() !== current.url) hidePreview();
  });

  /* ---------- Fronta ---------- */
  let jobs = [];
  const seenActive = new Set(); // úlohy, které tahle relace viděla běžet
  const saved = new Set();
  let pollTimer;
  const isActive = (j) => ["queued", "downloading", "processing"].includes(j.status);

  function statusText(j) {
    switch (j.status) {
      case "queued": return "Čeká ve frontě";
      case "downloading": {
        const parts = [`Stahuje se, ${Math.round(j.percent)} %`];
        if (j.speed) parts.push(fmtBytes(j.speed) + "/s");
        if (j.eta != null) parts.push("zbývá " + fmtTime(j.eta));
        return parts.join(", ");
      }
      case "processing": return j.stage || "Zpracovává se";
      case "done": return "Hotovo, " + fmtBytes(j.size);
      case "cancelled": return "Zrušeno";
      case "error": return j.error || "Chyba";
      default: return "";
    }
  }

  function createJobEl(j) {
    const el = document.createElement("li");
    el.className = "job";
    el.dataset.id = j.id;
    el.innerHTML =
      '<div class="job-thumb"></div>' +
      '<div class="job-main">' +
        '<p class="job-title"></p><p class="job-status"></p>' +
        '<div class="bar" role="progressbar" aria-valuemin="0" aria-valuemax="100"><i></i></div>' +
        '<div class="job-actions"><button type="button" class="link play" hidden>Přehrát</button><a class="link save" download hidden>Uložit</a><button type="button" class="link act"></button></div>' +
      "</div>";
    $(".play", el).addEventListener("click", () => openPlayer(el.dataset.id, el.dataset.filename || ""));
    $(".act", el).addEventListener("click", async () => {
      try { await api("/api/jobs/" + el.dataset.id, { method: "DELETE" }); } catch (err) { toast(err.message, true); }
      poll();
    });
    return el;
  }

  function updateJobEl(el, j) {
    el.classList.remove("queued", "downloading", "processing", "done", "error", "cancelled");
    el.classList.add(j.status);

    const thumb = $(".job-thumb", el);
    if (j.thumbnail && thumb.dataset.src !== j.thumbnail) {
      thumb.dataset.src = j.thumbnail;
      thumb.innerHTML = "";
      const img = document.createElement("img");
      img.alt = "";
      img.referrerPolicy = "no-referrer";
      img.src = j.thumbnail;
      img.onerror = () => img.remove();
      thumb.append(img);
    } else if (!j.thumbnail && !thumb.firstChild) {
      thumb.innerHTML = '<svg class="ico"><use href="#i-' + (j.kind === "remux" ? "convert" : "music") + '"/></svg>';
    }

    $(".job-title", el).textContent = j.filename || j.title;
    $(".job-status", el).textContent = statusText(j);
    const pct = j.status === "done" ? 100 : j.percent || 0;
    const bar = $(".bar", el);
    bar.setAttribute("aria-valuenow", String(Math.round(pct)));
    $("i", bar).style.width = pct + "%";

    const play = $(".play", el);
    play.hidden = j.status !== "done";
    el.dataset.filename = j.filename || "";
    const save = $(".save", el);
    save.hidden = j.status !== "done";
    if (j.status === "done") save.href = "/api/jobs/" + j.id + "/file";
    $(".act", el).textContent = isActive(j) ? "Zrušit" : "Odebrat";
  }

  function renderQueue() {
    const list = $("#queueList");
    const existing = new Map($$(".job", list).map((el) => [el.dataset.id, el]));
    $$(".empty", list).forEach((el) => el.remove());

    if (!jobs.length) {
      existing.forEach((el) => el.remove());
      const li = document.createElement("li");
      li.className = "empty";
      li.textContent = "Zatím tu nic není. Vlož odkaz a stahování se objeví tady.";
      list.append(li);
      return;
    }

    jobs.forEach((j, i) => {
      let el = existing.get(j.id);
      if (!el) el = createJobEl(j);
      existing.delete(j.id);
      updateJobEl(el, j);
      if (list.children[i] !== el) list.insertBefore(el, list.children[i] || null);
    });
    existing.forEach((el) => el.remove());
  }

  function triggerSave(id) {
    const a = document.createElement("a");
    a.href = "/api/jobs/" + id + "/file";
    a.download = "";
    document.body.append(a);
    a.click();
    a.remove();
  }

  function handleJobs() {
    for (const j of jobs) {
      if (isActive(j)) {
        seenActive.add(j.id);
      } else if ((j.status === "error" || j.status === "cancelled") && seenActive.has(j.id) && !refunded.has(j.id)) {
        refunded.add(j.id);
        loadQuota();
      } else if (j.status === "done" && seenActive.has(j.id) && !saved.has(j.id)) {
        saved.add(j.id);
        if (S.autoSave) triggerSave(j.id);
      }
    }
    const active = jobs.filter(isActive).length;
    const badge = $("#badge");
    badge.hidden = active === 0;
    badge.textContent = String(active);
  }

  async function poll() {
    clearTimeout(pollTimer);
    let delay = 4000;
    try {
      jobs = await api("/api/jobs");
      handleJobs();
      renderQueue();
      if (jobs.some(isActive)) delay = 800;
    } catch {
      delay = 6000;
    }
    pollTimer = setTimeout(poll, delay);
  }

  const drawer = $("#queue");
  const queueBtn = $("#queueBtn");
  function setQueue(open) {
    drawer.classList.toggle("open", open);
    drawer.inert = !open;
    queueBtn.setAttribute("aria-expanded", String(open));
    if (open) $("#queueClose").focus(); else if (drawer.contains(document.activeElement)) queueBtn.focus();
  }
  queueBtn.addEventListener("click", () => setQueue(!drawer.classList.contains("open")));
  $("#queueClose").addEventListener("click", () => setQueue(false));
  addEventListener("keydown", (e) => { if (e.key === "Escape") setQueue(false); });
  document.addEventListener("click", (e) => {
    if (drawer.classList.contains("open") && !drawer.contains(e.target) && !queueBtn.contains(e.target)) setQueue(false);
  });
  $("#clearBtn").addEventListener("click", async () => {
    try { await api("/api/jobs/clear", { method: "POST" }); } catch (err) { toast(err.message, true); }
    poll();
  });

  /* ---------- Převod ---------- */
  let remuxFile = null;
  const drop = $("#drop"), fileInput = $("#fileInput"), remuxBtn = $("#remuxBtn");

  function setFile(f) {
    remuxFile = f || null;
    $("#dropName").textContent = f ? `${f.name}, ${fmtBytes(f.size)}` : "Přetáhni sem soubor nebo klikni a vyber";
    remuxBtn.disabled = !f;
  }
  fileInput.addEventListener("change", () => setFile(fileInput.files[0]));
  ["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", (e) => { if (e.dataTransfer.files[0]) setFile(e.dataTransfer.files[0]); });

  $("#remuxForm").addEventListener("submit", (e) => {
    e.preventDefault();
    if (!remuxFile) return;
    const fd = new FormData();
    fd.append("file", remuxFile);
    fd.append("target", $("#remuxTarget").value);
    fd.append("reencode", $("#remuxReencode").checked ? "true" : "false");

    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/remux");
    xhr.upload.onprogress = (ev) => {
      if (ev.lengthComputable) remuxBtn.textContent = `Nahrávám, ${Math.round((ev.loaded / ev.total) * 100)} %`;
    };
    const finish = () => { remuxBtn.textContent = "Převést"; remuxBtn.disabled = !remuxFile; };
    xhr.onerror = () => { finish(); toast("Nahrání se nepovedlo. Zkontroluj spojení se serverem.", true); };
    xhr.onload = () => {
      finish();
      let data = {};
      try { data = JSON.parse(xhr.responseText); } catch { /* ignoruj */ }
      if (xhr.status >= 200 && xhr.status < 300) {
        seenActive.add(data.id);
        toast("Převod přidán do fronty");
        setFile(null);
        fileInput.value = "";
        setQueue(true);
        poll();
      } else {
        toast(typeof data.detail === "string" ? data.detail : "Převod se nepodařilo spustit.", true);
      }
    };
    remuxBtn.disabled = true;
    xhr.send(fd);
  });

  /* ---------- Weby ---------- */
  let sites = null;
  function renderSites() {
    const q = $("#sitesFilter").value.trim().toLowerCase();
    const matches = sites.names.filter((n) => n.toLowerCase().includes(q));
    const shown = matches.slice(0, 400);
    const list = $("#sitesList");
    list.innerHTML = "";
    for (const n of shown) {
      const li = document.createElement("li");
      li.textContent = n;
      list.append(li);
    }
    $("#sitesLead").textContent = q
      ? `Nalezeno ${matches.length} z ${sites.names.length}.` + (matches.length > shown.length ? ` Zobrazeno prvních ${shown.length}.` : "")
      : `Díky yt-dlp ${sites.version} umí aplikace stahovat z ${sites.names.length} webů. Zobrazeno prvních ${shown.length}, ostatní najdeš hledáním.`;
  }
  async function loadSites() {
    if (sites) return;
    try {
      sites = await api("/api/sites");
      renderSites();
    } catch (err) {
      $("#sitesLead").textContent = "Seznam se nepodařilo načíst: " + err.message;
    }
  }
  $("#sitesFilter").addEventListener("input", () => sites && renderSites());

  /* ---------- Denní limit a přístupový klíč ---------- */
  const refunded = new Set();
  let Q = null;
  let quotaTimer;

  function fmtWait(sec) {
    const total = Math.max(0, Math.round(sec || 0));
    let h = Math.floor(total / 3600);
    let m = Math.ceil((total % 3600) / 60);
    if (m === 60) { h += 1; m = 0; }
    if (h && m) return `${h} h ${m} min`;
    return h ? `${h} h` : `${Math.max(m, 1)} min`;
  }

  function renderQuota() {
    if (!Q) return;
    const hint = $("#quotaHint");
    $("#accessGroup").hidden = !Q.enabled;
    $("#keyClear").hidden = !Q.has_key;
    $("#keyInput").hidden = Q.has_key;
    $("#keySubmit").hidden = Q.has_key;
    renderRequest();

    if (!Q.enabled) { hint.hidden = true; return; }

    const title = $("#quotaTitle");
    const text = $("#quotaText");
    if (Q.has_key) {
      title.textContent = "Bez denního limitu";
      text.textContent = Q.label ? `Přístupový klíč „${Q.label}“ je aktivní.` : "Přístupový klíč je aktivní.";
      $("#keyHelp").textContent = "Klíč je přiřazený k tomuhle zařízení a jinde nefunguje. Odebrat ho můžeš, na tomhle zařízení ho pak zadáš znovu.";
      hint.hidden = true;
      return;
    }

    $("#keyHelp").textContent = Q.stale_key
      ? "Uložený klíč už neplatí (byl zrušený, uvolněný nebo patří jinému zařízení). Zadej ho znovu, případně požádej provozovatele o uvolnění."
      : "Klíč odstraní denní limit stažení. Po zadání se natrvalo přiřadí k tomuhle zařízení a jinde nebude fungovat.";
    title.textContent = `Dnes zbývá ${Q.remaining} z ${Q.limit} stažení`;
    text.textContent = Q.remaining === 0 && Q.reset_in != null
      ? `Limit je vyčerpaný. Další stažení se uvolní za ${fmtWait(Q.reset_in)}.`
      : "Limit se počítá za posledních 24 hodin. Nepovedené a zrušené stažení se nepočítá.";

    hint.hidden = false;
    hint.classList.toggle("empty", Q.remaining === 0);
    hint.textContent = Q.remaining === 0 && Q.reset_in != null
      ? `Denní limit je vyčerpaný, další stažení půjde za ${fmtWait(Q.reset_in)}. `
      : `Zbývá ${Q.remaining} z ${Q.limit} stažení za den. `;
    const a = document.createElement("a");
    a.href = "#/settings";
    a.textContent = "Mám přístupový klíč";
    hint.append(a);
    const b = document.createElement("a");
    b.href = "#/settings";
    b.textContent = "Požádat o klíč";
    hint.append(" nebo ", b);
  }

  /* Žádost o klíč: poslední odeslanou žádost si prohlížeč pamatuje, ať se po obnovení stránky nezeptá znovu */
  const REQ_KEY = "lumen_key_request";
  const REQ_KEEP = 14 * 24 * 3600 * 1000;
  function lastRequest() {
    try {
      const r = JSON.parse(localStorage.getItem(REQ_KEY) || "null");
      return r && r.email && Date.now() - r.ts < REQ_KEEP ? r : null;
    } catch { return null; }
  }
  function rememberRequest(email) {
    try { localStorage.setItem(REQ_KEY, JSON.stringify({ email, ts: Date.now() })); } catch { /* úložiště nemusí být dostupné */ }
  }
  function forgetRequest() {
    try { localStorage.removeItem(REQ_KEY); } catch { /* nevadí */ }
  }

  function renderRequest() {
    const box = $("#requestSetting");
    box.hidden = !Q || !Q.can_request;
    if (box.hidden) return;
    const sent = lastRequest();
    $("#requestForm").hidden = !!sent;
    $("#requestSent").hidden = !sent;
    if (sent) {
      $("#requestHelp").textContent = "Teď už jen počkej na e-mail s klíčem.";
      $("#requestSentText").replaceChildren(
        "Klíč ti pošlu e-mailem na ",
        Object.assign(document.createElement("strong"), { textContent: sent.email }),
        ". Odpovídám ručně, takže to může chvíli trvat. Kdyby nic nepřišlo, podívej se i do spamu."
      );
    } else {
      $("#requestHelp").textContent = "Napiš svůj e-mail a pošli žádost. Klíč ti vystavím ručně a pošlu ho na tuhle adresu. Může to trvat i několik hodin, někdy déle.";
    }
  }

  $("#requestForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const email = $("#requestEmail").value.trim();
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(email)) {
      toast("Zadej platnou e-mailovou adresu, třeba jmeno@seznam.cz.", true);
      $("#requestEmail").focus();
      return;
    }
    const btn = $("#requestSubmit");
    btn.disabled = true;
    try {
      const r = await api("/api/key/request", { method: "POST", body: { email, website: $("#requestWebsite").value } });
      if (r.email) rememberRequest(r.email); else rememberRequest(email);
      $("#requestEmail").value = "";
      renderRequest();
      toast("Žádost odeslána. Klíč ti přijde e-mailem.");
    } catch (err) {
      toast(err.message, true);
    } finally {
      btn.disabled = false;
    }
  });

  $("#requestAgain").addEventListener("click", () => {
    forgetRequest();
    renderRequest();
    $("#requestEmail").focus();
  });

  async function loadQuota() {
    clearTimeout(quotaTimer);
    try {
      Q = await api("/api/quota");
      renderQuota();
    } catch { /* server ještě startuje */ }
    // kdyby se mezitím uvolnilo místo v limitu nebo klíč zrušili, stav se po čase obnoví
    quotaTimer = setTimeout(loadQuota, 5 * 60 * 1000);
  }

  $("#keyForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const key = $("#keyInput").value.trim();
    if (!key) { $("#keyInput").focus(); return; }
    const btn = $("#keySubmit");
    btn.disabled = true;
    try {
      await api("/api/key", { method: "POST", body: { key } });
      $("#keyInput").value = "";
      toast("Klíč přijat, denní limit je pryč.");
      await loadQuota();
    } catch (err) {
      toast(err.message, true);
    } finally {
      btn.disabled = false;
    }
  });

  $("#keyClear").addEventListener("click", async () => {
    try {
      await api("/api/key", { method: "DELETE" });
      toast("Klíč odebrán.");
      await loadQuota();
    } catch (err) {
      toast(err.message, true);
    }
  });

  /* ---------- Upozornění na chybějící součásti ---------- */
  async function loadHealth() {
    try {
      const h = await api("/api/health");
      $("#versionLine").textContent = `yt-dlp ${h.ytdlp}. Při potížích s YouTube ho nejdřív aktualizuj (postup je v README).`;
      const msgs = [];
      if (!h.ffmpeg) msgs.push("Chybí ffmpeg. Bez něj nejde spojit video se zvukem ani převádět formáty.");
      if (!h.deno) msgs.push("Chybí Deno. Pro YouTube ho yt-dlp potřebuje, bez něj stahování často selže. Postup instalace je v README.");
      const banner = $("#banner");
      banner.innerHTML = "";
      msgs.forEach((m) => { const p = document.createElement("p"); p.textContent = m; banner.append(p); });
      banner.hidden = msgs.length === 0;
    } catch { /* server ještě startuje */ }
  }

  /* ---------- Start ---------- */
  $$("[data-setting]").forEach((el) => {
    const key = el.dataset.setting;
    if (el.type === "checkbox") el.checked = !!S[key];
    else if (el.type === "radio") el.checked = el.value === S[key];
    else el.value = S[key];

    el.addEventListener("change", () => {
      if (el.type === "checkbox") S[key] = el.checked;
      else if (el.type === "radio") { if (!el.checked) return; S[key] = el.value; }
      else S[key] = el.value;
      saveSettings();
      if (key === "mode") { updateOptions(); updateHint(); }
      if (key === "instant") updateSubmitLabel();
    });
  });

  applyTheme();
  updateHint();
  updateSubmitLabel();
  updateOptions();
  route();
  loadHealth();
  loadQuota();
  poll();
})();
