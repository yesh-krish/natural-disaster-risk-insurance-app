#!/usr/bin/env bash
# Manage keys for the public NDApp API (one key per person, so you can revoke one later).
#
#   sudo bash deploy/api_key.sh add <name>      create a key for someone and print it
#   sudo bash deploy/api_key.sh remove <name>   revoke that person's key
#   sudo bash deploy/api_key.sh list            show who has a key
#
# Keys are stored as "name key" lines in /etc/ndapp-api-keys (root only), and copied into
# /etc/ndapp-api.env for the ndapp-api service, which is restarted after every change.
set -euo pipefail

KEYS_FILE="${NDAPP_KEYS_FILE:-/etc/ndapp-api-keys}"
ENV_FILE="${NDAPP_API_ENV_FILE:-/etc/ndapp-api.env}"

die() { echo "$*" >&2; exit 1; }
[[ $EUID -eq 0 ]] || die "Run with sudo: sudo bash deploy/api_key.sh $*"

touch "$KEYS_FILE"
chmod 600 "$KEYS_FILE"

sync_env() {
  local keys
  keys="$(awk 'NF == 2 {print $2}' "$KEYS_FILE" | paste -sd, -)"
  # Keep any other settings (e.g. NDAPP_ALLOWED_ORIGINS); replace only the key line.
  { grep -v '^NDAPP_API_KEYS=' "$ENV_FILE" 2>/dev/null || true
    printf 'NDAPP_API_KEYS=%s\n' "$keys"; } > "$ENV_FILE.tmp"
  mv "$ENV_FILE.tmp" "$ENV_FILE"
  chmod 600 "$ENV_FILE"
}

restart_api() {
  if systemctl is-enabled ndapp-api >/dev/null 2>&1; then
    if [[ -s "$KEYS_FILE" ]]; then
      systemctl restart ndapp-api
    else
      systemctl stop ndapp-api
      echo "No keys left, so the public API is now off."
    fi
  fi
}

valid_name() {
  [[ "$1" =~ ^[A-Za-z0-9._-]+$ ]] || die "Use letters, numbers, dots, dashes or underscores for the name."
}

case "${1:-}" in
  add)
    name="${2:-}"; [[ -n "$name" ]] || die "Usage: sudo bash deploy/api_key.sh add <name>"
    valid_name "$name"
    if awk -v n="$name" '$1 == n {found = 1} END {exit !found}' "$KEYS_FILE"; then
      die "'$name' already has a key. Remove it first to issue a new one."
    fi
    key="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
    echo "$name $key" >> "$KEYS_FILE"
    sync_env
    restart_api
    echo "API key for $name (send it privately):"
    echo "$key"
    ;;
  remove)
    name="${2:-}"; [[ -n "$name" ]] || die "Usage: sudo bash deploy/api_key.sh remove <name>"
    valid_name "$name"
    awk -v n="$name" '$1 == n {found = 1} END {exit !found}' "$KEYS_FILE" || die "No key named '$name'."
    awk -v n="$name" '$1 != n' "$KEYS_FILE" > "$KEYS_FILE.tmp"
    mv "$KEYS_FILE.tmp" "$KEYS_FILE"
    chmod 600 "$KEYS_FILE"
    sync_env
    restart_api
    echo "Removed the key for $name."
    ;;
  list)
    if [[ -s "$KEYS_FILE" ]]; then awk '{print $1}' "$KEYS_FILE"; else echo "(no keys)"; fi
    ;;
  sync)
    sync_env
    ;;
  *)
    die "Usage: sudo bash deploy/api_key.sh add <name> | remove <name> | list"
    ;;
esac
