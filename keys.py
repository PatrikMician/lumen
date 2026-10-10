"""Správa přístupových klíčů (odstraňují denní limit stažení).

Klíč se při prvním zadání přiřadí k prohlížeči (zařízení), kde byl zadán, a jinde nefunguje.

Použití (ve složce projektu, přes python z .venv, bez sudo):
    python keys.py add "Honza"                vytvoří klíč pro 1 zařízení a vypíše ho (zobrazí se jen teď)
    python keys.py add "Honza" --devices 2    klíč pro 2 zařízení (např. telefon a počítač té samé osoby)
    python keys.py add "a@b.cz" --request 4   vytvoří klíč a označí žádost č. 4 jako vyřízenou
    python keys.py requests                   čekající žádosti o klíč z webu (e-mail, kdy přišla)
    python keys.py requests done 4            označí žádost č. 4 jako vyřízenou, aniž by se vytvářel klíč
    python keys.py list                       seznam klíčů a kolik zařízení mají obsazených
    python keys.py reset 3                    uvolní zařízení klíče č. 3 (nový telefon, vymazané cookies)
    python keys.py revoke 3                   zruší klíč č. 3 úplně

Aplikaci není potřeba restartovat, změny platí hned. Klíč se dá zadat podle čísla i podle přesného popisku.
"""
import sys
import time

from backend import keyrequests, quota


def main(argv):
    args = argv[1:]
    cmd = args[0] if args else ""
    rest = args[1:]

    if cmd == "add":
        devices = 1
        if "--devices" in rest:
            i = rest.index("--devices")
            try:
                devices = int(rest[i + 1])
                del rest[i:i + 2]
            except (IndexError, ValueError):
                print("Za --devices musí být číslo, třeba: python keys.py add \"Honza\" --devices 2")
                return 1
        request_id = None
        if "--request" in rest:
            i = rest.index("--request")
            try:
                request_id = int(rest[i + 1])
                del rest[i:i + 2]
            except (IndexError, ValueError):
                print("Za --request musí být číslo žádosti z `python keys.py requests`.")
                return 1
        label = " ".join(rest) or "bez popisku"
        key = quota.add_key(label, devices)
        if request_id is not None:
            keyrequests.mark_done(request_id)
        print(f"Vytvořen klíč pro: {label} (zařízení: {max(1, devices)})")
        print()
        print(f"    {key}")
        print()
        print("Zkopíruj si ho teď, příště už ho nepůjde zobrazit (uložený je jen jeho otisk).")
        print("Při prvním zadání se přiřadí k tomu zařízení, kde ho dotyčný vloží.")
    elif cmd == "requests":
        if rest[:1] == ["done"] and len(rest) > 1:
            ident = " ".join(rest[1:])
            print("Označeno jako vyřízené." if keyrequests.mark_done(ident)
                  else "Takovou žádost jsem nenašel (zkus číslo z `python keys.py requests`).")
            return 0
        open_requests = keyrequests.list_requests()
        if not open_requests:
            print("Žádné čekající žádosti o klíč.")
        for r in open_requests:
            when = time.strftime("%d.%m.%Y %H:%M", time.localtime(r.get("ts", 0)))
            mail = {True: "e-mail odeslán", False: "e-mail se NEODESLAL", None: "e-mail se odesílá"}[r.get("mailed")]
            print(f"{r['id']:>3}  {when}  {r['email']:<36} {mail}")
        if open_requests:
            print()
            print('Vyřízení: sudo lumenkeys add "<e-mail>" --request <číslo>   a klíč pošli na ten e-mail.')
    elif cmd == "list":
        records = quota.list_keys()
        if not records:
            print('Zatím žádné klíče. Vytvoř první: python keys.py add "Jméno"')
        for r in records:
            state = "ZRUŠEN" if r.get("revoked") else "platí"
            used = len(r.get("devices", []))
            slots = f"{used}/{r.get('max_devices', 1)} zař."
            created = time.strftime("%d.%m.%Y", time.localtime(r.get("created", 0)))
            print(f"{r['id']:>3}  {state:<7} {slots:<10} {r['label']:<28} ...{r.get('hint', '?')}  ({created})")
    elif cmd == "reset" and rest:
        ident = " ".join(rest)
        print("Zařízení uvolněna, klíč se přiřadí znovu při dalším zadání." if quota.reset_key(ident)
              else "Takový klíč jsem nenašel (zkus číslo z `python keys.py list`).")
    elif cmd == "revoke" and rest:
        ident = " ".join(rest)
        print("Zrušeno." if quota.revoke_key(ident) else "Takový klíč jsem nenašel (zkus číslo z `python keys.py list`).")
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
