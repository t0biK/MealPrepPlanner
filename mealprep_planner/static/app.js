let strings = {};
let me = null;

function t(key, params = {}) {
  const s = strings[key] ?? key;
  return s.replace(/\{(\w+)\}/g, (m, k) => (k in params ? params[k] : m));
}

function pickLang() {
  if (me && me.lang) return me.lang;
  for (const l of navigator.languages || []) {
    const code = l.slice(0, 2).toLowerCase();
    if (code === "de" || code === "en") return code;
  }
  return "de";
}

async function api(method, url, body) {
  const opts = { method };
  if (method !== "GET") {
    opts.headers = { "Content-Type": "application/json" };
    opts.body = JSON.stringify(body ?? {});
  }
  const r = await fetch(url, opts);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) {
    const e = new Error(data.error || "generic");
    e.code = data.error;
    e.field = data.field;
    throw e;
  }
  return data;
}

function errorText(e) {
  const key = "error." + (e.code || "generic");
  const text = key in strings ? t(key) : t("error.generic");
  return e.field ? `${text} (${e.field})` : text;
}

function el(tag, props = {}, ...children) {
  const node = Object.assign(document.createElement(tag), props);
  node.append(...children);
  return node;
}

function option(value, label, selected) {
  return el("option", { value, textContent: label, selected });
}

function card(title, ...children) {
  return el("section", { className: "card" }, ...(title ? [el("h2", { textContent: title })] : []), ...children);
}

let toastTimer;
function toast(text) {
  const box = document.getElementById("toast");
  box.textContent = text;
  box.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => box.classList.remove("show"), 2500);
}

async function loadLang() {
  const lang = pickLang();
  document.documentElement.lang = lang;
  strings = await (await fetch("i18n/" + lang + ".json")).json();
  document.title = t("app.title");
}

function renderChrome(route) {
  document.getElementById("top").textContent = "🍽️ " + t("app.title");
  const tab = (href, icon, label, active) =>
    el("a", { href, ...(active ? { ariaCurrent: "page" } : {}) }, el("span", { textContent: icon }), label);
  document.getElementById("nav").replaceChildren(
    tab("#/rezepte", "🍲", t("nav.recipes"), route === "rezepte"),
    tab("#/import", "📥", t("nav.import"), route === "import"),
    tab("#/einstellungen", "⚙️", t("nav.settings"), route === "einstellungen" || route === "systemcheck"),
  );
}

async function pageSettings(app) {
  const settings = await api("GET", "api/settings");
  const save = async (patch) => {
    try {
      await api("PUT", "api/settings", patch);
      toast(t("settings.saved"));
    } catch (e) {
      toast(errorText(e));
    }
  };

  const lang = el("select", {},
    option("", t("settings.language.auto"), !me.lang),
    option("de", t("settings.language.de"), me.lang === "de"),
    option("en", t("settings.language.en"), me.lang === "en"));
  lang.onchange = async () => {
    me = await api("PUT", "api/me", { lang: lang.value || null });
    await render();
  };

  const entityPicker = async (domain, key) => {
    let entities = [];
    try {
      entities = await api("GET", "api/ha/entities?domain=" + domain);
    } catch (e) {
      toast(errorText(e));
    }
    const sel = el("select", {}, option("", t("settings.none"), !settings[key]),
      ...entities.map((x) => option(x.entity_id, x.friendly_name, x.entity_id === settings[key])));
    sel.onchange = () => save({ [key]: sel.value || null });
    return sel;
  };

  const aiEnabled = el("input", { type: "checkbox", checked: settings.ai_enabled });
  aiEnabled.onchange = () => save({ ai_enabled: aiEnabled.checked });

  const portions = el("input", { type: "number", min: 1, max: 12, step: 1, value: settings.default_portions,
    ariaLabel: t("settings.default_portions") });
  portions.onchange = () => save({ default_portions: Number(portions.value) });

  const health = await api("GET", "api/health");
  app.replaceChildren(
    el("h1", { textContent: t("settings.title") }),
    card(t("settings.language"), lang),
    card(t("settings.default_portions"), portions),
    await tagEditor(),
    card(t("settings.bring"), await entityPicker("todo", "bring_entity")),
    card(t("settings.ai"),
      el("label", { className: "row" }, t("settings.ai_enabled"), aiEnabled),
      await entityPicker("ai_task", "ai_entity")),
    card(null, el("a", { href: "#/systemcheck", textContent: t("settings.systemcheck") + " →" })),
    el("p", { className: "muted", textContent: t("app.running", { version: health.version }) }),
  );
}

