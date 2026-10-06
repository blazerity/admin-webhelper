# Архитектура bAWH (актуально для v1.7.1)

> **Для ИИ и разработчиков:** это каноническая карта кода.
> Перед поиском по репозиторию прочитай файл целиком — здесь слои, точки входа,
> модели, сервисы, маршруты, authz, фоновые задачи и соглашения.
> Установка и ops — в [README.md](../README.md).
> Текст скриптов для формы «Скрипты» — в [SCRIPTS.md](SCRIPTS.md).

**Продукт:** внутренний веб-помощник администратора сети (карта устройств,
секторы/CIDR, ICMP+WMI+TCP/SNMP опрос, диагностика, скрипты на Windows через PsExec,
веб-VNC (экспериментально), LDAP-вход, отчёт по сроку паролей AD и in-app уведомления).
Рассчитан на корпоративную LAN, не для публикации в интернет.

**Версия:** файл [`VERSION`](../VERSION) → `1.7.1` (читает `app/version.py`).

**Стек:** Flask 3 SSR (Jinja2) · SQLAlchemy 2 / Flask-SQLAlchemy · PostgreSQL
(prod; SQLite допустим локально) · Flask-Login · Flask-WTF CSRF · APScheduler
(отдельный процесс) · ldap3 · cryptography Fernet · pypsexec · impacket (WMI) ·
Gunicorn + Nginx · Bootstrap 5 (vendorized) · noVNC + `websockets` (процесс `bawh-vnc`).

**Нет в репозитории:** Alembic / Flask-Migrate, Redis, Celery, WebSocket *в Flask*,
Telegram, S3, SPA-фреймворка, Pydantic. Каталог `tests/` тоже не в git
(локальный `unittest`, без pytest в requirements). Веб-VNC — отдельный процесс, не Gunicorn.

---

## 1. Слои и правило зависимости

```
HTTP / CLI / scheduler
        ↓
  routes/  (или scheduler_service / flask CLI)
        ↓
  services/   ← бизнес-логика; без Flask request/response
        ↓
  models/ + PostgreSQL
```

| Слой | Каталог / файл | Правило |
| --- | --- | --- |
| Маршруты | `app/routes/` | Только HTTP: форма → сервис → шаблон/JSON. **Не** пингуют и не ходят в LDAP/PsExec напрямую. |
| Сервисы | `app/services/` | Вся логика: опрос, LDAP, WMI, PsExec, УЗ, экспорт. Вызываются из веба, планировщика и CLI. |
| Модели | `app/models/` | Таблицы SQLAlchemy. Без HTML и сетевых вызовов. |
| Схема БД | `app/schema.py` | `ensure_schema()`: `create_all` для недостающих таблиц + seed `action_kinds` + идемпотентные data-fix (флаг в `app_settings`). |
| Authz | `app/authz.py` | Единственное место ACL секторов/устройств/script_runs и ролей. |
| Утилиты | `app/utils.py` | `utcnow` / `as_utc` / `ilike_pattern` / `clip` / пагинация. |

**Инвариант:** маршрут не делает side-effect сеть/LDAP; сервис не рендерит HTML.

---

## 2. Точки входа

| Точка | Файл / команда | Что делает |
| --- | --- | --- |
| Веб (prod) | `wsgi.py` → `create_app()` → Gunicorn `wsgi:app` | Только HTTP. Опрос здесь **запрещён**. |
| Веб (dev) | `python wsgi.py` (:8000) или `flask --app wsgi run` | То же. |
| Планировщик | `python -m app.scheduler_worker` | `create_app()` + `start_scheduler(app)`; опрос сети/железа. |
| Отчёты паролей | `python -m app.password_report_worker` | `start_password_report_scheduler(app)`. |
| Отчёты о ПК | `python -m app.pc_report_worker` | `start_pc_report_scheduler(app)`. |
| VNC-прокси | `python -m app.vnc_worker` | WebSocket→TCP, без Flask/БД; билет `SECRET_KEY`. |
| Flask CLI | `flask --app wsgi …` | `poll`, `hardware-poll`, `archive-logs`, `password-expiry [--dry-run]`, `sector-daily-report [--dry-run]`, `refresh-hostnames [--dry-run]`, `init-db`. |
| Docker | `Dockerfile` | Опциональный web-only образ; scheduler/отчёты — отдельно. |

Фабрика: `app/__init__.py` → `create_app(config_name)`.

Порядок в `create_app`:

1. `load_dotenv()` → конфиг из `APP_CONFIG` / `CONFIGS` (`default` | `development` | `testing`)
2. `configure_logging`
3. `db` / `login_manager` / `csrf` (`app/extensions.py` — **без** migrate)
4. `import app.models` + `user_loader`
5. blueprints + error handlers 403/404/500
6. `register_commands` (CLI планировщика) + CLI `init-db`
7. `ensure_schema()` в app context
8. `GET /health` → `{"status": "ok"}` (без auth)
9. template filter `dt`, context `app_name`, shell context

Планировщик в `extensions` **не** создаётся — иначе каждый воркер Gunicorn запустил бы свой опрос.

---

## 3. Каталоги репозитория

```
bAWH/                          # на сервере = /opt/bawh
  VERSION                      # semver (1.7.1)
  wsgi.py                      # WSGI entry
  requirements.txt
  .env.example
  Dockerfile                   # опционально, только web
  README.md                    # установка и ops
  docs/ARCHITECTURE.md         # ← этот файл
  app/
    __init__.py                # create_app
    config.py
    extensions.py              # db, login_manager, csrf
    schema.py                  # ensure_schema (вместо Alembic)
    authz.py
    utils.py
    logging_config.py
    version.py
    run_display.py             # подписи статусов запусков для UI
    scheduler_worker.py
    models/
    services/
    routes/
    templates/
    static/                    # css/, js/, vendor/bootstrap/
  deploy/                      # Debian 12: systemd, nginx, install
  vnc-agents/                  # MSI TightVNC (файлы .msi не в git)
  tests/                       # локально, в gitignore
  logs/                        # runtime (в gitignore содержимое)
```

---

## 4. Диаграмма слоёв

