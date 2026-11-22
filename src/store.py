"""State storage — Postgres (durable + trade history) or JSON-file fallback.

The bot keeps one small portfolio state (cash, realized, bags, holds) plus an
append-only trade log. With DATABASE_URL set → Postgres (transactional, crash-
safe, queryable for reports/analytics). Without it → an atomic JSON file (safe
for the single-writer 4h cron; trade log goes to trades.jsonl).

Interface both backends implement:
    load()        -> {cash, realized, positions, holds}
    save(cash, realized, positions, holds)
    log_trade(**row)   row = symbol, side, kind, price, qty, usdt, pnl
    recent_sells(limit) -> [ {ts, symbol, kind, price, qty, usdt, pnl}, … ]  newest-first
    load_perf()   -> {net_deposits, n_flows, bench} | None   (None = never seeded)
    record_flow(amount, source, note, bench, cash=None, ts=None)
        one deposit/withdrawal + the benchmark after it; with `cash` it also
        commits the ledger cash in the SAME transaction (Postgres), so a crash
        can never make the next cycle count the same deposit twice.
"""

from __future__ import annotations

import json

from datetime import datetime, timezone

from .config import STATE_DIR, config


def _default() -> dict:
    return {"cash": config.paper_start_balance, "realized": 0.0,
            "positions": {}, "holds": {}}


class JsonStore:
    backend = "json"

    def __init__(self, account_id: int = 1) -> None:
        # slot 1 keeps the legacy filenames (existing local state); slot 2+ get
        # their own files so accounts never share a ledger.
        sfx = "" if account_id == 1 else f"_{account_id}"
        self.account_id = account_id
        self.file = STATE_DIR / f"grid_state{sfx}.json"
        self.trades = STATE_DIR / f"trades{sfx}.jsonl"

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

    def recent_sells(self, limit: int = 50) -> list[dict]:
        """Last `limit` SELL rows, newest first. JSONL has no ts → date shown '—'."""
        if not self.trades.exists():
            return []
        sells = [json.loads(l) for l in self.trades.read_text().splitlines()
                 if l.strip() and json.loads(l).get("side") == "SELL"]
        return list(reversed(sells[-limit:]))

    # ── performance (deposits + benchmark) ───────────────────────────────
    @property
    def _perf_file(self):
        sfx = "" if self.account_id == 1 else f"_{self.account_id}"
        return STATE_DIR / f"perf{sfx}.json"

    def load_perf(self) -> dict | None:
        if not self._perf_file.exists():
            return None
        d = json.loads(self._perf_file.read_text())
        flows = d.get("flows", [])
        if not flows:
            return None
        return {"net_deposits": sum(f["amount"] for f in flows),
                "n_flows": len(flows), "bench": d.get("bench", {})}

    def record_flow(self, amount, source, note, bench, cash=None, ts=None) -> None:
        STATE_DIR.mkdir(exist_ok=True)
        d = (json.loads(self._perf_file.read_text())
             if self._perf_file.exists() else {"flows": [], "bench": {}})
        d["flows"].append({"ts": (ts or datetime.now(timezone.utc)).isoformat(),
                           "amount": amount, "source": source, "note": note})
        d["bench"] = bench
        tmp = self._perf_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, indent=2))
        tmp.replace(self._perf_file)
        if cash is not None:
            s = self.load()
            self.save(cash, s["realized"], s["positions"], s["holds"])



