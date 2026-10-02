# W2 orchestration

Base: `origin/cursor/w1-implementation-2c6a` → integration `cursor/w2-implementation-85f5`.

| Agent | Role | Branch pattern | Scope |
| --- | --- | --- | --- |
| A1 | Tech lead / contracts | integration | `docs/agents/w2-contracts.md` |
| A3 | Backend ops | `cursor/w2-03-bulk-batch-85f5` | bulk APIs, batch status/page route, map scripts context |
| A4 | Authz | `cursor/w2-06-bulk-authz-85f5` | authz helpers + tests |
| A2 | Frontend | `cursor/w2-01-map-bulk-ui-85f5` | multi-select, bulk bar, URL filters, favorites, batch.html |
| A5 | QA | `cursor/w2-07-regression-85f5` | checklist + edge tests |

Merge order: A4 → A3 → A2 → A5 → VERSION 0.4.0.
