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
from fastapi.responses import FileResponse, Response

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


_tag_cache: dict = {}


def tags(path: Path) -> dict:
    """Štítky souboru (mutagen): název, interpret, album, délka, datový tok, jestli má obal."""
    st = path.stat()
    key = (path.name, st.st_mtime, st.st_size)
    if key in _tag_cache:
        return _tag_cache[key]
    out = {}
    try:
        from mutagen import File as MFile

        easy = MFile(path, easy=True)
        if easy is not None:
            first = lambda k: (easy.get(k) or [None])[0]  # noqa: E731
            out = {"title": first("title"), "artist": first("artist"), "album": first("album")}
            if easy.info:
                out["duration"] = round(getattr(easy.info, "length", 0) or 0) or None
                out["bitrate"] = round((getattr(easy.info, "bitrate", 0) or 0) / 1000) or None
        out["cover"] = _cover(path) is not None
    except Exception:  # noqa: BLE001 - poškozený nebo neznámý soubor: jen bez štítků
        pass
    if len(_tag_cache) > 5000:
        _tag_cache.clear()
    _tag_cache[key] = out
    return out


def _cover(path: Path):
    """(data, typ) vloženého obalu, nebo None."""
    from mutagen import File as MFile

    f = MFile(path)
    if f is None:
        return None
    t = getattr(f, "tags", None)
    if t is not None and hasattr(t, "getall"):  # MP3 (ID3)
        pics = t.getall("APIC")
        if pics:
            return pics[0].data, pics[0].mime or "image/jpeg"
    if t is not None and "covr" in t:  # M4A
        c = t["covr"][0]
        return bytes(c), "image/png" if getattr(c, "imageformat", 0) == 14 else "image/jpeg"
    pics = getattr(f, "pictures", None)  # FLAC
    if pics:
        return pics[0].data, pics[0].mime or "image/jpeg"
    if t is not None and "metadata_block_picture" in t:  # Opus / Ogg
        import base64

        from mutagen.flac import Picture

        p = Picture(base64.b64decode(t["metadata_block_picture"][0]))
        return p.data, p.mime or "image/jpeg"
    return None


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
    tracks = [{"name": f.name, "size": f.stat().st_size, "mtime": f.stat().st_mtime, **tags(f)}
              for f in LIB_DIR.iterdir() if f.is_file() and f.suffix.lower() in AUDIO_EXT]
    tracks.sort(key=lambda t: t["name"].lower())
    return {"enabled": True, "dir": str(LIB_DIR), "tracks": tracks, "can_open": os.name == "nt"}


@router.get("/api/library/file/{name}")
def library_file(name: str):
    _need()
    path = _resolve(name)
    return FileResponse(path, media_type=TYPES.get(path.suffix.lower(), "audio/mpeg"))


@router.get("/api/library/cover/{name}")
def library_cover(name: str):
    _need()
    c = _cover(_resolve(name))
    if not c:
        raise HTTPException(404, "Skladba nemá obal.")
    return Response(c[0], media_type=c[1], headers={"Cache-Control": "private, max-age=86400"})


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
