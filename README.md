# bAWH

Внутренний веб-сервис мониторинга сети компании: секторы (подсети), устройства, история опросов, поиск, диагностика и запуск команд на Windows. Это сервис для сотрудников в своей сети, его не выставляют в интернет.

Если вы раньше не писали на Flask: приложение — это Python-процесс, который отвечает на HTTP. Страницы лежат в шаблонах, таблицы — в моделях, а действия вроде «пингануть сектор» — в сервисах. Маршрут только принимает запрос и вызывает сервис.

## Структура каталогов

```
bAWH/
  wsgi.py                 точка входа веб-процесса (Gunicorn и flask run)
  app/
    __init__.py           create_app(): собирает конфиг, БД, маршруты
    config.py             настройки из переменных окружения
    extensions.py         объекты Flask-расширений (БД, логин, CSRF, миграции)
    authz.py              кто какой сектор и устройство видит
    logging_config.py     лог в файл и в stdout (stdout забирает journalctl)
    models/               таблицы
    services/             бизнес-логика без HTTP
    routes/               URL и формы (blueprints)
    templates/            HTML-страницы
    static/               CSS и JS
  migrations/             схема PostgreSQL (Alembic через Flask-Migrate)
  tests/                  pytest
  deploy/                 systemd и Nginx
  requirements.txt        зависимости Python
  .env.example            образец настроек, без настоящих секретов
```

Четыре слоя, которые вы будете расширять:

| Слой | Каталог | Зачем |
| --- | --- | --- |
| Модели | `app/models/` | Описывают таблицы: пользователь, сектор, устройство, история, скрипт, настройки. Здесь нет пинга и нет HTML. |
| Сервисы | `app/services/` | Делают работу: LDAP, опрос, шифрование пароля, поиск. Функцию сервиса можно вызвать и из веба, и из планировщика, и из теста. |
| Маршруты | `app/routes/` | Куски сайта (Blueprint): прочитали форму, вызвали сервис, выбрали шаблон. Пинг, LDAP и PsExec отсюда напрямую не выполняются. |
| Шаблоны | `app/templates/` | То, что видит браузер. Общий каркас — `base.html`. |

Рядом с слоями лежит `app/authz.py`: администратор видит все секторы, обычный пользователь — только те, что выданы его логину или LDAP-группе. Маршруты вызывают эти проверки и не копируют условия по ролям.

Сборка приложения одна: `create_app()` в `app/__init__.py`. Её вызывают веб-процесс, тесты и процесс опроса. Проверка «процесс жив» — `GET /health` (без входа, ответ `{"status": "ok"}`).

### Модели

Импорт всех таблиц — `app/models/__init__.py`. Схема создаётся миграцией `migrations/versions/20261002_0001_initial.py`.

- `users`, `user_ldap_groups` — человек после входа через LDAP и его группы.
- `sectors`, `sector_ranges`, `sector_access` — имя сектора, CIDR-диапазоны и кому он виден.
- `devices`, `device_history` — последний статус устройства и журнал опросов.
- `scripts`, `script_runs` — библиотека скриптов и каждый запуск с логом.
- `app_settings` — параметры вроде интервала опроса.
- `remote_credentials` — учётка PsExec, пароль только шифротекстом.

### Сервисы

Карта модулей зафиксирована в `app/services/__init__.py`. Маршрут принимает запрос и вызывает функцию отсюда.

Уже в каталоге, с такими входами:

- `net_utils.py` — разбор IP/CIDR и MAC: `parse_range`, `expand_ranges`, `normalize_mac`.
- `crypto_service.py` — Fernet: `encrypt`, `decrypt`.
- `credential_service.py` — учётка удалённого администратора: `get_remote_admin_credentials`, `save_remote_admin_credentials`.
- `settings_service.py` — интервал опроса: `get_poll_interval_seconds`, `set_poll_interval_seconds`.

Остальные модули описаны там же (файлы могут дописываться отдельно, контракт не меняйте):

- `ldap_service.py` — проверка пароля в LDAP и список групп.
- `sector_service.py` — создание и правка секторов.
- `ping_service.py` — ICMP-пинг и запись истории. Общий вход опроса: `poll_all_sectors`.
- `discovery_service.py` — обратный DNS и MAC из соседской таблицы ARP.
- `scheduler_service.py` — цикл опроса для отдельного процесса.
- `search_service.py` — поиск устройств по IP, MAC, hostname.
- `psexec_service.py` — удалённая команда на Windows.
- `script_service.py` — библиотека скриптов и запуск в фоновом потоке.

