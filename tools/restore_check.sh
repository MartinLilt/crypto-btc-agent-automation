#!/usr/bin/env bash
# Restore drill for the offsite backup — run on the owner's Mac.
#
# Takes an encrypted nightly dump (the file the bot sends to Telegram), decrypts
# it with the private age key that lives only on this Mac, restores it into a
# throwaway Postgres 18 container, and prints the numbers to compare with the
# live database. A backup that has never been restored is a hope, not a backup.
#
#   tools/restore_check.sh path/to/grid-2026-11-23T03-17.sql.gz.age
#   tools/restore_check.sh --file-id <file_id from the pg-backup log>
#
# --file-id downloads the file through the bot (TELEGRAM_BOT_TOKEN from .env).
# Needs: age, docker. Key: ~/.config/grid-stream/backup.agekey (override: AGE_KEY).
# To restore for real, the decrypted dump is left in the work dir it prints.
set -euo pipefail
cd "$(dirname "$0")/.."

KEY=${AGE_KEY:-$HOME/.config/grid-stream/backup.agekey}
[ -r "$KEY" ] || { echo "no age key at $KEY"; exit 1; }
WORK=$(mktemp -d)
NAME=grid-restore-check-$$
cleanup() { docker rm -f "$NAME" >/dev/null 2>&1 || true; }
trap cleanup EXIT

if [ "${1:-}" = "--file-id" ]; then
  FILE_ID=${2:?file_id}
  TOKEN=$(sed -n 's/^TELEGRAM_BOT_TOKEN=\([^ #]*\).*/\1/p' .env)
  [ -n "$TOKEN" ] || { echo "TELEGRAM_BOT_TOKEN not in .env"; exit 1; }
  FPATH=$(curl -fsS "https://api.telegram.org/bot${TOKEN}/getFile" \
            --form-string "file_id=${FILE_ID}" \
          | sed -n 's/.*"file_path":"\([^"]*\)".*/\1/p')
  [ -n "$FPATH" ] || { echo "getFile returned no path"; exit 1; }
  IN="$WORK/backup.sql.gz.age"
  curl -fsS -o "$IN" "https://api.telegram.org/file/bot${TOKEN}/${FPATH}"
else
  IN=${1:?usage: restore_check.sh <file.age> | --file-id <id>}
fi

echo "encrypted : $IN ($(wc -c < "$IN" | tr -d ' ') bytes)"
age -d -i "$KEY" -o "$WORK/backup.sql.gz" "$IN"
gunzip -t "$WORK/backup.sql.gz"
echo "decrypted : $WORK/backup.sql.gz (gzip OK)"

docker run -d --name "$NAME" -e POSTGRES_PASSWORD=x -e POSTGRES_DB=grid postgres:18 >/dev/null
for _ in $(seq 60); do docker exec "$NAME" pg_isready -q -U postgres -d grid && break; sleep 1; done
# The dump was taken by the role `grid`; create it so ownership lines apply.
docker exec "$NAME" psql -qU postgres -d grid -c "CREATE ROLE grid" >/dev/null
gunzip -c "$WORK/backup.sql.gz" | docker exec -i "$NAME" psql -qU postgres -d grid -v ON_ERROR_STOP=1 >/dev/null
echo "restored  : OK"
docker exec "$NAME" psql -U postgres -d grid -At -F ' | ' -c "
  SELECT 'trades', count(*), max(id), round(sum(coalesce(pnl,0))::numeric, 4) FROM trades
  UNION ALL SELECT 'portfolio cash', id, null, round(cash::numeric, 8) FROM portfolio
  UNION ALL SELECT 'flows', count(*), max(id), round(sum(amount)::numeric, 4) FROM flows;"