```mermaid
flowchart TB
  subgraph entry["Точки входа"]
    WSGI["wsgi.py<br/>Gunicorn"]
    SCHED["app.scheduler_worker<br/>bawh-scheduler"]
    PWD_W["app.password_report_worker<br/>bawh-password-reports"]
    PC_W["app.pc_report_worker<br/>bawh-pc-reports"]
    CLI["flask CLI<br/>poll · hardware-poll · archive-logs · password-expiry · sector-daily-report · refresh-hostnames · init-db"]
  end

  subgraph app_core["app/"]
    CREATE["create_app()"]
    SCHEMA["schema.ensure_schema()"]
    AUTH["authz.py"]
    UTILS["utils.py"]
  end

  subgraph http["routes/"]
    R_AUTH["auth"]
    R_DEV["devices · search · diagnostics"]
    R_ACC["accounts · actions"]
    R_SEC["sectors"]
    R_SCR["scripts"]
    R_ADM["admin · login_services"]
    R_PWD["password_expiry · sector_daily_report"]
    R_NTF["notifications"]
  end

  subgraph services["services/"]
    S_POLL["ping · discovery · fingerprint · hardware_poll · scheduler"]
    S_ACC["account · action · export · audit"]
    S_ID["ldap · credential · crypto · settings"]
    S_RUN["script · psexec · batch · command_presets"]
    S_PWD["password_expiry* · password_ad · mailer<br/>sector_daily_report* · report_toggle"]
    S_OTH["sector · login_status · network_summary<br/>notification · update · log_archive · net_utils · device_kind"]
  end

  subgraph data["PostgreSQL / models/"]
    M_USER["users · user_ldap_groups · remote_credentials"]
    M_NET["sectors · sector_ranges · sector_access<br/>devices · device_history · network_poll_runs<br/>hardware_poll_runs · device_hardware_history"]
    M_ACC["endpoint_accounts · device_account_history · action_kinds"]
    M_SCR["scripts · script_runs · app_settings · login_services"]
    M_PWD["password_notifications · password_expiry_runs<br/>sector_daily_report_runs"]
    M_NTF["admin_audit_log · notifications"]
  end

  WSGI --> CREATE
  SCHED --> CREATE
  CLI --> CREATE
  CREATE --> SCHEMA
  CREATE --> http
  CREATE --> AUTH
  http --> services
  http --> AUTH
  SCHED --> S_POLL
  SCHED --> S_PWD
  services --> data
  services --> AUTH
  services --> UTILS
  S_POLL --> S_ACC
  SCHEMA --> data
```

---

## 5. Схема БД (без Alembic)

Файл: `app/schema.py`.

```python
def ensure_schema() -> None:
    # 1) если в metadata есть таблицы, которых нет в БД → db.create_all()
    # 2) колонки из моделей, которых нет в существующих таблицах → ALTER TABLE ADD COLUMN
    # 3) data-fix: номиналы ОЗУ/ПЗУ v1.5.1 (флаг app_settings data_fix.hw_gb_nominal_v151)
    # 4) ветка обновлений beta, если ещё не выбирали (data_fix.update_branch_beta_v167)
    # 5) скрипт TightVNC (удаление) в библиотеке (data_fix.seed_tightvnc_script_v176)
    # 6) если action_kinds пуст → seed_action_kinds() + commit
```

Вызывается при каждом старте `create_app` (web и scheduler).  
CLI `flask --app wsgi init-db` — то же (схема уже поднята фабрикой; команда печатает `database ready`).  
Установщик и self-update вызывают `flask init-db`.

**Таблицы:** `create_all` создаёт только **недостающие** таблицы. Новая сущность =
модель + экспорт в `models/__init__.py`; при деплое таблица появится на следующем старте.

**Колонки:** `ensure_schema` догоняет недостающие колонки через `ALTER TABLE … ADD COLUMN`
(с `DEFAULT` из `server_default` / скалярного `default`, иначе NOT NULL без default
добавляется как NULL). Это покрывает типичный случай «переустановили `/opt`, Postgres
не чистили» (например `users.is_viewer`). Не делает: rename/drop колонок, смену типа,
новые UNIQUE/FK/индексы на уже существующих колонках — для этого нужен ручной SQL
или пересоздание БД.

**Data-fix v1.5.1:** после опроса на v1.5.0 в `devices` / `device_hardware_history`
лежат «рваные» ГБ (15 ОЗУ, 238/244 диск). При старте 1.5.1 `normalize_stored_capacity_gb`
один раз приводит их к номиналу; маркер `data_fix.hw_gb_nominal_v151` в `app_settings`.
Повторный WMI-опрос для этого не нужен.

**Сид TightVNC:** при старте в библиотеку кладётся неопубликованное тело
«TightVNC (тихое удаление)» из `app/services/tightvnc_install_script.py`.
Старое «TightVNC (тихая установка)» заменяется. MSI админ кладёт в `vnc-agents/`;
при «Подключить» на карточке агент ставится или обновляется (пароль/порт/файрвол).
Маркер `data_fix.seed_tightvnc_script_v176`.

---

## 6. Модели (`app/models/`)

Импорт всех таблиц: `app/models/__init__.py`. Mixin: `TimestampMixin` в `base.py`.

