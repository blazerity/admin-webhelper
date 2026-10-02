# Устройство offline

Краткий операторский сценарий (заготовка; полное наполнение — W4-04).

## Симптом

На карте сети устройство в статусе **offline** (или «не отвечает» после опроса). Карточка открывается, вкладка polls показывает неуспешные проходы.

## Быстрая проверка

1. Убедиться, что `bawh-scheduler` активен: `systemctl is-active bawh-scheduler`.
2. На **Параметры** (`/admin/settings`) посмотреть последний `network_poll_runs` и блок health scheduler (после W1-08): не `stale` ли опрос.
3. С карточки устройства: **Ping** (diagnostics) — ICMP доходит?
4. Если ping ок, а WMI/инвентарь пустой — проверить учётку WMI в Параметрах и сеть до хоста (RPC/firewall). См. будущий runbook WMI.
5. Сектор и CIDR: устройство всё ещё в диапазоне сектора? ACL non-admin не скрывает «чужие» хосты как offline.

## Когда эскалировать

- Весь сектор offline при живом scheduler → сеть / маршрут / MIN_CIDR_PREFIX.
- Scheduler stale, web жив (`/health` ok) → журнал `bawh-scheduler`, диск, БД.
- Только один хост → кабель/питание/локальный firewall; PsExec отдельно.

Связано: [W1 regression checklist](w1-regression-checklist.md) §3–§6, §9.
