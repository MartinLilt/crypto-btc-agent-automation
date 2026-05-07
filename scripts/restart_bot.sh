#!/usr/bin/env bash
# Cleanly restart the bot.
#   1. Kills every running `python main.py` (avoids stale Telegram polling lock)
#   2. Calls Telegram /close to free server-side polling state
#   3. Starts fresh in background; verifies it reaches "Application started"
#
# Why this exists: nohup-detached bot processes don't show in `ps aux` with a
# truncated COMMAND column on macOS, so `pkill -f` searches sometimes miss them
# and zombies accumulate. This script uses pgrep on the un-truncated command
# line and kills by PID directly.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

echo "→ Searching for running bot processes..."
# pgrep matches against full argument list — bypasses ps's COMMAND truncation
PIDS=$(pgrep -f "python.*main\.py" || true)
if [ -n "$PIDS" ]; then
    echo "  found: $PIDS"
    echo "$PIDS" | xargs kill -9
    sleep 2
    LEFT=$(pgrep -f "python.*main\.py" || true)
    if [ -n "$LEFT" ]; then
        echo "❌ Some bots survived SIGKILL: $LEFT — investigate."
        exit 1
    fi
    echo "  all killed."
else
    echo "  none running."
fi

echo "→ Releasing Telegram polling lock (/close)..."
TOKEN=$(grep '^TELEGRAM_BOT_TOKEN=' .env | cut -d= -f2)
if [ -z "$TOKEN" ]; then
    echo "❌ TELEGRAM_BOT_TOKEN missing from .env"
    exit 1
fi
RESP=$(curl -s "https://api.telegram.org/bot${TOKEN}/close")
echo "  $RESP"
sleep 3

echo "→ Starting bot..."
# shellcheck disable=SC1091
source .venv/bin/activate
rm -f bot.log
nohup python main.py > bot.log 2>&1 &
NEW_PID=$!
echo "  spawned PID=$NEW_PID"

echo "→ Waiting up to 30s for 'Application started'..."
for _ in $(seq 1 30); do
    sleep 1
    if grep -q "Application started" bot.log 2>/dev/null; then
        echo "✅ Bot polling cleanly. PID=$NEW_PID"
        exit 0
    fi
    if grep -q "telegram.error.Conflict" bot.log 2>/dev/null; then
        echo "❌ Bot crashed with Telegram Conflict — another client uses this token."
        echo "   Check Docker / other machines / cron. Last log lines:"
        tail -8 bot.log
        exit 1
    fi
done
echo "⚠️  Timed out waiting for startup. Last log lines:"
tail -15 bot.log
exit 1