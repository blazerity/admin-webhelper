#!/usr/bin/env bash
# Установка bAWH на Debian 12: пакеты, PostgreSQL, venv, systemd, Nginx.
# Каталог /opt/bawh совпадает с deploy/bawh-web.service и bawh-scheduler.service.
# В консоли — баннер, прогресс-бар по шагам и спиннер ожидания PostgreSQL/health.
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
#   NO_COLOR=1        отключить ANSI-цвета

set -euo pipefail
umask 022

INSTALL_DIR=/opt/bawh
BAWH_REPO="${BAWH_REPO:-https://github.com/blazerity/admin-webhelper.git}"
BAWH_REF="${BAWH_REF:-main}"
ENV_CREATED=0
TOTAL_STEPS=10
CURRENT_STEP=0
# Путь к этому файлу. При запуске через curl | bash его нет: тогда код берётся из git.
SCRIPT_PATH="${BASH_SOURCE[0]:-}"
if [[ -n "$SCRIPT_PATH" && -f "$SCRIPT_PATH" ]]; then
  SCRIPT_PATH="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)/$(basename "$SCRIPT_PATH")"
fi

# Цвета и «живой» прогресс только если stdout — TTY (не при curl|bash > log).
# При curl|bash без TTY всё равно печатаем понятные шаги без escape-кодов.
UI_COLOR=0
UI_TTY=0
if [[ -t 1 ]]; then
  UI_TTY=1
  if [[ "${NO_COLOR:-}" == "" && "${TERM:-dumb}" != "dumb" ]]; then
    UI_COLOR=1
  fi
fi

C_RESET=""
C_BOLD=""
C_DIM=""
C_CYAN=""
C_GREEN=""
C_YELLOW=""
C_RED=""
C_BLUE=""
if [[ "$UI_COLOR" -eq 1 ]]; then
  C_RESET=$'\033[0m'
  C_BOLD=$'\033[1m'
  C_DIM=$'\033[2m'
  C_CYAN=$'\033[36m'
  C_GREEN=$'\033[32m'
  C_YELLOW=$'\033[33m'
  C_RED=$'\033[31m'
  C_BLUE=$'\033[34m'
fi

die() {
  printf '\n%s✗ Ошибка:%s %s\n' "$C_RED$C_BOLD" "$C_RESET" "$*" >&2
  exit 1
}

log() {
  printf '%s\n' "$*"
}

# Полоска прогресса: [████░░░░] 3/11
_progress_bar() {
  local current="$1" total="$2" width="${3:-28}"
  local filled empty pct i
  if (( total <= 0 )); then
    total=1
  fi
  if (( current > total )); then
    current="$total"
  fi
  filled=$(( current * width / total ))
  empty=$(( width - filled ))
  pct=$(( current * 100 / total ))
  printf '['
  for ((i = 0; i < filled; i++)); do printf '█'; done
  for ((i = 0; i < empty; i++)); do printf '░'; done
  printf '] %s/%s (%s%%)' "$current" "$total" "$pct"
}

print_banner() {
  local line
  line='════════════════════════════════════════════════════════════'
  printf '\n'
  printf '%s%s%s\n' "$C_CYAN$C_BOLD" "$line" "$C_RESET"
  printf '%s  bAWH · установка на Debian 12%s\n' "$C_CYAN$C_BOLD" "$C_RESET"
  printf '%s%s%s\n' "$C_CYAN$C_BOLD" "$line" "$C_RESET"
  printf '%s  Каталог:%s  %s\n' "$C_DIM" "$C_RESET" "$INSTALL_DIR"
  printf '%s  Репозиторий:%s %s\n' "$C_DIM" "$C_RESET" "$BAWH_REPO"
  printf '%s  Ветка:%s       %s\n' "$C_DIM" "$C_RESET" "$BAWH_REF"
  printf '%s  Шагов:%s       %s\n' "$C_DIM" "$C_RESET" "$TOTAL_STEPS"
  printf '%s%s%s\n\n' "$C_CYAN$C_BOLD" "$line" "$C_RESET"
}

# Начало шага: обновляет общий прогресс и печатает заголовок.
step_begin() {
  local title="$1"
  local detail="${2:-}"
  CURRENT_STEP=$((CURRENT_STEP + 1))
  printf '\n'
  printf '%s▶ Шаг %s/%s%s  %s\n' \
    "$C_BLUE$C_BOLD" "$CURRENT_STEP" "$TOTAL_STEPS" "$C_RESET" "$C_BOLD$title$C_RESET"
  printf '  %s%s%s\n' "$C_CYAN" "$(_progress_bar "$CURRENT_STEP" "$TOTAL_STEPS")" "$C_RESET"
  if [[ -n "$detail" ]]; then
    printf '  %s%s%s\n' "$C_DIM" "$detail" "$C_RESET"
  fi
}

