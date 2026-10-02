# A1 — Tech Lead / Архитектор продукта (P1)

**Миссия:** целостность продукта, совместимость URL/ролей/SSR, релизный ритм волны.

**Читать сначала:** `docs/ROADMAP.md` §1–4, `docs/ARCHITECTURE.md`, `README.md` (структура + маршруты).

## Backlog

### W1 (сейчас)

| ID | Задача | Артефакт |
| --- | --- | --- |
| **W1-01** | Схема новой навигации | ADR/спека: topbar = Карта, Действия, УЗ, Скрипты; settings = Секторы, Параметры, Обновления, Сервисы входа, Пароли AD settings. Старые URL живы. |
| **W1-10** | Review совместимости, VERSION, merge | Проверка PR A2–A5; bump `VERSION` при закрытии волны; краткий пункт в README/ARCHITECTURE при структурных изменениях |

### Позже

- W3: review Authz v2 и audit (обязательный review A4).
- W4: контракты `/api/v1/` aliases, semver.
- Ведение статусов в `docs/ROADMAP.md` после каждой волны.

## Зона касания

- `docs/ARCHITECTURE.md`, `docs/ROADMAP.md`, `docs/agents/`
- `VERSION`
- Review (не обязательно author): `app/authz.py`, `app/services/credential_service.py`, `app/services/update_service.py`, `app/services/ping_service.py`

## Не делать в одиночку

Всю вёрстку волны; весь feature-код устройств/скриптов.

## DoD агента на W1

- [ ] Опубликована схема навигации (W1-01), A2 может реализовывать без догадок.
- [ ] Все PR волны прошли review на breaking changes (URL, authz, scheduler/WMI main-thread).
- [ ] `VERSION` обновлён; README/ARCHITECTURE согласованы с фактом.
- [ ] A2–A5 отметили свои DoD.

## Смоук после деплоя

`GET /health` → логин LDAP → карта → ручной poll → не трогать Fernet/update без нужды.
