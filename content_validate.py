"""抓取前校验：广告过滤 + 本地区限制 + 模板字段是否有效。"""

from __future__ import annotations

import re

from config import Settings
from filters import is_blocked_text
from person_registry import (
    fields_from_text,
    is_allowed_region,
    is_contact_complete,
    person_id_from_text,
    render_fields_template,
)


def _meaningful_lines(rendered: str) -> list[str]:
    lines: list[str] = []
    for line in (rendered or "").splitlines():
        s = line.strip()
        if not s:
            continue
        if re.match(r"^[:：|\s📊]+$", s):
            continue
        m = re.match(r"^[^:：]+[:：]\s*(.*)$", s)
        if m and not (m.group(1) or "").strip():
            continue
        lines.append(s)
    return lines


def _person_check_text(text: str, fields: dict[str, str]) -> str:
    parts = [
        text or "",
        fields.get("name", ""),
        fields.get("project", ""),
        fields.get("region", ""),
    ]
    return "\n".join(p for p in parts if p.strip())


def validate_person_content(
    text: str,
    fields: dict[str, str] | None,
    settings: Settings,
) -> tuple[bool, str]:
    """广告 / 非本地区 / 无效资料在入库前拦截。"""
    caption = (text or "").strip()
    f = fields or fields_from_text(caption)

    blocked, reason = is_blocked_text(_person_check_text(caption, f), settings.blocked_keywords)
    if blocked:
        return False, reason

    if settings.region_filter_enabled:
        region = f.get("region") or ""
        ok, region_reason = is_allowed_region(region, settings.allowed_regions)
        if not ok:
            return False, region_reason

    return True, ""


def validate_for_capture(text: str, settings: Settings) -> tuple[bool, str]:
    """广告 / 非模板内容在入库前拦截。"""
    caption = (text or "").strip()
    if not caption:
        return False, "空内容"

    fields = fields_from_text(caption)
    ok, reason = validate_person_content(caption, fields, settings)
    if not ok:
        return False, reason

    pid = person_id_from_text(caption)
    if not pid:
        return False, "缺少可识别的名字+地区"

    if not is_contact_complete(fields):
        # 电报/频道不全：仍允许抓取入人员库，等待手动补全后发布
        return True, ""

    rendered = render_fields_template(settings.publish_template, fields)
    if not rendered.strip():
        return False, "模板渲染为空"

    meaningful = _meaningful_lines(rendered)
    if len(meaningful) < 2:
        return False, "模板有效字段不足（至少名字+一项资料）"

    return True, ""
