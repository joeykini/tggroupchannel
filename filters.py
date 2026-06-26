"""内容过滤：广告拦截与源站信息清理。"""

from __future__ import annotations

import re

SOURCE_REF_PATTERNS = [
    re.compile(r"https?://t\.me/[A-Za-z0-9_/\-]+", re.IGNORECASE),
    re.compile(r"(?<!\w)@[A-Za-z0-9_]{4,}(?!\w)"),
    re.compile(r"(频道|电报|飞机|直连|联系|客服)\s*[:：]\s*.+", re.IGNORECASE),
]

MARKETING_PATTERNS = [
    re.compile(r"(vpn|机场|梯子|翻墙|节点|代理|clash|v2ray|trojan|ssr)", re.IGNORECASE),
    re.compile(r"(广告|推广|招商|合作|返利|博彩|菠菜|代发|引流)", re.IGNORECASE),
    re.compile(r"商\s*[kK]", re.IGNORECASE),
]


def is_blocked_text(text: str, blocked_keywords: list[str]) -> tuple[bool, str]:
    raw = (text or "").lower()
    for keyword in blocked_keywords:
        kw = keyword.strip().lower()
        if kw and kw in raw:
            return True, f"命中屏蔽词: {kw}"
    for pattern in MARKETING_PATTERNS:
        m = pattern.search(text or "")
        if m:
            return True, f"命中广告规则: {m.group(0)}"
    return False, ""


def strip_source_references(text: str) -> str:
    cleaned = text or ""
    for pattern in SOURCE_REF_PATTERNS:
        cleaned = pattern.sub("", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()
