# crypto-btc-agent-automation

Binance spot grid/hold bot (paper|live via `TRADING_MODE`), Telegram reports,
Postgres or JSON state. Runs 24/7 on a DigitalOcean droplet (`deploy/server/`; Hetzner until 2026-10-09).

## Layout

```
.
├── AGENTS.md           # this file (CLAUDE.md is a symlink to it)
├── main.py             # one cycle over the basket; run by a systemd timer
├── requirements.txt    # runtime deps
├── requirements-dev.txt# + pytest
├── pytest.ini          # testpaths = tests
├── src/                # bot code (config, grid, regime, broker, net, store…)
├── backtest/           # research scripts (*_test.py here are NOT unit tests)
├── tests/              # unit tests, no network
├── deploy/server/      # cloud-init, systemd units, watchdog, pg backup
├── state/              # local JSON state (gitignored)
├── .vscode/            # editor config (interpreter, tests, launch)
└── obsidian/vault/     # LOCAL Obsidian vault: daily/, reference/, logs/Dev Log.md
```

## Environment

- Editor: **VS Code** (moved from PyCharm 2026-10-03; `.idea/` is legacy and ignored).
  `.vscode/` pins the `.venv` interpreter, loads `.env`, enables pytest.
- Virtualenv at `.venv/` (Python 3.14):
  ```bash
  source .venv/bin/activate
  pip install -r requirements-dev.txt
  ```
- Tests: `.venv/bin/python -m pytest` (each file also runs standalone).
- Local `.env` holds the REAL Telegram token and Binance keys. To run a cycle
  locally use the launch config "main.py (paper, no Telegram)".
- `.venv/`, `.idea/`, `.env`, `state/`, `__pycache__/` are gitignored.

## Obsidian vault

The project has its **own** local vault at `obsidian/vault/`.

- `daily/` — daily notes, `YYYY-MM-DD.md`.
- `reference/` — durable reference that outlives a session, e.g.
  `Binance API policy.md` (rate limits, ban rules, ToS lines we must not cross).
- `logs/Dev Log.md` — one entry per session / meaningful change.
- `plans/` — multi-step plans with checklists.
- `private/` — **gitignored**. The GitHub repo is PUBLIC, so committed notes use
  placeholders (`<server-ip>`, `<owner-ip>`, `<invoice>`, …); real values live in
  `private/ops.md`. Never write IPs, chat ids, invoice/ticket numbers into committed files.

Add a Dev Log entry whenever the project changes.

## Note on the two vaults

There is also a separate global Obsidian vault on this machine synced over MCP —
it is unrelated to this project. This project only touches its local `obsidian/vault/`.

## Conventions

- Dates `YYYY-MM-DD`, times 24h `HH:MM`.