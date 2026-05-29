"""创建带代理的 Telethon 客户端（国内网络通常需要）。"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from telethon import TelegramClient

from config import ROOT, Settings


def session_path(settings: Settings) -> Path:
    name = settings.session_name
    p = ROOT / name
    if p.suffix != ".session":
        return ROOT / f"{name}.session"
    return p


def parse_proxy(proxy_url: str) -> dict | None:
    """
    支持：
      socks5://127.0.0.1:7890
      http://127.0.0.1:7890
      127.0.0.1:7890  （默认 socks5）
    """
    raw = (proxy_url or "").strip()
    if not raw:
        return None
    if "://" not in raw:
        raw = f"socks5://{raw}"
    u = urlparse(raw)
    scheme = (u.scheme or "socks5").lower()
    if scheme in ("socks5", "socks"):
        ptype = "socks5"
    elif scheme in ("socks4",):
        ptype = "socks4"
    elif scheme in ("http", "https"):
        ptype = "http"
    else:
        ptype = scheme
    host = u.hostname
    port = u.port
    if not host or not port:
        raise ValueError(f"代理地址无效: {proxy_url}，示例 socks5://127.0.0.1:7890")
    proxy: dict = {
        "proxy_type": ptype,
        "addr": host,
        "port": int(port),
        "rdns": True,
    }
    if u.username:
        proxy["username"] = u.username
    if u.password:
        proxy["password"] = u.password
    return proxy


def create_client(settings: Settings) -> TelegramClient:
    proxy = parse_proxy(settings.telegram_proxy)
    kwargs: dict = {
        "connection_retries": 3,
        "retry_delay": 2,
        "timeout": 15,
    }
    if proxy:
        kwargs["proxy"] = proxy
    return TelegramClient(
        settings.session_name,
        settings.api_id,
        settings.api_hash,
        **kwargs,
    )
