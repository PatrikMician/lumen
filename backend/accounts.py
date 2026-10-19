"""Účty Lumenu: e-mail + heslo, přístupový klíč patří účtu, oblíbené skladby se ukládají k účtu.

Web funguje dál i bez účtu (klíč v cookie jednoho prohlížeče). Přihlášený uživatel má klíč účtu na každém
zařízení, kde se přihlásí; limit zařízení klíče platí dál (každé přihlášené zařízení = jedno zařízení klíče).

Server: účty v DATA_DIR/accounts.json (hesla jen jako otisk scrypt), přihlášení = náhodný token v cookie
`lumen_acc` (web) nebo v aplikaci. Aplikace (PC, Android) se přihlašují přes server (/api/remote/account/…),
jen když server běží; pak si účet pamatují (DATA_DIR/account.json) a fungují bez serveru dál.
"""
import hashlib
import json
import os
import secrets
import threading
import time
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from typing import Optional

from fastapi import HTTPException

from . import quota

ACCOUNTS_FILE = quota.DATA_DIR / "accounts.json"  # server
APP_ACCOUNT_FILE = quota.DATA_DIR / "account.json"  # aplikace: přihlášený účet
COOKIE = "lumen_acc"
SESSION_DAYS = 365
RESET_TTL = 3600
MAX_FAVORITES = 5000
_lock = threading.Lock()


# --------------------------------------------------------------------------
# Úložiště (server)
# --------------------------------------------------------------------------
_cache = {"mtime": None, "data": None}


def _load() -> dict:
    """Účty z disku (drží se podle času změny souboru, current_key se ptá při každém požadavku)."""
    try:
        mtime = ACCOUNTS_FILE.stat().st_mtime_ns
    except OSError:
        mtime = None
    if mtime is not None and _cache["mtime"] == mtime:
        return json.loads(_cache["data"])
    try:
        raw = ACCOUNTS_FILE.read_text("utf-8")
        data = json.loads(raw)
        _cache.update(mtime=mtime, data=raw)
    except (OSError, ValueError):
        data = {}
    data.setdefault("users", {})
    data.setdefault("sessions", {})
    data.setdefault("resets", {})
    return data


def _save(data: dict):
    tmp = ACCOUNTS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), "utf-8")
    try:
        tmp.chmod(0o600)
    except OSError:
        pass
    os.replace(tmp, ACCOUNTS_FILE)


def _h(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return f"scrypt$16384$8$1${salt.hex()}${dk.hex()}"


def check_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, dk = stored.split("$")
        got = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), dklen=32)
        return secrets.compare_digest(got.hex(), dk)
    except (ValueError, TypeError):
        return False


def check_new_password(password: str):
    if len(password or "") < 8:
        raise HTTPException(400, "Heslo musí mít aspoň 8 znaků.")
    if len(password) > 200:
        raise HTTPException(400, "Heslo je moc dlouhé.")


def _find_email(data: dict, email: str) -> Optional[str]:
    for uid, u in data["users"].items():
        if u.get("email") == email:
            return uid
    return None


def register(email: str, password: str) -> str:
    check_new_password(password)
    with _lock:
        data = _load()
        if _find_email(data, email):
            raise HTTPException(409, "Účet s tímhle e-mailem už existuje. Přihlas se, nebo si obnov heslo.")
        uid = secrets.token_hex(8)
        data["users"][uid] = {"email": email, "pw": hash_password(password), "created": time.time(), "key": "",
                              "fav": [], "fav_ts": 0}
        _save(data)
    return uid


def login(email: str, password: str) -> str:
    with _lock:
        data = _load()
        uid = _find_email(data, email)
        stored = data["users"][uid]["pw"] if uid else hash_password(secrets.token_hex(8))  # stejná doba i bez účtu
    if not uid or not check_password(password, stored):
        raise HTTPException(401, "Špatný e-mail nebo heslo.")
    return uid


def new_session(uid: str, device: str) -> str:
    token = secrets.token_urlsafe(32)
    now = time.time()
    with _lock:
        data = _load()
        data["sessions"] = {k: v for k, v in data["sessions"].items() if v.get("seen", 0) > now - SESSION_DAYS * 86400}
        data["sessions"][_h(token)] = {"uid": uid, "created": now, "seen": now, "device": device}
        _save(data)
    return token


