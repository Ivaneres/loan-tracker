# Agent notes

Project-specific guidance for coding agents lives in **`.cursor/rules/`** (checked into git).

| Rule | When it applies |
|---|---|
| `project-overview.mdc` | Always — stack, layout, auth, tests |
| `production-deployment.mdc` | Always — only follow when user asks to deploy |
| `daily-budget.mdc` | Daily budget / spending-daily files |

When you learn durable facts about this repo while implementing a feature (APIs, invariants, UX patterns, prod quirks), **update or add a `.cursor/rules/*.mdc` file** in the same change so the next agent inherits it. Prefer short, actionable bullets and `@path` references over pasting large code.
