"""Lumen: malý webový stahovač médií postavený nad yt-dlp a ffmpeg.

Spuštění:  python run.py   (viz README.md)
"""
import base64
import html
import ipaddress
import os
import re
import secrets
import shutil
import socket
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import yt_dlp
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile, Request
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from yt_dlp.version import __version__ as YTDLP_VERSION

from . import keyrequests, music, quota, remotekeys

APP_DIR = Path(os.getenv("APP_DIR") or quota.DATA_DIR / "app")  # server: aplikace ke stažení (viz APP_FILES)
# platforma -> (vzor názvu souboru, typ při stažení); nabízí se nejvyšší verze
APP_FILES = {
    "windows": (re.compile(r"LumenSetup-(\d+(?:\.\d+){1,3})\.exe"), "application/octet-stream"),
    "android": (re.compile(r"Lumen-(\d+(?:\.\d+){1,3})\.apk"), "application/vnd.android.package-archive"),
}
# kontakt pro podporu (Nastavení a Nápověda na webu i v PC verzi)
SUPPORT_NAME = os.getenv("SUPPORT_NAME", "Patrik Mičian").strip()
SUPPORT_EMAIL = os.getenv("SUPPORT_EMAIL", "micipapatrmi@gmail.com").strip()

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
# Cookies z prohlížeče má smysl číst jen tehdy, když aplikace běží na stejném počítači jako prohlížeč (doma).
# Na serveru žádný prohlížeč není a návštěvník by dostal chybu "could not find chrome cookies database".
ALLOW_BROWSER_COOKIES = os.getenv("ALLOW_BROWSER_COOKIES", "0") == "1"
IMPERSONATE = os.getenv("IMPERSONATE", "chrome").strip()  # napodobení prohlížeče pro weby, které vrací 403 (prázdné = vypnuto)
# JavaScript runtime pro YouTube (yt-dlp: deno, node, bun, quickjs). Server má Deno, PC verze zkouší i Node a Bun.
# Položka může mít cestu: "quickjs:/cesta/k/qjs" (Android má QuickJS přibalený pod jiným názvem).
_JS_SPECS = [r.strip() for r in os.getenv("JS_RUNTIMES", "deno").split(",") if r.strip()]
JS_RUNTIMES = [r.split(":", 1)[0].lower() for r in _JS_SPECS]
JS_PATHS = {r.split(":", 1)[0].lower(): r.split(":", 1)[1] for r in _JS_SPECS if ":" in r}
DESKTOP = os.getenv("LUMEN_DESKTOP") == "1"  # aplikace (PC, Android): běží u uživatele, klíče ověřuje server (KEY_SERVER)
PLATFORM = os.getenv("LUMEN_PLATFORM", "windows" if DESKTOP else "web")  # web | windows | android

