# A3 — Backend сети и операций (P3)

**Миссия:** устройства, опрос, поиск, скрипты/PsExec, batch, health опроса — без HTTP-логики в сервисах наоборот.

**Читать сначала:** `docs/ARCHITECTURE.md` (поток опроса), `app/services/ping_service.py`, `script_service.py`, `search_service.py`, `scheduler_service.py`, `app/routes/devices.py`, `diagnostics.py`, `scripts.py`, `admin.py`.

## Backlog

### W1 (сейчас)

| ID | Задача | Слой |
| --- | --- | --- |
| **W1-03** | API/сервис сводки сети | сервис агрегатов из `devices`, `network_poll_runs`, failed `script_runs` + тонкий route/JSON для карты |
| **W1-06** | Пресеты команд как данные | константа/seed (не только HTML); UI — A2 |
| **W1-07** | Быстрый старт скрипта на `device_id` | обёртка над `start_script_on_devices`; route на карточке |
| **W1-08** | Health scheduler | чтение последнего `network_poll_runs` (+ опционально heartbeat setting); блок на `/admin/settings` |

### W2

W2-02 bulk ping/script + batch status API; расширение `/map/status` для новых устройств.

### W3–W4

W3-04 watchlist/offline alerts (с A4/A2); W4-01 `/api/v1/` aliases; W4-03 CSV; W4-05 Linux spike; W4-06 archive `device_history` при необходимости.

## Жёсткие запреты

- Не ломать `run_network_poll` и чтение WMI-учётки **в главном потоке**.
- Не менять контракт polling `script_runs` status без aliases.
- Маршрут не вызывает ping/LDAP/PsExec напрямую — только через `services/`.
- Селекты скриптов — через visible devices/sectors (authz A4), не «все из БД».

## Тесты

Минимум: расширить/добавить pytest рядом с `tests/test_script_service.py`, `test_ping_service.py`, `test_search_service.py`, `test_network_poll_runs.py`.

Сценарии: empty sector, offline host, сводка без устройств, health без последнего poll.

## Пересечения

- UI — A2; права bulk/script — A4; миграции/релиз — A1; deploy heartbeat — A5.

## DoD агента на W1

- [ ] Сводка покрыта тестами; JSON стабилен для A2.
- [ ] Пресеты отдаются как данные.
- [ ] Скрипт с одного device_id работает через существующий pipeline `script_runs`.
- [ ] Health на settings не ломает ручной poll / scheduler.
