# Пароли AD не уходят

## Симптом

Отчёт «Пароли AD» строится, но SMTP/уведомления пользователям не доходят.

## Проверка

1. Пользователь — admin или `password_viewer` (дашборд); SMTP settings — только admin.
2. LDAP filter/base для модуля паролей в settings.
3. SMTP host/port/TLS/from; тестовая отправка если есть.
4. Журнал `password_expiry_runs` / логи приложения на ошибки SMTP.
5. Пользователь уже уведомлён недавно? (tracker dedupe)

## Не ломать

Не отключать password expiry ради «тишины» без согласования; секреты SMTP только в `.env`/settings.
