# UI-аудит bAWH — сводка

К [`UI_GUIDEBOOK.md`](UI_GUIDEBOOK.md). Источники:

- [UI bugs audit](bc-e4f62fa3-aa91-5fc8-b508-1831caf85414)
- [Guideline compliance audit](bc-98a563cd-f273-5894-bc83-6b7be498851b) — **6.5 / 10**
- [Duplicate functions audit](bc-1b283f6c-3b21-50f3-95ce-6d89b31c63f8)

---

## Критические пересечения (баг ∩ гайдлайн ∩ дубль)

| ID | Находка | Где |
| --- | --- | --- |
| X1 | Operator не видит запуск скрипта на карточке; ложный copy «только admin» | `_command_actions.html` |
| X2 | `password_viewer` видит admin-CTA → 403 | `password_expiry/dashboard.html` |
| X3 | Poll-actions без `can_run_diagnostics` (viewer → 403) | `_poll_actions.html` |
| X4 | Bulk Ping на карте без role-gate | `map.html` |
| X5 | ICMP «Проверить сейчас» дублируется Обзор ↔ Опросы | `detail.html`, `_poll_actions.html` |
| X6 | Тумблер schedule на dashboard **и** в settings | pwd + sector modules |
| X7 | «Опросить железо» на Обзоре и Оборудовании | `detail.html` |
| X8 | SMTP спрятан под «Пароли AD» | settings IA |

Compliance дополнительно: actions не в sidebar устройства (§2.3), мало `entity-link` на run/batch, hex вне токенов.

---

## Пять приоритетных улучшений

См. ответ руководителю направления / PR description. Критерий отбора: упрощение IA + соответствие гайдбуку + снятие дублей / ложных кнопок.
