# Архитектура bAWH

Краткий граф для навигации по коду. Детали установки — в [README.md](../README.md).

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
    AUTH["authz.py<br/>секторы · устройства · script_runs"]
    UTILS["utils.py<br/>utcnow · as_utc · ilike_pattern · clip"]
  end

  subgraph http["routes/ → templates/ + static/"]
    R_AUTH["auth"]
    R_DEV["devices · search · diagnostics"]
    R_ACC["accounts · actions"]
    R_SEC["sectors"]
    R_SCR["scripts"]
    R_PWD["password_expiry"]
    R_ADM["admin"]
  end

  subgraph services["services/ — бизнес-логика без HTTP"]
    S_POLL["ping_service · discovery_service · scheduler_service"]
    S_ACC["account_service · action_service"]
    S_ID["ldap_service · credential_service · crypto_service"]
    S_RUN["script_service · psexec_service"]
    S_PWD["password_expiry_service · password_ad_client · password_mailer"]
    S_OTH["sector_service · search_service · settings_service · update_service · log_archive_service · net_utils"]
  end

  subgraph data["models/ + PostgreSQL"]
    M_USER["users · user_ldap_groups · remote_credentials"]
    M_NET["sectors · sector_ranges · sector_access · devices · device_history"]
    M_ACC["endpoint_accounts · device_account_history · action_kinds"]
    M_SCR["scripts · script_runs · app_settings"]
    M_PWD["password_notifications · password_expiry_runs"]
  end

  WSGI --> CREATE
  SCHED --> CREATE
  CLI --> CREATE
  CREATE --> http
  CREATE --> AUTH
  http --> services
  http --> AUTH
  SCHED --> S_POLL
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
  participant DB as PostgreSQL

  Sch->>Ping: poll_all_sectors()
  Ping->>Ping: ICMP по CIDR секторов
  alt online
      Ping->>Disc: hostname / ARP MAC / WMI inventory
    Disc-->>Ping: serial, mac, logged_on_user
    Note over Disc: учётка WMI из Параметров (Fernet) или DISCOVERY_*
    Ping->>DB: devices + device_history
    opt logged_on_user is not None
      Ping->>Acc: apply_logged_on_user (savepoint)
      Acc->>DB: endpoint_accounts upsert + history
    end
  else offline
    Ping->>DB: обновить известный IP
  end
```

## Справочники и журналы

| Сущность | Таблица | Кто пишет | Кто читает |
| --- | --- | --- | --- |
| Оператор сайта | `users` | `ldap_service` | auth, authz |
| УЗ на ПК | `endpoint_accounts` | `account_service` | `/accounts`, карточка устройства |
| Появление УЗ | `device_account_history` | `account_service` | лента `/actions`, вкладка accounts |
| Типы действий | `action_kinds` | миграция / `seed_action_kinds` | `/actions` |
| Запуски | `script_runs` | `script_service` | `/scripts/runs`, лента `/actions` |
| Опросы | `device_history` | `ping_service` | вкладка polls |

Правила видимости — только в `app/authz.py`:

- `accessible_sector_ids` / `user_can_access_device`
- `user_can_see_script_run` — админ, автор, или доступное устройство (orphan без автора скрыт)

## Ключевые соглашения

- **Маршрут** не пингует и не ходит в LDAP — только форма → сервис → шаблон.
- **Домен УЗ** хранится как NetBIOS upper-case (первая метка DNS/UPN): `CORP\alice` и `alice@corp.local` — одна запись.
- **WMI UserName**: `None` в probe — WMI не вызывали/упал (текущую УЗ не трогаем); `""` — никто не залогинен.
- **ILIKE**: экранирование через `utils.ilike_pattern`.
- **Время**: `utils.utcnow` / `as_utc` — всегда timezone-aware UTC.

## Каталоги

```
bAWH/
  VERSION · wsgi.py · requirements.txt
  app/
    __init__.py · config.py · extensions.py · authz.py · utils.py
    models/ · services/ · routes/ · templates/ · static/
  migrations/versions/   # Alembic, сейчас до 0006_password_expiry
  tests/
  deploy/                # Debian 12: systemd, Nginx, install script
  docs/ARCHITECTURE.md   # этот файл
```
