"""
Database layer — SQLite (persistent) + Redis (cache).

SQLite tables:
  backtest_runs    — metadata per backtest run (symbol, period, stats)
  backtest_trades  — every simulated trade from backtest runs
  positions        — live/sim positions (one open at a time)

Redis keys (all with TTL):
  fg:{date}                 → Fear & Greed value for a date
  funding:{symbol}:{ts}     → funding rate snapshot
  candles:{symbol}:{interval}:{limit} → raw klines cache
  patterns:{symbol}         → latest computed patterns JSON
"""

import json
import logging
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Optional

from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

SQLITE_PATH = os.getenv("SQLITE_PATH", "data/backtest.db")
REDIS_URL = os.getenv("REDIS_URL",   "redis://localhost:6379/0")
TTL_FG = int(os.getenv("REDIS_TTL_FEAR_GREED", 3600))
TTL_FUNDING = int(os.getenv("REDIS_TTL_FUNDING",    300))
TTL_CANDLES = int(os.getenv("REDIS_TTL_CANDLES",    60))

# ── Redis (optional — bot works without it) ───────────────────────────────────

_redis_client = None
_redis_disabled = False   # set True after first failed connect → no retry spam


def _get_redis():
    """Return Redis client, or None if unavailable."""
    global _redis_client, _redis_disabled
    if _redis_disabled:
        return None
    if _redis_client is not None:
        return _redis_client
    try:
        import redis as redis_lib
        r = redis_lib.from_url(
            REDIS_URL, decode_responses=True,
            socket_timeout=2, socket_connect_timeout=2)
        r.ping()
        _redis_client = r
        logger.info("Redis connected: %s", REDIS_URL)
    except Exception as e:
        logger.warning("Redis unavailable (%s) — cache disabled", e)
        _redis_disabled = True   # ← don't retry on every call
        _redis_client = None
    return _redis_client


def cache_get(key: str) -> Optional[Any]:
    """Get JSON value from Redis. Returns None on miss or error."""
    r = _get_redis()
    if not r:
        return None
    try:
        raw = r.get(key)
        return json.loads(raw) if raw else None
    except Exception as e:
        logger.debug("Redis GET error: %s", e)
        return None


def cache_set(key: str, value: Any, ttl: int = 300) -> bool:
    """Set JSON value in Redis with TTL. Returns True on success."""
    r = _get_redis()
    if not r:
        return False
    try:
        r.setex(key, ttl, json.dumps(value, default=str))
        return True
    except Exception as e:
        logger.debug("Redis SET error: %s", e)
        return False


def cache_delete(key: str):
    """Delete a key from Redis."""
    r = _get_redis()
    if r:
        try:
            r.delete(key)
        except Exception:
            pass


# ── SQLite ────────────────────────────────────────────────────────────────────

def _ensure_dir():
    os.makedirs(os.path.dirname(os.path.abspath(SQLITE_PATH)), exist_ok=True)


