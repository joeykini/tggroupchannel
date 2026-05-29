"""创建 Telethon 客户端。"""

from __future__ import annotations

from pathlib import Path
from telethon import TelegramClient

from config import ROOT, Settings


def session_path(settings: Settings) -> Path:
    name = settings.session_name
    p = ROOT / name
    if p.suffix != ".session":
        return ROOT / f"{name}.session"
    return p


def create_client(settings: Settings) -> TelegramClient:
    kwargs: dict = {
        "connection_retries": 3,
        "retry_delay": 2,
        "timeout": 15,
    }
    return TelegramClient(
        settings.session_name,
        settings.api_id,
        settings.api_hash,
        **kwargs,
    )
