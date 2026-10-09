"""Denní limit stažení a přístupové klíče.

- Limit se počítá na návštěvníka (podle IP adresy, za Cloudflare Tunnelem z hlavičky
  CF-Connecting-IP) v posuvném okně 24 hodin. Počty se ukládají do SQLite, takže
  přežijí restart. IP se neukládá, jen její zasolený hash.
- Přístupové klíče spravuje provozovatel příkazem `python keys.py` (viz README).
  V souboru data/keys.json jsou jen hashe klíčů, samotný klíč se zobrazí jednou
  při vytvoření. Klíč se při prvním zadání přiřadí k prohlížeči (zařízení), kde byl
  zadán, a jinde nefunguje. Počet zařízení na klíč se nastaví při vytvoření
  (výchozí 1), uvolnit ho může jen provozovatel (`keys.py reset`).

Modul používá jen standardní knihovnu, takže ho jde načíst i z keys.py.
"""
import hashlib
import ipaddress
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional

DATA_DIR = Path(os.getenv("DATA_DIR", Path(__file__).resolve().parent.parent / "data")).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)
KEYS_FILE = DATA_DIR / "keys.json"
DB_FILE = DATA_DIR / "quota.db"
SALT_FILE = DATA_DIR / ".salt"
WINDOW = 24 * 3600  # délka okna, ve kterém se stažení počítají

KEY_PREFIX = "LMN"
KEY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # bez 0/O a 1/I, ať se klíč dobře opisuje


# --------------------------------------------------------------------------
# Kdo je návštěvník
# --------------------------------------------------------------------------
def _read_salt() -> bytes:
    if not SALT_FILE.exists():
        SALT_FILE.write_text(secrets.token_hex(16))
        try:
            SALT_FILE.chmod(0o600)
        except OSError:
            pass
    return SALT_FILE.read_text().strip().encode()


_SALT = _read_salt()


def client_ip(request) -> str:
    """IP návštěvníka. Hlavičce CF-Connecting-IP věříme jen tehdy, když požadavek přišel
    z tohoto počítače (cloudflared běží lokálně), jinak by si ji mohl nastavit kdokoliv."""
    peer = request.client.host if request.client else ""
    try:
        peer_ip = ipaddress.ip_address(peer)
    except ValueError:
        return peer or "unknown"
    ip = peer
    if peer_ip.is_loopback:
        forwarded = request.headers.get("cf-connecting-ip", "").strip()
        try:
            ipaddress.ip_address(forwarded)
            ip = forwarded
        except ValueError:
            pass
    return ip


def who(request) -> str:
    """Anonymní identifikátor návštěvníka (zasolený hash IP, u IPv6 celá síť /64)."""
    ip = client_ip(request)
    try:
        addr = ipaddress.ip_address(ip)
        if addr.version == 6:
            ip = str(ipaddress.ip_network(f"{addr}/64", strict=False).network_address)
    except ValueError:
        pass
    return hashlib.sha256(_SALT + ip.encode()).hexdigest()[:32]


# --------------------------------------------------------------------------
# Počítání stažení
# --------------------------------------------------------------------------
_lock = threading.Lock()
_db = sqlite3.connect(DB_FILE, check_same_thread=False, isolation_level=None)
_db.execute("CREATE TABLE IF NOT EXISTS uses (job_id TEXT PRIMARY KEY, who TEXT NOT NULL, ts REAL NOT NULL)")
_db.execute("CREATE INDEX IF NOT EXISTS uses_who ON uses (who, ts)")


def _prune(now: float):
    _db.execute("DELETE FROM uses WHERE ts < ?", (now - WINDOW,))


def usage(who_id: str):
    """Vrátí (kolik stažení už bylo využito, za kolik sekund se uvolní nejstarší)."""
    now = time.time()
    with _lock:
        _prune(now)
        used, oldest = _db.execute("SELECT COUNT(*), MIN(ts) FROM uses WHERE who = ?", (who_id,)).fetchone()
    reset_in = max(0, int(oldest + WINDOW - now)) if oldest else None
    return used, reset_in


def reserve(who_id: str, job_id: str, limit: int):
    """Zapíše stažení, pokud je ještě v limitu. Vrátí (True, None), jinak (False, sekundy do uvolnění)."""
    now = time.time()
    with _lock:
        _prune(now)
        used, oldest = _db.execute("SELECT COUNT(*), MIN(ts) FROM uses WHERE who = ?", (who_id,)).fetchone()
        if used >= limit:
            return False, max(1, int(oldest + WINDOW - now))
        _db.execute("INSERT OR REPLACE INTO uses (job_id, who, ts) VALUES (?, ?, ?)", (job_id, who_id, now))
    return True, None


def refund(job_id: str):
    """Vrátí stažení do limitu (když úloha selhala nebo ji uživatel zrušil)."""
    with _lock:
        _db.execute("DELETE FROM uses WHERE job_id = ?", (job_id,))