class PostgresStore:
    backend = "postgres"

    def __init__(self, dsn: str, account_id: int = 1) -> None:
        import psycopg
        self.account_id = account_id
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
            # Multi-account migration (idempotent): tag trades by account; the
            # pre-existing single-user rows belong to slot 1. portfolio already
            # keys by id, which we reuse as the account slot — no PK surgery.
            cur.execute("ALTER TABLE trades ADD COLUMN IF NOT EXISTS account_id INT;")
            cur.execute("UPDATE trades SET account_id = 1 WHERE account_id IS NULL;")
            # Performance: every deposit/withdrawal, and the buy-&-hold benchmark
            # bought with exactly those flows (src/performance.py).
            cur.execute("""
                CREATE TABLE IF NOT EXISTS flows (
                    id BIGSERIAL PRIMARY KEY, ts TIMESTAMPTZ DEFAULT now(),
                    account_id INT NOT NULL, amount DOUBLE PRECISION NOT NULL,
                    source TEXT NOT NULL, note TEXT);""")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS perf (
                    account_id INT PRIMARY KEY, bench JSONB NOT NULL,
                    updated_at TIMESTAMPTZ DEFAULT now());""")

    def load(self) -> dict:
        with self.conn.cursor() as cur:
            cur.execute("SELECT cash, realized, positions, holds FROM portfolio WHERE id=%s;",
                        (self.account_id,))
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
                VALUES (%s, %s, %s, %s, %s, now())
                ON CONFLICT (id) DO UPDATE SET
                    cash=EXCLUDED.cash, realized=EXCLUDED.realized,
                    positions=EXCLUDED.positions, holds=EXCLUDED.holds,
                    updated_at=now();""",
                (self.account_id, cash, realized, Json(positions), Json(holds)))

    def log_trade(self, **row) -> None:
        with self.conn.cursor() as cur:
            cur.execute("""
                INSERT INTO trades (account_id, symbol, side, kind, price, qty, usdt, pnl)
                VALUES (%(account_id)s, %(symbol)s, %(side)s, %(kind)s, %(price)s,
                        %(qty)s, %(usdt)s, %(pnl)s);""",
                {**row, "account_id": self.account_id})

    def recent_sells(self, limit: int = 50) -> list[dict]:
        """Last `limit` SELL rows for THIS account, newest first."""
        cols = ("ts", "symbol", "kind", "price", "qty", "usdt", "pnl")
        with self.conn.cursor() as cur:
            cur.execute("""
                SELECT ts, symbol, kind, price, qty, usdt, pnl
                FROM trades WHERE side='SELL' AND account_id=%s
                ORDER BY ts DESC LIMIT %s;""", (self.account_id, limit))
            return [dict(zip(cols, r)) for r in cur.fetchall()]

    def load_perf(self) -> dict | None:
        with self.conn.cursor() as cur:
            cur.execute("SELECT coalesce(sum(amount), 0), count(*) FROM flows "
                        "WHERE account_id=%s;", (self.account_id,))
            total, n = cur.fetchone()
            if not n:
                return None
            cur.execute("SELECT bench FROM perf WHERE account_id=%s;", (self.account_id,))
            r = cur.fetchone()
        return {"net_deposits": total, "n_flows": n, "bench": (r[0] if r else {}) or {}}

    def record_flow(self, amount, source, note, bench, cash=None, ts=None) -> None:
        from psycopg.types.json import Json
        with self.conn.transaction(), self.conn.cursor() as cur:
            cur.execute("""INSERT INTO flows (ts, account_id, amount, source, note)
                           VALUES (coalesce(%s, now()), %s, %s, %s, %s);""",
                        (ts, self.account_id, amount, source, note))
            cur.execute("""INSERT INTO perf (account_id, bench, updated_at)
                           VALUES (%s, %s, now())
                           ON CONFLICT (account_id) DO UPDATE SET
                               bench=EXCLUDED.bench, updated_at=now();""",
                        (self.account_id, Json(bench)))
            if cash is not None:
                cur.execute("UPDATE portfolio SET cash=%s, updated_at=now() WHERE id=%s;",
                            (cash, self.account_id))



def get_store(account_id: int = 1):
    if config.database_url:
        try:
            return PostgresStore(config.database_url, account_id)
        except Exception as exc:
            print(f"[store] Postgres unavailable ({str(exc)[:70]}) — JSON fallback")
    return JsonStore(account_id)