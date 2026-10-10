"""Lumen: malý webový stahovač médií postavený nad yt-dlp a ffmpeg.

Spuštění:  python run.py   (viz README.md)
"""
import base64
import ipaddress
import os
import re
import secrets
import shutil
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import yt_dlp
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from yt_dlp.version import __version__ as YTDLP_VERSION

from . import keyrequests, quota

# --------------------------------------------------------------------------
# Nastavení (jde měnit proměnnými prostředí, viz README)
# --------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"
DOWNLOAD_DIR = Path(os.getenv("DOWNLOAD_DIR", BASE_DIR / "downloads")).resolve()
MAX_PARALLEL = int(os.getenv("MAX_PARALLEL", "2"))
KEEP_HOURS = float(os.getenv("KEEP_HOURS", "24"))
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "2048"))
COOKIES_FILE = os.getenv("COOKIES_FILE", "")
AUTH_USER = os.getenv("AUTH_USER", "lumen")
AUTH_PASSWORD = os.getenv("AUTH_PASSWORD", "")
# Limity proti zahlcení (0 = bez limitu)
MAX_ACTIVE_PER_USER = int(os.getenv("MAX_ACTIVE_PER_USER", "2"))  # souběžně rozdělaných úloh na prohlížeč
MAX_QUEUE = int(os.getenv("MAX_QUEUE", "20"))  # celkem čekajících + běžících úloh na serveru
DAILY_LIMIT = int(os.getenv("DAILY_LIMIT", "5"))  # stažení za 24 h na návštěvníka bez přístupového klíče (0 = bez limitu)
INFO_PER_HOUR = int(os.getenv("INFO_PER_HOUR", "60"))  # načtení náhledu na prohlížeč za hodinu
MAX_DURATION_MIN = int(os.getenv("MAX_DURATION_MIN", "120"))  # nejdelší povolené video v minutách
ALLOW_GENERIC = os.getenv("ALLOW_GENERIC", "0") == "1"  # obecný extraktor (libovolná stránka)

BROWSERS = {"chrome", "firefox", "edge", "brave", "opera", "safari", "vivaldi", "chromium"}
AUDIO_FORMATS = {"mp3", "m4a", "opus", "flac", "wav"}
VIDEO_FORMATS = {"mp4", "mkv", "webm"}
REMUX_VIDEO = {"mp4", "mkv", "webm", "mov"}
ACTIVE = ("queued", "downloading", "processing")
SKIP_SUFFIXES = {".part", ".ytdl", ".temp", ".tmp", ".jpg", ".jpeg", ".png", ".webp", ".json", ".vtt", ".srt"}
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _find_ffmpeg():
    """Vrátí (cesta, je_v_PATH). Když ffmpeg není nainstalovaný, zkusí balík imageio-ffmpeg."""
    path = shutil.which("ffmpeg")
    if path:
        return path, True
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe(), False
    except Exception:
        return None, False


FFMPEG, FFMPEG_IN_PATH = _find_ffmpeg()

DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Lumen", docs_url=None, redoc_url=None)

# --------------------------------------------------------------------------
# Volitelné heslo (HTTP Basic). Zapne se nastavením AUTH_PASSWORD.
# --------------------------------------------------------------------------
@app.middleware("http")
async def basic_auth(request: Request, call_next):
    if not AUTH_PASSWORD:
        return await call_next(request)
    header = request.headers.get("authorization", "")
    if header.lower().startswith("basic "):
        try:
            user, _, password = base64.b64decode(header[6:]).decode("utf-8").partition(":")
            if secrets.compare_digest(user.encode(), AUTH_USER.encode()) and secrets.compare_digest(
                password.encode(), AUTH_PASSWORD.encode()
            ):
                return await call_next(request)
        except Exception:
            pass
    return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="lumen"'})


# --------------------------------------------------------------------------
# Oddělení uživatelů: každý prohlížeč dostane cookie s náhodným ID a vidí
# jen své vlastní úlohy.
# --------------------------------------------------------------------------
SID_RE = re.compile(r"[0-9a-f]{32}")


