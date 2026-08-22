"""Regression tests for the Binance -1100 class of bugs.

Binance rejects any order parameter whose value doesn't match its numeric regex
    ^([0-9]{1,20})(\\.[0-9]{1,20})?$
A Python float can violate this by stringifying to scientific notation — e.g.
`str(0.00009) == '9e-05'`, and the 'e' is an "illegal character". That silently
blocked every small-qty market-sell (a few-dollar BTC bag at ~$65k → 0.00009).

These tests pin the formatting so a small number can never reach the API as a
float again. No network: we seed the LOT_SIZE cache directly.

Run standalone (no pytest needed):
    python tests/test_order_formatting.py
or, if pytest is installed:
    pytest tests/test_order_formatting.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import binance_trade as bt  # noqa: E402
from src.config import config as CFG, Account  # noqa: E402
from src.store import JsonStore  # noqa: E402
import main as runner  # noqa: E402

# Binance's exact accepted numeric format for order parameters.
BINANCE_NUM = re.compile(r"^([0-9]{1,20})(\.[0-9]{1,20})?$")

# Real LOT_SIZE stepSizes seen across the live basket + a very fine one.
LOT_STEPS = {
    "BTCUSDC": "0.00001000",   # 5 dp — the one that produced '9e-05'
    "ETHUSDC": "0.00010000",   # 4 dp
    "BNBUSDC": "0.00100000",   # 3 dp
    "SOLUSDC": "0.01000000",   # 2 dp
    "XRPUSDC": "1.00000000",   # 0 dp (integer qty)
    "FINEUSDC": "0.00000001",  # 8 dp — finest, stresses tiny values hardest
}

# Quantities that specifically trigger scientific notation as bare floats.
TINY_QTYS = [9e-05, 8.984e-05, 1e-05, 5e-06, 1.23e-07, 3e-08]
NORMAL_QTYS = [0.0, 0.01, 0.5, 1.0, 7.0, 0.010012, 123.456789, 1000.0]


def _seed_cache() -> None:
    bt._lot_cache.update(LOT_STEPS)


def test_qty_str_is_always_plain_decimal() -> None:
    """qty_str output matches Binance's regex and never uses sci-notation."""
    _seed_cache()
    for symbol in LOT_STEPS:
        for q in TINY_QTYS + NORMAL_QTYS:
            s = bt.qty_str(symbol, q)
            assert "e" not in s and "E" not in s, f"{symbol} {q!r} -> {s!r} has exponent"
            assert BINANCE_NUM.match(s), f"{symbol} {q!r} -> {s!r} fails Binance regex"


def test_qty_str_rounds_down_to_step() -> None:
    """The string is the LOT_SIZE floor of the qty (never rounds up over balance)."""
    _seed_cache()
    assert bt.qty_str("BTCUSDC", 0.00009303) == "0.00009"   # was '9e-05' before the fix
    assert bt.qty_str("BTCUSDC", 0.00008984) == "0.00008"
    assert bt.qty_str("BNBUSDC", 0.010012) == "0.010"
    assert bt.qty_str("XRPUSDC", 7.9) == "7"                # 0 dp, floor -> integer string


def test_the_exact_pre_fix_regression() -> None:
    """The concrete value that took down live BTC TP-sells for a day."""
    _seed_cache()
    q = 0.00009
    assert str(q) == "9e-05"                # the raw float really is sci-notation
    assert bt.qty_str("BTCUSDC", q) == "0.00009"   # and we now send a legal string
    assert BINANCE_NUM.match(bt.qty_str("BTCUSDC", q))


def test_buy_quote_is_plain_decimal() -> None:
    """market_buy sends quoteOrderQty as a 2dp string, never a sci-notation float."""
    for amount in [5.0, 6.0, 10.5, 22.37, 0.01, 1234.5]:
        s = f"{amount:.2f}"
        assert "e" not in s and BINANCE_NUM.match(s), f"{amount!r} -> {s!r}"


