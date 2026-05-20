# Railway Deploy — Bot + Forward Tracker

Two services from this repo on Railway. The **bot** runs Telegram polling 24/7
(paper-trading via inline buttons, old 10-layer config). The **forward
tracker** runs once a day on cron and accumulates the 50/50 book's live P&L
to a JSON on a persistent volume — a second, independent paper-track of the
hardened research book on the same market.

## Prerequisites
- Railway account (the project may still exist from the prior deploy — check
  https://railway.app/dashboard).
- `TELEGRAM_BOT_TOKEN`, `OPENAI_API_KEY` (optional) from your local `.env`.
- This branch pushed to GitHub on `main` (auto-deploy hook trigger).

## Step 1 — Push the code
```bash
git checkout main
git merge feat/research-stack-and-hardened-book
git push origin main
```

## Step 2 — Service A: Telegram bot
- Railway project → **New Service → GitHub repo** → this repo, branch `main`.
- Settings → **Build** → Dockerfile (auto-detected).
- Settings → **Variables** → add:
  - `TELEGRAM_BOT_TOKEN = <your token>`
  - `OPENAI_API_KEY = <optional>`
  - `OPENAI_MODEL = gpt-4o-mini` (optional)
- Settings → **Volumes** → mount one at **`/app/data`** (SQLite DB + bot
  state persist here across redeploys).
- Deploy. Watch logs for `Application started`. From your Telegram, open
  the bot and click **Paper** to start a paper portfolio.

## Step 3 — Service B: Forward tracker (cron)
Same repo, same branch. Run command and schedule are different.

- **New Service → GitHub repo** → same repo.
- Settings → **Build** → Dockerfile.
- Settings → **Start Command** → `python -m research.forward step`
- Settings → **Cron Schedule** → `5 0 * * *`  (00:05 UTC, after the daily
  candle closes on Binance).
- Settings → **Variables**:
  - `FORWARD_STATE_PATH = /app/data/forward_state.json`
  - `FORWARD_CAPITAL = 10000`  (or whatever notional you're tracking)
- Settings → **Volumes** → mount at **`/app/data`** (shared concept with
  the bot's volume; on Railway you can attach the same volume to both
  services, or use a separate one — both work, the cron only writes the
  one JSON).
- First run: temporarily set the Start Command to
  `python -m research.forward init`, deploy once to create the state file,
  then change back to `step`. (Skipping: `step` will call `init` itself if
  the state file is missing, so you can also just leave it as `step` from
  the start.)

## Step 4 — Verify
- Bot: `/start` in Telegram → main menu appears.
- Tracker: in Railway logs of Service B, after the first cron fire you should
  see `forward days: 1 | equity €X | ...`. The state file lives at
  `/app/data/forward_state.json` and survives redeploys.

## Status checks from your laptop
The forward tracker also runs locally:
```bash
python -m research.forward report
```
This reads `research/forward_state.json` locally. The Railway version is
independent and persists to its own volume — that's the authoritative live
record.

## Rollback
- Bot: redeploy a previous commit from Railway UI.
- Tracker: same. State JSON is preserved on the volume regardless of redeploy.