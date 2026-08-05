"""The coin universe the pipeline screens.

Either a live, liquidity-ranked top-N list (AUTO_UNIVERSE=true) or the static
TARGET_COINS fallback. Fetched once per run and cached so every layer
(autopilot, screener, backtests) sees the same universe within a run.

Note: a bigger universe widens coverage but also multiplies the
multiple-comparisons / overfitting risk — the robust-score and OOS discipline
in the selector are what keep that honest.
"""

from __future__ import annotations

from .config import config

_cache: tuple[str, ...] | None = None


def get_universe(refresh: bool = False) -> tuple[str, ...]:
    global _cache
    if _cache is not None and not refresh:
        return _cache

    coins = config.target_coins
    if config.auto_universe:
        try:
            from .binance_api import get_top_symbols
            from .funding import get_perpetual_symbols

            # Pull a wide top-volume list, then keep only coins that also have a
            # perpetual future — filters out tokenized stocks / gold / stables
            # and guarantees real, liquid, carry-compatible crypto.
            wide = get_top_symbols(config.universe_size * 4, config.min_quote_volume,
                                   quote=config.quote_asset)
            perps = get_perpetual_symbols()
            # perps are USDT-quoted; match by base asset so USDC pairs pass too
            perp_bases = {s.replace("USDT", "") for s in perps}
            filtered = [s for s in wide
                        if s.replace(config.quote_asset, "") in perp_bases][: config.universe_size]
            if filtered:
                coins = tuple(filtered)
        except Exception:
            coins = config.target_coins  # fall back to the static list on error

    _cache = coins
    return _cache