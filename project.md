# MealPrep Planner

Single source of truth for scope, architecture and roadmap. Planning session 2026-10-06; later the same day the scope change "Weight Loss Journey" (M10–M14) was agreed.

## 1. Vision

MealPrep Planner is a Home Assistant App that turns the household's own recipe collection (imported from links such as Chefkoch, TikTok, YouTube or Instagram) into a weekly lunch-and-dinner plan. Every household member rates meals with 0–5 stars, and the planner uses these ratings transparently to suggest what the household likes while still mixing in new recipes. For the household's weight-loss journey it suggests each person's portion so the planned meals fit their personal calorie target, and a checklist sends the merged, scaled shopping list to Bring! (agreed 2026-10-06).

## 2. Rules for every session

- This file is the source of truth for scope. Read it first.
- Implementation is done by **Sonnet 5.5**, one milestone per session.
- **No assumptions.** If something is unclear or not covered here, ask the user. An agreed scope change goes into this file first, marked "(agreed YYYY-MM-DD)", and only then is code written.
- When a milestone's acceptance criteria are met, tick it here. Tick only what was really verified; if a part can't be verified yet (e.g. needs hardware), leave it unticked and note what is missing. Commit to local git after the user confirms.
- Keep it simple: standard library and native platform features first. Add no dependency without asking. Build no speculative abstractions, no config for values that never change, no scaffolding "for later".
- Validate everything at trust boundaries (imports, uploads, network input, AI/model output). Never trust it as is.

## 3. Decisions

All decided in the planning session on 2026-10-06. Rows marked "(agreed 2026-10-06)" come from the scope change "Weight Loss Journey"; they describe the target state, and the code follows the earlier wording until the named milestone is built.

| Topic | Decision |
|---|---|
| Platform | Home Assistant **App** (formerly "add-on") with **Ingress** on the existing HA OS. No custom integration, no LXC. |
| Hardware | Existing HA OS VM on Proxmox (amd64) only. Image built for `amd64` only. No extra hardware. |
| Name | "MealPrep Planner"; app slug `mealprep_planner`; repo `MealPrepPlanner`. |
| Users & identity | Every voter uses their own HA account. The app reads the user from the Ingress headers `X-Remote-User-Id`, `X-Remote-User-Name`, `X-Remote-User-Display-Name` and registers users on first visit. No own login. |
| Rights | All HA users are equal: add/edit/archive recipes, plan, change settings. "Delete" = archive (restorable). |
| Access | Ingress only, no exposed port. Requests are accepted only from the Ingress proxy `172.30.32.2`. |
| Meal slots | Lunch + dinner, Mon–Sun (14 slots). A default slot pattern (7×2 grid) in the settings; single slots can be switched off ("kein Kochen") per week. |
| Leftovers | One recipe per slot. A slot can be marked "Reste von …" an earlier slot of the same week: it shows the same recipe, its portions are added to the cooking slot, and it adds nothing to the shopping list (M14). No automatic spans, no batch-prep day. (agreed 2026-10-06) |
| Lunch/dinner suitability | Per-recipe flags "Mittag" / "Abend" (default: both). The planner fills a slot only with matching recipes. |
| Week | ISO week, Monday–Sunday, written `YYYY-Www`. |
| Plan flow | Manual: "Vorschlag erstellen" → reroll / lock / replace / switch off / adjust eaters → "Bestätigen". Past slots of a confirmed plan count as cooked unless marked "ausgefallen". Sending to Bring! is a separate button (M10). (agreed 2026-10-06) |
| Learning | Transparent scoring: stars, per-person tag preferences that predict unrated recipes, a repeat window and a quota of new recipes. Every suggestion shows why it was picked. No implicit signals, no machine learning. |
| Ratings | 0–5 stars. One current, editable rating per person and recipe. "Unrated" is not 0. A 0 from anyone is a veto: never suggested automatically, still plannable by hand. |
| Rating prompt | "Wie war's?" list on the app's start page. No notifications. |
| Planning rules | Repeat window (default 14 days, range 0–60) and new recipes per week (default 2, range 0–14), both in the settings. From M15 every category has its own 7×2 slot grid (e.g. Vesper = only dinners, Sonntagsessen = only Sunday); the planner puts a recipe automatically only into slots that **all** of its categories allow (hard rule, like the Mittag/Abend flags; by hand it can go anywhere). This replaces the M13 slot rules ("Mo–Fr Abend = Schnell"), which are removed (agreed 2026-10-07). Recipes whose kcal fit every eater's budget are preferred (M12). No weekly category quotas, no cooking-time limits, no hard calorie limits. (agreed 2026-10-06) |
| Portions | Cooked portions per slot = number of eaters + guests (M11), then the eaters' personal portions + guests + linked leftovers (M12, M14). Quantities are scaled from the recipe's servings. The setting "Portionen" (default 2, range 1–12) remains only as the servings fallback for imports without a yield. (agreed 2026-10-06) |
| Household & eaters | Each HA user has "Ich esse mit" (default on); these are the participants. A new week puts all participants on every active slot; in the week view a tap on a name removes or re-adds that person for this slot. "+ Portion" adds guest portions (1 portion each). Household settings (participants, targets, canteen) are visible to and editable by every user (M11, M12). (agreed 2026-10-06) |
| Calorie & protein targets | Each person can set a daily kcal target and a protein target (g). Both cover only the planned meals (lunch + dinner, including a canteen lunch); breakfast and snacks are not tracked. kcal drives personal portions and the planner; protein is display only (M12). (agreed 2026-10-06) |
| Canteen lunch | Per person and day, "Kantine" marks a lunch eaten at the canteen: it counts as that person's canteen kcal (default 700, range 0–2000) toward the target, removes them from that day's planned lunch and adds nothing to shopping. Each person can set default canteen weekdays; new weeks start from them. A planned meal a person is not part of otherwise counts as 0 kcal (M12). (agreed 2026-10-06) |
| Personal portions | Per person and day, all planned meals get the same portion factor = (kcal target − canteen kcal) ÷ sum of the meals' kcal per portion, rounded to ¼ and limited to 0.5–2. Without a target the factor is 1; if a meal of that day has no kcal, the factor is 1 and the app suggests "Nährwerte schätzen". Portions are suggestions in recipe portions, not grams (M12). (agreed 2026-10-06) |
| Web import | Own parser (stdlib) for schema.org `Recipe` JSON-LD. Otherwise OpenGraph title/image as prefill (from M4 also AI on the page text). |
| Video / social links | TikTok and YouTube via oEmbed (caption, thumbnail). Instagram: link only, caption pasted by hand. Ingredients and steps come from the text via AI. |
| AI | Via HA `ai_task.generate_data`; the entity is chosen in the settings, so the app holds no API key. Used for: free text (captions, pasted text, pages without JSON-LD), clean-up of **every** import (split ingredients, map to known names, tags, lunch/dinner flags), nutrition when the page has none (marked "geschätzt") and the "Nährwerte schätzen" button (M10); from M13 it also suggests categories. Not used for suggestions. If AI is off or fails, the rule-based result is kept. All AI output is validated. (agreed 2026-10-06) |
| Review | Every import becomes a draft that a person reviews and saves. Nothing enters the collection unreviewed. |
| Steps | Ingredients, steps and source link are stored; the recipe page doubles as cook view. |
| Images | Local copy of the recipe image / thumbnail, ≤ 2 MB, JPEG/PNG/WebP verified by magic bytes. |
| Tags & categories | Curated, editable tag list (25 defaults, §6). Import and AI pre-select from it. From M13, eight tags are categories: Schnell, Meal Prep, Sonntagsessen, Leicht, Proteinreich, Lunchbox, Ofengericht, Gäste; they are shown as chips, a recipe can have several, and any tag can be made a category in the tag editor (e.g. "Vesper"). From M15 each category carries a slot grid that limits where the planner puts its recipes (agreed 2026-10-07). Schnell (≤ 30 min), Leicht (≤ 500 kcal per portion) and Proteinreich (≥ 25 % of kcal from protein) are pre-ticked automatically, the others by AI or by hand. (agreed 2026-10-06) |
| Ingredient names | Free text with suggestions of already-used names (native `<datalist>`) in the edit form. Shopping merge = same name (case-insensitive) plus unit conversion g/kg and ml/cl/dl/l. |
| Nutrition | kcal, protein, fat, carbs per portion: from the page, else an AI estimate ("geschätzt"). A "Nährwerte schätzen" button re-estimates any recipe; page values are overwritten only after confirming (M10). Shown per recipe and as day totals in the week view; with targets, per person and day against the targets (M12). (agreed 2026-10-06) |
| Bulk import | Paste many links (one per line, max 50) → draft queue. |
| Phone sharing | Share sheet → HA companion app (`mobile_app.share` event, Android and iOS) → blueprint automation → HA Local To-do list "Rezept-Inbox" → the app polls it every 60 s → drafts. Copy/paste into the app also works. |
| Bring! push | No automatic push (M10). The button "An Bring! senden" (week view, any time after generating) opens a checklist with merged, scaled amounts; pantry items start unticked, already-sent items are shown without checkbox, items whose amount grew are pre-ticked with the difference. Ticked items are sent via HA (item = ingredient name, description = amount, e.g. "800 g"), then "Bring! öffnen" opens the Bring! app (if V9 finds a working link). (agreed 2026-10-06) |
| Bring! duplicates | If an open item with the same name already exists, our amount is appended to its description ("1 l" → "1 l + 500 ml"). |
| Bring! re-sending | The app remembers per week what it sent; the checklist pre-ticks only new items and increases. Items no longer needed are listed for manual removal in Bring!. One-way only. (agreed 2026-10-06) |
| Bring! recipe import | Recipes imported from public web pages get Bring!'s own import button (`https://api.getbring.com/rest/bringrecipes/deeplink?url=<source_url>&source=web`), as on Chefkoch: Bring!'s servers read the original page; the app itself is never exposed (M10). A whole-week import through this route would need a public page and is not done. (agreed 2026-10-06) |
| Bring! notification | None. |
| Pantry | Editable "Vorrat" name list (pre-filled, §6); matching items start unticked in the Bring! checklist (M10; before M10 they are never pushed). No stock tracking. (agreed 2026-10-06) |
| HA display | REST sensor `sensor.essensplan` (state = next meal; attributes = today, tomorrow, week), re-posted every 5 min and on every plan change. |
| Export / backup | No export/import feature. HA backups only (the app uses `backup: cold`). |
| Backend | Python 3.13, standard library only (`http.server`, `sqlite3`, `urllib`, `html.parser`, `json`, `threading`, …). |
| Frontend | Vanilla JS single-page app, no build step, no framework. DOM is built with `textContent` (never `innerHTML` with data). Hash routing, relative URLs. |
| Storage | SQLite `/data/mealprep.db`, schema version in `PRAGMA user_version`; images in `/data/images/`. |
| UI languages | German and English via i18n JSON files. Language = per-user override in the app, else browser language, fallback German. Recipe content is never translated; Bring! notes are always German. |
| Code & docs language | English. |
| Ingredient parsing | German recipes and units only (§6 unit table). |
| Tests | `unittest` (stdlib). No network access in tests. |
| Deployment | Public GitHub repo used as HA app repository; HA builds the image on the device from the Dockerfile. Version `0.<milestone>.<patch>`, bumped per milestone. Pushing to GitHub only after the user confirms. |
| Dev loop | Locally on Windows with Python 3.13 (`py -3.13`) against the real HA via URL + long-lived token in environment variables. |
| Fetching | Honest User-Agent `MealPrepPlanner/<version>`. If a site blocks it, report and ask before changing anything. SSRF-guarded (§5). |
| Performance | No explicit targets. |
| License | MIT. |
| Repo hygiene | Public repo: no personal names, HA addresses, household entity IDs, personal targets, tokens or copied third-party web pages are committed. |

## 4. Out of scope

- A native or installable mobile app / PWA; appearing directly in the phone's share sheet (sharing goes through the HA companion app).
- Reading ingredients from video, audio or images (no video download, transcription, OCR); logging in to TikTok/Instagram/YouTube; scraping beyond oEmbed.
- Export/import files, sharing recipes with other households, importing from other recipe apps.
- Automatic Bring! pushes (from M10), two-way Bring! sync, automatic removal of Bring! items, Bring! notifications; sending the whole week through Bring!'s own import screen; any public endpoint for Bring!. (agreed 2026-10-06)
- Inventory/stock tracking with amounts (only the pantry name list).
- Calculating targets from body data (BMR/TDEE), diet plans, allergen management, nutrients beyond kcal/protein/fat/carbs, nutrient databases (BLS, Open Food Facts); protein in the planner (display only); weight tracking or charts and logs of what was actually eaten (weight already lives in HA). (agreed 2026-10-06)
- Weekly category quotas, cooking-time limits, hard calorie limits in the planner. (agreed 2026-10-06)
- Implicit learning signals, machine-learning models, AI-based suggestions, AI recipe ideas outside the collection.
- Breakfast/snacks, automatic multi-day spans, batch-prep days, manual per-person portion overrides, canteen for dinner. (agreed 2026-10-06)
- Scheduled automatic plan generation; push notifications of any kind.
- Access outside Ingress (LAN port, own login), roles and permissions.
- HA calendar integration, a custom integration, HA entities other than `sensor.essensplan`.
- Translating recipe content; parsing non-German units (cups, oz).
- Architectures other than amd64, pre-built images, CI pipelines.
- Supermarket offers/prices, multiple households.

