#!/usr/bin/env bash
set -euo pipefail

BACKUP_DIR="./backups"
CONTAINER="${POSTGRES_CONTAINER:-postgres}"
DB_USER="${POSTGRES_USER:-barq_app}"
DB_NAME="${POSTGRES_DB:-barq_tasks}"

mkdir -p "$BACKUP_DIR"
TIMESTAMP=$(date -u +%Y%m%dT%H%M%SZ)
BACKUP_FILE="${BACKUP_DIR}/barq_tasks_${TIMESTAMP}.dump"

echo "Backing up PostgreSQL database '$DB_NAME' from container '$CONTAINER'..."
docker exec "$CONTAINER" pg_dump -U "$DB_USER" -d "$DB_NAME" --format=custom > "$BACKUP_FILE"

if [ ! -s "$BACKUP_FILE" ]; then
  echo "ERROR: backup file is empty or was not created: $BACKUP_FILE" >&2
  exit 1
fi

echo "Backup complete: $BACKUP_FILE ($(du -h "$BACKUP_FILE" | cut -f1))"
echo "$BACKUP_FILE"
