# Lumen

Malá webová aplikace na stahování videí a hudby (YouTube, SoundCloud a stovky dalších webů).
Backend je FastAPI nad `yt-dlp` a `ffmpeg`, frontend je čisté HTML/CSS/JS bez build kroku.

Funkce: načtení náhledu odkazu, režimy auto / video / audio, výběr kvality a formátu (MP4, MKV, WebM, MP3, M4A, Opus, FLAC, WAV), fronta s průběhem a historií, převod nahraného souboru přes ffmpeg, seznam podporovaných webů, světlý/tmavý motiv a barva zvýraznění.

## Co potřebuješ

1. **Python 3.10 nebo novější** (doporučuju 3.11+). Na Windows při instalaci zaškrtni "Add Python to PATH".
2. **Deno**. Od konce roku 2025 ho yt-dlp potřebuje, aby si poradil s YouTube. Bez něj stahování z YouTube často selže.
   - Windows (PowerShell): `winget install DenoLand.Deno` nebo `irm https://deno.land/install.ps1 | iex`
   - Linux / macOS: `curl -fsSL https://deno.land/install.sh | sh`
   - Po instalaci zavři a znovu otevři terminál, ať se Deno objeví v PATH. Ověříš příkazem `deno --version`.
3. **ffmpeg** je volitelný. Když ho nemáš nainstalovaný, použije se verze z balíčku `imageio-ffmpeg`. Plnohodnotný ffmpeg v PATH (`winget install Gyan.FFmpeg`) je ale spolehlivější.

## Spuštění na svém počítači

### Nejrychleji
- **Windows:** dvojklik na `start.bat`
- **Linux / macOS:** `./start.sh`

Skript vytvoří virtuální prostředí, nainstaluje závislosti, aktualizuje yt-dlp a otevře prohlížeč na `http://127.0.0.1:8000`.

### Ve VS Code
1. Rozbal ZIP a ve VS Code zvol **File > Open Folder** a vyber složku `lumen`.
2. Otevři terminál (**Terminal > New Terminal**) a spusť:
   ```
   python -m venv .venv
   .venv\Scripts\activate          (Windows)
   source .venv/bin/activate       (Linux / macOS)
   pip install -r requirements.txt
   python run.py
   ```
3. Vpravo dole nech VS Code vybrat interpreter z `.venv` (nebo `Ctrl+Shift+P`, **Python: Select Interpreter**).
4. Spuštění z editoru: **F5** (je připravená konfigurace "Lumen"). Zastavíš `Ctrl+C` v terminálu.

Stažené soubory se ukládají do složky `downloads/`, hotové úlohy se po 24 hodinách mažou.

## Když něco nejde

- **YouTube hlásí chybu nebo nic nestáhne:** nejdřív aktualizuj yt-dlp: `pip install -U "yt-dlp[default]"` (skripty `start.bat` / `start.sh` to dělají při každém startu). YouTube se mění často, takže je to nejčastější lék.
- **Nahoře se zobrazí žlutý pruh o chybějícím Deno / ffmpeg:** doinstaluj podle sekce výše a restartuj aplikaci.
- **"Sign in to confirm you're not a bot" nebo věkově omezené video:** v Nastavení zvol přihlášení z prohlížeče (nejspolehlivější je Firefox, u Chrome na Windows to bývá problém). Funguje jen na počítači, kde běží i prohlížeč.
- **Převod souboru selže:** zapni "Překódovat". Prosté přebalení nejde mezi všemi kombinacemi (třeba H.264 do WebM).
- Playlisty se nepodporují, z odkazu na playlist se stáhne jen první položka.

## Nasazení na server

Důležité: **aplikace je bez hesla otevřená komukoliv, kdo zná adresu.** Na veřejném serveru vždy nastav `AUTH_PASSWORD` a dej před aplikaci HTTPS.

### Proměnné prostředí