## 5. Architecture

```
 Browser / HA companion app        Home Assistant Core                     MealPrep Planner app
 (logged in to HA)                 (HA OS on Proxmox VM, amd64)            (container + /data volume)
 ------------------------------    ------------------------------------    ------------------------------
 sidebar "MealPrep Planner" -----> Ingress proxy 172.30.32.2 ------------> http.server :8099
                                   adds X-Remote-User-* headers            static SPA + JSON API
                                                                           SQLite /data/mealprep.db
 share sheet ->                                                            images /data/images/
 "Home Assistant" ---------------> event mobile_app.share
                                   -> blueprint automation
                                   -> todo "Rezept-Inbox"  <-------------- worker: poll every 60 s
                                   todo <Bring! list>      <-------------- ticked checklist items (M10)
                                     -> Bring! cloud -> Bring! apps
                                   ai_task.generate_data   <-------------- import clean-up, free text,
                                     -> LLM provider                       "Nährwerte schätzen"
                                   sensor.essensplan       <-------------- worker: every 5 min + on change
                                   (all via REST /core/api + Supervisor token)

 "In Bring! importieren" (link on a recipe page, M10)                      importer.fetch (SSRF-guarded)
   -> api.getbring.com deeplink -> Bring! servers read the                   -> recipe sites (JSON-LD)
      ORIGINAL public recipe page (e.g. Chefkoch), never the app             -> TikTok / YouTube oEmbed
                                                                             -> recipe images
```

### State ownership

| State | Owner, location | Notes |
|---|---|---|
| Recipes, ingredients, tags/categories, ratings, users (incl. participation, targets, canteen defaults), plans, slots, eaters, canteen days, settings, import jobs, pantry, pushed items | App, SQLite `/data/mealprep.db` | Included in HA backups (`backup: cold` stops the app briefly, so the DB file is consistent). |
| Recipe images | App, `/data/images/<sha256>.<ext>` | Content-addressed, never overwritten. |
| Identity and login | HA | The app trusts Ingress headers only from `172.30.32.2`. |
| Shopping list contents | Bring! (via the HA todo entity) | The app only writes and remembers what it pushed per week. |
| Shared links waiting for import | HA Local To-do "Rezept-Inbox" | The app removes an item after creating its import job. |
| AI provider and its configuration | HA `ai_task` entity | The app only calls it. |
| `sensor.essensplan` | Derived; re-posted by the app | Lost on HA restart until the next post (≤ 5 min). |
| Weight | HA (existing weight sensors) | Not used by the app (agreed 2026-10-06). |
| UI language override | App (`users.lang`) | Otherwise browser language. |
| UI state | Browser memory only | No localStorage. |

### How the parts talk

- **Browser ↔ app:** JSON over HTTP through Ingress. Only relative URLs (`api/...`, `images/...`, `app.js`), because Ingress serves the app under `/api/hassio_ingress/<token>/`. Routes are hash routes (`#/woche/2026-W41`). Every write request must be `Content-Type: application/json` (cheap CSRF protection), body ≤ 1 MB.
- **App → HA:** REST API `http://supervisor/core/api` with `SUPERVISOR_TOKEN` (needs `homeassistant_api: true`). Service responses via `?return_response`. Module `ha.py` is the only place that talks to HA.
- **HA → app:** only Ingress requests. No callbacks; the inbox is polled.
- **App → internet:** only through `importer.fetch` (SSRF guard and limits, see below).
- **Phone → Bring! (per-recipe import, M10):** a plain link to Bring!'s deeplink service carrying the recipe's public `source_url`; Bring!'s servers fetch that original page. The app is neither contacted nor exposed.
- **Background work:** one worker thread (`worker.py`) processes import jobs one at a time, polls the inbox (M5) and posts the sensor (M9). HTTP handlers never wait for page fetches or import AI; only "Nährwerte schätzen" (M10) waits for its AI answer (≤ 60 s).

### Runtime modes

| | prod (HA app) | dev (Windows PC) |
|---|---|---|
| Detected by | `SUPERVISOR_TOKEN` is set | `SUPERVISOR_TOKEN` not set |
| Listens on | `0.0.0.0:8099`, only `172.30.32.2` allowed | `127.0.0.1:8099` |
| Current user | Ingress headers (required, else 403) | `MEALPREP_DEV_USER` (default `Dev`, id `dev`) |
| HA API | `http://supervisor/core/api` + `SUPERVISOR_TOKEN` | `$MEALPREP_HA_URL/api` + `MEALPREP_HA_TOKEN` (long-lived token) |
| Data dir | `/data` | `./data` relative to `mealprep_planner/` (gitignored) |
| Time zone | Container `TZ` set by Supervisor; Alpine package `tzdata` installed | PC local time |

"Today" is always `datetime.now()` local time; dates are stored as ISO strings in local time.

### Trust boundaries

- **Ingress requests:** source IP must be `172.30.32.2` (prod); `X-Remote-User-Id` must match `^[A-Za-z0-9_-]{1,64}$`, names ≤ 100 chars. Missing or invalid → 403 `forbidden`.
- **API input:** JSON only, ≤ 1 MB; every field validated (`recipes.validate_draft`, settings and household validators); errors → 400 `invalid_field` with the field name. Week IDs match `^\d{4}-W\d{2}$` and must exist.
- **Fetching (`importer.check_url` + `fetch`):** only `http`/`https`; no userinfo; port 80/443 only; host must resolve exclusively to global IPs (`ipaddress.ip_address(a).is_global`), checked again on every redirect (max 5); timeout 15 s; HTML ≤ 3 MB, images ≤ 2 MB, oEmbed JSON ≤ 256 KB; no cookies. Residual DNS-rebinding risk is accepted (only authenticated household users submit URLs).
- **Images:** accepted only if magic bytes are JPEG (`FF D8 FF`), PNG (`89 50 4E 47`) or WebP (`RIFF....WEBP`); stored as `<sha256>.<ext>`; served only for names matching `^[0-9a-f]{64}\.(jpg|png|webp)$`.
- **Recipe pages, captions, AI output, HA responses:** untrusted data. Parsed defensively; AI output goes through `ai.validate_ai_output` and then `validate_draft`; a person reviews every draft. AI has no tools/actions; its text is never rendered as HTML.
- **Frontend:** data only via `textContent`/attributes; external links (source pages, Bring! deeplinks) only `http(s)`, URL-encoded, with `rel="noopener noreferrer"`. Response headers: `Content-Security-Policy: default-src 'self'; img-src 'self' data:; frame-ancestors 'self'`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`.
- **Secrets:** the HA token exists only in environment variables; nothing personal in the repo.

### Verified external behaviour

Checked in the planning session (2026-10-06): HA 2026.9 on HA OS 18.1 (KVM, amd64) with Supervisor; services `todo.add_item` (item, description), `todo.update_item` (item, rename, status, description), `todo.remove_item`, `todo.get_items` exist and the Bring! list supports create/update/delete/description; `ai_task.generate_data` takes `task_name`, `instructions`, `entity_id`, `structure`, `attachments`; `calendar` has no delete service; both companion apps fire `mobile_app.share` (Android: `url`, `text`, `subject`, `caller`; iOS: `url`, `text`, `entered`); the REST API supports `?return_response`; Ingress sends `X-Remote-User-Id/-Name/-Display-Name`. Bring!'s recipe import is pull-based: Bring!'s servers fetch the given URL, so it only works for public pages (Mealie documents the same and uses `https://api.getbring.com/rest/bringrecipes/deeplink?url=<url>&source=web`).

To verify on the device (V1–V7 in M1, V8–V9 in M10; fill in the result, then later milestones follow it):

| # | Question | Result |
|---|---|---|
| V1 | Ingress user headers present; format of the user id | open |
| V2 | App local time equals HA local time (TZ + tzdata) | open |
| V3 | `todo.add_item` on the Bring! list with a name that is already open: duplicate, overwrite or error? | Duplicate: a second open item with the same name and its own `uid` (checked via HA MCP, 2026-10-06). |
| V4 | `todo.update_item` changes the Bring! description? Any length limit observed? | Yes, addressed by `uid` (a name is ambiguous when duplicates exist). Bring! truncates the description: a 211-char note came back as its first 191 chars (checked via HA MCP, 2026-10-06). |
| V5 | Shape of `todo.get_items` response via REST `?return_response` | `{"service_response": {"<entity_id>": {"items": [{"summary", "uid", "status", "description"}]}}}`; `description` is `""` when empty (checked via HA MCP, 2026-10-06). |
| V6 | `ai_task.generate_data` via REST: variant A (`structure` with a multi-value text field) and/or variant B (JSON demanded in `instructions`) works? Typical duration? | Both work, ~3 s each. A: `service_response.data` is a dict. B: `service_response.data` is a string wrapped in a ```` ```json ```` fence. M4 uses **B** (nested draft shape; fence stripped before `json.loads`) (agreed 2026-10-06; checked via HA MCP). |
| V7 | `POST /api/states/sensor.essensplan` from the app works | open |
| V8 | The Bring! import link with a public Chefkoch URL opens Bring!'s import screen from the HA companion app (Android, iOS)? Do `baseQuantity` / `requestedQuantity` scale the amounts? | open |
| V9 | Which link reliably opens the Bring! app from the HA companion app (Android, iOS)? If none, there is no "Bring! öffnen" button. | open |

## 6. Data format

No files are shared or exported. Persistent data lives in SQLite; the schema is versioned with `PRAGMA user_version` (= number of applied migrations). Migrations are an append-only list of SQL scripts in `db.py`; a shipped migration is never edited. Drafts stored as JSON carry `"format_version": 1`.

### SQLite schema

Connection settings: `PRAGMA foreign_keys = ON; PRAGMA journal_mode = WAL; PRAGMA busy_timeout = 5000;` One connection per request/thread. Timestamps are ISO 8601 local time strings. Columns added with `ALTER TABLE` (from migration 7) carry no `CHECK`; their ranges are validated in code.

```sql
-- Migration 1 (M1)
CREATE TABLE settings (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL                       -- JSON value
);
CREATE TABLE users (
  id           TEXT PRIMARY KEY,            -- HA user id from X-Remote-User-Id; 'dev' in dev mode
  name         TEXT NOT NULL,
  display_name TEXT NOT NULL,
  lang         TEXT CHECK (lang IN ('de', 'en')),   -- NULL = browser language
  first_seen   TEXT NOT NULL,
  last_seen    TEXT NOT NULL
);

-- Migration 2 (M2)
CREATE TABLE tags (
  id   INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE COLLATE NOCASE
);
CREATE TABLE recipes (
  id               INTEGER PRIMARY KEY,
  title            TEXT NOT NULL,
  source_url       TEXT,
  source_kind      TEXT NOT NULL CHECK (source_kind IN ('manual','web','tiktok','youtube','instagram','text')),
  image            TEXT,                    -- file name in <data>/images
  servings         INTEGER NOT NULL CHECK (servings BETWEEN 1 AND 50),
  total_minutes    INTEGER CHECK (total_minutes BETWEEN 1 AND 1440),
  for_lunch        INTEGER NOT NULL DEFAULT 1,
  for_dinner       INTEGER NOT NULL DEFAULT 1,
  steps            TEXT NOT NULL DEFAULT '[]',   -- JSON array of strings
  kcal REAL, protein_g REAL, fat_g REAL, carbs_g REAL,   -- per portion
  nutrition_source TEXT CHECK (nutrition_source IN ('page','ai','manual')),
  archived         INTEGER NOT NULL DEFAULT 0,
  created_by       TEXT REFERENCES users(id),
  created_at       TEXT NOT NULL,
  updated_at       TEXT NOT NULL,
  CHECK (for_lunch OR for_dinner)
);
CREATE TABLE ingredients (
  recipe_id INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
  pos       INTEGER NOT NULL,
  amount    REAL,                           -- NULL = no amount ("Salz")
  unit      TEXT,                           -- canonical unit (unit table) or NULL
  name      TEXT NOT NULL,
  note      TEXT,
  PRIMARY KEY (recipe_id, pos)
);
CREATE TABLE recipe_tags (
  recipe_id INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
  tag_id    INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
  PRIMARY KEY (recipe_id, tag_id)
);
-- + INSERT of the default tags

-- Migration 3 (M3)
CREATE TABLE import_jobs (
  id         INTEGER PRIMARY KEY,
  url        TEXT,
  text       TEXT,
  origin     TEXT NOT NULL CHECK (origin IN ('single','bulk','inbox')),
  status     TEXT NOT NULL CHECK (status IN ('queued','running','review','failed','done','discarded')),
  error      TEXT,                          -- error code, e.g. 'fetch_blocked'
  draft      TEXT,                          -- JSON draft (format_version 1)
  recipe_id  INTEGER REFERENCES recipes(id),   -- set when saved
  created_by TEXT REFERENCES users(id),        -- NULL for inbox jobs
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  CHECK (url IS NOT NULL OR text IS NOT NULL)
);