function pageSystemcheck(app) {
  const checks = [
    ["ha", t("check.ha")],
    ["bring", t("check.bring")],
    ["ai", t("check.ai")],
    ["sensor", t("check.sensor")],
  ];
  const buttons = [];
  const cards = checks.map(([name, label]) => {
    const result = el("div");
    const button = el("button", { textContent: "▶", ariaLabel: label });
    buttons.push(button);
    button.onclick = async () => {
      buttons.forEach((b) => (b.disabled = true));
      result.replaceChildren(el("p", { className: "muted", textContent: t("systemcheck.running") }));
      try {
        const r = await api("POST", "api/system/check", { check: name });
        result.replaceChildren(
          el("span", { className: "badge " + (r.ok ? "ok" : "fail"), textContent: r.ok ? t("check.ok") : t("check.failed") }),
          el("details", {}, el("summary", { textContent: t("check.details") }),
            el("pre", { textContent: JSON.stringify(r.details, null, 2) })));
      } catch (e) {
        result.replaceChildren(el("span", { className: "badge fail", textContent: errorText(e) }));
      }
      buttons.forEach((b) => (b.disabled = false));
    };
    return card(null,
      el("div", { className: "row" }, el("strong", { textContent: label }), button),
      ...(name === "bring" ? [el("p", { className: "muted", textContent: t("check.bring.note") })] : []),
      result);
  });
  app.replaceChildren(el("h1", { textContent: t("systemcheck.title") }), ...cards);
}

// ---- recipes (M2) ----

let units = null; // [{unit, plural}] from the parser's unit table

async function loadUnits() {
  units ??= await api("GET", "api/units");
}

function fmtAmount(amount, unit) {
  if (amount == null) return "";
  const a = Math.round(amount * 100) / 100;
  const n = a.toLocaleString(document.documentElement.lang, { maximumFractionDigits: 2, useGrouping: false });
  if (!unit) return n;
  return `${n} ${a > 1 ? units.find((u) => u.unit === unit)?.plural ?? unit : unit}`;
}

function image(name) {
  return name ? [el("img", { className: "thumb", src: "images/" + name, alt: "", loading: "lazy" })] : [];
}

function chips(names) {
  return el("div", { className: "chips" }, ...names.map((n) => el("span", { className: "chip", textContent: n })));
}

function mealInfo(r) {
  return [
    r.total_minutes ? t("recipes.minutes", { n: r.total_minutes }) : "",
    r.for_lunch && !r.for_dinner ? t("recipes.lunch_only") : "",
    !r.for_lunch && r.for_dinner ? t("recipes.dinner_only") : "",
  ].filter(Boolean).join(" · ");
}

async function pageRecipes(app) {
  const tags = await api("GET", "api/tags");
  const list = el("div");
  let archived = false;

  const search = el("input", { type: "search", placeholder: t("recipes.search"), ariaLabel: t("recipes.search") });
  const tagSel = el("select", { ariaLabel: t("form.tags") }, option("", t("recipes.all_tags"), true),
    ...tags.map((x) => option(x.name, x.name, false)));
  const archive = el("input", { type: "checkbox" });

  const load = async () => {
    const qs = new URLSearchParams({ q: search.value, tag: tagSel.value, archived: archived ? 1 : 0 });
    try {
      const items = await api("GET", "api/recipes?" + qs);
      list.replaceChildren(...(items.length
        ? items.map((r) => el("a", { className: "card recipe-card", href: "#/rezepte/" + r.id },
          ...image(r.image),
          el("h2", { textContent: r.title }),
          el("p", { className: "muted", textContent: mealInfo(r) }),
          chips(r.tags)))
        : [el("p", { className: "muted", textContent: t("recipes.empty") })]));
    } catch (e) {
      toast(errorText(e));
    }
  };
  let timer;
  search.oninput = () => {
    clearTimeout(timer);
    timer = setTimeout(load, 250);
  };
  tagSel.onchange = load;
  archive.onchange = () => {
    archived = archive.checked;
    load();
  };

  app.replaceChildren(
    el("div", { className: "row" }, el("h1", { textContent: t("recipes.title") }),
      el("a", { className: "btn", href: "#/rezepte/neu", textContent: t("recipes.new") })),
    card(null, search, tagSel, el("label", { className: "row" }, t("recipes.archive"), archive)),
    list);
  await load();
}

