#!/usr/bin/env bash
# Tripwire for the thing that actually happens to small VPS boxes: someone gets
# in and runs a miner. A miner is loud in exactly two ways — it pegs the CPU and
# it opens or dials sockets nobody asked for — so those are what we watch.
#
# This box is idle ~99% of the time (one 4h cycle of a few seconds), which is
# what makes a plain load threshold usable here; it would be useless on a busy
# server.
set -uo pipefail
export LC_ALL=C

STATE=/var/lib/grid-watchdog
mkdir -p "$STATE"
ALERTS=()

# --- 1. listening sockets we did not put there ---------------------------------
# Anything bound to a public address other than ssh, or any new local service.
UNEXPECTED=$(ss -tlnH | awk '{print $4}' | sed 's/.*://' | sort -u \
             | grep -vE '^(22|53|5432)$' || true)
[ -n "$UNEXPECTED" ] && ALERTS+=("unexpected listening port(s): $(echo "$UNEXPECTED" | tr '\n' ' ')")

# --- 2. sustained CPU ----------------------------------------------------------
# 15-minute average, so a legitimate trading cycle (seconds) cannot trip it.
LOAD15=$(awk '{print $3}' /proc/loadavg)
if awk "BEGIN{exit !($LOAD15 > 1.0)}"; then
  TOP=$(ps -eo pcpu,user,comm --sort=-pcpu | sed -n '2,4p' | tr '\n' ';')
  ALERTS+=("15-min load $LOAD15 on an idle box — top: $TOP")
fi

# --- 3. outbound connections to anything but the handful we expect -------------
# Mining pools keep a long-lived TCP session open; our own cycle is short and
# talks only to Binance and Telegram, so a persistent stranger stands out.
STRANGERS=$(ss -tnH state established 2>/dev/null \
            | awk '{print $4, $5}' | grep -v '127\.0\.0\.1' | grep -v '::1' \
            | awk '{print $2}' | sed 's/:[0-9]*$//' | sort -u | head -20 || true)
if [ -n "$STRANGERS" ]; then
  RESOLVED=""
  for ip in $STRANGERS; do
    host=$(getent hosts "$ip" | awk '{print $2}')
    case "$host" in
      *binance*|*telegram*|*t.me*|*ubuntu.com*|*digitalocean*|*postgresql.org*|*github*) ;;
      *) RESOLVED="$RESOLVED $ip(${host:-unknown})" ;;
    esac
  done
  [ -n "$RESOLVED" ] && ALERTS+=("established connection to:$RESOLVED")
fi

# --- 4. fail2ban still standing ------------------------------------------------
systemctl is-active --quiet fail2ban || ALERTS+=("fail2ban is NOT running")

send() {
  ( set -a; . /etc/grid-stream.env; set +a
    if [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ]; then
      curl -sS -m 15 -o /dev/null \
        "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
        --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" \
        --data-urlencode "text=$1" || true
    fi )
  echo "$1"
}

# --- 5. the trading cycle is alive ---------------------------------------------
# main.py touches last_ok when every account finished its cycle. Stale means the
# timer stopped, the cycle crashes before its own Telegram message, or it is
# stuck — all silent otherwise. 4h45m = one 4h period plus slack. The message
# names the last good time, not the age, so it stays identical between runs and
# the dedup below sends it once (then daily), not every 15 minutes.
# Does NOT cover the box itself being down: nothing on the box can.
OK=/var/lib/grid-stream/last_ok
STALE_AFTER=${STALE_AFTER_SEC:-17100}
STALE=0
if [ -f "$OK" ]; then
  OK_AT=$(stat -c %Y "$OK")
  if [ $(( $(date +%s) - OK_AT )) -gt "$STALE_AFTER" ]; then
    ALERTS+=("no successful trading cycle since $(date -u -d "@$OK_AT" '+%Y-%m-%d %H:%M') UTC (timer: $(systemctl is-active grid-stream.timer))")
    STALE=1
  fi
else
  ALERTS+=("no successful trading cycle recorded ($OK missing; timer: $(systemctl is-active grid-stream.timer))")
  STALE=1
fi
if [ "$STALE" = 1 ]; then
  : > "$STATE/stale_alerted"
elif [ -f "$STATE/stale_alerted" ]; then
  rm -f "$STATE/stale_alerted"
  send "✅ grid-stream: trading cycle is back — last ok $(date -u -d "@$OK_AT" '+%Y-%m-%d %H:%M') UTC"
fi

# All clear: forget the last alarm too, or the same alarm coming back within a
# day would be swallowed by the dedup as a repeat.
[ ${#ALERTS[@]} -eq 0 ] && { : > "$STATE/last_clean"; rm -f "$STATE/fingerprint"; exit 0; }

MSG="⚠️ grid-stream box ($(hostname -I | awk '{print $1}'))"$'\n'"$(printf '• %s\n' "${ALERTS[@]}")"

# Do not repeat the same alarm every 15 minutes — only when it changes, or once
# a day if it persists. A tripwire that cries hourly gets muted, and a muted
# tripwire is not a tripwire.
FP=$(printf '%s' "$MSG" | sha256sum | cut -c1-16)
PREV=$(cat "$STATE/fingerprint" 2>/dev/null || true)
AGE=$(( $(date +%s) - $(stat -c %Y "$STATE/fingerprint" 2>/dev/null || echo 0) ))
if [ "$FP" = "$PREV" ] && [ "$AGE" -lt 86400 ]; then exit 0; fi
printf '%s' "$FP" > "$STATE/fingerprint"

send "$MSG"
