#!/usr/bin/env python3
"""命令行启动：监听频道并转发（可选 AI，见 .env / settings.json）。"""

import asyncio
import logging

from bridge import ChannelBridge

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)


async def main() -> None:
    bridge = ChannelBridge()
    await bridge.start()
    try:
        while bridge.running:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        await bridge.stop()


if __name__ == "__main__":
    asyncio.run(main())
