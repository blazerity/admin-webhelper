# W1 — регресс-чеклист приёмки

Ручная приёмка волны **W1 (Опора)** перед merge интеграционной ветки
`cursor/w1-implementation-2c6a` в `main`. Контракты: [`docs/agents/w1-contracts.md`](../agents/w1-contracts.md).

**Версия на старте волны:** 0.2.6 → цель после волны: **0.3.0** (bump делает координатор).

Отмечайте `[x]` на стенде. Блокеры — в PR / эскалация A1.

---

## 0. Подготовка стенда

- [ ] Код волны установлен (ветка/релиз W1), миграции применены.
- [ ] `bawh-web` и `bawh-scheduler` активны (см. [§ Deploy / scheduler](#9-deploy--scheduler--health)).
- [ ] Есть учётки: **admin** (LDAP_ADMIN_GROUP) и **non-admin** с ACL на хотя бы один сектор.
- [ ] В секторе есть устройства online и offline (или после ручного poll).
- [ ] Для команд/скриптов: учётка PsExec в Параметрах; хотя бы один скрипт в библиотеке (admin).

Автотесты на машине разработчика / CI:

```bash
python -m pytest
# или: .venv/bin/python -m pytest
```

Результаты прогона на ветке A5 (`feature/w1-09-regression-checklist`) — в [§ Pytest](#10-pytest-снимок).

---

## 1. Логин LDAP / logout

| # | Шаг | Ожидание |
| --- | --- | --- |
| 1.1 | `GET /login` | Форма входа, без утечки IP сервисов на экране |
| 1.2 | Вход admin (LDAP) | Редирект на карту `/` |
| 1.3 | Logout | Сессия сброшена; повторный `/` → `/login` |
| 1.4 | Вход non-admin | Карта только по доступным секторам |
| 1.5 | Неверный пароль | Ошибка, без 500 |

- [ ] 1.1–1.5 пройдены

---

## 2. Навигация (W1-01 / W1-02)

После W1 topbar (оператор): **Карта сети**, **Действия**, **Учётные записи**, **Скрипты** (только admin), **Пароли AD** (только admin), вход в **Настройки**.

| # | Шаг | Ожидание |
| --- | --- | --- |
| 2.1 | Admin: topbar | Пункты как в IA; активное состояние на текущем разделе |
| 2.2 | Non-admin: topbar | Нет «Скрипты» / «Пароли AD» (dashboard); есть Карта, Действия, УЗ, Настройки |
| 2.3 | Settings sidebar | Нет дублей Действия / УЗ / Скрипты (или вторичные ссылки — по ADR A1); Сектора, Параметры, Обновления, Экран входа, Пароли AD (settings) на месте |
| 2.4 | Старые URL | `/accounts/`, `/actions/`, `/scripts/`, `/admin/settings`, сектора — открываются (no breaking) |
| 2.5 | Mobile | Toggle меню (`aria-controls` / `aria-expanded`), фокус, закрытие после перехода |
| 2.6 | Desktop | Topbar читается без горизонтального скролла на типичной ширине |

- [ ] 2.1–2.6 пройдены

---

## 3. Карта: загрузка, suggest, сводка (W1-03…W1-05)

| # | Шаг | Ожидание |
| --- | --- | --- |
| 3.1 | `GET /` под admin | Секторы/устройства загружаются; `/map/status` обновляет статусы |
| 3.2 | Поиск на карте | Autocomplete через `GET /search/suggest` (fallback `GET /search/api`) |
| 3.3 | Выбор suggest | Переход на карточку / подсветка устройства |
| 3.4 | Сводка сети | Виджеты online/offline/unknown + last poll / failed scripts (если UI смержен) |
| 3.5 | `GET /api/network/summary` (session) | JSON по контракту W1; счётчики с учётом ACL секторов |
| 3.6 | Non-admin summary | Только видимые сектора; без утечки чужих устройств |
| 3.7 | Пустой сектор / нет устройств | Пустое состояние без JS-ошибок в консоли |

- [ ] 3.1–3.7 пройдены (или зафиксирован блокер A2/A3)

---

## 4. Карточка устройства (вкладки)

| # | Шаг | Ожидание |
| --- | --- | --- |
| 4.1 | `/devices/<id>?tab=overview` | Обзор устройства |
| 4.2 | `tab=accounts` | УЗ / sightings; «Показать все» при длинном списке |
| 4.3 | `tab=commands` | Ping/tracert/команда; история запусков |
| 4.4 | `tab=polls` | История опросов устройства |
| 4.5 | Чужой сектор (non-admin) | 403/404 как принято; не 500 |

- [ ] 4.1–4.5 пройдены

---

## 5. Пресеты команд + скрипт с карточки (W1-06 / W1-07)

| # | Шаг | Ожидание |
| --- | --- | --- |
| 5.1 | Admin, tab commands | Пресеты: whoami, `ipconfig /all`, hostname, `netstat -ano` |
| 5.2 | Выбор пресета | Команда подставляется в поле (источник — модуль/API A3, не только HTML) |
| 5.3 | Запуск пресет-команды | Redirect на `/scripts/runs/<id>`; лог/статус; cancel при running |
| 5.4 | Запуск скрипта с карточки | `POST /devices/<id>/scripts/run` (csrf + script_id) → run page |
| 5.5 | Non-admin: скрипт / пресеты admin-only | Кнопок нет; прямой POST → 403 |
| 5.6 | Device вне ACL | POST → 403/404 |

- [ ] 5.1–5.6 пройдены

---

## 6. Admin: settings, ручной poll, health scheduler (W1-08)

| # | Шаг | Ожидание |
| --- | --- | --- |
| 6.1 | `/admin/settings` | PsExec, WMI, интервал опроса, журнал `network_poll_runs` |
| 6.2 | Сменить интервал | Сохраняется; scheduler подхватывает без рестарта (как сейчас) |
| 6.3 | «Запустить опрос сейчас» | Новая строка poll run; карта обновляется |
| 6.4 | Блок health scheduler | `scheduler_health`: last_success_at, last_run, stale, stale_after_seconds |
| 6.5 | Stale | При остановленном `bawh-scheduler` (или старом poll) UI показывает stale=true |
| 6.6 | Non-admin `/admin/settings` | 403 |

- [ ] 6.1–6.6 пройдены

---

## 7. Пароли AD + обновления

| # | Шаг | Ожидание |
| --- | --- | --- |
| 7.1 | Admin: dashboard паролей AD (topbar) | Отчёт открывается |
| 7.2 | Non-admin: dashboard | 403 / пункт скрыт |
| 7.3 | `/admin/updates` | Страница открывается; нет регрессии форм update/rollback |
| 7.4 | После успешного update (если проверяете) | Перезапуск `bawh-web` + `bawh-scheduler` по sudoers |

- [ ] 7.1–7.4 пройдены

---

## 8. Публичные / session health probes

| # | Шаг | Ожидание |
| --- | --- | --- |
| 8.1 | `curl -s http://127.0.0.1:8000/health` | `{"status":"ok"}` без auth (probe install/Nginx) |
| 8.2 | `curl -s http://127.0.0.1/health` (через Nginx) | То же |
| 8.3 | `GET /api/login-services/status` | JSON статусов сервисов экрана входа (как до W1) |

- [ ] 8.1–8.3 пройдены

---

## 9. Deploy / scheduler / health

Цель W1-08 — **не ломать** install и юниты. Проверка на Debian 12 стенде после выката волны:

### Юниты

```bash
systemctl is-active bawh-web bawh-scheduler
systemctl status bawh-web bawh-scheduler --no-pager
# ExecStart ожидается:
#   bawh-web:       /opt/bawh/.venv/bin/gunicorn ... wsgi:app
#   bawh-scheduler: /opt/bawh/.venv/bin/python -m app.scheduler_worker
```

- [ ] Оба юнита `active`
- [ ] `EnvironmentFile=/opt/bawh/.env` подхватывается обоими
- [ ] После `systemctl restart bawh-scheduler` через ≤ 2× poll_interval появляется новый `network_poll_runs` со `finished_at` и mode `scheduled`

### Health после install / обновления

Install (`deploy/install-debian12.sh`) в конце вызывает `wait_for_health`:

1. `http://127.0.0.1:8000/health` (Gunicorn)
2. `http://127.0.0.1/health` через Nginx (Host = BAWH_SERVER_NAME)
3. опционально `http://<LAN_IP>/health`

Ручная сверка:

```bash
curl -fsS http://127.0.0.1:8000/health
journalctl -u bawh-web -n 50 --no-pager
journalctl -u bawh-scheduler -n 50 --no-pager
```

- [ ] `/health` ок до и после рестарта web
- [ ] Остановка только scheduler **не** валит `/health` web (probe остаётся ok; UI health → stale)
- [ ] Файлы `deploy/bawh-web.service`, `deploy/bawh-scheduler.service`, `deploy/nginx-bawh.conf` не требуют ручных правок ради W1 health
- [ ] Heartbeat/health данные для `/admin/settings` не меняют `ExecStart` scheduler и не добавляют обязательных новых env без записи в `.env.example`

### Регресс опасных путей (smoke)

- [ ] Ручной poll и scheduled poll не стартуют параллельно «в лоб» (вторая попытка — отказ / очередь как сейчас)
- [ ] Update UI по-прежнему перезапускает **оба** сервиса
- [ ] Fernet / PsExec учётка в Параметрах читается после рестарта web

---

## 10. Pytest (снимок)

Команда: `python -m pytest` (из корня репозитория, с зависимостями из `requirements.txt` + `pytest`).

| Дата (UTC) | Ветка | Результат | Примечание |
| --- | --- | --- | --- |
| 2026-10-02 | `feature/w1-09-regression-checklist` | **195 passed** in ~6s | База W1 без фич A2/A3; добавлен `tests/test_deploy_units.py` (юниты + `/health`) |

Полный зелёный прогон на **интеграционной** ветке после merge A1–A4 — зона координатора (DoD волны).

---

## 11. Критерии закрытия W1-09 (A5)

- [ ] Этот чеклист заполнен на стенде **или** блокеры перечислены с владельцем (A1–A4)
- [ ] Новые env (если появились у A3/A4) отражены в `.env.example` без секретов
- [ ] Install + systemd не сломаны изменениями health/heartbeat
- [ ] Нет критичных регрессий по §1–§8
- [ ] `pytest` зелёный на ветке A5; на интеграционной — после merge всех агентов

Связанные runbooks: [устройство offline](device-offline.md) (заготовка W4-04).