| Proměnná | Výchozí | Význam |
|---|---|---|
| `HOST` | `127.0.0.1` | Na jaké adrese poslouchat. Na serveru `0.0.0.0`. |
| `PORT` | `8000` | Port. |
| `AUTH_PASSWORD` | prázdné | Zapne přihlášení (HTTP Basic). |
| `AUTH_USER` | `lumen` | Uživatelské jméno k heslu. |
| `DOWNLOAD_DIR` | `./downloads` | Kam se ukládají soubory. |
| `KEEP_HOURS` | `24` | Za jak dlouho se hotové soubory smažou. |
| `MAX_PARALLEL` | `2` | Kolik úloh běží současně. |
| `MAX_UPLOAD_MB` | `2048` | Maximální velikost nahraného souboru při převodu. |
| `COOKIES_FILE` | prázdné | Cesta k `cookies.txt` exportovanému z prohlížeče (viz níže). |
| `DAILY_LIMIT` | `5` | Kolik stažení za 24 hodin má návštěvník bez přístupového klíče. `0` = bez limitu. |
| `DATA_DIR` | `./data` | Kde jsou počítadla limitu (`quota.db`) a klíče (`keys.json`). |
| `SMTP_USER`, `SMTP_PASSWORD` | prázdné | Účet, ze kterého se posílá e-mail o žádosti o klíč (u Gmailu adresa a **heslo aplikace**). Bez nich se žádosti jen ukládají. |
| `REQUEST_TO` | `SMTP_USER` | Kam přijde e-mail o nové žádosti. |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_SECURITY` | `smtp.gmail.com`, `587`, `starttls` | Poštovní server. Pro port 465 nastav `SMTP_SECURITY=ssl`. |
| `MAX_REQUESTS_PER_DAY` | `50` | Kolik žádostí o klíč smí za den přijít celkem. |
| `IMPERSONATE` | `chrome` | Napodobení prohlížeče (knihovna `curl-cffi`) pro weby, které jinak vrací chybu 403. Na YouTube se nepoužívá. Prázdné = vypnuto. |
| `PUBLIC_URL` | prázdné | Veřejná adresa Lumenu (např. `https://lumen.mican.dpdns.org`). Podle ní se tvoří odkaz „Schválit“ v e-mailu o žádosti. |
| `APPROVE_DEVICES` | `1` | Na kolik zařízení platí klíč vystavený schválením žádosti. |

### Varianta A: Docker
```
docker compose up -d --build
```
Předtím si v `docker-compose.yml` změň `AUTH_PASSWORD`. Aplikace poběží na portu 8000. Dockerfile jsem nemohl vyzkoušet, takže při prvním buildu počítej s drobnou úpravou.

### Varianta B: přímo na Linuxu
```
sudo apt install python3 python3-venv ffmpeg curl unzip
curl -fsSL https://deno.land/install.sh | sh
cd lumen && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
HOST=127.0.0.1 AUTH_PASSWORD=tvoje-heslo .venv/bin/python run.py
```
Pro trvalý běh to dej do `systemd` služby a před ni reverzní proxy, třeba Caddy (HTTPS si vyřídí sám):
```
tvoje-domena.cz {
    reverse_proxy 127.0.0.1:8000
}
```

### Denní limit a přístupové klíče

Kdo nemá klíč, může za posledních 24 hodin stáhnout nejvýš `DAILY_LIMIT` videí (výchozí 5). Limit se počítá podle IP adresy návštěvníka, za Cloudflare Tunnelem z hlavičky `CF-Connecting-IP`, a přežije restart serveru. IP se neukládá, jen její zasolený hash, u IPv6 se počítá celá síť /64. Stažení, které selže nebo se zruší, se do limitu nezapočítá. Převod souborů se do limitu nepočítá.

Přístupový klíč limit odstraní. Návštěvník ho vloží v **Nastavení → Přístup** a prohlížeč si ho pamatuje. **Klíč se při prvním zadání přiřadí k prohlížeči (zařízení), kde byl zadán, a jinde nefunguje**, takže ho nejde posílat dál. Klíče spravuješ na serveru (bez `sudo`), restart není potřeba:

```
.venv/bin/python keys.py add "Honza"              # klíč pro 1 zařízení, vypíše se jen jednou
.venv/bin/python keys.py add "Honza" --devices 2  # klíč pro 2 zařízení (třeba telefon a počítač)
.venv/bin/python keys.py list                     # seznam klíčů a obsazená zařízení
.venv/bin/python keys.py reset 2                  # uvolní zařízení klíče č. 2 (nový telefon, smazané cookies)
.venv/bin/python keys.py revoke 2                 # zruší klíč č. 2 úplně
```

Zařízení se pozná podle cookie v prohlížeči. Když ji uživatel smaže, nebo přejde na jiný prohlížeč či telefon, klíč u něj přestane fungovat, dokud ho nepustíš příkazem `reset`.

#### Žádost o klíč z webu

V **Nastavení → Přístup** může návštěvník bez klíče zadat svůj e-mail a kliknout na **Požádat o klíč**. Žádost se uloží do `data/requests.json` a tobě přijde e-mail (z `SMTP_USER` na `REQUEST_TO`) s odkazem **Schválit**.

1. Klikneš na odkaz v e-mailu. Otevře se stránka Lumenu a na ní potvrdíš tlačítkem **Schválit a poslat klíč** (nebo **Zamítnout**). Potvrzení je zvlášť, aby žádost nespustil e-mailový skener odkazů.
2. Aplikace vytvoří klíč (na `APPROVE_DEVICES` zařízení, výchozí 1) a pošle ho e-mailem žadateli. Odpověď žadatele na ten e-mail jde tobě.
3. Kdyby odeslání žadateli selhalo, ukáže se klíč na té samé stránce a pošleš ho sám.

