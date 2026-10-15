#!/bin/sh
set -eu

: "${FALKORDB_PASSWORD:?FALKORDB_PASSWORD is required}"

config_dir="${FALKORDB_CONFIG_DIR:-/data}"
mkdir -p "$config_dir"

escaped_password=$(printf '%s' "$FALKORDB_PASSWORD" | sed 's/\\/\\\\/g; s/"/\\"/g')

umask 077
{
  printf 'requirepass "%s"\n' "$escaped_password"
  printf 'appendonly yes\n'
  printf 'appendfsync everysec\n'
  printf 'dir /data\n'
} > "$config_dir/redis.conf"
