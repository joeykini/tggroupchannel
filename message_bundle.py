"""把 Telegram 消息（单条或相册）整理为一个图文帖子单元。"""

from __future__ import annotations

from dataclasses import dataclass, field

from telethon.tl.types import Message, MessageMediaPhoto


@dataclass
class MessageBundle:
    chat_id: int
    message_ids: list[int]
    grouped_id: int | None
    messages: list[Message] = field(repr=False)
    caption: str = ""
    has_media: bool = False
    media_count: int = 0

    @property
    def post_key(self) -> str:
        gid = self.grouped_id or self.message_ids[0]
        return f"{self.chat_id}_{gid}"


def extract_caption(messages: list[Message]) -> str:
    """相册配文可能挂在组内任意一条消息上，取最长正文。"""
    texts = [(m.message or "").strip() for m in messages if (m.message or "").strip()]
    if not texts:
        return ""
    return max(texts, key=len)


def has_photo_media(messages: list[Message]) -> bool:
    for m in messages:
        if m.media and isinstance(m.media, MessageMediaPhoto):
            return True
        if m.media:
            return True
    return False


def from_messages(messages: list[Message], chat_id: int) -> MessageBundle:
    messages = sorted(messages, key=lambda m: m.id)
    grouped_id = messages[0].grouped_id
    media_msgs = [m for m in messages if m.media]
    return MessageBundle(
        chat_id=chat_id,
        message_ids=[m.id for m in messages],
        grouped_id=grouped_id,
        messages=messages,
        caption=extract_caption(messages),
        has_media=bool(media_msgs),
        media_count=len(media_msgs),
    )
