# Offline / air-gap assets (W4-02)

bAWH по умолчанию раздаёт **self-host Bootstrap 5.3.3** из `app/static/vendor/bootstrap/`.

Шрифты: CDN Google Fonts убран; UI использует system stack (`Segoe UI` / `system-ui`) с опциональным Manrope, если оператор положит файлы локально.

## Закрытая сеть

1. Убедиться, что в образе/деплое есть `app/static/vendor/bootstrap/*.css|js`.
2. Не открывать `cdn.jsdelivr.net` / `fonts.googleapis.com` — они больше не требуются для базового UI.
3. Если нужен именно Manrope: положить woff2 в `app/static/vendor/fonts/` и добавить `@font-face` в `app.css` (не обязательно для работы).

## Проверка

Открыть карту без интернета на клиенте: Bootstrap collapse/modal и стили topbar должны работать.