async function pageRecipe(app, id) {
  await loadUnits();
  const r = await api("GET", "api/recipes/" + id);
  let portions = r.servings;

  const count = el("strong", { textContent: portions });
  const minus = el("button", { textContent: "−", ariaLabel: "−" });
  const plus = el("button", { textContent: "+", ariaLabel: "+" });
  const ingredients = el("ul", { className: "plain" });
  const showIngredients = () => {
    count.textContent = portions;
    minus.disabled = portions <= 1;
    plus.disabled = portions >= Math.max(12, r.servings);
    ingredients.replaceChildren(...r.ingredients.map((i) => el("li", {},
      el("strong", { textContent: fmtAmount(i.amount == null ? null : i.amount * portions / r.servings, i.unit) }),
      " " + i.name,
      ...(i.note ? [el("span", { className: "muted", textContent: " (" + i.note + ")" })] : []))));
  };
  minus.onclick = () => { portions--; showIngredients(); };
  plus.onclick = () => { portions++; showIngredients(); };
  showIngredients();

  const archiveBtn = el("button", { className: "secondary", textContent: r.archived ? t("recipe.restore") : t("recipe.archive") });
  archiveBtn.onclick = async () => {
    try {
      await api("POST", `api/recipes/${id}/${r.archived ? "restore" : "archive"}`);
      if (r.archived) await render();
      else location.hash = "#/rezepte";
    } catch (e) {
      toast(errorText(e));
    }
  };

  const nutrition = r.nutrition && [
    [r.nutrition.kcal, "recipe.kcal", ""], [r.nutrition.protein_g, "recipe.protein", " g"],
    [r.nutrition.fat_g, "recipe.fat", " g"], [r.nutrition.carbs_g, "recipe.carbs", " g"],
  ].filter(([v]) => v != null);
  const link = /^https?:\/\//.test(r.source_url || "") && el("a", {
    href: r.source_url, target: "_blank", rel: "noopener noreferrer", textContent: new URL(r.source_url).hostname });

  app.replaceChildren(
    el("a", { href: "#/rezepte", textContent: t("recipe.back") }),
    ...image(r.image),
    el("h1", { textContent: r.title }),
    ...(r.archived ? [el("p", {}, el("span", { className: "badge", textContent: t("recipe.archived") }))] : []),
    el("p", { className: "muted", textContent: mealInfo(r) }),
    chips(r.tags),
    card(t("recipe.ingredients"),
      el("div", { className: "row" }, t("recipe.portions"), el("div", { className: "stepper" }, minus, count, plus)),
      r.ingredients.length ? ingredients : el("p", { className: "muted", textContent: t("recipe.no_ingredients") })),
    ...(r.steps.length ? [card(t("recipe.steps"), el("ol", { className: "steps" },
      ...r.steps.map((s) => el("li", { textContent: s }))))] : []),
    ...(nutrition && nutrition.length ? [card(t("recipe.nutrition"),
      el("p", { textContent: nutrition.map(([v, k, u]) => `${v}${u} ${t(k)}`).join(" · ") }),
      ...(r.nutrition.source === "ai" ? [el("span", { className: "badge", textContent: t("recipe.estimated") })] : []))] : []),
    ...(link ? [el("p", {}, t("recipe.source") + ": ", link)] : []),
    el("div", { className: "actions" },
      el("a", { className: "btn", href: `#/rezepte/${id}/bearbeiten`, textContent: t("recipe.edit") }), archiveBtn),
  );
}

