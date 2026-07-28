# Agent notes

Project-specific guidance for coding agents lives in **`.cursor/rules/`** (checked into git).

| Rule | When it applies |
|---|---|
| `project-overview.mdc` | Always — stack, layout, auth, tests; **every feature**: full unit-test run + inline PR screenshots when UI changes |
| `production-deployment.mdc` | Always — only follow when user asks to deploy |
| `daily-budget.mdc` | Daily budget / spending-daily files (incl. required PR screenshots) |

## Standard feature routine

For **every** new feature / PR in this repo:

1. Implement + commit on a `cursor/…` branch.
2. Run **all** unit tests: `python -m unittest discover -s tests -v`.
3. If the change is user-visible, capture Playwright demos (`tmp/screenshot_*.py`), commit under `docs/screenshots/<feature>/`, push, and embed **inline** `<img>` tags in the PR body via `raw.githubusercontent.com` (see `project-overview.mdc`).
4. Update `.cursor/rules/` when you learn durable facts.

When you learn durable facts about this repo while implementing a feature (APIs, invariants, UX patterns, prod quirks), **update or add a `.cursor/rules/*.mdc` file** in the same change so the next agent inherits it. Prefer short, actionable bullets and `@path` references over pasting large code.
