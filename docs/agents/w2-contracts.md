# W2 — контракты и владение файлами

Координатор: интеграционная ветка `cursor/w2-implementation-85f5` (база — W1 `cursor/w1-implementation-2c6a`).
Агенты работают **каждый в своей ветке** от этой базы; мержит только координатор.

Статус продукта на старте W2: **0.3.0**. Цель волны — масштаб операций на карте без breaking changes.

---

## Владение файлами (жёстко)

| Агент | Можно менять | Нельзя трогать |
| --- | --- | --- |
| **A1** | `docs/adr/**`, `docs/ROADMAP.md`, `docs/agents/**`, `VERSION` (в конце) | продуктовый `app/` кроме согласованного merge |
| **A2** | `app/templates/**`, `app/static/**` | `app/services/`, `app/routes/`, `app/models/`, `app/authz.py` |
| **A3** | `app/services/**`, `app/routes/devices.py`, `app/routes/scripts.py`, `app/routes/diagnostics.py`, новые `tests/test_*batch*`, `test_*bulk*` | `app/templates/**`, `app/static/**`, `app/authz.py` |
| **A4** | `app/authz.py`, `tests/test_authz.py`, `.env.example` (только комментарии/ключи без секретов) | templates/static; не рефакторить ping/script pipeline |
| **A5** | `docs/runbooks/**`, доп. тесты без пересечения имён с A3/A4 | продуктовый UI/сервисы фич W2 (не дублировать A2/A3) |

Конфликт одного файла = остановиться и описать; не «перебивать» чужой код.

---

## API-контракты (A3 реализует, A2 потребляет)

### 1. Bulk ping — `POST /api/map/bulk/ping`

Session + login + CSRF. Body: `application/x-www-form-urlencoded` или JSON.

```
device_ids: list[int]   # 1..100
```

Authz: каждый device через `user_can_access_device`; недоступные → пропуск **или** 403 если ни одного доступного.  
Внутри: один `batch_id`, на каждое устройство `start_run(RunType.PING, ..., batch_id=...)`.

Ответ JSON:

```json
{
  "batch_id": "uuid",
  "run_ids": [1, 2],
  "accepted": 2,
  "skipped": 0,
  "progress_url": "/scripts/batches/<batch_id>"
}
```

### 2. Bulk script — `POST /api/map/bulk/script`

Только `user_can_run_scripts` (W2 = admin). CSRF.

```
device_ids: list[int]
script_id: int
```

Authz: `user_can_run_scripts` + каждое устройство `user_can_access_device`.  
Внутри: `start_script_on_devices` (уже даёт общий `batch_id`).

Ответ — как у bulk ping.

### 3. Batch status — `GET /api/batches/<batch_id>`

Login required. Возвращает только runs, видимые через `user_can_see_script_run`.  
Если ни одного видимого → 404.

```json
{
  "batch_id": "uuid",
  "total": 5,
  "pending": 1,
  "running": 1,
  "success": 2,
  "failed": 1,
  "cancelled": 0,
  "finished": false,
  "runs": [
    {
      "id": 1,
      "device_id": 10,
      "hostname": "PC1",
      "ip": "10.0.0.1",
      "status": "success",
      "run_type": "ping",
      "url": "/scripts/runs/1"
    }
  ]
}
```

`finished`: все runs в терминальных статусах (`success`/`failed`/`cancelled`/`error` — как в `RunStatus.FINISHED`).

### 4. Batch progress page — `GET /scripts/batches/<batch_id>`

SSR страница (A3 route + context; **разметку** делает A2 в `templates/scripts/batch.html`).  
Поллинг того же `GET /api/batches/<id>` из JS (можно расширить `run_log.js` или отдельный `batch.js`).

Кнопки: ссылка на каждый run; «Повторить failed» (POST, только для script/ping batch — A3: `POST /api/batches/<id>/retry-failed`) — **опционально в W2**, минимум: список + прогресс N/M.

Минимум W2: страница + JSON status + cancel отдельных runs через существующий cancel.

### 5. `/map/status` — новые устройства mid-session

Расширить payload: A2 при отсутствии карточки **добавляет** устройство в сектор (не только обновляет status).  
Поля device уже есть (`id`, `ip`, `hostname`, `status`, `kind`). Добавить при необходимости `url` карточки.

---

## URL-фильтры карты (A2)

Синхронизировать с query string (F5 / share):

| Param | Значение |
| --- | --- |
| `status` | `online`, `offline`, `unknown` (повторяемый или comma) |
| `type` | `notebook`, `desktop`, `other` |
| `q` | уже есть |
| `sector_id` | уже есть |
| `fav` | `1` — показать только избранные (W2-05) |

При клике фильтров — `history.replaceState` без полной перезагрузки. При загрузке — прочитать URL и активировать кнопки.

---

## Избранное (A2)

localStorage key: `bawh.map.favorites` = JSON array of device id numbers.  
UI: звезда/toggle на карточке устройства; фильтр «Избранные» в toolbar.  
Без таблицы БД в W2.

---

## Multi-select + bulk bar (A2)

- Checkbox / click-to-select на `[data-device-card]` (не ломая клик по ссылке карточки — отдельный hit-target).
- Sticky bulk bar: «Выбрано N», Ping, Запустить скрипт (если `data-can-run-scripts`), Снять выбор.
- Script: модалка/панель с select скриптов (список в `data-scripts-json` на map от SSR — A3 кладёт в context map только если `user_can_run_scripts`).
- После bulk → redirect или navigate на `progress_url`.

---

## Authz bulk (A4)

- Хелперы: `filter_accessible_devices(user, device_ids) -> list[Device]`; для script — дополнительно `user_can_run_scripts`.
- Route-тесты: non-admin POST bulk script → 403; device чужого сектора не попадает в batch.
- Не реализовывать operator в W2 — только точки enforce под будущее.

---

## DoD волны

- [ ] W2-01…W2-07 сделаны и в `cursor/w2-implementation-85f5`
- [ ] pytest зелёный
- [ ] Нет breaking URL
- [ ] VERSION → **0.4.0**
- [ ] `docs/runbooks/w2-regression-checklist.md`
