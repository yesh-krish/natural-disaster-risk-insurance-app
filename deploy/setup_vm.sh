#!/usr/bin/env bash
# Set up NDApp on a fresh Ubuntu 24.04 machine (Jetstream2 VM, or Ubuntu in WSL for testing).
#
# Run from inside a checkout of the repo:
#   sudo bash deploy/setup_vm.sh                      # plain HTTP on port 80 (WSL test)
#   sudo bash deploy/setup_vm.sh ndapp.example.org    # HTTPS for that hostname (Jetstream2)
#
# Options (environment variables):
#   SKIP_DATA=1     don't download the flood/fire rasters (do it later, or copy data/ up yourself)
#   NO_PASSWORD=1   serve the site publicly without the HTTP Basic password
#
# Safe to rerun: it updates packages, the venv, the service and the Caddy config in place,
# keeps an existing password, and the data scripts skip files that are already downloaded.
set -euo pipefail

DOMAIN="${1:-}"
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_USER="ndapp"
PORT=8000
ENV_FILE="/etc/ndapp.env"

if [[ $EUID -ne 0 ]]; then
  echo "Run with sudo: sudo bash deploy/setup_vm.sh [hostname]" >&2
  exit 1
fi
[[ -f "$APP_DIR/server.py" ]] || { echo "server.py not found in $APP_DIR" >&2; exit 1; }

echo "==> Installing system packages"
apt-get update -y
apt-get install -y python3 python3-venv python3-pip git curl debian-keyring \
  debian-archive-keyring apt-transport-https gnupg

if ! command -v caddy >/dev/null; then
  echo "==> Installing Caddy (web server with automatic HTTPS)"
  curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/gpg.key \
    | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt \
    > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -y
  apt-get install -y caddy
fi

echo "==> Creating service user '$APP_USER'"
id -u "$APP_USER" >/dev/null 2>&1 || useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"
mkdir -p "$APP_DIR/data"
chown -R "$APP_USER:$APP_USER" "$APP_DIR"
# The service user must be able to reach the checkout (e.g. when it lives in /home/<you>).
parent="$APP_DIR"
while [[ "$parent" != "/" ]]; do parent="$(dirname "$parent")"; chmod o+x "$parent"; done

echo "==> Building Python environment"
sudo -u "$APP_USER" python3 -m venv "$APP_DIR/.venv"
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install --quiet --upgrade pip
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"

if [[ "${SKIP_DATA:-0}" != "1" ]]; then
  echo "==> Downloading data (several GB; reruns skip finished files)"
  sudo -u "$APP_USER" -H bash -c "cd '$APP_DIR' && .venv/bin/python prepare_data.py --tile n29w99"
  sudo -u "$APP_USER" -H bash -c "cd '$APP_DIR' && .venv/bin/python prepare_fire_data.py --region CONUS"
fi

echo "==> Writing $ENV_FILE"
if [[ ! -f "$ENV_FILE" ]]; then
  {
    echo "HOST=127.0.0.1"
    echo "PORT=$PORT"
    if [[ "${NO_PASSWORD:-0}" != "1" ]]; then
      echo "NDAPP_USER=ndapp"
      echo "NDAPP_PASSWORD=$(tr -dc 'A-HJ-NP-Za-km-z2-9' </dev/urandom | head -c 16)"
    fi
  } > "$ENV_FILE"
  chmod 600 "$ENV_FILE"
fi

echo "==> Installing systemd service"
sed -e "s#__APP_DIR__#$APP_DIR#g" -e "s#__APP_USER__#$APP_USER#g" \
  "$APP_DIR/deploy/ndapp.service" > /etc/systemd/system/ndapp.service
systemctl daemon-reload
systemctl enable ndapp >/dev/null
systemctl restart ndapp

echo "==> Configuring Caddy"
SITE="${DOMAIN:-:80}"
sed -e "s#__SITE__#$SITE#g" -e "s#__PORT__#$PORT#g" \
  "$APP_DIR/deploy/Caddyfile.template" > /etc/caddy/Caddyfile
systemctl enable caddy >/dev/null
systemctl reload caddy 2>/dev/null || systemctl restart caddy

echo "==> Checking the app answers"
for _ in $(seq 1 20); do
  curl -fs -o /dev/null "http://127.0.0.1:$PORT/api/v1/health" -u "$(grep -s NDAPP_USER "$ENV_FILE" | cut -d= -f2):$(grep -s NDAPP_PASSWORD "$ENV_FILE" | cut -d= -f2)" && ok=1 && break
  sleep 1
done
if [[ "${ok:-0}" != "1" ]]; then
  echo "App did not respond. See: journalctl -u ndapp -n 50" >&2
  exit 1
fi

echo
echo "Done."
if [[ -n "$DOMAIN" ]]; then echo "Site: https://$DOMAIN/"; else echo "Site: http://localhost/"; fi
if grep -q NDAPP_PASSWORD "$ENV_FILE"; then
  echo "Login: user 'ndapp', password in $ENV_FILE (sudo cat $ENV_FILE)"
fi
echo "Logs: journalctl -u ndapp -f"
