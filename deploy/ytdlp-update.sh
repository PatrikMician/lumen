#!/usr/bin/env bash
# Noční aktualizace yt-dlp. Spouští ji systemd timer lumen-ytdlp.timer (nainstaluje ho update.sh),
# ručně:  sudo bash deploy/ytdlp-update.sh
# Aktualizuje yt-dlp v .venv pod vlastníkem složky (stejně jako update.sh). Když se verze změní, počká,
# až Lumen nemá rozdělané žádné stahování (nejvýš 30 minut), a službu restartuje, ať nová verze začne platit.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")/.."

[[ $EUID -eq 0 ]] || { echo "Spusť jako root: sudo bash deploy/ytdlp-update.sh" >&2; exit 1; }
OWNER="$(stat -c %U .)"
[[ "$OWNER" != root ]] || { echo "Složka patří rootovi, oprav: sudo chown -R <tvůj_uživatel>: $(pwd)" >&2; exit 1; }
[[ -x .venv/bin/pip ]] || { echo "Chybí .venv, nejdřív spusť sudo bash ./update.sh" >&2; exit 1; }
As() { sudo -u "$OWNER" -H "$@"; }

version() { As .venv/bin/python -c 'from yt_dlp.version import __version__ as v; print(v)' 2>/dev/null || echo "?"; }

BEFORE="$(version)"
As .venv/bin/pip install -q --disable-pip-version-check -U "yt-dlp[default,curl-cffi]"
AFTER="$(version)"

if [[ "$BEFORE" == "$AFTER" ]]; then
  echo "yt-dlp je aktuální ($AFTER)"
  exit 0
fi

echo "yt-dlp $BEFORE → $AFTER, čekám, až nepoběží žádné stahování…"
for _ in $(seq 1 60); do
  # /api/health vrací počet rozdělaných úloh; když Lumen neběží, rovnou restartujeme
  ACTIVE="$(curl -fs --max-time 5 http://127.0.0.1:8000/api/health \
    | python3 -c 'import json, sys; print(json.load(sys.stdin).get("active", 0))' 2>/dev/null || echo 0)"
  [[ "$ACTIVE" == "0" ]] && break
  sleep 30
done
systemctl restart lumen
echo "Lumen restartován s yt-dlp $AFTER"