step_ok() {
  local message="${1:-готово}"
  printf '  %s✓%s %s\n' "$C_GREEN$C_BOLD" "$C_RESET" "$message"
}

step_info() {
  printf '  %s·%s %s\n' "$C_DIM" "$C_RESET" "$*"
}

# Спиннер ожидания (postgres / health). Работает и без TTY — тогда точки.
wait_spinner() {
  local label="$1"
  local max_attempts="$2"
  local check_cmd="$3"
  local attempt=1
  local frames='|/-\\'
  local frame_i=0
  local body frame

  while (( attempt <= max_attempts )); do
    if eval "$check_cmd"; then
      if [[ "$UI_TTY" -eq 1 ]]; then
        printf '\r  %s✓%s %s %s— ок%s\n' "$C_GREEN$C_BOLD" "$C_RESET" "$label" "$C_DIM" "$C_RESET"
      else
        printf '  ✓ %s — ок\n' "$label"
      fi
      return 0
    fi
    if [[ "$UI_TTY" -eq 1 ]]; then
      frame_i=$(( (attempt - 1) % 4 ))
      frame="${frames:frame_i:1}"
      printf '\r  %s%s%s %s %s(%s/%s)%s   ' \
        "$C_YELLOW" "$frame" "$C_RESET" "$label" "$C_DIM" "$attempt" "$max_attempts" "$C_RESET"
    elif (( attempt == 1 || attempt % 5 == 0 )); then
      printf '  … %s (%s/%s)\n' "$label" "$attempt" "$max_attempts"
    fi
    sleep 1
    attempt=$((attempt + 1))
  done
  if [[ "$UI_TTY" -eq 1 ]]; then
    printf '\r'
  fi
  return 1
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
  step_begin "Пакеты Debian" "apt-get update и установка python, PostgreSQL, Nginx, git…"
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
  step_ok "пакеты установлены"
}

ensure_user() {
  step_begin "Системный пользователь" "учётная запись bawh для сервисов и файлов"
  if id bawh >/dev/null 2>&1; then
    step_info "пользователь bawh уже есть"
    step_ok "пропущено создание"
    return
  fi
  useradd --system --home-dir "$INSTALL_DIR" --shell /usr/sbin/nologin bawh
  step_ok "пользователь bawh создан"
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
  step_info "обновляю git: $BAWH_REPO ($BAWH_REF)"
  chown -R bawh:bawh "$INSTALL_DIR"
  runuser -u bawh -- env GIT_TERMINAL_PROMPT=0 git -C "$INSTALL_DIR" remote set-url origin "$BAWH_REPO"
  runuser -u bawh -- env GIT_TERMINAL_PROMPT=0 git -C "$INSTALL_DIR" fetch origin "$BAWH_REF"
  runuser -u bawh -- env GIT_TERMINAL_PROMPT=0 git -C "$INSTALL_DIR" checkout "$BAWH_REF"
  runuser -u bawh -- env GIT_TERMINAL_PROMPT=0 git -C "$INSTALL_DIR" pull --ff-only origin "$BAWH_REF"
}

install_sources() {
  step_begin "Исходники приложения" "код в $INSTALL_DIR"
  detect_local_repo
  mkdir -p "$INSTALL_DIR"

  if [[ -n "$LOCAL_REPO" ]]; then
    local local_real
    local_real="$(cd "$LOCAL_REPO" && pwd -P)"
    if [[ "$local_real" == "$(cd "$INSTALL_DIR" && pwd -P)" ]]; then
      if [[ -d "$INSTALL_DIR/.git" ]]; then
        update_git_checkout
      else
        step_info "код уже лежит в $INSTALL_DIR"
      fi
    else
      step_info "копирую проект из $LOCAL_REPO"
      rsync -a \
        --exclude .venv \
        --exclude .env \
        --exclude __pycache__ \
        --exclude .git \
        --exclude .pytest_cache \
        --exclude .cursor \
        --exclude .claude \
        --exclude .scratch \
        --exclude scripts \
        --exclude tools \
        --exclude tmp \
        --exclude temp \
        --exclude logs \
        --exclude backups \
        --exclude '*.db' \
        --exclude '*.pyc' \
        "$LOCAL_REPO/" "$INSTALL_DIR/"
    fi
    step_ok "исходники на месте"
    return
  fi

  if [[ -d "$INSTALL_DIR/.git" ]]; then
    update_git_checkout
    step_ok "репозиторий обновлён"
    return
  fi

  if [[ -n "$(ls -A "$INSTALL_DIR" 2>/dev/null || true)" ]]; then
    die "Каталог $INSTALL_DIR уже есть, и это не git-копия. Запустите скрипт из каталога проекта: sudo bash deploy/install-debian12.sh. Либо уберите $INSTALL_DIR и выполните команду ещё раз."
  fi

  step_info "клонирую $BAWH_REPO → $INSTALL_DIR"
  chown bawh:bawh "$INSTALL_DIR"
  runuser -u bawh -- env GIT_TERMINAL_PROMPT=0 git clone --branch "$BAWH_REF" "$BAWH_REPO" "$INSTALL_DIR"
  step_ok "репозиторий склонирован"
}