@app.middleware("http")
async def session_cookie(request: Request, call_next):
    sid = request.cookies.get("lumen_sid", "")
    is_new = not SID_RE.fullmatch(sid)
    if is_new:
        sid = uuid.uuid4().hex
    request.state.sid = sid
    response = await call_next(request)
    if is_new:
        response.set_cookie(
            "lumen_sid", sid, max_age=60 * 60 * 24 * 365, httponly=True, samesite="lax",
            secure=request.headers.get("x-forwarded-proto", request.url.scheme) == "https",
        )
    return response


def own_job(request: Request, jid: str):
    """Vrátí úlohu, jen pokud patří tomuto prohlížeči, jinak 404."""
    job = jobs.get(jid)
    if not job or job.get("owner") != request.state.sid:
        raise HTTPException(404, "Úloha neexistuje.")
    return job


def save_owner(jid: str, sid: str):
    d = DOWNLOAD_DIR / jid
    d.mkdir(parents=True, exist_ok=True)
    (d / ".owner").write_text(sid)


# --------------------------------------------------------------------------
# Limity a kontrola vstupu
# --------------------------------------------------------------------------
_history: dict = {"download": {}, "info": {}, "key": {}, "request": {}}
_history_lock = threading.Lock()


def rate_check(kind: str, sid: str, limit: int, window: float, msg: str):
    """Posuvné okno: povolí akci, pokud jich prohlížeč za `window` sekund neudělal víc než `limit`."""
    if limit <= 0:
        return
    now = time.time()
    with _history_lock:
        stamps = [t for t in _history[kind].get(sid, []) if t > now - window]
        if len(stamps) >= limit:
            _history[kind][sid] = stamps
            raise HTTPException(429, msg)
        stamps.append(now)
        _history[kind][sid] = stamps


KEY_COOKIE = "lumen_key"


def current_key(request: Request):
    """Záznam přístupového klíče z cookie, pokud platí a je přiřazený k tomuhle zařízení (prohlížeči).
    Zrušený nebo uvolněný klíč, případně klíč zkopírovaný na jiné zařízení, neplatí."""
    rec, _ = quota.get_key(request.cookies.get(KEY_COOKIE, ""), request.state.sid)
    return rec


def fmt_wait(sec) -> str:
    sec = int(sec or 0)
    h, m = sec // 3600, (sec % 3600 + 59) // 60
    if m == 60:
        h, m = h + 1, 0
    if h and m:
        return f"{h} h {m} min"
    return f"{h} h" if h else f"{max(m, 1)} min"


def check_capacity(sid: str):
    active = [j for j in jobs.values() if j["status"] in ACTIVE]
    if MAX_QUEUE > 0 and len(active) >= MAX_QUEUE:
        raise HTTPException(503, "Server je teď plně vytížený. Zkus to prosím za chvíli.")
    if MAX_ACTIVE_PER_USER > 0 and sum(1 for j in active if j.get("owner") == sid) >= MAX_ACTIVE_PER_USER:
        raise HTTPException(429, f"Můžeš mít rozdělaných nejvýš {MAX_ACTIVE_PER_USER} úlohy najednou. Počkej, až některá doběhne.")


def is_public_host(host: str) -> bool:
    """Odmítne adresy, které míří do domácí/interní sítě (ochrana proti SSRF)."""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if not ip.is_global:
            return False
    return True


def video_filter(state: dict):
    def _filter(info, *, incomplete=False):
        if info.get("is_live"):
            state["reject"] = "Živé přenosy nejsou podporované."
            return state["reject"]
        dur = info.get("duration")
        if MAX_DURATION_MIN > 0 and dur and dur > MAX_DURATION_MIN * 60:
            state["reject"] = f"Video je delší než {MAX_DURATION_MIN} minut, to server nestáhne."
            return state["reject"]
        return None

    return _filter


