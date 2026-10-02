# A5 — QA, платформа, деплой, документация (P5)

**Миссия:** принять волну до merge в `main`; air-gap/deploy/runbooks; ловить регрессии.

**Читать сначала:** `docs/ROADMAP.md` §2.E и §6, `deploy/install-debian12.sh`, `deploy/*.service`, `tests/conftest.py`, `.env.example`.

## Backlog

### W1 (сейчас)

| ID | Задача | Артефакт |
| --- | --- | --- |
| **W1-09** | Регресс-чеклист W1 + тесты edge cases | `docs/runbooks/w1-regression-checklist.md` + недостающие pytest |
| Поддержка A2/A3 | Пустые состояния, flaky | правки тестов, не смена продуктового scope |
| Deploy sanity | scheduler/web юниты | убедиться, что health/heartbeat W1-08 не ломает install |

### W2

**W2-07** регресс W2 + нагрузка карты на большом секторе.

### W3–W4

W3-07 матрица прав + security review с A1; W4-02 self-host assets; W4-04 runbooks (`устройство offline`, `WMI`, `PsExec`, `пароли AD`).

## Чеклист приёмки W1 (минимум)

1. Логин LDAP / logout  
2. Карта: загрузка, suggest, сводка (если есть)  
3. Карточка устройства: вкладки overview / accounts / commands / polls  
4. Пресет команды + скрипт с карточки + страница run / cancel  
5. Admin: settings, ручной poll, health scheduler  
6. Пароли AD dashboard (admin)  
7. Updates page не регрессит  
8. `GET /health`, `GET /api/login-services/status`  
9. Mobile/desktop nav, фокус/aria toggle меню  

## Пересечения

- Блокирует релиз, если DoD A1–A4 не выполнены → эскалация A1.
- Новые env → `.env.example` без секретов.
- A11y: online/offline контраст, aria у nav toggle.

## DoD агента на W1

- [ ] Чеклист W1 в `docs/runbooks/` пройден на стенде (или зафиксированы блокеры).
- [ ] Новые/изменённые env задокументированы.
- [ ] Install-скрипт и systemd не сломаны изменениями scheduler/health.
- [ ] Критичных регрессий по чеклисту нет.
