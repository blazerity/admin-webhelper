# Устройство offline

Операторский сценарий: хост на карте **offline**.

## Симптом

Карточка устройства и/или сводка показывают offline. Watchlist (W3) может прислать in-app уведомление.

## Быстрая проверка

1. `systemctl is-active bawh-web bawh-scheduler`
2. `/admin/settings` → health scheduler и последний `network_poll_runs`
3. С карточки: **Ping**
4. Если ping ок, а инвентарь пуст → [WMI не отвечает](wmi-no-response.md)
5. Сектор/CIDR/ACL: хост всё ещё в диапазоне? Non-admin не видит чужие сектора

## Эскалация

- Весь сектор offline при живом scheduler → сеть / маршрут
- Scheduler stale, `/health` ok → журнал scheduler, диск, БД
- Один хост → питание/firewall; удалённые скрипты — [PsExec отказал](psexec-failed.md)
