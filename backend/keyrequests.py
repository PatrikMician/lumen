"""Žádosti o přístupový klíč.

Návštěvník v Nastavení napíše svůj e-mail a klikne na „Požádat o klíč“. Žádost se uloží do
data/requests.json a provozovateli (REQUEST_TO) přijde e-mail, ve kterém je Reply-To nastavený na
adresu žadatele, takže stačí odpovědět. Klíč pak provozovatel vytvoří příkazem `lumenkeys add`
a pošle ho žadateli na jeho adresu.

V e-mailu jsou odkazy Schválit a Zamítnout (jednorázový náhodný token, platí 14 dní, token se ukládá jen
jako hash). Po schválení aplikace sama vytvoří klíč a pošle ho žadateli. Žadateli se tak nikdy nepošle nic
bez provozovatelova souhlasu, takže se aplikace nedá zneužít k rozesílání spamu.
Odeslání e-mailu je nastavené proměnnými prostředí (viz deploy/lumen.env.example). Bez nich se žádosti
jen ukládají a vyřídit se dají příkazem `lumenkeys requests`.

Žádost má druh (kind):
- "new"       nový klíč; žadatel si zvolí počet zařízení (1 až MAX_REQUEST_DEVICES) a může napsat důvod,
- "increase"  navýšení počtu zařízení u klíče, který žadatel už má; důvod je povinný.
Provozovatel na potvrzovací stránce počet zařízení schválí, nebo ho před schválením upraví.

Modul používá jen standardní knihovnu.
"""
import hashlib
import json
import logging
import os
import re
import secrets
import smtplib
import ssl
import threading
import time
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from . import quota

log = logging.getLogger("lumen.requests")

REQUESTS_FILE = quota.DATA_DIR / "requests.json"

SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_SECURITY = os.getenv("SMTP_SECURITY", "starttls").lower()  # starttls (587), ssl (465) nebo none
SMTP_USER = os.getenv("SMTP_USER", "").strip()
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "").replace(" ", "")  # Google heslo aplikace se zobrazuje po čtyřech znacích
MAIL_FROM = os.getenv("MAIL_FROM", "").strip() or SMTP_USER
REQUEST_TO = os.getenv("REQUEST_TO", "").strip() or SMTP_USER
MAX_REQUESTS_PER_DAY = int(os.getenv("MAX_REQUESTS_PER_DAY", "50"))  # celkem na celém serveru
REPEAT_AFTER = 24 * 3600  # stejná adresa může žádat znovu až po 24 hodinách
TOKEN_TTL = 14 * 86400  # jak dlouho jde žádost schválit odkazem z e-mailu
PUBLIC_URL = os.getenv("PUBLIC_URL", "").strip().rstrip("/")  # např. https://lumen.mican.dpdns.org
if PUBLIC_URL and not PUBLIC_URL.startswith(("http://", "https://")):
    PUBLIC_URL = "https://" + PUBLIC_URL
APPROVE_DEVICES = max(1, int(os.getenv("APPROVE_DEVICES", "1")))  # počet zařízení u starších žádostí, které ho neuvádějí
MAX_REQUEST_DEVICES = max(1, int(os.getenv("MAX_REQUEST_DEVICES", "5")))  # kolik zařízení si jde říct z webu
MAX_DEVICES = max(MAX_REQUEST_DEVICES, int(os.getenv("MAX_DEVICES", "20")))  # kolik zařízení může povolit provozovatel
REASON_MAX = 500

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9\-]+(\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,24}")
_lock = threading.Lock()


def _hash_token(token: str) -> str:
    return hashlib.sha256((token or "").encode()).hexdigest()


def valid_email(raw: str):
    """Vrátí e-mail malými písmeny, nebo None, když nevypadá jako e-mailová adresa.
    Povolené znaky jsou záměrně úzké, adresa se tak nedá použít k vložení hlaviček ani příkazů."""
    email = (raw or "").strip().lower()
    if len(email) > 254 or not EMAIL_RE.fullmatch(email) or ".." in email:
        return None
    return email


def clean_reason(raw: str) -> str:
    """Důvod žádosti: bez řídicích znaků (zalomení řádků zůstanou), oříznutý na REASON_MAX znaků."""
    text = re.sub(r"[\x00-\x09\x0b-\x1f\x7f]", " ", raw or "").replace("\r", "")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()[:REASON_MAX]


