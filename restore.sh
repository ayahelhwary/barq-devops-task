#!/usr/bin/env bash
set -euo pipefail

CONTAINER="${POSTGRES_CONTAINER:-postgres}"
DB_USER="${POSTGRES_USER:-barq_app}"
DB_NAME="${POSTGRES_DB:-barq_tasks}"

if [ $# -lt 1 ]; then
  LATEST=$(ls -t ./backups/*.dump 2>/dev/null | head -n1 || true)
  if [ -n "$LATEST" ]; then
    echo "No file given; using most recent backup: $LATEST" >&2
    BACKUP_FILE="$LATEST"
  else
    echo "ERROR: no backup file specified and none found in ./backups" >&2
    echo "Usage: $0 <backup_file.dump>" >&2
    exit 2
  fi
else
  BACKUP_FILE="$1"
fi

if [ ! -f "$BACKUP_FILE" ]; then
  echo "ERROR: backup file not found: $BACKUP_FILE" >&2
  exit 1
fi

echo "Restoring '$BACKUP_FILE' into database '$DB_NAME' on container '$CONTAINER'..."
docker exec -i "$CONTAINER" pg_restore -U "$DB_USER" -d "$DB_NAME" --clean --if-exists < "$BACKUP_FILE"

echo "Restore complete."
