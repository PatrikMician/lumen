"""Lumen pro PC: spustí Lumen na počítači uživatele a otevře ho v okně aplikace.

Všechno běží lokálně (YouTube vidí IP uživatele, ne server). Klíče a denní limit platí stejně jako na webu:
denní limit se počítá v aplikaci, klíče ověřuje a žádosti vyřizuje server Lumenu (KEY_SERVER).
- Server (FastAPI) běží na náhodném portu jen na 127.0.0.1.
- Okno je Microsoft Edge v režimu aplikace (--app) s vlastním profilem; když se zavře, Lumen skončí.
  Bez Edge/Chrome se otevře výchozí prohlížeč a malé okénko „Lumen běží“.
- yt-dlp je přibalený jako obyčejná složka (ne v exe), takže se dá aktualizovat: nová verze z PyPI
  se stáhne do %LOCALAPPDATA%\\Lumen\\pylib a použije se od dalšího spuštění.
- `Lumen.exe --uninstall` odinstaluje (volá ho záznam v Aplikacích Windows).
"""
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import zipfile
from pathlib import Path

APP_VERSION = "1.1.0"
LOCAL = Path(os.getenv("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "Lumen"
PYLIB = LOCAL / "pylib"
INSTANCE = LOCAL / "instance.json"
FROZEN = getattr(sys, "frozen", False)
BUNDLE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))  # _internal / kořen repa

LOCAL.mkdir(parents=True, exist_ok=True)

# Okenní aplikace nemá konzoli (sys.stdout je None) — výpisy jdou do souboru, ať print() nespadne.
if FROZEN or sys.stdout is None:
    _log = open(LOCAL / "lumen.log", "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = _log


def log(msg: str):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), msg, flush=True)


# --------------------------------------------------------------------------
# yt-dlp: aktualizovaná verze má přednost před přibalenou
# --------------------------------------------------------------------------
class _YtdlpFinder:
    """Načte yt_dlp i všechny jeho podmoduly ze složky (aktualizace, jinak přibalená kopie), nikdy z exe.
    Zabalený yt_dlp v exe zůstává jen kvůli tomu, aby PyInstaller přibalil, co yt-dlp ze standardní knihovny potřebuje."""

    def __init__(self, dirs):
        self.dirs = dirs

    def find_spec(self, name, path=None, target=None):
        import importlib.machinery

        if name == "yt_dlp":
            return importlib.machinery.PathFinder.find_spec(name, self.dirs)
        if name.startswith("yt_dlp."):
            return importlib.machinery.PathFinder.find_spec(name, path)
        return None


def ytdlp_paths():
    dirs = []
    cur = PYLIB / "current.txt"
    if cur.exists():
        updated = PYLIB / cur.read_text("utf-8").strip()
        if (updated / "yt_dlp" / "__init__.py").exists():
            dirs.append(str(updated))
    bundled = BUNDLE / "ytdlp_bundled"
    if (bundled / "yt_dlp" / "__init__.py").exists():
        dirs.append(str(bundled))
    if dirs:
        sys.meta_path.insert(0, _YtdlpFinder(dirs))


def _version_tuple(v: str):
    return tuple(int(x) for x in re.findall(r"\d+", v)[:4])


