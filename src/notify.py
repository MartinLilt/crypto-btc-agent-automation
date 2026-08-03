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


def send(text: str) -> bool:
    """Send a message to the configured chat. Returns True on success."""
    if not enabled():
        return False
    url = f"https://api.telegram.org/bot{config.telegram_bot_token}/sendMessage"
    try:
        r = requests.post(
            url,
            json={
                "chat_id": config.telegram_chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=_TIMEOUT,
        )
        return r.status_code == 200
    except requests.RequestException:
        return False