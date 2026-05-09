"""
sentiment_config.py
Dynamic sentiment classification thresholds from Config API.

Loaded at startup; refreshed every 60s in the background via a periodic asyncio task.
Invalidated immediately on config.changed Kafka events (namespace == "sentiment") — but
since AI Gateway has no Kafka consumer, refresh relies solely on the periodic task.

Defaults mirror packages/config-api/src/plughub_config_api/seed.py (sentiment.thresholds)
so the gateway works correctly even when the Config API is temporarily unreachable.

Classification algorithm:
  Categories are checked in descending order of their lower bound:
    satisfied  → score >= 0.3
    neutral    → score >= -0.3
    frustrated → score >= -0.6
    angry      → score <  -0.6   (catch-all)

This ordering is implicit in the threshold dict and preserved by classify().
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

logger = logging.getLogger("plughub.ai_gateway.sentiment_config")

# Default thresholds matching Config API seed — sentiment.thresholds
_DEFAULT_THRESHOLDS: dict[str, list[float]] = {
    "satisfied":  [0.3,   1.0],
    "neutral":    [-0.3,  0.3],
    "frustrated": [-0.6, -0.3],
    "angry":      [-1.0, -0.6],
}

# Fixed category order for classification (descending lower bound)
_ORDERED: list[str] = ["satisfied", "neutral", "frustrated"]


class SentimentConfig:
    """
    In-memory cache of Config API sentiment.thresholds.

    Thread-safe for asyncio (single-threaded event loop).

    Usage:
        sentiment_config.classify(0.45)             # → "satisfied"
        await sentiment_config.reload(config_api_url)  # startup / periodic refresh
    """

    def __init__(self) -> None:
        self._thresholds: dict[str, list[float]] = dict(_DEFAULT_THRESHOLDS)
        self._loaded_at: float = 0.0

    # ──────────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────────

    def classify(self, score: float) -> str:
        """
        Maps a sentiment score to a category label using current thresholds.
        Safe to call synchronously from emit/update paths.

        Algorithm: check each category in descending lower-bound order.
        The first whose lower bound is ≤ score wins. "angry" is the catch-all.
        """
        for category in _ORDERED:
            bounds = self._thresholds.get(category, _DEFAULT_THRESHOLDS[category])
            lower = bounds[0]
            if score >= lower:
                return category
        return "angry"

    async def reload(self, config_api_url: str) -> None:
        """
        Fetches GET {config_api_url}/config/sentiment and updates thresholds.
        Falls back silently to the current (or default) values on any error.
        """
        url = f"{config_api_url.rstrip('/')}/config/sentiment"
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(url)
            if resp.status_code == 200:
                body: dict[str, Any] = resp.json()
                # Config API returns { "entries": { key: { "value": ..., ... } } }
                # or a flat { key: value } dict depending on the endpoint used.
                entries = body.get("entries") or body
                raw = entries.get("thresholds")
                thresholds: dict[str, list[float]] | None = None
                if isinstance(raw, dict) and "value" in raw:
                    thresholds = raw["value"]
                elif isinstance(raw, dict) and raw:
                    thresholds = raw
                if thresholds:
                    self._thresholds = thresholds
                    self._loaded_at = time.monotonic()
                    logger.info(
                        "SentimentConfig reloaded: %d categories from %s",
                        len(thresholds), url,
                    )
            else:
                logger.warning(
                    "SentimentConfig: Config API returned HTTP %d — using current thresholds",
                    resp.status_code,
                )
        except Exception as exc:
            logger.warning(
                "SentimentConfig reload failed (%s) — using current/default thresholds: %s",
                url, exc,
            )

    async def refresh_loop(self, config_api_url: str, interval_s: float = 60.0) -> None:
        """
        Periodic background task: reloads thresholds every `interval_s` seconds.
        Designed to run as an asyncio.create_task() inside lifespan().
        Exits cleanly on CancelledError.
        """
        while True:
            try:
                await asyncio.sleep(interval_s)
                await self.reload(config_api_url)
            except asyncio.CancelledError:
                return
            except Exception as exc:
                logger.warning("SentimentConfig refresh_loop unexpected error: %s", exc)


# Module-level singleton — imported by sentiment_emitter and main.py
sentiment_config = SentimentConfig()
