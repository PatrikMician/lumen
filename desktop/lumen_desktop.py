"""Lumen pro PC: spustí Lumen na počítači uživatele a otevře ho v okně aplikace.

Všechno běží lokálně (YouTube vidí IP uživatele, ne server). Klíče a denní limit platí stejně jako na webu:
denní limit se počítá v aplikaci, klíče ověřuje a žádosti vyřizuje server Lumenu (KEY_SERVER).
- Server (FastAPI) běží na náhodném portu jen na 127.0.0.1.
- Okno je vlastní okno aplikace (pywebview + Microsoft WebView2, stejný engine jako Edge); když se zavře, Lumen skončí.
  Když WebView2 v systému chybí, Lumen ho doinstaluje (oficiální instalátor od Microsoftu). Teprve když to nejde,
  použije okno prohlížeče v režimu aplikace (Edge, Chrome, Brave…), úplně nakonec výchozí prohlížeč.
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

APP_VERSION = "1.1.1"
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
# Prohlížeče, které umí okno aplikace (--app); Firefox to neumí.
APP_BROWSERS = ("msedge", "chrome", "brave", "vivaldi", "chromium", "browser")  # browser.exe = Yandex


def _reg_value(root, key: str, name: str = ""):
    import winreg

    try:
        with winreg.OpenKey(root, key) as k:
            return winreg.QueryValueEx(k, name)[0]
    except OSError:
        return None


def _default_browser() -> str | None:
    """Výchozí prohlížeč Windows (UserChoice → příkaz pro otevření odkazu)."""
    import winreg

    prog = _reg_value(winreg.HKEY_CURRENT_USER,
                      r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice", "ProgId")
    cmd = prog and _reg_value(winreg.HKEY_CLASSES_ROOT, rf"{prog}\shell\open\command")
    if not cmd:
        return None
    m = re.match(r'\s*"([^"]+)"|\s*(\S+)', cmd)
    return (m.group(1) or m.group(2)) if m else None


def find_browser():
    """Prohlížeč pro okno aplikace: výchozí, když umí --app, jinak Edge, Chrome, Brave… (cesty i registr App Paths).
    Bez nich Lumen otevře výchozí prohlížeč jako obyčejnou kartu."""
    import winreg

    candidates = []
    try:
        candidates.append(_default_browser())
    except Exception:  # noqa: BLE001
        pass
    for exe in ("msedge.exe", "chrome.exe", "brave.exe", "vivaldi.exe", "chromium.exe"):
        for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            candidates.append(_reg_value(root, rf"Software\Microsoft\Windows\CurrentVersion\App Paths\{exe}"))
    pf86 = os.getenv("PROGRAMFILES(X86)", r"C:\Program Files (x86)")
    pf = os.getenv("PROGRAMFILES", r"C:\Program Files")
    la = os.getenv("LOCALAPPDATA", "")
    for base in (pf86, pf, la):
        candidates += [
            rf"{base}\Microsoft\Edge\Application\msedge.exe",
            rf"{base}\Google\Chrome\Application\chrome.exe",
            rf"{base}\BraveSoftware\Brave-Browser\Application\brave.exe",
            rf"{base}\Vivaldi\Application\vivaldi.exe",
        ]
    candidates += [shutil.which(n) for n in ("msedge", "chrome", "brave")]
    for path in candidates:
        if path and Path(path).stem.lower() in APP_BROWSERS and Path(path).is_file():
            return path
    return None


# --------------------------------------------------------------------------
# Vlastní okno: WebView2
# --------------------------------------------------------------------------
WEBVIEW2_ID = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
WEBVIEW2_SETUP = "https://go.microsoft.com/fwlink/p/?LinkId=2124703"  # oficiální Evergreen Bootstrapper


def webview2_version() -> str | None:
    import winreg

    for root, key in (
        (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_ID}"),
        (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_ID}"),
        (winreg.HKEY_CURRENT_USER, rf"Software\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_ID}"),
    ):
        v = _reg_value(root, key, "pv")
        if v and v != "0.0.0.0":
            return v
    return None


def _signed_by_microsoft(path: Path) -> bool:
    ps = ("$s = Get-AuthenticodeSignature -LiteralPath $env:LUMEN_CHECK; "
          "if ($s.Status -eq 'Valid' -and $s.SignerCertificate.Subject -like '*O=Microsoft Corporation*') { 'ok' }")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], capture_output=True,
                             text=True, timeout=60, env={**os.environ, "LUMEN_CHECK": str(path)},
                             creationflags=0x08000000)
        return out.stdout.strip() == "ok"
    except Exception:  # noqa: BLE001
        return False


def ensure_webview2() -> bool:
    """WebView2 je ve Windows 11 i většině Windows 10. Když chybí, stáhne a spustí oficiální instalátor."""
    if webview2_version():
        return True
    log("WebView2 chybí, instaluji ho")
    message("Lumen", "Lumen si pro své okno doinstaluje komponentu Microsoft WebView2.\n"
                     "Potrvá to asi minutu, pak se Lumen otevře sám.")
    setup = LOCAL / "MicrosoftEdgeWebview2Setup.exe"
    try:
        with urllib.request.urlopen(WEBVIEW2_SETUP, timeout=60) as r:
            setup.write_bytes(r.read())
        if not _signed_by_microsoft(setup):
            log("WebView2: instalátor není podepsaný Microsoftem, nespouštím ho")
            return False
        subprocess.run([str(setup), "/silent", "/install"], timeout=600)
    except Exception as e:  # noqa: BLE001
        log(f"WebView2: instalace se nepovedla: {type(e).__name__}: {e}")
    finally:
        setup.unlink(missing_ok=True)
    version = webview2_version()
    log(f"WebView2: {version or 'pořád chybí'}")
    return bool(version)


def run_app_window(port: int) -> bool:
    """Vlastní okno Lumenu. Čeká, dokud ho uživatel nezavře. False = okno se otevřít nepodařilo."""
    try:
        import webview

        webview.settings["ALLOW_DOWNLOADS"] = True  # „Uložit“ uloží soubor do Stažených souborů
        webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
        webview.create_window("Lumen", f"http://127.0.0.1:{port}/", width=1240, height=860, min_size=(420, 560),
                              background_color="#1B1730")
        # private_mode=False + vlastní složka: přihlášení klíčem a nastavení zůstanou i po zavření
        webview.start(gui="edgechromium", private_mode=False, storage_path=str(LOCAL / "webview"))
        return True
    except Exception as e:  # noqa: BLE001
        log(f"Vlastní okno se nepodařilo otevřít: {type(e).__name__}: {e}")
        return False


def show(port: int):
    """Okno Lumenu: vlastní (WebView2), záložně okno prohlížeče v režimu aplikace, nakonec karta v prohlížeči."""
    if ensure_webview2() and run_app_window(port):
        return
    window = open_window(port)
    if window is None:
        keep_alive_window(port)
    else:
        window.wait()


def open_window(port: int) -> subprocess.Popen | None:
    browser = find_browser()
    url = f"http://127.0.0.1:{port}/"
    log(f"okno: {browser or 'žádný prohlížeč s režimem aplikace, otevírám výchozí prohlížeč'}")
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
            show(int(info["port"]))
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

    try:
        show(port)
    finally:
        INSTANCE.unlink(missing_ok=True)
        server.should_exit = True
        time.sleep(0.5)
        log("Lumen ukončen")


if __name__ == "__main__":
    main()
