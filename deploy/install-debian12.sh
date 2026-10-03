#!/usr/bin/env bash
# Установка bAWH на Debian 12: пакеты, PostgreSQL, venv, systemd, Nginx.
# Каталог /opt/bawh совпадает с deploy/bawh-web.service и bawh-scheduler.service.
#
# С чистого сервера:
#   sudo apt-get update && sudo apt-get install -y curl ca-certificates && curl -fsSL https://raw.githubusercontent.com/blazerity/admin-webhelper/main/deploy/install-debian12.sh | sudo bash
#
# Из уже скачанного каталога проекта:
#   sudo bash deploy/install-debian12.sh
#
# Переменные:
#   BAWH_SERVER_NAME  имя в Nginx (по умолчанию hostname -f)
#   BAWH_REPO         git-адрес, если скрипт запущен не из каталога проекта
#   BAWH_REF          ветка (по умолчанию main)

set -euo pipefail
umask 022

INSTALL_DIR=/opt/bawh
BAWH_REPO="${BAWH_REPO:-https://github.com/blazerity/admin-webhelper.git}"
BAWH_REF="${BAWH_REF:-main}"
ENV_CREATED=0
# Путь к этому файлу. При запуске через curl | bash его нет: тогда код берётся из git.
SCRIPT_PATH="${BASH_SOURCE[0]:-}"
if [[ -n "$SCRIPT_PATH" && -f "$SCRIPT_PATH" ]]; then
  SCRIPT_PATH="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)/$(basename "$SCRIPT_PATH")"
fi

die() {
  printf '%s\n' "$*" >&2
  exit 1
}

log() {
  printf '%s\n' "$*"
}

require_root() {
  if [[ "$(id -u)" -ne 0 ]]; then
    die "Запустите скрипт от root: sudo bash deploy/install-debian12.sh"
  fi
}

require_debian_12() {
  if [[ ! -r /etc/os-release ]]; then
    die "Не найден /etc/os-release. Скрипт ставит bAWH на Debian 12."
  fi
  # shellcheck disable=SC1091
  . /etc/os-release
  if [[ "${ID:-}" != "debian" || "${VERSION_ID:-}" != "12" ]]; then
    die "Скрипт ставит bAWH на Debian 12. Сейчас система: ${PRETTY_NAME:-неизвестно}."
  fi
}

ensure_utf8_locale() {
  # Минимальные образы и curl|bash часто стартуют с LC_ALL=C (ASCII).
  # На Debian 12 C.UTF-8 есть из коробки (glibc).
  export LANG=C.UTF-8
  export LC_ALL=C.UTF-8
}

install_packages() {
  log "Ставлю пакеты Debian."
  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  apt-get install -y \
    -o Dpkg::Options::=--force-confdef \
    -o Dpkg::Options::=--force-confold \
    python3.11 \
    python3.11-venv \
    postgresql \
    nginx \
    iputils-ping \
    traceroute \
    git \
    rsync \
    ca-certificates \
    curl \
    sudo
}

ensure_user() {
  if id bawh >/dev/null 2>&1; then
    return
  fi
  log "Создаю системного пользователя bawh."
  useradd --system --home-dir "$INSTALL_DIR" --shell /usr/sbin/nologin bawh
}

detect_local_repo() {
  LOCAL_REPO=""
  local script_dir
  if [[ -z "$SCRIPT_PATH" || ! -f "$SCRIPT_PATH" ]]; then
    return
  fi
  script_dir="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)" || return
  if [[ -f "$script_dir/../wsgi.py" && -f "$script_dir/../requirements.txt" ]]; then
    LOCAL_REPO="$(cd "$script_dir/.." && pwd)"
  fi
}

update_git_checkout() {
  log "Обновляю $INSTALL_DIR из $BAWH_REPO ($BAWH_REF)."
  chown -R bawh:bawh "$INSTALL_DIR"
  runuser -u bawh -- env GIT_TERMINAL_PROMPT=0 git -C "$INSTALL_DIR" remote set-url origin "$BAWH_REPO"
  runuser -u bawh -- env GIT_TERMINAL_PROMPT=0 git -C "$INSTALL_DIR" fetch origin "$BAWH_REF"
  runuser -u bawh -- env GIT_TERMINAL_PROMPT=0 git -C "$INSTALL_DIR" checkout "$BAWH_REF"
  runuser -u bawh -- env GIT_TERMINAL_PROMPT=0 git -C "$INSTALL_DIR" pull --ff-only origin "$BAWH_REF"
}