BROWSERS = {"chrome", "firefox", "edge", "brave", "opera", "safari", "vivaldi", "chromium"}
AUDIO_FORMATS = {"mp3", "m4a", "opus", "flac", "wav"}
VIDEO_FORMATS = {"mp4", "mkv", "webm"}
REMUX_VIDEO = {"mp4", "mkv", "webm", "mov"}
ACTIVE = ("queued", "downloading", "processing")
SKIP_SUFFIXES = {".part", ".ytdl", ".temp", ".tmp", ".jpg", ".jpeg", ".png", ".webp", ".json", ".vtt", ".srt"}
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _find_ffmpeg():
    """Vrátí (cesta, je_v_PATH). Když ffmpeg není nainstalovaný, zkusí balík imageio-ffmpeg."""
    if os.getenv("FFMPEG_PATH"):  # Android: ffmpeg přibalený v aplikaci
        return os.environ["FFMPEG_PATH"], False
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
_history: dict = {"download": {}, "info": {}, "key": {}, "request": {}, "approve": {}, "music": {}, "music_track": {}}
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
    Zrušený nebo uvolněný klíč, případně klíč zkopírovaný na jiné zařízení, neplatí.
    V PC verzi klíč ověřuje server Lumenu (s mezipamětí, viz remotekeys)."""
    if remotekeys.enabled():
        rec, _ = remotekeys.check(request.cookies.get(KEY_COOKIE, ""))
        return rec
    rec, _ = quota.get_key(request.cookies.get(KEY_COOKIE, ""), request.state.sid)
    return rec


def key_error(why: str) -> HTTPException:
    if why == "other_device":
        return HTTPException(403, "Tenhle klíč je už přiřazený k jinému zařízení a funguje jen tam. "
                                  "Pokud sis změnil telefon nebo prohlížeč, napiš provozovateli, ať ti klíč uvolní.")
    return HTTPException(403, "Tenhle klíč neplatí. Zkontroluj, že je opsaný celý, nebo ho nech vystavit znovu.")


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
NO_IMPERSONATE_HOSTS = ("youtube.com", "youtu.be", "googlevideo.com", "youtube-nocookie.com")


@lru_cache(maxsize=1)
def impersonate_target():
    """Cíl pro napodobení prohlížeče, nebo None, když je vypnuté či chybí knihovna curl_cffi
    (bez ní by yt-dlp s volbou impersonate selhal u každého stažení)."""
    if not IMPERSONATE:
        return None
    try:
        import curl_cffi  # noqa: F401
        from yt_dlp.networking.impersonate import ImpersonateTarget
        return ImpersonateTarget.from_str(IMPERSONATE)
    except Exception:  # noqa: BLE001 - knihovna chybí nebo je nekompatibilní verze
        return None


def wants_impersonation(url: Optional[str]) -> bool:
    """Napodobení prohlížeče se zapíná jen pro weby mimo YouTube, kde by mohlo spíš uškodit."""
    host = (urlparse(url or "").hostname or "").lower()
    return bool(host) and not any(host == h or host.endswith("." + h) for h in NO_IMPERSONATE_HOSTS)


def base_opts(cookies_from: Optional[str] = None, url: Optional[str] = None) -> dict:
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
    if JS_RUNTIMES != ["deno"]:
        opts["js_runtimes"] = {r: ({"path": JS_PATHS[r]} if r in JS_PATHS else {}) for r in JS_RUNTIMES}
    if FFMPEG and not FFMPEG_IN_PATH:
        opts["ffmpeg_location"] = FFMPEG
    target = impersonate_target()
    if target is not None and wants_impersonation(url):
        opts["impersonate"] = target
    if COOKIES_FILE and Path(COOKIES_FILE).is_file():
        opts["cookiefile"] = COOKIES_FILE
    elif ALLOW_BROWSER_COOKIES and cookies_from in BROWSERS:
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
        "deno": any(os.path.isfile(JS_PATHS[r]) if r in JS_PATHS else shutil.which(r) for r in JS_RUNTIMES),  # jakýkoli JavaScript runtime pro YouTube
        "desktop": DESKTOP,
        "platform": PLATFORM,
        "support": {"name": SUPPORT_NAME, "email": SUPPORT_EMAIL} if SUPPORT_EMAIL else None,
        "impersonate": bool(impersonate_target()),
        "browser_cookies": ALLOW_BROWSER_COOKIES,
        "auth": bool(AUTH_PASSWORD),
        "keep_hours": KEEP_HOURS,
        "daily_limit": DAILY_LIMIT,
        "music": music.MUSIC_ENABLED,
        "active": sum(1 for j in jobs.values() if j["status"] in ACTIVE),  # noční aktualizace yt-dlp čeká, až bude 0
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
           "max_request_devices": keyrequests.MAX_REQUEST_DEVICES,
           "limit": DAILY_LIMIT, "used": 0, "remaining": DAILY_LIMIT, "reset_in": None}
    if rec and remotekeys.enabled():
        out.update(key_devices=int(rec.get("max_devices", 1)), key_used=int(rec.get("used", 0)),
                   increase_pending=rec.get("increase_pending"))
    elif rec:
        pending = keyrequests.open_increase(rec.get("id"))
        out.update(key_devices=int(rec.get("max_devices", 1)), key_used=len(rec.get("devices", [])),
                   increase_pending={"devices": keyrequests.requested_devices(pending), "ts": pending["ts"]} if pending else None)
    if DAILY_LIMIT > 0 and not rec:
        used, reset_in = quota.usage(quota.who(request))
        out.update(used=used, remaining=max(0, DAILY_LIMIT - used), reset_in=reset_in)
    return out


@app.post("/api/key")
def set_key(body: KeyIn, request: Request, response: Response):
    """Ověří přístupový klíč a uloží ho do cookie (klíč tak nezůstává v JavaScriptu)."""
    rate_check("key", quota.who(request), 10, 3600, "Moc pokusů o zadání klíče. Zkus to za hodinu.")
    if remotekeys.enabled():
        rec, detail = remotekeys.check(body.key, force=True)
        if not rec:
            raise HTTPException(403, detail or "Tenhle klíč neplatí.")
    else:
        rec, why = quota.get_key(body.key, request.state.sid, bind=True)
        if not rec:
            raise key_error(why)
    response.set_cookie(
        KEY_COOKIE, quota.normalize(body.key), max_age=60 * 60 * 24 * 365, httponly=True, samesite="lax",
        secure=request.headers.get("x-forwarded-proto", request.url.scheme) == "https",
    )
    return {"ok": True, "label": rec["label"]}


class KeyRequestIn(BaseModel):
    email: str
    devices: int = 1
    reason: str = ""
    website: str = ""  # past na boty: lidem je pole skryté, roboti ho vyplní


def check_devices(devices: int, low: int = 1):
    top = keyrequests.MAX_REQUEST_DEVICES
    if not low <= devices <= top:
        raise HTTPException(400, f"Počet zařízení musí být {low} až {top}." if low < top
                            else f"Počet zařízení může být nejvýš {top}.")


@app.post("/api/key/request")
def request_key(body: KeyRequestIn, request: Request, background: BackgroundTasks):
    """Žádost o přístupový klíč. Uloží se a provozovateli přijde e-mail s Reply-To na adresu žadatele."""
    if DAILY_LIMIT <= 0:
        raise HTTPException(404, "Tenhle server žádný limit nemá, klíč není potřeba.")
    if current_key(request):
        raise HTTPException(400, "Přístupový klíč už máš aktivní.")
    if remotekeys.enabled():  # PC verze: žádost vyřizuje server Lumenu
        return remotekeys.forward("/api/key/request", body.model_dump())
    if body.website.strip():
        return {"ok": True, "email": ""}  # robot: tváříme se, že se to povedlo, a nic neděláme
    email = keyrequests.valid_email(body.email)
    if not email:
        raise HTTPException(400, "Zadej platnou e-mailovou adresu, třeba jmeno@seznam.cz.")
    check_devices(body.devices)
    rate_check("request", quota.who(request), 3, 24 * 3600,
               "Dnes jsi už žádal několikrát. Počkej prosím na odpověď, klíč ti přijde e-mailem.")
    rec, status = keyrequests.add_request(email, body.devices, body.reason)
    if status == "full":
        raise HTTPException(503, "Dnes už přišlo hodně žádostí. Zkus to prosím zítra.")
    if status == "ok":
        background.add_task(keyrequests.notify, rec)
    return {"ok": True, "email": email, "devices": body.devices}


@app.post("/api/key/increase")
def request_increase(body: KeyRequestIn, request: Request, background: BackgroundTasks):
    """Žádost o víc zařízení u klíče, který má tohle zařízení aktivní. Důvod je povinný."""
    key = current_key(request)
    if not key:
        raise HTTPException(403, "O víc zařízení jde požádat jen s aktivním přístupovým klíčem.")
    if remotekeys.enabled():
        return remotekeys.forward_increase(request.cookies.get(KEY_COOKIE, ""), body.model_dump())
    return increase_core(key, body, request, background)


def increase_core(key: dict, body: KeyRequestIn, request: Request, background: BackgroundTasks):
    if body.website.strip():
        return {"ok": True, "email": "", "devices": body.devices}
    email = keyrequests.valid_email(body.email)
    if not email:
        raise HTTPException(400, "Zadej platnou e-mailovou adresu, třeba jmeno@seznam.cz.")
    current = int(key.get("max_devices", 1))
    if current >= keyrequests.MAX_REQUEST_DEVICES:
        raise HTTPException(400, f"Klíč už platí na {current} zařízení, víc přes web žádat nejde. Napiš provozovateli.")
    check_devices(body.devices, current + 1)
    reason = keyrequests.clean_reason(body.reason)
    if len(reason) < 3:
        raise HTTPException(400, "Napiš důvod, proč potřebuješ víc zařízení.")
    rate_check("request", quota.who(request), 3, 24 * 3600,
               "Dnes jsi už žádal několikrát. Počkej prosím na odpověď, klíč ti přijde e-mailem.")
    rec, status = keyrequests.add_request(email, body.devices, reason, key=key)
    if status == "full":
        raise HTTPException(503, "Dnes už přišlo hodně žádostí. Zkus to prosím zítra.")
    if status == "ok":
        background.add_task(keyrequests.notify, rec)
    return {"ok": True, "email": email, "devices": keyrequests.requested_devices(rec), "duplicate": status == "duplicate"}


# --- PC verze: ověření klíče a žádost o víc zařízení ------------------------------
DEVICE_RE = re.compile(r"[0-9a-f]{32}")


class RemoteKeyIn(BaseModel):
    key: str
    device: str


class RemoteIncreaseIn(KeyRequestIn):
    key: str
    device: str


def remote_device(device: str) -> str:
    if not DEVICE_RE.fullmatch(device or ""):
        raise HTTPException(400, "Neplatné ID zařízení.")
    return "desktop:" + device  # zařízení PC verze = jedna instalace Lumenu


@app.post("/api/remote/key")
def remote_key(body: RemoteKeyIn, request: Request):
    """PC verze ověří (a při prvním použití přiřadí) klíč k instalaci. Vrátí stav klíče pro Nastavení."""
    sid = remote_device(body.device)
    rate_check("key", quota.who(request), 30, 3600, "Moc pokusů o zadání klíče. Zkus to za hodinu.")
    rec, why = quota.get_key(body.key, sid, bind=True)
    if not rec:
        raise key_error(why)
    pending = keyrequests.open_increase(rec.get("id"))
    return {"id": rec.get("id"), "label": rec.get("label"), "max_devices": int(rec.get("max_devices", 1)),
            "used": len(rec.get("devices", [])),
            "increase_pending": {"devices": keyrequests.requested_devices(pending), "ts": pending["ts"]} if pending else None}


@app.post("/api/remote/increase")
def remote_increase(body: RemoteIncreaseIn, request: Request, background: BackgroundTasks):
    rec, _ = quota.get_key(body.key, remote_device(body.device))
    if not rec:
        raise HTTPException(403, "O víc zařízení jde požádat jen s aktivním přístupovým klíčem.")
    return increase_core(rec, body, request, background)


# --- Stažení PC verze -------------------------------------------------------------
def latest_app(platform: str = "windows"):
    """Nejnovější soubor aplikace pro platformu v APP_DIR jako (verze, cesta), nebo None."""
    pattern = APP_FILES[platform][0]
    found = []
    for f in (APP_DIR.iterdir() if APP_DIR.is_dir() else []):
        m = pattern.fullmatch(f.name)
        if m and f.is_file():
            found.append((tuple(int(x) for x in m.group(1).split(".")), m.group(1), f))
    if not found:
        return None
    _, version, path = max(found)
    return version, path


@app.get("/api/log", response_class=Response)
def app_log():
    """Aplikace (PC, Android): konec záznamu Lumenu pro podporu. Na serveru není."""
    path = os.getenv("LUMEN_LOG", "")
    if not DESKTOP or not path or not os.path.isfile(path):
        raise HTTPException(404, "Záznam tu není.")
    with open(path, "rb") as f:
        f.seek(max(0, os.path.getsize(path) - 64 * 1024))
        text = f.read().decode("utf-8", "replace")
    text = re.sub(r"\[download\][^\n]*?(?=\[download\]|\n|$)", "", text)  # průběh stahování nic neříká
    return Response(text, media_type="text/plain; charset=utf-8")


@app.get("/api/app")
def app_info():
    """Server: které aplikace jsou ke stažení? PC verze: je na serveru novější verze?"""
    if DESKTOP:
        return remotekeys.app_update()
    out = {}
    for platform in APP_FILES:
        latest = latest_app(platform)
        out[platform] = {"version": latest[0], "size": latest[1].stat().st_size, "url": f"/api/app/{platform}"} if latest else None
    return out


@app.get("/api/app/{platform}")
def app_download(platform: str):
    latest = None if DESKTOP or platform not in APP_FILES else latest_app(platform)
    if not latest:
        raise HTTPException(404, "Tahle aplikace teď není ke stažení.")
    return FileResponse(latest[1], filename=latest[1].name, media_type=APP_FILES[platform][1])


# --- Schválení žádosti odkazem z e-mailu -----------------------------------------
def _page(title: str, body_html: str, status: int = 200) -> HTMLResponse:
    doc = (
        '<!doctype html><html lang="cs"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="robots" content="noindex">'
        f"<title>{html.escape(title)}</title>"
        "<style>body{font:16px/1.55 system-ui,sans-serif;max-width:520px;margin:12vh auto;padding:0 20px;color:#1d1b2e;background:#f6f4fb}"
        "h1{font-size:1.5rem;margin:0 0 .6em}form{display:inline-block;margin:.4em .4em 0 0}"
        "button{font:inherit;font-weight:600;padding:.7em 1.4em;border:0;border-radius:999px;cursor:pointer;background:#f4a43a;color:#1d1b2e}"
        "button.no{background:#e4e0ef}code{display:block;margin:.8em 0;padding:.8em 1em;background:#fff;border-radius:10px;word-break:break-all}"
        ".muted{color:#6a6580;font-size:.92rem}"
        "label{display:flex;align-items:center;gap:.7em;margin:1em 0 .2em;font-weight:600}"
        "input[type=number]{font:inherit;width:5.5em;padding:.45em .7em;border:1px solid #d6cfe9;border-radius:10px;background:#fff}"
        "blockquote{margin:.6em 0;padding:.7em 1em;background:#fff;border-left:3px solid #f4a43a;border-radius:8px;white-space:pre-wrap;overflow-wrap:anywhere}"
        "</style></head><body>"
        f"{body_html}</body></html>"
    )
    return HTMLResponse(doc, status_code=status, headers={
        "Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Robots-Tag": "noindex"})


@app.get("/api/key/approve")
def approve_page(request: Request, id: int, t: str):
    """Potvrzovací stránka. Jen zobrazuje, nic nemění (e-mailové skenery odkazů by jinak žádost schválily samy)."""
    rate_check("approve", quota.who(request), 30, 3600, "Moc pokusů. Zkus to za hodinu.")
    rec = keyrequests.peek_request(id, t)
    if not rec:
        return _page("Odkaz neplatí", "<h1>Odkaz neplatí</h1><p>Žádost už je vyřízená, odkaz vypršel, nebo je neúplný. "
                     "Čekající žádosti uvidíš na serveru příkazem <code>sudo lumenkeys requests</code>.</p>", 404)
    mail = html.escape(rec["email"])
    tok = html.escape(t, quote=True)
    devices = keyrequests.requested_devices(rec)
    hidden = f'<input type="hidden" name="id" value="{rec["id"]}"><input type="hidden" name="t" value="{tok}">'
    reason = f"<p>Důvod:</p><blockquote>{html.escape(rec['reason'])}</blockquote>" if rec.get("reason") else ""
    if rec.get("kind") == "increase":
        key = quota.find_key(rec.get("key_id"))
        if not key:
            return _page("Klíč neexistuje", f"<h1>Klíč už neexistuje</h1><p>Klíč č. {html.escape(str(rec.get('key_id')))} "
                         "je zrušený nebo smazaný, navýšení nejde schválit. Žádost můžeš zamítnout.</p>"
                         f'<form method="post" action="/api/key/approve">{hidden}<button class="no" name="action" value="deny">Zamítnout</button></form>', 410)
        used = len(key.get("devices", []))
        low = max(1, used)
        title = "Žádost o víc zařízení"
        intro = (f"<p><strong>{mail}</strong> žádá u klíče č. {key['id']} „{html.escape(key.get('label', ''))}“ o "
                 f"<strong>{devices}</strong> zařízení. Teď klíč platí na {key.get('max_devices', 1)} zař., obsazená jsou {used}.</p>")
        note = '<p class="muted">Po schválení se počet hned změní a žadateli přijde e-mail. Níž než počet obsazených zařízení jít nejde (k tomu je <code style="display:inline;padding:0 .3em">lumenkeys reset</code>).</p>'
        button = "Schválit navýšení"
    else:
        low = 1
        title = "Žádost o klíč"
        intro = f"<p>Žádá <strong>{mail}</strong> o klíč na <strong>{devices}</strong> zařízení.</p>"
        note = '<p class="muted">Po schválení se vytvoří klíč na zvolený počet zařízení a pošle se na tuhle adresu.</p>'
        button = "Schválit a poslat klíč"
    value = max(low, devices)
    return _page(title, f"""<h1>{title}</h1>
{intro}{reason}
<form method="post" action="/api/key/approve">{hidden}
<label>Počet zařízení <input type="number" name="devices" min="{low}" max="{keyrequests.MAX_DEVICES}" value="{value}" required></label>
{note}
<button name="action" value="approve">{button}</button></form>
<form method="post" action="/api/key/approve">{hidden}
<button class="no" name="action" value="deny">Zamítnout</button></form>""")


@app.post("/api/key/approve")
def approve_action(request: Request, id: int = Form(...), t: str = Form(...), action: str = Form(...),
                   devices: Optional[int] = Form(None)):
    rate_check("approve", quota.who(request), 30, 3600, "Moc pokusů. Zkus to za hodinu.")
    if action not in ("approve", "deny"):
        raise HTTPException(400, "Neznámá akce.")
    if action == "approve":
        pending = keyrequests.peek_request(id, t)
        if pending and devices is None:
            devices = keyrequests.requested_devices(pending)
        if pending and not 1 <= devices <= keyrequests.MAX_DEVICES:
            # žádost se nevyřídí, odkaz platí dál
            return _page("Neplatný počet", f"<h1>Neplatný počet zařízení</h1><p>Zadej číslo od 1 do {keyrequests.MAX_DEVICES}. "
                         "Vrať se zpět a schval znovu, žádost pořád čeká.</p>", 400)
        if pending and pending.get("kind") == "increase" and not quota.find_key(pending.get("key_id")):
            return _page("Klíč neexistuje", "<h1>Klíč už neexistuje</h1><p>Klíč je zrušený nebo smazaný, navýšení nejde "
                         "schválit. Vrať se zpět a žádost zamítni.</p>", 410)
    rec = keyrequests.resolve_request(id, t, "approved" if action == "approve" else "denied", devices)
    if not rec:
        return _page("Odkaz neplatí", "<h1>Odkaz neplatí</h1><p>Žádost už je vyřízená nebo odkaz vypršel.</p>", 404)
    mail = html.escape(rec["email"])
    if action == "deny":
        return _page("Zamítnuto", f"<h1>Zamítnuto</h1><p>Žádost od <strong>{mail}</strong> je zamítnutá. Žadateli se nic neposílá.</p>")

    if rec.get("kind") == "increase":
        key = quota.set_max_devices(rec.get("key_id"), devices)
        if not key:
            return _page("Klíč neexistuje", "<h1>Klíč už neexistuje</h1><p>Mezitím ho někdo zrušil, nic se nezměnilo.</p>", 410)
        final = int(key["max_devices"])
        lowered = (f'<p class="muted">Zadal jsi {devices}, ale klíč už má obsazených {final} zařízení, '
                   f"takže zůstává na {final}.</p>") if final != devices else ""
        try:
            keyrequests.send_devices_mail(rec["email"], final)
        except Exception as exc:  # noqa: BLE001 - navýšení už platí, jen se o něm žadatel nedozví e-mailem
            print(f"E-mail o navýšení (žádost č. {rec['id']}) se nepodařilo odeslat: {type(exc).__name__}: {exc}", flush=True)
            return _page("E-mail neodešel", f"<h1>Navýšení platí, e-mail neodešel</h1><p>Klíč č. {key['id']} teď platí "
                         f"na <strong>{final}</strong> zařízení, ale e-mail pro <strong>{mail}</strong> se nepodařilo poslat. "
                         f"Dej mu vědět sám.</p>{lowered}", 502)
        return _page("Hotovo", f"<h1>Hotovo</h1><p>Klíč č. {key['id']} teď platí na <strong>{final}</strong> zařízení "
                     f"a <strong>{mail}</strong> dostal e-mail.</p>{lowered}")

    key = quota.add_key(rec["email"], devices)
    try:
        keyrequests.send_key_mail(rec["email"], key, devices)
    except Exception as exc:  # noqa: BLE001 - klíč už existuje, provozovatel ho musí dostat jinak
        print(f"Klíč pro žádost č. {rec['id']} se nepodařilo odeslat: {type(exc).__name__}: {exc}", flush=True)
        return _page("Klíč se neodeslal", f"""<h1>Klíč se nepodařilo odeslat</h1>