Процесс опроса — модуль `app.scheduler_worker`. Запуск: `python -m app.scheduler_worker`. Он создаёт приложение, чтобы читать конфигурацию и БД, и сам HTTP не обслуживает. Внутри APScheduler берёт интервал через `get_poll_interval_seconds()` и вызывает опрос секторов.

### Маршруты

Каждый файл в `app/routes/` — свой набор URL.

| Файл | Адреса |
| --- | --- |
| `auth.py` | `GET/POST /login`, `POST /logout` |
| `devices.py` | `GET /` карта сети, `GET /devices/<id>` |
| `sectors.py` | `/sectors` — список, создание, карточка, правка |
| `search.py` | `GET /search`, `GET /search/suggest` |
| `diagnostics.py` | `POST /devices/<id>/ping`, `/tracert`, `/command` |
| `scripts.py` | `/scripts` и страница запуска `/scripts/runs/<id>` |
| `admin.py` | `GET/POST /admin/settings` — учётка PsExec и интервал опроса |

Маршрут только читает форму и вызывает сервис. `POST /login` проверяет пароль в LDAP. Сектора, настройки, поиск и карточка устройства пишут и читают PostgreSQL. Ping и трассировка стартуют с сервера приложения, команда администратора и скрипт — через `psexec_service`, а страница `/scripts/runs/<id>` дочитывает лог опросом раз в 1,5 секунды.

## Быстрый старт для разработки

Нужен Python 3.11. Команды выполняйте из корня проекта (там, где лежат `wsgi.py` и `requirements.txt`).

Виртуальное окружение (venv) — отдельная папка с пакетами этого проекта, чтобы не ставить их во всю систему.

Linux:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Windows (PowerShell):

```powershell
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

Откройте `.env` и поменяйте `SECRET_KEY` на длинную случайную строку. Список переменных и комментарии к ним — в `.env.example`. Файл `.env` в git не попадает.

Чтобы открыть интерфейс на своём компьютере без PostgreSQL, в `.env` временно поставьте:

```
DATABASE_URL=sqlite:///bawh.db
```

Файл `bawh.db` появится в корне проекта. Для общей базы компании так не делают: SQLite здесь только для локального просмотра.

Дальше, с активированным venv:

```bash
flask --app wsgi db upgrade
flask --app wsgi run --debug
```

Первая команда создаёт таблицы. Вторая запускает отладочный сервер Flask. Откройте в браузере http://127.0.0.1:5000/login . Страница карты `/` перенаправит на вход, пока сессии нет.

Команда `flask run` слушает порт **5000**. Порт **8000** используют Gunicorn на сервере и `python wsgi.py`. Отладочный сервер Flask на Debian-сервис не ставят.

Тесты (PostgreSQL и LDAP не нужны):

```bash
python -m pytest
```

## PostgreSQL

На сервере данные лежат в PostgreSQL. Имя роли и имя базы — `bawh`. Пароль придумайте сами и впишите его только в `.env`. Формат строки смотрите в `.env.example` (`postgresql+psycopg2://...`). В коде пароля базы нет.

```bash
sudo -u postgres createuser --pwprompt bawh
sudo -u postgres createdb --owner=bawh bawh
```

`createuser --pwprompt` спросит пароль в терминале. Тот же пароль запишите в `.env`:

```
DATABASE_URL=postgresql+psycopg2://bawh:ПАРОЛЬ@localhost:5432/bawh
```

Роль PostgreSQL `bawh` и пользователь Linux `bawh` — разные учётные записи. Совпадает только имя.

Применение миграций (из корня проекта, venv включён, `.env` на месте). `create_app()` сам читает `.env`:

```bash
flask --app wsgi db upgrade
```

Повторный запуск безопасен: применятся только новые ревизии. Сейчас ревизия одна, `0001_initial`. Новую схему добавляйте новой миграцией (`flask --app wsgi db migrate -m "что изменилось"`, затем проверьте файл в `migrations/versions/` и выполните `db upgrade`), а не правкой уже применённого файла.

## LDAP, ключ Fernet и учётка PsExec

Все три вещи задаются окружением. Образец — `.env.example`. В репозиторий пароли не кладут.

### LDAP

Вход пользователей идёт в каталог компании через библиотеку **ldap3** (чистый Python, пакет `libldap` на сервере не нужен). Заполните в `.env` хост, порт, базу поиска, фильтр, домен и группу администраторов. Имена переменных: `LDAP_HOST`, `LDAP_PORT`, `LDAP_USE_SSL`, `LDAP_BASE_DN`, `LDAP_BIND_DN`, `LDAP_BIND_PASSWORD`, `LDAP_USER_FILTER`, `LDAP_ADMIN_GROUP`, `LDAP_DOMAIN`.

