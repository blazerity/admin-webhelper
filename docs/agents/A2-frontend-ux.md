# A2 — Frontend / UX (P2)

**Миссия:** шаблоны + static, ощущение «помощника» в браузере без смены visual language.

**Читать сначала:** `docs/ROADMAP.md` §2.A–B, `app/templates/base.html`, `app/templates/layouts/settings.html`, `app/static/js/map.js`, `app/static/css/app.css`.

## Backlog

### W1 (сейчас)

| ID | Задача | Файлы (ориентир) |
| --- | --- | --- |
| **W1-02** | Навигация + активные состояния по схеме A1 | `base.html`, `layouts/settings.html`, CSS nav |
| **W1-04** | Виджеты сводки на карте | `devices/map.html`, `map.js`, CSS; JSON от A3 |
| **W1-05** | Autocomplete через `/search/suggest` | `map.js` / `search.js`, поле поиска карты |
| **W1-06** | UI пресетов команд на вкладке commands | `devices/detail.html`, `_command_actions.html` |
| **W1-07** | UI «запустить скрипт» с карточки | тот же tab commands; не ломать `/scripts/runs/<id>` |

### W2

W2-01 multi-select + bulk bar; W2-03 прогресс batch; W2-04 фильтры в query string; W2-05 избранное (localStorage).

### W3–W4

W3-05 notification center UI; W3-06 фильтры UI; W4-02 self-host assets (с A5).

## Правила UI

- Развивать Manrope / teal / Bootstrap 5 из текущего `app.css`.
- Не вводить новую дизайн-систему, карточки в hero, purple-темы и т.п.
- Опасные кнопки скрывать по правам (источник прав — A4); скрытие ≠ защита.
- F5 должен сохранять фильтры карты (W2-04; в W1 — не ломать текущий state).

## Пересечения

- Контракты JSON — с A3 до вёрстки виджетов.
- Кнопки script/bulk — с A4.
- A11y/регресс пустых состояний — с A5.

## DoD агента на W1

- [ ] Операторские разделы доступны из topbar без обязательного захода в «Настройки».
- [ ] Сводка сети видна на карте (если API A3 готов) или есть graceful empty state.
- [ ] Suggest на карте работает; нет JS-ошибок в консоли Chrome/Firefox.
- [ ] Пресеты + запуск скрипта с карточки понятны без инструкции.
