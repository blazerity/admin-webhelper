# Распределение задач по 5 агентам

Рабочий пакет для запуска улучшений bAWH по [`docs/ROADMAP.md`](../ROADMAP.md), с опорой на [`docs/ARCHITECTURE.md`](../ARCHITECTURE.md) и [`README.md`](../../README.md).

Текущая версия продукта: **0.2.6**. Первая поставка — **волна W1**.

## Агенты

| Агент | Роль roadmap | Основная зона кода | Lead-направления |
| --- | --- | --- | --- |
| [A1](A1-tech-lead.md) | P1 Tech Lead | `docs/`, `VERSION`, review контрактов | целостность, IA, релизы |
| [A2](A2-frontend-ux.md) | P2 Frontend/UX | `app/templates/`, `app/static/` | A. Операторский контур |
| [A3](A3-backend-ops.md) | P3 Backend сети/операций | `app/services/`, `app/routes/` (devices/scripts/admin/search) | B. Быстрые операции, часть C |
| [A4](A4-authz-audit.md) | P4 Authz / аудит / AD | `app/authz.py`, LDAP, password expiry | D. Authz + аудит |
| [A5](A5-qa-platform.md) | P5 QA / платформа / docs | `tests/`, `deploy/`, `docs/runbooks/` | E. Платформа |

## Порядок старта W1 (чтобы не мешать друг другу)

```text
T0  A1: W1-01 схема навигации (контракт topbar vs settings)
T0  A3: W1-03 сервис сводки + W1-08 health scheduler
T0  A5: W1-09 черновик регресс-чеклиста
T0  A4: ADR ролей (код ролей — W3; для W1 только точки enforce на W1-07)
T1  A2: W1-02 навигация по схеме A1; затем W1-04/W1-05
T1  A3+A2: W1-06 пресеты, W1-07 скрипт с карточки (+ A4 на authz)
T2  A1: W1-10 review, VERSION, merge-готовность
```

Правило из roadmap: **один Lead на фичу**; параллельно не пилить ту же фичу без согласования Lead.

## Ветки и PR

- База: актуальный `main`.
- Имя ветки: `feature/<ID>-short-name` (например `feature/w1-03-network-summary`).
- Один ID волны = один PR, когда возможно.
- Слои: маршрут → сервис → модель; маршрут не пингует и не ходит в LDAP.
- Миграции: только новые Alembic-ревизии (сейчас последняя `0008_network_poll_runs`).
- UI: текущий Manrope / teal / Bootstrap 5 — без новой дизайн-системы.

## Контракты между агентами (W1)

| Контракт | Владелец | Потребитель | Суть |
| --- | --- | --- | --- |
| IA навигации | A1 | A2 | какие пункты в topbar, какие в settings-layout |
| JSON сводки сети | A3 | A2 | counts online/offline, last poll, failed runs |
| `/search/suggest` | A3 (стабильность) | A2 | autocomplete на карте |
| Пресеты команд | A3 (данные) | A2 (UI) | константа/seed, не хардкод только в HTML |
| Запуск скрипта с device | A3 | A2, A4 | обёртка над `start_script_on_devices` + authz |
| Health scheduler | A3 | A5 | блок на `/admin/settings` |
| DoD волны | A1 | все | README/ARCHITECTURE/VERSION, нет breaking changes |

## Что не делать в W1

См. §9 roadmap: SPA-rewrite, публичный SaaS, замена PsExec, мобильное приложение, auto-remediation, Linux remote как prod-фича.
