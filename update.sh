#!/usr/bin/env bash
# Instalace a aktualizace Lumenu na Ubuntu serveru. Spouštět jako root z téhle složky:
#   sudo ./update.sh            # git pull, případně doinstaluje závislosti a restartuje službu
#   sudo ./update.sh --force    # přeinstaluje závislosti (i nejnovější yt-dlp) a restartuje i bez nových commitů
# Poprvé to samé: nainstaluje balíčky, vytvoří uživatele a službu. Pouštět se dá opakovaně.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"
APP_DIR="$PWD"

SVC_USER=lumen
DATA_DIR=/var/lib/lumen
DL_BASE=/srv/lumen-downloads
DL_DIR="$DL_BASE/files"
ENV_FILE=/etc/lumen.env
PORT=8000

C_ACCENT=$'\033[38;5;203m'
C_OK=$'\033[38;5;114m'
C_WARN=$'\033[38;5;221m'
C_MUTED=$'\033[38;5;245m'
C_RESET=$'\033[0m'
Log()  { printf '%s→%s %s\n' "$C_ACCENT" "$C_RESET" "$*"; }
Ok()   { printf '%s✓%s %s\n' "$C_OK" "$C_RESET" "$*"; }
Warn() { printf '%s!%s %s\n' "$C_WARN" "$C_RESET" "$*"; }
Fail() { printf '%s✗ %s%s\n' $'\033[38;5;196m' "$*" "$C_RESET" >&2; exit 1; }

[[ $EUID -eq 0 ]] || Fail "Spusť jako root: sudo ./update.sh"
[[ -f backend/main.py ]] || Fail "backend/main.py chybí. Spusť update.sh z hlavní složky Lumenu."
FORCE=0
[[ "${1:-}" == "--force" ]] && FORCE=1

# Kód i virtuální prostředí patří vlastníkovi složky (ty), ne rootovi a ne službě. Služba kód jen čte.
OWNER="$(stat -c %U .)"
Run() { if [[ "$OWNER" == root ]]; then "$@"; else sudo -u "$OWNER" "$@"; fi; }

# ---- 1. git pull ------------------------------------------------------------
BEFORE=none; AFTER=none
if [[ -d .git ]]; then
  BEFORE="$(Run git rev-parse --short HEAD)"
  Log "Stahuji nejnovější verzi…"
  Run git pull --ff-only --quiet || Fail "git pull se nepovedl. Lokální změny ve složce? Zkus: git status"
  AFTER="$(Run git rev-parse --short HEAD)"
  [[ "$BEFORE" != "$AFTER" ]] && Run git log --oneline --no-decorate "$BEFORE..$AFTER" | sed "s/^/  ${C_MUTED}/;s/$/${C_RESET}/"
else
  Warn "Složka není git repozitář, přeskakuji git pull."
fi

INSTALLED=0
[[ -f /etc/systemd/system/lumen.service && -x .venv/bin/uvicorn ]] && INSTALLED=1
if [[ "$BEFORE" == "$AFTER" && $FORCE -eq 0 && $INSTALLED -eq 1 ]]; then
  Ok "Už je aktuální ($AFTER). Pro přeinstalaci i tak: sudo ./update.sh --force"
  exit 0
fi

