#!/usr/bin/env bash
# Nightly dump of the live position state. Railway used to own this; now we do.
set -euo pipefail
set -a; . /etc/grid-stream.env; set +a

DEST=/var/backups/grid
KEEP=14
mkdir -p "$DEST"
STAMP=$(date -u +%Y-%m-%dT%H-%M)
pg_dump "$DATABASE_URL" | gzip > "$DEST/grid-$STAMP.sql.gz"

# Keep the last N and drop the rest. A dump of this DB is well under a megabyte,
# so the retention is about noise, not disk.
ls -1t "$DEST"/grid-*.sql.gz | tail -n +$((KEEP + 1)) | xargs -r rm --

# Offsite ping: if HEALTHCHECK_BACKUP_URL is set, a silent failure to back up
# becomes a visible missed check-in instead of a surprise at restore time.
if [ -n "${HEALTHCHECK_BACKUP_URL:-}" ]; then
  curl -fsS -m 10 --retry 3 "$HEALTHCHECK_BACKUP_URL" > /dev/null || true
fi
