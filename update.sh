#!/usr/bin/env bash
# Instalace a aktualizace Lumenu na serveru (Ubuntu). Pouštět jako root:
#   sudo ./update.sh            # git pull + závislosti + restart služby
#   sudo ./update.sh --local    # bez git pull (první instalace z rozbalené složky, testování)
# Co je hotové, přeskočí. Data (/var/lib/lumen), /etc/lumen.env ani stažené soubory nemění.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

C_ACCENT=$'\033[38;5;203m'; C_OK=$'\033[38;5;114m'; C_WARN=$'\033[38;5;221m'; C_MUTED=$'\033[38;5;245m'; C_RESET=$'\033[0m'
Log()  { printf '%s→%s %s\n' "$C_ACCENT" "$C_RESET" "$*"; }
Ok()   { printf '%s✓%s %s\n' "$C_OK" "$C_RESET" "$*"; }
Warn() { printf '%s!%s %s\n' "$C_WARN" "$C_RESET" "$*"; }
Fail() { printf '%s✗ %s%s\n' $'\033[38;5;196m' "$*" "$C_RESET" >&2; exit 1; }

[[ $EUID -eq 0 ]] || Fail "Spusť jako root: sudo ./update.sh"
LOCAL=0
[[ "${1:-}" == "--local" ]] && LOCAL=1

# Vše se spouští pod vlastníkem složky (jeho deploy key, žádné "dubious ownership", soubory nepatří rootovi).
OWNER="$(stat -c %U .)"
[[ "$OWNER" != root ]] || Fail "Složka patří rootovi. Oprav: sudo chown -R <tvůj_uživatel>: $(pwd)"
As() { sudo -u "$OWNER" -H "$@"; }

# ---- 1. Předpoklady ----------------------------------------------------------
[[ -f /etc/lumen.env ]] || Fail "Chybí /etc/lumen.env. Vytvoř ho: sudo cp deploy/lumen.env.example /etc/lumen.env && sudo chmod 600 /etc/lumen.env"
id lumen >/dev/null 2>&1 || Fail "Chybí uživatel lumen. Vytvoř ho: sudo useradd --system --no-create-home --shell /usr/sbin/nologin lumen"
command -v ffmpeg >/dev/null || Warn "Chybí ffmpeg (sudo apt install ffmpeg), bez něj nejde spojit video se zvukem."
command -v deno >/dev/null || Warn "Chybí Deno, bez něj stahování z YouTube často selže (návod v README)."

# ---- 2. Kód -----------------------------------------------------------------
if (( LOCAL == 0 )); then
  BEFORE="$(As git rev-parse --short HEAD)"
  Log "Stahuji poslední verzi…"
  As git pull --ff-only --quiet || Fail "git pull selhal. Máš ve složce lokální změny? Zkus: git status"
  AFTER="$(As git rev-parse --short HEAD)"
  if [[ "$BEFORE" != "$AFTER" ]]; then
    As git log --oneline --no-decorate "$BEFORE..$AFTER" | sed "s/^/  ${C_MUTED}/;s/\$/${C_RESET}/"
  else
    Ok "Kód je aktuální ($AFTER)"
  fi
fi

# ---- 3. Python prostředí (patří vlastníkovi, služba z něj jen čte) -------------
[[ -d .venv ]] || { Log "Vytvářím .venv…"; As python3 -m venv .venv; }
Log "Instaluji závislosti a aktualizuji yt-dlp…"
As .venv/bin/pip install -q -r requirements.txt
As .venv/bin/pip install -q -U "yt-dlp[default,curl-cffi]"
chmod -R a+rX .   # služba (uživatel lumen) musí kód číst

# ---- 4. Data, stahování, služba ---------------------------------------------------
set -a; . /etc/lumen.env; set +a
DATA="${DATA_DIR:-/var/lib/lumen}"
install -d -o lumen -g lumen -m 750 "$DATA"
chown -R lumen:lumen "$DATA"
if [[ -n "${DOWNLOAD_DIR:-}" ]]; then
  install -d -o lumen -g lumen -m 750 "$DOWNLOAD_DIR" 2>/dev/null || Warn "Složku pro stahování ($DOWNLOAD_DIR) se nepodařilo vytvořit. Je připojený disk?"
fi

install -m 755 deploy/lumenkeys /usr/local/bin/lumenkeys
if ! cmp -s deploy/lumen.service /etc/systemd/system/lumen.service; then
  install -m 644 deploy/lumen.service /etc/systemd/system/lumen.service
  systemctl daemon-reload
  Log "Soubor služby aktualizován"
fi
systemctl enable lumen >/dev/null 2>&1 || true
Log "Restartuji Lumen…"
systemctl restart lumen

for _ in $(seq 1 30); do
  curl -fs "http://127.0.0.1:8000/api/health" >/dev/null 2>&1 && break
  sleep 1
done
curl -fs "http://127.0.0.1:8000/api/health" >/dev/null 2>&1 || Fail "Lumen nenaběhl. Logy: sudo journalctl -u lumen -n 30 --no-pager"
Ok "Lumen běží ($(As git rev-parse --short HEAD 2>/dev/null || echo bez-gitu))"
