"""Telegram report subscribers — whitelist-gated /start enrollment.

Delivery is by DM, and Telegram DMs need a numeric chat_id (you can't message a
@username). So a whitelisted user (username in config.telegram_whitelist) becomes
a subscriber by messaging the bot: we learn their chat_id from getUpdates (polled
once per cron cycle), store it, and push every report to all subscribers.
Non-whitelisted users are refused. Postgres when DATABASE_URL is set, else a JSON
file — same split as the trade store.

Interface both backends implement:
    offset() -> int              last consumed getUpdates offset
    set_offset(o)
    add(chat_id, username)       register / refresh a subscriber
    all() -> [(chat_id, username), ...]
"""
from __future__ import annotations

import json

from .config import STATE_DIR, config


class JsonSubs:
    def __init__(self) -> None:
        self.file = STATE_DIR / "subscribers.json"

    def _read(self) -> dict:
        if self.file.exists():
            return json.loads(self.file.read_text())
        return {"offset": 0, "subs": {}}

    def _write(self, d: dict) -> None:
        STATE_DIR.mkdir(exist_ok=True)
        tmp = self.file.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, indent=2))
        tmp.replace(self.file)

    def offset(self) -> int:
        return int(self._read().get("offset", 0))

    def set_offset(self, o: int) -> None:
        d = self._read(); d["offset"] = o; self._write(d)

    def add(self, chat_id: int, username: str) -> None:
        d = self._read(); d["subs"][str(chat_id)] = username; self._write(d)

    def all(self) -> list[tuple[int, str]]:
        return [(int(k), v) for k, v in self._read().get("subs", {}).items()]


class PgSubs:
    def __init__(self, dsn: str) -> None:
        import psycopg
        self.conn = psycopg.connect(dsn, autocommit=True)
        with self.conn.cursor() as cur:
            cur.execute("""CREATE TABLE IF NOT EXISTS subscribers (
                chat_id BIGINT PRIMARY KEY, username TEXT,
                added_at TIMESTAMPTZ DEFAULT now());""")
            cur.execute("CREATE TABLE IF NOT EXISTS bot_meta (k TEXT PRIMARY KEY, v TEXT);")

    def offset(self) -> int:
        with self.conn.cursor() as cur:
            cur.execute("SELECT v FROM bot_meta WHERE k='tg_offset';")
            r = cur.fetchone()
        return int(r[0]) if r else 0

    def set_offset(self, o: int) -> None:
        with self.conn.cursor() as cur:
            cur.execute("""INSERT INTO bot_meta (k, v) VALUES ('tg_offset', %s)
                ON CONFLICT (k) DO UPDATE SET v=EXCLUDED.v;""", (str(o),))

    def add(self, chat_id: int, username: str) -> None:
        with self.conn.cursor() as cur:
            cur.execute("""INSERT INTO subscribers (chat_id, username) VALUES (%s, %s)
                ON CONFLICT (chat_id) DO UPDATE SET username=EXCLUDED.username;""",
                        (chat_id, username))

    def all(self) -> list[tuple[int, str]]:
        with self.conn.cursor() as cur:
            cur.execute("SELECT chat_id, username FROM subscribers;")
            return [(r[0], r[1]) for r in cur.fetchall()]


def get_subscribers():
    if config.database_url:
        try:
            return PgSubs(config.database_url)
        except Exception as exc:
            print(f"[subs] Postgres unavailable ({str(exc)[:60]}) — JSON fallback")
    return JsonSubs()