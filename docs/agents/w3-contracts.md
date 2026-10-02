# W3 — контракты и владение файлами

Координатор: `cursor/w3-implementation-85f5` (база — W2 `cursor/w2-implementation-85f5`).
Агенты — отдельные ветки; мержит координатор.

Старт: **0.4.0**. Цель: сигналы + Authz v2 без breaking changes. Цель VERSION: **0.5.0**.

---

## Владение

| Агент | Можно менять | Нельзя |
| --- | --- | --- |
| **A1** | `docs/adr/**`, `docs/agents/**`, `docs/ROADMAP.md`, `VERSION` в конце | продуктовый код кроме merge |
| **A4** | `app/authz.py`, `app/models/user.py`, `app/models/` (audit), `app/services/ldap_service.py`, `app/services/audit_service.py`, `app/config.py`, `.env.example`, `migrations/versions/0009_*`, `tests/test_authz.py`, `tests/test_audit*`, правки enforce в `routes/scripts.py` / password_expiry для ролей | templates/static (кроме согласования с A2) |
| **A3** | `app/services/watchlist_service.py`, `app/services/notification_service.py`, models watchlist/notifications, migration `0010_*` если нужна отдельно от A4, routes devices/admin для watchlist API, scheduler hook, `tests/test_watchlist*`, `test_notification*` | `app/authz.py` |
| **A2** | `app/templates/**`, `app/static/**` | services/routes/models/authz |
| **A5** | `docs/runbooks/**`, доп. тесты матрицы прав | продуктовый scope фич |

Если A4 и A3 нужны разные миграции — **одна** ревизия `0009` делает A4 (roles flags + audit + script.is_published); A3 добавляет `0010` (watchlist + notifications). Порядок merge: A4 → A3 → A2 → A5.

---

## W3-01 / W3-02 — роли (A4)

### Config (`.env` / `config.py`)

Уже есть черновик в `.env.example`:

- `LDAP_VIEWER_GROUP`
- `LDAP_OPERATOR_GROUP`
- `LDAP_PASSWORD_VIEWER_GROUP`
- `LDAP_ADMIN_GROUP` (как сейчас)

### User flags (additive columns, обновляются при логине как `is_admin`)

```
is_viewer: bool
is_operator: bool
is_password_viewer: bool
```

`is_admin` остаётся каноном полного admin.

### Helpers в `authz.py`

```
user_has_role(user, role) -> bool  # admin implies all
user_can_run_scripts(user) -> bool
  # admin OR is_operator  (CRUD скриптов по-прежнему admin_required)
user_can_run_script(user, script) -> bool
  # admin OR (operator AND script.is_published)
user_can_view_password_expiry(user) -> bool
  # admin OR is_password_viewer
```

Viewer: только чтение; **не** может ping? Roadmap: viewer = read only. Значит diagnostics ping/tracert для viewer → 403. Operator и обычный user с sector_access (без viewer-only) сохраняют ping.

Уточнение совместимости:

- Пользователь **без** новых ролей, с `sector_access` — поведение как сейчас (ping да, scripts нет).
- `is_viewer=True` и **не** operator/admin — только чтение (без ping/tracert/command/script).
- `is_operator=True` — ping + published scripts на доступных секторах; CRUD scripts — нет.
- Admin — всё.

### Script.is_published

Additive column `scripts.is_published` BOOL NOT NULL DEFAULT TRUE (существующие скрипты доступны operator).  
Admin UI checkbox на форме скрипта (A2 разметка; A4 route/context).

### LDAP login

В `ldap_service` при синке user выставлять флаги по CN групп (casefold), аналогично admin.

### Enforce points

- `user_can_run_scripts` / `user_can_run_script` на всех script run routes + device script + bulk script
- Script list для operator: только чтение списка + run published (не create/edit/delete)
- `password_expiry.dashboard` — `user_can_view_password_expiry`; settings — admin only
- Topbar: Скрипты видны admin **или** operator; Пароли AD — admin или password_viewer (A2)
- Harden `_render_form` selects → `accessible_*` only

---

## W3-03 — admin_audit_log (A4)

Таблица:

```
id, created_at, actor_user_id, actor_username, action, entity_type, entity_id, detail (text/json)
```

`audit_service.log(actor, action, entity_type, entity_id, detail="")`

Писать из критичных POST: sector CRUD, admin settings save, script create/edit/delete, update/rollback.

Минимум GET `/admin/audit` для admin (простой список) — template A2 или минимальный stub A4.

---

## W3-04 — Watchlist (A3)

Таблица `device_watchlist`:

```
id, user_id, device_id, offline_minutes INT DEFAULT 15, created_at
UNIQUE(user_id, device_id)
```

API:

- `POST /devices/<id>/watch` — add/update (form: offline_minutes, csrf)
- `POST /devices/<id>/unwatch`
- List on device detail context `watching: bool`

При poll/scheduler (после обновления статусов или отдельный проход): если device offline дольше N минут и в watchlist — создать notification (dedupe: не чаще 1 раза пока снова online).

---

## W3-05 — In-app notifications (A3 data + A2 UI)

Таблица `notifications`:

```
id, user_id, kind, title, body, link_url, created_at, read_at NULL
```

Kinds: `device_offline`, `script_failed`, `poll_error` (минимум device_offline из watchlist; script_failed — опционально из failed runs автора).

API:

- `GET /api/notifications` → `{items: [...], unread: N}`
- `POST /api/notifications/read` → mark ids or all

UI A2: колокольчик в topbar + выпадающая лента.

---

## W3-06 — Пагинация actions & accounts (A4 service + A2 UI)

Query: `?page=1&per_page=50&q=&kind=`  
Сервис возвращает `{items, total, page, per_page}`.  
Шаблоны — пагинация Bootstrap.

---

## W3-07 — Матрица тестов (A5 + A4)

Матрица admin / operator / viewer / password_viewer / sector_user / no_access в pytest.
Чеклист `docs/runbooks/w3-regression-checklist.md`.

---

## DoD

- [ ] W3-01…W3-07 в интеграции
- [ ] pytest зелёный
- [ ] миграции additive 0009/0010
- [ ] VERSION 0.5.0
- [ ] старые админы LDAP_ADMIN_GROUP без регресса
