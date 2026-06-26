"""按固定模板从文案提取字段并渲染（支持多种标签别名）。"""

from __future__ import annotations

import hashlib
import re

# 标准字段 -> 可能出现的标签词（源站用词不一）
FIELD_LABELS: dict[str, list[str]] = {
    "name": ["名字", "姓名", "昵称", "呢称", "花名", "艺名", "名称", "妹子", "小姐姐"],
    "age": ["年龄", "岁数", "年岁"],
    "height": ["身高", "个头"],
    "weight": ["体重", "重量"],
    "cup": ["罩杯", "胸杯", "cup"],
    "project": ["项目", "项目分", "服务内容", "服务", "套餐"],
    "price_once": ["一次价格", "单次价格", "一次", "单次", "单P", "1P", "价位", "价格", "收费"],
    "price_twice": ["两次价格", "双次价格", "两次", "双次", "2P", "两P"],
    "region": ["地区", "区域", "位置", "定位", "地址", "所在", "地点"],
    "telegram": ["电报", "tg", "telegram", "联系", "私聊", "账号"],
    "channel": ["频道", "频道链接", "频道号", "公开群", "群链接"],
    "duplex": ["双向", "机器人", "bot", "双向机器人"],
}

# 评分区标签相对固定，单独维护
RATING_PATTERNS: dict[str, str] = {
    "review_count": r"(\d+)\s*条\s*车评",
    "overall_score": r"综合评分\s*([0-9.]+)",
    "good_rate": r"好评\s*([0-9]{1,3}%?)",
    "mid_rate": r"中评\s*([0-9]{1,3}%?)",
    "bad_rate": r"差评\s*([0-9]{1,3}%?)",
    "photo_score": r"\|?\s*人照\s*([0-9.]+)",
    "service_score": r"\|?\s*服务\s*([0-9.]+)",
    "face_score": r"\|?\s*颜值\s*([0-9.]+)",
    "attitude_score": r"\|?\s*态度\s*([0-9.]+)",
    "body_score": r"\|?\s*身材\s*([0-9.]+)",
    "env_score": r"\|?\s*环境\s*([0-9.]+)",
}

_EMOJI_PREFIX = r"[\U0001F300-\U0001FAFF\u2600-\u27BF\uFE0F\s]*"


def _first_match(text: str, patterns: list[str]) -> str:
    for pattern in patterns:
        m = re.search(pattern, text, flags=re.IGNORECASE)
        if m:
            return (m.group(1) or "").strip()
    return ""


def _normalize_text_for_parse(text: str) -> str:
    s = text or ""
    s = s.replace("\ufe0f", "").replace("\u20e3", "")
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    return s


def _normalize_name(raw: str) -> str:
    if not raw:
        return ""
    cleaned = re.sub(r"[（(].*?[)）]", "", raw).strip()
    return cleaned or raw.strip()


def _strip_price_noise(raw: str) -> str:
    line = raw.strip()
    line = re.sub(r"[💴💰￥¥]", "", line)
    line = re.sub(r"(?<=\d)\s+(?=\d)", "", line)
    return line


def _extract_prices_from_text(raw: str) -> tuple[str, str]:
    """从价位行或全文提取 1P/2P 或纯数字价格。"""
    if not raw:
        return "", ""

    for label in FIELD_LABELS["price_once"] + FIELD_LABELS["price_twice"]:
        val = _extract_line_value(raw, [label])
        if val:
            compact = _strip_price_noise(val)
            nums = re.findall(r"(\d{2,5})\s*[Pp]?", compact)
            if nums:
                if len(nums) == 1:
                    return nums[0], ""
                return nums[0], nums[1]

    compact = _strip_price_noise(raw)
    p_matches = re.findall(r"(\d{2,5})\s*[Pp]", compact, flags=re.IGNORECASE)
    if len(p_matches) >= 2:
        return p_matches[0], p_matches[1]
    if len(p_matches) == 1:
        return p_matches[0], ""

    money = re.findall(r"(?<![.\d])(\d{3,5})(?![.\d])", compact)
    if len(money) >= 2:
        return money[0], money[1]
    if len(money) == 1:
        return money[0], ""
    return "", ""


def _extract_line_value(text: str, labels: list[str]) -> str:
    for label in labels:
        pattern = (
            rf"(?:{_EMOJI_PREFIX})?"
            rf"{re.escape(label)}\s*[：:]\s*([^\n\r]+)"
        )
        m = re.search(pattern, text, flags=re.IGNORECASE)
        if m:
            return m.group(1).strip()
    return ""


