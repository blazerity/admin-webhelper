# W1 — контракты и владение файлами

Координатор: интеграционная ветка `cursor/w1-implementation-2c6a`.
Агенты работают **каждый в своей cloud-ветке** от этой базы; мержит только координатор.

Статус продукта на старте: **0.2.6**. Цель волны — функциональный операторский контур без breaking changes.

---

## Владение файлами (жёстко)

| Агент | Можно менять | Нельзя трогать |
| --- | --- | --- |
| **A1** | `docs/adr/**`, правки статусов в `docs/ROADMAP.md` / `docs/agents/**` | любой `app/`, `tests/`, `deploy/` |
| **A2** | `app/templates/**`, `app/static/**` | `app/services/`, `app/routes/`, `app/models/`, `app/authz.py` |
| **A3** | `app/services/**`, `app/routes/devices.py`, `app/routes/admin.py`, `app/routes/scripts.py`, `app/routes/search.py`, `app/routes/diagnostics.py`, новые `tests/test_*summary*`, `test_*preset*`, `test_*health*`, `test_search*` | `app/templates/**`, `app/static/**`, `app/authz.py` |
| **A4** | `app/authz.py`, `docs/adr/authz-v2.md`, `.env.example` (только новые ключи ролей без секретов), `tests/test_authz.py` | templates/static; не рефакторить ping/script pipeline |
| **A5** | `docs/runbooks/**`, доп. тесты без пересечения с A3/A4 именами, правки flaky в существующих тестах только если не ломают чужой WIP | продуктовый UI/сервисы фич W1 (не дублировать A2/A3) |

Конфликт одного файла = остановиться и описать в PR; не «перебивать» чужой код.

---

## API-контракты (A3 реализует, A2 потребляет)

### 1. Сводка сети — `GET /api/network/summary`

Session + login required. CSRF не нужен (GET).

```json
{
  "devices_total": 0,
  "devices_online": 0,
  "devices_offline": 0,
  "devices_unknown": 0,
  "last_poll": {
    "id": 1,
    "started_at": "ISO-8601",
    "finished_at": "ISO-8601|null",
    "mode": "scheduled|manual|cli",
    "scanned": 0,
    "online": 0,
    "offline": 0,
    "errors": 0,
    "error": ""
  },
  "failed_script_runs_24h": 0
}
```

Видимость устройств — через `accessible_sector_ids` / те же правила, что карта. `last_poll` — глобальный последний `network_poll_runs` (как в admin); для non-admin поля poll можно отдавать (это не секрет), либо `null` если политика ужесточится — по умолчанию **отдаём**.

Реализация: сервис (например `network_summary_service.py`) + тонкий blueprint или route в `devices`/`admin`. Предпочтительно `devices` или отдельный маленький blueprint, зарегистрированный в `create_app`.

### 2. Health scheduler — данные для `/admin/settings`

Сервис `get_scheduler_health()` → dict:

```json
{
  "last_success_at": "ISO-8601|null",
  "last_run": { "...как last_poll..." },
  "stale": true,
  "stale_after_seconds": 600
}
```

`stale`: нет успешного finished poll дольше `max(poll_interval * 2, 600)` (или разумный default).  
A3 передаёт dict в шаблон **через route** `admin.settings` (добавить в context).  
**Разметку** блока health в `admin/settings.html` делает **A2** (используя переменную `scheduler_health`).

### 3. Пресеты команд — `GET /api/command-presets`

Только admin (как удалённые команды сейчас).

```json
{
  "presets": [
    {"id": "whoami", "label": "whoami", "command": "whoami"},
    {"id": "ipconfig", "label": "ipconfig /all", "command": "ipconfig /all"},
    {"id": "hostname", "label": "hostname", "command": "hostname"},
    {"id": "netstat", "label": "netstat -ano", "command": "netstat -ano"}
  ]
}
```

Данные — константа/модуль в `services/`, не хардкод только в HTML. A2 может также зашить те же 4 пресета в data-атрибуты как progressive enhancement, но источник истины — API/модуль A3. Для SSR удобнее: A3 кладёт `command_presets` в context вкладки commands (route `devices.detail`) — **это предпочтительнее второго HTTP**. Тогда `GET /api/command-presets` — опциональный JSON для будущего; минимум W1: **context в detail** + модуль констант.

### 4. Запуск скрипта с карточки

- `POST /devices/<id>/scripts/run` (новый route в `devices` или `scripts`)
- form: `csrf_token`, `script_id`
- authz: **только admin** в W1 (как сейчас script library); проверка `user_can_access_device`
- внутри: `Script.query.get` + `start_script_on_devices(script, user, [device])`
- redirect на `/scripts/runs/<id>` первого run (как diagnostics)

A4 проверяет, что прямой POST без admin → 403; device чужого сектора → 403/404 как принято.

Список скриптов для селекта: A3 в `devices.detail` для tab commands передаёт `scripts=Script.query...` только если admin (иначе []).

### 5. Suggest

Сейчас есть `GET /search/api`.  
A3 добавляет **alias** `GET /search/suggest` с тем же JSON (или урезанным: id, hostname, ip, url, status) — без удаления `/search/api`.  
A2: autocomplete dropdown на карте, дергает suggest (fallback `/search/api`).

---

## IA навигации (A1 фиксирует в ADR; A2 реализует)

**Спека (W1-01 Done):** [`docs/adr/001-operator-navigation.md`](../adr/001-operator-navigation.md).

**Topbar (оператор):**

1. Карта сети → `devices.map`
2. Действия → `actions.list_actions`
3. Учётные записи → `accounts.list_accounts`
4. Скрипты → `scripts.list_scripts` (**только admin**; non-admin пункт скрыт)
5. Пароли AD (отчёт) → `password_expiry.dashboard` (**только admin**)
6. Настройки → `sectors.list_sectors` (вход в settings-layout)

**Settings sidebar:** Сектора, Параметры, Обновления, Экран входа, Пароли AD (settings). **Убрать** Действия / УЗ / Скрипты (они в topbar). Non-admin: topbar Карта + Действия + УЗ + Настройки(сектора); в sidebar только Сектора.

Старые URL не удалять. Active-state — в ADR §6.

---

## DoD волны (координатор закрывает)

- [ ] Все W1-01…W1-09 сделаны и смержены в `cursor/w1-implementation-2c6a`
- [ ] pytest зелёный на интеграционной ветке
- [ ] Нет breaking URL
- [ ] VERSION bump до 0.3.0 (minor — операторский контур) делает A1/координатор в конце
- [ ] README/ARCHITECTURE обновлены при необходимости
