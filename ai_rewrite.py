"""通过 OpenAI 兼容 API 对文案做 AI 复写。"""

from __future__ import annotations

import logging

import httpx

from config import Settings
from text_format import format_profile_caption, normalize_caption

log = logging.getLogger("ai-rewrite")

DEFAULT_PROFILE_PROMPT = """你是 Telegram 频道文案编辑。输入是带相册的频道帖子配文，常见结构为：
- 第一行：车评数量与综合评分（含 📊）
- 中间多行：好评/中评/差评占比，以及人照、颜值、身材、服务、态度、环境等分数，用 | 分隔
- 后面：名字、年龄、身高、体重、罩杯、价格、地区、@用户名、t.me 链接等

要求：
1. 必须保留所有数字、百分比、评分、链接、@用户名，禁止编造新事实
2. 保持上述分区与 | 对齐风格，字段行用「名字：值」形式
3. 只润色措辞，使更通顺自然
4. 只输出最终配文纯文本，不要 markdown 代码块，不要解释"""


async def rewrite_text(text: str, settings: Settings) -> str:
    text = normalize_caption(text)
    if not text.strip():
        return text
    if not settings.ai_enabled or not settings.openai_api_key:
        return format_profile_caption(text)

    prompt = settings.ai_prompt.strip() or DEFAULT_PROFILE_PROMPT
    url = f"{settings.openai_base_url}/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.openai_api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": settings.openai_model,
        "messages": [
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": (
                    "请复写以下 Telegram 频道配文，保持结构与数据不变：\n\n" + text
                ),
            },
        ],
        "temperature": 0.5,
    }

    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
        out = data["choices"][0]["message"]["content"].strip()
        out = format_profile_caption(out)
        log.info("AI 复写完成 (%d -> %d 字)", len(text), len(out))
        return out or format_profile_caption(text)
    except Exception:
        log.exception("AI 复写失败，使用排版后的原文")
        return format_profile_caption(text)
