# bAWH

Внутренний веб-помощник администратора сети: карта устройств, опрос, диагностика, скрипты на Windows (PsExec), пароли AD, ACL по секторам.

Рассчитан на корпоративную LAN. Не предназначен для публикации в интернет.

Актуальная карта кода (слои, модели, сервисы, маршруты, authz) — в [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md). Перед навигацией по репозиторию или запросом к ИИ читайте этот файл.

## Требования

- Debian 12
- PostgreSQL
- LDAP / Active Directory

## Установка

Одной командой на чистом Debian 12:

```bash
sudo apt-get update && sudo apt-get install -y curl ca-certificates && curl -fsSL https://raw.githubusercontent.com/blazerity/admin-webhelper/main/deploy/install-debian12.sh | sudo bash
```

Имя сайта (опционально):

```bash
sudo apt-get update && sudo apt-get install -y curl ca-certificates && curl -fsSL https://raw.githubusercontent.com/blazerity/admin-webhelper/main/deploy/install-debian12.sh | sudo BAWH_SERVER_NAME=bawh.example.com bash
```

Установщик кладёт код в `/opt/bawh`, поднимает PostgreSQL, Nginx, `bawh-web` и `bawh-scheduler`, пишет секреты в `/opt/bawh/.env`. В консоли показывает прогресс по шагам (пакеты → код → venv → БД → службы → Nginx → health).

После установки заполните LDAP в `/opt/bawh/.env` и перезапустите службы:

```bash
sudo systemctl restart bawh-web bawh-scheduler
```

Проверка: `curl -s http://127.0.0.1:8000/health` → `{"status":"ok"}`.

Пока сайт по HTTP, оставьте `SESSION_COOKIE_SECURE=0`.

## Конфигурация

Образец переменных — [`.env.example`](.env.example). На установленном сервере правьте `/opt/bawh/.env`.

Минимум для входа:

| Переменная | Назначение |
| --- | --- |
| `SECRET_KEY` | секрет сессий Flask |
| `FERNET_KEY` | шифрование паролей PsExec / WMI |
| `DATABASE_URL` | PostgreSQL |
| `LDAP_HOST` | контроллер домена |
| `LDAP_BASE_DN` | база поиска |
| `LDAP_DOMAIN` | UPN или NetBIOS |
| `LDAP_ADMIN_GROUP` | группа администраторов приложения |

Роли (опционально): `LDAP_VIEWER_GROUP`, `LDAP_OPERATOR_GROUP`, `LDAP_PASSWORD_VIEWER_GROUP`.

Учётку PsExec и WMI можно задать в веб-интерфейсе: **Параметры**.

## Службы

| Юнит | Назначение |
| --- | --- |
| `bawh-web` | Gunicorn (`127.0.0.1:8000`), снаружи — Nginx :80 |
| `bawh-scheduler` | периодический опрос сети |

```bash
sudo systemctl status bawh-web bawh-scheduler
journalctl -u bawh-web -u bawh-scheduler -f
```

## Обновление

Из UI: **Администратор → Обновления** (git pull + перезапуск).

Или повторный запуск установщика из каталога с кодом / через one-liner — обновит `/opt/bawh`, зависимости и юниты; существующий `.env` не перезаписывает.

Версия релиза — файл [`VERSION`](VERSION) (сейчас `1.6.2`).

## Тесты

Канонические регрессии — stdlib `unittest` в `tests/` (pytest в requirements нет):

```bash
python -m unittest discover -s tests -v
```

Временные диагностические скрипты, ручные прогоны и scratch агентов
(`debug_*`, `diagnose_*`, `scripts/`, `tools/`, `tmp/`, `.cursor/` …)
**не коммитятся** — остаются локально или в Cloud Agent. Правила и полный список
паттернов: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) §19 и [`.gitignore`](.gitignore).

## Локальный запуск (опционально)

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# для быстрой проверки UI без PostgreSQL:
# DATABASE_URL=sqlite:///bawh.db
flask --app wsgi run --debug
```

Таблицы и недостающие колонки подтягиваются при старте (`ensure_schema` + справочники).
Переустановка кода без очистки Postgres безопасна: новые колонки (например роли
`is_viewer` / `is_operator`) добавятся сами. Планировщик отдельно: `python -m app.scheduler_worker`.

Имена на карте — с самой машины (WMI), не reverse DNS. Если карточки показывают общее PTR-имя, на сервере с доступом в LAN:

```bash
flask --app wsgi refresh-hostnames --dry-run
flask --app wsgi refresh-hostnames
```

Строки с разными серийниками не сливаются: меняется только поле hostname.
Слияние карточек — только по серийнику: смена IP при том же SN обновляет ту же запись.

Конфигурация железа Windows (процессор, ОЗУ, диски, версия 10/11/Server и 25H2/26H2) —
отдельный опрос по WMI, по умолчанию каждый день в полдень:

```bash
flask --app wsgi hardware-poll --dry-run
flask --app wsgi hardware-poll
```