@contextmanager
def _conn():
    """Context manager for SQLite connection."""
    _ensure_dir()
    con = sqlite3.connect(SQLITE_PATH)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def init_db():
    """Create tables if they don't exist. Call once on startup."""
    with _conn() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS backtest_runs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol      TEXT    NOT NULL,
            interval    TEXT    NOT NULL DEFAULT '1h',
            days        INTEGER NOT NULL,
            tp_pct      REAL    NOT NULL,
            sl_pct      REAL    NOT NULL,
            total_candles   INTEGER,
            total_signals   INTEGER,
            wins            INTEGER,
            losses          INTEGER,
            timeouts        INTEGER,
            win_rate_pct    REAL,
            avg_profit_pct  REAL,
            avg_loss_pct    REAL,
            total_pnl_pct   REAL,
            max_drawdown_pct REAL,
            sharpe_ratio    REAL,
            signal_freq     REAL,
            created_at  TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS backtest_trades (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id      INTEGER NOT NULL REFERENCES backtest_runs(id),
            symbol      TEXT    NOT NULL,
            -- Entry context
            entry_time  TEXT    NOT NULL,
            entry_price REAL    NOT NULL,
            weekday     TEXT,
            hour_utc    INTEGER,
            -- Layer snapshots at signal moment
            l1_atr      REAL,
            l1_adx      REAL,
            l2_ema50    REAL,
            l2_ema200   REAL,
            l2_gap_pct  REAL,
            l3_rsi      REAL,
            l3_macd_hist REAL,
            l4_pass     INTEGER,
            l5_spread_pct REAL,
            l6_rr_ratio REAL,
            l8_funding  REAL,
            l8_oi_chg   REAL,
            l9_fg_value INTEGER,
            l10_buy_ratio REAL,
            l10_net_vol REAL,
            -- Outcome
            result      TEXT,   -- TP_HIT / SL_HIT / TIMEOUT
            exit_price  REAL,
            exit_time   TEXT,
            pnl_pct     REAL,
            hold_hours  INTEGER,
            max_drawdown_pct REAL
        );

        CREATE INDEX IF NOT EXISTS idx_trades_symbol
            ON backtest_trades(symbol);
        CREATE INDEX IF NOT EXISTS idx_trades_entry_time
            ON backtest_trades(entry_time);
        CREATE INDEX IF NOT EXISTS idx_trades_result
            ON backtest_trades(result);

        CREATE TABLE IF NOT EXISTS positions (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol        TEXT    NOT NULL,
            mode          TEXT    NOT NULL,
            entry_time    TEXT    NOT NULL,
            entry_price   REAL    NOT NULL,
            qty           REAL    NOT NULL,
            budget        REAL    NOT NULL,
            sl_price      REAL    NOT NULL,
            tp_price      REAL    NOT NULL,
            breakeven_hit INTEGER NOT NULL DEFAULT 0,
            status        TEXT    NOT NULL DEFAULT 'open',
            exit_price    REAL,
            exit_time     TEXT,
            exit_reason   TEXT,
            pnl_pct       REAL,
            total_score   INTEGER,
            created_at    TEXT DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_positions_status
            ON positions(status);

        CREATE TABLE IF NOT EXISTS paper_trades (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol          TEXT    NOT NULL,
            entry_time      TEXT    NOT NULL,
            entry_price     REAL    NOT NULL,
            tp_pct          REAL    NOT NULL,
            sl_pct          REAL    NOT NULL,
            tp_price        REAL    NOT NULL,
            sl_price        REAL    NOT NULL,
            total_score     INTEGER,
            layer_snapshot  TEXT,
            status          TEXT    NOT NULL DEFAULT 'OPEN',
            exit_time       TEXT,
            exit_price      REAL,
            pnl_pct         REAL,
            pnl_pct_net_fees REAL,
            hold_hours      INTEGER,
            notified_open   INTEGER DEFAULT 0,
            notified_close  INTEGER DEFAULT 0,
            created_at      TEXT DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_paper_status ON paper_trades(status);
        CREATE INDEX IF NOT EXISTS idx_paper_symbol ON paper_trades(symbol);
        """)
    # Migrations — add columns that didn't exist in older schema
    with _conn() as con:
        for col_def in [
            "ALTER TABLE backtest_trades ADD COLUMN total_score INTEGER",
            "ALTER TABLE backtest_trades ADD COLUMN pnl_pct_net_fees REAL",
        ]:
            try:
                con.execute(col_def)
            except Exception:
                pass   # column already exists

    logger.info("SQLite initialised: %s", SQLITE_PATH)


# ── Write helpers ─────────────────────────────────────────────────────────────

def save_backtest_run(meta: dict) -> int:
    """
    Insert a backtest run summary. Returns the new run_id.
    meta keys match column names of backtest_runs.
    """
    cols = [
        "symbol", "interval", "days", "tp_pct", "sl_pct",
        "total_candles", "total_signals", "wins", "losses", "timeouts",
        "win_rate_pct", "avg_profit_pct", "avg_loss_pct", "total_pnl_pct",
        "max_drawdown_pct", "sharpe_ratio", "signal_freq",
    ]
    present = {k: meta[k] for k in cols if k in meta}
    placeholders = ", ".join("?" * len(present))
    col_names = ", ".join(present.keys())
    with _conn() as con:
        cur = con.execute(
            f"INSERT INTO backtest_runs ({col_names}) VALUES ({placeholders})",
            list(present.values()),
        )
        return cur.lastrowid


def save_backtest_trades(run_id: int, trades: list[dict]):
    """Bulk-insert trade records for a run."""
    if not trades:
        return
    cols = [
        "run_id", "symbol",
        "entry_time", "entry_price", "weekday", "hour_utc",
        "l1_atr", "l1_adx", "l2_ema50", "l2_ema200", "l2_gap_pct",
        "l3_rsi", "l3_macd_hist", "l4_pass", "l5_spread_pct", "l6_rr_ratio",
        "l8_funding", "l8_oi_chg", "l9_fg_value", "l10_buy_ratio", "l10_net_vol",
        "result", "exit_price", "exit_time", "pnl_pct", "pnl_pct_net_fees",
        "hold_hours", "max_drawdown_pct", "total_score",
    ]
    placeholders = ", ".join("?" * len(cols))
    col_names = ", ".join(cols)
    rows = []
    for t in trades:
        row = [run_id] + [t.get(c) for c in cols[1:]]
        rows.append(row)
    with _conn() as con:
        con.executemany(
            f"INSERT INTO backtest_trades ({col_names}) VALUES ({placeholders})",
            rows,
        )
    logger.info("Saved %d trades for run_id=%d", len(trades), run_id)


# ── Read helpers ──────────────────────────────────────────────────────────────

def get_trades(symbol: str, days: Optional[int] = None) -> list[dict]:
    """
    Load trade records for a symbol.
    Optionally filter to last N days by entry_time.
    Returns list of dicts.
    """
    with _conn() as con:
        if days:
            rows = con.execute(
                """SELECT * FROM backtest_trades
                   WHERE symbol = ?
                     AND entry_time >= datetime('now', ?)
                   ORDER BY entry_time""",
                (symbol, f"-{days} days"),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT * FROM backtest_trades WHERE symbol = ? ORDER BY entry_time",
                (symbol,),
            ).fetchall()
        return [dict(r) for r in rows]


def get_runs(symbol: str, limit: int = 10) -> list[dict]:
    """Load last N backtest run summaries for a symbol."""
    with _conn() as con:
        rows = con.execute(
            """SELECT * FROM backtest_runs
               WHERE symbol = ?
               ORDER BY created_at DESC
               LIMIT ?""",
            (symbol, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def get_latest_run(symbol: str, days: int) -> Optional[dict]:
    """Get the most recent run for a symbol+days combo."""
    with _conn() as con:
        row = con.execute(
            """SELECT * FROM backtest_runs
               WHERE symbol = ? AND days = ?
               ORDER BY created_at DESC
               LIMIT 1""",
            (symbol, days),
        ).fetchone()
        return dict(row) if row else None


# ── Fear & Greed history cache in Redis ──────────────────────────────────────

def cache_fear_greed_history(history: dict):
    """
    Store Fear & Greed index history in Redis.
    history = {"2026-01-01": 45, "2026-01-02": 52, ...}
    """
    cache_set("fg:history", history, ttl=TTL_FG)


def get_fear_greed_for_date(date_str: str) -> Optional[int]:
    """
    Look up F&G value for a specific date (YYYY-MM-DD).
    Returns None if not in cache.
    """
    history = cache_get("fg:history")
    if history:
        return history.get(date_str)
    return None


# ── Funding rate history cache in Redis ──────────────────────────────────────

def cache_funding_history(symbol: str, history: list[dict]):
    """
    Store funding rate history list in Redis.
    Each item: {"timestamp": "2026-01-01T00:00:00", "rate": 0.012}
    """
    cache_set(f"funding:{symbol}:history", history, ttl=TTL_FUNDING)


def get_funding_for_timestamp(symbol: str, ts_ms: int) -> Optional[float]:
    """
    Find the closest funding rate for a given timestamp (ms).
    Returns rate % or None.
    """
    history = cache_get(f"funding:{symbol}:history")
    if not history:
        return None
    # Funding is every 8h — find closest entry
    target = ts_ms / 1000
    closest = min(history, key=lambda x: abs(
        datetime.fromisoformat(x["timestamp"]).replace(
            tzinfo=timezone.utc).timestamp() - target
    ), default=None)
    return closest["rate"] if closest else None


# ── Position CRUD ─────────────────────────────────────────────────────────────

def open_pos(data: dict) -> int:
    """Insert a new open position. Returns new row id."""
    cols = [
        "symbol", "mode", "entry_time", "entry_price",
        "qty", "budget", "sl_price", "tp_price", "total_score",
    ]
    present = {k: data[k] for k in cols if k in data}
    placeholders = ", ".join("?" * len(present))
    col_names = ", ".join(present.keys())
    with _conn() as con:
        cur = con.execute(
            f"INSERT INTO positions ({col_names}) VALUES ({placeholders})",
            list(present.values()),
        )
        return cur.lastrowid


def get_open_pos() -> Optional[dict]:
    """Return the single open position, or None."""
    with _conn() as con:
        row = con.execute(
            "SELECT * FROM positions WHERE status = 'open' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None


def close_pos(pos_id: int, exit_price: float, exit_time: str,
              reason: str, pnl_pct: float):
    """Mark position as closed."""
    with _conn() as con:
        con.execute(
            """UPDATE positions
               SET status='closed', exit_price=?, exit_time=?,
                   exit_reason=?, pnl_pct=?
               WHERE id=?""",
            (exit_price, exit_time, reason, pnl_pct, pos_id),
        )


def update_pos_sl(pos_id: int, sl_price: float, breakeven_hit: bool = False):
    """Update trailing stop level."""
    with _conn() as con:
        con.execute(
            "UPDATE positions SET sl_price=?, breakeven_hit=? WHERE id=?",
            (sl_price, int(breakeven_hit), pos_id),
        )


def get_closed_positions(limit: int = 20) -> list[dict]:
    """Return last N closed positions, newest first."""
    with _conn() as con:
        rows = con.execute(
            """SELECT * FROM positions WHERE status = 'closed'
               ORDER BY exit_time DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


# ── Paper trades CRUD ─────────────────────────────────────────────────────────

def open_paper_trade(data: dict) -> int:
    """Insert a new OPEN paper trade. Returns new row id."""
    cols = [
        "symbol", "entry_time", "entry_price",
        "tp_pct", "sl_pct", "tp_price", "sl_price",
        "total_score", "layer_snapshot",
    ]
    present = {k: data[k] for k in cols if k in data}
    placeholders = ", ".join("?" * len(present))
    col_names = ", ".join(present.keys())
    with _conn() as con:
        cur = con.execute(
            f"INSERT INTO paper_trades ({col_names}) VALUES ({placeholders})",
            list(present.values()),
        )
        return cur.lastrowid


def get_open_paper_trades() -> list[dict]:
    """Return all OPEN paper trades across symbols."""
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM paper_trades WHERE status = 'OPEN' ORDER BY entry_time"
        ).fetchall()
        return [dict(r) for r in rows]


def has_open_paper_trade(symbol: str) -> bool:
    """Check if there's an OPEN paper trade for this symbol."""
    with _conn() as con:
        row = con.execute(
            "SELECT 1 FROM paper_trades WHERE symbol = ? AND status = 'OPEN' LIMIT 1",
            (symbol,),
        ).fetchone()
        return row is not None


def close_paper_trade(trade_id: int, status: str, exit_price: float,
                      exit_time: str, pnl_pct: float, pnl_pct_net_fees: float,
                      hold_hours: int):
    """Mark paper trade as closed with outcome."""
    with _conn() as con:
        con.execute(
            """UPDATE paper_trades
               SET status=?, exit_price=?, exit_time=?, pnl_pct=?,
                   pnl_pct_net_fees=?, hold_hours=?
               WHERE id=?""",
            (status, exit_price, exit_time, pnl_pct, pnl_pct_net_fees,
             hold_hours, trade_id),
        )


def mark_paper_notified(trade_id: int, kind: str):
    """Mark notification as sent. kind = 'open' or 'close'."""
    col = f"notified_{kind}"
    with _conn() as con:
        con.execute(
            f"UPDATE paper_trades SET {col}=1 WHERE id=?",
            (trade_id,),
        )


def get_paper_trades(symbol: str | None = None,
                     status: str | None = None,
                     limit: int = 500) -> list[dict]:
    """Load paper trades with optional symbol/status filters."""
    sql = "SELECT * FROM paper_trades WHERE 1=1"
    params: list = []
    if symbol:
        sql += " AND symbol = ?"
        params.append(symbol)
    if status:
        sql += " AND status = ?"
        params.append(status)
    sql += " ORDER BY entry_time DESC LIMIT ?"
    params.append(limit)
    with _conn() as con:
        rows = con.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
