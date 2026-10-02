# W2 — регресс-чеклист приёмки

Ручная приёмка волны **W2 (масштаб операций на карте)** перед merge интеграционной ветки
`cursor/w2-implementation-85f5` в `main` (или следующий релизный канал). Контракты: [`docs/agents/w2-contracts.md`](../agents/w2-contracts.md).

**Версия на старте волны:** 0.3.0 → цель после волны: **0.4.0** (bump делает координатор / A1).

Отмечайте `[x]` на стенде. Блокеры — в PR / эскалация A1. UI-пункты (§1–§3, §5–§6) требуют merge **A2** (`cursor/w2-01-map-bulk-ui-85f5`); API/authz (§4, §7) — после merge **A3/A4**.

Связанный чеклист базы: [w1-regression-checklist.md](w1-regression-checklist.md) — после W2 прогнать smoke §8.

---

## 0. Подготовка стенда

- [ ] Код волны установлен (интеграционная ветка W2 с merge A2+A3+A4), миграции применены.
- [ ] `bawh-web` и `bawh-scheduler` активны.
- [ ] Учётки: **admin** и **non-admin** с ACL на хотя бы один сектор (и устройство вне ACL для негативных проверок).
- [ ] В секторе ≥ 2 устройств; в библиотеке ≥ 1 скрипт (admin).
- [ ] Браузер с чистым localStorage для ключа `bawh.map.favorites` (или готовы сбросить).

Автотесты:

```bash
python3 -m pytest -q
```