# --------------------------------------------------------------------------
# Úlohy (fronta stahování a převodů)
# --------------------------------------------------------------------------
jobs: dict = {}
files: dict = {}
cancelled: set = set()
executor = ThreadPoolExecutor(max_workers=MAX_PARALLEL)


def new_job(kind, title, thumbnail=None, jid=None, **extra):
    jid = jid or uuid.uuid4().hex[:12]
    job = {
        "id": jid,
        "kind": kind,
        "title": (title or "Bez názvu")[:300],
        "thumbnail": thumbnail,
        "status": "queued",
        "stage": "Čeká ve frontě",
        "percent": 0.0,
        "speed": None,
        "eta": None,
        "filename": None,
        "size": None,
        "error": None,
        "mode": None,
        "created": time.time(),
    }
    job.update(extra)
    jobs[jid] = job
    return job


def pick_file(folder: Path):
    candidates = [p for p in folder.iterdir() if p.is_file() and not p.name.startswith(".") and p.suffix.lower() not in SKIP_SUFFIXES]
    return max(candidates, key=lambda p: p.stat().st_size) if candidates else None


def friendly(err) -> str:
    msg = ANSI.sub("", str(err)).strip()
    msg = re.sub(r"^ERROR:\s*", "", msg)
    return msg[:400] or "Neznámá chyba."


def clean_url(raw: str) -> str:
    url = (raw or "").strip()
    # Povoleno jen http(s): yt-dlp by jinak uměl číst i lokální soubory (file://).
    if not re.match(r"^https?://", url, re.I):
        raise HTTPException(400, "Odkaz musí začínat http:// nebo https://")
    if len(url) > 2000:
        raise HTTPException(400, "Odkaz je příliš dlouhý.")
    host = urlparse(url).hostname
    if not host or not is_public_host(host):
        raise HTTPException(400, "Tenhle odkaz server nepovolí.")
    return url


def remove_job_files(jid: str):
    shutil.rmtree(DOWNLOAD_DIR / jid, ignore_errors=True)
    files.pop(jid, None)


def restore_jobs():
    """Po restartu načte hotové soubory z disku, takže historie přežije."""
    for d in DOWNLOAD_DIR.iterdir():
        if not (d.is_dir() and re.fullmatch(r"[0-9a-f]{12}", d.name)):
            continue
        f = pick_file(d)
        if not f or f.suffix.lower() == ".part":
            shutil.rmtree(d, ignore_errors=True)
            continue
        owner_file = d / ".owner"
        owner = owner_file.read_text().strip() if owner_file.is_file() else None
        job = new_job("download", f.stem, jid=d.name, created=d.stat().st_mtime, owner=owner)
        job.update(status="done", stage="Hotovo", percent=100.0, filename=f.name, size=f.stat().st_size)
        files[d.name] = f


def janitor():
    while True:
        time.sleep(600)
        cutoff = time.time() - KEEP_HOURS * 3600
        for jid, job in list(jobs.items()):
            if job["status"] not in ACTIVE and job["created"] < cutoff:
                jobs.pop(jid, None)
                cancelled.discard(jid)
                remove_job_files(jid)


# --------------------------------------------------------------------------
# yt-dlp
# --------------------------------------------------------------------------
def base_opts(cookies_from: Optional[str] = None) -> dict:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "playlist_items": "1",
        "retries": 5,
        "fragment_retries": 5,
        "concurrent_fragment_downloads": 4,
        "socket_timeout": 30,
    }
    if not ALLOW_GENERIC:
        opts["allowed_extractors"] = ["default", "-generic"]
    if FFMPEG and not FFMPEG_IN_PATH:
        opts["ffmpeg_location"] = FFMPEG
    if COOKIES_FILE and Path(COOKIES_FILE).is_file():
        opts["cookiefile"] = COOKIES_FILE
    elif cookies_from in BROWSERS:
        opts["cookiesfrombrowser"] = (cookies_from,)
    return opts


class UrlIn(BaseModel):
    url: str
    cookies_from: Optional[str] = None