def update_ytdlp():
    """Na pozadí: když je na PyPI novější yt-dlp, stáhne ho (ověří SHA-256) pro příští spuštění."""
    try:
        from yt_dlp.version import __version__ as current
        with urllib.request.urlopen("https://pypi.org/pypi/yt-dlp/json", timeout=20) as r:
            data = json.load(r)
        latest = data["info"]["version"]
        if _version_tuple(latest) <= _version_tuple(current):
            log(f"yt-dlp {current} je aktuální")
            return
        wheel = next(f for f in data["urls"] if f["packagetype"] == "bdist_wheel" and f["filename"].endswith("-py3-none-any.whl"))
        with urllib.request.urlopen(wheel["url"], timeout=120) as r:
            blob = r.read()
        if hashlib.sha256(blob).hexdigest() != wheel["digests"]["sha256"]:
            log("yt-dlp: stažený balík nesedí s kontrolním součtem, aktualizace přeskočena")
            return
        target = PYLIB / f"yt-dlp-{latest}"
        tmp = PYLIB / f".tmp-{latest}"
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True)
        whl = tmp / wheel["filename"]
        whl.write_bytes(blob)
        with zipfile.ZipFile(whl) as z:
            for name in z.namelist():
                if name.startswith("yt_dlp/"):
                    z.extract(name, tmp)
        whl.unlink()
        shutil.rmtree(target, ignore_errors=True)
        tmp.rename(target)
        (PYLIB / "current.txt").write_text(target.name, "utf-8")
        for old in PYLIB.glob("yt-dlp-*"):
            if old != target:
                shutil.rmtree(old, ignore_errors=True)
        log(f"yt-dlp aktualizován {current} → {latest}, platí od dalšího spuštění")
    except Exception as e:  # noqa: BLE001 - bez internetu nebo změna PyPI: Lumen jede dál se svou verzí
        log(f"yt-dlp: aktualizace se nepovedla: {type(e).__name__}: {e}")


# --------------------------------------------------------------------------
# Server
# --------------------------------------------------------------------------
def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def alive(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


def setup_env():
    defaults = {
        "LUMEN_DESKTOP": "1",
        "DATA_DIR": str(LOCAL / "data"),
        "DOWNLOAD_DIR": str(LOCAL / "downloads"),
        "DAILY_LIMIT": "5",            # stejně jako na webu; klíče ověřuje server Lumenu
        "MUSIC_REQUIRE_KEY": "1",
        "KEY_SERVER": "https://lumen.mican.dpdns.org",
        "LUMEN_APP_VERSION": APP_VERSION,
        "KEEP_HOURS": "6",             # soubor si prohlížeč hned uloží do Stažených souborů
        "MAX_PARALLEL": "3",
        "ALLOW_BROWSER_COOKIES": "1",  # prohlížeč je na stejném počítači, přihlášení z něj dává smysl
        "JS_RUNTIMES": "deno,node,bun",
        "PUBLIC_URL": "",
    }
    for k, v in defaults.items():
        os.environ.setdefault(k, v)


def start_server(port: int):
    import uvicorn

    from backend.main import app  # noqa: E402 - až po nastavení prostředí a cest k yt-dlp

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", log_config=None)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    return server, thread


# --------------------------------------------------------------------------
# Okno
# --------------------------------------------------------------------------
def find_browser():
    pf86 = os.getenv("PROGRAMFILES(X86)", r"C:\Program Files (x86)")
    pf = os.getenv("PROGRAMFILES", r"C:\Program Files")
    la = os.getenv("LOCALAPPDATA", "")
    for path in (
        rf"{pf86}\Microsoft\Edge\Application\msedge.exe",
        rf"{pf}\Microsoft\Edge\Application\msedge.exe",
        rf"{la}\Microsoft\Edge\Application\msedge.exe",
        rf"{pf}\Google\Chrome\Application\chrome.exe",
        rf"{pf86}\Google\Chrome\Application\chrome.exe",
        rf"{la}\Google\Chrome\Application\chrome.exe",
    ):
        if path and Path(path).is_file():
            return path
    return shutil.which("msedge") or shutil.which("chrome")


def open_window(port: int) -> subprocess.Popen | None:
    browser = find_browser()
    url = f"http://127.0.0.1:{port}/"
    if not browser:
        import webbrowser
        webbrowser.open(url)
        return None
    return subprocess.Popen([
        browser, f"--app={url}", f"--user-data-dir={LOCAL / 'okno'}", "--no-first-run",
        "--no-default-browser-check", "--disable-sync", "--window-size=1240,860",
    ])


def keep_alive_window(port: int):
    """Bez Edge/Chrome: malé okénko, jehož zavřením se Lumen ukončí."""
    import tkinter as tk

    root = tk.Tk()
    root.title("Lumen")
    root.geometry("340x130")
    tk.Label(root, text="Lumen běží v prohlížeči.\nZavřením tohoto okna ho ukončíš.", pady=14).pack()
    tk.Button(root, text="Otevřít znovu", command=lambda: __import__("webbrowser").open(f"http://127.0.0.1:{port}/")).pack()
    root.mainloop()


def message(title: str, text: str, error: bool = False):
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, title, 0x10 if error else 0x40)
    except Exception:  # noqa: BLE001
        log(f"{title}: {text}")