-- Migration 4 (M6)
CREATE TABLE ratings (
  user_id    TEXT NOT NULL REFERENCES users(id),
  recipe_id  INTEGER NOT NULL REFERENCES recipes(id),
  stars      INTEGER NOT NULL CHECK (stars BETWEEN 0 AND 5),
  updated_at TEXT NOT NULL,
  PRIMARY KEY (user_id, recipe_id)
);

-- Migration 5 (M7)
CREATE TABLE plans (
  week         TEXT PRIMARY KEY,            -- 'YYYY-Www'
  status       TEXT NOT NULL CHECK (status IN ('draft','confirmed')),
  confirmed_at TEXT
);
CREATE TABLE plan_slots (
  week      TEXT NOT NULL REFERENCES plans(week),
  day       INTEGER NOT NULL CHECK (day BETWEEN 0 AND 6),   -- 0 = Monday
  meal      TEXT NOT NULL CHECK (meal IN ('lunch','dinner')),
  active    INTEGER NOT NULL,               -- 0 = "kein Kochen"
  recipe_id INTEGER REFERENCES recipes(id),
  portions  INTEGER NOT NULL CHECK (portions BETWEEN 1 AND 12),
  locked    INTEGER NOT NULL DEFAULT 0,
  skipped   INTEGER NOT NULL DEFAULT 0,     -- "ausgefallen" (confirmed plans, date <= today)
  reason    TEXT,                           -- JSON, see "Suggestion reason"
  PRIMARY KEY (week, day, meal)
);

-- Migration 6 (M8)
CREATE TABLE pantry (
  name TEXT PRIMARY KEY COLLATE NOCASE
);
-- + INSERT of the default pantry names
CREATE TABLE pushed_items (
  week      TEXT NOT NULL,
  name      TEXT NOT NULL COLLATE NOCASE,
  unit_key  TEXT NOT NULL,                  -- 'g' | 'ml' | other canonical unit | '' (no unit)
  amount    REAL,                           -- cumulative amount pushed in unit_key; NULL = no amount
  pushed_at TEXT NOT NULL,
  PRIMARY KEY (week, name, unit_key)
);
-- Migration 7 (M11)
ALTER TABLE users ADD COLUMN eats INTEGER NOT NULL DEFAULT 1;           -- "Ich esse mit"
ALTER TABLE plan_slots ADD COLUMN guests INTEGER NOT NULL DEFAULT 0;    -- guest portions, 0–12
CREATE TABLE slot_eaters (                  -- row = person eats this slot
  week    TEXT NOT NULL,
  day     INTEGER NOT NULL,
  meal    TEXT NOT NULL,
  user_id TEXT NOT NULL REFERENCES users(id),
  PRIMARY KEY (week, day, meal, user_id),
  FOREIGN KEY (week, day, meal) REFERENCES plan_slots(week, day, meal) ON DELETE CASCADE
);
INSERT INTO slot_eaters (week, day, meal, user_id)   -- existing plans are test data (agreed 2026-10-06)
  SELECT s.week, s.day, s.meal, u.id FROM plan_slots s CROSS JOIN users u WHERE s.active = 1;
ALTER TABLE plan_slots DROP COLUMN portions;

-- Migration 8 (M12)
ALTER TABLE users ADD COLUMN kcal_target INTEGER;                       -- 300–5000, NULL = none
ALTER TABLE users ADD COLUMN protein_target_g INTEGER;                  -- 10–400, NULL = none
ALTER TABLE users ADD COLUMN canteen_kcal INTEGER NOT NULL DEFAULT 700; -- 0–2000
ALTER TABLE users ADD COLUMN canteen_days TEXT NOT NULL DEFAULT '[]';   -- JSON weekdays 0–6
CREATE TABLE plan_canteen (                 -- row = person eats at the canteen that day
  week    TEXT NOT NULL REFERENCES plans(week),
  day     INTEGER NOT NULL CHECK (day BETWEEN 0 AND 6),
  user_id TEXT NOT NULL REFERENCES users(id),
  PRIMARY KEY (week, day, user_id)
);

-- Migration 9 (M13)
ALTER TABLE tags ADD COLUMN category INTEGER NOT NULL DEFAULT 0;        -- 1 = planning category (chip, slot rule)
INSERT OR IGNORE INTO tags (name) VALUES
  ('Meal Prep'), ('Sonntagsessen'), ('Proteinreich'), ('Lunchbox'), ('Ofengericht'), ('Gäste');
UPDATE tags SET category = 1 WHERE name IN
  ('Schnell', 'Meal Prep', 'Sonntagsessen', 'Leicht', 'Proteinreich', 'Lunchbox', 'Ofengericht', 'Gäste');
ALTER TABLE plan_slots ADD COLUMN rule_tag_id INTEGER REFERENCES tags(id) ON DELETE SET NULL;   -- category rule

-- Migration 10 (M14)
ALTER TABLE plan_slots ADD COLUMN leftover_day INTEGER;                 -- "Reste von": source slot
ALTER TABLE plan_slots ADD COLUMN leftover_meal TEXT;