class DownloadIn(BaseModel):
    url: str
    mode: str = "auto"  # auto | video | audio
    quality: str = "best"  # "best" nebo maximální výška, třeba "1080"
    container: str = "mp4"
    audio_format: str = "mp3"
    audio_bitrate: str = "192"
    embed_cover: bool = False
    cookies_from: Optional[str] = None
    title: Optional[str] = None
    thumbnail: Optional[str] = None


@app.get("/api/health")
def health():
    return {
        "ytdlp": YTDLP_VERSION,
        "ffmpeg": bool(FFMPEG),
        "deno": bool(shutil.which("deno")),
        "auth": bool(AUTH_PASSWORD),
        "keep_hours": KEEP_HOURS,
        "daily_limit": DAILY_LIMIT,
    }


class KeyIn(BaseModel):
    key: str


@app.get("/api/quota")
def get_quota(request: Request):
    """Stav limitu pro tohoto návštěvníka (zobrazuje se v rozhraní)."""
    rec = current_key(request)
    out = {"enabled": DAILY_LIMIT > 0, "has_key": bool(rec), "label": rec["label"] if rec else None,
           "stale_key": bool(request.cookies.get(KEY_COOKIE)) and not rec,
           "can_request": DAILY_LIMIT > 0 and not rec,
           "limit": DAILY_LIMIT, "used": 0, "remaining": DAILY_LIMIT, "reset_in": None}
    if DAILY_LIMIT > 0 and not rec:
        used, reset_in = quota.usage(quota.who(request))
        out.update(used=used, remaining=max(0, DAILY_LIMIT - used), reset_in=reset_in)
    return out


@app.post("/api/key")
def set_key(body: KeyIn, request: Request, response: Response):
    """Ověří přístupový klíč a uloží ho do cookie (klíč tak nezůstává v JavaScriptu)."""
    rate_check("key", quota.who(request), 10, 3600, "Moc pokusů o zadání klíče. Zkus to za hodinu.")
    rec, why = quota.get_key(body.key, request.state.sid, bind=True)
    if why == "other_device":
        raise HTTPException(
            403,
            "Tenhle klíč je už přiřazený k jinému zařízení a funguje jen tam. "
            "Pokud sis změnil telefon nebo prohlížeč, napiš provozovateli, ať ti klíč uvolní.",
        )
    if not rec:
        raise HTTPException(403, "Tenhle klíč neplatí. Zkontroluj, že je opsaný celý, nebo ho nech vystavit znovu.")
    response.set_cookie(
        KEY_COOKIE, quota.normalize(body.key), max_age=60 * 60 * 24 * 365, httponly=True, samesite="lax",
        secure=request.headers.get("x-forwarded-proto", request.url.scheme) == "https",
    )
    return {"ok": True, "label": rec["label"]}


class KeyRequestIn(BaseModel):
    email: str
    website: str = ""  # past na boty: lidem je pole skryté, roboti ho vyplní


@app.post("/api/key/request")
def request_key(body: KeyRequestIn, request: Request, background: BackgroundTasks):
    """Žádost o přístupový klíč. Uloží se a provozovateli přijde e-mail s Reply-To na adresu žadatele."""
    if DAILY_LIMIT <= 0:
        raise HTTPException(404, "Tenhle server žádný limit nemá, klíč není potřeba.")
    if current_key(request):
        raise HTTPException(400, "Přístupový klíč už máš aktivní.")
    if body.website.strip():
        return {"ok": True, "email": ""}  # robot: tváříme se, že se to povedlo, a nic neděláme
    email = keyrequests.valid_email(body.email)
    if not email:
        raise HTTPException(400, "Zadej platnou e-mailovou adresu, třeba jmeno@seznam.cz.")
    rate_check("request", quota.who(request), 3, 24 * 3600,
               "Dnes jsi už žádal několikrát. Počkej prosím na odpověď, klíč ti přijde e-mailem.")
    rec, status = keyrequests.add_request(email)
    if status == "full":
        raise HTTPException(503, "Dnes už přišlo hodně žádostí. Zkus to prosím zítra.")
    if status == "ok":
        background.add_task(keyrequests.notify, rec)
    return {"ok": True, "email": email}