# --------------------------------------------------------------------------
# Odinstalace (Aplikace → Lumen → Odinstalovat)
# --------------------------------------------------------------------------
def uninstall():
    import ctypes
    import winreg

    yes = 6
    if ctypes.windll.user32.MessageBoxW(None, "Opravdu odinstalovat Lumen?", "Odinstalace Lumenu", 0x24) != yes:
        return
    remove_data = ctypes.windll.user32.MessageBoxW(
        None, "Smazat i data Lumenu (nastavení okna, mezipaměť hudby, rozpracovaná stažení)?\n"
              "Soubory, které sis stáhl do složky Stažené soubory, zůstanou.", "Odinstalace Lumenu", 0x24) == yes
    appdata = Path(os.getenv("APPDATA", ""))
    for lnk in (appdata / "Microsoft/Windows/Start Menu/Programs/Lumen.lnk", Path.home() / "Desktop/Lumen.lnk"):
        lnk.unlink(missing_ok=True)
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Lumen")
    except OSError:
        pass
    install_dir = Path(sys.executable).resolve().parent
    targets = [str(install_dir)] + ([str(LOCAL)] if remove_data else [])
    # běžící exe se samo smazat nedá: po ukončení to udělá cmd
    cmd = "timeout /t 2 /nobreak >nul & " + " & ".join(f'rmdir /s /q "{t}"' for t in targets)
    subprocess.Popen(["cmd", "/c", cmd], creationflags=0x08000000)  # CREATE_NO_WINDOW
    message("Odinstalace Lumenu", "Lumen je odinstalovaný.")


# --------------------------------------------------------------------------
def main():
    if "--uninstall" in sys.argv:
        uninstall()
        return
    # druhé spuštění: Lumen už běží, jen se otevře další okno
    try:
        info = json.loads(INSTANCE.read_text("utf-8"))
        if alive(int(info["port"])):
            open_window(int(info["port"]))
            return
    except Exception:  # noqa: BLE001
        pass

    log(f"Lumen {APP_VERSION} startuje")
    setup_env()
    ytdlp_paths()
    port = free_port()
    try:
        server, _ = start_server(port)
    except Exception as e:  # noqa: BLE001
        log(f"Server se nespustil: {type(e).__name__}: {e}")
        message("Lumen", f"Lumen se nepodařilo spustit:\n{e}\n\nPodrobnosti jsou v {LOCAL / 'lumen.log'}", error=True)
        return
    for _ in range(150):
        if alive(port):
            break
        time.sleep(0.2)
    else:
        message("Lumen", f"Lumen se nespustil včas. Podrobnosti jsou v {LOCAL / 'lumen.log'}", error=True)
        return
    INSTANCE.write_text(json.dumps({"port": port, "pid": os.getpid()}), "utf-8")
    threading.Thread(target=update_ytdlp, daemon=True).start()
    log(f"běží na http://127.0.0.1:{port}/")

    window = open_window(port)
    try:
        if window is None:
            keep_alive_window(port)
        else:
            window.wait()
    finally:
        INSTANCE.unlink(missing_ok=True)
        server.should_exit = True
        time.sleep(0.5)
        log("Lumen ukončen")


if __name__ == "__main__":
    main()