`LDAP_BIND_DN` и `LDAP_BIND_PASSWORD` можно оставить пустыми: тогда группы читаются от имени вошедшего пользователя. Члены группы из `LDAP_ADMIN_GROUP` (в образце это `bawh-admins`) становятся администраторами приложения.

### FERNET_KEY

Пароль учётки PsExec в таблице `remote_credentials` хранится зашифрованным (Fernet из пакета `cryptography`). Ключ лежит в переменной `FERNET_KEY`, в базу и в код его не записывают.

Сгенерировать (команда из `.env.example`):

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Вставьте строку в `.env` как `FERNET_KEY=...`. Один и тот же ключ должен быть у веб-процесса и у планировщика: оба читают один файл `.env`. Если ключ потерять, старый шифротекст не расшифровать — пароль вводят заново и кладут новый ключ в `.env`.

### Учётка PsExec

Удалённые команды на Windows выполняет библиотека **pypsexec** (`requirements.txt`). Сервер приложения стоит на Debian, а `psexec.exe` — программа Windows (Sysinternals): на Linux её нет в системе и она не является зависимостью Python. `pypsexec` работает внутри процесса приложения и ходит на Windows по сети. Пароль в исходниках не хранится. На время вызова он есть только в памяти (`app/models/setting.py`).

Откуда `get_remote_admin_credentials()` берёт учётку:

1. Строка `remote_credentials` (её пишет `save_remote_admin_credentials()`).
2. Иначе переменные `PSEXEC_USERNAME`, `PSEXEC_DOMAIN`, `PSEXEC_PASSWORD` из `.env`.

Форма `/admin/settings` вызывает `save_remote_admin_credentials()`: пароль шифруется и попадает в `remote_credentials`. После первого сохранения удалите `PSEXEC_PASSWORD` из `.env`, чтобы открытый пароль не оставался в файле окружения. Дальше рабочая копия лежит в базе. На той же странице задаётся интервал опроса; процесс `bawh-scheduler` подхватывает его без перезапуска.

## Деплой на Debian 12

Основной способ запуска — два сервиса systemd и Nginx на одном сервере Debian 12. Образ Docker в корне репозитория необязателен, см. комментарий в начале `Dockerfile`.

Команды ниже рассчитаны на то, что вы уже скопировали проект на сервер и стоите в его корне. Каталог установки — `/opt/bawh`.

### 1. Пакеты

```bash
sudo apt update
sudo apt install -y python3.11 python3.11-venv postgresql nginx iputils-ping traceroute
```

`iputils-ping` и `traceroute` нужны диагностике с самого сервера. Python-пакеты ставятся в venv на следующем шаге, не через apt.

### 2. Пользователь Linux и каталог

```bash
sudo useradd --system --home-dir /opt/bawh --shell /usr/sbin/nologin bawh
sudo mkdir -p /opt/bawh
sudo rsync -a --exclude .venv --exclude .env --exclude __pycache__ --exclude .git ./ /opt/bawh/
sudo chown -R bawh:bawh /opt/bawh
```

Пользователь `bawh` только запускает процессы. Вход по SSH ему не нужен (`nologin`).

### 3. Виртуальное окружение

```bash
sudo -u bawh python3.11 -m venv /opt/bawh/.venv
sudo -u bawh /opt/bawh/.venv/bin/pip install -r /opt/bawh/requirements.txt
```

### 4. Файл окружения

```bash
sudo -u bawh cp /opt/bawh/.env.example /opt/bawh/.env
sudo chmod 600 /opt/bawh/.env
sudo chown bawh:bawh /opt/bawh/.env
sudo -u bawh editor /opt/bawh/.env
```

Заполните значения по комментариям в `.env.example`: `SECRET_KEY`, `DATABASE_URL` с паролем из шага PostgreSQL, LDAP, `FERNET_KEY`. Пока сайт открыт по HTTP, оставьте `SESSION_COOKIE_SECURE=0`.

### 5. Миграции

```bash
sudo -u bawh bash -c 'cd /opt/bawh && .venv/bin/flask --app wsgi db upgrade'
```

### 6. Два юнита systemd

```bash
sudo cp /opt/bawh/deploy/bawh-web.service /opt/bawh/deploy/bawh-scheduler.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now bawh-web bawh-scheduler
```

Веб слушает только `127.0.0.1:8000`. Снаружи к этому порту не подключаются.

### 7. Nginx