# ---- 2. Systémové balíčky ---------------------------------------------------
MISSING=()
command -v python3 >/dev/null || MISSING+=(python3)
dpkg -s python3-venv >/dev/null 2>&1 || MISSING+=(python3-venv)
command -v ffmpeg  >/dev/null || MISSING+=(ffmpeg)
command -v curl    >/dev/null || MISSING+=(curl)
command -v unzip   >/dev/null || MISSING+=(unzip)
if (( ${#MISSING[@]} )); then
  Log "Instaluji: ${MISSING[*]}"
  apt-get update -qq
  apt-get install -y -qq "${MISSING[@]}" >/dev/null
fi
if ! command -v deno >/dev/null; then
  Log "Instaluji Deno (bez něj YouTube často nejde stáhnout)…"
  curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr/local sh >/dev/null 2>&1 \
    && Ok "Deno nainstalováno" || Warn "Deno se nepodařilo nainstalovat. Zkus ručně: curl -fsSL https://deno.land/install.sh | sudo DENO_INSTALL=/usr/local sh"
fi

# ---- 3. Uživatel služby a složky --------------------------------------------
if ! id "$SVC_USER" >/dev/null 2>&1; then
  Log "Vytvářím systémového uživatele $SVC_USER…"
  useradd --system --no-create-home --shell /usr/sbin/nologin "$SVC_USER"
fi
install -d -o "$SVC_USER" -g "$SVC_USER" -m 700 "$DATA_DIR"
install -d -o "$SVC_USER" -g "$SVC_USER" -m 755 "$DL_DIR"
if ! mountpoint -q "$DL_BASE"; then
  Warn "$DL_BASE není samostatný oddíl: stažená videa půjdou na systémový disk. Omezený disk viz README (sekce „Nasazení“)."
fi

# ---- 4. Nastavení (vytvoří se jen poprvé, existující se nepřepíše) ---------------
if [[ ! -f "$ENV_FILE" ]]; then
  cat > "$ENV_FILE" <<EOF
# Nastavení Lumenu. Po změně: sudo systemctl restart lumen
DOWNLOAD_DIR=$DL_DIR
KEEP_HOURS=24
MAX_PARALLEL=2
DAILY_LIMIT=5
# POZOR: AUTH_PASSWORD sem nedávej, zamkl by web i pro lidi s přístupovým klíčem.
# Žádosti o klíč e-mailem (až budou): SMTP_USER=... SMTP_PASSWORD=...
EOF
  chmod 600 "$ENV_FILE"
  Ok "Vytvořeno $ENV_FILE"
fi
if grep -qE '^AUTH_PASSWORD=.+' "$ENV_FILE"; then
  Warn "V $ENV_FILE je nastavené AUTH_PASSWORD, takže je web zamčený heslem celý. Pro veřejný web s limity ho smaž."
fi

# ---- 5. Python prostředí ----------------------------------------------------
NEW_VENV=0
if [[ ! -x .venv/bin/python ]]; then
  Log "Vytvářím virtuální prostředí…"
  Run python3 -m venv .venv
  NEW_VENV=1
fi
if [[ "$BEFORE" != "$AFTER" || $FORCE -eq 1 || $NEW_VENV -eq 1 || $INSTALLED -eq 0 ]]; then
  Log "Instaluji závislosti…"
  Run .venv/bin/pip install -q --disable-pip-version-check -r requirements.txt
  if [[ $FORCE -eq 1 ]]; then
    Run .venv/bin/pip install -q --disable-pip-version-check -U "yt-dlp[default]"
  fi
fi
sudo -u "$SVC_USER" test -r backend/main.py \
  || Fail "Uživatel $SVC_USER nesmí číst $APP_DIR. Zkontroluj práva: namei -l $APP_DIR/backend/main.py"

# ---- 6. Služba a nástroj na klíče ---------------------------------------------
sed "s|@APP_DIR@|$APP_DIR|g" deploy/lumen.service > /etc/systemd/system/lumen.service
sed "s|@APP_DIR@|$APP_DIR|g" deploy/lumen-keys > /usr/local/bin/lumen-keys
chmod 755 /usr/local/bin/lumen-keys
systemctl daemon-reload
systemctl enable lumen >/dev/null 2>&1
systemctl restart lumen

Log "Čekám, až Lumen naběhne…"
code=""
for _ in $(seq 1 40); do
  code="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/api/quota" || true)"
  [[ "$code" == 200 ]] && break
  sleep 1
done
[[ "$code" == 200 ]] || Fail "Lumen nenaběhl. Logy: sudo journalctl -u lumen -n 40 --no-pager"
Ok "Lumen běží ($BEFORE → $AFTER) na 127.0.0.1:$PORT"
printf '  Klíče:  sudo lumen-keys add "Jméno"   |   sudo lumen-keys list\n'
printf '  Tunel:  v Cloudflare nasměruj adresu na http://127.0.0.1:%s\n' "$PORT"
