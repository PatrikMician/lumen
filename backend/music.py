"""Hudba: procházení hudebního katalogu YouTube Music a přehrávání zvuku přes server.

- Katalog (domovská stránka, hledání, alba, interpreti, playlisty, další skladby, texty) čte knihovna
  ytmusicapi bez přihlášení. Výsledky se krátce drží v paměti, ať server neposílá stejný dotaz pořád dokola.
- Zvuk se přes yt-dlp vybere jako nejlepší m4a (AAC hraje i Safari) a server ho proudem přeposílá
  prohlížeči, stejně jako náhled videa (odkazy jsou vázané na IP serveru, přímo z prohlížeče by nešly).
- Online hudba je ve výchozím stavu jen pro návštěvníky s přístupovým klíčem (MUSIC_REQUIRE_KEY=1),
  aby starý server nezahltil kdokoliv z internetu. Hudba ze zařízení návštěvníka se na server vůbec neposílá.

Pozor: s language="cs" ytmusicapi nenajde nic ve filtrovaném hledání (počítá s anglickými popisky),
proto se nastavuje jen location.
"""
import os
import re
import threading
import time
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import parse_qs, urlparse

import yt_dlp
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse

from . import quota

MUSIC_ENABLED = os.getenv("MUSIC", "1") == "1"
MUSIC_REQUIRE_KEY = os.getenv("MUSIC_REQUIRE_KEY", "1") == "1"
MUSIC_LOCATION = os.getenv("MUSIC_LOCATION", "CZ").strip().upper() or "CZ"
MUSIC_BROWSE_PER_HOUR = int(os.getenv("MUSIC_BROWSE_PER_HOUR", "300"))  # dotazů do katalogu na prohlížeč za hodinu
MUSIC_TRACKS_PER_HOUR = int(os.getenv("MUSIC_TRACKS_PER_HOUR", "120"))  # spuštěných skladeb na prohlížeč za hodinu
CACHE_TTL = 15 * 60
CACHE_MAX = 400
ID_RE = re.compile(r"[\w-]{5,80}")
AUDIO_FORMAT = "bestaudio[ext=m4a]/bestaudio[acodec^=mp4a]/bestaudio"

router = APIRouter()

# Funkce z main.py (předá je configure(), ať se moduly navzájem neimportují dokola).
_deps: dict = {}


def configure(*, rate_check: Callable, current_key: Callable, base_opts: Callable, is_public_host: Callable,
              friendly: Callable, daily_limit: int, open_media: Callable, stream_media: Callable,
              media_status: Callable, media_ip: Callable, upstream_error: int):
    _deps.update(rate_check=rate_check, current_key=current_key, base_opts=base_opts,
                 is_public_host=is_public_host, friendly=friendly, daily_limit=daily_limit,
                 open_media=open_media, stream_media=stream_media, media_status=media_status,
                 media_ip=media_ip, upstream_error=upstream_error)


# --------------------------------------------------------------------------
# Přístup
# --------------------------------------------------------------------------
def online_allowed(request: Request) -> tuple:
    """(smí online hudbu, důvod když ne)."""
    if not MUSIC_ENABLED:
        return False, "disabled"
    if MUSIC_REQUIRE_KEY and _deps["daily_limit"] > 0 and not _deps["current_key"](request):
        return False, "key"
    return True, None


def need_online(request: Request, kind: str = "music"):
    ok, why = online_allowed(request)
    if why == "disabled":
        raise HTTPException(404, "Hudba je na tomhle serveru vypnutá.")
    if not ok:
        raise HTTPException(403, "Online hudba je jen s přístupovým klíčem. Požádat o něj můžeš v Nastavení.")
    limit = MUSIC_BROWSE_PER_HOUR if kind == "music" else MUSIC_TRACKS_PER_HOUR
    _deps["rate_check"](kind, request.state.sid, limit, 3600, "Moc rychle za sebou. Zkus to za chvíli.")