def _scan_labeled_lines(text: str) -> dict[str, str]:
    """扫描全文「标签：值」行，按别名映射到标准字段。"""
    alias_map: dict[str, str] = {}
    for field, labels in FIELD_LABELS.items():
        for label in labels:
            alias_map[label.lower()] = field

    found: dict[str, str] = {}
    line_pattern = re.compile(
        rf"^(?:{_EMOJI_PREFIX})?"
        r"([\u4e00-\u9fffA-Za-z0-9]{1,10})\s*[：:]\s*(.+)$",
        re.MULTILINE,
    )
    for m in line_pattern.finditer(text):
        label = re.sub(r"[\U0001F300-\U0001FAFF\u2600-\u27BF\uFE0F\s]+", "", m.group(1)).strip()
        value = m.group(2).strip()
        if not label or not value:
            continue
        field = alias_map.get(label.lower())
        if field and field not in found:
            found[field] = value
    return found


def _extract_contact_fields(text: str, existing: dict[str, str]) -> None:
    """补抓频道链接、@用户名、双向机器人。"""
    if not existing.get("channel"):
        existing["channel"] = _first_match(
            text,
            [
                r"(https?://t\.me/[A-Za-z0-9_/+]+)",
                r"(?:https?://)?t\.me/[A-Za-z0-9_/+]+",
            ],
        )

    if not existing.get("duplex"):
        existing["duplex"] = _first_match(
            text,
            [
                r"(?:双向|机器人|bot)\s*[：:]\s*([^\n\r]+)",
                r"(@[A-Za-z0-9_]*bot)\b",
            ],
        )

    if not existing.get("telegram"):
        tg = _first_match(
            text,
            [
                r"(?:电报|tg|telegram|联系|私聊|账号)\s*[：:]\s*([^\n\r]+)",
            ],
        )
        if not tg:
            for m in re.finditer(r"(@[A-Za-z0-9_]{4,})", text):
                handle = m.group(1)
                if existing.get("duplex") and handle.lower() == existing["duplex"].lower():
                    continue
                if handle.lower().endswith("bot") and existing.get("duplex"):
                    continue
                tg = handle
                break
        if tg and existing.get("duplex") and tg.strip().lower() == existing["duplex"].strip().lower():
            tg = ""
        existing["telegram"] = tg


def extract_profile_fields(text: str) -> dict[str, str]:
    raw = _normalize_text_for_parse(text)
    fields: dict[str, str] = {
        "review_count": "",
        "overall_score": "",
        "good_rate": "",
        "mid_rate": "",
        "bad_rate": "",
        "photo_score": "",
        "service_score": "",
        "face_score": "",
        "attitude_score": "",
        "body_score": "",
        "env_score": "",
        "name": "",
        "age": "",
        "height": "",
        "weight": "",
        "cup": "",
        "project": "",
        "price_once": "",
        "price_twice": "",
        "region": "",
        "telegram": "",
        "channel": "",
        "duplex": "",
    }

    for key, pattern in RATING_PATTERNS.items():
        mm = re.search(pattern, raw, flags=re.IGNORECASE)
        if mm:
            fields[key] = mm.group(1).strip()

    scanned = _scan_labeled_lines(raw)
    for key in (
        "name",
        "age",
        "height",
        "weight",
        "cup",
        "project",
        "price_once",
        "price_twice",
        "region",
        "telegram",
        "channel",
        "duplex",
    ):
        if scanned.get(key):
            fields[key] = scanned[key]

    for key, labels in FIELD_LABELS.items():
        if fields.get(key):
            continue
        val = _extract_line_value(raw, labels)
        if val:
            fields[key] = val

    fields["name"] = _normalize_name(fields["name"])

    if fields.get("price_once") and not re.search(r"\d", fields["price_once"]):
        fields["price_once"] = ""
    if fields.get("price_twice") and not re.search(r"\d", fields["price_twice"]):
        fields["price_twice"] = ""

    if not fields["price_once"] and not fields["price_twice"]:
        once, twice = _extract_prices_from_text(raw)
        fields["price_once"] = once
        fields["price_twice"] = twice
    elif fields["price_once"] and not fields["price_twice"]:
        once, twice = _extract_prices_from_text(fields["price_once"])
        if once:
            fields["price_once"] = once
        if twice:
            fields["price_twice"] = twice

    _extract_contact_fields(raw, fields)
    return fields


def content_fingerprint(text: str) -> str:
    """用于跨 message_id 的内容去重。"""
    fields = extract_profile_fields(text)
    parts = [
        fields.get("name", "").lower(),
        fields.get("region", "").lower(),
        fields.get("project", "").lower(),
        fields.get("price_once", ""),
        fields.get("price_twice", ""),
    ]
    key = "|".join(p for p in parts if p)
    if key:
        return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
    normalized = re.sub(r"\s+", "", (text or "").lower())
    if len(normalized) < 20:
        return ""
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:16]


def render_publish_template(text: str, template: str) -> str:
    if not text:
        return ""
    fields = extract_profile_fields(text)
    fields["raw"] = text
    safe = {k: (v or "") for k, v in fields.items()}
    try:
        return template.format_map(safe).strip()
    except Exception:
        return text