<p>E-mail pro <strong>{mail}</strong> neodešel, ale klíč na {devices} zař. je vytvořený. Pošli mu ho sám (zobrazí se jen teď):</p>
<code>{html.escape(key)}</code>""", 502)
    return _page("Hotovo", f"<h1>Hotovo</h1><p>Klíč na <strong>{devices}</strong> zař. je vytvořený a odeslaný na <strong>{mail}</strong>.</p>"
                 '<p class="muted">Kdyby e-mail nedorazil, podívej se žadateli do spamu. Klíč nejde zobrazit znovu, jen vystavit nový.</p>')


@app.delete("/api/key")
def clear_key(response: Response):
    response.delete_cookie(KEY_COOKIE)
    return {"ok": True}


@app.post("/api/info")
def info(body: UrlIn, request: Request):
    url = clean_url(body.url)
    rate_check("info", request.state.sid, INFO_PER_HOUR, 3600, "Moc rychle za sebou. Zkus to za chvíli.")
    opts = base_opts(body.cookies_from, url)
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
    opts = base_opts(body.cookies_from, url)
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
MEDIA_CHUNK = 8 * 1024 * 1024  # YouTube nemá rád neomezené rozsahy, posílá se po kouscích jako v yt-dlp
UPSTREAM_ERROR = 424  # ne 502: tu Cloudflare nahradí vlastní stránkou a prohlížeč se nedozví důvod


def media_ip(url: str) -> str:
    """IP, pro kterou YouTube odkaz vydal (parametr ip= v adrese googlevideo), do logu."""
    m = re.search(r"[?&]ip=([^&]+)", url or "")
    return m.group(1) if m else "?"


def open_media(info: dict, range_header: Optional[str]):
    """Otevře přímý odkaz na médium přes síťovou vrstvu yt-dlp: stejné cookies, hlavičky a síťové
    nastavení jako při jeho zjištění (obyčejný urllib dostával na serveru od googlevideo 403).
    Vrátí (ydl, odpověď); ydl je potřeba zavřít, až se odpověď dočte."""
    from yt_dlp.networking import Request as YRequest

    headers = {k: v for k, v in info["headers"].items() if k.lower() != "range"}
    if range_header:
        m = re.fullmatch(r"\s*bytes=(\d+)-\s*", range_header)
        host = urlparse(info["url"]).hostname or ""
        if m and host.endswith("googlevideo.com"):
            start = int(m.group(1))
            range_header = f"bytes={start}-{start + MEDIA_CHUNK - 1}"
        headers["Range"] = range_header
    ydl = yt_dlp.YoutubeDL({**info.get("opts", {}), "quiet": True, "no_warnings": True})
    try:
        return ydl, ydl.urlopen(YRequest(info["url"], headers=headers))
    except Exception:
        ydl.close()
        raise


def media_status(exc) -> Optional[int]:
    """HTTP kód z chyby yt-dlp (nebo urllib), jinak None."""
    return getattr(exc, "status", None) or getattr(exc, "code", None)


def stream_media(ydl, upstream, default_type: str, extra_headers: dict = None) -> StreamingResponse:
    out_headers = {"Accept-Ranges": "bytes", **(extra_headers or {})}
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
            ydl.close()

    return StreamingResponse(chunks(), status_code=upstream.status,
                             media_type=upstream.headers.get("Content-Type") or default_type, headers=out_headers)


PREVIEW_TTL = 600
_preview_cache: dict = {}
PREVIEW_FORMAT = (
    "b[height<=480][protocol=https]/b[protocol=https]/"
    "b[height<=480][protocol=http]/b[protocol=http]"
)


def resolve_preview(sid: str, url: str, ipv4: bool = False) -> dict:
    now = time.time()
    for k in [k for k, v in _preview_cache.items() if v["expires"] < now]:
        _preview_cache.pop(k, None)
    hit = _preview_cache.get((sid, url))
    if hit and not ipv4:
        return hit
    if not ipv4:
        rate_check("info", sid, INFO_PER_HOUR, 3600, "Moc rychle za sebou. Zkus to za chvíli.")
    state: dict = {}
    opts = base_opts(url=url)
    if ipv4:
        opts["source_address"] = "0.0.0.0"
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
    info = {"url": media_url, "headers": data.get("http_headers") or {}, "expires": now + PREVIEW_TTL, "opts": opts}
    _preview_cache[(sid, url)] = info
    return info


@app.get("/api/preview")
def preview(request: Request, url: str):
    url = clean_url(url)
    sid = request.state.sid
    last = None
    # 1) jak yt-dlp odkaz vydal, 2) znovu a celé přes IPv4 (odkaz je vázaný na IP; když server
    #    zjistí odkaz přes IPv6 a stahuje přes IPv4 nebo naopak, googlevideo vrátí 403)
    for ipv4 in (False, True):
        info = resolve_preview(sid, url, ipv4)
        try:
            ydl, upstream = open_media(info, request.headers.get("range"))
            if ipv4:
                print(f"Náhled: přes IPv4 prošlo (ip={media_ip(info['url'])})", flush=True)
            return stream_media(ydl, upstream, "video/mp4", {"Cache-Control": "no-store"})
        except Exception as e:  # noqa: BLE001
            _preview_cache.pop((sid, url), None)
            last = media_status(e)
            if last == 416:
                raise HTTPException(416, "Video se nepodařilo načíst.")
            print(f"Náhled: googlevideo vrátil {last or type(e).__name__} ({'IPv4' if ipv4 else 'výchozí síť'}, "
                  f"ip={media_ip(info['url'])})", flush=True)
    raise HTTPException(UPSTREAM_ERROR, f"Video se nepodařilo načíst (HTTP Error {last})." if last else "Video se nepodařilo načíst.")


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

music.configure(rate_check=rate_check, current_key=current_key, base_opts=base_opts, is_public_host=is_public_host,
                friendly=friendly, daily_limit=DAILY_LIMIT, open_media=open_media, stream_media=stream_media,
                media_status=media_status, media_ip=media_ip, upstream_error=UPSTREAM_ERROR)
app.include_router(music.router)

# Statické soubory musí být připojené jako poslední, aby nepřebily /api.
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
