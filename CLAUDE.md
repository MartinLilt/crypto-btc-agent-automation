# crypto-btc-agent-automation

Base Python project. Environment is set up; no application code yet.

## Layout

```
.
├── CLAUDE.md            # this file
├── main.py             # entrypoint (empty for now)
├── requirements.txt    # deps (none yet)
├── .gitignore
├── .venv/              # local virtualenv (Python 3.14, gitignored)
├── src/
│   └── __init__.py
└── obsidian/
    └── vault/          # LOCAL Obsidian vault — open THIS folder in Obsidian
        ├── daily/      # one note per day: YYYY-MM-DD.md
        ├── reference/  # durable notes: exchange policies, limits, checklists
        └── logs/
            └── Dev Log.md
```

## Environment

- Python 3.11+ (dev machine runs 3.14). Virtualenv at `.venv/`:
  ```bash
  source .venv/bin/activate
  ```
- Dependencies go in `requirements.txt` (empty for now).
- Git initialized; `.venv/`, `.idea/`, `.env`, `__pycache__/` are ignored.

## Obsidian vault

The project has its **own** local vault at `obsidian/vault/`.

- `daily/` — daily notes, `YYYY-MM-DD.md`.
- `reference/` — durable reference that outlives a session, e.g.
  `Binance API policy.md` (rate limits, ban rules, ToS lines we must not cross).
- `logs/Dev Log.md` — one entry per session / meaningful change.

Add a Dev Log entry whenever the project changes.

## Note on the two vaults

There is also a separate global Obsidian vault on this machine synced over MCP —
it is unrelated to this project. This project only touches its local `obsidian/vault/`.

## Conventions

- Dates `YYYY-MM-DD`, times 24h `HH:MM`.