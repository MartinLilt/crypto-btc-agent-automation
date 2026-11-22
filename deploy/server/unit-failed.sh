#!/usr/bin/env bash
# OnFailure= handler (grid-alert@.service). Fires when a unit dies the way the
# bot cannot report itself: a crash before its own Telegram message, an import
# error after a bad deploy, OOM, the 1h timeout. main.py exits 0 on ordinary
# account failures (and says so in Telegram), so this is only the hard stuff.
set -uo pipefail
UNIT="${1:?usage: unit-failed.sh <unit>}"

RESULT=$(systemctl show -p Result --value "$UNIT" 2>/dev/null || echo unknown)
# Last lines of the unit's log; capped well under Telegram's 4096-char limit.
LOG=$(journalctl -u "$UNIT" -n 20 --no-pager -o cat 2>/dev/null | tail -c 3000)
MSG="‼️ $UNIT failed on $(hostname) (result: $RESULT)"$'\n\n'"$LOG"

set -a; . /etc/grid-stream.env; set +a
if [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ]; then
  curl -sS -m 15 -o /dev/null \
    "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
    --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" \
    --data-urlencode "text=$MSG" || true
fi
echo "$MSG"