const numText = (n) => (n == null ? "" : String(+Number(n).toFixed(4)).replace(".", ","));

// `job` (import review): the form starts from the job's draft and saves it through the job
async function pageRecipeForm(app, id, job = null) {
  await loadUnits();
  const [tags, names, settings, existing] = await Promise.all([
    api("GET", "api/tags"), api("GET", "api/ingredient-names"), api("GET", "api/settings"),
    id ? api("GET", "api/recipes/" + id) : null,
  ]);
  const r = job ? job.draft : existing ?? { title: "", servings: settings.default_portions, total_minutes: null, for_lunch: true,
    for_dinner: true, tags: [], ingredients: [{}], steps: [], nutrition: null, source_url: null };

  const input = (props) => el("input", props);
  const label = (text, ...children) => el("label", { className: "field" }, el("span", { textContent: text }), ...children);
  const title = input({ type: "text", value: r.title, maxLength: 200, required: true });
  const servings = input({ type: "number", min: 1, max: 50, step: 1, value: r.servings });
  const minutes = input({ type: "number", min: 1, max: 1440, step: 1, value: r.total_minutes ?? "" });
  const lunch = input({ type: "checkbox", checked: r.for_lunch });
  const dinner = input({ type: "checkbox", checked: r.for_dinner });
  const sourceUrl = input({ type: "url", value: r.source_url ?? "", maxLength: 2048 });

  const tagBoxes = tags.map((x) => {
    const box = input({ type: "checkbox", checked: r.tags.some((n) => n.toLowerCase() === x.name.toLowerCase()) });
    return { name: x.name, box, node: el("label", { className: "chip pick" }, box, x.name) };
  });

  // ingredient rows
  const dataList = el("datalist", { id: "ingredient-names" }, ...names.map((n) => el("option", { value: n })));
  const ingBox = el("div");
  const ingRows = [];
  const addIngredient = (i = {}) => {
    const row = {
      amount: input({ type: "text", inputMode: "decimal", value: numText(i.amount), placeholder: t("form.amount"), ariaLabel: t("form.amount") }),
      unit: el("select", { ariaLabel: t("form.unit") }, option("", "–", !i.unit), ...units.map((u) => option(u.unit, u.unit, u.unit === i.unit))),
      name: input({ type: "text", value: i.name ?? "", maxLength: 100, placeholder: t("form.name"), ariaLabel: t("form.name") }),
      note: input({ type: "text", value: i.note ?? "", maxLength: 200, placeholder: t("form.note"), ariaLabel: t("form.note") }),
    };
    row.name.setAttribute("list", "ingredient-names");
    const remove = el("button", { type: "button", className: "secondary", textContent: "✕", ariaLabel: t("form.remove") });
    row.node = el("div", { className: "ing-row" }, row.amount, row.unit, remove, row.name, row.note);
    remove.onclick = () => {
      ingRows.splice(ingRows.indexOf(row), 1);
      row.node.remove();
    };
    ingRows.push(row);
    ingBox.append(row.node);
  };
  r.ingredients.forEach(addIngredient);
  const addIngBtn = el("button", { type: "button", className: "secondary", textContent: t("form.add_ingredient") });
  addIngBtn.onclick = () => addIngredient();

  const paste = el("textarea", { rows: 6, placeholder: t("form.paste_hint"), ariaLabel: t("form.paste") });
  const pasteBtn = el("button", { type: "button", className: "secondary", textContent: t("form.paste_apply") });
  pasteBtn.onclick = async () => {
    try {
      const parsed = await api("POST", "api/parse-ingredients", { text: paste.value });
      for (const row of ingRows.filter((x) => !x.name.value.trim() && !x.amount.value.trim())) {
        ingRows.splice(ingRows.indexOf(row), 1);
        row.node.remove();
      }
      parsed.forEach(addIngredient);
      paste.value = "";
    } catch (e) {
      toast(errorText(e));
    }
  };

  // step rows
  const stepBox = el("div");
  const stepRows = [];
  const addStep = (text = "") => {
    const area = el("textarea", { rows: 2, value: text, maxLength: 2000, ariaLabel: t("form.step") });
    const remove = el("button", { type: "button", className: "secondary", textContent: "✕", ariaLabel: t("form.remove") });
    const node = el("div", { className: "step-row" }, area, remove);
    remove.onclick = () => {
      stepRows.splice(stepRows.indexOf(area), 1);
      node.remove();
    };
    stepRows.push(area);
    stepBox.append(node);
  };
  r.steps.forEach(addStep);
  const addStepBtn = el("button", { type: "button", className: "secondary", textContent: t("form.add_step") });
  addStepBtn.onclick = () => addStep();

  const n = r.nutrition ?? {};
  const nutri = {
    kcal: input({ type: "number", min: 0, max: 5000, step: "any", value: n.kcal ?? "" }),
    protein_g: input({ type: "number", min: 0, max: 500, step: "any", value: n.protein_g ?? "" }),
    fat_g: input({ type: "number", min: 0, max: 500, step: "any", value: n.fat_g ?? "" }),
    carbs_g: input({ type: "number", min: 0, max: 500, step: "any", value: n.carbs_g ?? "" }),
  };

  const numOrNull = (box) => (box.value.trim() === "" ? null : Number(box.value));
  const save = async (ev) => {
    ev.preventDefault();
    if (!lunch.checked && !dinner.checked) return toast(t("form.need_meal"));
    const ingredients = [];
    for (const row of ingRows) {
      if (!row.name.value.trim() && !row.amount.value.trim() && !row.note.value.trim()) continue;
      const raw = row.amount.value.trim().replace(",", ".");
      const amount = raw === "" ? null : Number(raw);
      if (Number.isNaN(amount)) return toast(t("form.bad_amount", { value: row.amount.value }));
      ingredients.push({ amount, unit: row.unit.value || null, name: row.name.value, note: row.note.value || null });
    }
    const values = Object.fromEntries(Object.entries(nutri).map(([k, box]) => [k, numOrNull(box)]));
    const unchanged = n.source && Object.keys(values).every((k) => values[k] === (n[k] ?? null));
    const payload = {
      format_version: 1,
      title: title.value,
      source_url: sourceUrl.value.trim() || null,
      source_kind: r.source_kind ?? "manual",
      image: r.image ?? null,
      servings: Number(servings.value),
      total_minutes: numOrNull(minutes),
      for_lunch: lunch.checked,
      for_dinner: dinner.checked,
      tags: tagBoxes.filter((x) => x.box.checked).map((x) => x.name),
      ingredients,
      steps: stepRows.map((a) => a.value).filter((v) => v.trim()),
      nutrition: Object.values(values).every((v) => v === null) ? null : { ...values, source: unchanged ? n.source : "manual" },
    };
    try {
      const saved = job ? await api("POST", `api/imports/${job.id}/save`, payload)
        : await api(id ? "PUT" : "POST", id ? "api/recipes/" + id : "api/recipes", payload);
      location.hash = "#/rezepte/" + saved.id;
    } catch (e) {
      toast(errorText(e));
    }
  };

  const form = el("form", {},
    card(null, ...image(r.image), label(t("form.title"), title),
      el("div", { className: "two" }, label(t("form.servings"), servings), label(t("form.minutes"), minutes)),
      el("div", { className: "row" }, t("form.lunch"), lunch), el("div", { className: "row" }, t("form.dinner"), dinner)),
    card(t("form.tags"), el("div", { className: "chips" }, ...tagBoxes.map((x) => x.node))),
    card(t("form.ingredients"), dataList, ingBox, el("div", { className: "actions" }, addIngBtn),
      el("details", {}, el("summary", { textContent: t("form.paste") }), paste, pasteBtn)),
    card(t("form.steps"), stepBox, el("div", { className: "actions" }, addStepBtn)),
    card(t("form.nutrition"),
      ...(n.source === "ai" ? [el("p", {}, el("span", { className: "badge", textContent: t("recipe.estimated") }))] : []),
      el("div", { className: "two" },
        label(t("recipe.kcal"), nutri.kcal), label(t("recipe.protein") + " (g)", nutri.protein_g),
        label(t("recipe.fat") + " (g)", nutri.fat_g), label(t("recipe.carbs") + " (g)", nutri.carbs_g))),
    card(null, label(t("form.source_url"), sourceUrl)),
    el("div", { className: "actions" },
      el("button", { type: "submit", textContent: t("form.save") }),
      el("a", { className: "btn secondary", href: job ? "#/import" : id ? "#/rezepte/" + id : "#/rezepte", textContent: t("form.cancel") })));
  form.onsubmit = save;
  app.replaceChildren(
    el("h1", { textContent: job ? t("import.review") : id ? t("form.title_edit") : t("form.title_new") }),
    ...(job ? [el("div", { className: "warnings" }, ...(r.warnings ?? []).map((w) => el("p", { textContent: "⚠ " + t(`warning.${w}`) }))),
      captionBox(job)] : []),
    form,
    ...(job ? [jobActions(job, "#/import")] : []));
}