def requested_devices(rec: dict) -> int:
    return max(1, int(rec.get("devices") or APPROVE_DEVICES))


def mail_configured() -> bool:
    return bool(SMTP_USER and SMTP_PASSWORD and REQUEST_TO)


def _read() -> list:
    try:
        data = json.loads(REQUESTS_FILE.read_text("utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _write(records: list):
    tmp = REQUESTS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(records, ensure_ascii=False, indent=2), "utf-8")
    try:
        tmp.chmod(0o600)
    except OSError:
        pass
    os.replace(tmp, REQUESTS_FILE)


def _is_open(r: dict, now: float) -> bool:
    return not r.get("done") and now - r.get("ts", 0) < TOKEN_TTL


def add_request(email: str, devices: int = 1, reason: str = "", key: dict = None):
    """Uloží žádost. Bez `key` je to žádost o nový klíč, s `key` (záznam z quota) o navýšení počtu zařízení.
    Vrátí (záznam, stav), kde stav je:
    "ok"        nová žádost, provozovatele je potřeba upozornit,
    "duplicate" stejná žádost už čeká (ze stejné adresy dnes, u navýšení pro stejný klíč), nic se neposílá znovu,
    "full"      dnes už přišlo příliš mnoho žádostí."""
    now = time.time()
    kind = "increase" if key else "new"
    with _lock:
        records = _read()
        # hotové žádosti se po 60 dnech mažou, ať soubor nerostne
        records = [r for r in records if not (r.get("done") and now - r.get("ts", 0) > 60 * 86400)]
        for r in records:
            if r.get("kind", "new") != kind or r.get("done"):
                continue
            if kind == "new" and r.get("email") == email and now - r.get("ts", 0) < REPEAT_AFTER:
                return r, "duplicate"
            if kind == "increase" and r.get("key_id") == key.get("id") and _is_open(r, now):
                return r, "duplicate"
        if sum(1 for r in records if now - r.get("ts", 0) < 86400) >= MAX_REQUESTS_PER_DAY:
            return None, "full"
        token = secrets.token_urlsafe(32)
        rec = {
            "id": max((r.get("id", 0) for r in records), default=0) + 1,
            "kind": kind,
            "email": email,
            "devices": max(1, int(devices)),
            "reason": clean_reason(reason),
            "ts": int(now),
            "done": False,
            "mailed": None,  # None = zatím se neposílalo, True/False = výsledek odeslání
            "token_hash": _hash_token(token),  # token samotný se neukládá, jen jeho otisk
        }
        if key:
            rec.update(key_id=key.get("id"), key_label=key.get("label", ""),
                       current_devices=int(key.get("max_devices", 1)), used_devices=len(key.get("devices", [])))
        records.append(rec)
        _write(records)
    return {**rec, "token": token}, "ok"  # token je jen v paměti, pro odkazy v e-mailu


def open_increase(key_id) -> dict:
    """Čekající žádost o navýšení pro daný klíč (zobrazuje se v Nastavení), nebo None."""
    now = time.time()
    with _lock:
        for r in _read():
            if r.get("kind") == "increase" and r.get("key_id") == key_id and _is_open(r, now):
                return r
    return None


def list_requests(only_open: bool = True) -> list:
    with _lock:
        records = _read()
    return [r for r in records if not (only_open and r.get("done"))]


def mark_done(ident) -> bool:
    with _lock:
        records = _read()
        hit = False
        for r in records:
            if str(r.get("id")) == str(ident) or r.get("email") == str(ident).lower():
                r["done"] = True
                hit = True
        if hit:
            _write(records)
    return hit


def _token_ok(rec: dict, token: str) -> bool:
    return (not rec.get("done") and bool(rec.get("token_hash"))
            and secrets.compare_digest(rec["token_hash"], _hash_token(token))
            and time.time() - rec.get("ts", 0) < TOKEN_TTL)


def peek_request(rec_id: int, token: str):
    """Vrátí čekající žádost, pokud token sedí, jinak None. Nic nemění (volá se při zobrazení stránky)."""
    with _lock:
        for r in _read():
            if r.get("id") == rec_id and _token_ok(r, token):
                return r
    return None


def resolve_request(rec_id: int, token: str, decision: str, devices: int = None):
    """Jednorázově vyřídí žádost (decision = "approved" nebo "denied"); u schválení se uloží počet zařízení,
    který provozovatel potvrdil. Druhé použití stejného odkazu vrátí None."""
    with _lock:
        records = _read()
        for r in records:
            if r.get("id") == rec_id and _token_ok(r, token):
                r["done"] = True
                r["decision"] = decision
                r["token_hash"] = ""
                if decision == "approved" and devices:
                    r["approved_devices"] = int(devices)
                _write(records)
                return r
    return None


def _mark_mailed(rec_id: int, ok: bool):
    with _lock:
        records = _read()
        for r in records:
            if r.get("id") == rec_id:
                r["mailed"] = ok
        _write(records)


def manual_command(rec: dict) -> str:
    """Příkaz pro vyřízení žádosti bez odkazu z e-mailu."""
    devices = requested_devices(rec)
    if rec.get("kind") == "increase":
        return f"sudo lumenkeys devices {rec.get('key_id')} {devices} --request {rec['id']}"
    return f"sudo lumenkeys add \"{rec['email']}\" --devices {devices} --request {rec['id']}"


def build_mail(rec: dict) -> EmailMessage:
    email = rec["email"]
    increase = rec.get("kind") == "increase"
    devices = requested_devices(rec)
    when = time.strftime("%d.%m.%Y %H:%M", time.localtime(rec["ts"]))
    msg = EmailMessage()
    msg["Subject"] = (f"Lumen: žádost o víc zařízení ({devices}) od {email}" if increase
                      else f"Lumen: žádost o klíč od {email}")
    msg["From"] = MAIL_FROM
    msg["To"] = REQUEST_TO
    msg["Reply-To"] = email  # odpověď jde rovnou žadateli
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain="lumen.local")
    if increase:
        head = (
            f"Někdo s klíčem na Lumenu žádá o navýšení počtu zařízení.\n"
            f"\n"
            f"E-mail žadatele: {email}\n"
            f"Klíč:            č. {rec.get('key_id')} „{rec.get('key_label', '')}“, teď {rec.get('current_devices', 1)} zař."
            f" (obsazeno {rec.get('used_devices', 0)})\n"
            f"Žádá o:          {devices} zař.\n"
        )
    else:
        head = (
            f"Někdo si na Lumenu žádá o přístupový klíč.\n"
            f"\n"
            f"E-mail žadatele: {email}\n"
            f"Počet zařízení:  {devices}\n"
        )
    head += f"Odesláno:        {when}\nČíslo žádosti:   {rec['id']}\n"
    if rec.get("reason"):
        head += "\nDůvod:\n" + "\n".join("  " + line for line in rec["reason"].splitlines()) + "\n"
    head += "\n"
    token = rec.get("token")
    if PUBLIC_URL and token:
        base = f"{PUBLIC_URL}/api/key/approve?id={rec['id']}&t={token}"
        what = ("navýšení se hned projeví a žadateli přijde e-mail" if increase
                else "klíč se vytvoří a sám se pošle žadateli")
        body = (
            f"SCHVÁLIT ({what}):\n"
            f"{base}\n"
            f"\n"
            f"Na stránce, která se otevře, můžeš počet zařízení upravit a potvrdíš tlačítkem. Odkaz platí 14 dní a jde použít jednou.\n"
            f"Zamítnout můžeš na stejné stránce. Když nic neuděláš, žádost jen čeká.\n"
            f"\n"
            f"Ručně (bez odkazu): {manual_command(rec)}\n"
        )
    else:
        body = (
            f"1) Na serveru žádost vyřiď (a zároveň se označí jako vyřízená):\n"
            f"\n"
            f"    {manual_command(rec)}\n"
            f"\n"
            f"2) Klikni na Odpovědět. Tahle zpráva má Reply-To nastavený na adresu žadatele, takže odpověď jde\n"
            f"   rovnou jemu.{'' if increase else ' Vlož do ní klíč.'}\n"
            f"\n"
            f"(Odkaz pro schválení jedním kliknutím se objeví po nastavení PUBLIC_URL v /etc/lumen.env.)\n"
        )
    msg.set_content(head + body + "\nSeznam čekajících žádostí: sudo lumenkeys requests\n")
    return msg


