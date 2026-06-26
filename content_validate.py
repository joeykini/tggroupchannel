"""抓取前校验：广告过滤 + 模板字段是否有效。"""

from __future__ import annotations

import re

from config import Settings
from filters import is_blocked_text
from person_registry import fields_from_text, person_id_from_text, render_fields_template


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


def validate_for_capture(text: str, settings: Settings) -> tuple[bool, str]:
    """广告 / 非模板内容在入库前拦截。"""
    caption = (text or "").strip()
    if not caption:
        return False, "空内容"

    blocked, reason = is_blocked_text(caption, settings.blocked_keywords)
    if blocked:
        return False, reason

    pid = person_id_from_text(caption)
    if not pid:
        return False, "缺少可识别的名字+地区"

    fields = fields_from_text(caption)
    rendered = render_fields_template(settings.publish_template, fields)
    if not rendered.strip():
        return False, "模板渲染为空"

    meaningful = _meaningful_lines(rendered)
    if len(meaningful) < 2:
        return False, "模板有效字段不足（至少名字+一项资料）"

    return True, ""
