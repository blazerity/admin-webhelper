# W1 orchestration status

Интеграционный PR: https://github.com/blazerity/admin-webhelper/pull/27  
Контракты: [`w1-contracts.md`](w1-contracts.md)

## Cloud agents (parallel)

| Agent | Role | Cloud agent ID | Status |
| --- | --- | --- | --- |
| A1 | Tech Lead / nav ADR | `bc-7e7eab5f-6a11-5cc9-8490-e7910ccda3c9` | running |
| A2 | Frontend/UX | `bc-06732a81-d803-5b7c-896e-cf4ae491fe63` | running |
| A3 | Backend ops | `bc-aee33f9d-bba4-5402-bd51-079250a78505` | running |
| A4 | Authz prep | `bc-380a2cb7-062d-510e-a2a2-41badc6f1260` | running |
| A5 | QA / runbooks | `bc-c8563905-52db-550a-b5ca-6c4b35e4f6b4` | running |

## Merge order (coordinator)

1. A1 docs (ADR) — no code conflicts  
2. A4 authz shim + ADR  
3. A5 runbooks  
4. A3 backend APIs/tests  
5. A2 templates/static last (depends on A3 context vars)  
6. Full pytest + fix conflicts + VERSION bump

Update this file as agents complete.
