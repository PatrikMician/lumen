"""Lumen pro Android: server Lumenu v telefonu (spouští ho aplikace jako samostatný proces Pythonu).

Python, ffmpeg, QuickJS a yt-dlp dodává knihovna youtubedl-android, tenhle skript jen nastaví prostředí
a spustí FastAPI na 127.0.0.1:<port>. Klíče a denní limit fungují jako v PC verzi (ověřuje je server Lumenu).
Proměnné prostředí (cesty, port) nastavuje aplikace, tady jsou jen výchozí hodnoty.
"""
import os
import sys
import time


def setup_env():
    defaults = {
        "LUMEN_DESKTOP": "1",
        "LUMEN_PLATFORM": "android",
        "DAILY_LIMIT": "5",
        "MUSIC_REQUIRE_KEY": "1",
        "KEY_SERVER": "https://lumen.mican.dpdns.org",
        "KEEP_HOURS": "6",
        "MAX_PARALLEL": "2",
        "ALLOW_BROWSER_COOKIES": "0",
        "IMPERSONATE": "",  # curl_cffi pro Android není
        "PUBLIC_URL": "",
    }
    for k, v in defaults.items():
        os.environ.setdefault(k, v)


def pydantic_v1_compat():
    """V telefonu je pydantic 1 (pydantic 2 potřebuje nativní knihovnu, která pro Android není)."""
    import pydantic

    if pydantic.VERSION.startswith("1."):
        pydantic.BaseModel.model_dump = pydantic.BaseModel.dict


def main():
    port = int(os.environ.get("LUMEN_PORT", "8765"))
    setup_env()
    pydantic_v1_compat()
    import uvicorn

    from backend.main import app

    print(time.strftime("%Y-%m-%d %H:%M:%S"), f"Lumen {os.getenv('LUMEN_APP_VERSION', '?')} běží na 127.0.0.1:{port}",
          f"(Python {sys.version.split()[0]})", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning", log_config=None)


if __name__ == "__main__":
    main()
