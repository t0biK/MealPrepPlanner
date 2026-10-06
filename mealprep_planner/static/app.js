let strings = {};

function t(key, params = {}) {
  const s = strings[key] ?? key;
  return s.replace(/\{(\w+)\}/g, (m, k) => (k in params ? params[k] : m));
}

function pickLang() {
  for (const l of navigator.languages || []) {
    const code = l.slice(0, 2).toLowerCase();
    if (code === "de" || code === "en") return code;
  }
  return "de";
}

async function getJson(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(url + " " + r.status);
  return r.json();
}

async function main() {
  const app = document.getElementById("app");
  const lang = pickLang();
  document.documentElement.lang = lang;
  try {
    strings = await getJson("i18n/" + lang + ".json");
    document.title = t("app.title");
    const [health, me] = await Promise.all([getJson("api/health"), getJson("api/me")]);
    app.textContent =
      t("app.running", { version: health.version }) + " · " + t("app.hello", { name: me.display_name });
  } catch (e) {
    app.textContent = t("error.generic");
  }
}

main();
