"""通过 Telegram Bot 推送状态消息。"""

from __future__ import annotations

import logging

import httpx

from config import Settings

log = logging.getLogger("notifier")


async def push_bot_message(settings: Settings, text: str) -> None:
    if not settings.bot_enabled:
        return
    if not settings.bot_token or not settings.bot_chat_id:
        return
    url = f"https://api.telegram.org/bot{settings.bot_token}/sendMessage"
    payload = {
        "chat_id": settings.bot_chat_id,
        "text": text[:3900],
        "disable_web_page_preview": True,
    }
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
    except Exception:
        log.exception("Bot 推送失败")