def _stub_signed(calls):
    """Fake bt._signed that records calls and returns plausible payloads."""
    def fake(method, path, params):
        calls.append((method, path))
        if path.endswith("/account"):
            return {"balances": [{"asset": "USDC", "free": "10.0"},
                                 {"asset": "BTC", "free": "0.001"}]}
        if path.endswith("/order"):
            return {"executedQty": "0.001", "cummulativeQuoteQty": "60.0"}
        return {}
    return fake


def test_balance_cache_reads_account_once() -> None:
    """Repeated balance reads within a cycle hit /account only once."""
    bt._bal_cache = None
    calls = []
    orig, bt._signed = bt._signed, _stub_signed(calls)
    try:
        bt.get_free_balances()
        bt.get_free_balances()
        bt.free_quote()
        acct = [c for c in calls if c[1].endswith("/account")]
        assert len(acct) == 1, f"expected 1 /account call, got {len(acct)}"
    finally:
        bt._signed, bt._bal_cache = orig, None


def test_order_invalidates_balance_cache() -> None:
    """A fill drops the cache so the next read reflects post-trade balances."""
    bt._bal_cache = None
    bt._lot_cache["BTCUSDC"] = "0.00001000"   # avoid a network exchangeInfo call
    calls = []
    orig, bt._signed = bt._signed, _stub_signed(calls)
    try:
        bt.get_free_balances()                # 1st /account
        bt.market_sell("BTCUSDC", 0.001)      # fills -> invalidates cache
        bt.get_free_balances()                # 2nd /account (fresh, post-trade)
        acct = [c for c in calls if c[1].endswith("/account")]
        assert len(acct) == 2, f"expected 2 /account calls after a fill, got {len(acct)}"
        bt.get_free_balances(force=True)       # force bypasses the cache
        acct = [c for c in calls if c[1].endswith("/account")]
        assert len(acct) == 3, f"force=True should refetch, got {len(acct)}"
    finally:
        bt._signed, bt._bal_cache = orig, None


# ── NOTIONAL / lot-step round-trip (the 2026-08-22 -1013 on BTCUSDC) ─────

def _seed_notional() -> None:
    bt._notional_cache.update({sym: 5.0 for sym in LOT_STEPS})


def test_round_qty_keeps_a_qty_that_sits_exactly_on_the_step() -> None:
    """A qty already ON the LOT_SIZE step must survive rounding intact.

    The old float maths (`int(qty * 10**places)`) dropped a WHOLE step here:
    0.00007 * 100000 == 6.999999999999999 -> 6 -> 0.00006. On BTC that is
    ~$0.78, which pushed a $5.48 TP-sell under the $5 NOTIONAL floor."""
    _seed_cache()
    assert bt.round_qty("BTCUSDC", 0.00007) == 0.00007
    assert bt.qty_str("BTCUSDC", 0.00007) == "0.00007"
    assert bt.qty_str("ETHUSDC", 0.0024) == "0.0024"      # was '0.0023' (-$0.25)
    assert bt.qty_str("BNBUSDC", 0.008) == "0.008"
    # still a floor, never a round-up over the real balance
    assert bt.qty_str("BTCUSDC", 0.000069947) == "0.00006"


def test_sell_below_min_notional_is_refused_before_the_api() -> None:
    """A dust bag fails locally with a readable message — no -1013 round-trip."""
    _seed_cache(); _seed_notional()
    calls = []
    orig, bt._signed = bt._signed, _stub_signed(calls)
    try:
        try:
            bt.market_sell("BTCUSDC", 0.000069947, 78444.44)   # -> 0.00006 = $4.71
        except bt.TradeError as exc:
            assert "minNotional" in str(exc), exc
        else:
            raise AssertionError("expected TradeError for a sub-$5 sell")
        assert not [c for c in calls if c[1].endswith("/order")], "must not hit the API"
        # the same qty at a price that clears $5 goes through
        bt.market_sell("BTCUSDC", 0.000069947, 90000.0)        # 0.00006 = $5.40
        assert [c for c in calls if c[1].endswith("/order")], "legal sell must be sent"
    finally:
        bt._signed = orig
        bt._invalidate_balances()


