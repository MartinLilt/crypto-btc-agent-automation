"""Performance ledger: result vs deposits and the buy-&-hold benchmark.

No network: prices are passed in or monkeypatched, the store is a JsonStore in
a temp dir.

    python tests/test_performance.py      (standalone)
    pytest tests/test_performance.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import store as store_mod  # noqa: E402
from src.performance import (Performance, apply_flow, bench_value,  # noqa: E402
                             report_lines)
from src.store import JsonStore  # noqa: E402
import main as runner  # noqa: E402

COINS = ["BTCUSDC", "ETHUSDC"]
P1 = {"BTCUSDC": 100.0, "ETHUSDC": 10.0}
P2 = {"BTCUSDC": 50.0, "ETHUSDC": 20.0}


def close(a, b, tol=1e-9):
    return abs(a - b) <= tol


# ── pure math ────────────────────────────────────────────────────────────
def test_deposit_splits_equally() -> None:
    u = apply_flow({}, 100.0, P1, COINS)
    assert close(u["BTCUSDC"], 0.5) and close(u["ETHUSDC"], 5.0)
    assert close(bench_value(u, P1), 100.0)
    # BTC halves, ETH doubles -> 25 + 100
    assert close(bench_value(u, P2), 125.0)


def test_withdrawal_scales_every_coin() -> None:
    u = apply_flow({}, 100.0, P1, COINS)
    u = apply_flow(u, -25.0, P1, COINS)
    assert close(bench_value(u, P1), 75.0)
    assert close(u["BTCUSDC"] / u["ETHUSDC"], 0.1)     # weights unchanged


def test_withdrawal_larger_than_bench_empties_not_negative() -> None:
    u = apply_flow({}, 100.0, P1, COINS)
    u = apply_flow(u, -500.0, P1, COINS)
    assert all(q == 0.0 for q in u.values())
    assert bench_value(u, P1) == 0.0


def test_zero_flow_is_a_noop() -> None:
    u = apply_flow({}, 100.0, P1, COINS)
    assert apply_flow(u, 0.0, P2, COINS) == u


def test_deposit_skips_coins_without_price() -> None:
    u = apply_flow({}, 100.0, {"BTCUSDC": 100.0}, COINS)
    assert close(u["BTCUSDC"], 1.0) and "ETHUSDC" not in u


def test_deposit_with_no_prices_raises() -> None:
    try:
        apply_flow({}, 100.0, {}, COINS)
    except ValueError:
        return
    raise AssertionError("expected ValueError")


def test_missing_price_gives_none_not_a_wrong_number() -> None:
    u = apply_flow({}, 100.0, P1, COINS)
    assert bench_value(u, {"BTCUSDC": 100.0}) is None


def test_several_flows_path_dependent() -> None:
    u = apply_flow({}, 100.0, P1, COINS)          # 0.5 BTC, 5 ETH
    u = apply_flow(u, 50.0, P2, COINS)            # +0.5 BTC, +1.25 ETH
    assert close(u["BTCUSDC"], 1.0) and close(u["ETHUSDC"], 6.25)
    assert close(bench_value(u, P2), 50.0 + 125.0)


def test_performance_numbers_match_the_audit() -> None:
    # 2026-11-22 audit: deposits 126.72, equity 118.35, hold-the-basket 102.33
    p = Performance(126.72, 118.35, 102.33)
    assert close(p.result, -8.37, 1e-6)
    assert close(p.result_pct, -6.605, 1e-3)
    assert close(p.bench_pct, -19.247, 1e-3)
    assert close(p.edge, 16.02, 1e-6)
    lines = report_lines(p, ["BTCUSDC", "ETHUSDC", "BNBUSDC", "SOLUSDC"], "USDC")
    assert "126.72" in lines[0] and "-8.37" in lines[0] and "-6.6%" in lines[0]
    assert "BTC/ETH/BNB/SOL" in lines[1] and "лучше" in lines[1] and "16.02" in lines[1]


def test_report_says_worse_when_bot_lags() -> None:
    lines = report_lines(Performance(100, 90, 95), COINS, "USDC")
    assert "хуже на <b>5.00</b>" in lines[1]


def test_report_without_bench_says_so() -> None:
    lines = report_lines(Performance(100, 90, None), COINS, "USDC")
    assert "нет цен" in lines[1]


# ── store ────────────────────────────────────────────────────────────────
def _tmp_state(monkey_dir: Path):
    store_mod.STATE_DIR = monkey_dir


def test_json_store_roundtrip_and_accounts_isolated() -> None:
    old = store_mod.STATE_DIR
    with tempfile.TemporaryDirectory() as d:
        _tmp_state(Path(d))
        try:
            s1, s2 = JsonStore(1), JsonStore(2)
            assert s1.load_perf() is None
            s1.record_flow(100.0, "seed", "t", {"BTCUSDC": 1.0})
            s1.record_flow(-10.0, "auto", "w", {"BTCUSDC": 0.9})
            st = s1.load_perf()
            assert close(st["net_deposits"], 90.0) and st["n_flows"] == 2
            assert st["bench"] == {"BTCUSDC": 0.9}
            assert s2.load_perf() is None                 # slot 2 untouched
        finally:
            store_mod.STATE_DIR = old


def test_record_flow_commits_cash() -> None:
    old = store_mod.STATE_DIR
    with tempfile.TemporaryDirectory() as d:
        _tmp_state(Path(d))
        try:
            s = JsonStore(1)
            s.save(10.0, 1.5, {"BTCUSDC": []}, {})
            s.record_flow(50.0, "auto", "deposit", {}, cash=60.0)
            st = s.load()
            assert st["cash"] == 60.0 and st["realized"] == 1.5
        finally:
            store_mod.STATE_DIR = old


# ── runner wiring ────────────────────────────────────────────────────────
class FakeBroker:
    def __init__(self, store, cash, flow, equity):
        self.store, self.cash, self.external_flow = store, cash, flow
        self.capital_base, self._equity = 100.0, equity

    def equity(self, prices):
        return self._equity


def _with_runner(fn):
    old_dir, old_prices = store_mod.STATE_DIR, runner.get_prices
    runner.get_prices = lambda coins: dict(P1)
    with tempfile.TemporaryDirectory() as d:
        _tmp_state(Path(d))
        try:
            return fn()
        finally:
            store_mod.STATE_DIR, runner.get_prices = old_dir, old_prices


def test_first_cycle_seeds_with_equity_and_ignores_bogus_flow() -> None:
    def run():
        s = JsonStore(1)
        errs: list[str] = []
        # stored cash was the paper default -> a huge fake "withdrawal"
        st = runner._sync_performance(FakeBroker(s, 20.0, -2480.0, 120.0), COINS, errs)
        assert not errs
        assert st["n_flows"] == 1 and close(st["net_deposits"], 120.0)
        assert close(bench_value(st["bench"], P1), 120.0)
    _with_runner(run)


def test_deposit_is_booked_once_even_if_cycle_dies_after() -> None:
    def run():
        s = JsonStore(1)
        s.save(10.0, 0.0, {}, {})
        s.record_flow(100.0, "seed", "t", apply_flow({}, 100.0, P1, COINS))
        errs: list[str] = []
        st = runner._sync_performance(FakeBroker(s, 60.0, 50.0, 150.0), COINS, errs)
        assert not errs and st["n_flows"] == 2 and close(st["net_deposits"], 150.0)
        # the cycle "crashes" here; next cycle re-reads stored cash = 60 -> flow 0
        assert s.load()["cash"] == 60.0
        st2 = runner._sync_performance(FakeBroker(s, 60.0, 60.0 - s.load()["cash"], 150.0),
                                       COINS, errs)
        assert st2["n_flows"] == 2
    _with_runner(run)


def test_noise_below_threshold_is_not_a_flow() -> None:
    def run():
        s = JsonStore(1)
        s.record_flow(100.0, "seed", "t", {})
        st = runner._sync_performance(FakeBroker(s, 10.0, 0.3, 100.0), COINS, [])
        assert st["n_flows"] == 1
    _with_runner(run)


def test_failure_is_reported_not_raised() -> None:
    def run():
        s = JsonStore(1)
        s.record_flow(100.0, "seed", "t", {})
        runner.get_prices = lambda coins: (_ for _ in ()).throw(RuntimeError("down"))
        old_retry = runner._retry                         # retry would sleep; bypass it
        runner._retry = lambda fn, what, **kw: fn()
        try:
            errs: list[str] = []
            st = runner._sync_performance(FakeBroker(s, 60.0, 50.0, 150.0), COINS, errs)
        finally:
            runner._retry = old_retry
        assert st is None and errs and "+50.00" in errs[0]
        assert s.load_perf()["n_flows"] == 1             # nothing half-written
    _with_runner(run)


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for t in tests:
        t()
    print(f"ok — {len(tests)} tests")