@app.delete("/api/key")
def clear_key(response: Response):
    response.delete_cookie(KEY_COOKIE)
    return {"ok": True}


@app.post("/api/info")
def info(body: UrlIn, request: Request):
    url = clean_url(body.url)
    rate_check("info", request.state.sid, INFO_PER_HOUR, 3600, "Moc rychle za sebou. Zkus to za chvíli.")
    opts = base_opts(body.cookies_from)
    opts["skip_download"] = True
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            data = ydl.extract_info(url, download=False)
    except Exception as e:
        raise HTTPException(422, friendly(e))
    if not data:
        raise HTTPException(422, "K tomuto odkazu se nepodařilo nic zjistit.")
    if data.get("_type") == "playlist":
        entries = [x for x in (data.get("entries") or []) if x]
        if not entries:
            raise HTTPException(422, "Seznam nepřehrávání je prázdný.")
        data = entries[0]
    formats = data.get("formats") or []
    heights = sorted({f["height"] for f in formats if f.get("height") and f.get("vcodec") not in (None, "none")}, reverse=True)
    return {
        "title": data.get("title"),
        "uploader": data.get("uploader") or data.get("channel"),
        "duration": data.get("duration"),
        "thumbnail": data.get("thumbnail"),
        "extractor": data.get("extractor_key"),
        "heights": heights,
        "has_video": bool(heights),
    }


def run_download(jid: str, url: str, body: DownloadIn):
    job = jobs.get(jid)
    if not job:
        return
    if jid in cancelled:
        job.update(status="cancelled", stage="Zrušeno")
        remove_job_files(jid)
        return

    out_dir = DOWNLOAD_DIR / jid
    out_dir.mkdir(parents=True, exist_ok=True)
    state = {"done": 0}

    def hook(d):
        if jid in cancelled:
            raise RuntimeError("cancelled")
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            got = d.get("downloaded_bytes") or 0
            pct = got / total * 100 if total else 0
            streams = len((d.get("info_dict") or {}).get("requested_formats") or []) or 1
            overall = (state["done"] * 100 + pct) / streams
            job.update(status="downloading", stage="Stahuje se", percent=round(min(overall, 99.0), 1),
                       speed=d.get("speed"), eta=d.get("eta"))
        elif d["status"] == "finished":
            state["done"] += 1

    def pp_hook(d):
        if jid in cancelled:
            raise RuntimeError("cancelled")
        if d["status"] == "started":
            job.update(status="processing", stage="Zpracovává se", percent=99.0, speed=None, eta=None)

    filt_state: dict = {}
    opts = base_opts(body.cookies_from)
    opts.update(
        match_filter=video_filter(filt_state),
        outtmpl=str(out_dir / "%(title).120B [%(id)s].%(ext)s"),
        windowsfilenames=True,
        progress_hooks=[hook],
        postprocessor_hooks=[pp_hook],
    )

    if body.mode == "audio":
        codec = body.audio_format if body.audio_format in AUDIO_FORMATS else "mp3"
        pp = {"key": "FFmpegExtractAudio", "preferredcodec": codec}
        if codec in ("mp3", "m4a", "opus") and body.audio_bitrate in ("128", "192", "256", "320"):
            pp["preferredquality"] = body.audio_bitrate
        opts["format"] = "bestaudio/best"
        opts["postprocessors"] = [pp, {"key": "FFmpegMetadata", "add_metadata": True}]
        if body.embed_cover and codec in ("mp3", "m4a"):
            opts["writethumbnail"] = True
            opts["postprocessors"] += [
                {"key": "FFmpegThumbnailsConvertor", "format": "jpg", "when": "before_dl"},
                {"key": "EmbedThumbnail"},
            ]
    else:
        container = body.container if body.container in VIDEO_FORMATS else "mp4"
        cap = f"[height<={int(body.quality)}]" if body.quality.isdigit() else ""
        opts["format"] = f"bv*{cap}+ba/b{cap}"
        opts["merge_output_format"] = container
        if container == "mp4":
            opts["format_sort"] = ["res", "ext"]
        elif container == "webm":
            opts["format_sort"] = ["res", "vcodec:vp9", "acodec:opus"]
        opts["postprocessors"] = [{"key": "FFmpegMetadata", "add_metadata": True}]

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            data = ydl.extract_info(url, download=True)
        if data and data.get("_type") == "playlist":
            entries = [x for x in (data.get("entries") or []) if x]
            data = entries[0] if entries else {}
        if data and data.get("title"):
            job["title"] = data["title"][:300]
        if data and data.get("thumbnail") and not job.get("thumbnail"):
            job["thumbnail"] = data["thumbnail"]
        f = pick_file(out_dir)
        if not f:
            raise RuntimeError(filt_state.get("reject") or "Stahování skončilo, ale výsledný soubor se nenašel.")
        files[jid] = f
        job.update(status="done", stage="Hotovo", percent=100.0, speed=None, eta=None,
                   filename=f.name, size=f.stat().st_size)
    except Exception as e:
        quota.refund(jid)  # nepovedené nebo zrušené stažení se do denního limitu nepočítá
        if jid in cancelled:
            job.update(status="cancelled", stage="Zrušeno", speed=None, eta=None)
            shutil.rmtree(out_dir, ignore_errors=True)
        else:
            job.update(status="error", stage="Chyba", error=friendly(e), speed=None, eta=None)


