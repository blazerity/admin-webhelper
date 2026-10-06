#!/usr/bin/env bash
# Установить/обновить unit-файлы bAWH, sudoers и перечитать systemd.
# Вызывается из UI-обновления (sudo) и из install-debian12.sh.
set -euo pipefail

INSTALL_DIR="${1:-/opt/bawh}"
DEPLOY_DIR="$INSTALL_DIR/deploy"

if [[ ! -d "$DEPLOY_DIR" ]]; then
  echo "Нет каталога $DEPLOY_DIR" >&2
  exit 1
fi

for unit in bawh-web bawh-scheduler bawh-password-reports bawh-pc-reports; do
  src="$DEPLOY_DIR/${unit}.service"
  if [[ ! -f "$src" ]]; then
    echo "Нет unit-файла $src" >&2
    exit 1
  fi
  cp "$src" "/etc/systemd/system/${unit}.service"
done

if [[ -f "$DEPLOY_DIR/bawh-update.sudoers" ]]; then
  dest=/etc/sudoers.d/bawh-update
  cp "$DEPLOY_DIR/bawh-update.sudoers" "$dest"
  chmod 440 "$dest"
  if command -v visudo >/dev/null 2>&1; then
    if ! visudo -cf "$dest" >/dev/null; then
      echo "Файл $DEPLOY_DIR/bawh-update.sudoers не прошёл visudo" >&2
      exit 1
    fi
  fi
fi

systemctl daemon-reload
