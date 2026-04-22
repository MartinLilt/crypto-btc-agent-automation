# Tax & Fees Model

→ See also: [[Backtest Engine]] | [[Research Feature]]

---

## Binance Fees

```python
BINANCE_FEE_PCT = 0.1   # 0.1% per side → 0.2% round-trip
```

Applied in `_simulate_trade()`:
```python
pnl_pct_net_fees = pnl_pct - 0.2   # for TP/SL exits
# TIMEOUT exits: pnl_pct_net_fees = raw_pnl - 0.2
```

Break-even WR (after fees only, before tax):
```python
breakeven_wr_fees = (sl_pct + 0.2) / (tp_pct + sl_pct) * 100
# Example: TP 2% / SL 1%  →  (1.0 + 0.2) / (2.0 + 1.0) × 100 = 40.0%
```

---

## Lithuanian Capital Gains Tax (GPM 15%)

```python
LT_TAX_RATE = 0.15   # 15%
```

**Applied on net annual profit only** — losses offset gains:
```python
total_pnl_net_fees = sum(t["pnl_pct_net_fees"] for t in trades)
lt_tax_pct = max(0, total_pnl_net_fees) * LT_TAX_RATE
total_pnl_after_tax_pct = total_pnl_net_fees - lt_tax_pct
```

**Important:** Tax is NOT applied per-trade. A losing trade reduces the taxable base for the period.

---

## Example: 19 trades, TP 2% / SL 1%, 365d

| Metric | Value |
|--------|-------|
| Win rate | 47.4% (9 wins / 10 losses) |
| Gross PnL | ~+10% |
| After 0.2% fees × 19 trades | ~+6% |
| Lithuanian GPM (15% on net) | ~-0.9% |
| **Net after tax** | **~+5.1%** |
| On $500 budget | **+$25.5/year** |

---

## Stats Dict Keys

```python
{
    "total_pnl_pct":             float,   # gross, no fees
    "total_pnl_net_fees_pct":   float,   # after 0.2% round-trip
    "lt_tax_pct":                float,   # tax amount in %
    "total_pnl_after_tax_pct":  float,   # final net
    "breakeven_wr_fees":         float,   # min WR% to profit after fees
}
```