| Модель | Таблица | Файл | Назначение |
| --- | --- | --- | --- |
| `User`, `UserLdapGroup` | `users`, `user_ldap_groups` | `user.py` | Операторы сайта после LDAP. Флаги: `is_admin`, `is_viewer`, `is_operator`, `is_password_viewer`. Группы — для `sector_access`. |
| `Sector`, `SectorRange`, `SectorAccess` | `sectors`, `sector_ranges`, `sector_access` | `sector.py` | Подсети (CIDR) и ACL (subject = username или LDAP group CN). |
| `Device`, `DeviceHistory` | `devices`, `device_history` | `device.py` | Машины + журнал опросов. Идентичность: **только** `serial_number` (WMI). `hostname` — отображение (имя ОС), не ключ. IP — последний адрес, без unique; без SN — заглушка на этом IP. `current_account_id` — кто за ПК. `fingerprint_kind` / `fingerprint_detail` — TCP/SNMP-отпечаток серого адреса. Снимок железа (`cpu_name`, `ram_gb`, `disk_gb`, `os_*`, `hardware_checked_at`) пишет **отдельный** WMI-опрос, не ICMP. |
| `NetworkPollRun` | `network_poll_runs` | `poll_run.py` | Журнал полных прогонов ICMP-опроса. |
| `HardwarePollRun`, `DeviceHardwareHistory` | `hardware_poll_runs`, `device_hardware_history` | `hardware.py` | Журнал ежедневного опроса железа Windows и история смены CPU/ОЗУ/дисков/ОС. |
| `EndpointAccount`, `DeviceAccountHistory` | `endpoint_accounts`, `device_account_history` | `account.py` | УЗ на конечных точках (**не** путать с `users`). |
| `ActionKind` | `action_kinds` | `action.py` | Справочник типов (`ACTION_KIND_SEED` / `seed_action_kinds`). |
| `Script`, `ScriptRun` | `scripts`, `script_runs` | `script.py` | Библиотека и журнал запусков. У скрипта: `run_as`, `is_published`. У run: `run_type`, `batch_id`, статус. |
| `AppSetting`, `RemoteCredential` | `app_settings`, `remote_credentials` | `setting.py` | KV-настройки; per-user Fernet-шифротекст PsExec/пароля входа. |
| `PasswordNotification`, `PasswordExpiryRun` | `password_notifications`, `password_expiry_runs` | `password_expiry.py` | Письма о сроке пароля + снимки прогонов. |
| `SectorDailyReportRun` | `sector_daily_report_runs` | `sector_daily_report.py` | Снимки ежедневного отчёта о ПК (ноутбуки/СБ, железо, кандидаты). |
| `LoginService` | `login_services` | `login_service.py` | Сервисы для блока доступности на `/login`. |
| `AdminAuditLog` | `admin_audit_log` | `audit.py` | Журнал админ-действий. |
| `Notification` | `notifications` | `notification.py` | In-app колокольчик: `script_failed`. |

Не таблицы (enum/константы): `DeviceStatus`, `SessionType`, `RunType`, `RunStatus`,
`RunAs`, `NotificationKind`, `ActionKindCode`.

Seed коды действий: `poll`, `ping`, `tracert`, `command`, `script`, `account_sighting`.

### Кто пишет / кто читает

| Сущность | Таблица | Пишет | Читает |
| --- | --- | --- | --- |
| Оператор сайта | `users` | `ldap_service` | auth, authz |
| УЗ на ПК | `endpoint_accounts` | `account_service` | `/accounts`, карточка устройства |
| Появление УЗ | `device_account_history` | `account_service` | `/actions`, вкладка accounts |
| Типы действий | `action_kinds` | `ensure_schema` / seed | `/actions` |
| Запуски | `script_runs` | `script_service` | `/scripts/runs`, `/actions` |
| Опросы | `device_history` | `ping_service` | вкладка polls |
| Прогоны опроса | `network_poll_runs` | `ping_service.run_network_poll` | `/admin/settings` |
| Прогоны железа | `hardware_poll_runs` | `hardware_poll_service.run_hardware_poll` | `/admin/settings` |
| Снимки железа | `devices` + `device_hardware_history` | `hardware_poll_service` | карточка, вкладка «Оборудование» |
| Аудит админа | `admin_audit_log` | `audit_service` | `/admin/audit` |
| Уведомления | `notifications` | `notification_service` (script_failed) | `/api/notifications` |

---

## 7. Сервисы (`app/services/`)

### Сеть и опрос

| Модуль | Роль |
| --- | --- |
| `net_utils.py` | `parse_range`, `expand_ranges`, `normalize_mac`, `assert_host_or_ipv4`, `resolve_to_ipv4` |
| `ping_service.py` | ICMP; `run_network_poll` (mutex + журнал) → `poll_all_sectors`; `ping_host` / `trace_host` / `check_device` (разовый ICMP → `device_history`); ThreadPoolExecutor. После ICMP+WMI серые адреса — `fingerprint_service` |
| `fingerprint_service.py` | короткий TCP (445/135, 8728/8291, 9100/515, 554) + SNMPv1 sysDescr `public`; без новых зависимостей |
| `device_kind.py` | тип карты: имена AD (`BN***` = ноутбук, `n…`/`w…`/…), WMI-серийник, fingerprint, PTR/OUI; флаги `kind_shows_accounts` / `mac` / `serial` / `hardware` / `commands` («Прочее» ≈ ноутбук) |
| `discovery_service.py` | reverse DNS, ARP MAC, WMI (impacket): serial / MAC / hostname / logged_on_user **и** отдельный `lookup_wmi_hardware` (CPU / RAM / диски / Caption ОС + DisplayVersion из реестра **и** serial/hostname, кто ответил). Имя карточки: WMI `DNSHostName`/`Name`, PTR только если WMI пустой. Учётка: Настройки → Опросы ПК или `DISCOVERY_*` |
| `hardware_info.py` | чистый разбор снимка: ГБ (ОЗУ — ГиБ/планки, диски — этикетка 1000³ + номинал), семейство 10/11/Server, редакция Pro/Enterprise, 25H2/26H2 |
| `hardware_poll_service.py` | ежедневный опрос железа Windows: ping + WMI, снимок на `devices` только если SN зонда совпадает с карточкой (иначе remap на владельца SN или skip — VPN/DHCP). История при изменении. Mutex + `hardware_poll_runs` |
| `hardware_poll_settings.py` | cron / schedule_enabled в `app_settings` (default `0 12 * * *`, включено) |
| `hostname_sweep_service.py` | разовый проход по уже известным `devices`: ping + WMI-имя → UPDATE hostname. Не сливает строки; чужой SN на том же IP — не пишет имя. CLI `refresh-hostnames`, кнопка в Настройки → Опросы ПК |
| `scheduler_service.py` | APScheduler jobs + Flask CLI |
| `network_summary_service.py` | сводка карты + health планировщика |

### Идентичность и секреты

| Модуль | Роль |
| --- | --- |
| `ldap_service.py` | bind/search, mapping LDAP-групп → роли, `upsert_local_user` |
| `crypto_service.py` | Fernet `encrypt` / `decrypt` |
| `credential_service.py` | `get_remote_admin_credentials`, `save_remote_admin_credentials`, `remember_login_password` |
| `settings_service.py` | интервал опроса, WMI creds, update-sudo в `app_settings` |

### УЗ, действия, экспорт