Снимок прогона A5 — в [§ Pytest](#9-pytest-снимок).

---

## 1. Карта: multi-select + bulk bar (W2-01)

| # | Шаг | Ожидание |
| --- | --- | --- |
| 1.1 | Admin: `GET /` | Карточки с `[data-device-card]`; отдельный hit-target выбора (не ломает клик по ссылке карточки) |
| 1.2 | Выбрать 1–N устройств | Sticky bulk bar: «Выбрано N», Ping, Запустить скрипт, Снять выбор |
| 1.3 | Admin bulk bar | Есть кнопка скрипта (`data-can-run-scripts` / скрипты из `data-scripts-json`) |
| 1.4 | Non-admin bulk bar | Ping доступен (если ACL); кнопки/модалки скрипта нет |
| 1.5 | Снять выбор | Бар скрыт / N=0; карточки не выделены |
| 1.6 | Клик по ссылке карточки при multi-select | Переход на `/devices/<id>` без ложного toggle (или toggle только на checkbox/hit-target) |

- [ ] 1.1–1.6 пройдены (или блокер A2)

---

## 2. Bulk ping (W2-02 / API A3)

| # | Шаг | Ожидание |
| --- | --- | --- |
| 2.1 | Admin: выбрать устройства → Ping | `POST /api/map/bulk/ping` (session + CSRF) → JSON `batch_id`, `run_ids`, `accepted`, `skipped`, `progress_url` |
| 2.2 | После успеха | Redirect / navigate на `/scripts/batches/<batch_id>` |
| 2.3 | Non-admin: свои + чужие id | Чужие skipped; если ни одного доступного → 403 |
| 2.4 | > 100 device_ids | 400 |
| 2.5 | Без логина | 302/401 |

- [ ] 2.1–2.5 пройдены

---

## 3. Bulk script (W2-03)

| # | Шаг | Ожидание |
| --- | --- | --- |
| 3.1 | Admin: выбрать устройства → скрипт из модалки | `POST /api/map/bulk/script` → тот же shape ответа, что ping; `progress_url` |
| 3.2 | После успеха | Страница пачки; runs со `run_type` script |
| 3.3 | Non-admin прямой POST | **403** (даже на свои устройства) |
| 3.4 | Device вне ACL у admin-смешанного набора | Недоступные skipped; batch только по доступным |
| 3.5 | Нет script_id / несуществующий script | 400 / понятная ошибка, не 500 |

- [ ] 3.1–3.5 пройдены

---

## 4. Batch progress page + API (W2-03)

| # | Шаг | Ожидание |
| --- | --- | --- |
| 4.1 | `GET /scripts/batches/<batch_id>` (видимые runs) | SSR: `#batch-progress`, список runs со ссылками на `/scripts/runs/<id>`, счётчики N/M |
| 4.2 | `GET /api/batches/<batch_id>` | JSON по контракту; `finished` когда все терминальные |
| 4.3 | Поллинг со страницы | JS обновляет статусы без полной перезагрузки |
| 4.4 | Несуществующий / чужой batch (нет видимых runs) | API и SSR → **404** |
| 4.5 | Cancel отдельного run | Существующий cancel с run page; batch отражает `cancelled` |
| 4.6 | Без логина | batch page и API → login redirect / 401 |

- [ ] 4.1–4.6 пройдены

---

## 5. URL-фильтры карты F5 / share (W2-04)

| # | Шаг | Ожидание |
| --- | --- | --- |
| 5.1 | Клик фильтров status/type | `history.replaceState` обновляет query (`status`, `type`); без полной перезагрузки |
| 5.2 | F5 / открыть URL с `?status=online&type=notebook` | Кнопки активны; видны только подходящие карточки |
| 5.3 | `q`, `sector_id` | По-прежнему работают вместе с новыми фильтрами |
| 5.4 | `fav=1` | Только избранные (см. §6); сброс фильтра убирает param |
| 5.5 | Сбросить фильтры | Query очищается от filter-параметров |

- [ ] 5.1–5.5 пройдены (или блокер A2)

---

## 6. Избранное localStorage (W2-05)

| # | Шаг | Ожидание |
| --- | --- | --- |
| 6.1 | Toggle звезды на карточке | `localStorage['bawh.map.favorites']` = JSON array of numeric device ids |
| 6.2 | F5 | Состояние звёзд сохраняется |
| 6.3 | Фильтр «Избранные» / `fav=1` | Видны только избранные; пустое состояние без JS-ошибок |
| 6.4 | Снять все избранные | Ключ `[]` или отсутствует; фильтр не показывает чужие устройства |
| 6.5 | Нет записи в БД | После logout/login на том же браузере избранное локальное (ожидаемо для W2) |

- [ ] 6.1–6.5 пройдены (или блокер A2)

---

## 7. Authz bulk (W2-06)

| # | Шаг | Ожидание |
| --- | --- | --- |
| 7.1 | Non-admin `POST /api/map/bulk/script` | 403 |
| 7.2 | Non-admin bulk ping только свой сектор | Чужой sector device не в `accepted` / не в batch |
| 7.3 | Admin `map_scripts` в context карты | Список скриптов для bulk UI; non-admin → пустой список |
| 7.4 | UI non-admin | Нет точки входа «Запустить скрипт» в bulk bar |
| 7.5 | Operator role | Не реализуется в W2 — только enforce-точки; регресс не требует operator |

- [ ] 7.1–7.5 пройдены

---

## 8. Map mid-session: новое устройство + smoke W1

### 8.1 `/map/status` mid-session

| # | Шаг | Ожидание |
| --- | --- | --- |
| 8.1.1 | Пока карта открыта, в секторе появляется новое устройство (poll / другой клиент) | Следующий `/map/status` отдаёт device с `id`, `ip`, `hostname`, `status`, `kind`, `url` |
| 8.1.2 | UI (A2) | Карточка **добавляется** в сектор, не только обновляются уже отрисованные |
| 8.1.3 | Устройство исчезло / сменило статус | Статус/счётчики сектора обновляются без ошибок консоли |

- [ ] 8.1.1–8.1.3 пройдены (UI — при merge A2; JSON `url` — A3)

### 8.2 Smoke W1 (всё ещё зелёное)

Краткий прогон критичного из [W1 checklist](w1-regression-checklist.md):

| # | Шаг | Ожидание |
| --- | --- | --- |
| 8.2.1 | Login / logout / non-admin ACL | Как W1 §1 |
| 8.2.2 | Topbar IA, settings sidebar | Как W1 §2; нет breaking URL |
| 8.2.3 | Карта suggest + `/api/network/summary` | Как W1 §3 |
| 8.2.4 | Карточка: tabs, пресеты, script-from-card | Как W1 §4–§5 |
| 8.2.5 | Admin settings / scheduler health / `/health` | Как W1 §6 / §8 |
| 8.2.6 | Пароли AD + updates | Как W1 §7 |

- [ ] 8.2.1–8.2.6 smoke ок

---

## 9. Pytest (снимок)

Команда: `python3 -m pytest -q` (корень репозитория, deps из `requirements.txt` + `pytest`).

| Дата (UTC) | Ветка | Результат | Примечание |
| --- | --- | --- | --- |
| _(заполнить после прогона)_ | `cursor/w2-07-regression-85f5` | | База integration + A5 checklist/smoke; UI A2 может быть ещё не влита |

Полный зелёный прогон на **интеграционной** после merge A2 — зона координатора (DoD волны).

---

## 10. Критерии закрытия W2-07 (A5)

- [ ] Этот чеклист заполнен на стенде **или** блокеры с владельцем (A1–A4)
- [ ] `docs/runbooks/README.md` ссылается на W2 checklist
- [ ] Edge pytest (map smoke / batch 404 / login) зелёные и не пересекают имена A3/A4
- [ ] Нет breaking URL относительно W1
- [ ] `pytest` зелёный на ветке A5; на интеграционной — после merge A2
- [ ] VERSION → **0.4.0** делает координатор, не A5

Связанные runbooks: [W1](w1-regression-checklist.md), [устройство offline](device-offline.md).
