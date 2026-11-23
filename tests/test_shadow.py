"""Plan S shadow: measures only, never changes what the cycle trades.

No network: candles are synthetic, funding is stubbed, the broker is the PAPER
one on a JsonStore in a temp dir, and real order calls are booby-trapped.

    python tests/test_shadow.py      (standalone)
"""

from __future__ import annotations

import json
import math
import random
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backtest.portfolio import regime_series, utc_daily  # noqa: E402
from src import binance_trade, shadow, store as store_mod  # noqa: E402
from src.binance_api import Candle  # noqa: E402
from src.grid_broker import GridBroker  # noqa: E402
from src.regime import Regime  # noqa: E402
import main as runner  # noqa: E402

H4 = timedelta(hours=4)
T0 = datetime(2025, 1, 1, tzinfo=timezone.utc)


def candles(n=900, seed=1):
    """4h candles, UTC-aligned, with up/down/flat stretches so all regimes occur."""
    rnd = random.Random(seed)
    out, p = [], 100.0
    for i in range(n):
        drift = 0.004 * math.sin(i / 120)                 # slow trend cycles
        p *= math.exp(drift + rnd.gauss(0, 0.01))
        t = T0 + i * H4
        out.append(Candle(open_time=t, open=p, high=p * 1.005, low=p * 0.995, close=p,
                          volume=1.0, close_time=t + H4 - timedelta(milliseconds=1)))
    return out


def _forming(last: Candle) -> Candle:
    t = last.open_time + H4
    return Candle(open_time=t, open=last.close, high=last.close, low=last.close,
                  close=last.close * 1.03, volume=0.0, close_time=t + H4 - timedelta(milliseconds=1))


def test_daily_regime_matches_simulator_utc_daily() -> None:
    cs = candles()
    sim = utc_daily(regime_series([c.close for c in cs]), cs)
    seen, compared = set(), 0
    for k in range(400, len(cs)):
        live = cs[k - 389: k + 1] + [_forming(cs[k])]    # what a live cycle fetches (390)
        now = cs[k].close_time + timedelta(seconds=30)
        d = shadow.daily_regime(live, now)
        assert d is not None, k
        assert d.value == sim[k], (k, d, sim[k])
        seen.add(d.value); compared += 1
    assert seen == {"BULL", "BEAR", "NEUTRAL"}, seen     # the test really exercised all three
    assert compared > 400


def test_forming_candle_is_ignored() -> None:
    cs = candles()[:500]
    now = cs[-1].close_time + timedelta(seconds=30)
    a = shadow.daily_regime(cs, now)
    b = shadow.daily_regime(cs + [_forming(cs[-1])], now)
    assert a == b


def test_not_enough_history_is_none() -> None:
    cs = candles()[:200]
    assert shadow.daily_regime(cs, cs[-1].close_time) is None


def _rows(f):
    cs = candles()[:500]
    return shadow.rows(["BTCUSDC"], {"BTCUSDC": cs}, {"BTCUSDC": Regime.BULL},
                       {"BTCUSDC": "HOLD-BUY"}, {"BTCUSDC": f}, cs[-1].close_time)[0]


def test_gates_thresholds() -> None:
    r = _rows(0.0002); assert r["hold_gate"] and r["bag_gate"]
    r = _rows(0.00012); assert not r["hold_gate"] and r["bag_gate"]
    r = _rows(0.00005); assert not r["hold_gate"] and not r["bag_gate"]
    r = _rows(None); assert r["hold_gate"] is None and r["bag_gate"] is None


def test_report_is_quiet_unless_an_idea_disagrees() -> None:
    agree = [dict(symbol="BTCUSDC", live_regime="BEAR", daily_regime="BEAR",
                  funding=0.00001, hold_gate=False, bag_gate=False, action=None)]
    con, tg = shadow.report(agree, "USDC")
    assert tg == [] and "BTC:B" in con[0]
    differ = [dict(agree[0], daily_regime="NEUTRAL"),
              dict(symbol="ETHUSDC", live_regime="BULL", daily_regime="BULL", funding=0.0002,
                   hold_gate=True, bag_gate=True, action="HOLD-BUY"),
              dict(symbol="BNBUSDC", live_regime="NEUTRAL", daily_regime="NEUTRAL",
                   funding=0.00012, hold_gate=False, bag_gate=True, action="BUY")]
    _, tg = shadow.report(differ, "USDC")
    text = "\n".join(tg)
    assert "BTC: по дневной свече NEUTRAL" in text
    assert "ETH: funding" in text and "холд" in text
    assert "BNB: funding" in text and "мешков" in text