| Модуль | Роль |
| --- | --- |
| `account_service.py` | NetBIOS-нормализация домена, upsert УЗ, sightings, ACL-списки |
| `action_service.py` | лента действий (script_runs + account history) с authz как у run_detail |
| `export_service.py` | CSV (accounts, actions, poll runs) |
| `audit_service.py` | запись/чтение `admin_audit_log` |

### Удалённый запуск и скрипты

| Модуль | Роль |
| --- | --- |
| `psexec_service.py` | pypsexec-сессии, cancel/close. PowerShell: файл в `ADMIN$\Temp` + `cmd.exe -File` (не `-EncodedCommand`, иначе `STATUS_PIPE_BROKEN`); stdout в канал сразу (`on_output` → живой журнал); CLIXML вырезается |
| `vnc_token.py` | itsdangerous-билет: IP/порт из инвентаря, TTL 90 с; прокси без БД |
| `vnc_settings.py` | порт агента, общий пароль Fernet, тумблер шлюза |
| `tls_pem.py` / `tls_settings.py` / `tls_service.py` | PEM из Настройки → HTTPS, файлы в `certs/`, `apply-nginx-tls.sh` |
| `script_service.py` | CRUD/тело скриптов; enqueue на ThreadPoolExecutor в веб-процессе |
| `batch_service.py` | bulk ping/script + статус batch |
| `command_presets.py` | статические пресеты команд |

### Пароли AD

| Модуль | Роль |
| --- | --- |
| `password_expiry_service.py` | оркестрация прогона |
| `password_ad_client.py` | LDAP: пользователи и days-left |
| `password_mailer.py` | SMTP |
| `password_expiry_settings.py` | SMTP общий (UI: Настройки → Общие), bind LDAP модуля, пороги; fallback на `.env` |
| `password_notification_tracker.py` | дедуп писем |
| `password_report_builder.py` | отчёт для админа |
| `report_toggle_service.py` | тумблер расписания отчётов + ensure своей службы |
| `systemd_service.py` | `systemctl` start/enable/restart через sudo-учётку |

### Отчёты о ПК (бывш. «Отчёт по секторам»)

| Модуль | Роль |
| --- | --- |
| `sector_daily_report_service.py` | парк ноутбуков/СБ по секторам, диаграммы железа, кандидаты на замену, пробелы инвентаризации, CSV |
| `sector_daily_report_settings.py` | cron / recipients / schedule_enabled в `app_settings` (ключи `sector_daily_*`) |

### Прочее

| Модуль | Роль |
| --- | --- |
| `sector_service.py` | CRUD секторов, ranges, access |
| `login_service_status.py` | CRUD сервисов экрана входа + ICMP status |
| `notification_service.py` | in-app уведомления |
| `log_archive_service.py` | месячный tar.gz ротированных логов |
| `update_service.py` | выбранная ветка git → backup → pip → `flask init-db` → systemd restart (фон + `app.app_context()`) |

---

## 8. Маршруты (`app/routes/`)

Регистрация: `_register_blueprints` в `app/__init__.py`.

### Auth — `auth.py`

| Method | Path | Примечание |
| --- | --- | --- |
| GET/POST | `/login` | LDAP; шифрует пароль входа для fallback PsExec |
| POST | `/logout` | `@login_required` |

### Devices — `devices.py` (без prefix)

| Method | Path | Примечание |
| --- | --- | --- |
| GET | `/` | Карта сети |
| GET | `/map/status` | JSON статуса карты |
| POST | `/api/map/bulk/ping` | Bulk ping (нужны права diagnostics) |
| POST | `/api/map/bulk/script` | Bulk script (operator + published) |
| GET | `/api/batches/<batch_id>` | Статус batch |
| GET | `/api/network/summary` | Сводка сети |
| GET | `/api/command-presets` | Пресеты (**admin**) |
| GET | `/devices/<id>` | Карточка: вкладки по типу — overview (+ accounts у ноут/СБ/прочее; commands/hardware у ноут/СБ/сервер/прочее; polls у всех; SN только ноут/СБ). Кнопка «Рабочий стол» у admin/operator на Windows-подобных |
| GET | `/devices/<id>/vnc` | Экспериментальный noVNC: билет 90 с, цель только `Device.ip` + порт 5900–5999 |
| POST | `/devices/<id>/hardware-poll` | Разовый WMI-опрос железа (**admin**) |
| POST | `/devices/<id>/scripts/run` | Скрипт с карточки |

### Diagnostics — `diagnostics.py`

| Method | Path | Auth |
| --- | --- | --- |
| POST | `/devices/<id>/check` | diagnostics — быстрый ICMP → `device_history` + обновление статуса |
| POST | `/devices/<id>/ping` | diagnostics — diagnostic run в `script_runs` |
| POST | `/devices/<id>/tracert` | diagnostics |
| POST | `/devices/<id>/command` | **admin** |

### Sectors — prefix `/sectors`

CRUD: list, new, create, detail, edit, update, delete (mutating — `admin_required`).

### Search — `search.py`

| GET | `/search` | редирект на карту (старые закладки) |

Поиск на карте — клиентский фильтр (`static/js/map.js`), отдельных JSON-endpoint нет.

### Scripts — prefix `/scripts`

CRUD (admin); list/run (operator + `is_published`); `/runs/<id>`, `/status`, cancel, close-session; `/batches/<batch_id>`.

### Accounts — prefix `/accounts`

List, `export.csv`, detail (только видимые по ACL).

### Actions — prefix `/actions`

Лента + `export.csv`.

### Admin — prefix `/admin` (всё `admin_required`)

| Method | Path |
| --- | --- |
| GET/POST | `/admin/settings` | UI-разделы: `?section=general\|polls\|vnc\|tls` (бывш. монолит «Параметры») |
| GET | `/admin/poll-runs/export.csv` |
| POST | `/admin/poll-run` |
| GET | `/admin/hardware-poll-runs/export.csv` |
| POST | `/admin/hardware-poll-run` |
| POST | `/admin/refresh-hostnames` |
| GET/POST | `/admin/updates` |
| GET | `/admin/audit` | редирект на Настройки → Действия и аудит |

### Password expiry — prefix `/password-expiry`

| Method | Path | Auth |
| --- | --- | --- |
| GET | `/` | `password_viewer` |
| POST | `/toggle` | admin — тумблер рассылки + ensure `bawh-password-reports` |
| GET/POST | `/settings`, POST `/run`, `/pause`, `/notify` | admin |