install_sources() {
  detect_local_repo
  mkdir -p "$INSTALL_DIR"

  if [[ -n "$LOCAL_REPO" ]]; then
    local local_real
    local_real="$(cd "$LOCAL_REPO" && pwd -P)"
    if [[ "$local_real" == "$(cd "$INSTALL_DIR" && pwd -P)" ]]; then
      if [[ -d "$INSTALL_DIR/.git" ]]; then
        update_git_checkout
      else
        log "Код уже лежит в $INSTALL_DIR."
      fi
    else
      log "Копирую проект в $INSTALL_DIR."
      rsync -a \
        --exclude .venv \
        --exclude .env \
        --exclude __pycache__ \
        --exclude .git \
        --exclude .pytest_cache \
        --exclude logs \
        --exclude backups \
        --exclude '*.db' \
        --exclude '*.pyc' \
        "$LOCAL_REPO/" "$INSTALL_DIR/"
    fi
    return
  fi

  if [[ -d "$INSTALL_DIR/.git" ]]; then
    update_git_checkout
    return
  fi

  if [[ -n "$(ls -A "$INSTALL_DIR" 2>/dev/null || true)" ]]; then
    die "Каталог $INSTALL_DIR уже есть, и это не git-копия. Запустите скрипт из каталога проекта: sudo bash deploy/install-debian12.sh. Либо уберите $INSTALL_DIR и выполните команду ещё раз."
  fi

  log "Клонирую $BAWH_REPO в $INSTALL_DIR."
  chown bawh:bawh "$INSTALL_DIR"
  runuser -u bawh -- env GIT_TERMINAL_PROMPT=0 git clone --branch "$BAWH_REF" "$BAWH_REPO" "$INSTALL_DIR"
}

ensure_venv() {
  log "Собираю виртуальное окружение и ставлю зависимости Python."
  chown -R bawh:bawh "$INSTALL_DIR"
  runuser -u bawh -- python3.11 -m venv "$INSTALL_DIR/.venv"
  runuser -u bawh -- "$INSTALL_DIR/.venv/bin/pip" install \
    --no-cache-dir \
    --disable-pip-version-check \
    -r "$INSTALL_DIR/requirements.txt"
  # Роль PostgreSQL создаёт интерпретатор из этого venv от имени postgres.
  chmod 755 "$INSTALL_DIR"
  chmod -R a+rX "$INSTALL_DIR/.venv"
}

write_env_file() {
  log "Создаю $INSTALL_DIR/.env с новыми SECRET_KEY, FERNET_KEY и паролем базы."
  BAWH_INSTALL_DIR="$INSTALL_DIR" BAWH_REPO="$BAWH_REPO" BAWH_REF="$BAWH_REF" \
    "$INSTALL_DIR/.venv/bin/python" - <<'PY'
import os
import secrets
from pathlib import Path

from cryptography.fernet import Fernet

install_dir = Path(os.environ["BAWH_INSTALL_DIR"])
example = (install_dir / ".env.example").read_text(encoding="utf-8")
secret = secrets.token_urlsafe(48)
password = secrets.token_urlsafe(24)
fernet = Fernet.generate_key().decode()
database_url = f"postgresql+psycopg2://bawh:{password}@localhost:5432/bawh"
replacements = {
    "SECRET_KEY": secret,
    "DATABASE_URL": database_url,
    "FERNET_KEY": fernet,
}
remote = os.environ.get("BAWH_REPO", "").strip()
ref = os.environ.get("BAWH_REF", "main").strip() or "main"
if remote.startswith("https://") and " " not in remote:
    replacements["GIT_REMOTE_URL"] = remote
if ref and not any(char in ref for char in " \t\r\n"):
    replacements["GIT_BRANCH"] = ref
out = []
seen = set()
for line in example.splitlines(keepends=True):
    replaced = False
    for key, value in replacements.items():
        if line.startswith(key + "="):
            out.append(f"{key}={value}\n")
            seen.add(key)
            replaced = True
            break
    if not replaced:
        out.append(line)
missing = sorted(set(replacements) - seen)
if missing:
    raise SystemExit("В .env.example нет ключей: " + ", ".join(missing))
env_path = install_dir / ".env"
env_path.write_text("".join(out), encoding="utf-8")
env_path.chmod(0o600)
PY
  chown bawh:bawh "$INSTALL_DIR/.env"
  chmod 600 "$INSTALL_DIR/.env"
  ENV_CREATED=1
}

