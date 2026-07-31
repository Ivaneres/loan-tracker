# Agent notes

Project-specific guidance for coding agents lives in **`.cursor/rules/`** (checked into git).

| Rule | When it applies |
|---|---|
| `project-overview.mdc` | Always — stack, layout, auth, tests; **every feature**: full unit-test run + step-by-step UI playthrough with inline PR screenshots |
| `production-deployment.mdc` | Always — only follow when user asks to deploy |
| `daily-budget.mdc` | Daily budget / spending-daily files (incl. required PR screenshots) |
| `statement-manual-reconcile.mdc` | Home statement import preview — manual fuzzy match, missed spends, expected bills |
| `transaction-search.mdc` | Global transaction search |

## Standard feature routine

For **every** new feature / PR in this repo:

1. Implement + commit on a `cursor/…` branch.
2. Run **all** unit tests: `python -m unittest discover -s tests -v`.
3. If the change is user-visible, do a **step-by-step playthrough** of the real user flow (not a single static shot). Capture Playwright screenshots at each meaningful step — including happy path, creating/adding new values where relevant, and the result after save/import — then commit under `docs/screenshots/<feature>/`, push, and embed them **inline** in the PR body as ordered evidence (see `project-overview.mdc`).
4. Update `.cursor/rules/` when you learn durable facts.

When you learn durable facts about this repo while implementing a feature (APIs, invariants, UX patterns, prod quirks), **update or add a `.cursor/rules/*.mdc` file** in the same change so the next agent inherits it. Prefer short, actionable bullets and `@path` references over pasting large code.
