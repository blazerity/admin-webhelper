"""Точка входа для Gunicorn и локального запуска.

Продакшен (systemd):
    gunicorn --bind 127.0.0.1:8000 --workers 2 --threads 4 wsgi:app

Локально, для знакомства с проектом:
    flask --app wsgi run --debug

Периодический опрос сюда не встроен: его запускает отдельный процесс
`python -m app.scheduler_worker`. Так несколько воркеров Gunicorn
не пингуют сеть параллельно друг другу. Позже этот процесс
заменяется воркером Celery, а wsgi.py остаётся только веб-слоем.
"""

from app import create_app

app = create_app()

if __name__ == "__main__":
    # debug-сервер Flask только для разработки, не для Debian-сервиса.
    app.run(host="127.0.0.1", port=8000, debug=True)