wait_for_postgres() {
  log "Жду PostgreSQL."
  systemctl enable --now postgresql
  local attempt
  for attempt in $(seq 1 30); do
    if runuser -u postgres -- pg_isready -q; then
      return
    fi
    sleep 1
  done
  die "PostgreSQL не ответил. Смотрите: journalctl -u postgresql -n 50 --no-pager"
}

ensure_database() {
  if [[ ! -f "$INSTALL_DIR/.env" ]]; then
    write_env_file
  else
    log "Файл $INSTALL_DIR/.env уже есть, оставляю его."
    chown bawh:bawh "$INSTALL_DIR/.env"
    chmod 600 "$INSTALL_DIR/.env"
  fi
  # Кнопка «Обновить» читает GIT_REMOTE_URL. Пустое значение и старая заглушка
  # не должны уводить установку с адреса, с которого скрипт клонирует код.
  # Уже записанный свой адрес не переписывается.
  BAWH_INSTALL_DIR="$INSTALL_DIR" BAWH_REPO="$BAWH_REPO" BAWH_REF="$BAWH_REF" \
    "$INSTALL_DIR/.venv/bin/python" - <<'PY'
import os
from pathlib import Path

install_dir = Path(os.environ["BAWH_INSTALL_DIR"])
env_path = install_dir / ".env"
remote = os.environ.get("BAWH_REPO", "").strip()
ref = os.environ.get("BAWH_REF", "main").strip() or "main"
placeholder = "https://github.com/ORG/bAWH.git"
lines = env_path.read_text(encoding="utf-8").splitlines(keepends=True)

def fill(lines: list[str], key: str, value: str, replaceable: set[str]) -> list[str]:
    found = False
    out = []
    for line in lines:
        if line.startswith(key + "="):
            found = True
            current = line.split("=", 1)[1].strip()
            if current in replaceable:
                out.append(f"{key}={value}\n")
            else:
                out.append(line if line.endswith("\n") else line + "\n")
        else:
            out.append(line if line.endswith("\n") else line + "\n")
    if not found:
        if out and not out[-1].endswith("\n"):
            out[-1] += "\n"
        out.append(f"{key}={value}\n")
    return out

if remote.startswith("https://") and " " not in remote:
    lines = fill(lines, "GIT_REMOTE_URL", remote, {"", placeholder})
if ref and not any(char in ref for char in " \t\r\n"):
    lines = fill(lines, "GIT_BRANCH", ref, {""})
env_path.write_text("".join(lines), encoding="utf-8")
PY
  chown bawh:bawh "$INSTALL_DIR/.env"
  chmod 600 "$INSTALL_DIR/.env"

  wait_for_postgres
  # .env принадлежит bawh и закрыт от остальных. Пароль читает root и передаёт
  # его процессу postgres через окружение: пользователь postgres файл не откроет.
  local db_pass
  db_pass="$(
    BAWH_INSTALL_DIR="$INSTALL_DIR" "$INSTALL_DIR/.venv/bin/python" - <<'PY'
import os
from pathlib import Path
from urllib.parse import unquote, urlparse

install_dir = Path(os.environ["BAWH_INSTALL_DIR"])
database_url = ""
for line in (install_dir / ".env").read_text(encoding="utf-8").splitlines():
    if line.startswith("DATABASE_URL="):
        database_url = line.split("=", 1)[1].strip()
        break
if not database_url:
    raise SystemExit("В .env нет DATABASE_URL.")
parsed = urlparse(database_url.replace("postgresql+psycopg2://", "http://", 1))
user = unquote(parsed.username or "")
password = unquote(parsed.password or "")
if user != "bawh" or not password:
    raise SystemExit(
        "DATABASE_URL в .env должен быть postgresql+psycopg2://bawh:ПАРОЛЬ@localhost:5432/bawh."
    )
print(password, end="")
PY
  )" || die "Не удалось прочитать пароль базы из $INSTALL_DIR/.env."

  log "Проверяю роль и базу PostgreSQL bawh."
  chmod 755 "$INSTALL_DIR"
  runuser -u postgres -- env -i \
    PATH="/usr/bin:/bin" \
    HOME="/var/lib/postgresql" \
    LANG="C.UTF-8" \
    LC_ALL="C.UTF-8" \
    BAWH_DB_PASSWORD="$db_pass" \
    BAWH_RESET_DB_PASSWORD="$ENV_CREATED" \
    "$INSTALL_DIR/.venv/bin/python" - <<'PY'