-- Migration 11 (M15, agreed 2026-10-07)
ALTER TABLE tags ADD COLUMN slots TEXT;    -- JSON 14 × bool (index = day × 2 + (0 lunch, 1 dinner)); NULL = all slots
-- plan_slots.rule_tag_id removed (DROP COLUMN, or table rebuild if SQLite refuses because of its REFERENCES);
DELETE FROM settings WHERE key = 'slot_rules';
```

### Recipe draft (format_version 1)

Used for import drafts (`import_jobs.draft`), the AI result after validation, and the recipe create/update payload. `GET api/recipes/<id>` returns the same shape plus `id`, `archived` and (M6) rating fields; `image_url` and `warnings` exist only in drafts.

```json
{
  "format_version": 1,
  "title": "Spaghetti Carbonara",
  "source_url": "https://www.example.com/rezepte/spaghetti-carbonara",
  "source_kind": "web",
  "image_url": "https://www.example.com/img/carbonara.jpg",
  "image": "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08.jpg",
  "servings": 4,
  "total_minutes": 30,
  "for_lunch": true,
  "for_dinner": true,
  "tags": ["Nudeln", "Italienisch", "Schnell"],
  "ingredients": [
    {"amount": 400, "unit": "g", "name": "Spaghetti", "note": null},
    {"amount": 4, "unit": null, "name": "Eier", "note": "Größe M"},
    {"amount": null, "unit": null, "name": "Salz", "note": null}
  ],
  "steps": ["Spaghetti in Salzwasser bissfest garen.", "Eier mit Parmesan verquirlen."],
  "nutrition": {"kcal": 650, "protein_g": 28, "fat_g": 25, "carbs_g": 75, "source": "page"},
  "warnings": []
}
```

Validation (`recipes.validate_draft`), same for API input, imports and AI results:

| Field | Rule |
|---|---|
| `format_version` | must be `1` |
| `title` | string, 1–200 chars after trimming |
| `source_url` | `null` or `http(s)` URL ≤ 2048 chars |
| `source_kind` | `manual`, `web`, `tiktok`, `youtube`, `instagram`, `text` |
| `image_url` | draft only: `null` or `http(s)` URL ≤ 2048 chars |
| `image` | `null` or `^[0-9a-f]{64}\.(jpg\|png\|webp)$` and the file exists |
| `servings` | integer 1–50 |
| `total_minutes` | `null` or integer 1–1440 |
| `for_lunch`, `for_dinner` | booleans, at least one `true` |
| `tags` | ≤ 15 names, each an existing tag (case-insensitive) |
| `ingredients` | ≤ 100 items; `amount` `null` or 0 < x ≤ 100000; `unit` `null` or a canonical unit; `name` 1–100 chars; `note` `null` or ≤ 200 chars |
| `steps` | ≤ 50 strings, each 1–2000 chars |
| `nutrition` | `null` or object; `kcal` 0–5000, `protein_g`/`fat_g`/`carbs_g` 0–500, each may be `null`; `source` `page`/`ai`/`manual` |
| `warnings` | draft only: list of codes `no_recipe_data`, `already_imported`, `ai_failed`, `ai_disabled`, `paste_caption`, `image_failed` |

Unknown keys are dropped. Errors are returned per field.

### JSON-LD mapping (M3)

| Draft field | schema.org `Recipe` source |
|---|---|
| `title` | `name` |
| `servings` | `recipeYield`: first integer in string / number / list; else setting `default_portions` |
| `total_minutes` | `totalTime`, else `prepTime` + `cookTime` (ISO 8601 durations like `PT1H30M`, `P0DT2H`) |
| `ingredients` | `recipeIngredient[]` → `ingredients.parse_line` |
| `steps` | `recipeInstructions`: string (split by lines) / `[string]` / `[HowToStep.text]` / `[HowToSection.itemListElement[].text]` |
| `image_url` | `image`: string / `[string]` / `ImageObject.url` / `[ImageObject]`, first entry |
| `nutrition` | `nutrition.calories` ("520 kcal"), `proteinContent`, `fatContent`, `carbohydrateContent` ("25 g"), `source: "page"` |
| `tags` | `keywords` (comma string or list), `recipeCategory`, `recipeCuisine`, matched case-insensitively to existing tag names |

The `Recipe` object may sit at top level, in a list, in `@graph`, or have `@type` as a list. Strings are `html.unescape`d and stripped of tags.

### Units (German)

Canonical units as stored, with accepted aliases (case-insensitive, trailing dot optional):

| Group | Canonical (plural) ← aliases | Shopping list |
|---|---|---|
| Mass, base `g` | `g` ← gr, gramm; `kg` (×1000) ← kilo, kilogramm | summed in g; shown as kg from 1000 g |
| Volume, base `ml` | `ml` ← milliliter; `cl` (×10); `dl` (×100); `l` (×1000) ← liter, ltr | summed in ml; shown as l from 1000 ml |
| Spoons & pinches | `EL` ← essl, esslöffel, eßlöffel; `TL` ← teel, teelöffel; `Msp.` ← msp, messerspitze(n); `Prise (Prisen)`; `Spritzer`; `Schuss`; `Handvoll`; `Tasse (Tassen)` | summed per unit, 1 decimal |
| Countable | `Stück` ← stk, st; `Dose (Dosen)` ← ds; `Glas (Gläser)`; `Packung (Packungen)` ← pck, pkg, pkt, päckchen, pack; `Becher`; `Bund` ← bd; `Zehe (Zehen)`; `Scheibe (Scheiben)`; `Würfel`; `Zweig (Zweige)`; `Stange (Stangen)`; `Kopf (Köpfe)`; `Knolle (Knollen)`; `Flasche (Flaschen)`; `Blatt (Blätter)` | summed per unit, rounded **up** to whole numbers |
| No unit | `null` (e.g. "4 Eier") | summed, rounded up |

Number format (UI de, all Bring! notes): decimal comma, at most 2 decimals, trailing zeros removed ("1,5 kg", "800 g", "2 Dosen"); plural form when the amount is > 1.

Parser rules (`ingredients.parse_line`), in this order:
1. Trim; strip bullets (`-`, `•`, `*`) and numbering (`1.`); collapse whitespace.
2. Leading amount: mixed number `1 1/2`, fraction `1/2`, unicode fractions (`½ ¼ ¾ ⅓ ⅔ ⅛`, also `1½`), decimal with comma or dot, integer; ranges `2-3`, `2–3`, `2 bis 3` use the **upper** value.
3. Markers `n. B.`, `nach Belieben`, `etwas`, `evtl.`, `ggf.`, `wenig`, `einige` at the start → amount `null`, marker kept as `note`.
4. Unit: the next token (or a unit glued to the number, `200g`, `1l`) matched against the aliases.
5. Name = the rest; text after the first comma and text in parentheses → `note` (joined with `; `).
6. Never raise, never lose text: if no name remains, the whole line becomes the name with amount/unit `null`.

| Input | amount | unit | name | note |
|---|---|---|---|---|
| `500 g Mehl` | 500 | g | Mehl | – |
| `1 Dose Tomaten, gehackt` | 1 | Dose | Tomaten | gehackt |
| `2-3 Zehen Knoblauch` | 3 | Zehe | Knoblauch | – |
| `½ TL Salz` | 0.5 | TL | Salz | – |
| `1 ½ kg Kartoffeln (festkochend)` | 1.5 | kg | Kartoffeln | festkochend |
| `200g Speck` | 200 | g | Speck | – |
| `4 Eier` | 4 | – | Eier | – |
| `n. B. Pfeffer` | – | – | Pfeffer | n. B. |
| `1 Pck. Backpulver` | 1 | Packung | Backpulver | – |
| `Salz` | – | – | Salz | – |

### Settings (`settings` table, JSON values)

| Key | Type | Default | Validation | From |
|---|---|---|---|---|
| `bring_entity` | string | `null` | existing `todo.*` entity | M1 |
| `ai_enabled` | bool | `true` | – | M1 |
| `ai_entity` | string | `null` | existing `ai_task.*` entity | M1 |
| `default_portions` | int | 2 | 1–12; servings fallback for imports without a yield (until M11 also the default slot portions) | M2 |
| `inbox_entity` | string | `null` | existing `todo.*` entity, ≠ `bring_entity` | M5 |
| `slot_pattern` | 14 × bool | all `true` | index = day × 2 + (0 lunch, 1 dinner) | M7 |
| `repeat_window_days` | int | 14 | 0–60 | M7 |
| `new_per_week` | int | 2 | 0–14 | M7 |
| `slot_rules` | 14 × tag_id \| `null` | all `null` | each a category tag (index as `slot_pattern`); unknown ids read as `null`. **Removed in M15** (migration 11 deletes the stored value) | M13 |

Defaults live in code; the table stores only changed values.

Per-person values live in the `users` table:

| Column | Default | Validation | From |
|---|---|---|---|
| `eats` | 1 | 0/1 ("Ich esse mit") | M11 |
| `kcal_target` | `null` | `null` or 300–5000 (kcal for the planned meals of a day) | M12 |
| `protein_target_g` | `null` | `null` or 10–400 | M12 |
| `canteen_kcal` | 700 | 0–2000 | M12 |
| `canteen_days` | `[]` | JSON list of distinct weekdays 0–6 | M12 |

### Default lists

- **Tags (25):** Fleisch, Geflügel, Fisch, Vegetarisch, Vegan, Nudeln, Reis, Kartoffeln, Hülsenfrüchte, Suppe, Eintopf, Salat, Auflauf, Pfanne, Bowl, Deutsch, Italienisch, Asiatisch, Mexikanisch, Orientalisch, Indisch, Schnell, Leicht, Low Carb, Deftig.
- **Categories (M13, `category = 1`):** Schnell, Meal Prep (vorkochbar, 2–3 Tage haltbar), Sonntagsessen (aufwendiger, Genuss), Leicht, Proteinreich, Lunchbox (kalt/transportabel), Ofengericht (wenig Aufwand), Gäste; migration 9 adds the six that are not default tags yet.
- **Automatic categories (M13)** (constants in code, applied as pre-ticks to drafts and nutrition guesses, never silently on save): Schnell if `total_minutes` ≤ 30; Leicht if `kcal` ≤ 500; Proteinreich if `protein_g` × 4 ≥ 0.25 × `kcal`.
- **Pantry (11):** Salz, Pfeffer, Zucker, Mehl, Wasser, Öl, Olivenöl, Rapsöl, Sonnenblumenöl, Essig, Paprikapulver.

### Suggestion reason (`plan_slots.reason`, M7; extended in M12/M13)

```json
{"kind": "predicted", "score": 3.9, "tags": ["Nudeln", "Italienisch"]}
```

`kind`: `rated` (at least one rating), `predicted` (no rating, cooked before), `new` (no rating, never cooked), `manual` (set by hand), `none` (no candidate). `score` = household score (1 decimal). `tags` = up to 3 recipe tags with the highest affinity for the household. M13 adds `rule` (the slot's category or `null`) and `rule_met` (`false` when the planner had to fall back to any recipe), both removed again in M15; M12 adds `fit`: `ok`, `poor` (some eater would need a factor outside 0.75–1.5) or `unknown` (recipe without kcal). The frontend turns this into text via i18n.

### Sensor payload (M9)

`POST api/states/sensor.essensplan`:

```json
{
  "state": "Spaghetti Carbonara",
  "attributes": {
    "friendly_name": "Essensplan",
    "icon": "mdi:silverware-fork-knife",
    "today_lunch": null,
    "today_dinner": "Spaghetti Carbonara",
    "tomorrow_lunch": "Linsensuppe",
    "tomorrow_dinner": null,
    "week": [
      {"date": "2026-10-05", "meal": "dinner", "title": "Gemüsecurry"},
      {"date": "2026-10-06", "meal": "dinner", "title": "Spaghetti Carbonara"}
    ]
  }
}
```

### API errors and i18n files

Errors: HTTP status + `{"error": "<code>", "field": "<optional>", "detail": "<optional>"}`. Codes: `bad_request`, `forbidden`, `not_found`, `unsupported_media_type`, `too_large`, `invalid_field`, `ha_unavailable`, `ha_error`, `fetch_blocked`, `fetch_failed`, `fetch_too_large`, `no_recipe_data`, `ai_failed`, `ai_invalid`, `bring_failed`. The frontend shows `t("error.<code>")`.

`static/i18n/de.json` and `en.json` are flat key → string maps with `{name}` placeholders; both always have the same keys:

```json
{"nav.today": "Heute", "plan.reason.predicted": "Prognose {score} ★ · {tags}", "error.fetch_blocked": "Dieser Link ist nicht erlaubt."}
```

## 7. Pages / UI / CLI

Mobile-first, works in the HA companion app and desktop browsers. Navigation bar: **Heute · Woche · Rezepte · Import · Einstellungen**.

| Route | Page | What it offers | From |
|---|---|---|---|
| `#/` | Start / Heute | M0: status (version, greeting). M2–M8: redirects to `#/rezepte`. M9: today's and tomorrow's lunch/dinner (tap → recipe with the slot's portions), "Wie war's?" list with star widget, quick "Link hinzufügen" field. M12: "deine Portion" (or "Kantine") per meal. | M0, M9, M12 |
| `#/woche/<YYYY-Www>` | Woche | Week picker (prev/next, "Nächste Woche"), per day lunch + dinner: image, title, reason text, actions 🎲 reroll, 🔒 lock, ✏️ replace (search dialog), portions −/+, ⏸ switch off; day totals (≈ kcal); "Vorschlag erstellen", "Bestätigen"; after confirm: "ausgefallen" toggle on past slots. M8: push result panel ("gesendet / aktualisiert / Vorrat / nicht mehr benötigt / fehlgeschlagen"), "Erneut senden". M10: "Bestätigen" only confirms; "🛒 An Bring! senden" opens the checklist (push panel and "Erneut senden" move there). M11: eater chips (tap = remove/re-add) and "+ Portion" for guests replace the portions stepper; cooked portions shown. M12: portion per eater ("1¼"), "Kantine" toggle per person and day, per-person day totals of kcal (incl. canteen) and protein against the targets (green within ±10 %, amber outside), fit hint, "Nährwerte schätzen" hint on days without kcal. M13: category rule per slot, "Kategorie nicht erfüllt". M14: "Reste von …" select. | M7, M8, M10–M14 |
| `#/woche/<YYYY-Www>/einkauf` | Einkauf | Computed shopping list with status per item. Read-only. M10: checklist — checkbox per item with status (neu / mehr: +Differenz / Vorrat, unticked), already-sent items without checkbox; "Ausgewählte senden (N)"; result panel (gesendet / aktualisiert / fehlgeschlagen) with "Erneut senden" and "Bring! öffnen" (if V9 found a link); items no longer needed. | M8, M10 |
| `#/rezepte` | Rezepte | Search, tag filter, "Archiv" toggle, cards (image, title, tags, time). M6: household Ø, own stars, sort by score / title / newest, filter "von mir unbewertet". M13: category chips and category filter. | M2, M3, M6, M13 |
| `#/rezepte/<id>` | Rezept (cook view) | Image, portions stepper (scales ingredients), ingredients, steps, nutrition (+ "geschätzt" badge), source link, edit, archive/restore. M6: stars per person, own star widget (1–5 + "🚫 0 Nie wieder", tap again to clear), "Prognose für dich". M10: "In Bring! importieren" for recipes from public web pages. M13: category chips. | M2, M6, M10, M13 |
| `#/rezepte/neu`, `#/rezepte/<id>/bearbeiten` | Edit form | Title, servings, time, Mittag/Abend, tag checkboxes, ingredient rows (amount, unit select, name with `<datalist>`, note; add/remove), "Zutaten einfügen" textarea → parsed rows, steps (add/remove), nutrition, source URL. M10: "Nährwerte schätzen" (asks before overwriting page values). M13: category chips. | M2, M10, M13 |
| `#/import` | Import | Link field, "Mehrere Links" textarea (≤ 50), job list (wartet / läuft / prüfen / fehlgeschlagen) refreshed every 3 s while jobs run. M4: "Rezepttext einfügen" field. M5: inbox badge on jobs; nav badge with number of drafts to review. | M3–M5 |
| `#/import/<job-id>` | Draft review | Edit form prefilled from the draft, warnings on top, image preview; Speichern / Verwerfen / Erneut versuchen. M4: "Caption einfügen" re-runs AI on pasted text. M10: "Nährwerte schätzen". | M3, M4, M10 |
| `#/einstellungen` | Einstellungen | M1: language (Automatisch/Deutsch/English), Bring! list, AI on/off + AI entity, link to Systemcheck. M2: default portions, tag editor. M5: inbox list. M7: 7×2 slot pattern grid, repeat window, new per week. M8: pantry editor. M11: "Haushalt" ("Ich esse mit" per user). M12: per user kcal target, protein target, canteen kcal, canteen weekdays. M13: category per cell of the slot pattern grid, "Kategorie" toggle in the tag editor. | M1–M8, M11–M13 |
| `#/systemcheck` | Systemcheck | Buttons for checks `ha`, `bring` (writes a test item to Bring!), `ai`, `sensor`; results shown as text. M10: Bring! link tests (V8/V9). | M1, M10 |

### JSON API (all paths relative, prefix `api/`)

| Method & path | Purpose | From |
|---|---|---|
| `GET api/health` | `{"ok": true, "version": "…"}` | M0 |
| `GET api/me` / `PUT api/me` | current user `{id, name, display_name}` (+ `lang` from M1) / set `{lang: "de"\|"en"\|null}` | M0 / M1 |
| `GET api/settings` / `PUT api/settings` | read / partial update with validation | M1 |
| `GET api/ha/entities?domain=todo\|ai_task` | `[{entity_id, friendly_name}]` for pickers | M1 |
| `POST api/system/check` | `{check: "ha"\|"bring"\|"ai"\|"sensor"}` → `{ok, details}` | M1 |
| `GET api/recipes` | list; `q`, `tag`, `archived=0\|1`; M6: `sort=score\|title\|new`, `filter=unrated_by_me` | M2 |
| `POST api/recipes`, `GET/PUT api/recipes/<id>` | create / read / update | M2 |
| `POST api/recipes/<id>/archive`, `…/restore` | archive / restore | M2 |
| `GET/POST api/tags`, `PUT/DELETE api/tags/<id>` | tag editor (delete removes the tag from recipes); M13: `category` flag | M2, M13 |
| `GET api/ingredient-names` | known names, sorted case-insensitively | M2 |
| `GET api/units` | canonical units with plural forms `[{unit, plural}]` for the edit form and amount display (added in M2) | M2 |
| `POST api/parse-ingredients` | `{text}` → parsed ingredient list | M2 |
| `GET images/<sha256>.<ext>` | stored image, `Cache-Control: max-age=31536000, immutable` | M3 |
| `POST api/imports` | `{url}` or `{urls: […]}` (M3), `{text}` ≤ 20000 chars (M4) → job ids | M3 |
| `GET api/imports?status=`, `GET api/imports/<id>` | job list (newest first, ≤ 100) / job with draft | M3 |
| `POST api/imports/<id>/save`, `…/discard`, `…/retry` | save draft as recipe / discard / requeue | M3 |
| `POST api/imports/<id>/text` | `{text}` → re-run AI on a pasted caption, keeping link and image | M4 |
| `POST api/nutrition/estimate` | `{draft}` (title, servings, ≥ 1 ingredient) → `{nutrition}` (M13: + `suggested_tags`) via AI; nothing is saved; AI off or failed → 502 `ai_failed` | M10 |
| `PUT api/recipes/<id>/rating` | `{stars: 0–5 \| null}` for the current user | M6 |
| `GET api/plans/<week>` | plan with slots, reasons, day totals; created as draft from `slot_pattern` if missing; M11: eaters, guests, cooked portions; M12: portions per eater, canteen, per-person totals | M7, M11, M12 |
| `POST api/plans/<week>/generate` | fill all active, unlocked slots | M7 |
| `POST api/plans/<week>/slots/<day>/<meal>` | `{action: reroll\|set\|clear\|lock\|unlock\|activate\|deactivate\|portions\|skip\|unskip, recipe_id?, portions?}`; M11: `portions` replaced by `eater {user_id, on}` and `guests {n}`; M13: `rule {tag_id \| null}`; M14: `leftover {from_day, from_meal} \| null` | M7, M11, M13, M14 |
| `POST api/plans/<week>/confirm` | confirm; M8 also pushed to Bring!, from M10 it only confirms | M7, M8, M10 |
| `GET api/plans/<week>/shopping`, `POST api/plans/<week>/push` | list + pushed + no longer needed / push remaining difference; M10: checklist with statuses, `push {keys}` for draft and confirmed plans | M8, M10 |
| `GET/PUT api/pantry` | pantry names (each 1–100 chars, ≤ 300 names) | M8 |
| `GET api/household`, `PUT api/household/<user_id>` | `[{user_id, display_name, eats}]` / `{eats}` (M11); + `kcal_target`, `protein_target_g`, `canteen_kcal`, `canteen_days` (M12) | M11, M12 |
| `POST api/plans/<week>/canteen` | `{day, user_id, on}` | M12 |
| `GET api/today` | today, tomorrow, "Wie war's?" list; M12: own portion / canteen | M9, M12 |

### CLI and environment

