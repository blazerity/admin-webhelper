#!/usr/bin/env bash
# Применить HTTP или HTTPS конфиг Nginx из Параметров bAWH.
# Вызывается от root (sudoers / su). Аргумент — корень установки.
# Состояние: $INSTALL_DIR/certs/incoming/{fullchain.pem,privkey.pem,request.env}
set -euo pipefail

INSTALL_DIR="${1:-/opt/bawh}"
INCOMING="$INSTALL_DIR/certs/incoming"
REQ="$INCOMING/request.env"
SITE=/etc/nginx/sites-available/bawh
SSL_DIR=/etc/nginx/ssl/bawh
DEPLOY="$INSTALL_DIR/deploy"
TEMPLATE="$DEPLOY/nginx-bawh.conf"

die() { echo "$*" >&2; exit 1; }

[[ -d "$DEPLOY" ]] || die "Нет $DEPLOY"
[[ -f "$TEMPLATE" ]] || die "Нет шаблона $TEMPLATE"
[[ -f "$REQ" ]] || die "Нет $REQ — сначала сохраните сертификат в Параметрах."

read_var() {
  local key="$1"
  local line
  line="$(grep -E "^${key}=" "$REQ" | tail -n1 || true)"
  printf '%s' "${line#*=}"
}

ENABLE_HTTPS="$(read_var ENABLE_HTTPS)"
REDIRECT_HTTP="$(read_var REDIRECT_HTTP)"
SERVER_NAME="$(read_var SERVER_NAME)"
SET_SECURE_COOKIE="$(read_var SET_SECURE_COOKIE)"

if [[ -z "$SERVER_NAME" ]]; then
  SERVER_NAME="_"
fi
if [[ "$SERVER_NAME" != "_" && ! "$SERVER_NAME" =~ ^[A-Za-z0-9._-]+$ ]]; then
  die "Некорректный SERVER_NAME"
fi

vnc_location() {
  cat <<'EOF'
    location /vnc/ws {
        proxy_pass http://127.0.0.1:6080;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
        proxy_buffering off;
    }
EOF
}

proxy_root() {
  cat <<'EOF'
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;
        client_max_body_size 4m;
    }
EOF
}

write_http_only() {
  local name="$1"
  cat > "$SITE" <<EOF
# Сгенерировано deploy/apply-nginx-tls.sh (HTTP).
# Шаблон-источник: deploy/nginx-bawh.conf

server {
    listen 80 default_server;
    server_name _ ${name};

$(vnc_location)

$(proxy_root)
}
EOF
}

write_https() {
  local name="$1"
  local redirect="$2"
  mkdir -p "$SSL_DIR"
  cp "$INCOMING/fullchain.pem" "$SSL_DIR/fullchain.pem"
  cp "$INCOMING/privkey.pem" "$SSL_DIR/privkey.pem"
  chmod 600 "$SSL_DIR/fullchain.pem" "$SSL_DIR/privkey.pem"
  chown root:root "$SSL_DIR/fullchain.pem" "$SSL_DIR/privkey.pem"

  if [[ "$redirect" == "1" ]]; then
    cat > "$SITE" <<EOF
# Сгенерировано deploy/apply-nginx-tls.sh (HTTPS + redirect).

server {
    listen 80 default_server;
    server_name _ ${name};
    return 301 https://\$host\$request_uri;
}

server {
    listen 443 ssl default_server;
    server_name _ ${name};

    ssl_certificate     ${SSL_DIR}/fullchain.pem;
    ssl_certificate_key ${SSL_DIR}/privkey.pem;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_prefer_server_ciphers off;

$(vnc_location)

$(proxy_root)
}
EOF
  else
    cat > "$SITE" <<EOF
# Сгенерировано deploy/apply-nginx-tls.sh (HTTPS).

server {
    listen 80 default_server;
    server_name _ ${name};

$(vnc_location)

$(proxy_root)
}

server {
    listen 443 ssl default_server;
    server_name _ ${name};

    ssl_certificate     ${SSL_DIR}/fullchain.pem;
    ssl_certificate_key ${SSL_DIR}/privkey.pem;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_prefer_server_ciphers off;

$(vnc_location)

$(proxy_root)
}
EOF
  fi
}

if [[ "$ENABLE_HTTPS" == "1" ]]; then
  [[ -f "$INCOMING/fullchain.pem" && -f "$INCOMING/privkey.pem" ]] || die "Нет PEM в $INCOMING"
  write_https "$SERVER_NAME" "$REDIRECT_HTTP"
else
  write_http_only "$SERVER_NAME"
fi

ln -sfn "$SITE" /etc/nginx/sites-enabled/bawh
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx

if [[ "$ENABLE_HTTPS" == "1" ]] && command -v ufw >/dev/null 2>&1; then
  if ufw status | grep -q '^Status: active'; then
    ufw allow 443/tcp comment bawh-https || true
  fi
fi

ENV_FILE="$INSTALL_DIR/.env"
if [[ -f "$ENV_FILE" ]]; then
  if [[ "$SET_SECURE_COOKIE" == "1" && "$ENABLE_HTTPS" == "1" ]]; then
    if grep -q '^SESSION_COOKIE_SECURE=' "$ENV_FILE"; then
      sed -i 's/^SESSION_COOKIE_SECURE=.*/SESSION_COOKIE_SECURE=1/' "$ENV_FILE"
    else
      printf '\nSESSION_COOKIE_SECURE=1\n' >> "$ENV_FILE"
    fi
  else
    if grep -q '^SESSION_COOKIE_SECURE=' "$ENV_FILE"; then
      sed -i 's/^SESSION_COOKIE_SECURE=.*/SESSION_COOKIE_SECURE=0/' "$ENV_FILE"
    fi
  fi
fi

echo "nginx tls apply ok enable=${ENABLE_HTTPS} redirect=${REDIRECT_HTTP} name=${SERVER_NAME}"