def test_run_never_raises() -> None:
    class BadStore:
        def log_shadow(self, rows):
            raise RuntimeError("db down")
    old = shadow.funding_now
    try:
        shadow.funding_now = lambda *a: (_ for _ in ()).throw(RuntimeError("fapi down"))
        con, tg = shadow.run(BadStore(), ["BTCUSDC"], {"BTCUSDC": candles()[:500]},
                             {"BTCUSDC": Regime.BEAR}, {}, "USDC")
        assert "error" in con[0] and tg == []
        shadow.funding_now = lambda *a: {"BTCUSDC": 0.0}
        con, tg = shadow.run(BadStore(), ["BTCUSDC"], {"BTCUSDC": candles()[:500]},
                             {"BTCUSDC": Regime.BEAR}, {}, "USDC")
        assert con[0].startswith("shadow (S1")          # logging failed, report still made
    finally:
        shadow.funding_now = old


def _cycle(state: Path, shadow_run, k: int) -> tuple[list, dict]:
    """One paper run_account on synthetic data; returns (trades, final state)."""
    coins = ["BTCUSDC", "ETHUSDC"]
    data = {c: candles(seed=i + 3) for i, c in enumerate(coins)}
    store_mod.STATE_DIR = state
    saved = dict(get_candles=runner.get_candles, get_universe=runner.get_universe,
                 get_grid_broker=runner.get_grid_broker, get_prices=runner.get_prices,
                 shadow_run=runner.shadow.run)
    try:
        runner.get_candles = lambda symbol, interval, limit: (
            data[symbol][k - limit + 1: k + 1] + [_forming(data[symbol][k])])
        runner.get_universe = lambda: coins
        runner.get_grid_broker = lambda account: GridBroker(account)      # PAPER only
        runner.get_prices = lambda cs: {c: data[c][k].close for c in cs}
        runner.shadow.run = shadow_run
        runner.run_account(runner.config.accounts[0])
    finally:
        runner.get_candles, runner.get_universe = saved["get_candles"], saved["get_universe"]
        runner.get_grid_broker, runner.get_prices = saved["get_grid_broker"], saved["get_prices"]
        runner.shadow.run = saved["shadow_run"]
    trades = [json.loads(l) for l in (state / "trades.jsonl").read_text().splitlines()] \
        if (state / "trades.jsonl").exists() else []
    st = json.loads((state / "grid_state.json").read_text())
    return trades, st


def test_shadow_does_not_change_trading() -> None:
    def boom(*a, **k):
        raise SystemExit("REAL ORDER ATTEMPTED IN A TEST")
    old_buy, old_sell = binance_trade.market_buy, binance_trade.market_sell
    old_dir, old_fund = store_mod.STATE_DIR, shadow.funding_now
    binance_trade.market_buy = binance_trade.market_sell = boom
    shadow.funding_now = lambda coins, q: {c: 0.0002 for c in coins}
    try:
        traded = 0
        for k in range(420, 880, 23):                 # bars covering all regimes
            with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
                with_shadow = _cycle(Path(a), shadow.run, k)
                without = _cycle(Path(b), lambda *x, **y: ([], []), k)
                assert (Path(a) / "shadow.jsonl").exists()            # shadow did run
                for tr in with_shadow[0] + without[0]:
                    tr.pop("time", None)
                for st in (with_shadow[1], without[1]):
                    for bags in st["positions"].values():
                        for bag in bags:
                            bag.pop("time", None)
                    for h in st["holds"].values():
                        h.pop("time", None)
                assert with_shadow == without, k
                traded += bool(with_shadow[0])
        assert traded >= 3, f"only {traded} bars traded — the test proves too little"
    finally:
        binance_trade.market_buy, binance_trade.market_sell = old_buy, old_sell
        store_mod.STATE_DIR, shadow.funding_now = old_dir, old_fund


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for t in tests:
        t()
    print(f"ok — {len(tests)} tests")
