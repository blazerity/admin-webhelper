# ADR 002 — Authz v2 (роли поверх LDAP, additive)

Статус: **принято для W1-подготовки** (полный runtime — W3).  
Владелец: A4 / P4. Review: A1 / P1 обязателен на любой PR по authz.

## Контекст

Сейчас права бинарны: `User.is_admin` (из `LDAP_ADMIN_GROUP`) и ACL `sector_access` для non-admin. Non-admin видит сектора/устройства по ACL, может ping/tracert, но не библиотеку скриптов и не удалённую команду. Оператору без полного admin нельзя делегировать «разрешённые скрипты на своих секторах»; журнала админ-действий нет.

## Решение

Ввести **additive** роли поверх текущих флагов, без замены `is_admin` и `sector_access`:

| Роль | LDAP env (черновик) | Смысл |
| --- | --- | --- |
| `viewer` | `LDAP_VIEWER_GROUP` | Только чтение карты / устройств / истории в рамках `sector_access` |
| `operator` | `LDAP_OPERATOR_GROUP` | Ping/tracert + запуск **опубликованных** скриптов на доступных секторах (CRUD скриптов — admin) |
| `password_viewer` | `LDAP_PASSWORD_VIEWER_GROUP` | Отчёт паролей AD без SMTP/settings |
| `admin` | `LDAP_ADMIN_GROUP` (уже есть) | Как сейчас; канонический источник `is_admin` |

Правила совместимости:

1. `is_admin` и `sector_access` **остаются** источником прав в W1–W2.
2. Членство в `LDAP_ADMIN_GROUP` ведёт себя как раньше (полный admin).
3. Новые группы в `.env` — черновик ключей; **runtime mapping — W3** (`W3-01`).
4. UI-скрытие пунктов меню **не** считается защитой: каждый POST/mutating GET проверяется на сервере.

## W1 vs W3

| Волна | Что делаем |
| --- | --- |
| **W1 (этот ADR + shim)** | Документ ролей; `.env.example` ключи; `user_can_run_scripts(user)` → `is_admin`; enforce script-from-card как admin-only; список дыр селектов |
| **W2** | Authz на bulk под будущего operator (`W2-06`) |
| **W3** | Модель ролей + LDAP mapping; operator → опубликованные скрипты; `password_viewer`; `admin_audit_log`; harden селектов |

В W1 **все** запуски скриптов (библиотека и карточка устройства) остаются **admin-only**. Operator не включается до W3.

## Shim `user_can_run_scripts`

```text
user_can_run_scripts(user) -> bool
  W1: return is_admin
  W3: admin OR (operator AND script published AND sector visible)
```

Маршруты W1 должны опираться на этот хелпер (или эквивалентный `admin_required`), чтобы W3 расширил одно место, а не размазал проверки.

## Enforcement checklist — запуск скрипта с карточки (W1-07)

Контракт: `POST /devices/<id>/scripts/run` (form: `csrf_token`, `script_id`).

| # | Проверка | Ожидание W1 |
| --- | --- | --- |
| 1 | Не аутентифицирован | redirect login / 401-паттерн Flask-Login |
| 2 | Аутентифицирован, **не** admin (`user_can_run_scripts` = false) | **403** (нельзя обойти UI прямым POST) |
| 3 | Admin, устройство чужого сектора | 403/404 как `get_visible_device_or_404` |
| 4 | Admin, устройство доступно, script существует | `start_script_on_devices` → redirect на `/scripts/runs/<id>` |
| 5 | Список скриптов в context вкладки commands | только если admin (иначе `[]`) — A3 |
| 6 | Существующие `/scripts/*` CRUD и `/run` | `admin_required` / `user_can_run_scripts` |
| 7 | `POST /devices/<id>/command` | admin-only (как сейчас) |

Прямой POST без admin на карточный route → **403**. Когда A3 приземлит маршрут, тест в `tests/test_authz.py` должен ловить регресс (skip, пока route отсутствует).

## Селекты: дыры для W2/W3 harden

Точки, где в форму сейчас попадают **все** устройства/секторы (не `get_visible_*` / `accessible_*`). Зафиксировано для harden в W2 bulk / W3 role rollout:

| Место | Сейчас | Риск | Волна |
| --- | --- | --- | --- |
| `app/routes/scripts.py` → `_render_form` | `Device.query.order_by(...).all()` и `Sector.query...all()` | В HTML-селекте видны чужие хосты/сектора (execute всё же режет через `get_visible_*`) | **W3** (и частично W2 для bulk UI) |
| Будущий multi-select / bulk bar на карте | — | Нужен authz на выбор **и** на execute (`W2-06`) | **W2** |
| Context `scripts=` на `devices.detail` (tab commands) | A3: только admin получает список | Не отдавать чужие script id non-admin | **W1** (A3) + **W3** для operator |
| Любой будущий JSON presets / script catalog | — | Не полагаться на скрытие кнопки | **W3** |

Правило: селект = тот же контур видимости, что и execute. Пока селект шире execute — это известный UX/утечка inventory для admin-only форм; для non-admin формы скриптов недоступны (403), поэтому W1 приемлемо.

## Последствия

- Нет breaking change URL и LDAP_ADMIN_GROUP.
- W3 добавит чтение новых env и флаги/хелперы ролей; миграции только additive (`admin_audit_log` и т.п.).
- P1 review обязателен на authz/credentials PR.