- Run (dev): `cd mealprep_planner`, then `py -3.13 -m mealprep` → http://127.0.0.1:8099/
- Tests: `cd mealprep_planner`, then `py -3.13 -m unittest discover -s tests -v`

| Variable | Mode | Purpose |
|---|---|---|
| `SUPERVISOR_TOKEN` | prod (set by HA) | HA API auth; its presence selects prod mode |
| `MEALPREP_HA_URL` | dev | e.g. `http://<ha-ip>:8123` |
| `MEALPREP_HA_TOKEN` | dev | HA long-lived access token; set as user environment variable, never committed |
| `MEALPREP_DEV_USER` | dev | display name of the simulated user (default `Dev`) |

### Home Assistant side

- Sidebar panel "MealPrep Planner" (Ingress), also in the companion apps.
- `sensor.essensplan` for dashboards and Assist (M9).
- Blueprint "MealPrep Planner – Share to Rezept-Inbox" (M5).

## 8. Project structure

```
MealPrepPlanner/                      git repo = HA app repository
├── CLAUDE.md
├── project.md
├── README.md                         M0: what it is + dev quickstart; M1: install on HA; M5: sharing setup
├── LICENSE                           M0: MIT
├── .gitignore
├── repository.yaml                   M1: HA app repository manifest
├── blueprints/automation/mealprep_planner/
│   └── share_to_inbox.yaml           M5
└── mealprep_planner/                 the HA app (Docker build context)
    ├── config.yaml                   M1
    ├── Dockerfile                    M1
    ├── mealprep/                     Python package, stdlib only
    │   ├── __init__.py               VERSION
    │   ├── __main__.py               mode detection, start worker + server
    │   ├── server.py                 HTTP handler, routes, ingress/IP checks, static files, API handlers
    │   ├── db.py                     connection, migrations, settings, users            (M1)
    │   ├── ha.py                     HA REST client                                     (M1)
    │   ├── ingredients.py            parse, scale, format, aggregate                    (M2, M8)
    │   ├── recipes.py                validate_draft, recipe/tag queries, categories, Bring! import link (M2, M10, M13)
    │   ├── importer.py               check_url, fetch, JSON-LD/OpenGraph/oEmbed, images (M3, M4)
    │   ├── worker.py                 background loop: jobs, inbox, sensor               (M3, M5, M9)
    │   ├── ai.py                     ai_task prompts, nutrition guess, output validation (M4, M10, M13)
    │   ├── planner.py                prediction, plan generation, personal portions, sensor payload (M6, M7, M9, M12–M14)
    │   ├── plans.py                  week plans: load/store, slot actions, today page     (M7, M9, M11, M12, M14)
    │   └── shopping.py               shopping list, diff, Bring! checklist push          (M8, M10)
    ├── static/
    │   ├── index.html
    │   ├── app.js                    ES modules allowed, no build step
    │   ├── style.css
    │   └── i18n/de.json, i18n/en.json
    ├── tests/
    │   ├── test_*.py
    │   └── fixtures/                 self-written HTML/JSON samples only
    └── data/                         dev only (DB + images), gitignored
```

- **Runtime dependencies:** none beyond Python 3.13's standard library. Container: `python:3.13-alpine` + Alpine package `tzdata`.
- **Dev dependencies:** Python 3.13 on Windows (`py -3.13`), git. No pip packages, no Node.
- **External services (configured in HA, not in the app):** Bring! integration, an `ai_task` entity, Local To-do (inbox), HA companion apps.

## 9. Roadmap

- [ ] M0 Runnable skeleton (local)
- [ ] M1 HA app, Ingress and HA connection spike
- [ ] M2 Recipes and German ingredient parser
- [ ] M3 Web import and draft queue
- [ ] M4 AI enrichment and video links
- [ ] M5 Share from phone (Rezept-Inbox)
- [ ] M6 Ratings and taste prediction
- [ ] M7 Week planner
- [ ] M8 Shopping list and Bring! push
- [ ] M9 Today page, rating prompt and HA sensor

Scope change "Weight Loss Journey" (agreed 2026-10-06):

- [ ] M10 Bring! under control and nutrition guess
- [ ] M11 Household and eaters
- [ ] M12 Weight loss: targets, canteen and personal portions
- [ ] M13 Categories and slot rules
- [ ] M14 Leftovers ("Reste von …")

Scope change "Category slots" (agreed 2026-10-07):

- [ ] M15 Category slots (replaces the M13 slot rules)

Every milestone: bump `VERSION` (and from M1 `config.yaml`) to `0.<n>.0`; every new UI string goes into both `de.json` and `en.json`; all existing tests keep passing. "(manual)" marks checks done by hand, "(manual, HA)" on the HA device, "(manual, phone)" on a phone.

### M0 – Runnable skeleton (local)

**Build**
- `LICENSE` (MIT; copyright holder: ask the user), `README.md` (one paragraph + dev quickstart: install Python 3.13, run, test).
- `mealprep/__init__.py`: `VERSION = "0.0.1"`.
- `mealprep/__main__.py`: `main()` detects the mode (§5 runtime modes), starts `server.make_server(host, 8099, static_dir)` and logs `MealPrep Planner <version> listening on <host>:8099 (<mode>)`; Ctrl+C shuts down cleanly.
- `mealprep/server.py`: `ThreadingHTTPServer` + `Handler(BaseHTTPRequestHandler)`; route table `ROUTES = [(method, compiled_regex, handler)]`; helpers `send_json(status, obj)` and `send_error(status, code, field=None)`; `current_user(headers, mode)` per §5; prod IP allow-list (`172.30.32.2`); security headers (§5) on every response.
- Endpoints: `GET /` → `static/index.html`; static files from `static/` with extension whitelist (`.html .js .css .json .svg .png`) and correct `Content-Type`, anything resolving outside `static/` → 404; `GET api/health`; `GET api/me` → `{id, name, display_name}`; unknown `api/…` → 404 `not_found`.
- `static/index.html`, `app.js` (loads `i18n/<lang>.json`, `t(key, params)`, language = first of `navigator.languages` starting with `de`/`en`, else `de`; renders "MealPrep Planner läuft · Version … · Hallo <display_name>" via `textContent`), `style.css` (system font, 16 px, max-width 720 px, light/dark via `prefers-color-scheme`), `i18n/de.json` + `en.json` (keys `app.title`, `app.running`, `app.hello`, `error.generic`).

**Tests added**
- `tests/test_server.py`: server on port 0 in a thread (dev mode): `api/health` 200 + version; `api/me` returns the dev user; `/../mealprep/__init__.py` → 404; unknown API path → 404 JSON `not_found`; `current_user` in prod mode without header → 403, with headers → user; IP allow-list function (`172.30.32.2` allowed, `192.168.1.5` rejected).
- `tests/test_i18n.py`: `de.json` and `en.json` have identical key sets; every `t('…')` key used in `app.js` exists.
- `tests/test_stdlib_only.py`: every import in `mealprep/*.py` (via `ast`) is in `sys.stdlib_module_names` or is `mealprep`.

**Acceptance**
- [x] `py -3.13 -m mealprep` starts; http://127.0.0.1:8099/ shows "Hallo Dev" with a German browser language and "Hello Dev" with English (manual).
- [x] `py -3.13 -m unittest discover -s tests -v` passes.
- [ ] Ctrl+C stops the server within 2 s (manual).

### M1 – HA app, Ingress and HA connection spike

Goal: prove deployment, identity and every HA API the app depends on, before building features on them.

**Build**
- `repository.yaml`: `name: MealPrep Planner`, `url: https://github.com/<github-user>/MealPrepPlanner`, `maintainer: <github-user>` (ask the user).
- `mealprep_planner/config.yaml`: `name: MealPrep Planner`, `version: "0.1.0"`, `slug: mealprep_planner`, `description`, `url`, `arch: [amd64]`, `ingress: true`, `ingress_port: 8099`, `panel_icon: mdi:silverware-fork-knife`, `panel_title: MealPrep Planner`, `homeassistant_api: true`, `backup: cold`. No `ports`. Check key names against the current HA app developer docs.
- `mealprep_planner/Dockerfile`: `FROM python:3.13-alpine`, `RUN apk add --no-cache tzdata`, `WORKDIR /app`, `COPY mealprep ./mealprep`, `COPY static ./static`, `CMD ["python3", "-m", "mealprep"]`.
- `mealprep/db.py`: `connect(data_dir)` (Row factory, pragmas §6), `MIGRATIONS` list, `migrate(conn)` (each pending script in a transaction, then `PRAGMA user_version`), `get_settings(conn)`, `set_settings(conn, patch)` (validates §6 keys `bring_entity`, `ai_enabled`, `ai_entity`; entities must exist in HA), `upsert_user(conn, user)` on every request, `set_user_lang(conn, user_id, lang)`. Migration 1.
- `mealprep/ha.py`: `HAError(status, code)`; `request(method, path, body=None, timeout=10)`; `get_config()`, `get_state(entity_id)`, `list_entities(domain)` (from `GET api/states`), `call_service(domain, service, data, return_response=False, timeout=10)` (appends `?return_response` only when asked), `post_state(entity_id, state, attributes)`. Base URL/token per §5 runtime modes; missing config or connection error → `ha_unavailable`, non-2xx → `ha_error`.
- `server.py`: write requests require `Content-Type: application/json` (else 415 `unsupported_media_type`) and ≤ 1 MB (else 413 `too_large`); endpoints `PUT api/me`, `GET/PUT api/settings`, `GET api/ha/entities`, `POST api/system/check`.
- Systemcheck (`POST api/system/check`):
  - `ha`: HA version, time zone and language from `GET api/config`; app local time and `TZ`.
  - `bring` (writes to the selected Bring! list): add "MealPrep Test" with description "Test 1"; add it again with "Test 2"; `todo.get_items` → report count and descriptions of items with that name; `todo.update_item` description "Test 3"; read again; remove until none is left; report each step and the raw responses.
  - `ai`: `ai_task.generate_data` (entity from settings, `task_name: mealprep_check`) on a fixed, self-written German sample caption, twice: variant A with `structure` `{title: text, ingredients: text multiple}` and variant B without `structure`, demanding a JSON object in `instructions`. Report raw response, parse result and duration in ms.
  - `sensor`: post `sensor.essensplan` with state `Systemcheck`, read it back.
- UI: `#/einstellungen` (language, Bring! list, AI on/off + entity, link to Systemcheck), `#/systemcheck` (one button per check, results as preformatted text).
- README: install via repository URL in HA, start, "In der Seitenleiste anzeigen"; dev environment variables.
- After the on-device run: fill V1–V7 in §5 with the observed results.

**Tests added**
- `test_db.py`: empty DB migrates to version 1; `migrate` is idempotent; settings defaults; invalid values rejected with `invalid_field` (`bring_entity: "light.x"`, `ai_enabled: "yes"`, unknown key).
- `test_ha.py` (stub HA = `http.server` in a thread): Authorization header sent; `?return_response` only when requested; non-2xx → `HAError` with status; timeout/connection refused → `ha_unavailable`; `list_entities` filters by domain.
- `test_server.py`: 415 for non-JSON writes, 413 for > 1 MB, `PUT api/me` accepts only `de`/`en`/`null`.
- `test_version.py`: `config.yaml` `version:` equals `mealprep.VERSION` (regex, no YAML library).

**Acceptance**
- [ ] After the user confirms the push, the repo is public on GitHub; adding its URL in HA installs and starts "MealPrep Planner" (manual, HA).
- [ ] The sidebar panel greets the logged-in HA user by display name; a second HA account sees its own name (manual, HA).
- [ ] The app is not reachable from the LAN directly (manual, HA).
- [ ] Settings dropdowns list real `todo` and `ai_task` entities; choices persist across app restarts (manual, HA).
- [x] Language switch changes the UI immediately and persists per user (manual).
- [ ] Systemcheck `ha` shows the HA version; app local time equals HA local time (manual, HA).
- [ ] Systemcheck `bring`: the test item appears in and disappears from the Bring! phone app; V3/V4/V5 filled in §5 (manual, phone).
- [ ] Systemcheck `ai`: at least one variant returns parseable data; V6 filled in §5 (manual, HA).
- [ ] Systemcheck `sensor`: `sensor.essensplan` visible in Developer Tools → States; V7 filled (manual, HA).
- [ ] The app appears in a new HA backup (manual, HA).
- [x] All tests pass.

### M2 – Recipes and German ingredient parser

**Build**
- Migration 2 (tables + 25 default tags). Setting `default_portions`.
- `mealprep/ingredients.py`: `UNITS` (§6 unit table), `parse_line(line) -> dict(amount, unit, name, note)` (§6 rules), `parse_lines(text) -> list`, `scale(amount, factor)`, `fmt_amount(amount, unit) -> str` (German, plural forms).
- `mealprep/recipes.py`: `validate_draft(obj, tag_names) -> (draft, errors)` (§6), `create_recipe`, `update_recipe`, `get_recipe`, `list_recipes(q, tag, archived)`, `set_archived`, `known_ingredient_names`, `list_tags`, `create_tag`, `rename_tag`, `delete_tag`.
- API: recipes, tags, ingredient names, parse-ingredients (§7).
- UI: `#/rezepte`, `#/rezepte/<id>` (portions stepper 1–12, default = recipe servings), `#/rezepte/neu`, `#/rezepte/<id>/bearbeiten`; settings: default portions, tag editor. Default route `#/` → `#/rezepte`.

