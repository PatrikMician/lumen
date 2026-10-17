"""Knihovna hudby v aplikacích (PC, Android): složka, kam se ukládá stažená hudba a kam jde importovat skladby
ze zařízení. Hudba → V zařízení ji ukáže a hraje z ní. Na serveru (web) je vypnutá: MUSIC_LIBRARY_DIR není nastavené.

PC: ~/Music/Lumen, Android: Android/data/org.mican.lumen/files/Music (nastavuje aplikace).
"""
import os
import re
import shutil
from pathlib import Path
from typing import List

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

LIB_DIR = Path(os.environ["MUSIC_LIBRARY_DIR"]) if os.getenv("MUSIC_LIBRARY_DIR") else None
AUDIO_EXT = {".mp3", ".m4a", ".aac", ".opus", ".ogg", ".oga", ".flac", ".wav", ".weba"}
TYPES = {".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".aac": "audio/aac", ".opus": "audio/ogg", ".ogg": "audio/ogg",
         ".oga": "audio/ogg", ".flac": "audio/flac", ".wav": "audio/wav", ".weba": "audio/webm"}
MAX_IMPORT = 500 * 1024 * 1024  # jeden soubor

router = APIRouter()


def enabled() -> bool:
    return LIB_DIR is not None


def _need():
    if not LIB_DIR:
        raise HTTPException(404, "Knihovna hudby je jen v aplikaci Lumen.")
    LIB_DIR.mkdir(parents=True, exist_ok=True)


def _clean_name(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", Path(name).name).strip(" .")
    return name[:180] or "skladba"


def _unique(name: str) -> Path:
    target = LIB_DIR / name
    stem, ext = target.stem, target.suffix
    n = 2
    while target.exists():
        target = LIB_DIR / f"{stem} ({n}){ext}"
        n += 1
    return target


def add_file(src: Path):
    """Po stažení zvuku: kopie do knihovny (stejný soubor podruhé ne)."""
    if not LIB_DIR or src.suffix.lower() not in AUDIO_EXT:
        return
    LIB_DIR.mkdir(parents=True, exist_ok=True)
    name = _clean_name(src.name)
    existing = LIB_DIR / name
    if existing.exists() and existing.stat().st_size == src.stat().st_size:
        return
    shutil.copy2(src, _unique(name))
    print(f"Knihovna: uloženo {name}", flush=True)


def _resolve(name: str) -> Path:
    path = (LIB_DIR / name).resolve()
    if path.parent != LIB_DIR.resolve() or not path.is_file() or path.suffix.lower() not in AUDIO_EXT:
        raise HTTPException(404, "Skladba v knihovně není.")
    return path


@router.get("/api/library")
def library_list():
    if not LIB_DIR:
        return {"enabled": False, "tracks": []}
    _need()
    tracks = [{"name": f.name, "size": f.stat().st_size, "mtime": f.stat().st_mtime}
              for f in LIB_DIR.iterdir() if f.is_file() and f.suffix.lower() in AUDIO_EXT]
    tracks.sort(key=lambda t: t["name"].lower())
    return {"enabled": True, "dir": str(LIB_DIR), "tracks": tracks, "can_open": os.name == "nt"}


@router.get("/api/library/file/{name}")
def library_file(name: str):
    _need()
    path = _resolve(name)
    return FileResponse(path, media_type=TYPES.get(path.suffix.lower(), "audio/mpeg"))


@router.post("/api/library/import")
def library_import(files: List[UploadFile] = File(...)):
    _need()
    added, skipped = [], []
    for up in files:
        name = _clean_name(up.filename or "")
        if Path(name).suffix.lower() not in AUDIO_EXT:
            skipped.append(name)
            continue
        target = _unique(name)
        size = 0
        with open(target, "wb") as out:
            while True:
                chunk = up.file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_IMPORT:
                    break
                out.write(chunk)
        if size > MAX_IMPORT:
            target.unlink(missing_ok=True)
            skipped.append(name)
            continue
        added.append(target.name)
    if added:
        print(f"Knihovna: importováno {len(added)} skladeb", flush=True)
    return {"added": added, "skipped": skipped}


@router.delete("/api/library/file/{name}")
def library_delete(name: str):
    _need()
    _resolve(name).unlink()
    return {"ok": True}


@router.post("/api/library/open")
def library_open():
    """PC: otevře složku knihovny v Průzkumníku."""
    _need()
    if os.name != "nt":
        raise HTTPException(404, "Tady složku otevřít nejde.")
    os.startfile(str(LIB_DIR))  # noqa: S606 - jen vlastní složka knihovny
    return {"ok": True}
