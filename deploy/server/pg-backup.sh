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

# Offsite copy: a box that disappears takes /var/backups with it (2026-09:
# Hetzner kept the dumps when it cancelled the account). Encrypt to the
# owner's age public key and send to the owner's Telegram chat, silently.
# Only the owner's Mac holds the private key; the chat holds ciphertext.
# Runs AFTER the local dump and retention, so a Telegram outage can never cost
# the local copy; it still fails the unit (-> OnFailure alert) so it is seen.
if [ -n "${BACKUP_AGE_RECIPIENT:-}" ]; then
  SRC_DUMP="$DEST/grid-$STAMP.sql.gz"
  ENC=$(mktemp --suffix=.age)
  trap 'rm -f "$ENC"' EXIT
  age -r "$BACKUP_AGE_RECIPIENT" -o "$ENC" "$SRC_DUMP"
  RESP=$(curl -fsS -m 60 --retry 3 \
    "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendDocument" \
    --form-string "chat_id=${TELEGRAM_CHAT_ID}" \
    --form-string "disable_notification=true" \
    --form-string "caption=grid-stream backup ${STAMP} UTC (age-encrypted)" \
    -F "document=@${ENC};filename=grid-${STAMP}.sql.gz.age") \
    || { echo "offsite: Telegram upload FAILED (local copy is fine: $SRC_DUMP)"; exit 1; }
  # file_id lets the owner fetch this exact file later (tools: getFile).
  echo "offsite: sent grid-${STAMP}.sql.gz.age $(stat -c %s "$ENC") bytes," \
       "file_id=$(printf '%s' "$RESP" | sed -n 's/.*"document":{[^}]*"file_id":"\([^"]*\)".*/\1/p')"
fi
