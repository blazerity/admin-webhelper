# Продакшен на Debian 12 — через systemd (deploy/*.service).
# Образ необязателен: только веб. Опрос — отдельным процессом:
#   docker run --env-file .env bawh python -m app.scheduler_worker

FROM python:3.11-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends iputils-ping traceroute \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/bawh

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY wsgi.py ./
COPY app ./app

EXPOSE 8000

CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "--threads", "4", "--timeout", "120", "wsgi:app"]
