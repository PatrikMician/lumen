"""Instalátor Lumenu pro Windows (LumenSetup.exe).

Bez administrátorských práv: rozbalí přibalený payload.zip do %LOCALAPPDATA%\\Programs\\Lumen, vytvoří zástupce
v nabídce Start (a volitelně na ploše) a zapíše Lumen do Aplikací Windows (HKCU), odkud jde odinstalovat.
`LumenSetup.exe --silent` nainstaluje bez okna (pro testy).
"""
import os
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path

APP_VERSION = "1.1.0"
PAYLOAD = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "payload.zip"
TARGET = Path(os.getenv("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "Programs" / "Lumen"
START_MENU = Path(os.getenv("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Lumen.lnk"
DESKTOP = Path.home() / "Desktop" / "Lumen.lnk"
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Lumen"


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
    if lumen_running():
        raise RuntimeError("Lumen je spuštěný. Zavři jeho okno a spusť instalaci znovu.")
    # stará verze pryč (data uživatele jsou jinde, v %LOCALAPPDATA%\Lumen)
    if TARGET.exists():
        import shutil
        shutil.rmtree(TARGET)
    TARGET.mkdir(parents=True)
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


def gui():
    import tkinter as tk
    from tkinter import ttk

    root = tk.Tk()
    root.title("Instalace Lumenu")
    root.geometry("460x250")
    root.resizable(False, False)
    frame = ttk.Frame(root, padding=20)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text=f"Lumen {APP_VERSION}", font=("Segoe UI", 16, "bold")).pack(anchor="w")
    ttk.Label(frame, text="Stahování videí a hudby přímo na tvém počítači.\n"
                          f"Nainstaluje se do {TARGET}\n(bez práv správce).", justify="left").pack(anchor="w", pady=(6, 10))
    want_desktop = tk.BooleanVar(value=True)
    ttk.Checkbutton(frame, text="Zástupce na ploše", variable=want_desktop).pack(anchor="w")
    bar = ttk.Progressbar(frame, maximum=1000)
    bar.pack(fill="x", pady=12)
    status = ttk.Label(frame, text="")
    status.pack(anchor="w")
    buttons = ttk.Frame(frame)
    buttons.pack(anchor="e", pady=(8, 0))
    state = {"exe": None}

    def run_install():
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
        status.config(text="Hotovo. Lumen najdeš v nabídce Start.")
        go.config(text="Spustit Lumen", state="normal", command=launch)
        cancel.config(text="Zavřít")

    def launch():
        subprocess.Popen([str(state["exe"])], cwd=str(TARGET))
        root.destroy()

    go = ttk.Button(buttons, text="Instalovat", command=run_install)
    go.pack(side="left", padx=4)
    cancel = ttk.Button(buttons, text="Zrušit", command=root.destroy)
    cancel.pack(side="left")
    root.mainloop()


def main():
    if "--silent" in sys.argv:
        started = time.time()
        exe = install(desktop="--no-desktop" not in sys.argv)
        print(f"Nainstalováno do {exe.parent} za {time.time() - started:.1f} s")
        return
    gui()


if __name__ == "__main__":
    main()
