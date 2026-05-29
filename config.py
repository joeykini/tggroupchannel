"""配置：默认读 .env，网页保存(settings.json)优先覆盖。"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
SETTINGS_PATH = ROOT / "settings.json"

DEFAULT_BLOCKED_KEYWORDS = [
    "vpn",
    "机场",
    "梯子",
    "翻墙",
    "代理",
    "节点",
    "clash",
    "v2ray",
    "trojan",
    "ssr",
    "广告",
    "推广",
    "博彩",
    "招商",
]


def _split_list(raw: str | list[str] | None) -> list[str]:
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    return [p.strip() for p in str(raw).split(",") if p.strip()]


def _bool(val: str | bool | None, default: bool = True) -> bool:
    if isinstance(val, bool):
        return val
    if val is None:
        return default
    return str(val).lower() in ("1", "true", "yes", "on")


def _int(val: str | int | None, default: int) -> int:
    try:
        return int(val) if val is not None else default
    except (TypeError, ValueError):
        return default


@dataclass
class Settings:
    api_id: int = 0
    api_hash: str = ""
    session_name: str = "user"
    source_channels: list[str] = field(default_factory=list)
    target_channel: str = ""
    filter_keywords: list[str] = field(default_factory=list)
    blocked_keywords: list[str] = field(default_factory=lambda: list(DEFAULT_BLOCKED_KEYWORDS))
    forward_header: str = ""
    copy_without_forward_tag: bool = True
    strip_source_refs: bool = True
    ai_enabled: bool = False
    ai_prompt: str = ""
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o-mini"
    auto_publish: bool = True
    require_media: bool = False
    telegram_proxy: str = ""
    # 每日定时抓取（用于服务器守护进程场景）
    daily_fetch_enabled: bool = False
    daily_fetch_time: str = "03:00"
    daily_fetch_limit: int = 30
    # 抓取启动时是否先补抓一轮
    fetch_on_start: bool = False
    # Bot 推送
    bot_enabled: bool = False
    bot_token: str = ""
    bot_chat_id: str = ""

    def validate_for_capture(self) -> list[str]:
        errors: list[str] = []
        if not self.api_id:
            errors.append("缺少 API_ID")
        if not self.api_hash:
            errors.append("缺少 API_HASH")
        if not self.source_channels:
            errors.append("请配置至少一个源频道 SOURCE_CHANNELS")
        if self.ai_enabled and not self.openai_api_key:
            errors.append("已开启 AI 复写，请填写 OPENAI_API_KEY")
        if self.bot_enabled and (not self.bot_token or not self.bot_chat_id):
            errors.append("已开启 Bot 推送，请填写 BOT_TOKEN 与 BOT_CHAT_ID")
        return errors

    def validate_for_publish(self) -> list[str]:
        errors = self.validate_for_capture()
        if not self.target_channel:
            errors.append("发布前请先配置目标频道 TARGET_CHANNEL")
        return errors

    def to_dict(self) -> dict:
        d = asdict(self)
        d["source_channels"] = ",".join(self.source_channels)
        d["filter_keywords"] = ",".join(self.filter_keywords)
        d["blocked_keywords"] = ",".join(self.blocked_keywords)
        return d

    @classmethod
    def from_dict(cls, data: dict) -> Settings:
        src = _split_list(data.get("source_channels", []))
        kw = [k.lower() for k in _split_list(data.get("filter_keywords", []))]
        blocked = [k.lower() for k in _split_list(data.get("blocked_keywords", []))]
        if not blocked:
            blocked = list(DEFAULT_BLOCKED_KEYWORDS)

        return cls(
            api_id=_int(data.get("api_id"), 0),
            api_hash=str(data.get("api_hash") or "").strip(),
            session_name=str(data.get("session_name") or "user").strip() or "user",
            source_channels=src,
            target_channel=str(data.get("target_channel") or "").strip(),
            filter_keywords=kw,
            blocked_keywords=blocked,
            forward_header=str(data.get("forward_header") or "").strip(),
            copy_without_forward_tag=_bool(data.get("copy_without_forward_tag"), True),
            strip_source_refs=_bool(data.get("strip_source_refs"), True),
            ai_enabled=_bool(data.get("ai_enabled"), False),
            ai_prompt=str(data.get("ai_prompt") or ""),
            openai_api_key=str(data.get("openai_api_key") or "").strip(),
            openai_base_url=str(data.get("openai_base_url") or "https://api.openai.com/v1").rstrip("/"),
            openai_model=str(data.get("openai_model") or "gpt-4o-mini").strip(),
            auto_publish=_bool(data.get("auto_publish"), True),
            require_media=_bool(data.get("require_media"), False),
            telegram_proxy=str(data.get("telegram_proxy") or "").strip(),
            daily_fetch_enabled=_bool(data.get("daily_fetch_enabled"), False),
            daily_fetch_time=str(data.get("daily_fetch_time") or "03:00").strip(),
            daily_fetch_limit=max(1, _int(data.get("daily_fetch_limit"), 30)),
            fetch_on_start=_bool(data.get("fetch_on_start"), False),
            bot_enabled=_bool(data.get("bot_enabled"), False),
            bot_token=str(data.get("bot_token") or "").strip(),
            bot_chat_id=str(data.get("bot_chat_id") or "").strip(),
        )


def load_settings() -> Settings:
    # 先用 .env 作为默认值，再用 settings.json 覆盖，避免网页配置不生效
    data: dict = {}
    env_map = {
        "api_id": os.getenv("API_ID"),
        "api_hash": os.getenv("API_HASH"),
        "session_name": os.getenv("SESSION_NAME"),
        "source_channels": os.getenv("SOURCE_CHANNELS"),
        "target_channel": os.getenv("TARGET_CHANNEL"),
        "filter_keywords": os.getenv("FILTER_KEYWORDS"),
        "blocked_keywords": os.getenv("BLOCKED_KEYWORDS"),
        "forward_header": os.getenv("FORWARD_HEADER"),
        "copy_without_forward_tag": os.getenv("COPY_WITHOUT_FORWARD_TAG"),
        "strip_source_refs": os.getenv("STRIP_SOURCE_REFS"),
        "ai_enabled": os.getenv("AI_ENABLED"),
        "ai_prompt": os.getenv("AI_PROMPT"),
        "openai_api_key": os.getenv("OPENAI_API_KEY"),
        "openai_base_url": os.getenv("OPENAI_BASE_URL"),
        "openai_model": os.getenv("OPENAI_MODEL"),
        "auto_publish": os.getenv("AUTO_PUBLISH"),
        "require_media": os.getenv("REQUIRE_MEDIA"),
        "telegram_proxy": os.getenv("TELEGRAM_PROXY"),
        "daily_fetch_enabled": os.getenv("DAILY_FETCH_ENABLED"),
        "daily_fetch_time": os.getenv("DAILY_FETCH_TIME"),
        "daily_fetch_limit": os.getenv("DAILY_FETCH_LIMIT"),
        "fetch_on_start": os.getenv("FETCH_ON_START"),
        "bot_enabled": os.getenv("BOT_ENABLED"),
        "bot_token": os.getenv("BOT_TOKEN"),
        "bot_chat_id": os.getenv("BOT_CHAT_ID"),
    }
    for k, v in env_map.items():
        if v is not None and str(v).strip() != "":
            data[k] = v

    if SETTINGS_PATH.exists():
        try:
            file_data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            for k, v in file_data.items():
                if v is not None and str(v).strip() != "":
                    data[k] = v
        except json.JSONDecodeError:
            pass

    return Settings.from_dict(data)


def save_settings(settings: Settings) -> None:
    SETTINGS_PATH.write_text(
        json.dumps(settings.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
