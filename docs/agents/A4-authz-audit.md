# A4 — Authz, идентичность, аудит, пароли AD (P4)

**Миссия:** права и аудит additive к текущему `is_admin` + `sector_access`; password expiry не ломать.

**Читать сначала:** `app/authz.py`, `docs/ROADMAP.md` §2.D, `app/services/ldap_service.py`, `password_expiry_*`, `tests/test_authz.py`.

## Backlog

### W1 (сейчас — подготовка + точечный enforce)

| ID | Задача | Примечание |
| --- | --- | --- |
| **Подготовка W3** | ADR: LDAP groups → `viewer` / `operator` / `password_viewer` / `admin` | Код ролей — W3; документ и `.env.example` черновик ключей — сейчас |
| **W1-07 (помощь)** | Authz на запуск скрипта с карточки | Нельзя обойти UI прямым POST; non-admin пока как сейчас (без script), пока нет operator |
| Review селектов | Точки, где в форму попадают все устройства | Зафиксировать список для W2/W3 harden |

### W2

**W2-06** Authz на bulk под будущего operator.

### W3 (основной объём)

| ID | Задача |
| --- | --- |
| **W3-01** | Модель ролей + `.env`/LDAP mapping |
| **W3-02** | Operator: только опубликованные скрипты на своих секторах |
| **W3-03** | `admin_audit_log` + запись sector/admin/scripts/update |
| **W3-06** | Фильтры/пагинация `/actions`, `/accounts` (с A2) |

## Правила безопасности

- UI-скрытие не считается защитой.
- 403/404-паттерн: не светить чужие устройства.
- Старые админы из `LDAP_ADMIN_GROUP` ведут себя как раньше.
- P1 review обязателен на любой PR по authz/credentials.

## Тесты

Матрица: admin / operator / viewer / user с `sector_access` / без доступа — `tests/test_authz.py` + route-тесты.

## DoD агента на W1

- [ ] ADR ролей согласован с A1 (готово к W3).
- [ ] W1-07: POST запуска с карточки проходит через authz как существующие script routes.
- [ ] Список дыр в селектах задокументирован для W2/W3.
