# MealPrep Planner

A Home Assistant App that turns your household's own recipe collection into a weekly lunch-and-dinner plan, using everyone's star ratings, and pushes the shopping list to Bring!. See [project.md](project.md) for scope and roadmap.

## Development quickstart

Requires Python 3.13 (standard library only, no pip packages).

```
cd mealprep_planner
py -3.13 -m mealprep
```

Open http://127.0.0.1:8099/. Tests:

```
cd mealprep_planner
py -3.13 -m unittest discover -s tests -v
```
