# Lumen

Malá webová aplikace na stahování videí a hudby (YouTube, SoundCloud a stovky dalších webů).
Backend je FastAPI nad `yt-dlp` a `ffmpeg`, frontend je čisté HTML/CSS/JS bez build kroku.

Funkce: načtení náhledu odkazu, režimy auto / video / audio, výběr kvality a formátu (MP4, MKV, WebM, MP3, M4A, Opus, FLAC, WAV), fronta s průběhem a historií, převod nahraného souboru přes ffmpeg, hudební přehrávač (online katalog i hudba ze zařízení), seznam podporovaných webů, světlý/tmavý motiv a barva zvýraznění.

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
| `ALLOW_BROWSER_COOKIES` | `0` | `1` povolí volbu „Přihlášení z prohlížeče“ (čtení cookies z prohlížeče na stejném počítači). Na serveru nech `0`, tam prohlížeč není. |
| `COOKIES_FILE` | prázdné | Cesta k `cookies.txt` exportovanému z prohlížeče (viz níže). |
| `DAILY_LIMIT` | `5` | Kolik stažení za 24 hodin má návštěvník bez přístupového klíče. `0` = bez limitu. |
| `DATA_DIR` | `./data` | Kde jsou počítadla limitu (`quota.db`) a klíče (`keys.json`). |
| `SMTP_USER`, `SMTP_PASSWORD` | prázdné | Účet, ze kterého se posílá e-mail o žádosti o klíč (u Gmailu adresa a **heslo aplikace**). Bez nich se žádosti jen ukládají. |
| `REQUEST_TO` | `SMTP_USER` | Kam přijde e-mail o nové žádosti. |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_SECURITY` | `smtp.gmail.com`, `587`, `starttls` | Poštovní server. Pro port 465 nastav `SMTP_SECURITY=ssl`. |
| `MAX_REQUESTS_PER_DAY` | `50` | Kolik žádostí o klíč smí za den přijít celkem. |
| `IMPERSONATE` | `chrome` | Napodobení prohlížeče (knihovna `curl-cffi`) pro weby, které jinak vrací chybu 403. Na YouTube se nepoužívá. Prázdné = vypnuto. |
| `PUBLIC_URL` | prázdné | Veřejná adresa Lumenu (např. `https://lumen.mican.dpdns.org`). Podle ní se tvoří odkaz „Schválit“ v e-mailu o žádosti. |
| `APPROVE_DEVICES` | `1` | Počet zařízení u starších žádostí, které ho ještě neuváděly. Nové žádosti nesou počet zvolený žadatelem. |
| `MAX_REQUEST_DEVICES` | `5` | O kolik zařízení si jde říct z webu (u nového klíče i u navýšení). |
| `MAX_DEVICES` | `20` | Kolik zařízení můžeš povolit na schvalovací stránce. |
| `MUSIC` | `1` | `0` vypne online hudbu (hudba ze zařízení funguje dál, ta server nezatěžuje). |
| `MUSIC_REQUIRE_KEY` | `1` | Online hudba jen pro návštěvníky s přístupovým klíčem (když je zapnutý `DAILY_LIMIT`). |
| `MUSIC_LOCATION` | `CZ` | Země pro hudební katalog (domovská stránka, žebříčky). |
| `MUSIC_BROWSE_PER_HOUR` | `300` | Dotazů do hudebního katalogu na prohlížeč za hodinu. |
| `MUSIC_TRACKS_PER_HOUR` | `120` | Spuštěných skladeb na prohlížeč za hodinu. |
| `MUSIC_DIRECT` | `1` | `1` = nejdřív zkusit přímé čtení zvuku z YouTube, při 403 přejít na stažení do mezipaměti; `0` = rovnou přes mezipaměť. |
| `MUSIC_CACHE_DIR` | `DATA_DIR/music-cache` | Mezipaměť stažených skladeb. |
| `MUSIC_CACHE_MB` | `1024` | Největší velikost mezipaměti, nejdéle nehrané skladby se mažou. |
| `APP_DIR` | `DATA_DIR/app` | Instalátory PC verze ke stažení z webu (`LumenSetup-<verze>.exe`). |
| `JS_RUNTIMES` | `deno` | JavaScript runtime pro YouTube (`deno,node,bun,quickjs`). PC verze používá `deno,node,bun`. |
| `KEY_SERVER` | prázdné | Jen PC verze: adresa serveru Lumenu, který ověřuje klíče a vyřizuje žádosti. |

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
.venv/bin/python keys.py devices 2 3              # klíč č. 2 bude platit na 3 zařízení
.venv/bin/python keys.py reset 2                  # uvolní zařízení klíče č. 2 (nový telefon, smazané cookies)
.venv/bin/python keys.py revoke 2                 # zruší klíč č. 2 úplně
```

Zařízení se pozná podle cookie v prohlížeči. Když ji uživatel smaže, nebo přejde na jiný prohlížeč či telefon, klíč u něj přestane fungovat, dokud ho nepustíš příkazem `reset`.

#### Žádost o klíč z webu

V **Nastavení → Přístup** může návštěvník bez klíče zadat svůj e-mail, vybrat **počet zařízení** (1 až `MAX_REQUEST_DEVICES`), případně napsat důvod, a kliknout na **Požádat o klíč**. Žádost se uloží do `data/requests.json` a tobě přijde e-mail (z `SMTP_USER` na `REQUEST_TO`) s počtem zařízení, důvodem a odkazem **Schválit**.

1. Klikneš na odkaz v e-mailu. Otevře se stránka Lumenu s žádostí. **Počet zařízení můžeš před schválením upravit** a potvrdíš tlačítkem **Schválit a poslat klíč** (nebo **Zamítnout**). Potvrzení je zvlášť, aby žádost nespustil e-mailový skener odkazů.
2. Aplikace vytvoří klíč na potvrzený počet zařízení a pošle ho e-mailem žadateli. Odpověď žadatele na ten e-mail jde tobě.
3. Kdyby odeslání žadateli selhalo, ukáže se klíč na té samé stránce a pošleš ho sám.

**Víc zařízení později.** Kdo už klíč má, uvidí v **Nastavení → Přístup** blok **Víc zařízení**: vybere nový počet, napíše důvod (povinný) a svůj e-mail. Tobě přijde e-mail s odkazem, na stránce vidíš klíč, kolik zařízení má teď a kolik obsazených, a počet schválíš nebo upravíš. Navýšení platí hned a žadateli přijde e-mail. Pod počet už obsazených zařízení jít nejde (k tomu je `reset`). Pro jeden klíč čeká nejvýš jedna žádost o navýšení.

Odkaz je na jedno použití, platí 14 dní a obsahuje dlouhý náhodný token (v `requests.json` je jen jeho otisk). Aplikace neposílá nic nikomu, dokud žádost sám neschválíš, takže se nedá zneužít k rozesílání spamu. Proti zneužití formuláře je navíc limit 3 žádosti na návštěvníka za den, `MAX_REQUESTS_PER_DAY` celkem a skryté pole proti robotům. Žádost zadanou z té samé adresy do 24 hodin aplikace nepošle znovu.

Bez nastaveného `PUBLIC_URL` e-mail odkaz neobsahuje a žádost vyřídíš ručně:

```
lumenkeys requests                          # čekající žádosti (nový klíč / víc zařízení, počet, důvod, příkaz k vyřízení)
lumenkeys add "jan@seznam.cz" --request 3   # vytvoří klíč na počet zařízení ze žádosti č. 3 a označí ji jako vyřízenou
lumenkeys devices 5 3 --request 4           # navýšení: klíč č. 5 na 3 zařízení a žádost č. 4 vyřízená
```

Pak v e-mailu klikneš na Odpovědět (jde rovnou žadateli) a vložíš klíč. `lumenkeys requests done 3` označí žádost jako vyřízenou bez vytváření klíče.

Pro Gmail: v Google účtu zapni dvoufázové ověření, na https://myaccount.google.com/apppasswords vytvoř **heslo aplikace** a vlož ho do `/etc/lumen.env` jako `SMTP_PASSWORD` **bez mezer** (soubor čte i shell, mezery by ho rozbily). Obyčejné heslo k účtu nefunguje a nikdy ho tam nedávej. Po změně `/etc/lumen.env` spusť `sudo systemctl restart lumen`.

V souboru `data/keys.json` jsou jen otisky klíčů, takže ztracený klíč nejde zobrazit znovu, jen vystavit nový. Soubor `data/` nikam nesdílej a zálohuj ho.

Pozor: s nastaveným `AUTH_PASSWORD` si web zamkne heslem úplně celý (i návštěvníky s klíčem). Pro veřejný web s limitem nech `AUTH_PASSWORD` prázdné.

### Hudba

Záložka **Hudba** má tři části:

- **Online**: domovská stránka (rychlý výběr, hity, novinky), **žánry a nálady** (Rock, Pop, Hip-hop, Klasická… a Pohoda, Párty, Cvičení…, klik otevře playlisty žánru), hledání skladeb, alb, interpretů, playlistů a videí, stránky alb, interpretů a playlistů, rádio interpreta, text písně a **Automix** (když fronta dohraje, přidá podobné skladby). Do hledání jde vložit i odkaz z YouTube Music nebo YouTube. Katalog čte knihovna `ytmusicapi` bez přihlášení, zvuk vybírá yt-dlp (m4a, hraje i Safari) a server ho proudem přeposílá jako náhled videa. Každou skladbu jde **stáhnout** jako zvuk (počítá se jako běžné stažení). Ve výchozím stavu je online hudba jen pro návštěvníky s přístupovým klíčem (`MUSIC_REQUIRE_KEY`), aby starý server nezahltil kdokoliv z internetu.
- **V zařízení**: návštěvník vybere složku s hudbou (Chrome a Edge si ji zapamatují, po restartu prohlížeče stačí potvrdit **Znovu otevřít**) nebo přidá soubory. Soubory se nikam neposílají, hrají přímo z jeho disku, takže funguje i bez klíče. U souborů stažených Lumenem se z názvu odřízne `[id]` a „Interpret - Název“ se rozdělí.
- **Playlisty**: vlastní playlisty uložené v prohlížeči (localStorage). Skladba se přidá tlačítkem **Do playlistu**, celé album nebo playlist tlačítkem **Uložit jako playlist**, frontu v panelu fronty. Playlist jde přejmenovat, smazat a měnit v něm pořadí. Může mít online skladby i skladby ze zařízení; ty se přehrají, až je otevřená jejich složka.

Když skladba nejde přehrát, přehrávač se serveru zeptá na důvod a ukáže ho i s kódem nápovědy (třeba E110 při omezení IP). Server zkouší zvuk nejdřív přes YouTube Music (m4a), pak přes obyčejný YouTube, a každé selhání zapíše do logu (`journalctl -u lumen | grep Hudba`).

**Mezipaměť:** na mican-serveru googlevideo pustí přímé čtení samotné zvukové stopy jen na začátek a pak vrací 403 (náhled videa s kombinovaným formátem funguje, ověřeno 2026-10-15). Po prvním takovém 403 proto server skladby stahuje přes yt-dlp (stejně jako běžné stažení) do `MUSIC_CACHE_DIR` a posílá je prohlížeči už během stahování, takže hrají po pár sekundách; přetáčení na místo, které ještě není stažené, počká na konec stažení. Opakované přehrání jde rovnou z disku a přehrávač si během hraní nechá připravit další skladbu ve frontě. Stahuje se bez úpravy kontejneru (`fixup=never`), aby byly bajty během stahování i potom stejné.

Přehrávač má frontu, náhodné pořadí, opakování (vše / jedna skladba), hlasitost a ovládání ze zamčené obrazovky a mediálních kláves (Media Session). Online hudba zatěžuje server i síť a zvyšuje riziko, že YouTube IP serveru dočasně omezí (chyba E110), proto jsou na ni limity `MUSIC_BROWSE_PER_HOUR` a `MUSIC_TRACKS_PER_HOUR`.

Pozor: `ytmusicapi` s jazykem `cs` ve filtrovaném hledání nic nenajde (počítá s anglickými popisky), proto se nastavuje jen země (`MUSIC_LOCATION`).

### Noční aktualizace yt-dlp

`update.sh` nainstaluje systemd timer `lumen-ytdlp.timer`, který každou noc mezi 4:00 a 4:30 spustí `deploy/ytdlp-update.sh`. Ten aktualizuje yt-dlp v `.venv`, a když se verze změní, počká, až Lumen nemá rozdělané stahování (nejvýš 30 minut, podle `active` v `/api/health`), a službu restartuje. Když server v tu dobu neběžel, aktualizace proběhne po startu.

```
systemctl list-timers lumen-ytdlp.timer      # kdy poběží příště
journalctl -u lumen-ytdlp -n 20 --no-pager   # výsledek posledních běhů
sudo systemctl start lumen-ytdlp             # aktualizovat hned
```

### Pozor na YouTube ze serveru
YouTube často blokuje IP adresy datacenter (VPS) a chce po nich přihlášení. Když na serveru stahování z YouTube selhává a doma funguje, pomůže exportovat cookies z prohlížeče (rozšíření typu "Get cookies.txt LOCALLY"), nahrát soubor na server a nastavit `COOKIES_FILE=/cesta/k/cookies.txt`. Cookies jsou citlivé, jako heslo k účtu, takže soubor nikam nesdílej. Doma na vlastním počítači tenhle problém obvykle není.

## Struktura

```
backend/main.py     API: info, stahování, převod, fronta, weby, klíče a žádosti
backend/music.py    API hudby: katalog (ytmusicapi) a přehrávání zvuku přes server
static/             frontend (index.html, style.css, app.js, music.js)
deploy/             systemd služba, noční aktualizace yt-dlp (timer + skript), lumenkeys
desktop/            Lumen pro PC: spouštěč, instalátor a sestavovací skript
run.py              spuštění serveru
start.bat/.sh       instalace a spuštění jedním příkazem
Dockerfile, docker-compose.yml
```

## Lumen pro PC (Windows)

Instalační balíček `LumenSetup-<verze>.exe`: Lumen pak běží celý na počítači uživatele, ne na serveru. YouTube vidí jeho IP a server nic nestahuje.

- **Klíče a limity platí stejně jako na webu.** Denní limit (5 stažení) se počítá v aplikaci, online hudba je jen s klíčem. Klíč, žádost o klíč i žádost o víc zařízení vyřizuje server Lumenu (`KEY_SERVER`, endpointy `/api/remote/key` a `/api/remote/increase`): klíč se přiřadí k instalaci (stálé ID v `%LOCALAPPDATA%\Lumen\data\device_id`) jako jedno zařízení, schvalování e-mailem je stejné. Ověření platí 6 hodin, když server není dostupný, až 7 dní. **Server musí mít nasazenou verzi s těmito endpointy dřív, než lidé začnou PC verzi používat.**

- **Instalace** bez práv správce do `%LOCALAPPDATA%\Programs\Lumen`, zástupce v nabídce Start (a volitelně na ploše), odinstalace přes Nastavení → Aplikace → Lumen. Data (mezipaměť hudby, okno) jsou v `%LOCALAPPDATA%\Lumen`, stažené soubory ukládá prohlížeč do složky Stažené soubory.
- **Okno** je Microsoft Edge v režimu aplikace s vlastním profilem (Edge je ve Windows 10 a 11 vždy), bez něj Chrome, jinak výchozí prohlížeč. Zavřením okna se Lumen ukončí, druhé spuštění otevře další okno ke stejnému Lumenu.
- **yt-dlp se aktualizuje sám**: při spuštění zkontroluje PyPI, novou verzi stáhne (ověří SHA-256) do `%LOCALAPPDATA%\Lumen\pylib` a použije ji od dalšího spuštění. Proto není zabalený uvnitř exe, ale přibalený jako složka.
- **YouTube a JavaScript**: PC verze použije Deno, Node.js nebo Bun, cokoliv je nainstalované (`JS_RUNTIMES=deno,node,bun`). Bez nich některá videa nejdou, Lumen to ukáže v pruhu nahoře.
- **Log**: `%LOCALAPPDATA%\Lumen\lumen.log`.

Sestavení (Windows, Python 3.12+):

```
python -m venv .venv-desktop
.venv-desktop\Scripts\pip install -r requirements.txt -r desktop/requirements-desktop.txt
.venv-desktop\Scripts\python desktop/build_desktop.py
```

Výsledek je `dist\LumenSetup-<verze>.exe` (asi 80 MB, po instalaci asi 150 MB).

**Stažení z webu:** web Lumenu nabízí na stránce Stáhnout kartu „Lumen pro Windows“ (jen ve Windows prohlížeči), když je ve složce `APP_DIR` (výchozí `/var/lib/lumen/app`) instalátor `LumenSetup-<verze>.exe`; nabízí se nejvyšší verze. PC verze stejný údaj (`/api/app`) používá, aby uživateli nahlásila novější verzi. Nahrání nové verze z Windows:

```
scp dist\LumenSetup-1.1.0.exe mican@mican-server:/tmp/
ssh -t mican@mican-server "sudo install -D -o lumen -g lumen -m 644 /tmp/LumenSetup-1.1.0.exe /var/lib/lumen/app/LumenSetup-1.1.0.exe"
```

Starší verze ve složce nevadí (nabízí se nejvyšší), můžeš je smazat. Verze je v `desktop/lumen_desktop.py` a `desktop/setup_app.py` (`APP_VERSION`). Instalátor není podepsaný, Windows SmartScreen proto při prvním spuštění zobrazí varování (Další informace → Přesto spustit).

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
lumenkeys requests         # čekající žádosti o klíč a o víc zařízení z webu
lumenkeys devices 2 3        # klíč č. 2 bude platit na 3 zařízení
lumenkeys reset 2            # uvolní zařízení klíče č. 2
lumenkeys revoke 2           # zruší klíč č. 2
```