ensure_venv() {
  step_begin "Python venv и зависимости" "python3.11 -m venv + pip install -r requirements.txt"
  chown -R bawh:bawh "$INSTALL_DIR"
  runuser -u bawh -- python3.11 -m venv "$INSTALL_DIR/.venv"
  runuser -u bawh -- "$INSTALL_DIR/.venv/bin/pip" install \
    --no-cache-dir \
    --disable-pip-version-check \
    -r "$INSTALL_DIR/requirements.txt"
  # Роль PostgreSQL создаёт интерпретатор из этого venv от имени postgres.
  chmod 755 "$INSTALL_DIR"
  chmod -R a+rX "$INSTALL_DIR/.venv"
  step_ok "venv готов"
}

write_env_file() {
  step_info "создаю $INSTALL_DIR/.env (SECRET_KEY, FERNET_KEY, пароль БД)"
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
  step_info "запускаю PostgreSQL и жду готовности"
  systemctl enable --now postgresql
  if ! wait_spinner "ожидание PostgreSQL" 30 'runuser -u postgres -- pg_isready -q'; then
    die "PostgreSQL не ответил. Смотрите: journalctl -u postgresql -n 50 --no-pager"
  fi
}

ensure_database() {
  step_begin "PostgreSQL и .env" "роль/база bawh, секреты в $INSTALL_DIR/.env"
  if [[ ! -f "$INSTALL_DIR/.env" ]]; then
    write_env_file
  else
    step_info "файл $INSTALL_DIR/.env уже есть — оставляю без перезаписи"
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

  step_info "проверяю роль и базу PostgreSQL bawh"
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
  step_ok "база bawh готова"
}

prepare_database() {
  step_begin "Схема приложения" "flask init-db — таблицы и справочники"
  mkdir -p "$INSTALL_DIR/logs"
  chown bawh:bawh "$INSTALL_DIR/logs"
  runuser -u bawh -- env LANG="${LANG}" LC_ALL="${LC_ALL}" \
    bash -c "cd '$INSTALL_DIR' && exec .venv/bin/flask --app wsgi init-db"
  step_ok "схема создана"
}

install_systemd() {
  step_begin "systemd-службы" "bawh-web (Gunicorn) и bawh-scheduler"
  cp "$INSTALL_DIR/deploy/bawh-web.service" "$INSTALL_DIR/deploy/bawh-scheduler.service" /etc/systemd/system/
  systemctl daemon-reload
  systemctl enable --now bawh-web bawh-scheduler
  systemctl restart bawh-web bawh-scheduler
  step_ok "службы запущены"
}

install_update_sudoers() {
  step_begin "Sudoers для обновления из UI" "passwordless restart bawh-web / bawh-scheduler"
  local src="$INSTALL_DIR/deploy/bawh-update.sudoers"
  local dest=/etc/sudoers.d/bawh-update
  if [[ ! -f "$src" ]]; then
    step_info "нет $src — правило не ставлю"
    step_ok "пропущено"
    return 0
  fi
  if ! command -v visudo >/dev/null 2>&1; then
    step_info "нет visudo — правило не ставлю"
    step_ok "пропущено"
    return 0
  fi
  mkdir -p /etc/sudoers.d
  cp "$src" "$dest"
  chmod 440 "$dest"
  if ! visudo -cf "$dest" >/dev/null; then
    rm -f "$dest"
    die "Файл $src не прошёл проверку visudo. Автоперезапуск после обновления не настроен."
  fi
  step_ok "sudoers установлен"
}

