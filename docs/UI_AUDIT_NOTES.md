# UI-аудит bAWH

Рабочие заметки к [`UI_GUIDEBOOK.md`](UI_GUIDEBOOK.md).

Источники:
- [Duplicate functions audit](bc-1b283f6c-3b21-50f3-95ce-6d89b31c63f8)
- [UI bugs audit](bc-e4f62fa3-aa91-5fc8-b508-1831caf85414)
- ожидает: соответствие гайдлайну

---

## Critical bugs (роль ≠ UI)

| # | Проблема | Файл | Фикс |
| --- | --- | --- | --- |
| B1 | Operator не видит запуск скрипта на карточке; copy «только администратору» | `_command_actions.html` | Скрипты — `user_can_run_scripts`; CLI/пресеты — admin |
| B2 | password_viewer видит admin-кнопки (настройки, прогон, пауза, напомнить) → 403 | `password_expiry/dashboard.html` | Мутации / settings-link за `is_admin` |
| B3 | Вкладка Опросы: ping/tracert/check без `can_run_diagnostics` | `_poll_actions.html` | Рендер только при праве diagnostics |
| B4 | Bulk Ping на карте виден viewer → 403 | `map.html` / `devices.map` | Передать `can_run_diagnostics`, скрыть кнопку |

## Medium (copy / мёртвый UI / a11y)

- Пустой список скриптов: устаревший текст про «backend передаст…»
- Пустое железо: обещание кнопки «Опросить» не-admin
- `sectors/detail.html`: select/fav без `map.js`
- Фильтр типов карты по умолчанию прячет инфраструктуру
- Live CSV «Действия» не синхронизирует `q`
- Terminal input: `outline: none` без focus ring
- Operator: «Изменить скрипт» вместо «Запуск»
- Stale copy «После мержа A3…» в Параметрах
- Онлайн vs «доступен» — разные словари статусов

## Дубли функций (§10)

| # | Дубль | Канон |
| --- | --- | --- |
| D1 | ICMP «Проверить сейчас» Обзор + Опросы | Канон — вкладка Опросы |
| D2 | Тумблер schedule на dashboard + settings | Канон — только dashboard |
| D3 | «Опросить железо» Обзор + Оборудование | Канон — Оборудование |
| D4 | SMTP под «Пароли AD» | Общий раздел настроек |
| D5 | Narrative планировщика в 4 местах | Канон — Параметры |

Object-scoped (скрипт с карты/карточки, bulk vs single, deep-link dashboard↔settings) — оставить.

---

## Кандидаты в финальные 5 улучшений

1. Выровнять role-gates: карточка команд, polls, bulk ping, password dashboard (B1–B4).  
2. ICMP на карточке: один канонический вход + ясные лейблы Ping(лог)/Tracert (D1 + B3).  
3. Тумблер расписания отчётов — только на dashboard (D2).  
4. Железо: опрос только на вкладке Оборудование; Обзор — снимок + ссылка (D3).  
5. SMTP вынести из «Пароли AD» в общий раздел; плюс почистить ложный copy (D4, medium).
