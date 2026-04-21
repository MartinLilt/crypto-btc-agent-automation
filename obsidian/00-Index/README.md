# Crypto BTC Agent — Knowledge Base

## Vault Structure
```
obsidian/
  00-Index/       — this file + quick links
  01-Architecture/ — system design, data flows
  02-DevLog/      — change log per session
  03-Roadmap/     — features, bugs, ideas
```

## Quick Links
- [[Architecture Overview]]
- [[Signal Layers Deep Dive]]
- [[Backtest Engine]]
- [[Dev Log]]
- [[Roadmap & Ideas]]

## Project Status
**Last updated:** 2026-04-22
**Phase:** Signal analysis complete (10 layers), backtest working, patterns working.
**Not yet built:** Real trade execution (Phase 2-4 of roadmap).

## Key Numbers to Remember
| Metric | Value |
|--------|-------|
| Signal layers | 10 (all must pass) |
| Supported assets | 5 (BTC, ETH, LTC, SOL, LINK) |
| Backtest window | 220 candles |
| Hold timeout | 48h |
| Default TP | 2% |
| Default SL | 1% |
| Binance fee | 0.1% per side |
| Min RR ratio | 1.5 |