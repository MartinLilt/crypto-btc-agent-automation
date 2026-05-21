# Operating Runbook — Crypto BTC Agent

One page. Read this when something feels broken or before you change anything.

## Where things live

| Component | Location | What it does |
|---|---|---|
| **bot** service | Railway eu-west, `python main.py` | Telegram polling, paper signals via 10-layer config |
| **tracker** cron | Railway eu-west, `python -m research.forward step` every 5 min (`*/5 * * * *`) | Live P&L of hardened 50/50 book (rotation+carry) on `/app/data/forward_state.json`. Idempotent — logs `no new bar since ...` and exits until a new daily Binance bar closes. |
| Repo | `main` on github.com/MartinLilt/crypto-btc-agent-automation | Source of truth |
| Local research | `research/` (cached parquet + scripts) | NOT used by deployed bot |

## Status check (do this WEEKLY, not daily)

```bash
railway status                                # both services overview
railway logs --service bot | tail -30         # bot polling + scanner output
railway logs --service tracker | tail -30     # last cron run
railway run --service tracker -- python -m research.forward report
                                              # tracker equity + today's book
```

Look at the **tracker forward record** — `forward days: N | equity €X | total ±Y%`.
That's the only number that matters. Ignore daily wiggles.

## What to do if something breaks

| Symptom | Fix |
|---|---|
| Bot service `Crashed` | `railway service restart --service bot` |
| `restricted location` in logs | Check service region = eu-west; if drifted: `railway service scale eu-west=1 us-east=0 --service <name>` |
| `ModuleNotFoundError` | New dep in requirements.txt → `railway up --service <name>` to rebuild |
| Tracker showing `no new bar since...` for >2 days | Expected within a UTC day (cron polls every 5 min, only acts on a new daily bar). If >48h: Binance kline endpoint down (rare) or cron misfired — check `railway status` for cron schedule |
| Bot Telegram silent | Check `TELEGRAM_BOT_TOKEN` in Railway vars; check no second instance polling elsewhere |
| Forward state corrupt | Last-known good is `research/forward_state.json` locally. If lost on Railway volume: re-init via `railway run --service tracker -- python -m research.forward init` (track record restarts) |

## What NOT to do during the forward test

- **Do not tune parameters** of the 50/50 book. Every fix-while-running invalidates the experiment.
- **Do not add new strategies** to compare. Search has converged (see `research/RESULTS.md`).
- **Do not check the equity curve more than weekly.** Daily wiggles are noise; checking daily creates emotional pressure to act.
- **Do not deploy real money** until GO_LIVE.md criteria are met. Read GO_LIVE.md before any real-money decision.
- **Do not believe a green week.** Green or red, you need ~60 days minimum for any signal.
- **Do not delete the `research/forward_state.json` on the volume.** It IS the experiment.

## Honest expectations (so you don't panic)

- Daily 1σ wiggle of the book ≈ ±1.6%. Down days are normal.
- Expected ~10% of 1-year sims are negative (5th pctile ≈ −7%).
- Plausible bad drawdown over a year ≈ 20%. Tail (held-alt collapse) up to ~40%.
- The bot doing nothing for weeks at a time is correct — the regime filter sits in cash when conditions are wrong.

If anything feels urgent, walk away for 24h and re-read this page.