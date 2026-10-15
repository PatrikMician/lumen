"""Sestaví Lumen pro Android: dist/Lumen-<verze>.apk

    python android/build_android.py            # APK pro telefony (arm64-v8a, armeabi-v7a)
    python android/build_android.py --emulator # APK pro emulátor na PC (x86_64), jen na zkoušení

Potřebuje JDK 17 (JAVA_HOME) a Android SDK (ANDROID_HOME), viz README. Postup:
1. Python balíčky z requirements-android.txt (jen čistý Python) + backend + static → assets/lumen.zip
2. ikona z loga (desktop/build_desktop.py: draw_logo)
3. gradlew assembleRelease, podepsáno klíčem z android/keystore.properties (poprvé se klíč vytvoří)
"""
import argparse
import os
import secrets
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ANDROID = ROOT / "android"
APP_BUILD = ANDROID / "app" / "build"
# nástroje a podpisový klíč: ~/.lumen-android (ne AppData, Python z Microsoft Store/Install Manageru tam vidí jinou složku)
TOOLS = Path.home() / ".lumen-android"
KEYSTORE_PROPS = ANDROID / "keystore.properties"
ICON_SIZES = {"mdpi": 108, "hdpi": 162, "xhdpi": 216, "xxhdpi": 324, "xxxhdpi": 432}


def version() -> str:
    for line in (ANDROID / "gradle.properties").read_text("utf-8").splitlines():
        if line.startswith("lumenVersion="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("V android/gradle.properties chybí lumenVersion")


def find_tools(env: dict):
    """JAVA_HOME a ANDROID_HOME z prostředí, jinak ze složky %LOCALAPPDATA%\\lumen-android-tools."""
    if not env.get("JAVA_HOME"):
        jdks = sorted((TOOLS / "jdk").glob("jdk-17*"))
        if not jdks:
            raise SystemExit("Chybí JDK 17: nastav JAVA_HOME (postup je v README)")
        env["JAVA_HOME"] = str(jdks[-1])
    if not env.get("ANDROID_HOME"):
        if not (TOOLS / "sdk").is_dir():
            raise SystemExit("Chybí Android SDK: nastav ANDROID_HOME (postup je v README)")
        env["ANDROID_HOME"] = str(TOOLS / "sdk")
    return env


def bundle_python():
    """assets/lumen.zip: backend, static, spouštěč a čisté Python balíčky."""
    work = APP_BUILD / "lumen-py"
    shutil.rmtree(work, ignore_errors=True)
    deps = work / "pydeps"
    subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check",
                    "--target", str(deps), "--only-binary=:all:", "--platform", "any", "--python-version", "3.12",
                    "--implementation", "py", "--abi", "none", "-r", str(ANDROID / "requirements-android.txt")],
                   check=True)
    for junk in list(deps.glob("bin")) + list(deps.rglob("__pycache__")):
        shutil.rmtree(junk, ignore_errors=True)
    shutil.copytree(ROOT / "backend", work / "backend", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copytree(ROOT / "static", work / "static")
    shutil.copy2(ANDROID / "py" / "lumen_android.py", work / "lumen_android.py")

    out = APP_BUILD / "lumen-assets" / "lumen.zip"
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(work.rglob("*")):
            if f.is_file():
                z.write(f, f.relative_to(work).as_posix())
    print(f"assets/lumen.zip: {out.stat().st_size / 1e6:.1f} MB")


def icons():
    """Popředí adaptivní ikony: logo uprostřed (bezpečná zóna je 66 z 108 dp)."""
    sys.path.insert(0, str(ROOT / "desktop"))
    from build_desktop import draw_logo
    from PIL import Image

    for dpi, px in ICON_SIZES.items():
        logo = draw_logo(round(px * 0.62))
        canvas = Image.new("RGBA", (px, px), (0, 0, 0, 0))
        canvas.paste(logo, ((px - logo.width) // 2, (px - logo.height) // 2), logo)
        target = APP_BUILD / "lumen-res" / f"mipmap-{dpi}" / "ic_launcher_fg.png"
        target.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(target)


def ensure_keystore(env: dict):
    """Podpisový klíč: bez stejného klíče nejde nová verze nainstalovat přes starou. Ukládá se mimo repozitář."""
    if KEYSTORE_PROPS.exists():
        return
    store = TOOLS / "keystore" / "lumen-release.jks"
    store.parent.mkdir(parents=True, exist_ok=True)
    password = secrets.token_urlsafe(24)
    keytool = Path(env["JAVA_HOME"]) / "bin" / ("keytool.exe" if os.name == "nt" else "keytool")
    subprocess.run([str(keytool), "-genkeypair", "-keystore", str(store), "-alias", "lumen", "-keyalg", "RSA",
                    "-keysize", "4096", "-validity", "36500", "-storepass", password, "-keypass", password,
                    "-dname", "CN=Lumen"], check=True)
    KEYSTORE_PROPS.write_text(
        f"storeFile={store.as_posix()}\nstorePassword={password}\nkeyAlias=lumen\nkeyPassword={password}\n", "utf-8")
    print(f"Vytvořen podpisový klíč {store} (záloha! bez něj nejde vydat aktualizaci)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--emulator", action="store_true", help="APK pro emulátor (x86_64)")
    args = ap.parse_args()

    env = find_tools(dict(os.environ))
    bundle_python()
    icons()
    ensure_keystore(env)
    abis = "x86_64" if args.emulator else "arm64-v8a,armeabi-v7a"
    gradlew = ANDROID / ("gradlew.bat" if os.name == "nt" else "gradlew")
    extra = ["-PlumenTestEnv=MUSIC_REQUIRE_KEY=0"] if args.emulator else []  # emulátor: hudba bez klíče, ladění
    subprocess.run([str(gradlew), "--no-daemon", "assembleRelease", f"-Pabis={abis}", *extra], cwd=ANDROID, env=env,
                   check=True)

    apk = APP_BUILD / "outputs" / "apk" / "release" / "app-release.apk"
    name = f"Lumen-{version()}" + ("-emulator" if args.emulator else "") + ".apk"
    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    shutil.copy2(apk, dist / name)
    print(f"Hotovo: {dist / name} ({(dist / name).stat().st_size / 1e6:.0f} MB)")


if __name__ == "__main__":
    main()
