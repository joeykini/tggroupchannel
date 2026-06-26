"""配置：默认读 .env，网页保存(settings.json)优先覆盖。"""

from __future__ import annotations

import json
import os
import re
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
    "中奖",
    "口令红包",
    "工兵券",
    "商k",
]

DEFAULT_ALLOWED_REGIONS = [
    "清江浦区",
    "淮阴区",
    "淮安区",
    "洪泽区",
    "涟水县",
    "盱眙县",
    "金湖县",
]


def _split_list(raw: str | list[str] | None) -> list[str]:
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    return [p.strip() for p in re.split(r"[,;；\n]+", str(raw)) if p.strip()]


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
    template_extract_enabled: bool = False
    content_dedup_enabled: bool = True
    sync_enabled: bool = True
    sync_interval_minutes: int = 60
    sync_scan_limit: int = 200
    delete_from_target_on_source_removed: bool = False
    roster_enabled: bool = True
    roster_group_1: str = "@HuaiAnHub"
    roster_channel_1: str = "@huaian008"
    roster_group_2: str = "@HuaiAn_YangZhou"
    roster_channel_2: str = "@huaian0901"
    roster_trigger_keyword: str = "出勤"
    roster_bot_names: list[str] = field(default_factory=lambda: ["修车小助手"])
    roster_sync_enabled: bool = True
    roster_sync_interval_minutes: int = 120
    roster_sync_daily_enabled: bool = True
    roster_sync_time: str = "02:30"
    publish_interval_seconds: int = 30
    auto_publish_after_roster: bool = False
    nightly_job_enabled: bool = True
    region_filter_enabled: bool = True
    allowed_regions: list[str] = field(default_factory=lambda: list(DEFAULT_ALLOWED_REGIONS))
    delete_inactive_from_target: bool = True
    person_dedup_enabled: bool = True
    publish_template: str = (
        "📊 {review_count}条车评，综合评分\n"
        "好评 {good_rate}    |人照 {photo_score}    |服务 {service_score}\n"
        "中评 {mid_rate}    |颜值 {face_score}    |态度 {attitude_score}\n"
        "差评 {bad_rate}    |身材 {body_score}    |环境 {env_score}\n\n"
        "名字:{name}\n"
        "年龄:{age}\n"
        "体重:{weight}\n"
        "罩杯:{cup}\n"
        "项目:{project}\n"
        "一次价格:{price_once}\n"
        "两次价格:{price_twice}\n"
        "地区:{region}\n"
        "电报:{telegram}\n"
        "频道:{channel}\n"
        "双向:{duplex}"
    )
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o-mini"
    auto_publish: bool = False
    require_media: bool = False
    # 每日定时抓取（用于服务器守护进程场景）
    daily_fetch_enabled: bool = False
    daily_fetch_time: str = "03:00"
    daily_fetch_limit: int = 30
    # 抓取启动时是否先补抓一轮
    fetch_on_start: bool = False
    # Bot 推送与管理
    bot_enabled: bool = False
    bot_token: str = ""
    bot_chat_id: str = ""
    bot_admin_ids: list[str] = field(default_factory=list)

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
        d["bot_admin_ids"] = ",".join(self.bot_admin_ids)
        d["roster_bot_names"] = ",".join(self.roster_bot_names)
        d["allowed_regions"] = ",".join(self.allowed_regions)
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
            template_extract_enabled=_bool(data.get("template_extract_enabled"), False),
            content_dedup_enabled=_bool(data.get("content_dedup_enabled"), True),
            sync_enabled=_bool(data.get("sync_enabled"), True),
            sync_interval_minutes=max(5, _int(data.get("sync_interval_minutes"), 60)),
            sync_scan_limit=max(20, _int(data.get("sync_scan_limit"), 200)),
            delete_from_target_on_source_removed=_bool(
                data.get("delete_from_target_on_source_removed"), False
            ),
            roster_enabled=_bool(data.get("roster_enabled"), True),
            roster_group_1=str(data.get("roster_group_1") or "@HuaiAnHub").strip(),
            roster_channel_1=str(data.get("roster_channel_1") or "@huaian008").strip(),
            roster_group_2=str(data.get("roster_group_2") or "@HuaiAn_YangZhou").strip(),
            roster_channel_2=str(data.get("roster_channel_2") or "@huaian0901").strip(),
            roster_trigger_keyword=str(data.get("roster_trigger_keyword") or "出勤").strip(),
            roster_bot_names=_split_list(data.get("roster_bot_names", ["修车小助手"])),
            roster_sync_enabled=_bool(data.get("roster_sync_enabled"), True),
            roster_sync_interval_minutes=max(30, _int(data.get("roster_sync_interval_minutes"), 120)),
            roster_sync_daily_enabled=_bool(data.get("roster_sync_daily_enabled"), True),
            roster_sync_time=str(data.get("roster_sync_time") or "02:30").strip(),
            publish_interval_seconds=max(5, _int(data.get("publish_interval_seconds"), 30)),
            auto_publish_after_roster=_bool(data.get("auto_publish_after_roster"), False),
            nightly_job_enabled=_bool(data.get("nightly_job_enabled"), True),
            region_filter_enabled=_bool(data.get("region_filter_enabled"), True),
            allowed_regions=_split_list(data.get("allowed_regions")) or list(DEFAULT_ALLOWED_REGIONS),
            delete_inactive_from_target=_bool(data.get("delete_inactive_from_target"), True),
            person_dedup_enabled=_bool(data.get("person_dedup_enabled"), True),
            publish_template=str(data.get("publish_template") or cls.publish_template),
            openai_api_key=str(data.get("openai_api_key") or "").strip(),
            openai_base_url=str(data.get("openai_base_url") or "https://api.openai.com/v1").rstrip("/"),
            openai_model=str(data.get("openai_model") or "gpt-4o-mini").strip(),
            auto_publish=_bool(data.get("auto_publish"), False),
            require_media=_bool(data.get("require_media"), False),
            daily_fetch_enabled=_bool(data.get("daily_fetch_enabled"), False),
            daily_fetch_time=str(data.get("daily_fetch_time") or "03:00").strip(),
            daily_fetch_limit=max(1, _int(data.get("daily_fetch_limit"), 30)),
            fetch_on_start=_bool(data.get("fetch_on_start"), False),
            bot_enabled=_bool(data.get("bot_enabled"), False),
            bot_token=str(data.get("bot_token") or "").strip(),
            bot_chat_id=str(data.get("bot_chat_id") or "").strip(),
            bot_admin_ids=[x for x in _split_list(data.get("bot_admin_ids", []))],
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
        "template_extract_enabled": os.getenv("TEMPLATE_EXTRACT_ENABLED"),
        "content_dedup_enabled": os.getenv("CONTENT_DEDUP_ENABLED"),
        "sync_enabled": os.getenv("SYNC_ENABLED"),
        "sync_interval_minutes": os.getenv("SYNC_INTERVAL_MINUTES"),
        "sync_scan_limit": os.getenv("SYNC_SCAN_LIMIT"),
        "delete_from_target_on_source_removed": os.getenv("DELETE_FROM_TARGET_ON_SOURCE_REMOVED"),
        "roster_enabled": os.getenv("ROSTER_ENABLED"),
        "roster_group_1": os.getenv("ROSTER_GROUP_1"),
        "roster_channel_1": os.getenv("ROSTER_CHANNEL_1"),
        "roster_group_2": os.getenv("ROSTER_GROUP_2"),
        "roster_channel_2": os.getenv("ROSTER_CHANNEL_2"),
        "roster_trigger_keyword": os.getenv("ROSTER_TRIGGER_KEYWORD"),
        "roster_bot_names": os.getenv("ROSTER_BOT_NAMES"),
        "roster_sync_enabled": os.getenv("ROSTER_SYNC_ENABLED"),
        "roster_sync_interval_minutes": os.getenv("ROSTER_SYNC_INTERVAL_MINUTES"),
        "roster_sync_daily_enabled": os.getenv("ROSTER_SYNC_DAILY_ENABLED"),
        "roster_sync_time": os.getenv("ROSTER_SYNC_TIME"),
        "publish_interval_seconds": os.getenv("PUBLISH_INTERVAL_SECONDS"),
        "auto_publish_after_roster": os.getenv("AUTO_PUBLISH_AFTER_ROSTER"),
        "nightly_job_enabled": os.getenv("NIGHTLY_JOB_ENABLED"),
        "region_filter_enabled": os.getenv("REGION_FILTER_ENABLED"),
        "allowed_regions": os.getenv("ALLOWED_REGIONS"),
        "delete_inactive_from_target": os.getenv("DELETE_INACTIVE_FROM_TARGET"),
        "person_dedup_enabled": os.getenv("PERSON_DEDUP_ENABLED"),
        "publish_template": os.getenv("PUBLISH_TEMPLATE"),
        "openai_api_key": os.getenv("OPENAI_API_KEY"),
        "openai_base_url": os.getenv("OPENAI_BASE_URL"),
        "openai_model": os.getenv("OPENAI_MODEL"),
        "auto_publish": os.getenv("AUTO_PUBLISH"),
        "require_media": os.getenv("REQUIRE_MEDIA"),
        "daily_fetch_enabled": os.getenv("DAILY_FETCH_ENABLED"),
        "daily_fetch_time": os.getenv("DAILY_FETCH_TIME"),
        "daily_fetch_limit": os.getenv("DAILY_FETCH_LIMIT"),
        "fetch_on_start": os.getenv("FETCH_ON_START"),
        "bot_enabled": os.getenv("BOT_ENABLED"),
        "bot_token": os.getenv("BOT_TOKEN"),
        "bot_chat_id": os.getenv("BOT_CHAT_ID"),
        "bot_admin_ids": os.getenv("BOT_ADMIN_IDS"),
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


def patch_settings(**changes: str | bool | int | list[str]) -> Settings:
    """合并修改并写入 settings.json。"""
    current = load_settings().to_dict()
    for key, value in changes.items():
        if key in ("source_channels", "filter_keywords", "blocked_keywords", "bot_admin_ids", "roster_bot_names", "allowed_regions"):
            if isinstance(value, list):
                current[key] = ",".join(value)
            else:
                current[key] = str(value)
        elif isinstance(value, bool):
            current[key] = value
        else:
            current[key] = value
    updated = Settings.from_dict(current)
    save_settings(updated)
    return updated