Odkaz je na jedno použití, platí 14 dní a obsahuje dlouhý náhodný token (v `requests.json` je jen jeho otisk). Aplikace neposílá nic nikomu, dokud žádost sám neschválíš, takže se nedá zneužít k rozesílání spamu. Proti zneužití formuláře je navíc limit 3 žádosti na návštěvníka za den, `MAX_REQUESTS_PER_DAY` celkem a skryté pole proti robotům. Žádost zadanou z té samé adresy do 24 hodin aplikace nepošle znovu.

Bez nastaveného `PUBLIC_URL` e-mail odkaz neobsahuje a žádost vyřídíš ručně:

```
lumenkeys requests                          # čekající žádosti
lumenkeys add "jan@seznam.cz" --request 3   # vytvoří klíč a označí žádost č. 3 jako vyřízenou
```

Pak v e-mailu klikneš na Odpovědět (jde rovnou žadateli) a vložíš klíč. `lumenkeys requests done 3` označí žádost jako vyřízenou bez vytváření klíče.

Pro Gmail: v Google účtu zapni dvoufázové ověření, na https://myaccount.google.com/apppasswords vytvoř **heslo aplikace** a vlož ho do `/etc/lumen.env` jako `SMTP_PASSWORD` **bez mezer** (soubor čte i shell, mezery by ho rozbily). Obyčejné heslo k účtu nefunguje a nikdy ho tam nedávej. Po změně `/etc/lumen.env` spusť `sudo systemctl restart lumen`.

V souboru `data/keys.json` jsou jen otisky klíčů, takže ztracený klíč nejde zobrazit znovu, jen vystavit nový. Soubor `data/` nikam nesdílej a zálohuj ho.

Pozor: s nastaveným `AUTH_PASSWORD` si web zamkne heslem úplně celý (i návštěvníky s klíčem). Pro veřejný web s limitem nech `AUTH_PASSWORD` prázdné.

### Pozor na YouTube ze serveru
YouTube často blokuje IP adresy datacenter (VPS) a chce po nich přihlášení. Když na serveru stahování z YouTube selhává a doma funguje, pomůže exportovat cookies z prohlížeče (rozšíření typu "Get cookies.txt LOCALLY"), nahrát soubor na server a nastavit `COOKIES_FILE=/cesta/k/cookies.txt`. Cookies jsou citlivé, jako heslo k účtu, takže soubor nikam nesdílej. Doma na vlastním počítači tenhle problém obvykle není.

## Struktura

```
backend/main.py     API: info, stahování, převod, fronta, weby
static/             frontend (index.html, style.css, app.js)
run.py              spuštění serveru
start.bat/.sh       instalace a spuštění jedním příkazem
Dockerfile, docker-compose.yml
```

## Právní poznámka

Stahování z YouTube a dalších služeb může být v rozporu s jejich podmínkami užívání. Používej aplikaci jen pro obsah, ke kterému máš práva, nebo pro osobní účely tam, kde to zákon dovoluje.

## Server s gitem (update.sh)

Tohle repo je určené pro server, kde kód leží v `/opt/lumen` a aktualizuje se jedním příkazem:

```
cd /opt/lumen && git pull && sudo ./update.sh
```

`update.sh` stáhne kód (nebo `--local` bez gitu), doinstaluje závislosti, aktualizuje yt-dlp, nainstaluje systemd službu `lumen` a restartuje ji. Nemění: `/etc/lumen.env` (nastavení), `/var/lib/lumen` (klíče a limity) a stažené soubory.

Rozdělení práv, ať má veřejná služba co nejmíň možností:

| Co | Kde | Komu patří |
|---|---|---|
| kód a `.venv` | `/opt/lumen` | správce serveru (dělá `git pull`) |
| klíče a limity | `/var/lib/lumen` | uživatel `lumen` (služba) |
| stažené soubory | `/srv/lumen-downloads/files` | uživatel `lumen`, omezený disk |

Služba běží jako uživatel `lumen`, do kódu jen čte a nevidí `/srv/cloud` ani datové disky (`InaccessiblePaths`).

Klíče spravuješ příkazem `lumenkeys` (nainstaluje ho `update.sh`):

```
lumenkeys add "Honza"        # nový klíč na 1 zařízení
lumenkeys add "Honza" --devices 2
lumenkeys list
lumenkeys requests         # čekající žádosti o klíč z webu
lumenkeys reset 2            # uvolní zařízení klíče č. 2
lumenkeys revoke 2           # zruší klíč č. 2
```