**Tests added**
- `test_ingredients.py`: ≥ 30 table-driven lines incl. all §6 examples, unicode and mixed fractions, ranges, glued units, unknown unit words staying in the name, comma/parentheses notes, markers, empty and garbage lines (never raise, text kept); `fmt_amount` German formatting and plurals.
- `test_recipes.py`: §6 example draft valid; one rejection test per validation rule with field-specific errors; create/get/update round trip; archived recipes hidden by default; deleting a tag removes its associations; known names distinct and sorted.

**Acceptance**
- [ ] A recipe entered on the phone by pasting 10 ingredient lines from a Chefkoch page: ≥ 9 of 10 lines parsed correctly without edits (manual, phone).
- [x] Changing portions on the recipe page scales the amounts (manual).
- [x] Archive and restore work; archived recipes are hidden from the list (manual).
- [x] All tests pass.

### M3 – Web import and draft queue

**Build**
- Migration 3 (`import_jobs`).
- `mealprep/importer.py`: `check_url(url)` and `fetch(url, max_bytes, accept) -> (final_url, content_type, body)` per §5 (custom redirect handler re-checking each hop; User-Agent `MealPrepPlanner/<VERSION> (+https://github.com/<github-user>/MealPrepPlanner)`; `Accept-Language: de-DE,de;q=0.9`); `extract(html, base_url)` (HTMLParser collecting JSON-LD scripts, `og:title`, `og:image`, `og:description`, `<title>`); `recipe_from_jsonld(objs, default_portions, tag_names)` per §6 mapping; `download_image(url, data_dir) -> filename | None`; `build_draft(job, conn) -> draft` (fetch ≤ 3 MB HTML → JSON-LD → draft; no `Recipe` → OpenGraph prefill + warning `no_recipe_data`; existing `source_url` → warning `already_imported`; image failure → warning `image_failed`; result through `validate_draft`).
- `mealprep/worker.py`: one daemon thread started by `__main__`; at start, `running` jobs are reset to `queued`; loop: take the oldest `queued` job → `running` → `review` (draft stored) or `failed` (error code); sleep 1 s when idle.
- API: imports and images (§7); `{urls}` keeps only `http(s)` strings, max 50.
- UI: `#/import`, `#/import/<job-id>` (§7); recipe list and detail show images.

