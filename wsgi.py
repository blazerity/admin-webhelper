"""Точка входа Gunicorn / локального flask run.

Опрос сети — `python -m app.scheduler_worker`.
Отчёты о паролях — `python -m app.password_report_worker`.
Отчёты о ПК — `python -m app.pc_report_worker`.
"""

from app import create_app

app = create_app()

if __name__ == "__main__":
    # Только для разработки, не для Debian-сервиса.
    app.run(host="127.0.0.1", port=8000, debug=True)