import os

import psycopg2

password = os.environ["BAWH_DB_PASSWORD"]
reset_password = os.environ.get("BAWH_RESET_DB_PASSWORD") == "1"

def quote_literal(value: str) -> str:
    tag = "bawh"
    while f"${tag}$" in value:
        tag += "x"
    return f"${tag}${value}${tag}$"

conn = psycopg2.connect(dbname="postgres", user="postgres", host="/var/run/postgresql")
conn.autocommit = True
cur = conn.cursor()
cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", ("bawh",))
role_exists = cur.fetchone() is not None
quoted = quote_literal(password)
if role_exists:
    if reset_password:
        cur.execute(f"ALTER ROLE bawh WITH LOGIN PASSWORD {quoted}")
else:
    cur.execute(f"CREATE ROLE bawh LOGIN PASSWORD {quoted}")
cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", ("bawh",))
if cur.fetchone() is None:
    cur.execute("CREATE DATABASE bawh OWNER bawh")
conn.close()

# В PostgreSQL 15 схема public принадлежит владельцу базы.
# Явная выдача прав нужна и на более старой политике.
conn = psycopg2.connect(dbname="bawh", user="postgres", host="/var/run/postgresql")
conn.autocommit = True
conn.cursor().execute("GRANT ALL ON SCHEMA public TO bawh")
PY
}

prepare_database() {
  log "Готовлю схему базы данных."
  mkdir -p "$INSTALL_DIR/logs"
  chown bawh:bawh "$INSTALL_DIR/logs"
  runuser -u bawh -- env LANG="${LANG}" LC_ALL="${LC_ALL}" \
    bash -c "cd '$INSTALL_DIR' && exec .venv/bin/flask --app wsgi init-db"
}

install_systemd() {
  log "Включаю сервисы bawh-web и bawh-scheduler."
  cp "$INSTALL_DIR/deploy/bawh-web.service" "$INSTALL_DIR/deploy/bawh-scheduler.service" /etc/systemd/system/
  systemctl daemon-reload
  systemctl enable --now bawh-web bawh-scheduler
  systemctl restart bawh-web bawh-scheduler
}

install_update_sudoers() {
  local src="$INSTALL_DIR/deploy/bawh-update.sudoers"
  local dest=/etc/sudoers.d/bawh-update
  if [[ ! -f "$src" ]]; then
    log "Нет $src — правило для автоперезапуска после обновления не ставлю."
    return 0
  fi
  if ! command -v visudo >/dev/null 2>&1; then
    log "Нет visudo — правило для автоперезапуска после обновления не ставлю."
    return 0
  fi
  log "Ставлю passwordless sudo для перезапуска служб после обновления из UI."
  mkdir -p /etc/sudoers.d
  cp "$src" "$dest"
  chmod 440 "$dest"
  if ! visudo -cf "$dest" >/dev/null; then
    rm -f "$dest"
    die "Файл $src не прошёл проверку visudo. Автоперезапуск после обновления не настроен."
  fi
}