// ---- import (M3) ----

function jobActions(job, after) {
  const act = (action, label, cls) => {
    const b = el("button", { type: "button", className: cls, textContent: label });
    b.onclick = async () => {
      try {
        await api("POST", `api/imports/${job.id}/${action}`);
        if (location.hash === after) await render();
        else location.hash = after;
      } catch (e) {
        toast(errorText(e));
      }
    };
    return b;
  };
  return el("div", { className: "actions" },
    ...(job.status === "failed" || job.status === "review" ? [act("retry", t("import.retry"), "secondary")] : []),
    act("discard", t("import.discard"), "secondary"));
}

// pasted caption: the job is queued again and the AI reads the text; link and image stay
function captionBox(job) {
  const area = el("textarea", { rows: 6, maxLength: 20000, placeholder: t("import.caption_hint"), ariaLabel: t("import.caption") });
  const button = el("button", { type: "button", className: "secondary", textContent: t("import.start") });
  button.onclick = async () => {
    try {
      await api("POST", `api/imports/${job.id}/text`, { text: area.value });
      location.hash = "#/import";
    } catch (e) {
      toast(errorText(e));
    }
  };
  return el("details", {}, el("summary", { textContent: t("import.caption") }), area, button);
}

async function pageImport(app) {
  const list = el("div");
  let timer;
  const load = async () => {
    clearTimeout(timer);
    try {
      const jobs = await api("GET", "api/imports?status=queued,running,review,failed");
      list.replaceChildren(...(jobs.length ? jobs.map((j) => {
        const label = j.title || j.url || t("import.text_job");
        return card(null,
          el("div", { className: "row" },
            j.status === "review" ? el("a", { href: "#/import/" + j.id, textContent: label }) : el("span", { textContent: label }),
            el("span", { className: "badge " + (j.status === "failed" ? "fail" : ""), textContent: t(`import.status.${j.status}`) })),
          ...(j.error ? [el("p", { className: "muted", textContent: t(`error.${j.error}`) })] : []),
          ...(j.status === "failed" ? [jobActions(j, "#/import")] : []));
      }) : [el("p", { className: "muted", textContent: t("import.empty") })]));
      if (list.isConnected && jobs.some((j) => j.status === "queued" || j.status === "running")) timer = setTimeout(load, 3000);
    } catch (e) {
      toast(errorText(e));
    }
  };

  const submit = async (body, box) => {
    try {
      await api("POST", "api/imports", body);
      box.value = "";
      await load();
    } catch (e) {
      toast(errorText(e));
    }
  };
  const link = el("input", { type: "url", placeholder: t("import.link"), ariaLabel: t("import.link"), maxLength: 2048 });
  const linkBtn = el("button", { type: "button", textContent: t("import.start") });
  linkBtn.onclick = () => submit({ url: link.value }, link);
  const bulk = el("textarea", { rows: 6, placeholder: t("import.bulk_hint"), ariaLabel: t("import.bulk") });
  const bulkBtn = el("button", { type: "button", textContent: t("import.start") });
  bulkBtn.onclick = () => submit({ urls: bulk.value.split(/\r?\n/) }, bulk);
  const pasted = el("textarea", { rows: 6, maxLength: 20000, placeholder: t("import.text_hint"), ariaLabel: t("import.text") });
  const pastedBtn = el("button", { type: "button", textContent: t("import.start") });
  pastedBtn.onclick = () => submit({ text: pasted.value }, pasted);

  app.replaceChildren(
    el("h1", { textContent: t("import.title") }),
    card(null, el("div", { className: "row" }, link, linkBtn),
      el("details", {}, el("summary", { textContent: t("import.bulk") }), bulk, bulkBtn),
      el("details", {}, el("summary", { textContent: t("import.text") }), pasted, pastedBtn)),
    list);
  await load();
}

