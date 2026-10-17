/* Hudba: online katalog (přes server) a hudba ze zařízení, společný přehrávač s frontou.
   Online skladby hrají z /api/music/stream/<id>, místní soubory přímo z disku (nic se neposílá na server). */
(() => {
  "use strict";

  const L = window.Lumen;
  if (!L) return;
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
  const el = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  };
  const icon = (name, cls = "") => {
    const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    s.setAttribute("class", ("ico " + cls).trim());
    const u = document.createElementNS("http://www.w3.org/2000/svg", "use");
    u.setAttribute("href", "#i-" + name);
    s.append(u);
    return s;
  };
  const fmt = (s) => (s || s === 0) && isFinite(s) ? L.fmtTime(s) : "0:00";
  const artistNames = (t) => (t.artists || []).map((a) => a.name).join(", ");
  /* 1 skladba, 2–4 skladby, 5 skladeb */
  const songs = (n) => n + " " + (n === 1 ? "skladba" : n >= 2 && n <= 4 ? "skladby" : "skladeb");

  /* ---------- Předvolby ---------- */
  const PREF_KEY = "lumen.music";
  const P = { volume: 1, shuffle: false, repeat: "off", automix: true, source: "online" };
  try { Object.assign(P, JSON.parse(localStorage.getItem(PREF_KEY) || "{}")); } catch { /* soukromé okno */ }
  const savePrefs = () => { try { localStorage.setItem(PREF_KEY, JSON.stringify(P)); } catch { /* nevadí */ } };

  /* ---------- Přehrávač a fronta ---------- */
  const audio = $("#mAudio");
  let queue = [];   // { src: "online"|"local", id, title, artists, album, duration, thumb, file?, handle? }
  let index = -1;
  let objectUrl = null;
  let errorsInRow = 0;
  let automixBusy = false;

  const current = () => queue[index] || null;

  function onlineUrl(t) { return "https://music.youtube.com/watch?v=" + t.id; }

  function fileUrl(file) {
    if (objectUrl) URL.revokeObjectURL(objectUrl);
    objectUrl = URL.createObjectURL(file);
    return objectUrl;
  }

  /* Místní skladba z playlistu nese jen cestu: soubor se dohledá v načtené knihovně (záložka V zařízení). */
  function withFile(t) {
    if (t.src !== "local" || t.file || t.handle || t.url) return t;
    const hit = local.find((x) => x.id === t.id);
    return hit ? { ...t, file: hit.file, handle: hit.handle } : t;
  }

  function startPlay() {
    const p = audio.play();
    if (p) p.catch((err) => {
      // Safari a mobilní prohlížeče pustí zvuk jen přímo po klepnutí; jinak zůstane tlačítko přehrát
      if (err.name === "NotAllowedError") L.toast("Prohlížeč zvuk sám nespustil, klepni na tlačítko přehrát.");
    });
  }

  function afterLoad(t) {
    updateMediaSession();
    loadLyricsRef(t);
  }

  function load(i, autoplay = true) {
    if (i < 0 || i >= queue.length) return;
    index = i;
    const t = (queue[i] = withFile(queue[i]));
    renderNow();
    renderQueue();
    markNow();
    // src a play() musí proběhnout hned v kliknutí (bez await), jinak je Safari na iPhonu zablokuje
    if (t.src === "online" || t.file || t.url) {
      audio.src = t.src === "online" ? "/api/music/stream/" + encodeURIComponent(t.id) : t.url || fileUrl(t.file);
      if (autoplay) startPlay();
      afterLoad(t);
      return;
    }
    if (!t.handle) {
      L.toast("Soubor není načtený: " + t.title + ". Otevři složku v záložce V zařízení.", true);
      if (index < queue.length - 1) setTimeout(() => next(), 800);
      return;
    }
    t.handle.getFile().then((file) => {
      if (current() !== t) return;
      audio.src = fileUrl(file);
      if (autoplay) startPlay();
      afterLoad(t);
    }, (err) => L.toast("Soubor už není dostupný: " + (err.message || t.title), true));
  }

  function playList(list, start = 0) {
    if (!list.length) return;
    queue = list.map((t) => ({ ...t }));
    if (P.shuffle) {
      const first = queue.splice(start, 1)[0];
      shuffleInPlace(queue);
      queue.unshift(first);
      start = 0;
    }
    errorsInRow = 0;
    load(start);
  }

  /* jedna skladba: zahraje ji a hned za ni přidá podobné (jako rádio) */
  function playSingle(t) {
    playList([t]);
    if (P.automix && t.src === "online") automix();
  }

  function markNow() {
    const t = current();
    const key = t ? t.src + ":" + t.id : null;
    $$(".m-track[data-key]").forEach((n) => n.classList.toggle("now", n.dataset.key === key));
  }

  function addToQueue(list, next = false) {
    const items = list.map((t) => ({ ...t }));
    if (!queue.length) { playList(items); return; }
    if (next) queue.splice(index + 1, 0, ...items); else queue.push(...items);
    renderQueue();
    L.toast(items.length === 1 ? (next ? "Zahraje jako další" : "Přidáno do fronty") : `Do fronty ${items.length <= 4 ? "přidány" : "přidáno"} ${songs(items.length)}`);
  }

  function shuffleInPlace(a) {
    for (let i = a.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [a[i], a[j]] = [a[j], a[i]];
    }
    return a;
  }

  async function next(auto = false) {
    if (!queue.length) return;
    if (auto && P.repeat === "one") { audio.currentTime = 0; audio.play().catch(() => {}); return; }
    if (index < queue.length - 1) return load(index + 1);
    if (P.repeat === "all") return load(0);
    if (await automix()) return load(index + 1);
    if (auto) { audio.pause(); renderNow(); }
  }

  function prev() {
    if (audio.currentTime > 3 || index <= 0) { audio.currentTime = 0; return; }
    load(index - 1);
  }

  /* Automix: na konci fronty doplní podobné skladby k poslední online skladbě */
  async function automix() {
    if (!P.automix || automixBusy) return false;
    const last = [...queue].reverse().find((t) => t.src === "online");
    if (!last) return false;
    automixBusy = true;
    try {
      const data = await L.api("/api/music/next/" + encodeURIComponent(last.id));
      const have = new Set(queue.map((t) => t.id));
      const fresh = data.tracks.filter((t) => !have.has(t.id)).map((t) => ({ ...t, src: "online" }));
      if (!fresh.length) return false;
      queue.push(...fresh.slice(0, 20));
      renderQueue();
      return true;
    } catch (err) {
      L.toast(err.message, true);
      return false;
    } finally {
      automixBusy = false;
    }
  }

  audio.addEventListener("ended", () => next(true));
  audio.addEventListener("playing", () => { errorsInRow = 0; setLoading(false); renderPlayState(); prefetchNext(); });
  // server může skladbu nejdřív stahovat (pár sekund), tlačítko přehrát mezitím ukazuje načítání
  audio.addEventListener("loadstart", () => setLoading(true));
  ["canplay", "error", "emptied", "pause"].forEach((ev) => audio.addEventListener(ev, () => setLoading(false)));
  function setLoading(on) { $("#mPlay").classList.toggle("loading", on && !!audio.getAttribute("src")); }

  /* Příprava předem: server skladbu stáhne do mezipaměti (nebo zjistí stream) dřív, než na ni klikneš.
     Volá se po najetí myší, po otevření alba či playlistu a pro další skladbu ve frontě. */
  const prepared = new Set();
  function prepare(id) {
    if (!id || prepared.has(id) || !status || !status.online) return;
    prepared.add(id);
    fetch("/api/music/prepare/" + encodeURIComponent(id), { method: "POST" })
      .then((r) => (r.ok ? r.json() : { state: "failed" }))
      .then((d) => { if (d.state === "busy" || d.state === "failed") prepared.delete(id); })  // zkusí se znovu později
      .catch(() => prepared.delete(id));
  }
  function prefetchNext() {
    const n = queue[index + 1];
    if (n && n.src === "online") setTimeout(() => prepare(n.id), 3000);
  }
  /* najetí myší (nebo prstem) na skladbu: krátká prodleva, ať se nepřipravuje všechno při projetí seznamem */
  function prepareOnHover(node, t) {
    if (t.src !== "online" && t.type !== "song" && t.type !== "video") return;
    let timer = null;
    node.addEventListener("pointerenter", () => { timer = setTimeout(() => prepare(t.id), 350); });
    node.addEventListener("pointerleave", () => clearTimeout(timer));
    node.addEventListener("touchstart", () => prepare(t.id), { passive: true });
  }
  audio.addEventListener("pause", renderPlayState);
  audio.addEventListener("play", renderPlayState);
  audio.addEventListener("timeupdate", renderTime);
  audio.addEventListener("durationchange", () => {
    const t = current();
    if (t && !t.duration && isFinite(audio.duration)) t.duration = Math.round(audio.duration);
    renderTime();
  });
  audio.addEventListener("error", async () => {
    const t = current();
    const src = audio.getAttribute("src");
    if (!t || !src) return;
    errorsInRow += 1;
    let msg = t.src === "local" ? "Prohlížeč nepřehraje tenhle soubor: " + t.title : "Skladbu se nepodařilo přehrát: " + t.title;
    if (t.src === "online") {
      // přehrávač chybu serveru nevidí, zeptáme se znovu a ukážeme skutečný důvod (i s kódem nápovědy)
      try {
        const r = await fetch(src, { headers: { Range: "bytes=0-1" } });
        if (!r.ok) {
          const d = await r.json().catch(() => ({}));
          msg = d.detail || `${msg} (HTTP Error ${r.status})`;
        } else if (audio.error && audio.error.code === 4) {
          msg = "Prohlížeč nepřehraje formát skladby: " + t.title;
        }
      } catch { /* necháme obecnou hlášku */ }
    }
    if (current() !== t) return;
    if (audio.error && t.src === "online") {
      // kód přehrávače: 2 = síť, 3 = dekódování, 4 = formát; do hlášky i do logu aplikace
      msg += ` (přehrávač: ${audio.error.code}${audio.error.message ? ", " + audio.error.message : ""})`;
      fetch("/api/music/client-error", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: t.id, code: audio.error.code, message: audio.error.message || "" }) }).catch(() => {});
    }
    L.toast(msg, true);
    if (errorsInRow < 3 && index < queue.length - 1) setTimeout(() => next(), 2500);
  });
  // když se spustí jiné video nebo zvuk (náhled, přehrávač stažených souborů), hudba se ztlumí
  document.addEventListener("play", (e) => { if (e.target !== audio && e.target instanceof HTMLMediaElement) audio.pause(); }, true);

  /* ---------- Lišta přehrávače ---------- */
  const bar = $("#mBar");

  function renderNow() {
    const t = current();
    bar.hidden = !t;
    document.body.classList.toggle("m-active", !!t);
    if (!t) return;
    $("#mTitle").textContent = t.title;
    $("#mArtist").textContent = artistNames(t) || (t.album && t.album.name) || (t.src === "local" ? "Ze zařízení" : "");
    const img = $("#mCover");
    img.hidden = !t.thumb;
    if (t.thumb) img.src = t.thumb; else img.removeAttribute("src");
    $("#mDownload").hidden = t.src !== "online";
    renderPlayState();
    renderTime();
  }

  function renderPlayState() {
    const playing = !audio.paused && !audio.ended;
    const btn = $("#mPlay");
    $("use", btn).setAttribute("href", playing ? "#i-pause" : "#i-play");
    btn.setAttribute("aria-label", playing ? "Pozastavit" : "Přehrát");
    if ("mediaSession" in navigator) navigator.mediaSession.playbackState = playing ? "playing" : "paused";
    $$(".m-track.now").forEach((n) => n.classList.toggle("paused", !playing));
  }

  let seeking = false;
  function renderTime() {
    const t = current();
    const dur = isFinite(audio.duration) && audio.duration ? audio.duration : (t && t.duration) || 0;
    $("#mTime").textContent = `${fmt(audio.currentTime)} / ${fmt(dur)}`;
    if (!seeking) $("#mSeek").value = dur ? String(Math.round((audio.currentTime / dur) * 1000)) : "0";
    $("#mSeek").style.setProperty("--p", (dur ? (audio.currentTime / dur) * 100 : 0) + "%");
  }

  $("#mSeek").addEventListener("input", () => {
    seeking = true;
    const dur = audio.duration || (current() && current().duration) || 0;
    $("#mSeek").style.setProperty("--p", $("#mSeek").value / 10 + "%");
    $("#mTime").textContent = `${fmt((dur * $("#mSeek").value) / 1000)} / ${fmt(dur)}`;
  });
  $("#mSeek").addEventListener("change", () => {
    const dur = audio.duration || 0;
    if (dur) audio.currentTime = (dur * $("#mSeek").value) / 1000;
    seeking = false;
  });

  $("#mPlay").addEventListener("click", () => {
    if (!current()) return;
    if (audio.paused) audio.play().catch((err) => L.toast("Skladbu se nepodařilo přehrát: " + err.message, true));
    else audio.pause();
  });
  $("#mNext").addEventListener("click", () => next());
  $("#mPrev").addEventListener("click", prev);

  function renderToggles() {
    const sh = $("#mShuffle");
    sh.classList.toggle("on", P.shuffle);
    sh.setAttribute("aria-pressed", String(P.shuffle));
    const rp = $("#mRepeat");
    rp.classList.toggle("on", P.repeat !== "off");
    rp.setAttribute("aria-pressed", String(P.repeat !== "off"));
    rp.setAttribute("aria-label", { off: "Opakovat: vypnuto", all: "Opakovat: celá fronta", one: "Opakovat: jedna skladba" }[P.repeat]);
    $(".m-one", rp).hidden = P.repeat !== "one";
    $("#mAutomix").checked = P.automix;
  }
  $("#mShuffle").addEventListener("click", () => {
    P.shuffle = !P.shuffle;
    if (P.shuffle && queue.length > index + 2) {
      const rest = shuffleInPlace(queue.splice(index + 1));
      queue.push(...rest);
      renderQueue();
    }
    savePrefs();
    renderToggles();
  });
  $("#mRepeat").addEventListener("click", () => {
    P.repeat = { off: "all", all: "one", one: "off" }[P.repeat] || "off";
    savePrefs();
    renderToggles();
  });
  $("#mAutomix").addEventListener("change", () => { P.automix = $("#mAutomix").checked; savePrefs(); });

  const vol = $("#mVolume");
  vol.value = String(Math.round(P.volume * 100));
  audio.volume = P.volume;
  vol.addEventListener("input", () => { P.volume = vol.value / 100; audio.volume = P.volume; savePrefs(); });

  $("#mDownload").addEventListener("click", async () => {
    const t = current();
    if (!t || t.src !== "online") return;
    try { await L.downloadAudio({ url: onlineUrl(t), title: [artistNames(t), t.title].filter(Boolean).join(" - "), thumb: t.thumb }); }
    catch (err) { L.toast(err.message, true); }
  });

  /* Ovládání ze zamčené obrazovky a mediálních kláves */
  function updateMediaSession() {
    const t = current();
    if (!("mediaSession" in navigator) || !t) return;
    navigator.mediaSession.metadata = new MediaMetadata({
      title: t.title,
      artist: artistNames(t),
      album: (t.album && t.album.name) || "",
      artwork: t.thumb ? [{ src: t.thumb, sizes: "544x544" }] : [],
    });
  }
  if ("mediaSession" in navigator) {
    const ms = navigator.mediaSession;
    const set = (a, fn) => { try { ms.setActionHandler(a, fn); } catch { /* akci prohlížeč nezná */ } };
    set("play", () => audio.play());
    set("pause", () => audio.pause());
    set("previoustrack", prev);
    set("nexttrack", () => next());
    set("seekto", (d) => { if (d.seekTime != null) audio.currentTime = d.seekTime; });
  }

  /* ---------- Fronta (panel) ---------- */
  const qPanel = $("#mQueue");
  function setQueuePanel(open) {
    qPanel.classList.toggle("open", open);
    qPanel.inert = !open;
    $("#mQueueBtn").setAttribute("aria-expanded", String(open));
    if (open) { renderQueue(); $("#mQueueClose").focus(); }
  }
  $("#mQueueBtn").addEventListener("click", () => setQueuePanel(!qPanel.classList.contains("open")));
  $("#mQueueClose").addEventListener("click", () => setQueuePanel(false));
  addEventListener("keydown", (e) => { if (e.key === "Escape") setQueuePanel(false); });
  document.addEventListener("click", (e) => {
    if (qPanel.classList.contains("open") && !qPanel.contains(e.target) && !$("#mQueueBtn").contains(e.target)) setQueuePanel(false);
  });
  $("#mQueueSave").addEventListener("click", () => {
    if (queue.length) openPick(queue, "Fronta " + new Date().toLocaleDateString("cs-CZ"));
  });
  $("#mQueueClear").addEventListener("click", () => {
    const t = current();
    queue = t ? [t] : [];
    index = t ? 0 : -1;
    renderQueue();
  });

  function renderQueue() {
    if (!qPanel.classList.contains("open")) return;
    const list = $("#mQueueList");
    list.replaceChildren();
    if (!queue.length) { list.append(el("li", "empty", "Fronta je prázdná.")); return; }
    queue.forEach((t, i) => {
      const row = trackRow(t, i, {
        onPlay: () => load(i),
        actions: i === index ? [] : [["Odebrat", () => {
          queue.splice(i, 1);
          if (i < index) index -= 1;
          renderQueue();
        }]],
      });
      if (i < index) row.classList.add("past");
      list.append(row);
    });
    const now = $(".m-track.now", list);
    if (now) now.scrollIntoView({ block: "nearest" });
  }

  /* ---------- Text písně ---------- */
  let lyricsRef = null;
  async function loadLyricsRef(t) {
    lyricsRef = null;
    $("#mLyricsBtn").hidden = true;
    if (t.src !== "online") return;
    try {
      const data = await L.api("/api/music/next/" + encodeURIComponent(t.id));
      if (current() === t && data.lyrics) {
        lyricsRef = data.lyrics;
        $("#mLyricsBtn").hidden = false;
      }
    } catch { /* bez textu */ }
  }
  $("#mLyricsBtn").addEventListener("click", async () => {
    const t = current();
    if (!t || !lyricsRef) return;
    const dlg = $("#mLyrics");
    $("#mLyricsTitle").textContent = [artistNames(t), t.title].filter(Boolean).join(" – ");
    $("#mLyricsText").textContent = "Načítám text…";
    $("#mLyricsSource").textContent = "";
    dlg.showModal();
    try {
      const ly = await L.api("/api/music/lyrics/" + encodeURIComponent(lyricsRef));
      $("#mLyricsText").textContent = ly.text || "Text k téhle skladbě není k dispozici.";
      $("#mLyricsSource").textContent = ly.source || "";
    } catch (err) {
      $("#mLyricsText").textContent = err.message;
    }
  });
  $("#mLyricsClose").addEventListener("click", () => $("#mLyrics").close());
  $("#mLyrics").addEventListener("click", (e) => { if (e.target.id === "mLyrics") $("#mLyrics").close(); });

  /* ---------- Řádek skladby a karta ---------- */
  function trackRow(t, i, { onPlay, actions = [], number = null } = {}) {
    const li = el("li", "m-track");
    li.dataset.key = t.src + ":" + t.id;
    if (current() && current().id === t.id && current().src === t.src) li.classList.add("now");
    const play = el("button", "m-track-main");
    play.type = "button";
    const thumb = el("span", "m-track-thumb");
    if (number != null && !t.thumb) thumb.append(el("span", "m-track-no", String(number)));
    else if (t.thumb) {
      const img = el("img");
      img.alt = "";
      img.loading = "lazy";
      img.referrerPolicy = "no-referrer";
      img.src = t.thumb;
      img.onerror = () => img.remove();
      thumb.append(img);
    } else thumb.append(icon("music"));
    thumb.append(icon("play", "m-track-hover"));
    const text = el("span", "m-track-text");
    text.append(el("span", "m-track-title", t.title));
    const sub = [artistNames(t), t.album && t.album.name && t.src === "local" ? t.album.name : null].filter(Boolean).join(" · ");
    if (sub) text.append(el("span", "m-track-sub", sub));
    play.append(thumb, text);
    play.addEventListener("click", onPlay);
    if (t.src === "online") prepareOnHover(li, t);
    li.append(play);
    li.append(el("span", "m-track-dur muted small", t.duration ? fmt(t.duration) : ""));
    const menu = el("span", "m-track-actions");
    for (const [label, fn] of actions) {
      const b = el("button", "link", label);
      b.type = "button";
      b.addEventListener("click", (e) => { e.stopPropagation(); fn(); });
      menu.append(b);
    }
    li.append(menu);
    return li;
  }

  function trackActions(t, list) {
    const acts = [["Další", () => addToQueue([t], true)], ["Do fronty", () => addToQueue([t])], ["Do playlistu", () => openPick([t])]];
    if (t.src === "online") {
      acts.push(["Stáhnout", async () => {
        try { await L.downloadAudio({ url: onlineUrl(t), title: [artistNames(t), t.title].filter(Boolean).join(" - "), thumb: t.thumb }); }
        catch (err) { L.toast(err.message, true); }
      }]);
    }
    return acts;
  }

  function trackList(list, { numbered = false } = {}) {
    const ul = el("ul", "m-tracks");
    list.forEach((t, i) => ul.append(trackRow(t, i, {
      onPlay: () => playList(list, i),
      actions: trackActions(t, list),
      number: numbered ? i + 1 : null,
    })));
    return ul;
  }

  function cardEl(item) {
    const a = el("a", "m-card m-" + item.type);
    a.href = item.type === "song" || item.type === "video" ? "#" : `#/music/${item.type}/${encodeURIComponent(item.id)}`;
    const cover = el("span", "m-card-cover");
    if (item.thumb) {
      const img = el("img");
      img.alt = "";
      img.loading = "lazy";
      img.referrerPolicy = "no-referrer";
      img.src = item.thumb;
      img.onerror = () => img.remove();
      cover.append(img);
    } else cover.append(icon("music"));
    if (item.type !== "artist") cover.append(icon("play", "m-card-play"));
    a.append(cover, el("span", "m-card-title", item.title));
    const sub = item.subtitle || artistNames(item) || { song: "Skladba", video: "Video", album: "Album", playlist: "Playlist", artist: "Interpret" }[item.type];
    a.append(el("span", "m-card-sub", sub));
    if (item.type === "song" || item.type === "video") {
      a.addEventListener("click", (e) => { e.preventDefault(); playSingle({ ...item, src: "online" }); });
      prepareOnHover(a, item);
    }
    return a;
  }

  function shelf(title, items) {
    const sec = el("section", "m-shelf");
    sec.append(el("h2", "group", title));
    const row = el("div", "m-row");
    items.forEach((it) => row.append(cardEl(it)));
    sec.append(row);
    return sec;
  }

  /* ---------- Online: stránky ---------- */
  const content = $("#mContent");
  let status = null;
  let renderToken = 0;

  async function loadStatus() {
    try { status = await L.api("/api/music/status"); } catch { status = { enabled: true, online: false, reason: "error" }; }
    $("#mLocked").hidden = status.online || status.reason !== "key";
    $("#mSearchForm").hidden = !status.online;
    return status;
  }

  function busy(text = "Načítám…") {
    content.replaceChildren(el("p", "muted m-loading", text));
  }

  function failed(err) {
    const box = el("div", "m-error");
    box.append(el("p", null, err.message));
    const retry = el("button", "btn", "Zkusit znovu");
    retry.type = "button";
    retry.addEventListener("click", () => openMusicRoute(true));
    box.append(retry);
    content.replaceChildren(box);
    L.toast(err.message, true);
  }

  async function pageHome(token) {
    $("#mFilters").hidden = true;
    busy();
    const [data, genres] = await Promise.all([L.api("/api/music/home"), L.api("/api/music/genres").catch(() => null)]);
    if (token !== renderToken) return;
    const nodes = data.sections.map((s) => shelf(s.title, s.items));
    if (genres && genres.sections.length) nodes.unshift(genreChips(genres.sections));
    content.replaceChildren(...nodes);
    if (!data.sections.length && !nodes.length) content.replaceChildren(el("p", "muted", "Domovská stránka je teď prázdná, zkus něco vyhledat."));
  }

  /* Žánry a nálady: řádek štítků nad domovskou stránkou, klik otevře playlisty žánru */
  function genreChips(sections) {
    const box = el("section", "m-genres");
    for (const sec of sections) {
      box.append(el("h2", "group", sec.title));
      const row = el("div", "m-filters m-genre-row");
      for (const g of sec.items) {
        const a = el("a", "m-chip", g.title);
        a.href = `#/music/genre/${encodeURIComponent(g.params)}/${encodeURIComponent(g.title)}`;
        row.append(a);
      }
      box.append(row);
    }
    return box;
  }

  async function pageGenre(token, params, name) {
    $("#mFilters").hidden = true;
    busy();
    const data = await L.api("/api/music/genre?params=" + encodeURIComponent(params));
    if (token !== renderToken) return;
    const grid = el("div", "m-grid");
    data.items.forEach((it) => grid.append(cardEl(it)));
    const head = el("div", "m-genre-head");
    head.append(el("p", "m-detail-kind muted small", "Žánr a nálada"), el("h2", "m-detail-title", name || "Žánr"));
    content.replaceChildren(backButton(), head, data.items.length ? grid : el("p", "muted", "K tomuhle žánru teď nejsou žádné playlisty."));
    window.scrollTo({ top: 0 });
  }

  async function pageSearch(token, type, q) {
    $("#mQuery").value = q;
    const filters = $("#mFilters");
    filters.hidden = false;
    $$("button", filters).forEach((b) => {
      const on = b.dataset.type === type;
      b.classList.toggle("on", on);
      b.setAttribute("aria-selected", String(on));
    });
    busy("Hledám…");
    const data = await L.api(`/api/music/search?type=${encodeURIComponent(type)}&q=${encodeURIComponent(q)}`);
    if (token !== renderToken) return;
    const nodes = [];
    if (type === "all") {
      for (const s of data.sections) {
        if (s.kind === "song" || s.kind === "video") {
          const sec = el("section", "m-shelf");
          sec.append(el("h2", "group", s.title), trackList(s.items.map((t) => ({ ...t, src: "online" }))));
          nodes.push(sec);
        } else nodes.push(shelf(s.title, s.items));
      }
    } else if (type === "songs" || type === "videos") {
      nodes.push(trackList(data.items.map((t) => ({ ...t, src: "online" }))));
    } else {
      const grid = el("div", "m-grid");
      data.items.forEach((it) => grid.append(cardEl(it)));
      nodes.push(grid);
    }
    content.replaceChildren(...nodes);
    if (!nodes.length || (type !== "all" && !data.items.length)) content.replaceChildren(el("p", "muted", `Pro „${q}“ jsem nic nenašel.`));
  }

  function detailHeader(data, tracks, extra = []) {
    const head = el("div", "m-detail-head" + (data.type === "artist" ? " artist" : ""));
    const cover = el("span", "m-detail-cover");
    if (data.thumb) {
      const img = el("img");
      img.alt = "";
      img.referrerPolicy = "no-referrer";
      img.src = data.thumb;
      cover.append(img);
    } else cover.append(icon("music"));
    const info = el("div", "m-detail-info");
    info.append(el("p", "m-detail-kind muted small", { album: "Album", playlist: "Playlist", artist: "Interpret" }[data.type] || ""));
    info.append(el("h2", "m-detail-title", data.title || ""));
    if (data.subtitle) info.append(el("p", "muted small", data.subtitle));
    const btns = el("div", "m-detail-btns");
    if (tracks.length) {
      const play = el("button", "btn primary");
      play.type = "button";
      play.append(icon("play"), document.createTextNode("Přehrát"));
      play.addEventListener("click", () => playList(tracks, 0));
      const mix = el("button", "btn");
      mix.type = "button";
      mix.append(icon("shuffle"), document.createTextNode("Náhodně"));
      mix.addEventListener("click", () => playList(shuffleInPlace([...tracks]), 0));
      const add = el("button", "btn");
      add.type = "button";
      add.append(icon("plus"), document.createTextNode("Do fronty"));
      add.addEventListener("click", () => addToQueue(tracks));
      btns.append(play, mix, add);
    }
    extra.forEach((b) => btns.append(b));
    info.append(btns);
    head.append(cover, info);
    return head;
  }

  function backButton() {
    const b = el("button", "link m-back");
    b.type = "button";
    b.append(icon("back"), document.createTextNode("Zpět"));
    b.addEventListener("click", () => (history.length > 1 ? history.back() : (location.hash = "#/music")));
    return b;
  }

  async function pageDetail(token, kind, id) {
    $("#mFilters").hidden = true;
    busy();
    const data = await L.api(`/api/music/${kind}/${encodeURIComponent(id)}`);
    if (token !== renderToken) return;
    const tracks = (data.tracks || []).map((t) => ({ ...t, src: "online" }));
    const extra = [];
    if (kind === "artist" && data.radio && tracks[0]) {
      const radio = el("button", "btn");
      radio.type = "button";
      radio.textContent = "Rádio";
      radio.addEventListener("click", async () => {
        try {
          const mix = await L.api(`/api/music/next/${encodeURIComponent(tracks[0].id)}?playlist=${encodeURIComponent(data.radio)}`);
          playList(mix.tracks.map((t) => ({ ...t, src: "online" })), 0);
        } catch (err) { L.toast(err.message, true); }
      });
      extra.push(radio);
    }
    if (tracks.length) {
      const save = el("button", "btn");
      save.type = "button";
      save.append(icon("plus"), document.createTextNode("Uložit jako playlist"));
      save.addEventListener("click", () => openPick(tracks, data.title));
      extra.push(save);
    }
    const nodes = [backButton(), detailHeader(data, tracks, extra)];
    if (data.description) nodes.push(el("p", "muted small m-desc", data.description));
    if (tracks.length) {
      const sec = el("section", "m-shelf");
      if (kind === "artist") sec.append(el("h2", "group", "Skladby"));
      sec.append(trackList(tracks, { numbered: kind === "album" }));
      if (kind === "artist" && data.all_songs) {
        const all = el("a", "link m-more", "Všechny skladby");
        all.href = "#/music/playlist/" + encodeURIComponent(data.all_songs);
        sec.append(all);
      }
      nodes.push(sec);
    } else if (kind !== "artist") {
      nodes.push(el("p", "muted", "Tady nejsou žádné skladby, které by šly přehrát."));
    }
    (data.sections || []).forEach((s) => nodes.push(shelf(s.title, s.items)));
    content.replaceChildren(...nodes);
    window.scrollTo({ top: 0 });
    tracks.slice(0, 2).forEach((t) => prepare(t.id));  // album / playlist: první skladby budou hrát hned
  }

  /* Odkaz z YouTube Music / YouTube vložený do hledání */
  function parseLink(text) {
    let u;
    try { u = new URL(text.trim()); } catch { return null; }
    const host = u.hostname.replace(/^(www|m)\./, "");
    if (!["music.youtube.com", "youtube.com", "youtu.be"].includes(host)) return null;
    const list = u.searchParams.get("list");
    const v = host === "youtu.be" ? u.pathname.slice(1) : u.searchParams.get("v");
    if (u.pathname.startsWith("/browse/")) {
      const id = u.pathname.split("/")[2];
      return id.startsWith("MPRE") ? { kind: "album", id } : { kind: "playlist", id };
    }
    if (u.pathname.startsWith("/channel/")) return { kind: "artist", id: u.pathname.split("/")[2] };
    if (u.pathname === "/playlist" && list) return { kind: "playlist", id: list };
    if (v) return { kind: "song", id: v, list };
    return null;
  }

  $("#mSearchForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const q = $("#mQuery").value.trim();
    if (!q) { $("#mQuery").focus(); return; }
    const link = parseLink(q);
    if (link && link.kind === "song") {
      try {
        const data = await L.api(`/api/music/next/${encodeURIComponent(link.id)}${link.list ? "?playlist=" + encodeURIComponent(link.list) : ""}`);
        const list = data.tracks.map((t) => ({ ...t, src: "online" }));
        const start = Math.max(0, list.findIndex((t) => t.id === link.id));
        if (list.length) playList(list, start); else L.toast("Ke skladbě se nic nenašlo.", true);
      } catch (err) { L.toast(err.message, true); }
      return;
    }
    location.hash = link ? `#/music/${link.kind}/${encodeURIComponent(link.id)}` : `#/music/search/all/${encodeURIComponent(q)}`;
  });
  $$("#mFilters button").forEach((b) => b.addEventListener("click", () => {
    const q = $("#mQuery").value.trim();
    if (q) location.hash = `#/music/search/${b.dataset.type}/${encodeURIComponent(q)}`;
  }));

  /* ---------- Místní hudba ---------- */
  const AUDIO_RE = /\.(mp3|m4a|aac|opus|ogg|oga|flac|wav|weba|webm)$/i;
  let local = [];
  let dirHandle = null;

  function localTrack(file, path, handle = null) {
    const parts = path.split("/");
    const folder = parts.length > 1 ? parts[parts.length - 2] : "";
    let name = parts[parts.length - 1].replace(/\.[^.]+$/, "").replace(/\s*\[[\w-]{6,20}\]$/, "").trim();  // Lumen přidává [id]
    let artist = "";
    const m = name.match(/^(.+?)\s+[-–]\s+(.+)$/);
    if (m) { artist = m[1].trim(); name = m[2].trim(); }
    return {
      src: "local", id: "local:" + path, title: name || parts[parts.length - 1],
      artists: artist ? [{ name: artist }] : [], album: folder ? { name: folder } : null,
      duration: null, thumb: null, file, handle, path,
    };
  }

  function setLocal(list, append = false) {
    const seen = new Set(append ? local.map((t) => t.id) : []);
    const merged = append ? [...local] : [];
    for (const t of list) if (!seen.has(t.id)) { seen.add(t.id); merged.push(t); }
    merged.sort((a, b) => a.path.localeCompare(b.path, "cs", { numeric: true }));
    local = merged;
    renderLocal();
  }

  function filteredLocal() {
    const q = $("#mLocalFilter").value.trim().toLowerCase();
    return q ? local.filter((t) => (t.title + " " + artistNames(t) + " " + (t.album ? t.album.name : "")).toLowerCase().includes(q)) : local;
  }

  function renderLocal() {
    const list = filteredLocal();
    const has = local.length > 0;
    $("#mLocalFilter").hidden = !has;
    $("#mLocalBar").hidden = !has;
    $("#mLocalCount").textContent = list.length === local.length ? songs(local.length) : `${list.length} z ${songs(local.length)}`;
    const ul = $("#mLocalList");
    ul.replaceChildren();
    if (!has) {
      ul.append(el("li", "empty", "Zatím tu nic není. Vyber složku s hudbou nebo přidej soubory."));
      return;
    }
    list.slice(0, 1500).forEach((t, i) => ul.append(trackRow(t, i, {
      onPlay: () => playList(list, i),
      actions: trackActions(t, list),
    })));
    if (list.length > 1500) ul.append(el("li", "empty", `Zobrazeno prvních 1500 skladeb, zbytek najdeš hledáním.`));
  }

  $("#mLocalFilter").addEventListener("input", renderLocal);
  $("#mLocalPlay").addEventListener("click", () => playList(filteredLocal(), 0));
  $("#mLocalShuffle").addEventListener("click", () => playList(shuffleInPlace([...filteredLocal()]), 0));
  $("#mLocalClear").addEventListener("click", async () => {
    local = local.filter((t) => t.id.startsWith("lib:"));  // knihovna Lumenu zůstává
    dirHandle = null;
    await idb("delete").catch(() => {});
    $("#mReconnect").hidden = true;
    renderLocal();
  });

  /* Knihovna Lumenu (jen aplikace PC a Android): složka, kam se ukládá stažená hudba, s importem ze zařízení */
  const LIB = "Knihovna Lumenu/";
  async function loadLibrary(quiet = true) {
    let d;
    try { d = await L.api("/api/library"); } catch { return; }
    $("#mLib").hidden = !d.enabled;
    if (!d.enabled) return;
    $("#mLibOpen").hidden = !d.can_open;
    $("#mLibNote").textContent = `Stažená hudba se sem ukládá sama (${d.dir}). Skladby ze zařízení sem přidáš importem.`;
    const tracks = d.tracks.map((x) => ({ ...localTrack(null, LIB + x.name), id: "lib:" + x.name,
      url: "/api/library/file/" + encodeURIComponent(x.name) }));
    local = local.filter((t) => !t.id.startsWith("lib:"));
    setLocal(tracks, true);
    if (!quiet && !tracks.length) L.toast("Knihovna je zatím prázdná.");
  }
  $("#mLibImport").addEventListener("change", async (e) => {
    const files = [...e.target.files];
    e.target.value = "";
    if (!files.length) return;
    const form = new FormData();
    files.forEach((f) => form.append("files", f, f.name));
    L.toast(`Importuji ${files.length} ${files.length === 1 ? "skladbu" : "skladeb"}…`);
    try {
      const r = await fetch("/api/library/import", { method: "POST", body: form });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || "Import se nepovedl.");
      L.toast(`Importováno: ${d.added.length}${d.skipped.length ? `, přeskočeno (není hudba): ${d.skipped.length}` : ""}.`);
      loadLibrary();
    } catch (err) { L.toast(err.message, true); }
  });
  $("#mLibOpen").addEventListener("click", () => fetch("/api/library/open", { method: "POST" }).catch(() => {}));
  loadLibrary();

  $("#mFiles").addEventListener("change", (e) => {
    const files = [...e.target.files].filter((f) => AUDIO_RE.test(f.name) || f.type.startsWith("audio/"));
    setLocal(files.map((f) => localTrack(f, "Přidané soubory/" + f.name)), true);
    e.target.value = "";
  });
  $("#mDirInput").addEventListener("change", (e) => {
    const files = [...e.target.files].filter((f) => AUDIO_RE.test(f.name));
    setLocal(files.map((f) => localTrack(f, f.webkitRelativePath || f.name)), true);
    e.target.value = "";
    if (!files.length) L.toast("Ve složce jsem nenašel žádnou hudbu.", true);
  });

  /* Složka se pamatuje (v prohlížečích, které to umí): IndexedDB drží jen odkaz, ne soubory */
  function idb(op, value) {
    return new Promise((resolve, reject) => {
      if (!("indexedDB" in window)) { reject(new Error("no idb")); return; }
      const req = indexedDB.open("lumen-music", 1);
      req.onupgradeneeded = () => req.result.createObjectStore("handles");
      req.onerror = () => reject(req.error);
      req.onsuccess = () => {
        const tx = req.result.transaction("handles", op === "get" ? "readonly" : "readwrite");
        const store = tx.objectStore("handles");
        const r = op === "get" ? store.get("dir") : op === "put" ? store.put(value, "dir") : store.delete("dir");
        r.onsuccess = () => resolve(r.result);
        r.onerror = () => reject(r.error);
      };
    });
  }

  async function scanDir(handle) {
    const found = [];
    async function walk(dir, path) {
      for await (const entry of dir.values()) {
        if (found.length >= 5000) return;
        if (entry.name.startsWith(".")) continue;
        if (entry.kind === "directory") await walk(entry, path + entry.name + "/");
        else if (AUDIO_RE.test(entry.name)) found.push(localTrack(null, path + entry.name, entry));
      }
    }
    $("#mLocalCount").textContent = "Načítám složku…";
    await walk(handle, handle.name + "/");
    setLocal(found, true);
    if (!found.length) L.toast("Ve složce jsem nenašel žádnou hudbu.", true);
  }

  $("#mPickDir").addEventListener("click", async () => {
    if (!window.showDirectoryPicker) { $("#mDirInput").click(); return; }
    try {
      const handle = await window.showDirectoryPicker({ id: "lumen-music", mode: "read" });
      dirHandle = handle;
      idb("put", handle).catch(() => {});
      $("#mReconnect").hidden = true;
      await scanDir(handle);
    } catch (err) {
      if (err.name !== "AbortError") L.toast("Přístup ke složce se nepodařil: " + err.message, true);
    }
  });

  $("#mReconnect").addEventListener("click", async () => {
    if (!dirHandle) return;
    try {
      if ((await dirHandle.requestPermission({ mode: "read" })) !== "granted") throw new Error("prohlížeč ho nepovolil");
      $("#mReconnect").hidden = true;
      await scanDir(dirHandle);
    } catch (err) {
      L.toast("Přístup ke složce se nepodařil: " + err.message, true);
    }
  });

  async function restoreDir() {
    try {
      const handle = await idb("get");
      if (!handle || !handle.queryPermission) return;
      dirHandle = handle;
      if ((await handle.queryPermission({ mode: "read" })) === "granted") await scanDir(handle);
      else {
        const b = $("#mReconnect");
        b.textContent = `Znovu otevřít „${handle.name}“`;
        b.hidden = false;
      }
    } catch { /* prohlížeč odkaz na složku neumí uložit */ }
  }

  /* ---------- Moje playlisty ----------
     Ukládají se v tomhle prohlížeči (localStorage). Online skladby jdou přehrát kdekoli s klíčem,
     skladby ze zařízení se dohledají v načtené složce podle cesty. */
  const PL_KEY = "lumen.playlists";
  const mineBox = $("#mMineContent");

  function loadPlaylists() {
    try {
      const data = JSON.parse(localStorage.getItem(PL_KEY) || "[]");
      return Array.isArray(data) ? data : [];
    } catch { return []; }
  }
  let playlists = loadPlaylists();

  function savePlaylists() {
    try {
      localStorage.setItem(PL_KEY, JSON.stringify(playlists));
      return true;
    } catch {
      L.toast("Playlist se nepodařilo uložit, úložiště prohlížeče je plné nebo vypnuté.", true);
      return false;
    }
  }

  /* do playlistu jdou jen údaje o skladbě, ne soubor (ten se při přehrání dohledá) */
  function storable(t) {
    const out = { src: t.src, id: t.id, title: t.title, artists: t.artists || [], album: t.album || null,
                  duration: t.duration || null, thumb: t.thumb || null };
    if (t.src === "local") out.path = t.path || t.id.replace(/^local:/, "");
    return out;
  }

  const findPlaylist = (id) => playlists.find((p) => p.id === id);
  const newId = () => "pl" + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);

  function createPlaylist(name, tracks = []) {
    const pl = { id: newId(), name: name.trim().slice(0, 80) || "Nový playlist", created: Date.now(), tracks: tracks.map(storable) };
    playlists.unshift(pl);
    return savePlaylists() ? pl : null;
  }

  function addTracks(pl, tracks) {
    const have = new Set(pl.tracks.map((t) => t.src + ":" + t.id));
    const fresh = tracks.map(storable).filter((t) => !have.has(t.src + ":" + t.id));
    pl.tracks.push(...fresh);
    pl.updated = Date.now();
    if (!savePlaylists()) return;
    const skipped = tracks.length - fresh.length;
    L.toast(fresh.length
      ? `Do „${pl.name}“ ${fresh.length === 1 ? "přidána" : fresh.length <= 4 ? "přidány" : "přidáno"} ${songs(fresh.length)}` + (skipped ? `, ${songs(skipped)} už tam ${skipped === 1 ? "byla" : "byly"}` : "")
      : `Všechny skladby už v „${pl.name}“ jsou`);
  }

  /* Dialog „Přidat do playlistu“: výběr existujícího nebo nový podle názvu */
  let pickTracks = [];
  const pick = $("#mPick");
  function openPick(tracks, suggestName = "") {
    pickTracks = tracks.map(storable);
    $("#mPickTitle").textContent = pickTracks.length === 1 ? "Přidat „" + pickTracks[0].title + "“ do playlistu" : `Přidat ${songs(pickTracks.length)} do playlistu`;
    const list = $("#mPickList");
    list.replaceChildren();
    for (const pl of playlists) {
      const li = el("li");
      const b = el("button", "m-pick-item");
      b.type = "button";
      b.append(el("span", "m-pick-name", pl.name), el("span", "muted small", pl.tracks.length + " skl."));
      b.addEventListener("click", () => { addTracks(pl, pickTracks); pick.close(); refreshMine(); });
      li.append(b);
      list.append(li);
    }
    if (!playlists.length) list.append(el("li", "muted small m-pick-empty", "Zatím nemáš žádný playlist, založ první."));
    $("#mPickName").value = suggestName;
    pick.showModal();
    if (!playlists.length || suggestName) $("#mPickName").focus();
  }
  $("#mPickForm").addEventListener("submit", (e) => {
    e.preventDefault();
    const name = $("#mPickName").value.trim();
    if (!name) { $("#mPickName").focus(); return; }
    const pl = createPlaylist(name, pickTracks);
    pick.close();
    if (pl) L.toast(`Playlist „${pl.name}“ vytvořen (${songs(pl.tracks.length)})`);
    refreshMine();
  });
  $("#mPickClose").addEventListener("click", () => pick.close());
  pick.addEventListener("click", (e) => { if (e.target === pick) pick.close(); });

  function refreshMine() {
    if ($("#mMine").hidden) return;
    const parts = location.hash.replace(/^#\/?/, "").split("/");
    if (parts[1] === "my" && parts[2]) pageMine(decodeURIComponent(parts[2])); else pageMineList();
  }

  function playlistCover(pl) {
    const thumbs = pl.tracks.map((t) => t.thumb).filter(Boolean).slice(0, 4);
    const cover = el("span", "m-card-cover m-mosaic" + (thumbs.length >= 4 ? " four" : ""));
    if (!thumbs.length) cover.append(icon("music"));
    (thumbs.length >= 4 ? thumbs : thumbs.slice(0, 1)).forEach((src) => {
      const img = el("img");
      img.alt = "";
      img.loading = "lazy";
      img.referrerPolicy = "no-referrer";
      img.src = src;
      img.onerror = () => img.remove();
      cover.append(img);
    });
    return cover;
  }

  function pageMineList() {
    const form = el("form", "pill m-new-pl");
    form.autocomplete = "off";
    const input = el("input");
    input.type = "text";
    input.maxLength = 80;
    input.placeholder = "Název nového playlistu";
    input.setAttribute("aria-label", "Název nového playlistu");
    const btn = el("button", "btn primary", "Vytvořit");
    btn.type = "submit";
    form.append(input, btn);
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      if (!input.value.trim()) { input.focus(); return; }
      const pl = createPlaylist(input.value);
      if (pl) location.hash = "#/music/my/" + encodeURIComponent(pl.id);
    });
    const nodes = [form, el("p", "muted small m-local-note", "Playlisty se ukládají v tomhle prohlížeči. Skladby přidáš tlačítkem „Do playlistu“ u skladby, celé album nebo playlist tlačítkem „Uložit jako playlist“, frontu v panelu fronty.")];
    if (playlists.length) {
      const grid = el("div", "m-grid");
      for (const pl of playlists) {
        const a = el("a", "m-card m-playlist");
        a.href = "#/music/my/" + encodeURIComponent(pl.id);
        a.append(playlistCover(pl), el("span", "m-card-title", pl.name),
                 el("span", "m-card-sub", songs(pl.tracks.length)));
        grid.append(a);
      }
      nodes.push(grid);
    } else {
      nodes.push(el("p", "muted", "Zatím nemáš žádný playlist."));
    }
    mineBox.replaceChildren(...nodes);
  }

  function pageMine(id) {
    const pl = findPlaylist(id);
    if (!pl) {
      mineBox.replaceChildren(backButton(), el("p", "muted", "Tenhle playlist už neexistuje."));
      return;
    }
    const tracks = pl.tracks.map((t) => ({ ...t }));
    const total = tracks.reduce((s, t) => s + (t.duration || 0), 0);
    const data = {
      type: "mine", title: pl.name, thumb: tracks.map((t) => t.thumb).find(Boolean) || null,
      subtitle: [songs(tracks.length), total ? fmt(total) : null].filter(Boolean).join(" · "),
    };
    const rename = el("button", "btn", "Přejmenovat");
    rename.type = "button";
    const del = el("button", "btn m-danger", "Smazat");
    del.type = "button";
    const head = detailHeader(data, tracks, [rename, del]);
    $(".m-detail-kind", head).textContent = "Můj playlist";
    head.querySelector(".m-detail-cover").replaceWith(Object.assign(playlistCover(pl), { className: "m-detail-cover m-mosaic" + (pl.tracks.filter((t) => t.thumb).length >= 4 ? " four" : "") }));

    rename.addEventListener("click", () => {
      const title = $(".m-detail-title", head);
      const form = el("form", "m-rename");
      const input = el("input", "plain-input");
      input.value = pl.name;
      input.maxLength = 80;
      input.setAttribute("aria-label", "Nový název playlistu");
      const ok = el("button", "btn primary", "Uložit");
      ok.type = "submit";
      form.append(input, ok);
      form.addEventListener("submit", (e) => {
        e.preventDefault();
        if (!input.value.trim()) return;
        pl.name = input.value.trim().slice(0, 80);
        savePlaylists();
        pageMine(id);
      });
      title.replaceWith(form);
      input.focus();
      input.select();
    });
    del.addEventListener("click", () => {
      if (!confirm(`Smazat playlist „${pl.name}“? Skladby samotné zůstanou.`)) return;
      playlists = playlists.filter((p) => p.id !== id);
      savePlaylists();
      location.hash = "#/music/playlists";
    });

    const ul = el("ul", "m-tracks");
    tracks.forEach((t, i) => {
      const acts = [];
      if (i > 0) acts.push(["Nahoru", () => move(i, -1)]);
      if (i < tracks.length - 1) acts.push(["Dolů", () => move(i, 1)]);
      acts.push(["Odebrat", () => { pl.tracks.splice(i, 1); savePlaylists(); pageMine(id); }]);
      const row = trackRow(t, i, { onPlay: () => playList(tracks, i), actions: acts });
      if (t.src === "local") {
        row.classList.add("m-local-track");
        if (!local.some((x) => x.id === t.id)) row.title = "Soubor ze zařízení: přehraje se, až otevřeš jeho složku v záložce V zařízení.";
      }
      ul.append(row);
    });
    function move(i, dir) {
      const [t] = pl.tracks.splice(i, 1);
      pl.tracks.splice(i + dir, 0, t);
      savePlaylists();
      pageMine(id);
    }
    mineBox.replaceChildren(backButton(), head, tracks.length ? ul : el("p", "muted", "Playlist je prázdný. Skladby přidáš tlačítkem „Do playlistu“ u skladby."));
    markNow();
  }

  // změny v jiné záložce prohlížeče
  addEventListener("storage", (e) => { if (e.key === PL_KEY) { playlists = loadPlaylists(); refreshMine(); } });

  /* ---------- Směrování #/music/… ---------- */
  function setSource(src) {
    P.source = src;
    savePrefs();
    $$('input[name="msrc"]').forEach((r) => { r.checked = r.value === src; });
    $("#mOnline").hidden = src !== "online";
    $("#mLocal").hidden = src !== "local";
    $("#mMine").hidden = src !== "mine";
  }
  const SOURCE_HASH = { online: "#/music", local: "#/music/local", mine: "#/music/playlists" };
  $$('input[name="msrc"]').forEach((r) => r.addEventListener("change", () => {
    if (r.checked) setSource(r.value);
    if (r.checked) location.hash = SOURCE_HASH[r.value] || "#/music";
  }));

  let lastRoute = null;
  async function openMusicRoute(force = false) {
    const parts = location.hash.replace(/^#\/?/, "").split("/");
    if (parts[0] !== "music") return;
    const key = parts.slice(1).join("/");
    if (!force && key === lastRoute) return;
    lastRoute = key;
    if (!parts[1] && P.source !== "online" && SOURCE_HASH[P.source]) { location.replace(SOURCE_HASH[P.source]); return; }
    if (parts[1] === "local") {
      setSource("local");
      renderLocal();
      loadLibrary();  // nově stažené skladby
      return;
    }
    if (parts[1] === "playlists" || parts[1] === "my") {
      setSource("mine");
      if (parts[1] === "my" && parts[2]) pageMine(decodeURIComponent(parts[2])); else pageMineList();
      return;
    }
    setSource("online");
    const st = await loadStatus();
    if (!st.enabled) { content.replaceChildren(el("p", "muted", "Online hudba je na tomhle serveru vypnutá.")); return; }
    if (!st.online) { content.replaceChildren(); $("#mFilters").hidden = true; return; }
    const token = ++renderToken;
    try {
      if (parts[1] === "search" && parts[3] != null) await pageSearch(token, parts[2], decodeURIComponent(parts.slice(3).join("/")));
      else if (["album", "artist", "playlist"].includes(parts[1]) && parts[2]) await pageDetail(token, parts[1], decodeURIComponent(parts[2]));
      else if (parts[1] === "genre" && parts[2]) await pageGenre(token, decodeURIComponent(parts[2]), decodeURIComponent(parts[3] || ""));
      else await pageHome(token);
    } catch (err) {
      if (token === renderToken) failed(err);
    }
  }

  addEventListener("hashchange", () => openMusicRoute());
  // po zadání klíče v Nastavení se online hudba odemkne
  L.onQuota(() => {
    if (status && !status.online && L.hasKey()) { lastRoute = null; if (location.hash.startsWith("#/music")) openMusicRoute(true); }
  });

  renderToggles();
  restoreDir();
  openMusicRoute();
})();
