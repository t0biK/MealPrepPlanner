# MealPrep Planner

A Home Assistant App that turns your household's own recipe collection into a weekly lunch-and-dinner plan, using everyone's star ratings, and pushes the shopping list to Bring!. See [project.md](project.md) for scope and roadmap.

## Install on Home Assistant

1. In Home Assistant: Settings → Apps → App store → ⋮ → Repositories, add `https://github.com/t0biK/MealPrepPlanner`.
2. Install "MealPrep Planner" and start it (HA builds the image on the device).
3. Enable "In der Seitenleiste anzeigen" (show in sidebar), then open the panel.
4. In the app: Einstellungen → choose the Bring! list and the AI entity; Systemcheck verifies the connection to Home Assistant.

## Share recipes from your phone (Rezept-Inbox)

Share a link or text from any app to the Home Assistant companion app; MealPrep Planner turns it into a recipe draft within about a minute.

1. In Home Assistant: Settings → Devices & services → Add integration → **Local To-do**, name the list "Rezept-Inbox". Use a list of its own, not the Bring! list.
2. Import the blueprint: [![Import blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fraw.githubusercontent.com%2Ft0biK%2FMealPrepPlanner%2Fmain%2Fblueprints%2Fautomation%2Fmealprep_planner%2Fshare_to_inbox.yaml)
3. Settings → Automations & scenes → Create automation → "MealPrep Planner – Share to Rezept-Inbox"; choose the Rezept-Inbox list and save.
4. In the app: Einstellungen → Rezept-Inbox → choose the list.
5. Share: **Android**: Share → "Home Assistant". **iPhone**: Share sheet → "Home Assistant" (the companion app must be installed and logged in). Plain recipe text can be shared the same way.

The app polls the list every 60 s, creates a draft for every link (max 10 per entry) or for text of at least 20 characters, and removes the entry. Drafts show up under "Import" with an "Inbox" badge; the number of drafts to review is shown on the navigation tab. Entries with neither a link nor enough text stay in the list.

## Development quickstart

Requires Python 3.13 (standard library only, no pip packages).

```
cd mealprep_planner
py -3.13 -m mealprep
```

Open http://127.0.0.1:8099/. To use a real Home Assistant, set these environment variables first (never commit them):

| Variable | Purpose |
|---|---|
| `MEALPREP_HA_URL` | e.g. `http://<ha-ip>:8123` |
| `MEALPREP_HA_TOKEN` | HA long-lived access token |
| `MEALPREP_DEV_USER` | display name of the simulated user (default `Dev`) |

Tests:

```
cd mealprep_planner
py -3.13 -m unittest discover -s tests -v
```
