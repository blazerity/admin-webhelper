# ADR 001 — Операторская навигация (topbar vs settings)

| Поле | Значение |
| --- | --- |
| Статус | Accepted |
| Дата | 2026-10-02 |
| Волна / задача | W1 / **W1-01** |
| Автор | A1 (Tech Lead) |
| Реализация UI | **A2** (`app/templates/base.html`, `layouts/settings.html`) |

Связано: [w1-contracts.md § IA](../agents/w1-contracts.md), [ROADMAP.md §2.A](../ROADMAP.md), [A2-frontend-ux.md](../agents/A2-frontend-ux.md).

---

## Контекст

Сейчас в topbar только «Карта сети», «Пароли AD» (admin) и «Настройки». Действия, УЗ и Скрипты живут в sidebar settings-layout — операторский путь размыт (ROADMAP §2.A).

Цель W1: разделить **операторские** разделы (ежедневная работа) и **админские/справочники настроек**, не ломая существующие URL и роли `is_admin` / non-admin.

---

## Решение

### 1. Topbar (основное меню)

Порядок пунктов слева направо:

| # | Подпись | Endpoint (href) | Кто видит |
| --- | --- | --- | --- |
| 1 | Карта сети | `devices.map` → `/` | все authenticated |
| 2 | Действия | `actions.list_actions` → `/actions` | все authenticated |
| 3 | Учётные записи | `accounts.list_accounts` → `/accounts` | все authenticated |
| 4 | Скрипты | `scripts.list_scripts` → `/scripts` | **только admin** |
| 5 | Пароли AD | `password_expiry.dashboard` → `/password-expiry` | **только admin** |
| 6 | Настройки | см. §3 → вход в settings-layout | все authenticated |

Бренд `app_name` → `devices.map` без изменений.

### 2. Settings sidebar

Оставить только админские / конфигурационные разделы. **Убрать** из sidebar дубли topbar: Действия, Учётные записи, Скрипты.

| # | Подпись | Endpoint (href) | Кто видит |
| --- | --- | --- | --- |
| 1 | Сектора | `sectors.list_sectors` → `/sectors` | все authenticated (как сейчас) |
| 2 | Параметры | `admin.settings` → `/admin/settings` | **только admin** |
| 3 | Обновления | `admin.updates` → `/admin/updates` | **только admin** |
| 4 | Экран входа | `login_services.list_services` → `/login-services` | **только admin** |
| 5 | Пароли AD | `password_expiry.settings_page` → `/password-expiry/settings` | **только admin** |

Порядок в sidebar: Сектора → Параметры → Обновления → Экран входа → Пароли AD (settings). Допускается лёгкая перестановка admin-пунктов, если визуально привычнее текущему layout; **семантика** — без Действий/УЗ/Скриптов.

### 3. Вход «Настройки»

- Href пункта «Настройки» в topbar: `sectors.list_sectors` (как сейчас) — для admin и non-admin.
- Non-admin после W1 в sidebar видит только **Сектора**; операторские разделы УЗ/Действия — в topbar.
- Admin попадает в тот же layout и дальше переходит по sidebar.

### 4. Матрица видимости

| Раздел | Admin | Non-admin |
| --- | --- | --- |
| Карта сети | ✓ | ✓ |
| Действия | ✓ | ✓ |
| Учётные записи | ✓ | ✓ |
| Скрипты | ✓ | ✗ (скрыт в UI; authz на routes без изменений в W1) |
| Пароли AD (отчёт) | ✓ | ✗ |
| Настройки → Сектора | ✓ | ✓ |
| Настройки → Параметры / Обновления / Экран входа / Пароли AD settings | ✓ | ✗ |

Страницы `/accounts` и `/actions` для non-admin **уже** существуют и доступны через settings; после W1 меняется только точка входа (topbar), не права.

### 5. Совместимость URL

**Не удалять и не переименовывать** маршруты. Все текущие пути остаются валидными:

| Путь | Статус после W1 |
| --- | --- |
| `/`, `/devices/<id>` | без изменений |
| `/actions`, `/accounts`, `/scripts`, `/password-expiry` | без изменений; вход чаще из topbar |
| `/sectors`, `/admin/settings`, `/admin/updates`, `/login-services`, `/password-expiry/settings` | без изменений; вход из settings |
| `/search`, `/search/api` | без изменений (alias suggest — A3) |

Закладки и внешние ссылки продолжают работать. Меняется только состав ссылок в шаблонах.

### 6. Active-state: правила по `request.endpoint`

A2 реализует в `base.html` / `layouts/settings.html`. Одна страница — **один** активный пункт (не подсвечивать одновременно topbar «Настройки» и topbar «Действия»).

Пусть `ep = request.endpoint or ''`.

#### Topbar

| Пункт | Active, если |
| --- | --- |
| Карта сети | `ep == 'devices.map'` **или** `ep == 'devices.detail'` (и при необходимости `ep.startswith('diagnostics.')` / `ep == 'search.search'` если такие endpoints встречаются в UI — по факту кода A2) |
| Действия | `ep.startswith('actions.')` |
| Учётные записи | `ep.startswith('accounts.')` |
| Скрипты | `ep.startswith('scripts.')` |
| Пароли AD (отчёт) | `ep.startswith('password_expiry.')` **и** `ep != 'password_expiry.settings_page'` |
| Настройки | `settings_active` (см. ниже) — **без** `actions.`, `accounts.`, `scripts.` |

```text
settings_active =
  ep.startswith('sectors.')
  or ep.startswith('admin.')
  or ep.startswith('login_services.')
  or ep == 'password_expiry.settings_page'
```

#### Settings sidebar

| Пункт | Active, если |
| --- | --- |
| Сектора | `ep.startswith('sectors.')` |
| Параметры | `ep == 'admin.settings'` (или `ep.startswith('admin.')` кроме `admin.updates`, если появятся подстраницы параметров — сейчас достаточно `== 'admin.settings'`) |
| Обновления | `ep == 'admin.updates'` |
| Экран входа | `ep.startswith('login_services.')` |
| Пароли AD | `ep == 'password_expiry.settings_page'` |

`script_runs` (`scripts.run_detail` и т.п.) подсвечивают topbar **Скрипты**, не «Настройки».

### 7. Out of scope W1-01

- Вёрстка/CSS (A2, задача W1-02).
- Изменение authz, VERSION, routes.
- Дашборд-сводка на карте (W1-03/04).

---

## Последствия

- A2 может реализовать W1-02 по этой таблице без догадок.
- Non-admin получает прямой доступ к Действиям и УЗ из topbar; Скрипты и отчёт Пароли AD по-прежнему скрыты.
- Settings становится конфигурационным контуром; меньше дублирования ссылок.
- Риск регресса active-state снят явными правилами endpoint; старые URL не ломаются.