def _devices_text(devices: int) -> str:
    if devices == 1:
        return "Klíč se přiřadí k zařízení, kde ho zadáš jako první, a na jiném zařízení nebude fungovat."
    return (f"Klíč platí na {devices} zařízeních: přiřadí se postupně k prvním {devices} zařízením (prohlížečům), "
            f"kde ho zadáš, a na dalších už fungovat nebude.")


def build_devices_mail(email: str, devices: int) -> EmailMessage:
    """E-mail žadateli o schváleném navýšení počtu zařízení."""
    msg = EmailMessage()
    msg["Subject"] = f"Lumen: tvůj klíč teď platí na {devices} zařízení"
    msg["From"] = MAIL_FROM
    msg["To"] = email
    msg["Reply-To"] = REQUEST_TO
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain="lumen.local")
    msg.set_content(
        f"Ahoj,\n"
        f"\n"
        f"tvoje žádost o víc zařízení byla schválena. Tvůj přístupový klíč teď platí na {devices} zařízení.\n"
        f"\n"
        f"Na novém zařízení otevři Lumen, jdi do Nastavení a v části Přístup zadej stejný klíč jako dosud.\n"
        f"Zařízení, kde klíč už používáš, zůstávají beze změny.\n"
    )
    return msg


def send_devices_mail(email: str, devices: int):
    if not mail_configured():
        raise RuntimeError("Není nastavený SMTP_USER a SMTP_PASSWORD.")
    _send(build_devices_mail(email, devices))


