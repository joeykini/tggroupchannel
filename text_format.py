"""频道结构化配文的自动排版（评分区、资料区）。"""

from __future__ import annotations

import re

# 资料行：名字：xxx / 名字: xxx
_FIELD_LINE = re.compile(
    r"^([\u4e00-\u9fffA-Za-z]{1,8})\s*[:：]\s*(.+)$",
    re.MULTILINE,
)
# 含 | 的评分明细行
_RATING_LINE = re.compile(r"\|.+")


def normalize_caption(text: str) -> str:
    if not text or not text.strip():
        return text
    t = text.replace("\r\n", "\n").strip()
    # 合并多余空行
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t


def format_profile_caption(text: str) -> str:
    """
    针对「评分 + 多行 | 分隔 + 名字年龄…」类帖子做排版。
    AI 复写前后都可调用，保证段落清晰。
    """
    t = normalize_caption(text)
    if not t:
        return t

    lines = t.split("\n")
    blocks: list[str] = []
    current: list[str] = []

    def flush() -> None:
        if not current:
            return
        blocks.append("\n".join(current))
        current.clear()

    for line in lines:
        stripped = line.strip()
        if not stripped:
            flush()
            continue
        # 评分明细块（含 |）
        if _RATING_LINE.search(stripped):
            flush()
            current.append(stripped)
            continue
        # 综合评分标题行（含「评分」或 📊）
        if "评分" in stripped or "📊" in stripped:
            flush()
            current.append(stripped)
            continue
        # 资料字段行
        if _FIELD_LINE.match(stripped):
            flush()
            current.append(stripped)
            continue
        current.append(stripped)

    flush()
    return "\n\n".join(blocks) if blocks else t
