#!/usr/bin/env bash
# Daily pg_dump backup for GetJobbed self-hosted Postgres.
# Keeps last 7 backups. Run from project root or set COMPOSE_FILE.
#
# Crontab (run as deploy user):
#   0 2 * * * /home/ubuntu/GetJobbed/scripts/backup.sh >> /home/ubuntu/getjobbed-backup.log 2>&1

set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-/home/ubuntu/getjobbed-backups}"
COMPOSE_FILE="${COMPOSE_FILE:-$(dirname "$(realpath "$0")")/../docker-compose.yml}"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="${BACKUP_DIR}/getjobbed_${TIMESTAMP}.sql.gz"

mkdir -p "$BACKUP_DIR"

docker compose -f "$COMPOSE_FILE" exec -T db \
    pg_dump -U getjobbed getjobbed | gzip > "$BACKUP_FILE"

find "$BACKUP_DIR" -name 'getjobbed_*.sql.gz' -type f \
    | sort -r | tail -n +8 | xargs -r rm -f

echo "[$(date)] backup ok: $BACKUP_FILE ($(du -sh "$BACKUP_FILE" | cut -f1))"