def build_key_mail(email: str, key: str, devices: int = 1) -> EmailMessage:
    """E-mail žadateli s vystaveným klíčem. Odpověď na něj jde provozovateli."""
    msg = EmailMessage()
    msg["Subject"] = "Tvůj přístupový klíč pro Lumen"
    msg["From"] = MAIL_FROM
    msg["To"] = email
    msg["Reply-To"] = REQUEST_TO
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain="lumen.local")
    where = f"Otevři Lumen ({PUBLIC_URL}), jdi do Nastavení a v části Přístup klíč vlož." if PUBLIC_URL \
        else "Otevři Lumen, jdi do Nastavení a v části Přístup klíč vlož."
    msg.set_content(
        f"Ahoj,\n"
        f"\n"
        f"tvoje žádost o přístup do Lumenu byla schválena. Tady je tvůj klíč:\n"
        f"\n"
        f"    {key}\n"
        f"\n"
        f"{where}\n"
        f"\n"
        f"{_devices_text(devices)} "
        f"Nikomu ho proto neposílej. Kdyby sis měnil telefon nebo prohlížeč, odpověz na tenhle e-mail "
        f"a klíč ti uvolním. O víc zařízení požádáš v Nastavení → Přístup.\n"
    )
    return msg


def send_key_mail(email: str, key: str, devices: int = 1):
    """Pošle žadateli klíč. Při chybě vyvolá výjimku (volající ukáže klíč provozovateli, ať ho pošle ručně)."""
    if not mail_configured():
        raise RuntimeError("Není nastavený SMTP_USER a SMTP_PASSWORD.")
    _send(build_key_mail(email, key, devices))


def _send(msg: EmailMessage):
    ctx = ssl.create_default_context()
    if SMTP_SECURITY == "ssl":
        server = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=20, context=ctx)
    else:
        server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20)
    with server:
        server.ehlo()
        if SMTP_SECURITY == "starttls":
            server.starttls(context=ctx)
            server.ehlo()
        if SMTP_USER and SMTP_PASSWORD:
            server.login(SMTP_USER, SMTP_PASSWORD)
        server.send_message(msg)


def notify(rec: dict):
    """Pošle provozovateli e-mail o nové žádosti. Má běžet na pozadí, chyby se jen zalogují
    a žádost zůstane uložená (uvidí se v `lumenkeys requests`)."""
    if not mail_configured():
        log.warning("Žádost o klíč č. %s uložena, ale e-mail se neposílá: chybí SMTP_USER nebo SMTP_PASSWORD.", rec["id"])
        _mark_mailed(rec["id"], False)
        return
    try:
        _send(build_mail(rec))
    except Exception as exc:  # noqa: BLE001 - jakákoli chyba sítě nebo přihlášení
        log.error("Žádost o klíč č. %s: e-mail se nepodařilo odeslat (%s: %s).", rec["id"], type(exc).__name__, exc)
        _mark_mailed(rec["id"], False)
        return
    _mark_mailed(rec["id"], True)
