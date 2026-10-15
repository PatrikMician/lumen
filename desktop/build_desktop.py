"""Sestaví Lumen pro PC: dist/Lumen (aplikace) a dist/LumenSetup-<verze>.exe (instalátor).

Spouštět ve složce projektu z prostředí, kde jsou závislosti Lumenu i sestavovací nástroje:
    python -m venv .venv-desktop
    .venv-desktop\\Scripts\\pip install -r requirements.txt -r desktop/requirements-desktop.txt
    .venv-desktop\\Scripts\\python desktop/build_desktop.py
"""
import importlib.util
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DESKTOP = ROOT / "desktop"
BUILD = ROOT / "build" / "desktop"
DIST = ROOT / "dist"
sys.path.insert(0, str(DESKTOP))
from lumen_desktop import APP_VERSION  # noqa: E402

# yt-dlp jde do balíčku i jako obyčejná složka (kvůli aktualizacím, viz _YtdlpFinder), jeho závislosti normálně dovnitř
YTDLP_DEPS = ["curl_cffi", "websockets", "brotli", "mutagen", "certifi", "Cryptodome", "requests", "urllib3", "yt_dlp_ejs"]


def pkg_dir(name: str) -> Path:
    spec = importlib.util.find_spec(name)
    if not spec or not spec.origin:
        raise SystemExit(f"Chybí balík {name}, nainstaluj requirements.txt")
    return Path(spec.origin).parent


def draw_logo(size: int = 256):
    """Kompaktní logo (static/logo-small.svg) nakreslené v Pillow: 4× větší a zmenšené, ať jsou hrany hladké."""
    import math

    from PIL import Image, ImageDraw

    S = size * 4
    k = S / 100  # souřadnice z SVG (viewBox 0 0 100 100)
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # paprsky: elipsa rx 3.2, ry 6.2 se středem (50, 7), otočená po 45° kolem středu
    for n in range(8):
        a = math.radians(45 * n)
        pts = []
        for i in range(48):
            t = 2 * math.pi * i / 48
            x, y = 3.2 * math.cos(t), 6.2 * math.sin(t) - 43
            pts.append(((50 + x * math.cos(a) - y * math.sin(a)) * k, (50 + x * math.sin(a) + y * math.cos(a)) * k))
        d.polygon(pts, fill=(0xFF, 0xC9, 0x3C, 255))
    # slunce s přechodem #FFC800 → #FF9500 (zleva shora doprava dolů)
    grad = Image.new("RGBA", (S, S))
    gp = grad.load()
    x1, y1, x2, y2 = 0.2 * S, 0.15 * S, 0.85 * S, 0.9 * S
    dx, dy = x2 - x1, y2 - y1
    ln = dx * dx + dy * dy
    for yy in range(S):
        for xx in range(0, S, 4):
            t = min(1, max(0, ((xx - x1) * dx + (yy - y1) * dy) / ln))
            c = (255, int(0xC8 + (0x95 - 0xC8) * t), 0, 255)
            for j in range(4):
                gp[xx + j, yy] = c
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).ellipse(((50 - 32) * k, (50 - 32) * k, (50 + 32) * k, (50 + 32) * k), fill=255)
    img.paste(grad, (0, 0), mask)
    # ikona stažení: bílé čáry s kulatými konci
    w = 5.4 * k
    white = (255, 255, 255, 255)

    def line(points):
        pts = [(x * k, y * k) for x, y in points]
        d.line(pts, fill=white, width=int(w), joint="curve")
        for x, y in pts:
            d.ellipse((x - w / 2, y - w / 2, x + w / 2, y + w / 2), fill=white)

    line([(50, 31), (50, 58.3)])
    line([(39.4, 47.9), (50, 58.5), (60.6, 47.9)])
    tray = [(31.4, 57.3), (31.4, 64.6)]
    tray += [(34.8 - 3.4 * math.cos(math.radians(a)), 64.6 + 3.4 * math.sin(math.radians(a))) for a in range(0, 91, 15)]
    tray += [(65.2 + 3.4 * math.cos(math.radians(a)), 64.6 + 3.4 * math.sin(math.radians(a))) for a in range(90, -1, -15)]
    tray += [(68.6, 57.3)]
    line(tray)
    return img.resize((size, size), Image.LANCZOS)


def make_icon() -> Path:
    ico = BUILD / "lumen.ico"
    draw_logo(256).save(ico, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    return ico


def pyinstaller(*args: str):
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--log-level", "WARN",
           "--distpath", str(DIST), "--workpath", str(BUILD / "work"), "--specpath", str(BUILD), *args]
    print("+", " ".join(cmd[3:8]), "…", flush=True)
    subprocess.run(cmd, check=True, cwd=ROOT)


def main():
    BUILD.mkdir(parents=True, exist_ok=True)
    ico = make_icon()
    sep = ";"  # oddělovač zdroj;cíl pro --add-data na Windows

    collect = []
    for dep in YTDLP_DEPS:
        if importlib.util.find_spec(dep):
            collect += ["--collect-all", dep]
    pyinstaller(
        str(DESKTOP / "lumen_desktop.py"), "--name", "Lumen", "--onedir", "--windowed", "--icon", str(ico),
        "--paths", str(ROOT),
        "--add-data", f"{ROOT / 'static'}{sep}static",
        "--add-data", f"{pkg_dir('yt_dlp')}{sep}ytdlp_bundled/yt_dlp",
        "--hidden-import", "yt_dlp",  # jen kvůli závislostem; za běhu se yt_dlp načítá ze složky
        "--exclude-module", "Cryptodome.SelfTest",
        "--hidden-import", "backend.main", "--hidden-import", "backend.music",
        "--collect-submodules", "uvicorn", "--collect-all", "imageio_ffmpeg", "--collect-all", "ytmusicapi",
        "--collect-all", "webview",  # vlastní okno (WebView2), včetně DLL pro WinForms
        *collect,
    )
    app_dir = DIST / "Lumen"
    payload = BUILD / "payload.zip"
    with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for f in sorted(app_dir.rglob("*")):
            if f.is_file() and "__pycache__" not in f.parts:
                z.write(f, f.relative_to(app_dir))
    print(f"payload {payload.stat().st_size / 1e6:.0f} MB", flush=True)

    setup_name = f"LumenSetup-{APP_VERSION}"
    pyinstaller(
        str(DESKTOP / "setup_app.py"), "--name", setup_name, "--onefile", "--windowed", "--icon", str(ico),
        "--add-data", f"{payload}{sep}.",
    )
    setup = DIST / f"{setup_name}.exe"
    print(f"\nHotovo: {setup} ({setup.stat().st_size / 1e6:.0f} MB)")


if __name__ == "__main__":
    main()
