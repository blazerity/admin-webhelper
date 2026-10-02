# W4 — контракты

Координатор: `cursor/w4-implementation-85f5` (база W3). Цель VERSION **0.6.0**.

## Scope

| ID | Owner | Суть |
| --- | --- | --- |
| W4-01 | A3 | `/api/v1/...` aliases на существующие JSON (summary, map/status, batches, notifications, search/suggest, command-presets, bulk) — старые пути остаются |
| W4-02 | A2+A5 | Self-host Bootstrap+fonts **или** документированный offline fallback в README/runbook |
| W4-03 | A3+A2 | CSV export: accounts, actions, poll runs (admin) |
| W4-04 | A5 | Runbooks: device-offline (дополнить), wmi-no-response, psexec-failed, password-ad-mail |
| W4-05 | A3 | Spike Linux remote — только `docs/spikes/linux-remote.md`, без prod кода |
| W4-06 | A3 | Оценка/заготовка архива `device_history` — документ; код только если простой additive |

## DoD

pytest green; VERSION 0.6.0; no breaking URLs; runbooks linked.
