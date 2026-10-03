# W3 — регресс-чеклист приёмки

Ручная приёмка волны **W3 (сигналы + Authz v2)** перед merge интеграционной ветки
`cursor/w3-implementation-85f5` в `main` (или следующий релизный канал). Контракты: [`docs/agents/w3-contracts.md`](../agents/w3-contracts.md).

**Версия на старте волны:** 0.4.0 → цель после волны: **0.5.0** (bump делает координатор / A1).

Отмечайте `[x]` на стенде. Блокеры — в PR / эскалация A1. UI (§4 колокольчик, §5 пагинация/watch на карточке, topbar ролей) — после merge **A2**; API/authz/audit (§1–§3, §5 сервис) — **A4**; watchlist/notifications backend (§4) — **A3**.

Связанные чеклисты: [W1](w1-regression-checklist.md), [W2](w2-regression-checklist.md) — после W3 прогнать smoke §6.

---

## 0. Подготовка стенда

- [ ] Код волны установлен (интеграционная ветка W3 с merge A4→A3→A2), миграции `0009` + `0010` применены.
- [ ] `bawh-web` и `bawh-scheduler` активны.
- [ ] Учётки / группы LDAP (или локальные флаги после логина):
  - **admin** (`LDAP_ADMIN_GROUP`)
  - **operator** (`LDAP_OPERATOR_GROUP`) с ACL на сектор
  - **viewer** (`LDAP_VIEWER_GROUP`) с ACL на сектор
  - **password_viewer** (`LDAP_PASSWORD_VIEWER_GROUP`)
  - **sector_user** без новых ролей, с ACL на сектор
  - **no_access** — без ролей и без sector_access
- [ ] В секторе ≥ 1 устройство; в библиотеке ≥ 1 published и ≥ 1 draft скрипт (admin).
- [ ] Для watchlist: устройство, которое можно перевести в offline (или дождаться poll).

Автотесты:

```bash
python3 -m pytest -q
```

