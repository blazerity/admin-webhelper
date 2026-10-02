# bAWH

Внутренний веб-сервис мониторинга сети компании: секторы (подсети), устройства, история опросов, поиск, диагностика и запуск команд на Windows. Сервис рассчитан на сотрудников во внутренней сети и не предназначен для публикации в интернет.

Приложение — Flask-процесс на Python. Страницы в шаблонах, таблицы в моделях, действия (пинг, LDAP, PsExec) в сервисах. Маршрут принимает HTTP-запрос и вызывает сервис.

## Структура каталогов

Граф слоёв, поток опроса и соглашения по коду — в [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

```
bAWH/
  VERSION                 номер релиза (semver), для страницы обновлений
  wsgi.py                 точка входа веб-процесса (Gunicorn и flask run)
  app/
    __init__.py           create_app(): собирает конфиг, БД, маршруты
    config.py             настройки из переменных окружения
    extensions.py         объекты Flask-расширений (БД, логин, CSRF, миграции)
    authz.py              видимость секторов, устройств и script_runs
    utils.py              utcnow, as_utc, ilike_pattern, clip
    logging_config.py     лог в файл (суточная ротация) и в stdout (journalctl)
    models/               таблицы
    services/             бизнес-логика без HTTP
    routes/               URL и формы (blueprints)
    templates/            HTML-страницы
    static/               CSS и JS
  migrations/             схема PostgreSQL (Alembic через Flask-Migrate)
  docs/                   архитектура (графы Mermaid)
  tests/                  pytest
  deploy/                 systemd, Nginx и установка одной командой
  requirements.txt        зависимости Python
  .env.example            образец настроек, без настоящих секретов
```

| Слой | Каталог | Назначение |
| --- | --- | --- |
| Модели | `app/models/` | Таблицы: пользователь, сектор, устройство, УЗ на ПК, история, скрипт, настройки. Без пинга и без HTML. |
| Сервисы | `app/services/` | LDAP, опрос, УЗ, действия, шифрование, поиск, PsExec. Вызываются из веба, планировщика и тестов. |
| Маршруты | `app/routes/` | Blueprint: форма → сервис → шаблон. Пинг, LDAP и PsExec отсюда напрямую не выполняются. |
| Шаблоны | `app/templates/` | HTML. Общий каркас — `base.html`. |

`app/authz.py`: администратор видит всё; обычный пользователь — секторы по логину/LDAP-группе; `script_runs` — автор или доступное устройство (`user_can_see_script_run`).

Сборка одна: `create_app()` в `app/__init__.py`. Её вызывают веб-процесс, тесты и процесс опроса. Проверка «процесс жив» — `GET /health` (без входа, ответ `{"status": "ok"}`). Доступность сервисов компании на экране входа — `GET /api/login-services/status`.

### Модели

Импорт всех таблиц — `app/models/__init__.py`. Схема создаётся миграциями в `migrations/versions/` (сейчас до `0007_login_services`).

Справочники и журналы разделены:

- `users`, `user_ldap_groups` — операторы сайта после входа через LDAP и их группы.
- `sectors`, `sector_ranges`, `sector_access` — справочник секторов, CIDR и кому сектор виден.
- `devices`, `device_history` — справочник машин и журнал опросов. Уникальность: `serial_number` (WMI), иначе hostname; IP — последний адрес; `current_account_id` — кто сейчас за ПК.
- `endpoint_accounts`, `device_account_history` — справочник УЗ на конечных точках и факты «УЗ замечена на устройстве» (не путать с `users`). Ключ домена — NetBIOS (первая метка DNS/UPN): `CORP\alice` и `alice@corp.local` — одна запись.
- `action_kinds` — справочник типов действий (`ACTION_KIND_SEED` в модели); лента `/actions` собирается из `script_runs` и `device_account_history` с теми же правилами видимости, что и карточка запуска.
- `scripts`, `script_runs` — библиотека скриптов и журнал запусков (скрипт, ping, tracert, команда). У скрипта есть `run_as`: учётка PsExec или `NT AUTHORITY\SYSTEM`.
- `app_settings` — параметры вроде интервала опроса.
- `password_notifications`, `password_expiry_runs` — история писем о сроке пароля и снимки прогонов.
- `login_services` — сервисы компании для блока доступности на экране входа (имя + IP/FQDN).
- `remote_credentials` — учётка PsExec **на каждого пользователя сайта**; пароли только шифротекстом Fernet. Пустые поля формы означают запуск от входа на сайт (пароль входа тоже хранится зашифрованным).

### Сервисы

- `net_utils.py` — разбор IP/CIDR и MAC: `parse_range`, `expand_ranges`, `normalize_mac`, `assert_host_or_ipv4`, `resolve_to_ipv4`.
- `crypto_service.py` — Fernet: `encrypt`, `decrypt`.
- `credential_service.py` — учётка удалённого запуска для конкретного пользователя: `get_remote_admin_credentials`, `save_remote_admin_credentials`, `remember_login_password`.
- `settings_service.py` — интервал опроса: `get_poll_interval_seconds`, `set_poll_interval_seconds`.
- `ldap_service.py` — проверка пароля в LDAP и список групп.
- `sector_service.py` — создание и правка секторов.
- `login_service_status.py` — CRUD сервисов экрана входа и ICMP-проверка для публичного статуса.
- `ping_service.py` — ICMP-пинг и запись истории. Общий вход опроса: `poll_all_sectors`. Пустые адреса в `devices` не создаёт.
- `discovery_service.py` — обратный DNS, MAC из ARP/WMI, серийник и текущая УЗ по WMI (учётка в Параметрах или `DISCOVERY_*` в `.env`).
- `account_service.py` — справочник `endpoint_accounts`, разбор/нормализация `DOMAIN\user`, upsert с защитой от гонки, запись появлений УЗ.
- `action_service.py` — справочник `action_kinds` и лента недавних действий (authz как у `scripts.run_detail`).
- `scheduler_service.py` — цикл опроса для отдельного процесса.
- `search_service.py` — поиск устройств по IP, MAC, hostname, serial.
- `psexec_service.py` — удалённая команда на Windows.
- `script_service.py` — библиотека скриптов и запуск в фоновом потоке.
- `update_service.py` — обновление кода из публичного git и откат на резервную копию.
- `password_expiry_service.py` — проверка срока паролей AD, письма и админ-отчёт (LDAP host из `.env`, bind/SMTP в настройках модуля с fallback на `.env`, пороги в `app_settings`).

Процесс опроса — `python -m app.scheduler_worker`. Он создаёт приложение (конфиг и БД), HTTP не обслуживает. APScheduler берёт интервал через `get_poll_interval_seconds()` и вызывает опрос секторов.

### Маршруты

| Файл | Адреса |
| --- | --- |
| `auth.py` | `GET/POST /login`, `POST /logout` |
| `devices.py` | `GET /` карта сети, `GET /devices/<id>` (вкладки overview / accounts / commands / polls) |
| `accounts.py` | `/accounts` — справочник УЗ, `/accounts/<id>` — карточка |
| `actions.py` | `/actions` — справочник типов действий и лента событий |
| `sectors.py` | `/sectors` — список, создание, карточка, правка, удаление |
| `search.py` | `GET /search`, `GET /search/suggest` |
| `diagnostics.py` | `POST /devices/<id>/ping`, `/tracert`, `/command` |
| `scripts.py` | `/scripts` — библиотека, запуск, `/scripts/runs/<id>` (лог, отмена) |
| `admin.py` | `GET/POST /admin/settings` — учётка PsExec, учётка WMI для опроса и интервал; `GET/POST /admin/updates` — обновление из git и откат |
| `password_expiry.py` | `/password-expiry` — отчёт (пункт верхнего меню); `/password-expiry/settings` — bind/SMTP/пороги в «Настройки» (админы) |
| `login_services.py` | `/login-services` — сервисы для панели доступности на `/login` (админы); `GET /api/login-services/status` — публичный JSON (online + ms) |

`POST /login` проверяет пароль в LDAP и сохраняет зашифрованный пароль входа для возможного PsExec. Ping и трассировка стартуют с сервера приложения; команда и скрипт — через `psexec_service`. Страница `/scripts/runs/<id>` дочитывает лог опросом раз в 1,5 секунды.

## Локальная разработка

Нужен Python 3.11. Команды из корня проекта (`wsgi.py`, `requirements.txt`).

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Windows (PowerShell): `py -3.11 -m venv .venv`, затем `.venv\Scripts\Activate.ps1` и те же `pip` / `Copy-Item .env.example .env`.

В `.env` задайте длинный случайный `SECRET_KEY`. Список переменных — в `.env.example`. Файл `.env` в git не попадает.

Для интерфейса без PostgreSQL временно:

```
DATABASE_URL=sqlite:///bawh.db
```

Для общей базы компании используйте PostgreSQL.

```bash
flask --app wsgi db upgrade
flask --app wsgi run --debug
```

Отладочный сервер слушает порт **5000** (`http://127.0.0.1:5000/login`). Порт **8000** — Gunicorn на сервере и `python wsgi.py`.

```bash
python -m pytest
```

## PostgreSQL

На сервере: роль и база `bawh`. Пароль только в `.env` (`postgresql+psycopg2://...`).

```bash
sudo -u postgres createuser --pwprompt bawh
sudo -u postgres createdb --owner=bawh bawh
```

```
DATABASE_URL=postgresql+psycopg2://bawh:ПАРОЛЬ@localhost:5432/bawh
```

Роль PostgreSQL `bawh` и пользователь Linux `bawh` — разные учётные записи.

```bash
flask --app wsgi db upgrade
```

Повторный запуск безопасен. Новую схему — новой миграцией (`flask --app wsgi db migrate -m "..."`, проверка файла, затем `db upgrade`), без правки уже применённых ревизий.

## LDAP, Fernet и учётка PsExec

Образец — `.env.example`. Пароли в репозиторий не кладут.

### LDAP

Вход через **ldap3** (чистый Python; `libldap` на сервере не нужен). Переменные: `LDAP_HOST`, `LDAP_PORT`, `LDAP_USE_SSL`, `LDAP_BASE_DN`, `LDAP_BIND_DN`, `LDAP_BIND_PASSWORD`, `LDAP_USER_FILTER`, `LDAP_ADMIN_GROUP`, `LDAP_DOMAIN`.

`LDAP_HOST` может быть `ldap://...` / `ldaps://...` или голым хостом (тогда берутся `LDAP_PORT` и `LDAP_USE_SSL`). `LDAP_BIND_DN` / `LDAP_BIND_PASSWORD` можно оставить пустыми — группы читаются от имени вошедшего. Члены `LDAP_ADMIN_GROUP` (в образце `bawh-admins`) — администраторы приложения. `LDAP_DOMAIN` с точкой — UPN (`user@domain`); без точки — NetBIOS (`DOMAIN\user`).

### FERNET_KEY

Пароли в `remote_credentials` шифруются Fernet (`cryptography`). Ключ только в `FERNET_KEY`.

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Один ключ у `bawh-web` и `bawh-scheduler` (общий `.env`). Потеря ключа — старый шифротекст нечитаем; пароли вводят заново.

### Учётка PsExec

Удалённые команды — **pypsexec** (`requirements.txt`). На Debian нет `psexec.exe` Sysinternals: библиотека ходит на Windows по сети из процесса приложения.

Порядок для пользователя (`get_remote_admin_credentials(user_id)`):

1. Его строка `remote_credentials`: заполненные `username` и `password_encrypted` (форма `/admin/settings`).
2. Иначе имя входа на сайт, `LDAP_DOMAIN` и зашифрованный пароль последнего успешного LDAP-входа.

Открытый пароль только в памяти на время вызова. Учётка берётся из строки пользователя в БД (или из пароля входа). Интервал опроса на той же странице настроек; `bawh-scheduler` подхватывает его без перезапуска.

## Деплой на Debian 12

Два сервиса systemd и Nginx. Docker в корне репозитория необязателен (см. `Dockerfile`). Каталог установки — `/opt/bawh`.

### Установка одной командой

```bash
sudo apt-get update && sudo apt-get install -y curl ca-certificates && curl -fsSL https://raw.githubusercontent.com/blazerity/admin-webhelper/main/deploy/install-debian12.sh | sudo bash
```

Скрипт `deploy/install-debian12.sh` ставит пакеты, заводит пользователя Linux `bawh`, кладёт код в `/opt/bawh`, создаёт роль и базу PostgreSQL `bawh`, собирает venv, применяет миграции и включает `bawh-web`, `bawh-scheduler` и сайт Nginx. `SECRET_KEY`, `FERNET_KEY` и пароль базы пишет в `/opt/bawh/.env` (права `600`). `GIT_REMOTE_URL` / `GIT_BRANCH` берутся из `BAWH_REPO` / `BAWH_REF` (уже заданный свой адрес в `.env` не перезаписывается). Имя сайта — `hostname -f`, либо:

```bash
sudo apt-get update && sudo apt-get install -y curl ca-certificates && curl -fsSL https://raw.githubusercontent.com/blazerity/admin-webhelper/main/deploy/install-debian12.sh | sudo BAWH_SERVER_NAME=bawh.example.com bash
```

Если код уже на сервере:

```bash
sudo bash deploy/install-debian12.sh
```

Повторный запуск обновляет код, зависимости, миграции, юниты и `/etc/nginx/sites-available/bawh`; существующий `.env` не трогает. Сайт Nginx `default` отключается. bAWH: порт **80** (Nginx) и `127.0.0.1:8000` (Gunicorn). При включённом ufw скрипт открывает TCP 80.

После установки заполните LDAP в `/opt/bawh/.env` и:

```bash
sudo systemctl restart bawh-web bawh-scheduler
```

Проверка: `curl -s http://127.0.0.1:8000/health` → `{"status":"ok"}`. Пока сайт по HTTP — `SESSION_COOKIE_SECURE=0`.

### Установка вручную

Те же шаги, что выполняет скрипт, из корня уже скопированного проекта.

#### 1. Пакеты

```bash
sudo apt update
sudo apt install -y python3.11 python3.11-venv postgresql nginx git iputils-ping traceroute
```

`iputils-ping` и `traceroute` нужны диагностике с сервера.

#### 2. Пользователь Linux и каталог

```bash
sudo useradd --system --home-dir /opt/bawh --shell /usr/sbin/nologin bawh
sudo mkdir -p /opt/bawh
sudo rsync -a --exclude .venv --exclude .env --exclude __pycache__ --exclude .git ./ /opt/bawh/
sudo chown -R bawh:bawh /opt/bawh
```

#### 3. Виртуальное окружение

```bash
sudo -u bawh python3.11 -m venv /opt/bawh/.venv
sudo -u bawh /opt/bawh/.venv/bin/pip install -r /opt/bawh/requirements.txt
```

#### 4. Файл окружения

```bash
sudo -u bawh cp /opt/bawh/.env.example /opt/bawh/.env
sudo chmod 600 /opt/bawh/.env
sudo chown bawh:bawh /opt/bawh/.env
sudo -u bawh editor /opt/bawh/.env
```

Заполните `SECRET_KEY`, `DATABASE_URL`, LDAP, `FERNET_KEY`. При HTTP — `SESSION_COOKIE_SECURE=0`.

#### 5. Миграции

```bash
sudo -u bawh bash -c 'cd /opt/bawh && .venv/bin/flask --app wsgi db upgrade'
```

#### 6. systemd

```bash
sudo cp /opt/bawh/deploy/bawh-web.service /opt/bawh/deploy/bawh-scheduler.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now bawh-web bawh-scheduler
```

Gunicorn слушает только `127.0.0.1:8000`; снаружи его публикует Nginx на порту 80.

#### 7. Nginx

`deploy/nginx-bawh.conf` — порт 80, `default_server`. При желании поправьте `server_name`, затем:

```bash
sudo cp /opt/bawh/deploy/nginx-bawh.conf /etc/nginx/sites-available/bawh
sudo ln -s /etc/nginx/sites-available/bawh /etc/nginx/sites-enabled/bawh
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl reload nginx
```

```bash
curl -s http://127.0.0.1:8000/health
```

С другого хоста в сети: `http://IP-СЕРВЕРА/` (порт 80). После HTTPS в `.env` — `SESSION_COOKIE_SECURE=1` и `sudo systemctl restart bawh-web` (см. комментарий в `deploy/nginx-bawh.conf`).

#### 8. Журнал

```bash
journalctl -u bawh-web -u bawh-scheduler -f
systemctl status bawh-web bawh-scheduler
```

Дополнительно пишется `logs/bawh.log` (`LOG_FILE`):

| Файл | Что это |
| --- | --- |
| `logs/bawh.log` | текущий день |
| `logs/bawh.log.YYYY-MM-DD` | суточный хвост (ротация около полуночи) |
| `logs/archive/bawh-YYYY-MM.tar.gz` | упаковка завершённого месяца |

Месячную упаковку делает `bawh-scheduler` раз в сутки (~00:20 UTC) или вручную:

```bash
cd /opt/bawh && sudo -u bawh .venv/bin/flask archive-logs
```

Срок хранения месячных архивов — `LOG_ARCHIVE_KEEP_MONTHS` (по умолчанию 12).

#### 9. Обновление из веб-интерфейса

Администратор → «Обновления»: «Обновить из git» сначала копирует код в `backups/`, затем подтягивает публичный репозиторий. «Откатить» возвращает выбранную копию (текущая версия перед откатом тоже сохраняется).

Номер версии — файл `VERSION` в корне (сейчас `0.2.1`, semver: `MAJOR.MINOR.PATCH`). Страница обновлений показывает его, а не хеш коммита. Перед релизом увеличьте номер, закоммитьте и запушьте: правка `0.2.1` → `0.2.2` (исправление) или `0.3.0` (новые возможности). Пока `VERSION` не меняли, проверка всё равно увидит новый коммит и напишет «сборка …».

Не входят в копию и не перезаписываются: `.env`, `.venv`, `logs/`, `script_library/`, `*.db`, `backups/`. Схема PostgreSQL при откате кода назад не откатывается.

```
GIT_REMOTE_URL=https://github.com/blazerity/admin-webhelper.git
GIT_BRANCH=main
```

Пустой `GIT_REMOTE_URL` — берётся `origin` каталога установки. Повторный `deploy/install-debian12.sh` обновляет каталог с `.git` через `git pull --ff-only`.

Автоперезапуск служб после обновления:

```bash
sudo cp /opt/bawh/deploy/bawh-update.sudoers /etc/sudoers.d/bawh-update
sudo chmod 440 /etc/sudoers.d/bawh-update
sudo visudo -cf /etc/sudoers.d/bawh-update
```

Без правила код обновится, в интерфейсе останется команда `sudo systemctl restart bawh-scheduler bawh-web`.

## Почему два сервиса

`bawh-web.service` — Gunicorn с `--workers 2`. APScheduler в веб-процессе не создаётся. Иначе каждый worker пинговал бы сеть сам.

Опрос — `bawh-scheduler.service`:

```bash
/opt/bawh/.venv/bin/python -m app.scheduler_worker
```

Один процесс читает интервал, вызывает `ping_service.poll_all_sectors` и пишет историю. Веб показывает уже записанные данные и принимает действия пользователя.

Локально (venv и `.env`):

```bash
python -m app.scheduler_worker
```

Веб отдельно: `flask --app wsgi run --debug`.

## Точки расширения

Зафиксированы в коде и в `app/services/__init__.py`:

- **Celery вместо APScheduler** — задача вызывает ту же `poll_all_sectors`; вместо `bawh-scheduler.service` — воркер Celery и брокер.
- **Партиции `device_history`** — сейчас одна таблица и индекс `(device_id, timestamp)`; комментарий в `0001_initial` про `PARTITION BY RANGE (timestamp)`.
- **WebSocket лога скрипта** — сейчас `GET` раз в 1,5 с (`app/static/js/run_log.js`) из‑за синхронного Gunicorn.
- **Поиск** — при росте `devices` можно ускорить `pg_trgm` без смены контракта `search_service`.
- **Вынос remote-exec** — PsExec/скрипты в отдельный процесс; веб только пишет `script_runs` и читает лог.

Права на секторы — в Python (`authz.accessible_sector_ids`); при тысячах секторов — кандидат на `EXISTS` в SQL.

## Тесты

```bash
python -m pytest
```

Настройки — `pytest.ini`, фикстуры — `tests/conftest.py`. Конфигурация `testing`: SQLite в памяти, CSRF выключен, LDAP и сеть не используются. Fernet-ключ генерируется в `conftest.py`. Живой PostgreSQL и `.env` не нужны.

Покрытие включает, среди прочего:

- `tests/test_app.py` — `/health`, вход, редирект без сессии.
- `tests/test_authz.py` — доступ к секторам.
- `tests/test_net_utils.py` — CIDR и MAC.
- `tests/test_settings_and_crypto.py` — интервал и шифрование.
- `tests/test_ldap_service.py`, `tests/test_ping_service.py`, `tests/test_psexec_service.py`, `tests/test_script_service.py`, `tests/test_search_service.py`, `tests/test_sector_service.py`, `tests/test_discovery_service.py`, `tests/test_account_service.py`, `tests/test_update_service.py`, `tests/test_update_routes.py`.

Новый сервис — с тестом в `tests/`, без настоящего LDAP и чужих IP. Для HTTP — фикстура `client`.
