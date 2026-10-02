# Продакшен на Debian 12 запускается через systemd:
# deploy/bawh-web.service и deploy/bawh-scheduler.service.
# Этот образ необязателен. Он поднимает только веб.
#
# Опрос сети — вторая команда, отдельным контейнером:
#   docker run --env-file .env bawh python -m app.scheduler_worker
# Один CMD на gunicorn и планировщик вместе запускать нельзя:
# несколько воркеров веб-процесса начнут пинговать сеть параллельно.

FROM python:3.11-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends iputils-ping traceroute \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/bawh

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY wsgi.py ./
COPY app ./app
COPY migrations ./migrations

EXPOSE 8000

CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "--threads", "4", "--timeout", "120", "wsgi:app"]
