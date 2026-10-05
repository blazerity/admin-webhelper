# UI-аудит bAWH — сводка

К [`UI_GUIDEBOOK.md`](UI_GUIDEBOOK.md).

Источники аудита:
- [UI bugs audit](bc-e4f62fa3-aa91-5fc8-b508-1831caf85414)
- [Guideline compliance audit](bc-98a563cd-f273-5894-bc83-6b7be498851b)
- [Duplicate functions audit](bc-1b283f6c-3b21-50f3-95ce-6d89b31c63f8)

## Внедрено

| ID | Изменение |
| --- | --- |
| X1 | Operator видит запуск скрипта на карточке; CLI — admin |
| X2 | password_viewer: только отчёт, без admin-CTA |
| X3 | Poll-actions за `can_run_diagnostics` |
| X4 | Bulk Ping скрыт без diagnostics |
| X5 | ICMP-check только на вкладке «Опросы» |
| X6 | Тумблер schedule только на dashboard |
| X7 | Опрос железа только на «Оборудование» |
| X8 | SMTP в Параметры → Почта |
| + | entity-link на run/batch, focus ring терминала, interactive=false на секторах, copy/IA polish |

Остаточные low-severity (hex вне токенов, table-sm на части списков) — не блокируют.