**Tests added**
- `test_importer.py`: `check_url` rejects `file:`, `ftp:`, `javascript:`, userinfo, ports other than 80/443, `localhost`, `127.0.0.1`, `10.0.0.1`, `172.16.0.1`, `192.168.0.1`, `169.254.1.1`, `::1`, `fc00::1`, `fe80::1`, and hostnames resolving to private IPs (`socket.getaddrinfo` mocked); accepts a public hostname; redirect to a private IP blocked and body over the limit rejected (stub server; the stub's loopback address is allowed through a test-only patch of the IP check); JSON-LD fixtures (self-written): plain `Recipe`, `@graph`, `@type` list, `HowToSection` steps, image as list/`ImageObject`, yield "4 Portionen", durations `PT1H30M`/`PT45M`/`P0DT2H`, nutrition strings, HTML entities; page without JSON-LD → OpenGraph draft with `no_recipe_data`; image magic bytes accept/reject.
- `test_worker.py`: job transitions `queued → running → review/failed` with a stubbed `build_draft`; `running` reset on start.

**Acceptance**
- [ ] 5 real Chefkoch links and 3 links from other German recipe sites (e.g. lecker.de, eatsmarter.de, kitchenstories.com) each give a draft with title, image, servings, steps and ≥ 90 % correctly parsed ingredients (manual). If a site blocks the User-Agent, report it and ask. *Still open: only 1 Chefkoch and 1 EatSmarter link tried so far (both fine; neither site blocked the User-Agent).*
- [ ] Bulk paste of 10 links creates 10 jobs, processed one after another; the page updates without reload (manual). *Still open: tried with 3 links and a single link, the page updated without reload.*
- [x] Importing the same link twice shows the "already imported" warning (manual).
- [x] `http://192.168.0.1/` is rejected with a clear message (manual).
- [x] All tests pass.

### M4 – AI enrichment and video links

**Build**
- `mealprep/ai.py` (transport variant per V6):
  - `enrich(draft, known_names, tag_names) -> (draft, warnings)`: sends title, servings, ingredient lines, steps and nutrition plus up to 500 known names (most used first) and all tag names to `ai_task.generate_data` (`task_name: mealprep_import`, timeout 60 s). German instructions: split each ingredient into amount/unit/name/note using only canonical units; use a known name when it is the same ingredient ("Zwiebel" → "Zwiebeln"); split combined lines ("Salz und Pfeffer"); choose ≤ 6 tags from the list; choose lunch/dinner suitability; estimate nutrition per portion only if missing; never add ingredients that are not in the source.
  - `from_text(text, known_names, tag_names) -> draft`: same output from free text (caption, pasted recipe, page text); title derived if missing.
  - `validate_ai_output(obj) -> (patch, warnings)`: §6 limits; unknown keys dropped; tags outside the list dropped; unknown unit → `unit: null`, unit word prepended to `note`; unparseable or missing title → `AIInvalid`.
  - Merge rules: page nutrition (`source: page`) is never overwritten; AI nutrition gets `source: ai`; `source_url` and `image` are never changed by AI; AI off → warning `ai_disabled`, AI error/invalid → warning `ai_failed`, rule-based draft kept.
- `importer.py`: `source_kind(url)` by host (`tiktok.com`, `vm.tiktok.com` → tiktok; `youtube.com`, `m.youtube.com`, `youtu.be` → youtube; `instagram.com` → instagram; else web); short links resolved through `fetch` redirects; `oembed(url, kind)`: TikTok `https://www.tiktok.com/oembed?url=<url>`, YouTube `https://www.youtube.com/oembed?url=<url>&format=json` → caption/title (≤ 5000 chars), author (≤ 200), thumbnail (http(s)); Instagram: no fetch, draft with link + warning `paste_caption`; `page_text(html) -> str` (visible text without script/style/noscript, whitespace collapsed, ≤ 20000 chars).
- Import flow: web with JSON-LD → `enrich`; web without JSON-LD → `from_text(page_text)`; video → `from_text(caption)` + thumbnail image; text job → `from_text`. With AI off: video captions fall back to `parse_lines` on lines that start with an amount.
- API `{text}` in `POST api/imports`; `POST api/imports/<id>/text`. UI: "Rezepttext einfügen" on `#/import`, "Caption einfügen" in the review form; "geschätzt" badge for `source: ai` nutrition.

**Tests added**
- `test_ai.py`: valid sample output; one test per limit violation; unknown tags/units/keys; non-JSON and partial output; merge rules (page nutrition kept, AI nutrition marked, image/url untouched); prompt contains ≤ 500 known names and all tags; AI disabled/failing → rule-based draft + warning (stubbed `ha.call_service`).
- `test_importer.py`: `source_kind` for all host variants; oEmbed parsing (self-written fixtures, missing fields); `page_text` drops scripts/styles; caption fallback without AI.

**Acceptance**
- [ ] 3 real TikTok recipe links: drafts with title, thumbnail and the ingredients listed in the caption; only small corrections needed (manual).
- [ ] 1 YouTube link: title + thumbnail; pasting the description fills ingredients (manual).
- [ ] 1 Instagram link: draft with link; pasted caption fills ingredients (manual, phone).
- [ ] A Chefkoch import maps an ingredient to an existing differently-written name (e.g. "Zwiebel" → existing "Zwiebeln") (manual).
- [ ] A recipe page without nutrition gets an AI estimate shown as "geschätzt"; Chefkoch nutrition stays marked as from the page (manual).
- [ ] With AI switched off, imports still work rule-based (manual).
- [x] All tests pass.

### M5 – Share from phone (Rezept-Inbox)

**Build**
- Setting `inbox_entity`.
- `blueprints/automation/mealprep_planner/share_to_inbox.yaml`: input `inbox` (entity selector, domain `todo`); trigger: event `mobile_app.share`; condition: event data has `url` or `text`; action: `todo.add_item` on the inbox with `item` = `url` if present else `text` truncated to 250 chars, `description` = full `text` (or empty).
- `worker.py`: every 60 s, if `inbox_entity` is set: `todo.get_items` (`status: needs_action`, `return_response`) → for each item: `extract_urls(summary + "\n" + description)` (regex `https?://[^\s<>"']+`, trailing `.,;:!?)` stripped, ≤ 10 per item) → one job per URL (`origin: inbox`); no URL but ≥ 20 chars of text → text job; then `todo.remove_item`. No duplicate job for the same URL created within the last hour (covers failed removals).
- UI: inbox badge on jobs; navigation badge with the number of drafts in `review`.
- README: create Local To-do list "Rezept-Inbox", import the blueprint (my.home-assistant.io blueprint-import link to the raw GitHub URL), create the automation, how to share on Android and iOS.

**Tests added**
- `test_inbox.py`: `extract_urls` on typical share texts ("Schau dir dieses Video an! https://vm.tiktok.com/ZMabc/ 😋", several links, trailing punctuation, no link); polling creates jobs and removes items (stub HA); failed removal → no duplicate job on the next poll; no `inbox_entity` → no HA calls.

**Acceptance**
- [ ] The blueprint imports via the README link; an automation is created from it (manual, HA).
- [ ] Android: share a TikTok video → "Home Assistant" → draft appears in the app within 2 min; the inbox list is empty afterwards (manual, phone).
- [ ] iPhone: share a Chefkoch link from Safari and an Instagram post → drafts appear (manual, phone).
- [ ] Sharing plain recipe text creates a text draft (manual, phone).
- [x] All tests pass.

### M6 – Ratings and taste prediction

**Build**
- Migration 4 (`ratings`).
- `mealprep/planner.py` (pure functions on plain data):
  ```
  PRIOR = 3.0, K = 2
  G         = mean of all stars of all users (PRIOR if there are none)
  mu_u      = mean of u's stars (G if u has none)
  a_u(t)    = (sum of u's stars on recipes tagged t + K * mu_u) / (count of those + K)
  pred_u(r) = mean of a_u(t) over the tags t of r (mu_u if r has no tags)
  s_u(r)    = u's stars on r if rated, else pred_u(r)
  score(r)  = mean of s_u(r) over all known users (G if there are none)
  vetoed(r) = some user rated r with 0
  ```
  Functions: `user_means`, `global_mean`, `predict(user, recipe, …)`, `household_score(recipe, …) -> (score, details)`, `is_vetoed`. Zeros count in all means.
- API: `PUT api/recipes/<id>/rating`; `GET api/recipes/<id>` adds `ratings [{user_id, display_name, stars}]`, `my_stars`, `my_prediction` (when unrated), `household_score`, `vetoed`; `GET api/recipes` adds `household_score`, `my_stars`, `vetoed`, `sort`, `filter=unrated_by_me`.
- UI: star widget on the recipe page (buttons 1–5 + "🚫 0 Nie wieder", `aria-label`s, tap the current value to clear); list shows household Ø and own stars; detail shows each person's stars, "Prognose für dich: 3,8 ★", veto badge.

**Tests added**
- `test_prediction.py`: worked example (2 users, 6 recipes, tags) with expected values to 2 decimals; no ratings → `PRIOR`; users without ratings shift all scores equally (ranking unchanged); zeros count in means and veto; veto by any user; untagged recipe → user mean.

**Acceptance**
- [ ] Two household members rate 10 recipes each on their phones; each sees the other's stars by name (manual, phone, 2 accounts).
- [ ] An unrated recipe sharing tags with highly rated recipes shows a higher "Prognose" than one sharing tags with low-rated recipes (manual).
- [x] All tests pass.

### M7 – Week planner

*Changed later (agreed 2026-10-06): per-slot portions become eaters + guests in M11; per-person day totals against targets come in M12.*

**Build**
- Migration 5 (`plans`, `plan_slots`). Settings `slot_pattern`, `repeat_window_days`, `new_per_week`.
- `planner.py`: `week_dates(week) -> [date × 7]`, `week_of(date) -> "YYYY-Www"`, `history(conn, exclude_week) -> [(recipe_id, date)]` (non-skipped, filled slots of other confirmed plans), `generate(plan, recipes, ratings, users, history, settings, rng)`, `reroll(plan, day, meal, …)`. Algorithm:
  1. Slots to fill: active, unlocked, not skipped; processed Monday → Sunday, lunch before dinner. Locked slots and inactive slots stay unchanged.
  2. Candidates for a slot: not archived; matching `for_lunch`/`for_dinner`; not vetoed; not used in another slot of the same week; not in `history` within `repeat_window_days` (|Δdays| < window) of the slot date.
  3. "New" recipe = no ratings and not in `history`. `min(new_per_week, slots to fill)` random slots get a new recipe if one is available, the others a known one; an empty pool falls back to the other pool.
  4. Pick by weighted random over the pool: weight = `exp((score − best_score) / 0.5)`; `random.Random(seed)` so tests are deterministic.
  5. No candidate → slot stays empty with reason `none`. Each filled slot stores its reason (§6).
  6. `reroll`: same rules for one slot, excluding its current recipe. `set` (manual) accepts any non-archived recipe, including vetoed ones, reason `manual`.
  7. Past slots of a confirmed plan (slot date < today) count as cooked and are protected: `generate` leaves them unchanged, and `reroll`, `set` and `clear` on them are refused with 400 `bad_request`. `reroll` on a locked slot is refused with 400 `bad_request` too (agreed 2026-10-06).
- Day totals: per day the sum of kcal/protein/fat/carbs per portion over active, non-skipped, filled slots; flags `estimated` (any AI value) and `incomplete` (any recipe without nutrition).
- API: plans (§7). `GET` on a missing week creates a draft from `slot_pattern` with `default_portions`. Confirm sets `status = confirmed` and `confirmed_at`; editing a confirmed plan is allowed. `skip`/`unskip` only on confirmed plans for dates ≤ today.
- UI: `#/woche/<week>` (§7; default: current week); settings: slot pattern grid, repeat window, new per week. Default route stays `#/rezepte`.

**Tests added**
- `test_planner.py`: ISO weeks (2026-W01 dates, 2026-W53 exists, 2027-W01, invalid week rejected); one test per candidate rule; deterministic `generate` with a seed; locked/inactive/skipped slots untouched; new quota met exactly when enough new recipes exist, fallback otherwise; `reroll` returns a different recipe when ≥ 2 candidates exist; empty pool → reason `none`; day totals with estimated/incomplete flags.

**Acceptance**
- [ ] With ≥ 20 recipes, "Vorschlag erstellen" fills all active slots of next week according to the slot pattern; no recipe twice; nothing cooked in the last 14 days (manual).
- [ ] Reroll, lock, replace, portions and switching off work on the phone; locked slots survive a new "Vorschlag erstellen" (manual, phone).
- [ ] Every suggestion shows its reason; day totals show kcal, with "≈" when estimates are involved (manual).
- [ ] "Bestätigen" marks the plan as confirmed; past slots can be marked "ausgefallen" (manual).
- [x] All tests pass.

### M8 – Shopping list and Bring! push

*Changed later (agreed 2026-10-06): M10 removes the push on "Bestätigen"; sending goes through the checklist.*

Follow the results V3–V5 in §5; if they contradict this description, ask before deviating.

**Build**
- Migration 6 (`pantry` + defaults, `pushed_items`).
- `ingredients.py`: `unit_key(unit)` (`g`, `ml`, canonical unit or `''`), `to_base(amount, unit)`, `shopping_round(amount, unit)` (§6 unit table), `aggregate(lines) -> {name_key: Item}` (key = name case-folded; amount-less parts ignored when the same name has an amount), `format_note(parts) -> str` (mass, volume, then other units alphabetically, joined with " + ", e.g. "1,25 kg + 2 Dosen"; empty if no amounts).
- `mealprep/shopping.py`: `build_list(conn, week)` (active, non-skipped, filled slots; amounts × slot portions / recipe servings; pantry names excluded case-insensitively); `diff(current, pushed) -> (to_push, no_longer_needed)` (per name + unit_key: positive delta is pushed; amount-less items once; smaller or missing current amounts → no longer needed); `push(conn, week) -> result`: read open Bring! items (`todo.get_items`, `needs_action`), then per item to push: open item with the same name (case-insensitive; the first one if several, V3) → `todo.update_item` addressed by its `uid` (V4) with description = existing + " + " + note (or just the note if empty; Bring! truncates descriptions beyond ~191 chars and the app accepts that silently (agreed 2026-10-06)); else `todo.add_item` (item = name, description = note); every successful call immediately updates `pushed_items`, so a partial failure can be resumed. Items in `no_longer_needed` are assumed to be removed by hand in Bring!: each push (after reporting them in its result) lowers their `pushed_items` amount to the current need (row deleted when nothing is needed any more), so if they are needed again later, the difference is pushed again (agreed 2026-10-06).
- `POST api/plans/<week>/confirm` pushes after confirming; the plan stays confirmed even if the push fails. Result: `{added, updated, skipped_pantry, no_longer_needed, failed}`. `POST api/plans/<week>/push` pushes the remaining difference. `GET api/plans/<week>/shopping`, `GET/PUT api/pantry`.
- UI: result panel after confirm, "Erneut senden", `#/woche/<week>/einkauf`; settings: pantry editor (one name per line).

**Tests added**
- `test_shopping.py`: aggregation (g + kg, ml + l, Dose and g for the same name, amount-less only, amount-less + amount); scaling by portions/servings; rounding up countable units; pantry exclusion (case-insensitive); German note formatting; diff (new, increased, decreased, removed, amount-less pushed once); push against stub HA (add vs. update, description concatenation, partial failure recorded correctly); missing `bring_entity` → `bring_failed`.

**Acceptance**
- [ ] Confirming a real week puts all non-pantry ingredients into Bring! with amounts as notes, visible on the phone within 1 min (manual, phone).
- [ ] An item already open in Bring! (e.g. "Milch") gets our amount appended instead of a duplicate (manual, phone).
- [ ] After replacing one meal and confirming again, only new or increased items are added; items no longer needed are listed in the app (manual).
- [ ] With a broken Bring! connection during confirm, the plan is still confirmed and "Erneut senden" later pushes the rest (manual).
- [x] All tests pass.

### M9 – Today page, rating prompt and HA sensor

**Build**
- `GET api/today` → `{today: {date, lunch, dinner}, tomorrow: {…}, rate: [{recipe_id, title, date, meal}]}`; `rate` = filled, active, non-skipped slots of confirmed plans dated in the 14 days before today whose recipe the current user has not rated; newest first, one entry per recipe, ≤ 10.
- `planner.sensor_payload(slots, now) -> (state, attributes)` (§6): state = next meal title: before 14:00 today's lunch if active and filled, else before 21:00 today's dinner, else tomorrow's first active filled slot; `–` if none; truncated to 255 chars. Attributes as in §6; `week` = filled active slots of the current week.
- `worker.py`: post `sensor.essensplan` every 5 min and right after any plan change; errors are logged, never raised.
- UI: `#/` becomes "Heute" (§7) and the default route.

**Tests added**
- `test_today.py`: rate list rules (rated, skipped, older than 14 days, future, other user's rating, duplicates); sensor state at 08:00, 13:59, 14:00, 20:59, 21:00 with and without an active lunch, empty week, title truncation.

**Acceptance**
- [ ] The start page shows today's meals and yesterday's unrated meal; rating it removes it from the list (manual, phone).
- [ ] An HA dashboard entity card shows `sensor.essensplan`; it switches at 14:00 and 21:00 (manual, HA).
- [ ] After an HA restart, the sensor is back within 5 min (manual, HA).
- [x] All tests pass.

### M10 – Bring! under control and nutrition guess

Goal: nothing reaches Bring! unless a person ticks it; Chefkoch-style Bring! import for web recipes; nutrition estimates on demand (agreed 2026-10-06).

**Build**
- `server.py`: `POST api/plans/<week>/confirm` only confirms (no push, no `push` key in the response). `POST api/plans/<week>/push` takes `{keys: […]}` (1–500 keys of the current checklist; anything else → 400 `invalid_field` `keys`) and works for draft and confirmed plans.
- `shopping.py`:
  - `build_list` keeps pantry items, flagged as pantry, instead of dropping them.
  - `view(conn, week)` returns the checklist: `items: [{key, name, note, status, checked}]` with `key` = name case-folded and `status` `new` (never sent; checked), `more` (sent before, more needed now; `note` = the difference; checked), `pantry` (name in the pantry list, not sent yet; `note` = full amount; unchecked) or `sent` (nothing new; shown without checkbox); plus `no_longer_needed` as today.
  - `push(conn, week, keys)`: same logic as today (open items, update by `uid`, partial-failure recording, `_lower`), but only for the given keys; a ticked pantry item is sent with its full amount and recorded in `pushed_items` like any other item. Result `{added, updated, failed, no_longer_needed}`.
- `ai.py`: `estimate_nutrition(draft, entity) -> nutrition`: own prompt (title, servings, ingredient lines; answer `{kcal, protein_g, fat_g, carbs_g}` per portion) through `_ask`, validated with the §6 ranges, `source: "ai"`; AI off, HA error or unusable answer → the endpoint answers 502 `ai_failed`.
- API `POST api/nutrition/estimate` (§7); it saves nothing.
- `recipes.py`: `bring_import_url(source_url, servings=None, portions=None) -> str` = `https://api.getbring.com/rest/bringrecipes/deeplink?url=<url-encoded>&source=web`, plus `&baseQuantity=<servings>&requestedQuantity=<portions>` only if V8 confirms these parameters.
- UI:
  - Week view: "Bestätigen" only confirms; "Erneut senden", its hint and the push panel leave the week view; new button "🛒 An Bring! senden" (enabled once a slot is filled) → `#/woche/<week>/einkauf`.
  - `#/woche/<week>/einkauf` becomes the checklist (§7): "Ausgewählte senden (N)", result panel, "Erneut senden" for failed items, "Bring! öffnen" if V9 found a link, items no longer needed.
  - Recipe page: "In Bring! importieren" for `source_kind = web` recipes with a `source_url` (`rel="noopener noreferrer"`).
  - Edit form and draft review: "Nährwerte schätzen" (asks first when the current values have `source: page`; fills the four fields, marked "geschätzt").
  - Systemcheck: Bring! link tests (a field for a public recipe URL → the deeplink with and without `&baseQuantity=4&requestedQuantity=2`; candidate links that open the Bring! app, looked up by the session).
- After the phone checks: fill V8 and V9 in §5.

**Tests added**
- `test_shopping.py`: checklist statuses and default ticks (new, more with difference, pantry case-insensitive, sent); push only for the given keys; ticked pantry item sent and recorded; push on a draft plan.
- `test_server.py`: confirm no longer calls HA and returns no `push`; `push {keys}` validation (missing, empty, unknown key).
- `test_ai.py`: `estimate_nutrition` with a valid answer; out-of-range values dropped; non-JSON answer and AI off → `ai_failed`; the endpoint saves nothing.
- `test_recipes.py`: `bring_import_url` encodes URLs with `?`, `&`, `#` and umlauts.

**Acceptance**
- [ ] "Bestätigen" sends nothing to Bring! (manual).
- [ ] After "Vorschlag erstellen", "An Bring! senden" shows the checklist with pantry items unticked; sending the ticked items puts them into Bring! with amounts as notes, visible on the phone within 1 min (manual, phone).
- [ ] After replacing a meal, the checklist ticks only new items and increases; items already sent have no checkbox (manual).
- [ ] "In Bring! importieren" on an imported Chefkoch recipe opens Bring!'s import screen on Android and iOS; V8 filled (manual, phone).
- [ ] "Bring! öffnen" opens the Bring! app, or V9 records that no link works and the button is left out (manual, phone).
- [ ] "Nährwerte schätzen" fills the four values of a manual recipe within 60 s; on a Chefkoch recipe it asks before overwriting (manual).
- [x] All tests pass.

### M11 – Household and eaters

**Build**
- Migration 7 (§6): `users.eats`, `plan_slots.guests`, table `slot_eaters` (existing plans: every user becomes eater of every active slot), `plan_slots.portions` dropped. Existing plans are test data (agreed 2026-10-06).
- `db.py`: `household(conn) -> [{user_id, display_name, eats}]`, `set_household(conn, user_id, patch)` (validates `eats` as bool; unknown user → 404 `not_found`).
- `plans.py`: `load_plan` creates new weeks with every participant (`eats = 1`) on every active slot and `guests = 0`; slots carry `eaters` (user ids) and `guests`; `cooked_portions(slot)` = number of eaters + guests. `ACTIONS`: `portions` removed; new `eater {user_id, on}` (existing user, active slot only) and `guests {n}` (0–12); `activate` puts all participants on the slot. `view` returns per slot `eaters [{user_id, display_name}]`, `guests`, `cooked_portions`; `dated_slots` and `today_view` return cooked portions instead of `portions`.
- `shopping.build_list`: amounts × cooked portions ÷ recipe servings (cooked portions from eaters + guests instead of `s.portions`).
- API: `GET api/household`, `PUT api/household/<user_id>` `{eats}`.
- UI: settings "Haushalt" (one row per user with an "Ich esse mit" toggle); week view: eater chips per slot (tap = remove/re-add), "+ Portion" stepper for guests instead of the portions stepper, cooked portions label; today page shows cooked portions.

**Tests added**
- `test_db.py`: migration 7 on a DB with an existing plan → eaters for every user on the active slots, `portions` column gone; household validation.
- `test_planner.py` / `test_server.py`: a new week puts exactly the participants on every active slot; `eater` and `guests` actions incl. ranges and unknown users; `activate` re-adds the participants; the `portions` action is rejected; shopping scales by eaters + guests; the today page shows cooked portions.

**Acceptance**
- [ ] Two HA users with "Ich esse mit" appear on every slot of a new week; removing one from a slot and adding a guest changes the cooked portions and the shopping amounts (manual).
- [ ] A user who does not eat along is not put on new weeks (manual).
- [x] All tests pass.

### M12 – Weight loss: targets, canteen and personal portions

**Build**
- Migration 8 (§6): target and canteen columns on `users`, table `plan_canteen`.
- `db.py`: household fields `kcal_target`, `protein_target_g`, `canteen_kcal`, `canteen_days` (validation as in the §6 users table).
- `planner.py` (pure functions):
  ```
  STEP = 0.25, MIN_FACTOR = 0.5, MAX_FACTOR = 2.0, FIT_RANGE = (0.75, 1.5)
  For each person u and day d:
    C     = canteen_kcal(u) if (week, d, u) is in plan_canteen else 0
    meals = active, non-skipped, filled slots of d where u is an eater (from M14 also leftover slots,
            with the recipe of their source slot); k = kcal per portion of the meal's recipe
    if u has no kcal_target:     factor(u, d) = 1
    elif some meal has no kcal:  factor(u, d) = 1, day flagged "incomplete"
    else:                        factor(u, d) = clamp(round_to_step((kcal_target - C) / sum(k)), MIN_FACTOR, MAX_FACTOR)
                                 (kcal_target - C <= 0 -> MIN_FACTOR; rounding to the nearest 0.25, halves up)
    day_kcal(u, d)    = C + sum(factor * k)
    day_protein(u, d) = sum(factor * protein per portion)      (canteen protein unknown, not counted)
  cooked_portions(s) = sum(factor(u, d_s) over eaters u of s) + guests(s)
  Fit, used by generate/reroll for candidate r in slot s, per eater u of s with a target:
    b(u) = (kcal_target - C) / number of meals of u on d_s (counting s)
    misfit if b(u) / k(r) is outside FIT_RANGE
    adjusted score = score - misfits / eaters with a target    (k(r) unknown -> unchanged, fit "unknown")
  ```
  Functions: `personal_factors(...)`, `person_day_totals(...)`, `cooked_portions(...)` (replaces the M11 count), fit adjustment inside `generate`/`reroll` with reason `fit`. The per-portion `day_totals` stay for days where no eater has a target.
- `plans.py`: new weeks get the canteen defaults (every participant whose `canteen_days` contains the day gets a `plan_canteen` row and is not put on that day's lunch); `set_canteen(conn, week, day, user_id, on)`: on → row added and the person removed from that day's lunch eaters; off → row deleted and the person put back on the lunch if it is active. `view` adds per slot `portions_by_user`, per day `canteen` (user ids) and `totals_by_user {user_id: {kcal, kcal_target, protein_g, protein_target, canteen, incomplete}}`; `today_view` adds `my_portion`, `my_kcal`, `my_canteen`.
- `shopping.build_list`: cooked portions with personal factors.
- API: household fields; `POST api/plans/<week>/canteen` `{day, user_id, on}`.
- UI: settings "Haushalt" per user: kcal target, protein target, canteen kcal, canteen weekdays; week view: portion per eater ("1¼"), "Kantine" toggle per person and day, per-person day totals (green within ±10 % of the target, amber outside), fit hint "passt nicht zu euren Zielen", "Nährwerte schätzen" link on incomplete days; today page: "Deine Portion: 1¼ (≈ 780 kcal)" or "Kantine".

**Tests added**
- `test_portions.py`: worked example (targets 1700/1300, lunch 450 + dinner 800 kcal → factors 1.25 and 1.0, cooked lunch portions 2.25); canteen day (700 kcal + dinner 800 → factor 1.25, day total 1700); budget ≤ 0 → 0.5; clamps at 0.5 and 2.0; rounding (1.36 → 1.25, 1.38 → 1.5); no target → 1.0; missing kcal → 1.0 + incomplete; protein totals; fit adjustment lowers a misfit recipe's score and leaves `unknown` unchanged; canteen defaults applied to new weeks only; the canteen toggle removes/re-adds the lunch eater; household validation (ranges, weekday list).
- `test_today.py`: `my_portion`, `my_kcal`, `my_canteen`.

**Acceptance**
- [ ] With kcal targets for two people, the week view shows a portion per person and meal and day totals close to each target (manual, phone).
- [ ] A canteen lunch for one person counts their canteen kcal, removes only them from the planned lunch and lowers the cooked portions and shopping amounts (manual).
- [ ] A very calorie-dense recipe is rarely suggested and shows the fit hint when set by hand (manual).
- [ ] Protein per person and day is shown against the protein target (manual).
- [ ] The start page shows "deine Portion" or "Kantine" (manual, phone).
- [x] All tests pass.

### M13 – Categories and slot rules

**Build**
- Migration 9 (§6): `tags.category`, the six missing category tags, the 8 categories flagged, `plan_slots.rule_tag_id` (`ON DELETE SET NULL`). Setting `slot_rules` (§6).
- `recipes.py`: tags carry `category`; `update_tag(id, name, category)`; `auto_categories(draft) -> set[str]` (constants `QUICK_MAX_MINUTES = 30`, `LIGHT_MAX_KCAL = 500`, `PROTEIN_MIN_SHARE = 0.25`; missing values → no tick), applied by `importer.build_draft` after the AI and returned by `api/nutrition/estimate` as `suggested_tags`.
- `ai.py`: `MAX_TAGS = 8`; the prompt asks to judge Meal Prep, Sonntagsessen, Lunchbox, Ofengericht and Gäste from the recipe.
- `plans.py`: new weeks copy `slot_rules` into `rule_tag_id`; slot action `rule {tag_id | null}` (category tags only).
- `planner._candidates`: with a rule, only recipes with that tag; if none qualifies, the rule is dropped for this slot and the reason gets `rule_met: false`; reasons carry `rule`.
- UI: category chips (styled apart from other tags) on cards, detail and form; category filter on `#/rezepte`; "Kategorie" toggle in the tag editor; a category select per cell of the slot pattern grid; a rule select per slot in the week view with the hint "Kategorie nicht erfüllt".

**Tests added**
- `test_db.py`: migration 9 on an existing DB (missing tags added, existing ones not duplicated, exactly the 8 flagged).
- `test_recipes.py`: `category` round trip; `auto_categories` at the boundaries (30/31 min, 500/501 kcal, protein share exactly 25 %, missing values).
- `test_planner.py`: rule filters; fallback with `rule_met: false`; a rule changed in one week leaves `slot_rules` unchanged; deleting a category tag clears the rules.
- `test_ai.py`: up to 8 tags kept.

**Acceptance**
- [ ] With "Mo–Fr Abend = Schnell" in the pattern, those slots get Schnell recipes; without a matching recipe the slot is filled anyway and says so (manual).
- [ ] An imported recipe with ≤ 30 min arrives with "Schnell" ticked; the AI suggests sensible categories (manual).
- [ ] Recipes can be filtered by category (manual).
- [x] All tests pass.

### M14 – Leftovers ("Reste von …")

**Build**
- Migration 10 (§6): `plan_slots.leftover_day`, `plan_slots.leftover_meal`.
- `plans.py`: slot action `leftover {from_day, from_meal} | null`. The source must be in the same week, strictly earlier (day, then lunch before dinner), active, filled and not itself a leftover. The leftover slot shows the source's recipe (dynamic: it follows rerolls and clears of the source); `null` removes the link and empties the slot; deactivating a source removes its leftover links.
- `planner.py`: `generate`/`reroll` skip leftover slots but count their recipe as used; personal factors count leftover meals with the source recipe's kcal; cooked portions of the source add, for every slot t marked "Reste von" it, the factors of t's eaters + t's guests; category rules are ignored on leftover slots.
- `shopping.build_list`: leftover slots add nothing themselves (their portions are in the source).
- History and "Wie war's?": a leftover adds no extra history or rating entry.
- UI: "Reste von …" select listing the valid earlier slots; a leftover slot shows "Reste von <Tag> <Mahlzeit>" instead of reroll/replace.

**Tests added**
- `test_planner.py`: valid and invalid sources (later slot, other week, leftover of a leftover, empty, inactive); a leftover follows a reroll of its source; `generate` skips leftover slots; cooked portions include leftovers; shopping counts the source only; no extra rate-list entry.

**Acceptance**
- [ ] "Di Mittag = Reste von Mo Abend" shows the same recipe on Tuesday, raises Monday's cooked portions and adds nothing extra to the checklist (manual).
- [ ] "Vorschlag erstellen" leaves leftover slots alone (manual).
- [x] All tests pass.

### M15 – Category slots (agreed 2026-10-07)

Goal: a category says where its recipes belong in the week (e.g. "Vesper" only for dinner, "Sonntagsessen" only on Sunday), instead of a slot demanding a category. Replaces the M13 slot rules.

**Build**
- Migration 11 (§6): `tags.slots` (JSON 14 × bool, `NULL` = all slots; existing categories start with `NULL`); `plan_slots.rule_tag_id` removed; stored `slot_rules` setting deleted.
- `recipes.py`: tags carry `slots` (14 bools; `NULL` is returned as all `true`); `update_tag(id, name, category, slots)` validates `slots` as a list of exactly 14 bools (else 400 `invalid_field` `slots`); `slots` only matters while `category = 1`. `planning_recipes` returns per recipe its allowed slot mask = AND over the `slots` of all its category tags (all `true` without categories).
- `planner.py`: candidate rule (§9 M7 step 2) gets one more condition: the recipe's allowed slot mask is `true` at `day × 2 + meal` of the slot. Applies to `generate` and `reroll`; `set` (manual) ignores it, as it ignores the Mittag/Abend flags. No candidate → slot stays empty with reason `none`, as today. Leftover slots are unaffected (skipped by `generate`).
- Removed: setting `slot_rules` and its validator, `plans.py` copy of `slot_rules` into new weeks, slot action `rule`, `rule` in slots/view, `rule` and `rule_met` in reasons, the `_candidates` rule filter and fallback; UI: rule grid in the settings, rule select per slot and the hint "Kategorie nicht erfüllt" in the week view; their i18n keys.
- UI: in the tag editor, a category tag gets a 7×2 grid (Mo–So × Mittag/Abend, same look as the slot pattern grid) to tick its allowed slots, default all ticked; the recipe page and edit form are unchanged (categories are still ticked like tags, several allowed).
- Bump version to 0.15.0.

**Tests added**
- `test_db.py`: migration 11 on a DB at version 10 (with a slot that had a `rule_tag_id` and a stored `slot_rules`) → `rule_tag_id` gone, `slot_rules` deleted, `tags.slots` `NULL`.
- `test_recipes.py`: `slots` round trip and validation (13 / 15 entries, non-bool); allowed mask = AND over several categories; recipe without categories → all `true`; non-category tag's `slots` ignored.
- `test_planner.py`: a "dinner only" category never lands in a lunch slot; a "Sunday only" category only on Sunday; two categories → intersection; no candidate → reason `none`; `reroll` respects the mask; `set` ignores it.
- Removed tests for the M13 slot rules are deleted, not skipped.

**Acceptance**
- [ ] A category "Vesper" with only the dinner slots ticked: "Vorschlag erstellen" never puts a Vesper recipe into a lunch (manual).
- [ ] A recipe with two categories only appears in slots both allow (manual).
- [ ] The settings and week view no longer show slot rules (manual).
- [ ] All tests pass.

## 10. Testing

**Automated** (`cd mealprep_planner`, then `py -3.13 -m unittest discover -s tests -v`; stdlib `unittest` only; no network, no real HA):
- Core logic: ingredient parsing, scaling, formatting and aggregation; taste prediction and household score; plan generation (seeded) incl. category rules (M13) and calorie fit (M12); shopping diff and checklist (M10); eaters and cooked portions (M11); personal portions, canteen and per-person totals (M12); automatic categories (M13); leftovers (M14); sensor state; "Wie war's?" rules; ISO week helpers.
- Data validation: `validate_draft`, AI output and nutrition-guess validation, settings and household fields, migrations (fresh DB → current version, idempotent; migrations 7–10 also on a DB with existing plans).
- Trust boundaries: SSRF guard (schemes, ports, private/loopback/link-local IPs, redirects), size limits, image magic bytes, Ingress IP allow-list and user headers, JSON-only writes, body size, static path traversal, Bring! deeplink URL encoding.
- HA client and Bring!/AI/inbox flows against a stub HA server (`http.server` in a thread); page fetching against stub servers or mocks; fixtures are self-written.
- Hygiene: stdlib-only imports, i18n key parity and coverage, `config.yaml` version = `VERSION`.

**By hand** (listed per milestone as "(manual …)"):
- Installing/updating the app from GitHub, Ingress panel, user identity with two HA accounts, backups.
- Real recipe sites, TikTok/YouTube/Instagram links, AI result quality, share sheet on Android and iOS.
- Bring! phone app contents, Bring! import and "Bring! öffnen" links, `sensor.essensplan` on a dashboard and after an HA restart.
- Personal portions and day totals with real targets; phone usability of every page in both languages.

## How we'll work afterwards

- Planning, evaluations and scope changes happen in a planning session: you ask, I decide, you update `project.md` and commit.
- Each milestone is implemented in its own new **Sonnet 5.5** session started with "Implement M<n> from project.md".
- When I ask "is X possible?" or "how much work is Y?", answer from the current code and `project.md` first: what works today without code, options from cheapest to most expensive with an effort estimate (sessions, rough lines, files touched, new dependencies), risks, and a recommendation. Don't add a milestone until I agree.
