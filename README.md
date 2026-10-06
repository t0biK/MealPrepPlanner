# MealPrep Planner

A Home Assistant App that turns your household's own recipe collection into a weekly lunch-and-dinner plan, using everyone's star ratings, and pushes the shopping list to Bring!. See [project.md](project.md) for scope and roadmap.

## Install on Home Assistant

1. In Home Assistant: Settings → Apps → App store → ⋮ → Repositories, add `https://github.com/t0biK/MealPrepPlanner`.
2. Install "MealPrep Planner" and start it (HA builds the image on the device).
3. Enable "In der Seitenleiste anzeigen" (show in sidebar), then open the panel.
4. In the app: Einstellungen → choose the Bring! list and the AI entity; Systemcheck verifies the connection to Home Assistant.

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