async function pageImportJob(app, id) {
  const job = await api("GET", "api/imports/" + id);
  if (job.status === "review") return pageRecipeForm(app, null, job);
  app.replaceChildren(
    el("a", { href: "#/import", textContent: t("import.back") }),
    el("h1", { textContent: t("import.title") }),
    card(null, el("p", { textContent: job.title || job.url || t("import.text_job") }),
      el("span", { className: "badge", textContent: t(`import.status.${job.status}`) }),
      ...(job.error ? [el("p", { className: "muted", textContent: t(`error.${job.error}`) })] : []),
      ...(job.status === "failed" ? [jobActions(job, "#/import")] : [])));
}

async function tagEditor() {
  const body = el("div");
  const refresh = async () => {
    const tags = await api("GET", "api/tags");
    const rows = tags.map((x) => {
      const name = el("input", { type: "text", value: x.name, maxLength: 50, ariaLabel: x.name });
      name.onchange = async () => {
        try {
          await api("PUT", "api/tags/" + x.id, { name: name.value });
          toast(t("settings.saved"));
        } catch (e) {
          toast(errorText(e));
        }
        await refresh();
      };
      const del = el("button", { type: "button", className: "secondary", textContent: "✕", ariaLabel: t("settings.delete") });
      del.onclick = async () => {
        if (!confirm(t("settings.tag_delete_confirm", { name: x.name }))) return;
        try {
          await api("DELETE", "api/tags/" + x.id);
        } catch (e) {
          toast(errorText(e));
        }
        await refresh();
      };
      return el("div", { className: "tag-row" }, name, del);
    });
    const fresh = el("input", { type: "text", maxLength: 50, placeholder: t("settings.tag_add"), ariaLabel: t("settings.tag_add") });
    const add = el("button", { type: "button", textContent: t("settings.add") });
    add.onclick = async () => {
      try {
        await api("POST", "api/tags", { name: fresh.value });
        await refresh();
      } catch (e) {
        toast(errorText(e));
      }
    };
    body.replaceChildren(...rows, el("div", { className: "tag-row" }, fresh, add));
  };
  await refresh();
  return card(t("settings.tags"), body);
}