### Отчёты о ПК — prefix `/pc-reports` (legacy `/sector-daily-report` → 301)

| Method | Path | Auth |
| --- | --- | --- |
| GET | `/` | admin — дашборд парка ПК + диаграммы + тумблер |
| POST | `/toggle` | admin — тумблер рассылки + ensure `bawh-pc-reports` |
| GET/POST | `/settings`, POST `/run` | admin |
| GET | `/export.csv` | admin — CSV парка / кандидатов / пробелов |

### Login services — `login_services.py`

CRUD `/login-services/…` (admin); публичный `GET /api/login-services/status`.

### Notifications — `notifications.py`

| GET | `/api/notifications` |
| POST | `/api/notifications/read` |

### Открытые без логина

- `GET /health`
- `GET /api/login-services/status`
- `GET/POST /login`

---

## 9. Авторизация и безопасность

### Вход

`POST /login` → `ldap_service.authenticate` → `upsert_local_user` (роли + группы) →
опционально `remember_login_password` (Fernet) → `login_user` → cookie-сессия.

### Сессия / CSRF

- Flask-Login cookie: `HttpOnly`, `SameSite=Lax`, опционально `Secure` (`SESSION_COOKIE_SECURE`)
- CSRF глобально (Flask-WTF); в `TestingConfig` выключен
- Open-redirect guard: только относительные `next`

### Authz (`app/authz.py`)

| Роль | LDAP env | Флаг `User` | Смысл |
| --- | --- | --- | --- |
| `admin` | `LDAP_ADMIN_GROUP` | `is_admin` | Всё (`user_has_role` считает admin обладателем любой роли) |
| `viewer` | `LDAP_VIEWER_GROUP` | `is_viewer` | Чтение карты/устройств в рамках `sector_access`; **без** ping/скриптов |
| `operator` | `LDAP_OPERATOR_GROUP` | `is_operator` | Ping/tracert + веб-VNC + запуск **опубликованных** скриптов; CRUD скриптов — только admin |
| `password_viewer` | `LDAP_PASSWORD_VIEWER_GROUP` | `is_password_viewer` | Отчёт паролей AD; SMTP/settings — admin |

Базовые правила:

- Admin видит всё.
- Non-admin: сектора/устройства по `sector_access` (username или CN группы из `user_ldap_groups`).
- `script_runs`: admin, автор, или доступное устройство; orphan без автора скрыт (`user_can_see_script_run`).
- UI-скрытие меню **не** защита: каждый mutating endpoint проверяет на сервере.

Хелперы: `admin_required`, `user_has_role`, `user_can_run_scripts`, `user_can_run_script`,
`user_can_run_diagnostics`, `user_can_connect_vnc` (как диагностика), `user_can_view_password_expiry`, `accessible_sector_ids`,
`user_can_access_device`, `get_visible_device_or_404`, `filter_accessible_devices`,
`user_can_see_script_run`.

### Секреты

| Что | Где |
| --- | --- |
| `SECRET_KEY`, `FERNET_KEY`, LDAP/SMTP/DB пароли | только `.env` / EnvironmentFile |
| Пароли PsExec / пароль входа | Fernet ciphertext в `remote_credentials` |
| Учётка WMI discovery | `.env` `DISCOVERY_*` или Настройки → Опросы ПК (`settings_service`) |
| Пароль VNC (общий) | Fernet в `app_settings` (`vnc_password_encrypted`) |
| TLS ключ/сертификат | файлы `/opt/bawh/certs/` (не БД) |

Порядок учётки PsExec (`get_remote_admin_credentials(user_id)`):

1. Заполненные username+password в строке пользователя (Настройки → Общие / PsExec)
2. Иначе имя входа + `LDAP_DOMAIN` + зашифрованный пароль последнего LDAP-входа

---

## 10. Фоновые задачи

### Процесс `bawh-scheduler` (APScheduler) — опрос

| Job | Триггер | Действие |
| --- | --- | --- |
| `poll-devices` | Interval из `app_settings` / `POLL_INTERVAL_SECONDS` | `run_network_poll` |
| `hardware-poll` | cron из настроек (default `0 12 * * *`, локальный TZ) | `run_hardware_poll` (если включено) |
| `archive-logs` | daily ~00:20 UTC | `log_archive_service` |

Интервал опроса и cron железа подхватываются без перезапуска процесса.

### Процесс `bawh-password-reports` — пароли AD

| Job | Триггер | Действие |
| --- | --- | --- |
| `password-expiry` | cron из настроек модуля | `run_password_expiry` (если включено) |
| `password-expiry-refresh` | каждые ~30 с | перечитать cron/тумблер из БД |

Тумблер «Сервис отчётов» на UI модуля включает job и поднимает
systemd-unit `bawh-password-reports` (sudo-учётка: Настройки → Общие).
Выключение тумблера останавливает только эту службу — опрос сети не затрагивается.

### Процесс `bawh-pc-reports` — отчёты о ПК

| Job | Триггер | Действие |
| --- | --- | --- |
| `sector-daily-report` | cron из настроек модуля (default `0 7 * * *`) | `run_sector_daily_report` (если включено) |
| `sector-daily-refresh` | каждые ~30 с | перечитать cron/тумблер из БД |

Аналогично: тумблер → `bawh-pc-reports`. Отчёты **не** зависят от прогона опроса сети.

### Внутри веб-процесса

| Механизм | Где | Зачем |
| --- | --- | --- |
| `ThreadPoolExecutor` | `ping_service` | параллельный ICMP+discovery; запись в БД — в главном потоке |
| `ThreadPoolExecutor` | `script_service.enqueue_run` | фоновый PsExec/ping/tracert; UI поллит `/scripts/runs/<id>/status` (~1.5 с, `run_log.js`) |
| `threading.Thread` | `update_service.begin_update` / `begin_rollback` | фон, чтобы Gunicorn не упёрся в таймаут; поток с `app.app_context()` — sudo-учётка из Настройки → Общие / `UPDATE_SUDO_USER` |

**Нет** WebSocket / SSE / Celery / общей очереди сообщений.

---

## 11. Ключевые потоки данных

### A. HTTP lifecycle

