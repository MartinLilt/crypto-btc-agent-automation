"""Telegram output — push Grid-stream results.

Sends a short results message via the Telegram Bot API. No-op (returns False)
when TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID aren't configured, so the bot runs
fine without Telegram. Never raises — a failed send must not break a cycle.
"""

from __future__ import annotations

import requests

from .config import config

_TIMEOUT = 10


def enabled() -> bool:
    return bool(config.telegram_bot_token and config.telegram_chat_id)


def send(text: str, chat_id: str | int | None = None) -> bool:
    """Send a message. Defaults to the configured (owner) chat; pass chat_id to
    target a specific subscriber. Returns True on success."""
    if not config.telegram_bot_token:
        return False
    chat_id = chat_id if chat_id is not None else config.telegram_chat_id
    if not chat_id:
        return False
    url = f"https://api.telegram.org/bot{config.telegram_bot_token}/sendMessage"
    try:
        r = requests.post(
            url,
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=_TIMEOUT,
        )
        return r.status_code == 200
    except requests.RequestException:
        return False


def get_updates(offset: int = 0) -> list[dict]:
    """Poll new Telegram updates (short poll, no long-hold) from `offset`.
    Used once per cron cycle to catch /start messages for whitelist enrollment.
    Returns [] on any failure — subscription is best-effort, never blocks a cycle."""
    if not config.telegram_bot_token:
        return []
    url = f"https://api.telegram.org/bot{config.telegram_bot_token}/getUpdates"
    try:
        r = requests.post(url, json={"offset": offset, "timeout": 0,
                                     "allowed_updates": ["message"]}, timeout=_TIMEOUT)
        if r.status_code != 200:
            return []
        return r.json().get("result", [])
    except requests.RequestException:
        return []