def test_min_round_trip_unit_covers_two_lot_steps() -> None:
    """Bag floor = NOTIONAL + the step lost on the buy + the one lost on the sell."""
    _seed_cache(); _seed_notional()
    btc = bt.min_round_trip_unit("BTCUSDC", 78_265.0)
    assert btc > 6.5, btc          # the $6 unit that broke was NOT enough
    assert btc < 7.5, btc
    # a $6 bag stays fine where the step is small relative to the bag
    assert bt.min_round_trip_unit("ETHUSDC", 2_511.0) < 6.0     # step ~$0.25
    assert bt.min_round_trip_unit("BNBUSDC", 715.0) < 6.5       # step ~$0.72


# ── multi-account + whitelist ─────────────────────────────────────────────

def test_account_active_flag() -> None:
    """An account is active only with BOTH key and secret (else it's skipped)."""
    assert Account(1, "A", "a", "key", "sec").active
    assert not Account(2, "B", "b", "", "").active
    assert not Account(2, "B", "b", "key", "").active


def test_store_slots_are_isolated() -> None:
    """Each account slot maps to its own JSON files — no shared ledger."""
    assert JsonStore(1).file.name == "grid_state.json"     # slot 1 keeps legacy names
    assert JsonStore(1).trades.name == "trades.jsonl"
    assert JsonStore(2).file.name == "grid_state_2.json"
    assert JsonStore(2).trades.name == "trades_2.jsonl"


def test_credentials_switch_clears_cache() -> None:
    """Switching accounts repoints signing keys AND drops the balance cache."""
    bt._bal_cache = {"USDC": 1.0}
    try:
        bt.set_credentials("KEY2", "SEC2")
        assert bt._key_secret() == ("KEY2", "SEC2")
        assert bt._bal_cache is None, "cache must clear so balances don't leak across accounts"
    finally:
        bt._creds = None
        bt._bal_cache = None


class _FakeSubs:
    def __init__(self) -> None:
        self.o = 0
        self.added: list[tuple[int, str]] = []
    def offset(self) -> int: return self.o
    def set_offset(self, o: int) -> None: self.o = o
    def add(self, chat_id: int, username: str) -> None: self.added.append((chat_id, username))
    def all(self): return list(self.added)


def test_whitelist_gates_subscription() -> None:
    """/start enrollment: only whitelisted @usernames are registered; a stranger
    is refused (and never becomes a report recipient). Offset advances."""
    fake = _FakeSubs()
    sent: list[tuple] = []
    updates = [
        {"update_id": 10, "message": {"chat": {"id": 111}, "from": {"username": "Limi_AMM"}}},
        {"update_id": 11, "message": {"chat": {"id": 222}, "from": {"username": "stranger"}}},
    ]
    orig = (runner.get_subscribers, runner.notify.get_updates, runner.notify.send,
            CFG.telegram_whitelist, CFG.telegram_bot_token)
    runner.get_subscribers = lambda: fake
    runner.notify.get_updates = lambda offset: updates
    runner.notify.send = lambda text, chat_id=None: sent.append((chat_id, text)) or True
    object.__setattr__(CFG, "telegram_whitelist", ("limi_amm", "aleksandli"))
    object.__setattr__(CFG, "telegram_bot_token", "TESTTOKEN")
    try:
        runner._process_subscriptions()
        assert (111, "limi_amm") in fake.added, "whitelisted user must be registered"
        assert all(c != 222 for c, _ in fake.added), "stranger must NOT be registered"
        assert any(cid == 222 for cid, _ in sent), "stranger must get a refusal reply"
        assert fake.o == 12, "offset must advance past the last update_id"
    finally:
        (runner.get_subscribers, runner.notify.get_updates, runner.notify.send,
         wl, tok) = (*orig,)
        object.__setattr__(CFG, "telegram_whitelist", wl)
        object.__setattr__(CFG, "telegram_bot_token", tok)


def _run_standalone() -> int:
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"FAIL {t.__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run_standalone())