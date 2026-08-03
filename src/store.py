"""State storage — Postgres (durable + trade history) or JSON-file fallback.

The bot keeps one small portfolio state (cash, realized, bags, holds) plus an
append-only trade log. With DATABASE_URL set → Postgres (transactional, crash-
safe, queryable for reports/analytics). Without it → an atomic JSON file (safe
for the single-writer 4h cron; trade log goes to trades.jsonl).

Interface both backends implement:
    load()        -> {cash, realized, positions, holds}
    save(cash, realized, positions, holds)
    log_trade(**row)   row = symbol, side, kind, price, qty, usdt, pnl
"""

from __future__ import annotations

import json

from .config import STATE_DIR, config


def _default() -> dict:
    return {"cash": config.paper_start_balance, "realized": 0.0,
            "positions": {}, "holds": {}}


class JsonStore:
    backend = "json"

    def __init__(self) -> None:
        self.file = STATE_DIR / "grid_state.json"
        self.trades = STATE_DIR / "trades.jsonl"

    def load(self) -> dict:
        if self.file.exists():
            d = json.loads(self.file.read_text())
            return {"cash": d.get("cash", config.paper_start_balance),
                    "realized": d.get("realized", 0.0),
                    "positions": d.get("positions", {}),
                    "holds": d.get("holds", {})}
        return _default()

    def save(self, cash, realized, positions, holds) -> None:
        STATE_DIR.mkdir(exist_ok=True)
        payload = {"cash": cash, "realized": realized,
                   "positions": positions, "holds": holds}
        tmp = self.file.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2))
        tmp.replace(self.file)          # atomic rename — no half-written file

    def log_trade(self, **row) -> None:
        STATE_DIR.mkdir(exist_ok=True)
        with self.trades.open("a") as f:
            f.write(json.dumps(row) + "\n")


class PostgresStore:
    backend = "postgres"

    def __init__(self, dsn: str) -> None:
        import psycopg
        self.conn = psycopg.connect(dsn, autocommit=True)
        with self.conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS portfolio (
                    id INT PRIMARY KEY DEFAULT 1,
                    cash DOUBLE PRECISION, realized DOUBLE PRECISION,
                    positions JSONB, holds JSONB,
                    updated_at TIMESTAMPTZ DEFAULT now());""")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id BIGSERIAL PRIMARY KEY, ts TIMESTAMPTZ DEFAULT now(),
                    symbol TEXT, side TEXT, kind TEXT,
                    price DOUBLE PRECISION, qty DOUBLE PRECISION,
                    usdt DOUBLE PRECISION, pnl DOUBLE PRECISION);""")

    def load(self) -> dict:
        with self.conn.cursor() as cur:
            cur.execute("SELECT cash, realized, positions, holds FROM portfolio WHERE id=1;")
            r = cur.fetchone()
        if not r:
            return _default()
        return {"cash": r[0], "realized": r[1],
                "positions": r[2] or {}, "holds": r[3] or {}}

    def save(self, cash, realized, positions, holds) -> None:
        from psycopg.types.json import Json
        with self.conn.cursor() as cur:
            cur.execute("""
                INSERT INTO portfolio (id, cash, realized, positions, holds, updated_at)
                VALUES (1, %s, %s, %s, %s, now())
                ON CONFLICT (id) DO UPDATE SET
                    cash=EXCLUDED.cash, realized=EXCLUDED.realized,
                    positions=EXCLUDED.positions, holds=EXCLUDED.holds,
                    updated_at=now();""",
                (cash, realized, Json(positions), Json(holds)))

    def log_trade(self, **row) -> None:
        with self.conn.cursor() as cur:
            cur.execute("""
                INSERT INTO trades (symbol, side, kind, price, qty, usdt, pnl)
                VALUES (%(symbol)s, %(side)s, %(kind)s, %(price)s, %(qty)s,
                        %(usdt)s, %(pnl)s);""", row)


def get_store():
    if config.database_url:
        try:
            return PostgresStore(config.database_url)
        except Exception as exc:
            print(f"[store] Postgres unavailable ({str(exc)[:70]}) — JSON fallback")
    return JsonStore()