@app.post("/api/download")
def download(body: DownloadIn, request: Request):
    url = clean_url(body.url)
    if body.mode not in ("auto", "video", "audio"):
        raise HTTPException(400, "Neplatný režim.")
    check_capacity(request.state.sid)
    jid = uuid.uuid4().hex[:12]
    if DAILY_LIMIT > 0 and not current_key(request):
        ok, wait = quota.reserve(quota.who(request), jid, DAILY_LIMIT)
        if not ok:
            raise HTTPException(
                429,
                f"Denní limit {DAILY_LIMIT} stažení je vyčerpaný. Další půjde za {fmt_wait(wait)}. "
                "Bez limitu stahuješ s přístupovým klíčem (Nastavení).",
            )
    thumb = body.thumbnail if body.thumbnail and re.match(r"^https?://", body.thumbnail, re.I) else None
    job = new_job("download", body.title or url, thumb, jid=jid, mode=body.mode, owner=request.state.sid)
    save_owner(job["id"], request.state.sid)
    executor.submit(run_download, job["id"], url, body)
    return {"id": job["id"]}


# --------------------------------------------------------------------------
# Převod / remux přes ffmpeg
# --------------------------------------------------------------------------
AUDIO_CODECS = {
    "mp3": ["-c:a", "libmp3lame", "-b:a", "192k"],
    "m4a": ["-c:a", "aac", "-b:a", "192k"],
    "opus": ["-c:a", "libopus", "-b:a", "128k"],
    "flac": ["-c:a", "flac"],
    "wav": ["-c:a", "pcm_s16le"],
}