```
Nginx → Gunicorn (wsgi:app)
  → session / CSRF / Flask-Login
  → blueprint (@login_required / @admin_required / authz)
  → service (DB / LDAP / ping / psexec)
  → Jinja template или jsonify
```

### B. Опрос сети

```mermaid
sequenceDiagram
  participant Sch as bawh-scheduler
  participant Ping as ping_service
  participant Disc as discovery_service
  participant Acc as account_service
  participant DB as PostgreSQL

  Sch->>Ping: run_network_poll()
  Ping->>Ping: ICMP по CIDR секторов
  Note over Ping: учётка WMI в главном потоке<br/>(Настройки → Опросы ПК / DISCOVERY_*)
  Note over Ping: итог → network_poll_runs
  alt online
    Ping->>Disc: WMI hostname (PTR запасной) / ARP MAC / serial
    Disc-->>Ping: serial, mac, hostname, logged_on_user
    opt серый адрес (нет AD-имени и WMI-серийника)
      Ping->>Ping: TCP 445/135, 8728/8291, 9100/515, 554 + SNMP sysDescr
    end
    Ping->>DB: devices + device_history (+ fingerprint_kind)
    Note over Ping,DB: SN занял IP — другие карточки с этим адресом → offline
    opt logged_on_user is not None
      Ping->>Acc: apply_logged_on_user (savepoint)
      Acc->>DB: endpoint_accounts + history
    end
  else offline
    Ping->>DB: обновить известный IP / статус
  end
```

### C. Скрипт / команда

Форма/API → authz → `script_service.start_run(s)` (pending commit) →
ThreadPool `execute_run` → `psexec_service` или локальный ping/tracert →
обновление `script_runs` → JS поллит status. При ошибке — возможна `Notification` (`script_failed`).

### D. Password expiry

Scheduler cron / CLI / UI → `run_password_expiry` → LDAP fetch → classify →
SMTP → `password_expiry_runs` + `password_notifications`.

### D2. Отчёты о ПК

Scheduler cron / CLI / UI → `run_sector_daily_report` → ноутбуки (`BN***` / `n…`) и СБ (`w…`)
по секторам + агрегаты железа (ОЗУ/диск/ОС/CPU) + активность за прошедший
локальный день → SMTP → `sector_daily_report_runs`. Дашборд `/pc-reports` строит
живой снимок; Chart.js (vendor) рисует диаграммы. Дополнительно: кандидаты на
замену, пробелы инвентаризации, экспорт CSV.

### D3. Опрос железа Windows

Scheduler cron (полдень) / CLI `hardware-poll` / UI Настройки → Опросы ПК или карточка →
`run_hardware_poll` → ping известных Windows-целей (серийник WMI, имена BN/n/w/v/сервер,
fingerprint windows) → `lookup_wmi_hardware` (железо **и** serial/hostname) →
снимок на карточку с тем же SN; если на IP отвечает другой известный SN —
полный прогон пишет ему, разовый с карточки — skip. История — только если
CPU/ОЗУ/диски/ОС изменились. Итог прогона — `hardware_poll_runs`
(в UI: последние 5 + «Показать все»).
ICMP-опрос сети **не** трогает эти поля.

### E. Self-update

Admin UI → `update_service` (фоновый поток + `app.app_context()`).
Ветка: `app_settings.update_git_branch` (список через `git ls-remote --heads`),
иначе `GIT_BRANCH`. Хотфикс 1.6.7 при первом старте ставит `beta`, если
ключ ещё пустой. Дальше:
backup → git → pip → `flask init-db` → опциональный restart
`bawh-web` + `bawh-scheduler` + при включённых тумблерах
`bawh-password-reports` / `bawh-pc-reports` (sudo-учётка из Настройки → Общие / sudoers).
Перед restart — `deploy/sync-systemd-units.sh` (unit-файлы + sudoers).
Ошибка перезапуска не должна помечать уже выполненную замену кода как failed.

---

## 12. Соглашения по домену и коду

- **Домен УЗ** = NetBIOS upper-case (первая метка DNS/UPN): `CORP\alice` и `alice@corp.local` — одна запись.
- **Идентичность устройства:** только `serial_number`. Один SN — одна строка `devices`; смена DHCP/VPN-адреса обновляет IP/сектор/MAC у неё. Другие карточки, у которых ещё записан этот IP, становятся offline (короткая аренда VPN). Hostname и PTR **не** ключи слияния: одинаковое имя при разных SN — две карточки. Без SN можно переиспользовать только запись с тем же IP и пустым SN; зонд без SN не забирает строку, у которой SN уже есть. Призрак без SN на старом IP после появления SN на новом — известная дыра (не сливать автоматически).
- **Hostname:** сначала `Win32_ComputerSystem.DNSHostName` / `Name` из WMI; PTR (`socket.gethostbyaddr`) — только если WMI имя не отдал и у строки ещё пусто. Устаревший PTR не затирает уже записанное OS-имя. Разовый проход по инвентарю: `flask --app wsgi refresh-hostnames` (чужой SN на том же IP имя не пишет).
- **Тип устройства:** свои имена AD важнее WMI-серийника; серийник = Windows; иначе TCP/SNMP-отпечаток; иначе PTR/OUI. Ноутбуки: `BN***` (`bn` + 3 или 5 цифр) и префикс `n…`; СБ — `w…`.
- **Fingerprint:** только серые онлайн-адреса (нет AD-имени и нет WMI-серийника). Пустой зонд не затирает прошлый отпечаток.
- **WMI UserName:** `None` в probe — WMI не вызывали/упал (текущую УЗ **не** трогаем); `""` — никто не залогинен.
- **Опрос железа:** отдельный job, не ICMP. Цели — Windows (SN с WMI, имена BN/n/w/v/ktn/spb/kgl, fingerprint `windows`). Камеры/МФУ/роутеры не зонд. CPU = `Win32_Processor.Name`, ОЗУ = сумма `Win32_PhysicalMemory.Capacity` (запасной — `TotalPhysicalMemory`) в ГиБ с привязкой к номиналу планок, диски = сумма `Win32_DiskDrive.Size` без USB в десятичных ГБ (этикетка 1000³, номинал 256/512/…) (запасной — локальные тома), ОС = Caption + DisplayVersion (реестр StdRegProv) / сборка. Снимок пишется только если WMI-серийник (запасной — короткое имя ОС) совпадает с карточкой: иначе это чужой ПК на переиспользованном VPN/DHCP-адресе.
- **ILIKE:** только через `utils.ilike_pattern` (экранирование `%`/`_`).
- **Время:** всегда timezone-aware UTC (`utils.utcnow` / `as_utc`); в шаблонах фильтр `dt`.
- **Пустые адреса** в `devices` опрос не создаёт.
- **Селект = execute:** списки устройств/секторов в формах должны совпадать с ACL execute.
- Новый код: маршрут тонкий; логика в сервисе; новая таблица — модель + `ensure_schema` на старте.

