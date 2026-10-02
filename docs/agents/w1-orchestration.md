# W1 orchestration status

Интеграционный PR: https://github.com/blazerity/admin-webhelper/pull/27  
Контракты: [`w1-contracts.md`](w1-contracts.md)

## Cloud agents (parallel)

| Agent | Role | Cloud agent ID | Status |
| --- | --- | --- | --- |
| A1 | Tech Lead / nav ADR | `bc-7e7eab5f-6a11-5cc9-8490-e7910ccda3c9` | **merged** (`feature/w1-01-navigation-ia`) |
| A2 | Frontend/UX | `bc-06732a81-d803-5b7c-896e-cf4ae491fe63` | **merged** (`cursor/w1-02-operator-ui-fe63`) |
| A3 | Backend ops | `bc-aee33f9d-bba4-5402-bd51-079250a78505` | **merged** (`feature/w1-03-network-ops-backend`) |
| A4 | Authz prep | `bc-380a2cb7-062d-510e-a2a2-41badc6f1260` | **merged** (`cursor/w1-authz-prep-1260`) |
| A5 | QA / runbooks | `bc-c8563905-52db-550a-b5ca-6c4b35e4f6b4` | **merged** (`feature/w1-09-regression-checklist`) |

## Merge order (coordinator)

1. ~~A1 docs (ADR)~~ ✅  
2. ~~A4 authz shim + ADR~~ ✅  
3. ~~A5 runbooks~~ ✅  
4. ~~A3 backend~~ ✅  
5. ~~A2 UI~~ ✅ (merged before A3; wire-up verified after)  
6. ~~pytest + VERSION~~ ✅ `0.3.0`, **219 passed**

## W1 DoD

- [x] Nav topbar + slim settings (A1 ADR + A2)
- [x] Network summary API + map widgets
- [x] `/search/suggest` + autocomplete
- [x] Command presets + script-from-card
- [x] Scheduler health on settings
- [x] Authz shim + ADR roles (runtime in W3)
- [x] Regression checklist / runbooks
- [x] No breaking URL; pytest green
