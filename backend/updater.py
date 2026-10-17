"""Aktualizace aplikací (PC, Android) ze serveru Lumenu.

Server je zapnutý jen při vydání nové verze, aplikace proto kontroluje na pozadí (PC: naplánovaná úloha
`Lumen.exe --check-update`, Android: WorkManager) a v otevřené aplikaci (/api/app). Samotná aktualizace:
stránka /update.html zavolá /api/app/update/start, sleduje průběh stahování (/api/app/update/status) a pak
- PC: spustí `LumenSetup-<verze>.exe --update` a Lumen se ukončí (instalátor ho po instalaci spustí znovu),
- Android: APK nainstaluje aplikace (LumenAndroid.installUpdate), stažené je v UPDATE_DIR.
"""
import hashlib
import os
import subprocess
import threading
import urllib.request
from pathlib import Path

from fastapi import APIRouter, HTTPException

from . import quota, remotekeys

UPDATE_DIR = Path(os.getenv("UPDATE_DIR") or quota.DATA_DIR / "update")
PLATFORM = os.getenv("LUMEN_PLATFORM", "windows")
router = APIRouter()
_lock = threading.Lock()
state = {"phase": "idle", "percent": 0.0, "version": None, "error": None, "file": None, "platform": PLATFORM}


def _set(**kw):
    with _lock:
        state.update(kw)


def _run():
    try:
        info = remotekeys.app_update(force=True).get("update")
        if not info:
            raise RuntimeError("Server Lumenu teď žádnou novou verzi nenabízí (nebo není zapnutý). Zkus to později.")
        _set(version=info["version"], phase="downloading", percent=0.0)
        UPDATE_DIR.mkdir(parents=True, exist_ok=True)
        for old in UPDATE_DIR.iterdir():
            old.unlink(missing_ok=True)
        name = f"LumenSetup-{info['version']}.exe" if PLATFORM == "windows" else f"Lumen-{info['version']}.apk"
        part = UPDATE_DIR / (name + ".part")
        digest = hashlib.sha256()
        req = urllib.request.Request(info["url"], headers={"User-Agent": f"LumenDesktop/{remotekeys.APP_VERSION}"})
        with urllib.request.urlopen(req, timeout=30) as r, open(part, "wb") as out:
            total = int(r.headers.get("Content-Length") or info.get("size") or 0)
            done = 0
            while True:
                chunk = r.read(256 * 1024)
                if not chunk:
                    break
                out.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                if total:
                    _set(percent=min(99.0, done * 100 / total))
        if total and done != total:
            raise RuntimeError("Stahování se přerušilo. Zkus to znovu.")
        if info.get("sha256") and digest.hexdigest() != info["sha256"]:
            raise RuntimeError("Stažený soubor nesedí s kontrolním součtem. Zkus to znovu.")
        target = UPDATE_DIR / name
        os.replace(part, target)
        print(f"Aktualizace: staženo {name} ({done // 1024} kB)", flush=True)
        if PLATFORM == "windows":
            _set(phase="installing", percent=100.0, file=str(target))
            flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
            subprocess.Popen([str(target), "--update"], creationflags=flags, close_fds=True)
            # instalátor počká, až se Lumen ukončí; stránka mezitím ukáže „Instaluji…“
            threading.Timer(2.0, lambda: os._exit(0)).start()
        else:
            _set(phase="ready", percent=100.0, file=str(target))
    except Exception as e:  # noqa: BLE001
        print(f"Aktualizace: {type(e).__name__}: {e}", flush=True)
        _set(phase="error", error=str(e))


@router.post("/api/app/update/start")
def update_start():
    if not remotekeys.enabled():
        raise HTTPException(404, "Aktualizace jsou jen v aplikaci Lumen.")
    with _lock:
        if state["phase"] in ("downloading", "installing"):
            return dict(state)
        state.update(phase="checking", percent=0.0, error=None, file=None)
    threading.Thread(target=_run, daemon=True).start()
    return dict(state)


@router.get("/api/app/update/status")
def update_status():
    with _lock:
        return dict(state)