Снимок прогона A5 — в [§ Pytest](#7-pytest-снимок).

---

## 1. Матрица ролей (W3-01 / W3-02)

| # | Роль | Карта / устройства | Ping / diagnostics | Скрипты (list / run) | CRUD скриптов | Пароли AD dashboard | Пароли AD settings | Audit |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1.1 | **admin** | все сектора | да | все (published + draft) | да | да | да | да |
| 1.2 | **operator** | только ACL | да | list + run **published** only | нет (403) | нет | нет | нет |
| 1.3 | **viewer** | только ACL (read) | **нет** (403) | нет (403) | нет | нет | нет | нет |
| 1.4 | **password_viewer** | по ACL / как sector | ping как sector_user (не viewer) | нет | нет | **да** | **нет** (403) | нет |
| 1.5 | **sector_user** (без новых ролей) | только ACL | да | нет (403) | нет | нет | нет | нет |
| 1.6 | **no_access** | пустая карта | нет устройств | нет | нет | нет | нет | нет |

Дополнительно на стенде:

| # | Шаг | Ожидание |
| --- | --- | --- |
| 1.7 | Admin: topbar | «Скрипты», «Пароли AD», вход в Настройки; в settings — пункт «Аудит» |
| 1.8 | Operator: topbar | «Скрипты» есть; «Пароли AD» нет (если не password_viewer) |
| 1.9 | Password_viewer: topbar | «Пароли AD» есть; «Скрипты» нет (если не operator/admin) |
| 1.10 | Viewer: bulk ping / device ping | 403; UI не предлагает diagnostics |
| 1.11 | Operator: draft script run (library / card / bulk) | 403; published — ок на доступном устройстве |
| 1.12 | Admin: checkbox «Опубликован для операторов» на форме скрипта | Сохраняется (`is_published`); operator видит только published в списке |
| 1.13 | Прямой POST в обход UI (sector_user → script run, viewer → ping) | 403, не 500 |

- [ ] 1.1–1.13 пройдены (или блокер A4/A2)

---

## 2. Audit log (W3-03)

| # | Шаг | Ожидание |
| --- | --- | --- |
| 2.1 | Admin: `GET /admin/audit` | Страница списка; записи с actor, action, entity, временем |
| 2.2 | Non-admin (любая роль кроме admin) | **403** |
| 2.3 | Admin: создать/изменить/удалить сектор | Строка audit (`sector.*`) |
| 2.4 | Admin: создать/править/удалить скрипт | Строка audit (`script.*`) |
| 2.5 | Admin: сохранить параметры / критичный settings POST | Строка audit |
| 2.6 | Admin: update/rollback (если доступно на стенде) | Строка audit; без регресса update UI |

- [ ] 2.1–2.6 пройдены

---

## 3. Watchlist (W3-04)

| # | Шаг | Ожидание |
| --- | --- | --- |
| 3.1 | Device detail: Watch с `offline_minutes` | `POST /devices/<id>/watch` → `watching=true`; порог сохранён |
| 3.2 | Unwatch | `POST .../unwatch` → watching сброшен |
| 3.3 | Повторный watch того же device | Upsert (UNIQUE user+device), порог обновлён |
| 3.4 | Device offline дольше N минут + запись в watchlist | После poll/evaluate — notification `device_offline` |
| 3.5 | Dedupe | Пока offline — повторный evaluate **не** спамит; после online → снова offline — новый алерт допустим |
| 3.6 | Чужое устройство (вне ACL) | Watch/unwatch → 403/404; не создаёт чужой watch |
| 3.7 | Без логина | redirect /login |

- [ ] 3.1–3.7 пройдены

---

## 4. In-app notifications (W3-05)

| # | Шаг | Ожидание |
| --- | --- | --- |
| 4.1 | `GET /api/notifications` (session) | `{items: [...], unread: N}` только свои |
| 4.2 | Колокольчик в topbar (A2) | Badge unread; панель ленты без ошибок консоли |
| 4.3 | `POST /api/notifications/read` с `ids` | Отмеченные `read_at`; unread уменьшается |
| 4.4 | `POST .../read` с `all: true` | Все свои прочитаны; unread=0 |
| 4.5 | Чужие id в `ids` | Не отмечают чужие; нет 500 |
| 4.6 | Без логина | 302/401 |
| 4.7 | Kind `device_offline` из watchlist | title/body/link осмысленны (ссылка на устройство) |
| 4.8 | `script_failed` (если включено) | Уведомление автору failed run; без дубля при повторном вызове helper |

- [ ] 4.1–4.8 пройдены (UI — при merge A2)

---

## 5. Пагинация actions & accounts (W3-06)

| # | Шаг | Ожидание |
| --- | --- | --- |
| 5.1 | `GET /actions/?page=1&per_page=10` | 200; список + Bootstrap pagination; total/page согласованы |
| 5.2 | `kind=` / `q=` фильтры | Сужают выдачу; query сохраняется в ссылках страниц |
| 5.3 | `GET /accounts/?page=1&per_page=10&q=...` | 200; пагинация + поиск |
| 5.4 | `per_page` вне разумных границ / `page` за пределами | Понятное поведение (clamp / пустая страница), не 500 |
| 5.5 | Non-admin | Только видимые по ACL данные (нет утечки чужих actions/accounts) |

- [ ] 5.1–5.5 пройдены

---

## 6. Smoke W1 / W2 (всё ещё зелёное)

Краткий прогон критичного из [W1](w1-regression-checklist.md) и [W2](w2-regression-checklist.md):

| # | Шаг | Ожидание |
| --- | --- | --- |
| 6.1 | Login / logout / ACL карта | Как W1 §1; admin из `LDAP_ADMIN_GROUP` без регресса |
| 6.2 | Topbar IA + settings sidebar | Как W1 §2; добавлены Аудит / колокольчик без breaking URL |
| 6.3 | Карта suggest + network summary | Как W1 §3 |
| 6.4 | Device tabs, пресеты, script-from-card | Как W1 §4–§5; operator: published only |
| 6.5 | Admin settings / scheduler health / `/health` | Как W1 §6 / §8 |
| 6.6 | Multi-select + bulk ping | Как W2 §1–§2; viewer bulk ping запрещён |
| 6.7 | Bulk script + batch progress | Как W2 §3–§4; operator может published; sector_user — 403 |
| 6.8 | URL-фильтры + избранное localStorage | Как W2 §5–§6 |
| 6.9 | Пароли AD + updates | Как W1 §7; password_viewer — dashboard да, settings нет |

- [ ] 6.1–6.9 smoke ок

---

## 7. Pytest (снимок)

Команда: `python3 -m pytest -q` (корень репозитория, deps из `requirements.txt` + `pytest`).

| Дата (UTC) | Ветка | Результат | Примечание |
| --- | --- | --- | --- |
| 2026-10-02 | `cursor/w3-07-regression-85f5` | **273 passed** in ~9s | База integration W3 + A5 checklist; edge `no_access` / viewer HTTP в `test_authz` |

Полный зелёный прогон на **интеграционной** после merge A2 — зона координатора (DoD волны).

---

## 8. Критерии закрытия W3-07 (A5)

- [ ] Этот чеклист заполнен на стенде **или** блокеры с владельцем (A1–A4)
- [ ] `docs/runbooks/README.md` ссылается на W3 checklist
- [ ] Матрица admin / operator / viewer / password_viewer / sector_user / no_access покрыта в `tests/test_authz.py` (и доп. edge A5 при пробелах)
- [ ] Нет breaking URL относительно W1/W2
- [ ] `pytest` зелёный на ветке A5; на интеграционной — после merge A2
- [ ] VERSION → **0.5.0** делает координатор, не A5

Связанные runbooks: [W1](w1-regression-checklist.md), [W2](w2-regression-checklist.md), [устройство offline](device-offline.md).