install_nginx() {
  step_begin "Nginx" "прокси на Gunicorn :8000, порт 80"
  local server_name
  server_name="${BAWH_SERVER_NAME:-}"
  if [[ -z "$server_name" ]]; then
    server_name="$(hostname -f 2>/dev/null || hostname)"
  fi
  if [[ ! "$server_name" =~ ^[A-Za-z0-9._-]+$ ]]; then
    die "BAWH_SERVER_NAME может содержать только буквы, цифры, точку, дефис и подчёркивание."
  fi
  step_info "server_name = $server_name"
  sed "s/server_name _ bawh.example.com;/server_name _ ${server_name};/" \
    "$INSTALL_DIR/deploy/nginx-bawh.conf" > /etc/nginx/sites-available/bawh
  ln -sfn /etc/nginx/sites-available/bawh /etc/nginx/sites-enabled/bawh
  rm -f /etc/nginx/sites-enabled/default
  nginx -t
  systemctl enable --now nginx
  systemctl reload nginx
  if command -v ufw >/dev/null 2>&1 && ufw status | grep -q '^Status: active'; then
    step_info "ufw активен — открываю TCP 80"
    ufw allow 80/tcp comment bawh
  fi
  SERVER_NAME="$server_name"
  LAN_IP="$(hostname -I 2>/dev/null | tr ' ' '\n' | grep -E '^[0-9]+(\.[0-9]+){3}$' | grep -v '^127\.' | head -n 1 || true)"
  step_ok "Nginx настроен"
}

wait_until_ok() {
  local url="$1"
  local host="${2:-}"
  local curl_host=()
  if [[ -n "$host" ]]; then
    curl_host=(-H "Host: $host")
  fi
  local body
  body="$(curl -fsS "${curl_host[@]}" "$url" 2>/dev/null || true)"
  [[ "$body" == *'"ok"'* ]]
}

wait_for_health() {
  step_begin "Проверка здоровья" "Gunicorn /health и Nginx /health"
  if ! wait_spinner "Gunicorn http://127.0.0.1:8000/health" 30 \
    'wait_until_ok "http://127.0.0.1:8000/health"'; then
    die "bawh-web не ответил на /health. Журнал: journalctl -u bawh-web -n 50 --no-pager"
  fi
  if ! wait_spinner "Nginx http://127.0.0.1/health" 30 \
    "wait_until_ok \"http://127.0.0.1/health\" \"$SERVER_NAME\""; then
    die "Nginx не отдал /health. Журнал: journalctl -u nginx -n 50 --no-pager"
  fi
  if [[ -n "${LAN_IP:-}" ]]; then
    if ! wait_spinner "LAN http://${LAN_IP}/health" 30 \
      "wait_until_ok \"http://${LAN_IP}/health\""; then
      die "Nginx не ответил на http://${LAN_IP}/health. Порт 80 должен быть открыт на адресе сервера."
    fi
  else
    step_info "LAN IP не определён — проверку по сети пропускаю"
  fi
  step_ok "сервис отвечает"
}

print_summary() {
  local line
  line='════════════════════════════════════════════════════════════'
  printf '\n'
  printf '%s%s%s\n' "$C_GREEN$C_BOLD" "$line" "$C_RESET"
  printf '%s  ✓  bAWH установлен успешно%s\n' "$C_GREEN$C_BOLD" "$C_RESET"
  printf '  %s%s%s\n' "$C_CYAN" "$(_progress_bar "$TOTAL_STEPS" "$TOTAL_STEPS")" "$C_RESET"
  printf '%s%s%s\n' "$C_GREEN$C_BOLD" "$line" "$C_RESET"
  printf '\n'
  printf '  %sКаталог:%s     %s\n' "$C_BOLD" "$C_RESET" "$INSTALL_DIR"
  printf '  %sНастройки:%s   %s/.env\n' "$C_BOLD" "$C_RESET" "$INSTALL_DIR"
  if [[ -n "${LAN_IP:-}" ]]; then
    printf '  %sСайт (LAN):%s  http://%s/\n' "$C_BOLD" "$C_RESET" "$LAN_IP"
  fi
  printf '  %sСайт (имя):%s  http://%s/\n' "$C_BOLD" "$C_RESET" "$SERVER_NAME"
  printf '  %sПроверка:%s    curl -s http://127.0.0.1:8000/health\n' "$C_BOLD" "$C_RESET"
  printf '\n'
  printf '%sСледующий шаг:%s заполните LDAP в %s/.env\n' "$C_YELLOW$C_BOLD" "$C_RESET" "$INSTALL_DIR"
  printf '  (LDAP_HOST, LDAP_BASE_DN, LDAP_DOMAIN) и перезапустите:\n'
  printf '    systemctl restart bawh-web bawh-scheduler\n'
  printf '\n'
  printf '%sPsExec:%s учётка на странице /admin/settings (на пользователя).\n' "$C_DIM" "$C_RESET"
  printf '%sHTTP:%s  пока без TLS оставьте SESSION_COOKIE_SECURE=0.\n\n' "$C_DIM" "$C_RESET"
}

main() {
  # sudo запускают из домашнего каталога. postgres и bawh туда зайти не могут,
  # и pg_isready пишет «could not change directory to /home/...».
  cd /
  require_root
  require_debian_12
  export DEBIAN_FRONTEND=noninteractive
  ensure_utf8_locale
  print_banner
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