def user_for_token(token: str) -> Optional[tuple]:
    """(uid, záznam účtu, zařízení) pro platný token, jinak None."""
    if not token or len(token) < 20:
        return None
    data = _load()
    s = data["sessions"].get(_h(token))
    if not s or s.get("seen", 0) < time.time() - SESSION_DAYS * 86400:
        return None
    u = data["users"].get(s["uid"])
    if not u:
        return None
    if time.time() - s.get("seen", 0) > 86400:  # poslední použití stačí po dnech
        with _lock:
            d = _load()
            if _h(token) in d["sessions"]:
                d["sessions"][_h(token)]["seen"] = time.time()
                _save(d)
    return s["uid"], u, s.get("device", "")


def end_session(token: str):
    with _lock:
        data = _load()
        if data["sessions"].pop(_h(token or ""), None):
            _save(data)


def update_user(uid: str, **fields):
    with _lock:
        data = _load()
        if uid in data["users"]:
            data["users"][uid].update(fields)
            _save(data)


def set_favorites(uid: str, items: list, ts: float) -> dict:
    items = [x for x in items if isinstance(x, dict) and x.get("id")][:MAX_FAVORITES]
    with _lock:
        data = _load()
        u = data["users"].get(uid)
        if not u:
            raise HTTPException(401, "Nejsi přihlášený.")
        u["fav"] = items
        u["fav_ts"] = float(ts) if ts else time.time()
        _save(data)
        return {"items": u["fav"], "ts": u["fav_ts"]}


# --------------------------------------------------------------------------
# Obnova hesla e-mailem
# --------------------------------------------------------------------------
def start_reset(email: str) -> Optional[str]:
    """Vytvoří token pro obnovu hesla (None, když účet neexistuje; navenek se to nepozná)."""
    with _lock:
        data = _load()
        uid = _find_email(data, email)
        if not uid:
            return None
        now = time.time()
        data["resets"] = {k: v for k, v in data["resets"].items() if v["ts"] > now - RESET_TTL}
        token = secrets.token_urlsafe(24)
        data["resets"][_h(token)] = {"uid": uid, "ts": now}
        _save(data)
    return token


def finish_reset(token: str, password: str):
    check_new_password(password)
    with _lock:
        data = _load()
        r = data["resets"].pop(_h(token or ""), None)
        if not r or r["ts"] < time.time() - RESET_TTL or r["uid"] not in data["users"]:
            raise HTTPException(400, "Odkaz pro obnovu hesla už neplatí. Požádej o nový.")
        data["users"][r["uid"]]["pw"] = hash_password(password)
        data["sessions"] = {k: v for k, v in data["sessions"].items() if v["uid"] != r["uid"]}  # odhlásit všude
        _save(data)


def reset_mail(email: str, link: str, mail_from: str) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = "Lumen: obnova hesla"
    msg["From"] = mail_from
    msg["To"] = email
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain="lumen.local")
    msg.set_content(
        "Ahoj,\n\n"
        "někdo (nejspíš ty) požádal o obnovu hesla k účtu Lumenu. Nové heslo si nastavíš tady:\n\n"
        f"    {link}\n\n"
        "Odkaz platí hodinu. Pokud jsi o obnovu nežádal, tenhle e-mail ignoruj, heslo zůstane stejné.\n"
    )
    return msg


# --------------------------------------------------------------------------
# Aplikace (PC, Android): přihlášený účet uložený v zařízení
# --------------------------------------------------------------------------
def app_load() -> dict:
    try:
        data = json.loads(APP_ACCOUNT_FILE.read_text("utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def app_save(data: dict):
    if not data:
        APP_ACCOUNT_FILE.unlink(missing_ok=True)
        return
    tmp = APP_ACCOUNT_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), "utf-8")
    os.replace(tmp, APP_ACCOUNT_FILE)


def app_key() -> str:
    """Klíč přihlášeného účtu v aplikaci (prázdný, když účet klíč nemá nebo nikdo není přihlášený)."""
    return app_load().get("key", "")
