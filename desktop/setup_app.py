"""Instalátor Lumenu pro Windows (LumenSetup.exe).

Bez administrátorských práv: rozbalí přibalený payload.zip do %LOCALAPPDATA%\\Programs\\Lumen, vytvoří zástupce
v nabídce Start (a volitelně na ploše) a zapíše Lumen do Aplikací Windows (HKCU), odkud jde odinstalovat.
`LumenSetup.exe --silent` nainstaluje bez okna (pro testy). `LumenSetup.exe --update` (spouští ho Lumen při
aktualizaci): počká, až se Lumen ukončí, nainstaluje bez otázek s ukazatelem průběhu a Lumen znovu spustí.
Stará verze se před instalací vždy smaže (běžící Lumen se nejdřív zavře), takže nová verze jde nainstalovat
přes starou bez odinstalace. Po instalaci instalátor nabídne, že se sám smaže (při aktualizaci to udělá sám).
"""
import os
import shutil
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path

APP_VERSION = "1.2.4"
PAYLOAD = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "payload.zip"
TARGET = Path(os.getenv("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "Programs" / "Lumen"
START_MENU = Path(os.getenv("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Lumen.lnk"
DESKTOP = Path.home() / "Desktop" / "Lumen.lnk"
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Lumen"
NO_WINDOW = 0x08000000

def clean_env() -> dict:
    """Prostředí pro spuštění jiného zabaleného programu (Lumen ↔ instalátor). Bez toho nový proces zdědí
    proměnné PyInstalleru (_PYI_*) a nespustí se (OSError: Invalid argument: …Lumen.exe)."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("_PYI") and k != "_MEIPASS2"}
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return env


def launch_lumen(exe: Path):
    subprocess.Popen([str(exe)], cwd=str(exe.parent), env=clean_env())


def stop_lumen(wait: int = 15):
    """Zavře běžící Lumen (i proces na pozadí, třeba kontrolu aktualizace), jinak by nešel přepsat."""
    if not lumen_running():
        return
    # nejdřív slušně (jako zavření okna křížkem), Lumen se pak ukončí sám
    subprocess.run(["taskkill", "/IM", "Lumen.exe"], capture_output=True, creationflags=NO_WINDOW)
    end = time.time() + wait
    while lumen_running() and time.time() < end:
        time.sleep(0.5)
    if lumen_running():
        subprocess.run(["taskkill", "/F", "/T", "/IM", "Lumen.exe"], capture_output=True, creationflags=NO_WINDOW)
        time.sleep(1.5)


def remove_old():
    """Smaže starou verzi: obsah složky, ne složku samotnou (tu mohl mít jiný proces jako pracovní složku,
    pak ji Windows nedovolí smazat ani přejmenovat). Zamčený soubor zkusí znovu, nakonec ho jen odsune."""
    for old in TARGET.parent.glob("Lumen.old-*"):
        shutil.rmtree(old, ignore_errors=True)
    if not TARGET.exists():
        return
    stuck = []
    for attempt in range(6):
        stuck = []
        for item in list(TARGET.iterdir()):
            try:
                shutil.rmtree(item) if item.is_dir() and not item.is_symlink() else item.unlink()
            except OSError:
                stuck.append(item)
        if not stuck:
            return
        stop_lumen(wait=2)
        time.sleep(1)
    aside = TARGET.parent / f"Lumen.old-{int(time.time())}"
    for item in stuck:
        try:
            aside.mkdir(exist_ok=True)
            item.rename(aside / item.name)  # zamčený soubor jde aspoň přesunout, smaže se příště
        except OSError as e:
            raise RuntimeError(f"Starou verzi Lumenu se nepodařilo smazat: soubor {item} je zamčený ({e.strerror}). "
                               "Zavři Lumen, případně restartuj počítač, a spusť instalaci znovu.")


def delete_later(path: Path):
    """Smaže soubor, až ho nic nedrží: cmd to zkouší 60 s. Příkaz jde jako řetězec (seznam by subprocess
    obalil uvozovkami po svém a cmd by mu nerozuměl). Mimo „job“ zavaděče PyInstalleru, ať přežije instalátor."""
    loop = f'for /l %i in (1,1,60) do (ping -n 2 127.0.0.1 >nul & del /f /q "{path}" 2>nul & if not exist "{path}" exit)'
    for flags in (NO_WINDOW | 0x01000000, NO_WINDOW):  # CREATE_BREAKAWAY_FROM_JOB, kdyby nešlo, bez něj
        try:
            subprocess.Popen(f'cmd /d /c "{loop}"', creationflags=flags, env=clean_env(),
                             cwd=str(path.parent.parent), close_fds=True)
            return
        except OSError:
            continue


def delete_self_later():
    """Smaže tenhle instalační soubor, až skončí (běžící exe se smazat nedá)."""
    me = Path(sys.executable)
    if getattr(sys, "frozen", False) and me.suffix.lower() == ".exe":
        delete_later(me)


def shortcut(lnk: Path, exe: Path):
    lnk.parent.mkdir(parents=True, exist_ok=True)
    ps = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:LNK); "
        "$s.TargetPath = $env:EXE; $s.WorkingDirectory = $env:DIR; $s.IconLocation = $env:EXE + ',0'; "
        "$s.Description = 'Lumen: stahování videí a hudby'; $s.Save()"
    )
    env = {**os.environ, "LNK": str(lnk), "EXE": str(exe), "DIR": str(exe.parent)}
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], env=env, check=True,
                   creationflags=0x08000000, capture_output=True)


def register(exe: Path, size_kb: int):
    import winreg

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as k:
        for name, value in {
            "DisplayName": "Lumen", "DisplayVersion": APP_VERSION, "Publisher": "Patrik Mičian",
            "InstallLocation": str(exe.parent), "DisplayIcon": f"{exe},0",
            "UninstallString": f'"{exe}" --uninstall', "URLInfoAbout": "https://lumen.mican.dpdns.org",
        }.items():
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, value)
        winreg.SetValueEx(k, "EstimatedSize", 0, winreg.REG_DWORD, size_kb)
        winreg.SetValueEx(k, "NoModify", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(k, "NoRepair", 0, winreg.REG_DWORD, 1)


def lumen_running() -> bool:
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq Lumen.exe", "/NH"], capture_output=True, text=True,
                         creationflags=0x08000000).stdout
    return "Lumen.exe" in out


def install(desktop: bool, progress=lambda done, total: None) -> Path:
    stop_lumen()
    remove_old()  # stará verze pryč (data uživatele jsou jinde, v %LOCALAPPDATA%\Lumen)
    TARGET.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(PAYLOAD) as z:
        items = z.infolist()
        total = sum(i.file_size for i in items) or 1
        done = 0
        for i in items:
            z.extract(i, TARGET)
            done += i.file_size
            progress(done, total)
    exe = TARGET / "Lumen.exe"
    if not exe.exists():
        raise RuntimeError("V instalačním balíčku chybí Lumen.exe.")
    shortcut(START_MENU, exe)
    if desktop:
        shortcut(DESKTOP, exe)
    register(exe, total // 1024)
    return exe


def make_window(title: str):
    """Okno instalátoru: ostré při zvětšení písma ve Windows (DPI), s ikonou Lumenu, velikost podle obsahu."""
    import ctypes
    import tkinter as tk

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # jinak je okno rozmazané a při 125 %+ se obsah ořízne
    except Exception:  # noqa: BLE001
        pass
    root = tk.Tk()
    root.title(title)
    root.resizable(False, False)
    try:
        root.iconbitmap(default=sys.executable)  # ikona z exe (místo pírka Tk)
    except Exception:  # noqa: BLE001
        pass
    return root


def fit(root):
    """Po vložení obsahu: okno přesně na obsah a doprostřed obrazovky."""
    root.update_idletasks()
    w, h = root.winfo_reqwidth(), root.winfo_reqheight()
    x = (root.winfo_screenwidth() - w) // 2
    y = (root.winfo_screenheight() - h) // 3
    root.geometry(f"{w}x{h}+{x}+{y}")


def gui():
    import tkinter as tk
    from tkinter import ttk

    root = make_window("Instalace Lumenu")
    frame = ttk.Frame(root, padding=20)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text=f"Lumen {APP_VERSION}", font=("Segoe UI", 16, "bold")).pack(anchor="w")
    ttk.Label(frame, text="Stahování videí a hudby přímo na tvém počítači.\n"
                          f"Nainstaluje se do {TARGET}\n(bez práv správce).", justify="left").pack(anchor="w", pady=(6, 10))
    want_desktop = tk.BooleanVar(value=True)
    ttk.Checkbutton(frame, text="Zástupce na ploše", variable=want_desktop).pack(anchor="w")
    bar = ttk.Progressbar(frame, maximum=1000, length=420)
    bar.pack(fill="x", pady=12)
    status = ttk.Label(frame, text="", wraplength=420, justify="left")
    status.pack(anchor="w")
    buttons = ttk.Frame(frame)
    buttons.pack(anchor="e", pady=(8, 0))
    state = {"exe": None}

    def run_install():
        from tkinter import messagebox

        if lumen_running() and not messagebox.askyesno(
                "Instalace Lumenu", "Lumen teď běží. Zavřít ho a pokračovat v instalaci?\n"
                                    "(Rozpracovaná stažení se přeruší.)"):
            return
        go.config(state="disabled")
        status.config(text="Instaluji…")

        def worker():
            try:
                exe = install(want_desktop.get(), lambda d, t: root.after(0, bar.config, {"value": d * 1000 // t}))
            except Exception as e:  # noqa: BLE001
                root.after(0, lambda: (status.config(text=str(e)), go.config(state="normal")))
                return
            state["exe"] = exe
            root.after(0, finished)

        threading.Thread(target=worker, daemon=True).start()

    def finished():
        from tkinter import messagebox

        status.config(text="Hotovo. Lumen najdeš v nabídce Start.")
        go.config(text="Spustit Lumen", state="normal", command=launch)
        cancel.config(text="Zavřít")
        if messagebox.askyesno("Instalace Lumenu", f"Lumen je nainstalovaný.\n\nSmazat instalační soubor "
                               f"{Path(sys.executable).name}? Už ho nebudeš potřebovat, další verze si Lumen "
                               "stáhne sám.", parent=root):
            delete_self_later()

    def launch():
        launch_lumen(state["exe"])
        root.destroy()

    go = ttk.Button(buttons, text="Instalovat", command=run_install)
    go.pack(side="left", padx=4)
    cancel = ttk.Button(buttons, text="Zrušit", command=root.destroy)
    cancel.pack(side="left")
    fit(root)
    root.mainloop()


def update_gui():
    import tkinter as tk
    from tkinter import ttk

    root = make_window("Aktualizace Lumenu")
    root.attributes("-topmost", True)
    frame = ttk.Frame(root, padding=20)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text=f"Aktualizuji Lumen na verzi {APP_VERSION}…", font=("Segoe UI", 12, "bold")).pack(anchor="w")
    bar = ttk.Progressbar(frame, maximum=1000, length=420)
    bar.pack(fill="x", pady=14)
    status = ttk.Label(frame, text="Čekám, až se Lumen zavře…", wraplength=420, justify="left")
    status.pack(anchor="w")
    buttons = ttk.Frame(frame)
    buttons.pack(anchor="e", pady=(10, 0))
    retry = ttk.Button(buttons, text="Zkusit znovu", command=lambda: start())
    close = ttk.Button(buttons, text="Zavřít", command=root.destroy)

    def say(text: str, failed: bool = False):
        status.config(text=text)
        if failed:
            retry.pack(side="left", padx=4)
            close.pack(side="left")
        else:
            retry.pack_forget()
            close.pack_forget()
        fit(root)  # delší text (chyba) se jinak ořízne

    def start():
        say("Čekám, až se Lumen zavře…")
        threading.Thread(target=worker, daemon=True).start()

    def worker():
        try:
            stop_lumen(wait=30)  # Lumen se po spuštění instalátoru sám ukončí
            root.after(0, say, "Instaluji…")
            exe = install(DESKTOP.exists(), lambda d, t: root.after(0, bar.config, {"value": d * 1000 // t}))
        except Exception as e:  # noqa: BLE001
            root.after(0, say, f"Aktualizace se nepovedla: {e}\n\nInstalační soubor je {sys.executable}, "
                                "můžeš ho spustit i ručně.", True)
            return
        launch_lumen(exe)
        delete_self_later()  # stažená aktualizace už není potřeba
        root.after(800, root.destroy)

    fit(root)
    start()
    root.mainloop()


def main():
    # pracovní složka nesmí být složka Lumenu (instalátor spuštěný z Lumenu ji zdědí), jinak ji Windows
    # nedovolí smazat a aktualizace selže
    try:
        os.chdir(os.getenv("TEMP") or str(TARGET.parent.parent))
    except OSError:
        pass
    if "--update" in sys.argv:
        update_gui()
        return
    if "--silent" in sys.argv:
        started = time.time()
        exe = install(desktop="--no-desktop" not in sys.argv)
        print(f"Nainstalováno do {exe.parent} za {time.time() - started:.1f} s")
        return
    gui()


if __name__ == "__main__":
    main()