---

## 13. Frontend

Канонические правила UI/UX (IA, токены, адаптив, разделение «работа vs конфиг») —
в [`docs/UI_GUIDEBOOK.md`](UI_GUIDEBOOK.md). Ниже — карта файлов.

**Шаблоны** (`app/templates/`): каркас `base.html` (topbar: карта, пароли AD,
отчёт о ПК, настройки). Сайдбар настроек (`layouts/settings.html`): сектора,
общие / опросы ПК / VNC / HTTPS, модули, экран входа, скрипты, обнаруженные УЗ,
действия и аудит, обновления. Домены: `accounts/`, `actions/`,
`admin/` (`settings_general` / `settings_polls` / `settings_vnc` / `settings_tls`),
`auth/`, `devices/`, `email/`, `errors/`, `layouts/`, `login_services/`,
`macros/`, `password_expiry/`, `scripts/`, `sector_daily_report/` (UI «Отчёт о ПК»), `sectors/`.

**Static:**

| Путь | Назначение |
| --- | --- |
| `static/css/app.css` | стили; `.entity-link` — единый вид ссылок на устройство / УЗ |
| `static/favicon.ico`, `favicon.svg` | иконка вкладки (`base.html`) |
| `static/js/http.js` | общий fetch/CSRF helper |
| `static/js/map.js` | карта сети (сайдбар поиск/фильтры); автообновление опционально (localStorage) |
| `static/js/search.js` | живой фильтр карты (без suggest-dropdown); поиск по УЗ |
| `static/js/live_search.js` | живой поиск списков Действия и аудит / Обнаруженные УЗ |
| `static/js/batch.js` | bulk операции |
| `static/js/notifications.js` | колокольчик |
| `static/js/run_log.js` | поллинг лога запуска |
| `static/js/login_status.js` | статус сервисов на `/login` |
| `static/js/pc_reports.js` | диаграммы отчётов о ПК (Chart.js) |
| `static/js/vnc_session.js` | подготовка агента + noVNC RFB на `/devices/<id>/vnc` |
| `static/vendor/bootstrap/` | Bootstrap 5 offline (air-gap) |
| `static/vendor/chart.js/` | Chart.js 4 UMD offline (air-gap) |
| `static/vendor/novnc/` | noVNC 1.5.0 core (air-gap) |

---

## 14. Конфигурация (обзор)

Источник: `.env` / `EnvironmentFile` → `app/config.py`. Полный список — `.env.example`.

| Группа | Переменные |
| --- | --- |
| App | `APP_CONFIG`, `SECRET_KEY`, `APP_NAME`, `LOG_*`, `LOG_ARCHIVE_*` |
| DB | `DATABASE_URL` |
| LDAP | `LDAP_HOST/PORT/USE_SSL/BASE_DN/BIND_*/USER_FILTER/ADMIN_GROUP/VIEWER_GROUP/OPERATOR_GROUP/PASSWORD_VIEWER_GROUP/DOMAIN` |
| SMTP | `SMTP_HOST/PORT/USE_STARTTLS/FROM/USER/PASSWORD` (UI модуля паролей может переопределить) |
| Poll | `POLL_INTERVAL_SECONDS`, `MIN_CIDR_PREFIX`, `MAX_HOSTS_PER_POLL`, `DISCOVERY_USERNAME/PASSWORD/DOMAIN` |
| Crypto | `FERNET_KEY` |
| Scripts | `SCRIPT_LIBRARY_DIR`, `SCRIPT_TIMEOUT_SECONDS` |
| Updates | `GIT_REMOTE_URL`, `GIT_BRANCH` (запас, если в UI ветка не выбрана), `UPDATE_BACKUP_KEEP`, `UPDATE_RESTART`, `UPDATE_SUDO_USER` |
| Cookies | `SESSION_COOKIE_SECURE` (также Настройки → HTTPS) |
| VNC proxy | `VNC_LISTEN_HOST`, `VNC_LISTEN_PORT`, `VNC_MAX_SESSIONS` |

Также в коде: `PROJECT_ROOT`, `MAX_LOG_CHARS`, `MAX_REMOTE_COMMAND_CHARS`.

---

## 15. Деплой

Основной путь: **Debian 12 + systemd + Nginx** (`deploy/`), каталог `/opt/bawh`.

| Артефакт | Роль |
| --- | --- |
| `deploy/install-debian12.sh` | Установка одной командой → PG, venv, `flask init-db`, nginx, systemd |
| `deploy/bawh-web.service` | Gunicorn `127.0.0.1:8000`, `EnvironmentFile=/opt/bawh/.env` |
| `deploy/bawh-scheduler.service` | `python -m app.scheduler_worker` (опрос / железо / архив) |
| `deploy/bawh-password-reports.service` | `python -m app.password_report_worker` |
| `deploy/bawh-pc-reports.service` | `python -m app.pc_report_worker` |
| `deploy/bawh-vnc.service` | `python -m app.vnc_worker` (127.0.0.1:6080) |
| `deploy/sync-systemd-units.sh` | cp unit'ов + sudoers + `daemon-reload` |
| `deploy/nginx-bawh.conf` | :80 → gunicorn + `/vnc/ws` → bawh-vnc |
| `deploy/apply-nginx-tls.sh` | PEM из Настройки → HTTPS → listen 443, reload nginx |
| `deploy/bawh-update.sudoers` | passwordless restart всех служб + start/enable/stop отчётов/VNC + sync + TLS |
| `deploy/logrotate-bawh` | logrotate |
| `Dockerfile` | опционально, только web |

Обязательны **web**, **scheduler** и **bawh-vnc** (шлюз можно остановить тумблером
в Настройки → VNC). Службы отчётов поднимаются тумблерами в UI. Один `.env` на все
процессы (общий `FERNET_KEY` / `SECRET_KEY`).

