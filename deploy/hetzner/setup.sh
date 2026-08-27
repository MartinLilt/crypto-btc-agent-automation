#!/usr/bin/env bash
# Post-boot setup for the Hetzner box. Idempotent — safe to re-run.
# Run as bot: sudo bash setup.sh
set -euo pipefail

REPO=https://github.com/MartinLilt/crypto-btc-agent-automation.git
# Unit files come from wherever this script sits, not from the clone: during the
# first run they are newer than what is pushed to GitHub.
SRC="$(cd "$(dirname "$0")" && pwd)"
APP=/opt/grid-stream
DB_USER=grid
DB_NAME=grid

# 1. Code. Public repo, so no deploy key; secrets live in /etc/grid-stream.env
#    and never in git.
if [ -d "$APP/.git" ]; then
  git -C "$APP" pull --ff-only
else
  mkdir -p "$APP"
  chown bot:bot "$APP"
  sudo -u bot git clone "$REPO" "$APP"
fi

# 2. Virtualenv.
sudo -u bot python3 -m venv "$APP/.venv"
sudo -u bot "$APP/.venv/bin/pip" install --upgrade pip
sudo -u bot "$APP/.venv/bin/pip" install -r "$APP/requirements.txt"

# 3. Postgres, localhost only. The password is generated once and written into
#    the env file; re-running setup keeps whatever is already there.
if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='$DB_USER'" | grep -q 1; then
  DB_PASS=$(openssl rand -base64 24 | tr -d '/+=')
  sudo -u postgres psql -c "CREATE USER $DB_USER WITH PASSWORD '$DB_PASS';"
  sudo -u postgres createdb -O "$DB_USER" "$DB_NAME"
  echo "DATABASE_URL=postgresql://$DB_USER:$DB_PASS@127.0.0.1:5432/$DB_NAME" \
    > /etc/grid-stream.db.env
  chmod 600 /etc/grid-stream.db.env
  echo "  wrote /etc/grid-stream.db.env — append its line to /etc/grid-stream.env"
fi

# 4. systemd units. The timer replaces Railway's cron; the service replaces the
#    deploy.
install -m 644 "$SRC/grid-stream.service" /etc/systemd/system/
install -m 644 "$SRC/grid-stream.timer"   /etc/systemd/system/
install -m 644 "$SRC/pg-backup.service"   /etc/systemd/system/
install -m 644 "$SRC/pg-backup.timer"     /etc/systemd/system/
install -m 755 "$SRC/pg-backup.sh"        /usr/local/bin/pg-backup
systemctl daemon-reload

# The timer is NOT enabled here on purpose. Enabling it starts trading; that is
# a separate, deliberate step once the Railway scheduler is off — two
# schedulers against one state means duplicate orders.
echo
echo "Setup done. Next, by hand:"
echo "  1. write /etc/grid-stream.env  (chmod 600, owner root, group bot)"
echo "  2. systemctl enable --now pg-backup.timer"
echo "  3. STOP the Railway cron, restore the dump, THEN:"
echo "     systemctl enable --now grid-stream.timer"
