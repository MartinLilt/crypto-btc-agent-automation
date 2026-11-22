#!/usr/bin/env bash
# Post-boot setup for the box (DigitalOcean droplet since 2026-10-09). Idempotent, so it is safe to re-run.
# Run as ops: sudo bash setup.sh
#
# Since the 2026-10 rebuild the bot runs as `grid`, a system user with no shell,
# no sudo and no write access to its own code. The first box ran it as `bot`
# with NOPASSWD sudo, so any hole in the bot was root.
set -euo pipefail

REPO=https://github.com/MartinLilt/crypto-btc-agent-automation.git
# Unit files come from wherever this script sits, not from the clone: during the
# first run they are newer than what is pushed to GitHub.
SRC="$(cd "$(dirname "$0")" && pwd)"
APP=/opt/grid-stream
RUN_USER=grid
DB_USER=grid
DB_NAME=grid
PG_MAJOR=18   # must match the server the dump came from (Railway, then box 1: 18)

# 1. Runtime user.
id "$RUN_USER" >/dev/null 2>&1 || \
  useradd --system --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin "$RUN_USER"

# 2. Code and venv, owned by root and read-only to the bot. Public repo, so no
#    deploy key. Secrets live in /etc/grid-stream.env and never in git.
if [ -d "$APP/.git" ]; then
  git -C "$APP" pull --ff-only
else
  git clone "$REPO" "$APP"
fi
python3 -m venv "$APP/.venv"
"$APP/.venv/bin/pip" install --upgrade pip
"$APP/.venv/bin/pip" install -r "$APP/requirements.txt"
chown -R root:root "$APP"
chmod -R go-w "$APP"

# 3. Postgres from PGDG (Ubuntu 24.04 ships 16; the dump is from 18).
#    The repo helper comes with postgresql-common, a distro package.
if ! command -v "/usr/lib/postgresql/$PG_MAJOR/bin/postgres" >/dev/null 2>&1; then
  /usr/share/postgresql-common/pgdg/apt.postgresql.org.sh -y
  apt-get install -y "postgresql-$PG_MAJOR" "postgresql-client-$PG_MAJOR"
fi
# Listens on localhost only (the PGDG default); the watchdog alarms on anything else.

if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='$DB_USER'" | grep -q 1; then
  DB_PASS=$(openssl rand -base64 24 | tr -d '/+=')
  sudo -u postgres psql -c "CREATE USER $DB_USER WITH PASSWORD '$DB_PASS';"
  sudo -u postgres createdb -O "$DB_USER" -E UTF8 -T template0 "$DB_NAME"
  umask 077
  echo "DATABASE_URL=postgresql://$DB_USER:$DB_PASS@127.0.0.1:5432/$DB_NAME" \
    > /etc/grid-stream.db.env
  echo "  wrote /etc/grid-stream.db.env, append its line to /etc/grid-stream.env"
fi

# 4. Secrets file: root writes it, the bot can only read it.
touch /etc/grid-stream.env
chown root:"$RUN_USER" /etc/grid-stream.env
chmod 640 /etc/grid-stream.env

install -d -m 700 /var/backups/grid

# 5. systemd units and scripts.
install -m 644 "$SRC/grid-stream.service" /etc/systemd/system/
install -m 644 "$SRC/grid-stream.timer"   /etc/systemd/system/
install -m 644 "$SRC/pg-backup.service"   /etc/systemd/system/
install -m 644 "$SRC/pg-backup.timer"     /etc/systemd/system/
install -m 644 "$SRC/watchdog.service"    /etc/systemd/system/
install -m 644 "$SRC/watchdog.timer"      /etc/systemd/system/
install -m 755 "$SRC/pg-backup.sh"        /usr/local/bin/pg-backup
install -m 755 "$SRC/watchdog.sh"         /usr/local/bin/grid-watchdog
install -m 644 "$SRC/grid-alert@.service" /etc/systemd/system/
install -m 755 "$SRC/unit-failed.sh"      /usr/local/bin/grid-unit-failed
systemctl daemon-reload

# The trading timer is NOT enabled here on purpose. Enabling it starts trading;
# that is a separate step once the state is restored and a paper cycle is clean.
echo
echo "Setup done. Next, by hand:"
echo "  1. sudoedit /etc/grid-stream.env   (secrets + the DATABASE_URL line)"
echo "  2. restore the dump, then: systemctl enable --now pg-backup.timer watchdog.timer"
echo "  3. dry cycle in paper, then: systemctl enable --now grid-stream.timer"
