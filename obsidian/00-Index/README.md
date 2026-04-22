# Crypto BTC Agent — Knowledge Base

## Vault Structure
```
obsidian/
  00-Index/          — this file + quick links
  01-Architecture/   — system design, data flows, feature details
  02-DevLog/         — change log per session
  03-Roadmap/        — features, bugs, ideas
```

---

## Quick Links

### Core Architecture
- [[Architecture Overview]] — high-level flow, data sources, file map
- [[Signal Layers Deep Dive]] — all 10 layers with scoring details
- [[Backtest Engine]] — simulation model, stats dict, SQLite schema
- [[Filters & Hard Blocks]] — RSI, ADX danger zone, weekly EMA21

### Feature Hubs
- [[Research Feature]] — auto grid search, 12 combos, budget projections
- [[ADX Danger Zone Analysis]] — data-driven discovery from 318 trades
- [[Tax & Fees Model]] — Lithuanian GPM 15%, Binance 0.2% round-trip

### History & Plans
- [[Dev Log]] — chronological change log
- [[Roadmap & Ideas]] — pending features, known issues

---

## Project Status

**Last updated:** 2026-04-22
**Phase:** Signal analysis complete (10 layers), backtest + Research working, patterns working.
**Not yet built:** Real trade execution (Phase 2–4 of roadmap).

---

## Key Numbers

| Metric | Value |
|--------|-------|
| Signal layers | 10 (all must pass + no hard blocks) |
| Entry score threshold | 70 / 100 |
| Supported assets | 5 (BTC, ETH, LTC, SOL, LINK) |
| Backtest warmup | 210 candles (EMA-200 + buffer) |
| Hold timeout | 48h |
| Binance fee | 0.1% per side (0.2% round-trip) |
| Lithuanian tax | 15% GPM on net annual profit |
| Min RR ratio | 1.5 (default TP 2% / SL 1% → RR ≈ 1.8) |
| Research combos | 12 (4 TP/SL × 3 periods) |
| ADX danger zone | 25–40 (WR 5–33% → BLOCKED) |

---

## Bot Flows (quick ref)

```
/start → Main Menu
    📊 Live     → asset → _run_analysis()
    🧮 Simulator → asset → period → budget → TP picker → run
    📈 Patterns  → asset → compute_patterns()
    🧪 Research  → asset → [auto, no input] → 12 combos → best + projections
```