/* Nápověda k chybám: načte katalog /errors.json a pozná, který kód chyby patří k textu hlášky.
   Používá ho aplikace (tlačítko Pomoc u chyby) i stránka napoveda.html. */
(() => {
  "use strict";
  let list = null;
  let loading = null;

  function load() {
    if (list) return Promise.resolve(list);
    if (!loading) {
      loading = fetch("/errors.json", { cache: "no-cache" })
        .then((r) => { if (!r.ok) throw new Error("errors.json"); return r.json(); })
        .then((j) => {
          list = (j.errors || []).map((e) => ({
            ...e,
            _re: (e.match || []).map((m) => { try { return new RegExp(m, "i"); } catch { return null; } }).filter(Boolean),
          }));
          return list;
        })
        .catch(() => { loading = null; return []; }); // příště se načte znovu
    }
    return loading;
  }

  /** Vrátí záznam katalogu pro text chyby, nebo null (když katalog ještě není načtený nebo nic nesedí). */
  function find(message) {
    if (!list || !message) return null;
    const text = String(message);
    return list.find((e) => e._re.some((re) => re.test(text))) || null;
  }

  function byCode(code) {
    return list ? list.find((e) => e.code === String(code).toUpperCase()) || null : null;
  }

  function fallback() { return byCode("E999"); }

  /** Odkaz na stránku nápovědy. U neznámé chyby se přidá i její text, ať ho uživatel vidí a může poslat dál. */
  function url(entry, message) {
    let u = "/napoveda.html?kod=" + encodeURIComponent(entry.code);
    if (entry.code === "E999" && message) u += "&text=" + encodeURIComponent(String(message).slice(0, 300));
    return u + "#" + entry.code;
  }

  window.LumenHelp = { load, find, byCode, fallback, url, all: () => list || [] };
})();
