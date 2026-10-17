"""PC verze: přístupové klíče ověřuje a žádosti vyřizuje server Lumenu (KEY_SERVER).

Seznam klíčů je jen na serveru. PC verze proto klíč pošle serveru (/api/remote/key) spolu se stálým ID
instalace a server ho přiřadí k tomuhle počítači jako k jednomu zařízení (stejně jako prohlížeč na webu).
Výsledek se drží v mezipaměti: ověřuje se znovu po CHECK_EVERY, a když server není dostupný, platí poslední
úspěšné ověření ještě OFFLINE_GRACE. Server je zapnutý jen občas (při vydání nové verze), aplikace proto
běží bez něj: ověřený klíč platí dál a po nedostupném serveru se další dotaz zkusí až za RETRY_AFTER. Žádosti o klíč a o víc zařízení se přeposílají serveru, schválení
e-mailem zůstává stejné. Denní limit stažení se počítá přímo v PC verzi.
"""
import json
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.request

from fastapi import HTTPException

from . import quota

KEY_SERVER = os.getenv("KEY_SERVER", "").strip().rstrip("/")
APP_VERSION = os.getenv("LUMEN_APP_VERSION", "")
CHECK_EVERY = 6 * 3600
OFFLINE_GRACE = 365 * 86400
RETRY_AFTER = 10 * 60  # po nedostupném serveru se další dotaz zkusí až za chvíli (každý by čekal na timeout)
CACHE_FILE = quota.DATA_DIR / "remote_key.json"
DEVICE_FILE = quota.DATA_DIR / "device_id"
_lock = threading.Lock()
_app_cache = {"ts": 0.0, "data": None}
_down = {"until": 0.0}


def enabled() -> bool:
    return bool(KEY_SERVER)


def device_id() -> str:
    """Stálé ID téhle instalace (náhodné, nic neprozradí); server podle něj pozná zařízení."""
    with _lock:
        try:
            value = DEVICE_FILE.read_text("utf-8").strip()
        except OSError:
            value = ""
        if not re.fullmatch(r"[0-9a-f]{32}", value):
            value = secrets.token_hex(16)
            DEVICE_FILE.write_text(value, "utf-8")
        return value


def _request(method: str, path: str, payload: dict = None, timeout: int = 15):
    """(HTTP kód, JSON). Kód None = server nedostupný."""
    offline = {"detail": "Server Lumenu teď není dostupný (zapíná se jen občas). Zkus to později."}
    if time.time() < _down["until"]:
        return None, offline
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(KEY_SERVER + path, data=data, method=method, headers={
        "Content-Type": "application/json", "Accept": "application/json",
        "User-Agent": f"LumenDesktop/{APP_VERSION or '0'}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read() or b"{}")
        except ValueError:
            body = {}
        return e.code, body if isinstance(body, dict) else {}
    except Exception as e:  # noqa: BLE001 - bez internetu, DNS, timeout
        print(f"Server Lumenu {KEY_SERVER} nedostupný: {type(e).__name__}: {e}", flush=True)
        _down["until"] = time.time() + RETRY_AFTER
        return None, offline


def _load() -> dict:
    try:
        data = json.loads(CACHE_FILE.read_text("utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(data: dict):
    tmp = CACHE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), "utf-8")
    os.replace(tmp, CACHE_FILE)


def check(key: str, force: bool = False):
    """Ověří klíč u serveru (s mezipamětí). Vrátí (záznam klíče, None), nebo (None, důvod)."""
    norm = quota.normalize(key)
    if len(norm) < 8:
        return None, None
    now = time.time()
    with _lock:
        cache = _load()
    same = cache.get("key") == norm
    if same and not force and now - cache.get("checked", 0) < CHECK_EVERY:
        return cache.get("rec"), None
    status, data = _request("POST", "/api/remote/key", {"key": norm, "device": device_id()})
    if status == 200:
        with _lock:
            _save({"key": norm, "rec": data, "checked": now})
        return data, None
    if status is None:
        if same and now - cache.get("checked", 0) < OFFLINE_GRACE:
            return cache.get("rec"), None  # chvíli bez internetu klíč platí dál
        return None, data.get("detail")
    if status == 403 and same:
        with _lock:
            CACHE_FILE.unlink(missing_ok=True)  # klíč zrušený, uvolněný nebo patří jinému zařízení
    return None, data.get("detail") or f"Server Lumenu odpověděl chybou {status}."


def forward(path: str, payload: dict) -> dict:
    """Přepošle žádost serveru a vrátí jeho odpověď (nebo jeho chybu se stejným textem)."""
    status, data = _request("POST", path, payload)
    if status == 200:
        return data
    raise HTTPException(status or 503, data.get("detail") or "Server Lumenu žádost nepřijal.")


def forward_increase(key: str, payload: dict) -> dict:
    data = forward("/api/remote/increase", {**payload, "key": quota.normalize(key), "device": device_id()})
    with _lock:  # ať Nastavení hned ukáže čekající žádost
        cache = _load()
        if cache:
            cache["checked"] = 0
            _save(cache)
    return data


def _version(v: str):
    return tuple(int(x) for x in re.findall(r"\d+", v or "")[:4])


def app_update(force: bool = False) -> dict:
    """Pro aplikaci (PC, Android): je na serveru novější verze? (výsledek se drží hodinu, nedostupný server 10 min)"""
    now = time.time()
    if force:
        _down["until"] = 0.0
    if force or now - _app_cache["ts"] > 3600 or _app_cache["data"] is None:
        status, data = _request("GET", "/api/app", timeout=8)
        _app_cache.update(ts=now if status == 200 else now - 3600 + RETRY_AFTER, data=data if status == 200 else {})
    win = (_app_cache["data"] or {}).get(os.getenv("LUMEN_PLATFORM", "windows"))
    newer = bool(win and APP_VERSION and _version(win.get("version")) > _version(APP_VERSION))
    return {"current": APP_VERSION, "update": {"version": win["version"], "size": win.get("size"),
                                               "sha256": win.get("sha256"), "url": KEY_SERVER + win["url"]}
            if newer else None}