def run_remux(jid: str, src: Path, target: str, reencode: bool):
    job = jobs.get(jid)
    if not job:
        return
    if jid in cancelled:
        job.update(status="cancelled", stage="Zrušeno")
        remove_job_files(jid)
        return

    job.update(status="processing", stage="Převádí se", percent=50.0)
    out_dir = src.parent.parent
    dst = out_dir / f"{src.stem}.{target}"
    cmd = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-i", str(src)]
    if target in AUDIO_CODECS:
        cmd += ["-vn", *AUDIO_CODECS[target]]
    elif reencode:
        if target == "webm":
            cmd += ["-c:v", "libvpx-vp9", "-crf", "32", "-b:v", "0", "-c:a", "libopus"]
        else:
            cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", "20", "-c:a", "aac", "-b:a", "192k"]
    else:
        cmd += ["-map", "0:v?", "-map", "0:a?", "-c", "copy"]
    if target in ("mp4", "mov"):
        cmd += ["-movflags", "+faststart"]
    cmd.append(str(dst))

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if jid in cancelled:
            job.update(status="cancelled", stage="Zrušeno")
            remove_job_files(jid)
            return
        if proc.returncode != 0 or not dst.is_file():
            lines = [l for l in (proc.stderr or "").strip().splitlines() if l.strip()]
            hint = " Zkus zapnout překódování." if not reencode and target not in AUDIO_CODECS else ""
            raise RuntimeError((lines[-1] if lines else "ffmpeg selhal.") + hint)
        shutil.rmtree(src.parent, ignore_errors=True)
        files[jid] = dst
        job.update(status="done", stage="Hotovo", percent=100.0, filename=dst.name, size=dst.stat().st_size)
    except Exception as e:
        job.update(status="error", stage="Chyba", error=friendly(e))


@app.post("/api/remux")
def remux(request: Request, file: UploadFile = File(...), target: str = Form(...), reencode: bool = Form(False)):
    if not FFMPEG:
        raise HTTPException(503, "Na serveru chybí ffmpeg.")
    check_capacity(request.state.sid)
    if target not in REMUX_VIDEO | AUDIO_FORMATS:
        raise HTTPException(400, "Neplatný cílový formát.")
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", Path(file.filename or "soubor").name) or "soubor"
    job = new_job("remux", name, mode=target, owner=request.state.sid)
    src_dir = DOWNLOAD_DIR / job["id"] / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    save_owner(job["id"], request.state.sid)
    src = src_dir / name
    limit = MAX_UPLOAD_MB * 1024 * 1024
    size = 0
    with open(src, "wb") as out:
        while chunk := file.file.read(1024 * 1024):
            size += len(chunk)
            if size > limit:
                out.close()
                jobs.pop(job["id"], None)
                remove_job_files(job["id"])
                raise HTTPException(413, f"Soubor je větší než {MAX_UPLOAD_MB} MB.")
            out.write(chunk)
    executor.submit(run_remux, job["id"], src, target, reencode)
    return {"id": job["id"]}


# --------------------------------------------------------------------------
# Fronta, soubory, weby
# --------------------------------------------------------------------------
@app.get("/api/jobs")
def list_jobs(request: Request):
    mine = (j for j in jobs.values() if j.get("owner") == request.state.sid)
    return [{k: v for k, v in j.items() if k != "owner"} for j in sorted(mine, key=lambda j: j["created"], reverse=True)]


@app.get("/api/jobs/{jid}/file")
def get_file(jid: str, request: Request):
    own_job(request, jid)
    f = files.get(jid)
    if not f or not f.is_file():
        raise HTTPException(404, "Soubor už není k dispozici.")
    return FileResponse(f, filename=f.name)


# --------------------------------------------------------------------------
# Náhled bez stahování: server vezme přímý odkaz na video a proudem ho posílá
# prohlížeči (odkazy YouTube jsou vázané na IP serveru, přímo z prohlížeče by nešly).
# --------------------------------------------------------------------------
PREVIEW_TTL = 600
_preview_cache: dict = {}
PREVIEW_FORMAT = (
    "b[height<=480][protocol=https]/b[protocol=https]/"
    "b[height<=480][protocol=http]/b[protocol=http]"
)