# --------------------------------------------------------------------------
# Přístupové klíče
# --------------------------------------------------------------------------
def normalize(key: str) -> str:
    """Z 'lmn-abcde-...' udělá 'LMNABCDE...', ať nevadí pomlčky, mezery ani velikost písmen."""
    return re.sub(r"[^A-Z0-9]", "", (key or "").upper())


def _hash(key: str) -> str:
    return hashlib.sha256(normalize(key).encode()).hexdigest()


_keys_cache = {"mtime": None, "records": []}
_keys_lock = threading.Lock()


def _load() -> list:
    try:
        mtime = KEYS_FILE.stat().st_mtime_ns
    except FileNotFoundError:
        return []
    with _keys_lock:
        if _keys_cache["mtime"] != mtime:
            try:
                _keys_cache["records"] = json.loads(KEYS_FILE.read_text("utf-8"))
            except (OSError, ValueError):
                _keys_cache["records"] = []
            _keys_cache["mtime"] = mtime
        return _keys_cache["records"]


def _save(records: list):
    tmp = KEYS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(records, ensure_ascii=False, indent=2), "utf-8")
    try:
        tmp.chmod(0o600)
    except OSError:
        pass
    os.replace(tmp, KEYS_FILE)


def _device_id(sid: str) -> str:
    return hashlib.sha256(_SALT + b"device:" + (sid or "").encode()).hexdigest()[:32]


def _find(key: str) -> Optional[dict]:
    if not key or len(normalize(key)) < 8:
        return None
    h = _hash(key)
    for rec in _load():
        if not rec.get("revoked") and secrets.compare_digest(rec.get("hash", ""), h):
            return rec
    return None


def get_key(key: str, sid: str, bind: bool = False):
    """Ověří klíč pro dané zařízení (sid = cookie prohlížeče).

    Vrátí (záznam, None) když klíč platí pro toto zařízení, jinak (None, důvod), kde důvod je
    "unknown" (neplatí / zrušený) nebo "other_device" (je přiřazený k jinému zařízení).
    S bind=True se klíč při prvním zadání přiřadí k tomuto zařízení (pokud je ještě místo)."""
    rec = _find(key)
    if not rec:
        return None, "unknown"
    dev = _device_id(sid)
    if dev in rec.get("devices", []):
        return rec, None
    if not bind:
        return None, "other_device"
    with _keys_lock:
        records = json.loads(KEYS_FILE.read_text("utf-8"))  # čerstvě z disku, ať nepřepíšeme změnu z keys.py
        for r in records:
            if r.get("hash") == rec["hash"] and not r.get("revoked"):
                devices = r.setdefault("devices", [])
                if dev in devices:
                    return r, None
                if len(devices) >= int(r.get("max_devices", 1)):
                    return None, "other_device"
                devices.append(dev)
                _save(records)
                _keys_cache["mtime"] = None  # vynutí znovunačtení
                return r, None
    return None, "unknown"


def add_key(label: str, max_devices: int = 1) -> str:
    """Vytvoří nový klíč a vrátí ho (jediná chvíle, kdy je vidět celý)."""
    groups = ["".join(secrets.choice(KEY_ALPHABET) for _ in range(5)) for _ in range(4)]
    key = KEY_PREFIX + "-" + "-".join(groups)
    with _keys_lock:
        records = json.loads(KEYS_FILE.read_text("utf-8")) if KEYS_FILE.exists() else []
        next_id = max((r.get("id", 0) for r in records), default=0) + 1
        records.append({
            "id": next_id,
            "label": label.strip()[:60] or f"klíč {next_id}",
            "hash": _hash(key),
            "hint": key[-5:],
            "created": int(time.time()),
            "revoked": False,
            "max_devices": max(1, int(max_devices)),
            "devices": [],
        })
        _save(records)
        _keys_cache["mtime"] = None
    return key


def list_keys() -> list:
    return list(_load())


def revoke_key(ident: str) -> bool:
    """Zruší klíč podle čísla nebo přesného popisku."""
    records = list(_load())
    hit = False
    for r in records:
        if str(r.get("id")) == str(ident) or r.get("label") == ident:
            r["revoked"] = True
            hit = True
    if hit:
        _save(records)
    return hit


def reset_key(ident: str) -> bool:
    """Uvolní přiřazená zařízení klíče (např. po změně telefonu), klíč se pak přiřadí znovu při dalším zadání."""
    with _keys_lock:
        records = json.loads(KEYS_FILE.read_text("utf-8")) if KEYS_FILE.exists() else []
        hit = False
        for r in records:
            if str(r.get("id")) == str(ident) or r.get("label") == ident:
                r["devices"] = []
                hit = True
        if hit:
            _save(records)
            _keys_cache["mtime"] = None
    return hit