def check_id(value: str) -> str:
    if not ID_RE.fullmatch(value or ""):
        raise HTTPException(400, "Neplatný odkaz na hudbu.")
    return value


# --------------------------------------------------------------------------
# Katalog (ytmusicapi)
# --------------------------------------------------------------------------
_local = threading.local()
_cache: dict = {}
_cache_lock = threading.Lock()


def _ytm():
    if not hasattr(_local, "ytm"):
        from ytmusicapi import YTMusic

        _local.ytm = YTMusic(location=MUSIC_LOCATION)
    return _local.ytm


def cached(key: tuple, fn: Callable):
    now = time.time()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and hit[0] > now:
            return hit[1]
    try:
        data = fn(_ytm())
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - síť, změna formátu YouTube Music, neznámé ID
        print(f"Hudba: {key[0]} selhalo: {type(exc).__name__}: {exc}", flush=True)
        raise HTTPException(502, "Hudební katalog teď nejde načíst. Zkus to za chvíli.")
    with _cache_lock:
        if len(_cache) >= CACHE_MAX:
            for k in sorted(_cache, key=lambda k: _cache[k][0])[: CACHE_MAX // 4]:
                _cache.pop(k, None)
        _cache[key] = (now + CACHE_TTL, data)
    return data


def _thumb(thumbs, size: int = 544) -> Optional[str]:
    """Největší miniatura; u obalů z googleusercontent si řekneme rovnou o větší rozměr."""
    if not thumbs:
        return None
    if isinstance(thumbs, dict):
        thumbs = thumbs.get("thumbnails") or []
    best = max(thumbs, key=lambda t: t.get("width") or 0).get("url") if thumbs else None
    if best and "googleusercontent.com" in best:
        best = re.sub(r"=w\d+-h\d+", f"=w{size}-h{size}", best)
    return best


def _seconds(text) -> Optional[int]:
    if isinstance(text, (int, float)):
        return int(text)
    if not text or not isinstance(text, str) or not re.fullmatch(r"\d+(:\d{1,2}){1,2}", text.strip()):
        return None
    total = 0
    for part in text.strip().split(":"):
        total = total * 60 + int(part)
    return total


def _cz(text) -> Optional[str]:
    """Popisky z katalogu jsou anglicky (jazyk nejde přepnout, viz výše): „1 hour, 14 minutes“ → „1 h 14 min“."""
    if not text or not isinstance(text, str):
        return text
    text = re.sub(r"(\d+)\+? hours?", r"\1 h", text)
    text = re.sub(r"(\d+)\+? minutes?", r"\1 min", text)
    text = re.sub(r"(\d+)\+? seconds?", r"\1 s", text)
    text = re.sub(r"(\d+) songs?", r"\1 skladeb", text)
    text = text.replace(" h, ", " h ")
    return {"Single": "Singl", "Song": "Skladba", "Video": "Video", "Playlist": "Playlist"}.get(text, text)


def _artists(item) -> list:
    out = []
    for a in item.get("artists") or []:
        if isinstance(a, dict) and a.get("name"):
            out.append({"name": a["name"], "id": a.get("id")})
    if not out and item.get("artist"):
        out.append({"name": item["artist"], "id": item.get("browseId")})
    return out


def track(item: dict, album: dict = None, cover: str = None) -> Optional[dict]:
    """Skladba ve tvaru, se kterým počítá přehrávač. Bez videoId (nedostupná skladba) vrací None."""
    vid = item.get("videoId")
    if not vid or item.get("isAvailable") is False:
        return None
    alb = item.get("album") if isinstance(item.get("album"), dict) else None
    return {
        "type": "song",
        "id": vid,
        "title": item.get("title") or "Bez názvu",
        "artists": _artists(item),
        "album": {"name": alb.get("name"), "id": alb.get("id")} if alb else album,
        "duration": item.get("duration_seconds") or _seconds(item.get("duration") or item.get("length")),
        "thumb": _thumb(item.get("thumbnails") or item.get("thumbnail")) or cover,
        "explicit": bool(item.get("isExplicit")),
    }


def card(item: dict) -> Optional[dict]:
    """Položka katalogu (skladba, video, album, interpret, playlist) pro mřížku a výsledky hledání."""
    kind = (item.get("resultType") or "").lower()
    if item.get("videoId") and kind in ("", "song", "video"):
        t = track(item)
        if t and kind == "video":
            t["type"] = "video"
        return t
    thumb = _thumb(item.get("thumbnails"))
    if kind == "artist" or (not kind and str(item.get("browseId", "")).startswith("UC") and not item.get("title")):
        bid = item.get("browseId") or (item.get("artists") or [{}])[0].get("id")
        name = item.get("artist") or item.get("title") or (item.get("artists") or [{}])[0].get("name")
        return {"type": "artist", "id": bid, "title": name, "thumb": thumb,
                "subtitle": item.get("subscribers") or ""} if bid and name else None
    bid = item.get("browseId") or ""
    if kind == "album" or bid.startswith("MPRE"):
        sub = " · ".join(x for x in [_cz(item.get("type")) if kind else None, ", ".join(a["name"] for a in _artists(item)),
                                     item.get("year")] if x)
        return {"type": "album", "id": bid, "title": item.get("title"), "thumb": thumb, "subtitle": sub}
    pid = item.get("playlistId") or bid
    if kind == "playlist" or pid:
        author = item.get("author")
        if isinstance(author, list):
            author = ", ".join(a.get("name", "") for a in author if isinstance(a, dict))
        sub = author or item.get("description") or (f"{item.get('itemCount')} skladeb" if item.get("itemCount") else "")
        return {"type": "playlist", "id": pid, "title": item.get("title"), "thumb": thumb, "subtitle": sub or ""} if pid else None
    return None


def cards(items) -> list:
    return [c for c in (card(i) for i in items or []) if c and c.get("id") and c.get("title")]


def tracks(items, album: dict = None, cover: str = None) -> list:
    return [t for t in (track(i, album, cover) for i in items or []) if t]


HOME_TITLES = {
    "quick picks": "Rychlý výběr", "all hits": "Hity", "today's biggest hits": "Největší hity dneška",
    "new releases": "Novinky", "trending songs for you": "Trendy pro tebe", "long listening": "Na dlouhé poslouchání",
    "recommended music videos": "Doporučené klipy", "charts": "Žebříčky", "mixed for you": "Mixy pro tebe",
    "from the community": "Od komunity", "moods & genres": "Nálady a žánry", "albums for you": "Alba pro tebe",
}
SEARCH_FILTERS = {"songs", "albums", "artists", "playlists", "videos"}


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------
@router.get("/api/music/status")
def music_status(request: Request):
    ok, why = online_allowed(request)
    return {"enabled": MUSIC_ENABLED, "online": ok, "reason": why}


@router.get("/api/music/home")
def music_home(request: Request):
    need_online(request)

    def load(y):
        sections = []
        for s in y.get_home(limit=8):
            items = cards(s.get("contents"))
            if items:
                title = s.get("title") or ""
                sections.append({"title": HOME_TITLES.get(title.lower(), title), "items": items[:20]})
        return {"sections": sections}

    return cached(("home",), load)


@router.get("/api/music/search")
def music_search(request: Request, q: str, type: str = "all"):
    need_online(request)
    q = (q or "").strip()[:200]
    if not q:
        raise HTTPException(400, "Napiš, co chceš najít.")
    if type in SEARCH_FILTERS:
        return cached(("search", type, q.lower()), lambda y: {"items": cards(y.search(q, filter=type, limit=40))})
    if type != "all":
        raise HTTPException(400, "Neznámý druh hledání.")

    def load(y):
        groups = {"top": [], "song": [], "album": [], "artist": [], "playlist": [], "video": []}
        for item in y.search(q, limit=20):
            c = card(item)
            if not c or not c.get("id") or not c.get("title"):
                continue
            if item.get("category") == "Top result" and not groups["top"]:
                groups["top"].append(c)
            elif c["type"] in groups:
                groups[c["type"]].append(c)
        # v nefiltrovaném hledání ytmusicapi u části skladeb nevyčte interpreta ani délku,
        # skladby proto bereme z hledání jen skladeb (úplná data)
        try:
            songs = cards(y.search(q, filter="songs", limit=10))[:8]
        except Exception:  # noqa: BLE001 - když druhé hledání selže, zůstanou neúplné skladby
            songs = []
        if songs:
            groups["song"] = songs
        names = [("top", "Nejlepší výsledek"), ("song", "Skladby"), ("album", "Alba"), ("artist", "Interpreti"),
                 ("playlist", "Playlisty"), ("video", "Videa")]
        return {"sections": [{"title": title, "kind": k, "items": groups[k]} for k, title in names if groups[k]]}

    return cached(("search", "all", q.lower()), load)


@router.get("/api/music/album/{album_id}")
def music_album(request: Request, album_id: str):
    need_online(request)
    check_id(album_id)

    def load(y):
        a = y.get_album(album_id)
        cover = _thumb(a.get("thumbnails"))
        ref = {"name": a.get("title"), "id": album_id}
        artists = ", ".join(x["name"] for x in _artists(a))
        return {"type": "album", "id": album_id, "title": a.get("title"), "thumb": cover,
                "subtitle": " · ".join(x for x in [_cz(a.get("type")), artists, a.get("year"), _cz(a.get("duration"))] if x),
                "tracks": tracks(a.get("tracks"), ref, cover)}

    return cached(("album", album_id), load)


@router.get("/api/music/artist/{artist_id}")
def music_artist(request: Request, artist_id: str):
    need_online(request)
    check_id(artist_id)

    def load(y):
        a = y.get_artist(artist_id)
        songs = a.get("songs") or {}
        shelves = []
        for key, title in (("albums", "Alba"), ("singles", "Singly a EP"), ("videos", "Videa"), ("related", "Podobní interpreti")):
            items = cards([{**i, "resultType": {"albums": "album", "singles": "album", "videos": "video",
                                                "related": "artist"}[key]} for i in (a.get(key) or {}).get("results", [])])
            if items:
                shelves.append({"title": title, "items": items})
        desc = (a.get("description") or "").strip()
        return {"type": "artist", "id": artist_id, "title": a.get("name"), "thumb": _thumb(a.get("thumbnails")),
                "subtitle": a.get("monthlyListeners") or a.get("subscribers") or "",
                "description": desc[:600] + ("…" if len(desc) > 600 else ""),
                "tracks": tracks(songs.get("results")), "all_songs": songs.get("browseId"),
                "radio": a.get("radioId"), "sections": shelves}

    return cached(("artist", artist_id), load)


@router.get("/api/music/playlist/{playlist_id}")
def music_playlist(request: Request, playlist_id: str):
    need_online(request)
    check_id(playlist_id)

    def load(y):
        p = y.get_playlist(playlist_id, limit=200)
        author = p.get("author")
        if isinstance(author, dict):
            author = author.get("name")
        count = p.get("trackCount")
        return {"type": "playlist", "id": playlist_id, "title": p.get("title"), "thumb": _thumb(p.get("thumbnails")),
                "subtitle": " · ".join(str(x) for x in [author, f"{count} skladeb" if count else None, _cz(p.get("duration"))] if x),
                "tracks": tracks(p.get("tracks"))}

    return cached(("playlist", playlist_id), load)


@router.get("/api/music/next/{video_id}")
def music_next(request: Request, video_id: str, playlist: Optional[str] = None):
    """Další skladby (mix podle skladby, nebo pokračování playlistu) a odkaz na text písně."""
    need_online(request)
    check_id(video_id)
    if playlist:
        check_id(playlist)

    def load(y):
        w = y.get_watch_playlist(videoId=video_id, playlistId=playlist, limit=30, radio=not playlist)
        return {"tracks": tracks(w.get("tracks")), "lyrics": w.get("lyrics")}

    return cached(("next", video_id, playlist), load)


@router.get("/api/music/lyrics/{browse_id}")
def music_lyrics(request: Request, browse_id: str):
    need_online(request)
    check_id(browse_id)

    def load(y):
        ly = y.get_lyrics(browse_id) or {}
        text = ly.get("lyrics")
        if isinstance(text, list):  # časované texty
            text = "\n".join(getattr(line, "text", "") or (line.get("text", "") if isinstance(line, dict) else "") for line in text)
        return {"text": text or "", "source": ly.get("source") or ""}

    return cached(("lyrics", browse_id), load)


GENRE_NAMES = {
    # nálady a chvíle
    "chill": "Pohoda", "christmas": "Vánoce", "commute": "Na cestu", "energize": "Energie", "feel good": "Dobrá nálada",
    "focus": "Soustředění", "gaming": "Hraní", "party": "Párty", "romance": "Romantika", "sad": "Smutné",
    "sleep": "Spánek", "workout": "Cvičení",
    # žánry
    "african": "Africká", "arabic": "Arabská", "autumn": "Podzim", "classical": "Klasická", "country & americana": "Country",
    "dance & electronic": "Dance a elektronika", "decades": "Desetiletí", "family": "Pro rodinu",
    "folk & acoustic": "Folk a akustická", "indie & alternative": "Indie a alternativa", "jazz": "Jazz",
    "latin": "Latino", "metal": "Metal", "pop": "Pop", "r&b & soul": "R&B a soul", "reggae & caribbean": "Reggae",
    "rock": "Rock", "soundtracks & musicals": "Soundtracky a muzikály", "korean hip-hop": "Korejský hip-hop",
    "mandopop & cantopop": "Mandopop a cantopop", "hip-hop": "Hip-hop", "blues": "Blues",
    "bollywood & indian": "Bollywood a indická",
}
GENRE_SECTIONS = {"genres": "Žánry", "moods & moments": "Nálady a chvíle"}
PARAMS_RE = re.compile(r"[\w=-]{6,200}")


@router.get("/api/music/genres")
def music_genres(request: Request):
    """Žánry a nálady z katalogu; každá položka nese `params` pro /api/music/genre."""
    need_online(request)

    def load(y):
        sections = []
        for title, items in y.get_mood_categories().items():
            entries = [{"title": GENRE_NAMES.get(i["title"].lower(), i["title"]), "params": i["params"]}
                       for i in items if i.get("params") and i.get("title")]
            if entries:
                sections.append({"title": GENRE_SECTIONS.get(title.lower(), title), "items": entries})
        sections.sort(key=lambda s: s["title"] != "Žánry")  # žánry první
        return {"sections": sections}

    return cached(("genres",), load)


def _mood_playlists(y, params: str) -> list:
    """Playlisty žánru. ytmusicapi.get_mood_playlists spadne na jediné položce bez odkazu (třeba
    u žánru Rock, ověřeno s 1.12.3), proto se odpověď projde sama a takové položky se přeskočí."""
    try:
        return y.get_mood_playlists(params)
    except Exception:  # noqa: BLE001
        pass
    response = y._send_request("browse", {"browseId": "FEmusic_moods_and_genres_category", "params": params})
    found, seen = [], set()

    def walk(node):
        if isinstance(node, dict):
            item = node.get("musicTwoRowItemRenderer")
            if item:
                runs = (item.get("title") or {}).get("runs") or [{}]
                endpoint = (item.get("navigationEndpoint") or runs[0].get("navigationEndpoint") or {}).get("browseEndpoint") or {}
                bid = endpoint.get("browseId", "")
                if bid and bid not in seen and runs[0].get("text"):
                    seen.add(bid)
                    thumbs = (((item.get("thumbnailRenderer") or {}).get("musicThumbnailRenderer") or {})
                              .get("thumbnail") or {}).get("thumbnails") or []
                    sub = "".join(r.get("text", "") for r in (item.get("subtitle") or {}).get("runs") or [])
                    found.append({"title": runs[0]["text"], "playlistId": bid[2:] if bid.startswith("VL") else bid,
                                  "thumbnails": thumbs, "description": sub})
                return
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(response)
    return found


@router.get("/api/music/genre")
def music_genre(request: Request, params: str):
    """Playlisty jednoho žánru nebo nálady."""
    need_online(request)
    if not PARAMS_RE.fullmatch(params or ""):
        raise HTTPException(400, "Neplatný odkaz na hudbu.")
    return cached(("genre", params), lambda y: {"items": cards(_mood_playlists(y, params))[:80]})


# --------------------------------------------------------------------------
# Přehrávání zvuku
# --------------------------------------------------------------------------
_streams: dict = {}
_streams_lock = threading.Lock()


def resolve_stream(video_id: str, ipv4: bool = False) -> dict:
    now = time.time()
    with _streams_lock:
        for k in [k for k, v in _streams.items() if v["expires"] < now]:
            _streams.pop(k, None)
        hit = _streams.get(video_id)
    if hit and (not ipv4 or hit.get("ipv4")):
        return hit
    data, media, opts, last_error = None, None, None, "zvuk se nenašel"
    # 1) YouTube Music s m4a (hraje všude), 2) obyčejný YouTube s jakýmkoliv zvukem, kdyby Music
    # klient na serveru formáty nevrátil (třeba kvůli omezení IP nebo změně na straně YouTube)
    for url, fmt in ((f"https://music.youtube.com/watch?v={video_id}", AUDIO_FORMAT),
                     (f"https://www.youtube.com/watch?v={video_id}", AUDIO_FORMAT + "/best")):
        opts = _deps["base_opts"](url=url)
        if ipv4:
            opts["source_address"] = "0.0.0.0"
        opts.update(skip_download=True, format=fmt)
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                data = ydl.extract_info(url, download=False)
        except Exception as e:  # noqa: BLE001
            last_error = _deps["friendly"](e)
            print(f"Hudba: stream {video_id} z {url.split('/')[2]} selhal: {last_error}", flush=True)
            continue
        media = (data or {}).get("url")
        host = urlparse(media).hostname if media else None
        if data and not data.get("is_live") and media and host and _deps["is_public_host"](host):
            break
        media = None
        last_error = "zvuk se nenašel"
    if not media:
        raise HTTPException(422, "Skladbu se nepodařilo přehrát: " + last_error)
    try:
        expire = int(parse_qs(urlparse(media).query).get("expire", ["0"])[0])
    except ValueError:
        expire = 0
    ttl = min(5 * 3600, expire - now - 300) if expire > now + 600 else 30 * 60
    info = {"url": media, "headers": data.get("http_headers") or {}, "expires": now + ttl, "opts": opts, "ipv4": ipv4,
            "type": "audio/mp4" if data.get("ext") == "m4a" else f"audio/{data.get('ext') or 'webm'}"}
    with _streams_lock:
        _streams[video_id] = info
    return info


# --------------------------------------------------------------------------
# Záloha: stažení skladby přes yt-dlp do mezipaměti na disku
# --------------------------------------------------------------------------
# Na serveru googlevideo pustí přímé čtení samotné zvukové stopy jen na začátek a pak vrací 403
# (ověřeno na mican-server 2026-10-15, IP i síť nehrály roli), stahování přes yt-dlp ale funguje.
# Skladba se proto stáhne celá (jako běžné stažení v Lumenu) a pošle se ze souboru i s přetáčením.
CACHE_DIR = Path(os.getenv("MUSIC_CACHE_DIR", "") or quota.DATA_DIR / "music-cache")
CACHE_MB = int(os.getenv("MUSIC_CACHE_MB", "1024"))
CACHE_TYPES = {".m4a": "audio/mp4", ".mp4": "audio/mp4", ".webm": "audio/webm", ".opus": "audio/ogg", ".mp3": "audio/mpeg"}
_use_download = os.getenv("MUSIC_DIRECT", "1") != "1"  # po prvním 403 rovnou přes mezipaměť
_dl_locks: dict = {}
_dl_locks_guard = threading.Lock()


def cached_file(video_id: str) -> Optional[Path]:
    for f in CACHE_DIR.glob(f"{video_id}.*"):
        if f.suffix.lower() in CACHE_TYPES and f.stat().st_size > 0:
            return f
    return None


def prune_cache():
    files = sorted((f for f in CACHE_DIR.glob("*") if f.is_file()), key=lambda f: f.stat().st_mtime)
    total = sum(f.stat().st_size for f in files)
    while files and total > CACHE_MB * 1024 * 1024:
        f = files.pop(0)
        total -= f.stat().st_size
        f.unlink(missing_ok=True)


_downloads: dict = {}  # video_id -> {"done": Event, "error": str | None, "started": float}


def _download_worker(video_id: str, state: dict):
    last_error = "zvuk se nenašel"
    for url in (f"https://music.youtube.com/watch?v={video_id}", f"https://www.youtube.com/watch?v={video_id}"):
        opts = _deps["base_opts"](url=url)
        # fixup=never: soubor zůstane bajt po bajtu stejný jako během stahování, takže se dá přehrávat
        # už rozstahovaný (.part) a pak dál z hotového souboru se stejnými pozicemi
        opts.update(format=AUDIO_FORMAT, outtmpl=str(CACHE_DIR / f"{video_id}.%(ext)s"), noprogress=True,
                    fixup="never", nopart=False)
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.extract_info(url, download=True)
        except Exception as e:  # noqa: BLE001
            last_error = _deps["friendly"](e)
            print(f"Hudba: stažení {video_id} z {url.split('/')[2]} selhalo: {last_error}", flush=True)
            for f in CACHE_DIR.glob(f"{video_id}.*"):
                f.unlink(missing_ok=True)
            continue
        hit = cached_file(video_id)
        if hit:
            print(f"Hudba: {video_id} stažena do mezipaměti za {time.time() - state['started']:.1f} s "
                  f"({hit.stat().st_size // 1024} kB)", flush=True)
            prune_cache()
            state["done"].set()
            return
    state["error"] = last_error
    state["done"].set()


def start_download(video_id: str) -> dict:
    """Spustí stažení na pozadí (jen jednou pro skladbu) a vrátí jeho stav."""
    with _dl_locks_guard:
        state = _downloads.get(video_id)
        if state and not (state["done"].is_set() and (state["error"] or not cached_file(video_id))):
            return state
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        state = {"done": threading.Event(), "error": None, "started": time.time()}
        _downloads[video_id] = state
    threading.Thread(target=_download_worker, args=(video_id, state), daemon=True).start()
    return state


def _part_file(video_id: str) -> Optional[Path]:
    for f in CACHE_DIR.glob(f"{video_id}.*.part"):
        return f
    return None


def follow_download(video_id: str, state: dict):
    """Posílá rozstahovaný soubor, jak roste, až do konce stahování (prohlížeč hraje hned)."""
    pos, idle = 0, 0.0
    while True:
        path = cached_file(video_id) or _part_file(video_id)
        chunk = b""
        if path:
            try:
                with open(path, "rb") as f:  # otevírá se pokaždé znovu: yt-dlp soubor na konci přejmenuje
                    f.seek(pos)
                    chunk = f.read(256 * 1024)
            except OSError:
                chunk = b""
        if chunk:
            pos += len(chunk)
            idle = 0.0
            yield chunk
            continue
        final = cached_file(video_id)
        if state["done"].is_set() and (not final or pos >= final.stat().st_size):
            return
        idle += 0.2
        if idle > 60:
            return
        time.sleep(0.2)


def serve_cached(path: Path) -> FileResponse:
    path.touch()  # mezipaměť maže nejdéle nehrané
    return FileResponse(path, media_type=CACHE_TYPES.get(path.suffix.lower(), "audio/mp4"),
                        headers={"Cache-Control": "private, max-age=3600"})


PREPARE_MAX = int(os.getenv("MUSIC_PREPARE_MAX", "3"))  # kolik skladeb se smí připravovat najednou (starý server)


def _preparing() -> int:
    return sum(1 for st in list(_downloads.values()) if not st["done"].is_set())


@router.post("/api/music/prepare/{video_id}")
def music_prepare(request: Request, video_id: str):
    """Připraví skladbu předem (po najetí myší, otevření alba, další ve frontě), ať po kliknutí hraje hned.
    Nic neposílá: v režimu mezipaměti rozjede stažení na pozadí, jinak jen zjistí adresu streamu."""
    check_id(video_id)
    need_online(request)  # počítá se jako dotaz do katalogu, ne jako spuštěná skladba
    if cached_file(video_id):
        return {"state": "ready"}
    if _use_download:
        state = _downloads.get(video_id)
        if state and not state["done"].is_set():
            return {"state": "preparing"}
        if _preparing() >= PREPARE_MAX:
            return {"state": "busy"}
        start_download(video_id)
        return {"state": "preparing"}
    try:
        resolve_stream(video_id)
    except HTTPException:
        return {"state": "failed"}
    return {"state": "ready"}


@router.get("/api/music/stream/{video_id}")
def music_stream(request: Request, video_id: str):
    global _use_download
    check_id(video_id)
    rng = request.headers.get("range")
    # do limitu se počítá spuštění skladby, ne každý kousek, o který si přehrávač řekne při přetáčení
    need_online(request, "music_track" if not rng or rng.strip() in ("bytes=0-", "bytes=0-1") else "music")
    hit = cached_file(video_id)
    if hit:
        return serve_cached(hit)
    if not _use_download:
        info = resolve_stream(video_id)
        try:
            ydl, upstream = _deps["open_media"](info, rng)
            return _deps["stream_media"](ydl, upstream, info["type"], {"Cache-Control": "private, max-age=3600"})
        except Exception as e:  # noqa: BLE001
            with _streams_lock:
                _streams.pop(video_id, None)
            status = _deps["media_status"](e)
            if status == 416:
                raise HTTPException(416, "Mimo rozsah skladby.")
            _use_download = True
            print(f"Hudba: přímé čtení {video_id} vrátilo {status or type(e).__name__} "
                  f"(ip={_deps['media_ip'](info['url'])}), dál přes stažení do mezipaměti", flush=True)
    state = start_download(video_id)
    # počkat na začátek souboru (nebo na konec stažení / chybu), pak hned posílat
    deadline = time.time() + 45
    while time.time() < deadline and not state["done"].is_set():
        part = _part_file(video_id)
        if part and part.stat().st_size >= 64 * 1024:
            break
        time.sleep(0.2)
    if state["error"]:
        raise HTTPException(422, "Skladbu se nepodařilo přehrát: " + state["error"])
    hit = cached_file(video_id)
    if hit:
        return serve_cached(hit)
    start = re.fullmatch(r"\s*bytes=(\d+)-\d*\s*", rng or "")
    if start and int(start.group(1)) > 0:
        # přetáčení během stahování: počká se na celý soubor
        state["done"].wait(120)
        hit = cached_file(video_id)
        if not hit:
            raise HTTPException(422, "Skladbu se nepodařilo přehrát: " + (state["error"] or "stažení nedoběhlo"))
        return serve_cached(hit)
    if not _part_file(video_id) and not state["done"].is_set():
        raise HTTPException(_deps["upstream_error"], "Skladbu se nepodařilo načíst: stahování se nerozběhlo.")
    return StreamingResponse(follow_download(video_id, state), media_type="audio/mp4",
                             headers={"Cache-Control": "no-store"})