В `deploy/nginx-bawh.conf` замените `server_name bawh.example.com` на имя сервера в вашей сети, затем:

```bash
sudo cp /opt/bawh/deploy/nginx-bawh.conf /etc/nginx/sites-available/bawh
sudo ln -s /etc/nginx/sites-available/bawh /etc/nginx/sites-enabled/bawh
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl reload nginx
```

Проверка с самого сервера:

```bash
curl -s http://127.0.0.1:8000/health
```

Ожидается `{"status":"ok"}`. Страницу входа откройте уже по имени из `server_name`, порт 80.

Когда на этом Nginx появится HTTPS, в `.env` поставьте `SESSION_COOKIE_SECURE=1` и выполните `sudo systemctl restart bawh-web`. Комментарий об этом есть в `deploy/nginx-bawh.conf`.

### 8. Журнал

stdout обоих процессов забирает journald. Файл `logs/bawh.log` (см. `LOG_FILE`) пишется дополнительно, каталог создаётся при старте.

```bash
journalctl -u bawh-web -u bawh-scheduler -f
```

Статус:

```bash
systemctl status bawh-web bawh-scheduler
```

## Почему два сервиса

Gunicorn в `bawh-web.service` запущен с `--workers 2`. Каждый worker — отдельный процесс Python со своим копированием приложения.

Планировщик в этот процесс не входит: в `app/config.py` стоит `SCHEDULER_ENABLED = False`, а в `app/extensions.py` APScheduler специально не создаётся. Если опрос повесить на веб-процесс, каждый worker начнёт пинговать сеть сам по себе.

Опрос — второй юнит, `bawh-scheduler.service`:

```bash
/opt/bawh/.venv/bin/python -m app.scheduler_worker
```

Один процесс читает интервал, вызывает `ping_service.poll_all_sectors` и пишет историю. Веб в это время только показывает уже записанные данные и принимает действия пользователя.

Локально планировщик запускается той же командой из корня проекта, с активированным venv и заполненным `.env`:

```bash
python -m app.scheduler_worker
```

Веб при этом может быть запущен отдельно через `flask --app wsgi run --debug`.

## Куда расти

Точки расширения уже названы в коде, менять вызовы под них не нужно.

- **Celery вместо APScheduler.** Задача по расписанию вызывает ту же `ping_service.poll_all_sectors`. `wsgi.py` остаётся только веб-слоем. Понадобится брокер (обычно Redis) и воркер Celery вместо `bawh-scheduler.service`.
- **Партиции `device_history`.** Сейчас это одна таблица и индекс `(device_id, timestamp)`. Когда строк станет много, отдельная миграция переводит её на `PARTITION BY RANGE (timestamp)` по месяцам. Комментарий к таблице уже стоит в `0001_initial`.
- **WebSocket вместо опроса лога.** Страница запуска скрипта сейчас обновляет лог обычным `GET` раз в 1,5 секунды (`app/static/js/run_log.js`), потому что так работает синхронный Gunicorn. Позже этот файл заменяют подпиской, контракт страницы `/scripts/runs/<id>` можно сохранить.
- **Поиск.** При росте таблицы `devices` подстроку ускоряют расширением PostgreSQL `pg_trgm`, контракт `search_service` не меняется.
- **Вынос remote-exec.** PsExec и скрипты — кандидат на отдельный сервис. Веб тогда только создаёт строку `script_runs` и читает лог, а команды на Windows выполняет другой процесс.

Права на секторы сейчас считаются в Python (`authz.accessible_sector_ids`). Когда секторов станут тысячи, это место меняют на один запрос `EXISTS`.

## Тесты

Запуск из корня проекта:

```bash
python -m pytest
```

Настройки pytest — в `pytest.ini` (`testpaths = tests`). Общие фикстуры — `tests/conftest.py`.

Тесты поднимают приложение с конфигурацией `testing`: SQLite в памяти, CSRF выключен, LDAP и сеть не используются. Ключ Fernet для прогона генерируется в `conftest.py`. Свой `.env` и живой PostgreSQL для `pytest` не нужны.

Что уже покрыто файлами:

- `tests/test_app.py` — `/health`, страница входа, редирект карты без сессии.
- `tests/test_authz.py` — кто видит сектор.
- `tests/test_net_utils.py` — разбор CIDR и MAC.
- `tests/test_settings_and_crypto.py` — интервал опроса и шифрование пароля.

Новый сервис сопровождайте тестом в `tests/`, без обращения к настоящему LDAP и к чужим IP. Для веб-страницы используйте фикстуру `client` (тестовый клиент Flask).