---

## 16. Внешние интеграции

| Интеграция | Как | Где |
| --- | --- | --- |
| AD / LDAP | ldap3 bind+search | `ldap_service`, `password_ad_client` |
| SMTP | mailer | `password_mailer` |
| ICMP / traceroute | OS `ping` / `traceroute` | `ping_service` |
| TCP fingerprint | короткий connect 445/135/8728/8291/9100/515/554 | `fingerprint_service` |
| SNMPv1 sysDescr | UDP/161 community `public`, без pysnmp | `fingerprint_service` |
| Windows WMI | impacket | `discovery_service` |
| Windows remote | pypsexec (SMB/ADMIN$) | `psexec_service` |
| Git HTTPS | update check / pull | `update_service` |
| systemd | restart после update | `update_service`, sudoers |

---

## 17. Карта «куда смотреть»

| Задача | С чего начать |
| --- | --- |
| Новый HTTP endpoint | `app/routes/<domain>.py` → сервис → шаблон; blueprint уже в `__init__` |
| Права доступа | `app/authz.py` + флаги `User` + LDAP groups в `ldap_service` |
| Опрос / ICMP / WMI / fingerprint | `ping_service` → `discovery_service` → `fingerprint_service` → `account_service` |
| Опрос железа Windows | `hardware_poll_service` → `discovery_service.lookup_wmi_hardware` → `hardware_info`; CLI `flask hardware-poll`; Настройки → Опросы ПК и вкладка «Оборудование». Снимок привязан к SN, не к IP. |
| Идентичность устройств | `ping_service._resolve_device`: только SN; hostname display-only; при занятии IP другим SN предыдущий владелец → offline |
| Имена с машин (не PTR) | `hostname_sweep_service`, `flask refresh-hostnames`, POST `/admin/refresh-hostnames` |
| Карта сети UI | `routes/devices.py`, `templates/devices/map.html`, `static/js/map.js`, `network_summary_service` |
| Текст скрипта для веб-формы | [`SCRIPTS.md`](SCRIPTS.md) — поля, интерпретатор, SYSTEM, журнал, чеклист |
| Скрипты / PsExec (код) | `script_service`, `psexec_service`, `routes/scripts.py`, `credential_service` |
| Веб-VNC | `vnc_token`, `vnc_settings`, `app/vnc_worker.py`, `routes/devices.py` `vnc_session` / `vnc_prepare`, сид удаления `tightvnc_install_script`, раздача MSI `routes/vnc_agents.py` + `vnc-agents/`, [`VNC.md`](VNC.md) |
| HTTPS из UI | `tls_pem`, `tls_settings`, `tls_service`, `deploy/apply-nginx-tls.sh`, Настройки → HTTPS |
| Bulk с карты | `batch_service`, `routes/devices.py` bulk API, `static/js/batch.js` |
| УЗ на ПК | `account_service`, `models/account.py`, `routes/accounts.py` |
| Пароли AD | `password_expiry_service` + `password_*`, `routes/password_expiry.py` |
| Отчёты о ПК | `sector_daily_report_service` + settings, `routes/sector_daily_report.py`, `static/js/pc_reports.js` |
| Уведомления | `notification_service`, `routes/notifications.py` |
| Настройки / опрос вручную | `routes/admin.py` (`section=general|polls|vnc|tls`), `settings_service` |
| Аудит админа | `audit_service`, лента в Настройки → Действия и аудит (`/admin/audit` → редирект) |
| Новая таблица | модель в `models/` → `__all__` в `models/__init__.py` → `ensure_schema` на старте |
| Конфиг / env | `config.py` + `.env.example` |
| Деплой unit | `deploy/*` |
| Версия релиза | корневой `VERSION`, `app/version.py` |

---

## 18. Связанные документы

| Документ | Когда нужен |
| --- | --- |
| [README.md](../README.md) | Установка на Debian, минимальный `.env`, службы, обновление |
| [`.env.example`](../.env.example) | Полный список переменных окружения |
| [UI_GUIDEBOOK.md](UI_GUIDEBOOK.md) | IA, токены, компоненты, адаптив, «работа vs конфиг», чеклист экранов |
| [SCRIPTS.md](SCRIPTS.md) | Требования к телу скрипта из формы Настройки → Скрипты (для агентов и авторов) |
| [VNC.md](VNC.md) | Стол в браузере, агент TightVNC при подключении, тихое удаление, HTTPS из Настройки → HTTPS |

---

## 19. Что коммитить и что оставлять локально / в Cloud

**В git (канон):** `app/`, `deploy/`, `docs/`,
`VERSION`, `requirements.txt`, `wsgi.py`, `.env.example`, `Dockerfile`, `README.md`.

**Не коммитить** (остаются на машине разработчика или в рабочей среде Cloud Agent;
прописано в [`.gitignore`](../.gitignore)):

| Паттерн / каталог | Зачем |
| --- | --- |
| `.env`, `*.db`, `instance/`, `.venv/` | секреты и локальная БД |
| `logs/*`, `backups/`, `*.log` | runtime |
| `certs/` | PEM/ключи HTTPS из Настройки → HTTPS |
| `vnc-agents/*.msi`, `vnc-agents/*.exe` | установщик TightVNC, который админ кладёт на сервер |
| `.cursor/`, `.claude/`, `.scratch/` | scratch агентов и IDE |
| `tests/` | локальные unittest, в поставку не входят |
| `scripts/`, `tools/`, `tmp/`, `temp/` | одноразовые черновики |
| `debug_*`, `diagnose_*`, `diag_*`, `*_manual.py`, `smoke_*.py`, `harness_*.py`, `scratch_*.py` | диагностика и ручные прогоны |

Установщик (`deploy/install-debian12.sh`) и self-update (`update_service._SKIP_DIRS`)
тоже пропускают эти каталоги — в `/opt/bawh` они не попадут.

**Тесты:** `tests/` в gitignore. Локально можно держать `unittest`
(`python -m unittest discover -s tests -v`). Временные/ручные/диагностические
скрипты — только локально или в Cloud, без push в git.

---

*Обновляйте этот файл при смене слоёв, схемы БД, новых blueprints/сервисов/ролей или инвариантов домена.*
