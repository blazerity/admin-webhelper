# Архитектура bAWH

Краткий граф для навигации по коду. Детали установки — в [README.md](../README.md). План развития — в [ROADMAP.md](ROADMAP.md). Текущий релиз: **v0.6.0** (волны W1–W4 + simplify [#32](https://github.com/blazerity/admin-webhelper/pull/32)).

## Слои

```mermaid
flowchart TB
  subgraph entry["Точки входа"]
    WSGI["wsgi.py<br/>Gunicorn / flask run"]
    SCHED["app.scheduler_worker<br/>bawh-scheduler"]
    CLI["flask CLI<br/>db / archive-logs"]
  end

  subgraph app_core["app/"]
    CREATE["create_app()<br/>config · extensions · logging"]
    AUTH["authz.py<br/>секторы · роли · script_runs"]
    UTILS["utils.py<br/>utcnow · as_utc · normalize_page · clip"]
  end

  subgraph http["routes/ → templates/ + static/"]
    R_AUTH["auth"]
    R_DEV["devices · search · diagnostics"]
    R_ACC["accounts · actions"]
    R_SEC["sectors"]
    R_LOGIN["login_services"]
    R_SCR["scripts"]
    R_PWD["password_expiry"]
    R_NTF["notifications"]
    R_ADM["admin"]
    R_API["api_v1<br/>тонкие aliases → legacy"]
  end

  subgraph frontend["static/js/"]
    JS_HTTP["http.js<br/>fetchJson · postJson · escapeHtml"]
    JS_UI["map.js · batch.js · notifications.js · search.js"]
  end

  subgraph services["services/ — бизнес-логика без HTTP"]
    S_POLL["ping_service · discovery_service · scheduler_service"]
    S_ACC["account_service · action_service · export_service"]
    S_ID["ldap_service · credential_service · crypto_service"]
    S_RUN["script_service · psexec_service · batch_service"]
    S_NTF["notification_service · watchlist_service · audit_service"]
    S_PWD["password_expiry_service · password_ad_client · password_mailer"]
    S_OTH["sector_service · login_service_status · search_service · settings_service · update_service · log_archive_service · net_utils · network_summary_service · command_presets"]
  end

  subgraph data["models/ + PostgreSQL"]
    M_USER["users · user_ldap_groups · remote_credentials"]
    M_NET["sectors · sector_ranges · sector_access · devices · device_history · network_poll_runs"]
    M_ACC["endpoint_accounts · device_account_history · action_kinds"]
    M_SCR["scripts · script_runs · app_settings · login_services"]
    M_NTF["notifications · device_watchlist · admin_audit_log"]
    M_PWD["password_notifications · password_expiry_runs"]
  end

  WSGI --> CREATE
  SCHED --> CREATE
  CLI --> CREATE
  CREATE --> http
  CREATE --> AUTH
  http --> services
  http --> AUTH
  JS_UI --> JS_HTTP
  JS_UI --> R_API
  JS_UI --> http
  SCHED --> S_POLL
  S_POLL --> S_NTF
  services --> data
  services --> AUTH
  services --> UTILS
  S_POLL --> S_ACC
```

## Поток опроса

```mermaid
sequenceDiagram
  participant Sch as bawh-scheduler
  participant Ping as ping_service
  participant Disc as discovery_service
  participant Acc as account_service
  participant Watch as watchlist_service
  participant Ntf as notification_service
  participant DB as PostgreSQL

  Sch->>Ping: run_network_poll()
  Ping->>Ping: ICMP по CIDR секторов
  Note over Ping: учётка WMI читается в главном потоке<br/>(Параметры / DISCOVERY_*), не в воркерах
  Note over Ping: итог → network_poll_runs
  alt online
    Ping->>Disc: hostname / ARP MAC / WMI inventory (+creds)
    Disc-->>Ping: serial, mac, logged_on_user
    Ping->>DB: devices + device_history
    opt logged_on_user is not None
      Ping->>Acc: apply_logged_on_user (savepoint)
      Acc->>DB: endpoint_accounts upsert + history
    end
  else offline
    Ping->>DB: обновить известный IP
  end
  Ping->>Watch: evaluate_watchlist_alerts()
  Watch->>Ntf: existing_offline_alerts (batch) + create
  Ntf->>DB: notifications (device_offline, dedupe по эпизоду)
```

## Очередь запусков (ping / script / command)

```mermaid
flowchart LR
  Route["routes: diagnostics / scripts / bulk"] --> Start["script_service.start_run<br/>или start_*_on_devices"]
  Start --> Commit["один db.session.commit<br/>на пачку ScriptRun"]
  Commit --> Pool["ThreadPoolExecutor<br/>MAX_RUN_WORKERS=8"]
  Pool --> Exec["execute_run(app, run_id)<br/>app_context + PsExec / ping"]
  Exec --> Status["script_runs.status / log_text"]
  Status --> UI["batch.js / run_log.js<br/>polling JSON"]
```

Bulk ping/script пишут все `pending`-строки одним commit и ставят `execute_run` в общую очередь (`enqueue_run`). Отдельный daemon-поток на каждый run больше не создаётся. При перезапуске процесса незавершённые runs остаются в БД; повторный enqueue — вручную или следующим action (Celery — точка расширения).

## Authz v2

Правила видимости — только в `app/authz.py` (ADR: [002-authz-v2.md](adr/002-authz-v2.md)):

| Проверка | Смысл |
| --- | --- |
| `accessible_sector_ids` / `user_can_access_device` | секторный ACL; устройство без `sector_id` недоступно non-admin |
| `user_can_see_script_run` | админ, автор run, или доступное устройство |
| `filter_accessible_devices` | bulk: один ACL-запрос секторов на пачку id |
| `user_can_run_diagnostics` | admin / operator; чистый viewer — нет; legacy user без ролей — да |
| `user_can_run_scripts` / `user_can_run_script` | admin / operator; non-admin — только `is_published` |
| `user_can_view_password_expiry` | admin / password_viewer |

Роли additive поверх `is_admin` + `sector_access`. Bulk-маршруты вызывают `user_can_run_diagnostics` / `user_can_run_scripts` напрямую (алиасы `user_can_bulk_*` удалены в #32).

## Внутренний API v1

Blueprint `app/routes/api_v1.py` (`/api/v1/...`) — тонкие aliases: делегирует в legacy JSON-handlers (`devices`, `notifications`, `search`). Старые пути (`/api/map/status`, `/api/batches/...`, `/search/suggest`, …) не удаляются. Контракт — session cookie + CSRF как у UI.

Общий фронтенд-клиент: `app/static/js/http.js` (`window.BawhHttp`) — `fetchJson` / `postJson` / `escapeHtml` для map, batch, notifications, search.

## Справочники и журналы

| Сущность | Таблица | Кто пишет | Кто читает |
| --- | --- | --- | --- |
| Оператор сайта | `users` | `ldap_service` | auth, authz |
| УЗ на ПК | `endpoint_accounts` | `account_service` | `/accounts`, карточка устройства |
| Появление УЗ | `device_account_history` | `account_service` | лента `/actions`, вкладка accounts |
| Типы действий | `action_kinds` | миграция / `seed_action_kinds` | `/actions` |
| Запуски | `script_runs` | `script_service` | `/scripts/runs`, batch, лента `/actions` |
| Опросы | `device_history` | `ping_service` | вкладка polls |
| Прогоны опроса | `network_poll_runs` | `ping_service.run_network_poll` | `/admin/settings`, health |
| In-app уведомления | `notifications` | `notification_service` | `/notifications`, колокольчик |
| Watchlist | `device_watchlist` | `watchlist_service` | карточка устройства, poll → alerts |
| Audit админки | `admin_audit_log` | `audit_service` | админ-журналы |

## Ключевые соглашения

- **Маршрут** не пингует и не ходит в LDAP — только форма → сервис → шаблон (или JSON).
- **Домен УЗ** хранится как NetBIOS upper-case (первая метка DNS/UPN): `CORP\alice` и `alice@corp.local` — одна запись.
- **WMI UserName**: `None` в probe — WMI не вызывали/упал (текущую УЗ не трогаем); `""` — никто не залогинен.
- **ILIKE**: экранирование через `utils.ilike_pattern`.
- **Время**: `utils.utcnow` / `as_utc` — всегда timezone-aware UTC.
- **Пагинация**: `utils.normalize_page` + `export_service.csv_attachment` для CSV.
- **Watchlist offline**: dedupe по эпизоду (`last_seen` → `created_at`); в poll — batch-lookup `existing_offline_alerts`, не N× SELECT.

## Каталоги

```
bAWH/
  VERSION · wsgi.py · requirements.txt   # VERSION = 0.6.0
  app/
    __init__.py · config.py · extensions.py · authz.py · utils.py
    models/ · services/ · routes/ · templates/ · static/js/
  migrations/versions/   # Alembic, сейчас до 0010_watchlist_notifications
  tests/
  deploy/                # Debian 12: systemd, Nginx, install-debian12.sh
  docs/ARCHITECTURE.md   # этот файл
  docs/ROADMAP.md        # план волн W1–W4 (закрыты в 0.6.0)
  docs/agents/           # брифы агентов A1–A5
  docs/adr/              # IA, Authz v2
  docs/runbooks/         # чеклисты приёмки и операторские сценарии
```
