# CLAUDE.md

- Read `project.md` first; it is the source of truth for scope, architecture and roadmap.
- Implementation sessions run on **Sonnet 5.5**.
- No assumptions: if anything is unclear or not covered in `project.md`, ask the user before deciding.
- Work on one milestone per session.
- When a milestone's acceptance criteria are met, tick it in `project.md`, then commit after the user confirms.

Quick reference (details in `project.md` §7–§8):
- Run: `cd mealprep_planner`, then `py -3.13 -m mealprep`
- Test: `cd mealprep_planner`, then `py -3.13 -m unittest discover -s tests -v`
- Public repo: never commit secrets or personal data; push to GitHub only after the user confirms.
