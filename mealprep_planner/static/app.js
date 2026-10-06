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

function renderChrome(route, reviewCount = 0) {
  document.getElementById("top").textContent = "🍽️ " + t("app.title");
  const tab = (href, icon, label, active, count = 0) =>
    el("a", { href, ...(active ? { ariaCurrent: "page" } : {}) }, el("span", { textContent: icon }), label,
      ...(count ? [el("b", { className: "count", textContent: count, ariaLabel: t("nav.review_count", { count }) })] : []));
  document.getElementById("nav").replaceChildren(
    tab("#/", "🏠", t("nav.today"), route === "heute"),
    tab("#/woche", "📅", t("nav.week"), route === "woche"),
    tab("#/rezepte", "🍲", t("nav.recipes"), route === "rezepte"),
    tab("#/import", "📥", t("nav.import"), route === "import", reviewCount),
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

  const numberSetting = (key, min, max) => {
    const input = el("input", { type: "number", min, max, step: 1, value: settings[key], ariaLabel: t(`settings.${key}`) });
    input.onchange = () => save({ [key]: Number(input.value) });
    return input;
  };

  // 7 x 2 grid: which meals a new week plans by default (index = day * 2 + 0 lunch / 1 dinner)
  const pattern = [...settings.slot_pattern];
  const dayName = (day, weekday) => new Date(2024, 0, 1 + day).toLocaleDateString(document.documentElement.lang, { weekday });
  const patternGrid = el("div", { className: "pattern" }, el("span"),
    ...[0, 1, 2, 3, 4, 5, 6].map((day) => el("span", { textContent: dayName(day, "short") })),
    ...["form.lunch", "form.dinner"].flatMap((meal, m) => [
      el("span", { textContent: t(meal) }),
      ...[0, 1, 2, 3, 4, 5, 6].map((day) => {
        const box = el("input", { type: "checkbox", checked: pattern[day * 2 + m],
          ariaLabel: t("plan.slot_label", { day: dayName(day, "long"), meal: t(meal) }) });
        box.onchange = () => {
          pattern[day * 2 + m] = box.checked;
          save({ slot_pattern: pattern });
        };
        return box;
      })]));

  // pantry: one name per line, never pushed to Bring!
  const pantry = el("textarea", { rows: 8, ariaLabel: t("settings.pantry") });
  pantry.value = (await api("GET", "api/pantry")).names.join("\n");
  pantry.onchange = async () => {
    try {
      const saved = await api("PUT", "api/pantry", { names: pantry.value.split("\n").map((n) => n.trim()).filter(Boolean) });
      pantry.value = saved.names.join("\n");
      toast(t("settings.saved"));
    } catch (e) {
      toast(errorText(e));
    }
  };

  const health = await api("GET", "api/health");
  app.replaceChildren(
    el("h1", { textContent: t("settings.title") }),
    card(t("settings.language"), lang),
    card(t("settings.default_portions"), numberSetting("default_portions", 1, 12)),
    card(t("settings.slot_pattern"), patternGrid, el("p", { className: "muted", textContent: t("settings.slot_pattern_hint") })),
    card(t("settings.repeat_window_days"), numberSetting("repeat_window_days", 0, 60)),
    card(t("settings.new_per_week"), numberSetting("new_per_week", 0, 14)),
    await tagEditor(),
    card(t("settings.pantry"), pantry, el("p", { className: "muted", textContent: t("settings.pantry_hint") })),
    card(t("settings.bring"), await entityPicker("todo", "bring_entity")),
    card(t("settings.inbox"), await entityPicker("todo", "inbox_entity")),
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

const fmtScore = (n) => n.toLocaleString(document.documentElement.lang, { minimumFractionDigits: 1, maximumFractionDigits: 1 });

// household average, own stars and veto badge of a recipe card
function ratingLine(r) {
  return el("p", { className: "rating-line" },
    t("rating.household", { score: fmtScore(r.household_score) }) + " · " +
      (r.my_stars == null ? t("rating.mine_none") : t("rating.mine", { n: r.my_stars })),
    ...(r.vetoed ? [" ", el("span", { className: "badge fail", textContent: t("rating.vetoed") })] : []));
}

// 1-5 stars and "never again" (0); tapping the current value clears the rating
function starWidget(id, current, onChange) {
  const set = async (n) => {
    try {
      onChange(await api("PUT", `api/recipes/${id}/rating`, { stars: n === current ? null : n }));
    } catch (e) {
      toast(errorText(e));
    }
  };
  const star = (n) => {
    const b = el("button", { type: "button", className: "star" + (current != null && n <= current ? " on" : ""),
      textContent: "★", ariaLabel: t("rating.stars", { n }), ariaPressed: String(current === n) });
    b.onclick = () => set(n);
    return b;
  };
  const never = el("button", { type: "button", className: "never" + (current === 0 ? " on" : ""),
    textContent: t("rating.never_button"), ariaLabel: t("rating.never"), ariaPressed: String(current === 0) });
  never.onclick = () => set(0);
  return el("div", { className: "stars" }, ...[1, 2, 3, 4, 5].map(star), never);
}

function ratingCard(id, r) {
  const box = card(null);
  const show = (info) => {
    box.replaceChildren(
      el("h2", { textContent: t("rating.title") }),
      el("p", {}, t("rating.household", { score: fmtScore(info.household_score) }) + " ",
        ...(info.vetoed ? [el("span", { className: "badge fail", textContent: t("rating.vetoed") })] : [])),
      info.ratings.length
        ? el("ul", { className: "plain" }, ...info.ratings.map((x) => el("li", {
          textContent: t(x.stars === 0 ? "rating.person_never" : "rating.person", { name: x.display_name, n: x.stars }) })))
        : el("p", { className: "muted", textContent: t("rating.nobody") }),
      el("h2", { textContent: t("rating.yours") }),
      starWidget(id, info.my_stars, show),
      el("p", { className: "muted", textContent: t("rating.hint") }),
      ...(info.my_prediction != null
        ? [el("p", {}, el("strong", { textContent: t("rating.prediction", { score: fmtScore(info.my_prediction) }) }))] : []));
  };
  show(r);
  return box;
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
  const sortSel = el("select", { ariaLabel: t("recipes.sort") },
    ...["title", "score", "new"].map((k) => option(k, t(`recipes.sort.${k}`), k === "title")));
  const unrated = el("input", { type: "checkbox" });

  const load = async () => {
    const qs = new URLSearchParams({ q: search.value, tag: tagSel.value, archived: archived ? 1 : 0, sort: sortSel.value });
    if (unrated.checked) qs.set("filter", "unrated_by_me");
    try {
      const items = await api("GET", "api/recipes?" + qs);
      list.replaceChildren(...(items.length
        ? items.map((r) => el("a", { className: "card recipe-card", href: "#/rezepte/" + r.id },
          ...image(r.image),
          el("h2", { textContent: r.title }),
          el("p", { className: "muted", textContent: mealInfo(r) }),
          ratingLine(r),
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
  sortSel.onchange = load;
  unrated.onchange = load;
  archive.onchange = () => {
    archived = archive.checked;
    load();
  };

  app.replaceChildren(
    el("div", { className: "row" }, el("h1", { textContent: t("recipes.title") }),
      el("a", { className: "btn", href: "#/rezepte/neu", textContent: t("recipes.new") })),
    card(null, search, tagSel, sortSel, el("label", { className: "row" }, t("recipes.unrated_by_me"), unrated),
      el("label", { className: "row" }, t("recipes.archive"), archive)),
    list);
  await load();
}

// link to a recipe page; the portions stepper starts at `portions` (a plan slot's) instead of the recipe's servings
const recipeHref = (id, portions) => `#/rezepte/${id}?p=${portions}`;

async function pageRecipe(app, id, query = "") {
  await loadUnits();
  const r = await api("GET", "api/recipes/" + id);
  const wanted = Number(new URLSearchParams(query).get("p"));
  let portions = Number.isInteger(wanted) && wanted >= 1 && wanted <= 50 ? wanted : r.servings;

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
    ratingCard(id, r),
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

// ---- week planner (M7) ----

const DAY_MS = 86400000;

// ISO week id ("2026-W41") of a local date: the year and week number of its Thursday
function weekOf(d) {
  const thursday = new Date(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()));
  thursday.setUTCDate(thursday.getUTCDate() + 3 - ((thursday.getUTCDay() + 6) % 7));
  const n = Math.floor((thursday - Date.UTC(thursday.getUTCFullYear(), 0, 1)) / DAY_MS / 7) + 1;
  return `${thursday.getUTCFullYear()}-W${String(n).padStart(2, "0")}`;
}

// the week id n weeks later (n may be negative)
function shiftWeek(week, n) {
  const [year, number] = week.split("-W").map(Number);
  const jan4 = new Date(year, 0, 4);
  return weekOf(new Date(year, 0, 4 - ((jan4.getDay() + 6) % 7) + (number - 1 + n) * 7));
}

const parseDate = (iso) => new Date(...iso.split("-").map((v, i) => (i === 1 ? v - 1 : +v)));

function reasonText(r) {
  if (!r) return "";
  const text = t(`plan.reason.${r.kind}`, { score: r.score == null ? "" : fmtScore(r.score) });
  return r.tags?.length ? `${text} · ${r.tags.join(", ")}` : text;
}

function totalsText(total) {
  if (!total.kcal && !total.incomplete) return "";
  const approx = total.estimated ? "≈ " : "";
  const parts = [`${approx}${Math.round(total.kcal)} kcal`,
    ...[["recipe.protein", total.protein_g], ["recipe.fat", total.fat_g], ["recipe.carbs", total.carbs_g]]
      .map(([k, v]) => `${t(k)} ${approx}${Math.round(v)} g`)];
  if (total.incomplete) parts.push(t("plan.incomplete"));
  return `${t("plan.totals")}: ${parts.join(" · ")}`;
}

// search dialog: pick any recipe for a slot
function pickRecipe(onPick) {
  const dialog = el("dialog", { className: "picker" });
  const list = el("div");
  const search = el("input", { type: "search", placeholder: t("recipes.search"), ariaLabel: t("recipes.search") });
  const load = async () => {
    try {
      const items = await api("GET", "api/recipes?" + new URLSearchParams({ q: search.value, sort: "score" }));
      list.replaceChildren(...(items.length ? items.map((r) => {
        const b = el("button", { type: "button", className: "pick-card" },
          el("strong", { textContent: r.title }), el("span", { className: "muted", textContent: mealInfo(r) }), ratingLine(r));
        b.onclick = () => {
          dialog.close();
          onPick(r.id);
        };
        return b;
      }) : [el("p", { className: "muted", textContent: t("recipes.empty") })]));
    } catch (e) {
      toast(errorText(e));
    }
  };
  let timer;
  search.oninput = () => {
    clearTimeout(timer);
    timer = setTimeout(load, 250);
  };
  const close = el("button", { type: "button", className: "secondary", textContent: t("form.cancel") });
  close.onclick = () => dialog.close();
  dialog.onclose = () => dialog.remove();
  dialog.append(el("h2", { textContent: t("plan.replace_title") }), search, list, close);
  document.body.append(dialog);
  dialog.showModal();
  load();
}

// "name: note" line of a shopping/push entry (pantry entries are plain names)
const entryText = (e) => (typeof e === "string" ? e : e.note ? `${e.name}: ${e.note}` : e.name);

function pushPanel(r) {
  if (r.error) return card(t("push.title"), el("p", { textContent: t(`error.${r.error}`) }));
  const sections = [["push.added", r.added], ["push.updated", r.updated], ["push.failed", r.failed],
    ["push.no_longer_needed", r.no_longer_needed], ["push.pantry", r.skipped_pantry]].filter(([, list]) => list.length);
  const nothing = !(r.added.length || r.updated.length || r.failed.length || r.no_longer_needed.length);
  return card(t("push.title"),
    ...(nothing ? [el("p", { textContent: t("push.nothing") })] : []),
    ...sections.flatMap(([key, list]) => [el("strong", { textContent: `${t(key)} (${list.length})` }),
      el("ul", { className: "plain" }, ...list.map((e) => el("li", { textContent: entryText(e) })))]));
}

async function pageShopping(app, week) {
  const data = await api("GET", `api/plans/${week}/shopping`);
  app.replaceChildren(
    el("h1", { textContent: t("shopping.title") }),
    el("a", { href: "#/woche/" + week, textContent: t("shopping.back") }),
    card(t("plan.week_label", { week }),
      ...(data.items.length ? [el("ul", { className: "plain" }, ...data.items.map((i) => el("li", { className: "row" },
        el("span", { textContent: entryText(i) }),
        el("span", { className: "badge " + (i.status === "pushed" ? "ok" : ""), textContent: t(`shopping.status.${i.status}`) }))))]
        : [el("p", { className: "muted", textContent: t("shopping.empty") })])),
    ...(data.pantry.length ? [card(t("push.pantry"), el("p", { textContent: data.pantry.join(", ") }))] : []),
    ...(data.no_longer_needed.length ? [card(t("push.no_longer_needed"),
      el("ul", { className: "plain" }, ...data.no_longer_needed.map((e) => el("li", { textContent: entryText(e) }))))] : []));
}

async function pageWeek(app, week) {
  let plan = await api("GET", "api/plans/" + week);
  let pushResult = null; // result of the last push to Bring! (kept while this page is open)
  const root = el("div");
  const act = async (path, body) => {
    try {
      plan = await api("POST", `api/plans/${week}/${path}`, body ?? {});
      if (plan.push) pushResult = plan.push;
      show();
      return true;
    } catch (e) {
      toast(errorText(e));
      return false;
    }
  };
  const slotAct = (s, body) => act(`slots/${s.day}/${s.meal}`, body);
  const icon = (text, label, onclick) => {
    const b = el("button", { type: "button", className: "secondary icon", textContent: text, ariaLabel: label, title: label });
    b.onclick = onclick;
    return b;
  };

  const slotView = (s) => {
    const meal = el("strong", { textContent: t(s.meal === "lunch" ? "form.lunch" : "form.dinner") });
    if (!s.active) {
      return el("div", { className: "slot off" }, el("div", { className: "row" }, meal,
        el("span", { className: "muted", textContent: t("plan.off") }),
        icon("▶", t("plan.activate"), () => slotAct(s, { action: "activate" }))));
    }
    const minus = icon("−", t("plan.portions_less"), () => slotAct(s, { action: "portions", portions: s.portions - 1 }));
    const plus = icon("+", t("plan.portions_more"), () => slotAct(s, { action: "portions", portions: s.portions + 1 }));
    minus.disabled = s.portions <= 1;
    plus.disabled = s.portions >= 12;
    const past = plan.status === "confirmed" && s.date < plan.today;  // cooked: the server refuses reroll/set/clear
    const skip = el("input", { type: "checkbox", checked: s.skipped });
    skip.onchange = () => slotAct(s, { action: skip.checked ? "skip" : "unskip" });
    return el("div", { className: "slot" + (s.skipped ? " skipped" : "") },
      el("div", { className: "row" }, meal,
        el("div", { className: "stepper", role: "group", ariaLabel: t("plan.portions") }, minus, el("strong", { textContent: s.portions }), plus)),
      ...(s.recipe ? [...image(s.recipe.image), el("a", { href: "#/rezepte/" + s.recipe.id, textContent: s.recipe.title })]
        : [el("span", { className: "muted", textContent: t("plan.empty_slot") })]),
      el("p", { className: "muted", textContent: reasonText(s.reason) }),
      el("div", { className: "actions" },
        ...(s.locked || past ? [] : [icon("🎲", t("plan.reroll"), () => slotAct(s, { action: "reroll" }))]),
        ...(past ? [] : [icon("✏️", t("plan.replace"), () => pickRecipe((id) => slotAct(s, { action: "set", recipe_id: id })))]),
        ...(s.recipe ? [icon(s.locked ? "🔓" : "🔒", t(s.locked ? "plan.unlock" : "plan.lock"),
          () => slotAct(s, { action: s.locked ? "unlock" : "lock" }))] : []),
        icon("⏸", t("plan.deactivate"), () => slotAct(s, { action: "deactivate" }))),
      ...(plan.status === "confirmed" && s.date <= plan.today ? [el("label", { className: "row" }, t("plan.skipped"), skip)] : []));
  };

  const show = () => {
    const lang = document.documentElement.lang;
    const range = (iso) => parseDate(iso).toLocaleDateString(lang, { day: "numeric", month: "numeric" });
    const generate = el("button", { type: "button", textContent: t("plan.generate") });
    generate.onclick = () => act("generate");
    const confirmBtn = el("button", { type: "button", className: plan.status === "confirmed" ? "secondary" : "", textContent: t("plan.confirm") });
    confirmBtn.onclick = async () => {
      if (await act("confirm")) toast(t("plan.confirmed"));
    };
    const resend = el("button", { type: "button", className: "secondary", textContent: t("plan.resend") });
    resend.onclick = async () => {
      try {
        pushResult = await api("POST", `api/plans/${week}/push`);
        show();
      } catch (e) {
        toast(errorText(e));
      }
    };
    root.replaceChildren(
      el("div", { className: "week-nav" },
        el("a", { className: "btn secondary", href: "#/woche/" + shiftWeek(week, -1), textContent: "‹", ariaLabel: t("plan.prev") }),
        el("strong", { textContent: `${t("plan.week_label", { week })} · ${range(plan.dates[0])} – ${range(plan.dates[6])}` }),
        el("a", { className: "btn secondary", href: "#/woche/" + shiftWeek(week, 1), textContent: "›", ariaLabel: t("plan.next") })),
      el("div", { className: "row" },
        el("span", { className: "badge " + (plan.status === "confirmed" ? "ok" : ""), textContent: t(`plan.status.${plan.status}`) }),
        el("a", { href: `#/woche/${week}/einkauf`, textContent: t("plan.shopping") }),
        el("a", { href: "#/woche/" + shiftWeek(weekOf(new Date()), 1), textContent: t("plan.next_week") })),
      el("div", { className: "actions" }, generate, confirmBtn, ...(plan.status === "confirmed" ? [resend] : [])),
      ...(plan.status === "confirmed" ? [el("p", { className: "muted", textContent: t("plan.resend_hint") })] : []),
      ...(pushResult ? [pushPanel(pushResult)] : []),
      ...plan.dates.map((iso, day) => card(
        parseDate(iso).toLocaleDateString(lang, { weekday: "long", day: "numeric", month: "numeric" }),
        ...plan.slots.filter((s) => s.day === day).map(slotView),
        ...(totalsText(plan.totals[day]) ? [el("p", { className: "muted", textContent: totalsText(plan.totals[day]) })] : []))));
  };
  app.replaceChildren(el("h1", { textContent: t("plan.title") }), root);
  show();
}

// ---- today page (M9) ----

async function pageToday(app) {
  const data = await api("GET", "api/today");
  const dayText = (iso) => parseDate(iso).toLocaleDateString(document.documentElement.lang,
    { weekday: "long", day: "numeric", month: "numeric" });
  const mealRow = (meal, s) => el("div", { className: "row" },
    el("strong", { textContent: t(meal === "lunch" ? "form.lunch" : "form.dinner") }),
    s ? el("span", {}, el("a", { href: recipeHref(s.recipe_id, s.portions), textContent: s.title }), " ",
      el("span", { className: "muted", textContent: t("today.portions", { n: s.portions }) }))
      : el("span", { className: "muted", textContent: "–" }));
  const dayCard = (label, d) => card(`${t(label)} · ${dayText(d.date)}`,
    ...(d.lunch || d.dinner ? [mealRow("lunch", d.lunch), mealRow("dinner", d.dinner)]
      : [el("p", { className: "muted", textContent: t("today.nothing") })]));

  // "Wie war's?": rating a recipe removes it from the list
  let rate = data.rate;
  const rateList = el("ul", { className: "plain" });
  const showRate = () => rateList.replaceChildren(...(rate.length ? rate.map((x) => el("li", {},
    el("a", { href: "#/rezepte/" + x.recipe_id, textContent: x.title }),
    el("p", { className: "muted", textContent: `${dayText(x.date)} · ${t(x.meal === "lunch" ? "form.lunch" : "form.dinner")}` }),
    starWidget(x.recipe_id, null, () => {
      rate = rate.filter((y) => y !== x);
      showRate();
    }))) : [el("li", { className: "muted", textContent: t("today.rate_empty") })]));
  showRate();

  const link = el("input", { type: "url", placeholder: t("import.link"), ariaLabel: t("today.add_link"), maxLength: 2048 });
  const add = el("button", { type: "button", textContent: t("import.start") });
  add.onclick = async () => {
    try {
      await api("POST", "api/imports", { url: link.value });
      link.value = "";
      toast(t("today.link_added"));
    } catch (e) {
      toast(errorText(e));
    }
  };

  app.replaceChildren(
    el("h1", { textContent: t("today.title") }),
    dayCard("today.today", data.today),
    dayCard("today.tomorrow", data.tomorrow),
    card(t("today.rate"), el("p", { className: "muted", textContent: t("today.rate_hint") }), rateList),
    card(t("today.add_link"), el("div", { className: "row" }, link, add)));
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
            el("span", {},
              ...(j.origin === "inbox" ? [el("span", { className: "badge", textContent: t("import.inbox") }), " "] : []),
              el("span", { className: "badge " + (j.status === "failed" ? "fail" : ""), textContent: t(`import.status.${j.status}`) }))),
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
      ...(job.origin === "inbox" ? [el("span", { className: "badge", textContent: t("import.inbox") }), " "] : []),
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
function routePage(parts, query) {
  const [a, b, c] = parts;
  if (!a) return ["heute", pageToday];
  if (a === "woche") {
    if (!b) return ["woche", async () => location.replace("#/woche/" + weekOf(new Date()))];
    if (/^\d{4}-W\d{2}$/.test(b) && !c) return ["woche", (app) => pageWeek(app, b)];
    if (/^\d{4}-W\d{2}$/.test(b) && c === "einkauf") return ["woche", (app) => pageShopping(app, b)];
  }
  if (a === "rezepte") {
    if (!b) return ["rezepte", pageRecipes];
    if (b === "neu" && !c) return ["rezepte", (app) => pageRecipeForm(app, null)];
    if (/^\d+$/.test(b) && !c) return ["rezepte", (app) => pageRecipe(app, b, query)];
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
  const [path, query = ""] = location.hash.replace(/^#\/?/, "").split("?");
  const parts = path.split("/").filter(Boolean);
  try {
    if (!me) me = await api("GET", "api/me");
    await loadLang();
    const found = routePage(parts, query);
    if (!found) return location.replace("#/");
    const review = await api("GET", "api/imports?status=review").catch(() => []);
    renderChrome(found[0], review.length);
    await found[1](app);
  } catch (e) {
    app.textContent = strings["error.generic"] ? errorText(e) : "Error";
  }
}

window.addEventListener("hashchange", render);
render();
