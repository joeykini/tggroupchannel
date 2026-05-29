"""按固定模板从文案提取字段并渲染。"""

from __future__ import annotations

import re


def _first_match(text: str, patterns: list[str]) -> str:
    for pattern in patterns:
        m = re.search(pattern, text, flags=re.IGNORECASE)
        if m:
            return (m.group(1) or "").strip()
    return ""


def _normalize_text_for_parse(text: str) -> str:
    s = text or ""
    # keycap emoji: 1️⃣ -> 1
    s = s.replace("\ufe0f", "").replace("\u20e3", "")
    # unify spaces/newlines for more stable regex behavior
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    return s


def _normalize_name(raw: str) -> str:
    if not raw:
        return ""
    # 昵称里常见“（休息）/（暂停）”之类状态，输出模板只保留名称本体
    cleaned = re.sub(r"[（(].*?[)）]", "", raw).strip()
    return cleaned or raw.strip()


def _extract_prices_from_price_line(raw: str) -> tuple[str, str]:
    if not raw:
        return "", ""
    line = _extract_line_value(raw, ["价位", "价格"])
    if not line:
        return "", ""
    # 95 4P 这类写法合并为 954P
    compact = re.sub(r"(?<=\d)\s+(?=\d)", "", line)
    nums = re.findall(r"(\d{2,5})\s*[Pp]?", compact)
    if not nums:
        return "", ""
    if len(nums) == 1:
        return nums[0], ""
    return nums[0], nums[1]


def _extract_line_value(text: str, labels: list[str]) -> str:
    for label in labels:
        # 允许行首有 emoji/符号，例如：🌺昵称：小布
        pattern = rf"{re.escape(label)}\s*[：:]\s*([^\n\r]+)"
        m = re.search(pattern, text, flags=re.IGNORECASE)
        if m:
            return m.group(1).strip()
    return ""


def extract_profile_fields(text: str) -> dict[str, str]:
    raw = _normalize_text_for_parse(text)
    fields: dict[str, str] = {
        "review_count": "",
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

    m = re.search(r"(\d+)\s*条车评", raw)
    if m:
        fields["review_count"] = m.group(1)
    for key, pattern in (
        ("good_rate", r"好评\s*([0-9]{1,3}%?)"),
        ("mid_rate", r"中评\s*([0-9]{1,3}%?)"),
        ("bad_rate", r"差评\s*([0-9]{1,3}%?)"),
        ("photo_score", r"\|?\s*人照\s*([0-9]+)"),
        ("service_score", r"\|?\s*服务\s*([0-9]+)"),
        ("face_score", r"\|?\s*颜值\s*([0-9]+)"),
        ("attitude_score", r"\|?\s*态度\s*([0-9]+)"),
        ("body_score", r"\|?\s*身材\s*([0-9]+)"),
        ("env_score", r"\|?\s*环境\s*([0-9]+)"),
    ):
        mm = re.search(pattern, raw)
        if mm:
            fields[key] = mm.group(1).strip()

    fields["name"] = _normalize_name(_extract_line_value(raw, ["名字", "姓名", "昵称", "呢称"]))
    fields["age"] = _extract_line_value(raw, ["年龄", "岁"])
    fields["height"] = _extract_line_value(raw, ["身高"])
    fields["weight"] = _extract_line_value(raw, ["体重"])
    fields["cup"] = _extract_line_value(raw, ["罩杯"])
    fields["project"] = _extract_line_value(raw, ["项目", "项目分"])
    fields["price_once"] = _extract_line_value(raw, ["一次价格", "单次价格", "一次"])
    fields["price_twice"] = _extract_line_value(raw, ["两次价格", "双次价格", "两次"])
    fields["region"] = _extract_line_value(raw, ["地区", "区域", "位置", "定位"])
    fields["telegram"] = _extract_line_value(raw, ["电报", "tg", "telegram"])
    fields["channel"] = _extract_line_value(raw, ["频道", "频道链接"])
    fields["duplex"] = _extract_line_value(raw, ["双向", "机器人", "bot"])

    if not fields["price_once"] and not fields["price_twice"]:
        once, twice = _extract_prices_from_price_line(raw)
        fields["price_once"] = once
        fields["price_twice"] = twice

    # 容错：无显式标签时，从全文补抓。
    if not fields["channel"]:
        fields["channel"] = _first_match(
            raw,
            [
                r"(https?://t\.me/[A-Za-z0-9_]+)",
                r"(t\.me/[A-Za-z0-9_]+)",
            ],
        )
    if not fields["duplex"]:
        fields["duplex"] = _first_match(
            raw,
            [
                r"(?:双向|机器人|bot)\s*[：:]\s*([^\n\r]+)",
                r"(@[A-Za-z0-9_]*bot)\b",
            ],
        )
    if not fields["telegram"]:
        tg = _first_match(
            raw,
            [
                r"(?:电报|tg|telegram|联系方式)\s*[：:]\s*([^\n\r]+)",
                r"(@[A-Za-z0-9_]{5,})",
            ],
        )
        if tg and fields["duplex"] and tg == fields["duplex"]:
            tg = ""
        fields["telegram"] = tg
    return fields


def render_publish_template(text: str, template: str) -> str:
    if not text:
        return ""
    fields = extract_profile_fields(text)
    fields["raw"] = text
    safe = {k: (v or "") for k, v in fields.items()}
    try:
        out = template.format_map(safe).strip()
        return out
    except Exception:
        return text