// hash route -> [nav tab, page function]
function routePage(parts) {
  const [a, b, c] = parts;
  if (a === "rezepte") {
    if (!b) return ["rezepte", pageRecipes];
    if (b === "neu" && !c) return ["rezepte", (app) => pageRecipeForm(app, null)];
    if (/^\d+$/.test(b) && !c) return ["rezepte", (app) => pageRecipe(app, b)];
    if (/^\d+$/.test(b) && c === "bearbeiten") return ["rezepte", (app) => pageRecipeForm(app, b)];
  }
  if (a === "import") {
    if (!b) return ["import", pageImport];
    if (/^\d+$/.test(b) && !c) return ["import", (app) => pageImportJob(app, b)];
  }
  if (a === "einstellungen" && !b) return ["einstellungen", pageSettings];
  if (a === "systemcheck" && !b) return ["systemcheck", pageSystemcheck];
  return null;
}

async function render() {
  const app = document.getElementById("app");
  const parts = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  try {
    if (!me) me = await api("GET", "api/me");
    await loadLang();
    const found = routePage(parts);
    if (!found) return location.replace("#/rezepte");
    renderChrome(found[0]);
    await found[1](app);
  } catch (e) {
    app.textContent = strings["error.generic"] ? errorText(e) : "Error";
  }
}

window.addEventListener("hashchange", render);
render();