install_nginx() {
  local server_name
  server_name="${BAWH_SERVER_NAME:-}"
  if [[ -z "$server_name" ]]; then
    server_name="$(hostname -f 2>/dev/null || hostname)"
  fi
  if [[ ! "$server_name" =~ ^[A-Za-z0-9._-]+$ ]]; then
    die "BAWH_SERVER_NAME может содержать только буквы, цифры, точку, дефис и подчёркивание."
  fi
  log "Настраиваю Nginx на порту 80 (default_server), имя $server_name."
  sed "s/server_name _ bawh.example.com;/server_name _ ${server_name};/" \
    "$INSTALL_DIR/deploy/nginx-bawh.conf" > /etc/nginx/sites-available/bawh
  ln -sfn /etc/nginx/sites-available/bawh /etc/nginx/sites-enabled/bawh
  rm -f /etc/nginx/sites-enabled/default
  nginx -t
  systemctl enable --now nginx
  systemctl reload nginx
  if command -v ufw >/dev/null 2>&1 && ufw status | grep -q '^Status: active'; then
    log "В ufw открываю TCP 80 для доступа из локальной сети."
    ufw allow 80/tcp comment bawh
  fi
  SERVER_NAME="$server_name"
  LAN_IP="$(hostname -I 2>/dev/null | tr ' ' '\n' | grep -E '^[0-9]+(\.[0-9]+){3}$' | grep -v '^127\.' | head -n 1 || true)"
}

wait_until_ok() {
  local url host attempt body curl_host
  url="$1"
  host="${2:-}"
  curl_host=()
  if [[ -n "$host" ]]; then
    curl_host=(-H "Host: $host")
  fi
  for attempt in $(seq 1 30); do
    body="$(curl -fsS "${curl_host[@]}" "$url" 2>/dev/null || true)"
    if [[ "$body" == *'"ok"'* ]]; then
      return 0
    fi
    sleep 1
  done
  return 1
}

wait_for_health() {
  log "Проверяю http://127.0.0.1:8000/health."
  if ! wait_until_ok "http://127.0.0.1:8000/health"; then
    die "bawh-web не ответил на /health. Журнал: journalctl -u bawh-web -n 50 --no-pager"
  fi
  log "Проверяю http://127.0.0.1/health через Nginx."
  if ! wait_until_ok "http://127.0.0.1/health" "$SERVER_NAME"; then
    die "Nginx не отдал /health. Журнал: journalctl -u nginx -n 50 --no-pager"
  fi
  if [[ -n "${LAN_IP:-}" ]]; then
    log "Проверяю http://${LAN_IP}/health (как из локальной сети, по IP)."
    if ! wait_until_ok "http://${LAN_IP}/health"; then
      die "Nginx не ответил на http://${LAN_IP}/health. Порт 80 должен быть открыт на адресе сервера."
    fi
  fi
}

print_summary() {
  log ""
  log "bAWH установлен."
  log "  Каталог: $INSTALL_DIR"
  log "  Настройки: $INSTALL_DIR/.env"
  if [[ -n "${LAN_IP:-}" ]]; then
    log "  Сайт в локальной сети: http://${LAN_IP}/"
  fi
  log "  Сайт по имени: http://${SERVER_NAME}/"
  log "  Проверка: curl -s http://127.0.0.1:8000/health"
  log ""
  log "Заполните LDAP в $INSTALL_DIR/.env (LDAP_HOST, LDAP_BASE_DN, LDAP_DOMAIN) и перезапустите сервисы:"
  log "  systemctl restart bawh-web bawh-scheduler"
  log "Учётку PsExec задают на странице /admin/settings (на пользователя). Пока Nginx отдаёт HTTP, оставьте SESSION_COOKIE_SECURE=0."
}

main() {
  # sudo запускают из домашнего каталога. postgres и bawh туда зайти не могут,
  # и pg_isready пишет «could not change directory to /home/...».
  cd /
  require_root
  require_debian_12
  export DEBIAN_FRONTEND=noninteractive
  ensure_utf8_locale
  install_packages
  ensure_user
  install_sources
  if [[ ! -f "$INSTALL_DIR/wsgi.py" || ! -f "$INSTALL_DIR/requirements.txt" ]]; then
    die "В $INSTALL_DIR нет файлов проекта."
  fi
  ensure_venv
  ensure_database
  prepare_database
  install_systemd
  install_update_sudoers
  install_nginx
  wait_for_health
  print_summary
}

main "$@"
