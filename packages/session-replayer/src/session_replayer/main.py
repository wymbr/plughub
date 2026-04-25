"""
main.py
Entrypoint do Session Replayer service.
"""

from __future__ import annotations

import asyncio
import logging
import signal

from .consumer import SessionReplayerConsumer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


async def run() -> None:
    consumer = SessionReplayerConsumer()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(consumer.stop()))

    try:
        await consumer.start()
    except asyncio.CancelledError:
        pass
    finally:
        await consumer.stop()
        logger.info("SessionReplayer: shutdown complete")


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
