# W3 orchestration

Base: `origin/cursor/w2-implementation-85f5` → `cursor/w3-implementation-85f5`.

| Agent | Branch | Scope |
| --- | --- | --- |
| A4 | `cursor/w3-01-authz-audit-85f5` | roles, operator scripts, audit, pagination services |
| A3 | `cursor/w3-04-watchlist-85f5` | watchlist, notifications backend |
| A2 | `cursor/w3-05-signals-ui-85f5` | bell, watch UI, pagination UI, script published checkbox |
| A5 | `cursor/w3-07-regression-85f5` | matrix tests + checklist |

Merge: A4 → A3 → A2 → A5 → VERSION 0.5.0.
