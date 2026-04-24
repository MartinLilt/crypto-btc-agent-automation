# Crypto BTC Agent - Knowledge Base

> Auto-loaded by Claude via `.claudeinclude`. Update when architecture/signals change.

---

## Vault Structure

```
obsidian/
  00-Index/           <- This hub + MOC
  01-Architecture/    <- System design, layers, features
  02-DevLog/          <- Reverse-chrono change log
  03-Roadmap/         <- Features, bugs, ideas
```

---

## Quick Links by Topic

### Signal System
- [[Signal Layers Deep Dive]] - All 10 layers with scoring (0-10 each, threshold 70/100)
- [[Filters & Hard Blocks]] - RSI >65, ADX 25-40, weekly EMA21
- [[ADX Danger Zone Analysis]] - Data-driven discovery from 318 trades

### Core Systems
- [[Architecture Overview]] - High-level flow, data sources, file map
- [[Backtest Engine]] - Simulation model, stats, SQLite schema
- [[Research Feature]] - Auto grid search, 12 combos, projections

### Business Logic
- [[Tax & Fees Model]] - Lithuanian GPM 15%, Binance 0.2% round-trip

### History & Planning
- [[Dev Log]] - Chronological change log (newest first)
- [[Roadmap & Ideas]] - Pending features, known issues

---

## Project Status

| Item | Value |
|------|-------|
| **Last updated** | 2026-04-24 |
| **Phase** | Analysis + backtest complete |
| **Not built** | Real trade execution |
| **Assets** | BTCUSDT only |

---

## Key Numbers

| Metric | Value |
|--------|-------|
| Signal layers | 10 (all scored 0-10) |
| Entry threshold | 70/100 + no hard blocks |
| Hard blocks | RSI >65, ADX 25-40, price < weekly EMA21 |
| Backtest warmup | 210 candles (EMA-200 + buffer) |
| Hold timeout | 48h |
| Default TP/SL | 2% / 1% (RR 2:1) |
| Binance fee | 0.1% per side (0.2% round-trip) |
| Lithuanian tax | 15% GPM on net annual profit |
| Research combos | 12 (4 TP/SL x 3 periods) |
| ADX danger zone | 25-40 (WR 5-33% -> blocked) |

---

## Bot Flows

```
/start -> Main Menu
    Live      -> asset -> _run_analysis()
    Simulator -> asset -> period -> budget -> TP picker -> run
    Patterns  -> asset -> compute_patterns()
    Research  -> asset -> [auto] 12 combos -> best + projections
```

---

## File Map (src/)

| Path | Role |
|------|------|
| `main.py` | Telegram handlers, UI |
| `src/signals/indicators.py` | 10 signal layers |
| `src/signals/pattern_analyzer.py` | ADX/score/hour patterns |
| `src/backtest/engine.py` | Backtest + Research |
| `src/data/binance_client.py` | Binance REST |
| `src/data/news_client.py` | News + sentiment |
| `src/data/db.py` | SQLite ops |
| `src/ai/orchestrator.py` | Ollama LLM review |
| `src/bot/strings.py` | EN/RU i18n |

---

## Update Guidelines

1. **Architecture changes** -> edit `01-Architecture/*.md`
2. **Signal/threshold changes** -> edit `Signal Layers Deep Dive.md`
3. **Session work** -> prepend to `Dev Log.md` (newest first)
4. **Roadmap progress** -> update `Roadmap & Ideas.md`
5. **Use `[[Page Name]]` links** for cross-references