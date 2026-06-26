#!/usr/bin/env python3
"""命令行入口：监听、登录、同步、补抓（不依赖网页）。"""

from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import datetime

from bridge import ChannelBridge
from bot_admin import BotAdmin
from config import load_settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("forwarder")


async def _daily_scheduler(bridge: ChannelBridge) -> None:
    last_run = ""
    while bridge.running:
        try:
            settings = load_settings()
            if settings.daily_fetch_enabled:
                now = datetime.now()
                day_key = now.strftime("%Y-%m-%d")
                if now.strftime("%H:%M") == settings.daily_fetch_time and last_run != day_key:
                    last_run = day_key
                    log.info("触发每日定时抓取")
                    await bridge.fetch_recent_once(limit_per_channel=settings.daily_fetch_limit)
        except Exception as e:
            log.error("定时抓取失败: %s", e)
        await asyncio.sleep(30)


async def _sync_scheduler(bridge: ChannelBridge) -> None:
    while bridge.running:
        try:
            settings = load_settings()
            if settings.sync_enabled:
                await bridge.sync_with_source()
        except Exception as e:
            log.error("源频道同步失败: %s", e)
        interval = max(5, load_settings().sync_interval_minutes)
        await asyncio.sleep(interval * 60)


async def _roster_scheduler(bridge: ChannelBridge) -> None:
    while bridge.running:
        try:
            settings = load_settings()
            if settings.roster_sync_enabled and settings.roster_enabled:
                await bridge.sync_roster()
        except Exception as e:
            log.error("出勤同步失败: %s", e)
        interval = max(30, load_settings().roster_sync_interval_minutes)
        await asyncio.sleep(interval * 60)


async def cmd_run() -> None:
    bridge = ChannelBridge()
    admin = BotAdmin(bridge)
    await bridge.start()
    daily_task = asyncio.create_task(_daily_scheduler(bridge))
    sync_task = asyncio.create_task(_sync_scheduler(bridge))
    roster_task = asyncio.create_task(_roster_scheduler(bridge))
    admin_task = asyncio.create_task(admin.run_loop())
    try:
        while bridge.running:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        log.info("收到退出信号")
    finally:
        daily_task.cancel()
        sync_task.cancel()
        roster_task.cancel()
        admin_task.cancel()
        await admin.close()
        await bridge.stop()


async def cmd_login() -> None:
    bridge = ChannelBridge()
    result = await bridge.cli_login()
    log.info("登录成功: %s", result.get("user"))


async def cmd_sync() -> None:
    bridge = ChannelBridge()
    result = await bridge.sync_with_source()
    log.info("同步结果: %s", result)


async def cmd_fetch(limit: int, since_hours: int) -> None:
    bridge = ChannelBridge()
    result = await bridge.fetch_recent_once(
        limit_per_channel=limit,
        since_hours=since_hours or None,
    )
    log.info("补抓完成: %s", result)


async def cmd_roster() -> None:
    bridge = ChannelBridge()
    result = await bridge.sync_roster()
    log.info("出勤同步: %s", result)


def main() -> None:
    parser = argparse.ArgumentParser(description="Telegram 频道抓取转发（命令行）")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("run", help="监听源频道并自动处理/发布（默认）")
    sub.add_parser("login", help="命令行登录 Telegram 账号")
    sub.add_parser("sync", help="对比源频道删帖与重复项")
    sub.add_parser("roster", help="抓取出勤名单并同步统一榜")
    fetch_p = sub.add_parser("fetch", help="立即补抓最近帖子")
    fetch_p.add_argument("--limit", type=int, default=30, help="每频道条数")
    fetch_p.add_argument("--since-hours", type=int, default=0, help="仅抓最近 N 小时")

    args = parser.parse_args()
    command = args.command or "run"

    if command == "login":
        asyncio.run(cmd_login())
    elif command == "sync":
        asyncio.run(cmd_sync())
    elif command == "roster":
        asyncio.run(cmd_roster())
    elif command == "fetch":
        asyncio.run(cmd_fetch(args.limit, args.since_hours))
    else:
        asyncio.run(cmd_run())


if __name__ == "__main__":
    main()