def resolve_preview(sid: str, url: str) -> dict:
    now = time.time()
    for k in [k for k, v in _preview_cache.items() if v["expires"] < now]:
        _preview_cache.pop(k, None)
    hit = _preview_cache.get((sid, url))
    if hit:
        return hit
    rate_check("info", sid, INFO_PER_HOUR, 3600, "Moc rychle za sebou. Zkus to za chvíli.")
    state: dict = {}
    opts = base_opts()
    opts.update(skip_download=True, format=PREVIEW_FORMAT, match_filter=video_filter(state))
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            data = ydl.extract_info(url, download=False)
    except Exception as e:
        raise HTTPException(422, friendly(e))
    if data and data.get("_type") == "playlist":
        entries = [x for x in (data.get("entries") or []) if x]
        data = entries[0] if entries else None
    if not data:
        raise HTTPException(422, state.get("reject") or "Náhled se nepodařilo připravit.")
    media_url = data.get("url")
    host = urlparse(media_url).hostname if media_url else None
    if not media_url or not host or not is_public_host(host):
        raise HTTPException(422, "Tohle video nejde přehrát jako náhled, můžeš ho ale stáhnout.")
    info = {"url": media_url, "headers": data.get("http_headers") or {}, "expires": now + PREVIEW_TTL}
    _preview_cache[(sid, url)] = info
    return info


@app.get("/api/preview")
def preview(request: Request, url: str):
    url = clean_url(url)
    info = resolve_preview(request.state.sid, url)
    headers = {k: v for k, v in info["headers"].items() if k.lower() != "range"}
    if request.headers.get("range"):
        headers["Range"] = request.headers["range"]
    try:
        upstream = urllib.request.urlopen(urllib.request.Request(info["url"], headers=headers), timeout=30)
    except urllib.error.HTTPError as e:
        _preview_cache.pop((request.state.sid, url), None)
        raise HTTPException(416 if e.code == 416 else 502, "Video se nepodařilo načíst.")
    except Exception:
        _preview_cache.pop((request.state.sid, url), None)
        raise HTTPException(502, "Video se nepodařilo načíst.")
    out_headers = {"Accept-Ranges": "bytes", "Cache-Control": "no-store"}
    for h in ("Content-Length", "Content-Range"):
        if upstream.headers.get(h):
            out_headers[h] = upstream.headers[h]

    def chunks():
        try:
            while True:
                chunk = upstream.read(64 * 1024)
                if not chunk:
                    break
                yield chunk
        finally:
            upstream.close()

    return StreamingResponse(
        chunks(), status_code=upstream.status,
        media_type=upstream.headers.get("Content-Type") or "video/mp4", headers=out_headers,
    )


@app.get("/api/jobs/{jid}/stream")
def stream_file(jid: str, request: Request):
    """Stejný soubor jako /file, ale k přehrání v prohlížeči (bez vynuceného stažení)."""
    own_job(request, jid)
    f = files.get(jid)
    if not f or not f.is_file():
        raise HTTPException(404, "Soubor už není k dispozici.")
    return FileResponse(f)


@app.delete("/api/jobs/{jid}")
def delete_job(jid: str, request: Request):
    job = own_job(request, jid)
    if job["status"] in ACTIVE:
        cancelled.add(jid)
        job.update(status="cancelled", stage="Zrušeno", speed=None, eta=None)
        return {"ok": True}
    jobs.pop(jid, None)
    cancelled.discard(jid)
    remove_job_files(jid)
    return {"ok": True}


@app.post("/api/jobs/clear")
def clear_finished(request: Request):
    for jid, job in list(jobs.items()):
        if job.get("owner") == request.state.sid and job["status"] not in ACTIVE:
            jobs.pop(jid, None)
            cancelled.discard(jid)
            remove_job_files(jid)
    return {"ok": True}


@lru_cache(maxsize=1)
def _site_names():
    from yt_dlp.extractor import gen_extractor_classes

    names = set()
    for ie in gen_extractor_classes():
        try:
            if not ie.working():
                continue
            name = ie.IE_NAME
        except Exception:
            continue
        if name == "generic":
            continue
        names.add(name.split(":")[0])
    return sorted(names, key=str.lower)


@app.get("/api/sites")
def sites():
    return {"version": YTDLP_VERSION, "names": _site_names()}


# --------------------------------------------------------------------------
# Start
# --------------------------------------------------------------------------
restore_jobs()
threading.Thread(target=janitor, daemon=True).start()

# Statické soubory musí být připojené jako poslední, aby nepřebily /api.
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
