"""频道监听：抓取相册+配文 → AI 改写 → 待发布池/批量发布。"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path

from telethon import TelegramClient, events
from telethon.errors import ChatAdminRequiredError, SessionPasswordNeededError
from telethon.tl.types import DocumentAttributeVideo, Message, MessageMediaDocument, MessageMediaPhoto

from ai_rewrite import rewrite_text
from config import ROOT, Settings, load_settings
from filters import is_blocked_text, strip_source_references
from message_bundle import MessageBundle, from_messages
from notifier import push_bot_message
from post_store import (
    StoredPost,
    add_or_ignore,
    exists,
    get,
    list_pending_ids,
    update as store_update,
)
from template_extract import render_publish_template
from text_format import format_profile_caption, normalize_caption
from tg_client import create_client, session_path

log = logging.getLogger("bridge")

LogCallback = Callable[[str, str], None]
PostCallback = Callable[[StoredPost], None]

MEDIA_DIR = ROOT / "data" / "media"
MEDIA_DIR.mkdir(parents=True, exist_ok=True)


class ChannelBridge:
    def __init__(
        self,
        settings: Settings | None = None,
        on_log: LogCallback | None = None,
        on_post: PostCallback | None = None,
    ) -> None:
        self.settings = settings or load_settings()
        self.on_log = on_log
        self.on_post = on_post
        self._client: TelegramClient | None = None
        self._task: asyncio.Task | None = None
        self._running = False
        self._target_check_cache: tuple[str, bool, str] | None = None
        self._target_invalid_notified = False

    @property
    def running(self) -> bool:
        return self._running

    def _emit(self, level: str, msg: str) -> None:
        getattr(log, level.lower(), log.info)(msg)
        if self.on_log:
            self.on_log(level, msg)

    def reload_settings(self) -> None:
        self.settings = load_settings()
        self._target_check_cache = None
        self._target_invalid_notified = False

    async def _notify(self, text: str) -> None:
        await push_bot_message(self.settings, text)

    async def _get_send_client(self) -> TelegramClient:
        if self._client and self._client.is_connected():
            return self._client
        client = create_client(self.settings)
        await client.connect()
        if not await client.is_user_authorized():
            await client.disconnect()
            raise RuntimeError("当前账号未登录，请先在网页完成手机号登录")
        return client

    async def is_logged_in(self) -> bool:
        self.reload_settings()
        if self._client and self._client.is_connected():
            try:
                return await self._client.is_user_authorized()
            except Exception:
                return False
        if not session_path(self.settings).exists():
            return False
        client = create_client(self.settings)
        try:
            await asyncio.wait_for(client.connect(), timeout=20)
            return await client.is_user_authorized()
        except Exception:
            return False
        finally:
            if client.is_connected():
                await client.disconnect()

    async def login_send_code(self, phone: str) -> dict:
        self.settings = load_settings()
        if self._client and self._client.is_connected():
            await self._client.disconnect()
        client = create_client(self.settings)
        await client.connect()
        self._client = client
        sent = await client.send_code_request(phone)
        return {"phone_code_hash": sent.phone_code_hash}

    async def login_confirm(
        self,
        phone: str,
        code: str,
        phone_code_hash: str,
        password: str | None = None,
    ) -> dict:
        if not self._client:
            raise RuntimeError("请先调用 login_send_code")
        try:
            await self._client.sign_in(phone, code, phone_code_hash=phone_code_hash)
        except SessionPasswordNeededError:
            if not password:
                return {"need_2fa": True}
            await self._client.sign_in(password=password)
        me = await self._client.get_me()
        await self._client.disconnect()
        self._client = None
        return {"ok": True, "user": f"{me.first_name} (@{me.username or '无用户名'})"}

    def _matches_filter(self, text: str) -> bool:
        if not self.settings.filter_keywords:
            return True
        lower = (text or "").lower()
        return any(kw in lower for kw in self.settings.filter_keywords)

    def _should_process(self, bundle: MessageBundle) -> tuple[bool, str]:
        if self.settings.require_media and not bundle.has_media:
            return False, "已设置仅抓取带媒体帖子"
        if not bundle.caption and not bundle.has_media:
            return False, "空内容"
        if bundle.caption and not self._matches_filter(bundle.caption):
            return False, "未命中关键词过滤"
        blocked, reason = is_blocked_text(bundle.caption or "", self.settings.blocked_keywords)
        if blocked:
            return False, reason
        return True, ""

    async def _download_previews(
        self, client: TelegramClient, bundle: MessageBundle, post_id: str
    ) -> list[dict[str, str]]:
        items: list[dict[str, str]] = []
        media_msgs = [m for m in bundle.messages if m.media]
        for i, msg in enumerate(media_msgs):
            dest = MEDIA_DIR / f"{post_id}_{i}"
            try:
                downloaded = await client.download_media(msg, file=str(dest))
                if downloaded:
                    media_type = "file"
                    if isinstance(msg.media, MessageMediaPhoto):
                        media_type = "image"
                    elif isinstance(msg.media, MessageMediaDocument):
                        mime = (getattr(msg.file, "mime_type", "") or "").lower()
                        attrs = getattr(msg.document, "attributes", []) or []
                        if mime.startswith("video/") or any(
                            isinstance(a, DocumentAttributeVideo) for a in attrs
                        ):
                            media_type = "video"
                        elif mime.startswith("image/"):
                            media_type = "image"
                    items.append(
                        {
                            "type": media_type,
                            "path": f"/media/{Path(downloaded).name}",
                        }
                    )
            except Exception as e:
                self._emit("ERROR", f"预览图下载失败: {e}")
        return items

    async def _build_final_text(self, raw_caption: str) -> tuple[str, str, str, str]:
        original = normalize_caption(raw_caption)
        cleaned = strip_source_references(original) if self.settings.strip_source_refs else original
        cleaned = format_profile_caption(cleaned)
        rewritten = cleaned
        if rewritten and self.settings.ai_enabled:
            rewritten = await rewrite_text(rewritten, self.settings)
        else:
            rewritten = format_profile_caption(rewritten)
            if self.settings.template_extract_enabled and self.settings.publish_template.strip():
                rewritten = render_publish_template(rewritten, self.settings.publish_template)

        parts: list[str] = []
        if self.settings.forward_header:
            parts.append(self.settings.forward_header)
        if rewritten:
            parts.append(rewritten)
        final = "\n\n".join(parts) if parts else ""
        return original, cleaned, rewritten, final

    async def _check_target_channel(self, client: TelegramClient) -> tuple[bool, str]:
        target = (self.settings.target_channel or "").strip()
        if not target:
            return False, "未配置目标频道"
        if self._target_check_cache and self._target_check_cache[0] == target:
            return self._target_check_cache[1], self._target_check_cache[2]
        try:
            await client.get_entity(target)
            self._target_check_cache = (target, True, "")
            self._target_invalid_notified = False
            return True, ""
        except Exception as e:
            reason = f"目标频道无效: {target} ({e})"
            self._target_check_cache = (target, False, reason)
            return False, reason

    async def validate_target_channel(self) -> dict:
        """供网页按钮手动测试目标频道可用性。"""
        self.reload_settings()
        if not self.settings.target_channel:
            return {"ok": False, "reason": "未配置目标频道"}
        errs = self.settings.validate_for_capture()
        if errs:
            return {"ok": False, "reason": "; ".join(errs)}
        client = await self._get_send_client()
        own_temp_client = client is not self._client
        try:
            ok, reason = await self._check_target_channel(client)
            return {"ok": ok, "reason": reason}
        finally:
            if own_temp_client and client.is_connected():
                await client.disconnect()

    async def _send_from_paths(
        self, client: TelegramClient, media_items: list[dict[str, str]], caption: str
    ) -> None:
        files = [str((MEDIA_DIR / Path(i.get("path", "")).name)) for i in media_items]
        existing = [f for f in files if Path(f).exists()]
        if existing:
            await client.send_file(self.settings.target_channel, existing, caption=caption or None)
        elif caption:
            await client.send_message(self.settings.target_channel, caption, link_preview=False)

    async def _send_bundle(
        self,
        client: TelegramClient,
        bundle: MessageBundle,
        caption: str,
    ) -> None:
        s = self.settings
        if s.copy_without_forward_tag:
            media_msgs = [m for m in bundle.messages if m.media]
            if media_msgs:
                files = [m.media for m in media_msgs]
                await client.send_file(
                    s.target_channel,
                    files,
                    caption=caption or None,
                )
            elif caption:
                await client.send_message(s.target_channel, caption, link_preview=False)
            return
        await client.forward_messages(s.target_channel, bundle.messages)

    async def _process_bundle(self, client: TelegramClient, bundle: MessageBundle) -> None:
        post_id = bundle.post_key
        if exists(post_id):
            return

        ok, reason = self._should_process(bundle)
        status = "captured" if ok else "blocked"
        stored = StoredPost(
            id=post_id,
            source_key=post_id,
            chat_id=bundle.chat_id,
            message_ids=bundle.message_ids,
            text_original=bundle.caption or "",
            media_count=bundle.media_count,
            status=status,
            blocked_reason="" if ok else reason,
        )
        inserted = add_or_ignore(stored)
        if not inserted:
            return

        if not ok:
            self._emit("INFO", f"已过滤 {post_id}: {reason}")
            return

        try:
            media_items = await self._download_previews(client, bundle, post_id)
            image_paths = [i["path"] for i in media_items if i.get("type") == "image"]
            store_update(
                post_id,
                image_paths=image_paths,
                media_items=media_items,
                media_count=bundle.media_count,
            )

            original, cleaned, rewritten, final = await self._build_final_text(bundle.caption)
            store_update(
                post_id,
                text_original=original,
                text_cleaned=cleaned,
                text_formatted=rewritten,
                text_final=final,
                status="rewritten",
            )

            if not self.settings.auto_publish:
                store_update(post_id, status="pending")
                self._emit("INFO", f"待发布: {post_id}")
                return

            ok_target, target_reason = await self._check_target_channel(client)
            if not ok_target:
                store_update(post_id, status="pending", error=target_reason)
                if not self._target_invalid_notified:
                    self._target_invalid_notified = True
                    self._emit("ERROR", target_reason)
                return

            await self._send_bundle(client, bundle, final)
            store_update(
                post_id,
                status="sent",
                published_at=datetime.now(timezone.utc).isoformat(),
            )
            self._emit("INFO", f"已发布 {post_id} -> {self.settings.target_channel}")
            await self._notify(f"✅ 已发布\n{post_id}\n目标: {self.settings.target_channel}")
        except ChatAdminRequiredError:
            store_update(post_id, status="failed", error="目标频道无发消息权限")
            self._emit("ERROR", "目标频道无发消息权限")
            await self._notify("❌ 发布失败：目标频道无发消息权限")
        except Exception as e:
            msg = str(e)
            if "ResolveUsernameRequest" in msg or "username is unacceptable" in msg:
                store_update(post_id, status="pending", error=f"目标频道无效: {msg}")
                self._emit("ERROR", f"目标频道无效，已转待发布: {msg}")
                return
            store_update(post_id, status="failed", error=msg)
            self._emit("ERROR", f"处理失败: {msg}")
            await self._notify(f"❌ 处理失败: {msg}")

    async def publish_posts(self, post_ids: list[str]) -> dict:
        self.reload_settings()
        errs = self.settings.validate_for_publish()
        if errs:
            raise ValueError("; ".join(errs))
        if not post_ids:
            return {"published": 0, "failed": 0}
        client = await self._get_send_client()
        own_temp_client = client is not self._client

        published = 0
        failed = 0
        try:
            for pid in post_ids:
                post = get(pid)
                if not post:
                    failed += 1
                    continue
                try:
                    text = post.text_final or post.text_formatted or post.text_cleaned or post.text_original
                    media_items = post.media_items or [{"type": "image", "path": p} for p in post.image_paths]
                    await self._send_from_paths(client, media_items, text)
                    store_update(
                        pid,
                        status="sent",
                        published_at=datetime.now(timezone.utc).isoformat(),
                        error="",
                    )
                    published += 1
                except Exception as e:
                    store_update(pid, status="failed", error=str(e))
                    failed += 1
            if published:
                await self._notify(f"📢 批量发布完成：成功 {published}，失败 {failed}")
        finally:
            if own_temp_client and client.is_connected():
                await client.disconnect()
        return {"published": published, "failed": failed}

    async def publish_all_pending(self) -> dict:
        return await self.publish_posts(list_pending_ids(limit=500))

    async def fetch_recent_once(self, limit_per_channel: int = 30, since_hours: int | None = None) -> dict:
        self.reload_settings()
        errs = self.settings.validate_for_capture()
        if errs:
            raise ValueError("; ".join(errs))
        client = await self._get_send_client()
        own_temp_client = client is not self._client
        handled = 0
        try:
            for source in self.settings.source_channels:
                grouped: dict[int, list[Message]] = defaultdict(list)
                singles: list[Message] = []
                since_dt = None
                if since_hours and since_hours > 0:
                    since_dt = datetime.now(timezone.utc) - timedelta(hours=since_hours)
                scan_limit = limit_per_channel if not since_dt else max(limit_per_channel * 5, 100)
                async for msg in client.iter_messages(source, limit=scan_limit):
                    if since_dt and msg.date and msg.date < since_dt:
                        break
                    if msg.grouped_id:
                        grouped[int(msg.grouped_id)].append(msg)
                    else:
                        singles.append(msg)
                    if len(grouped) + len(singles) >= limit_per_channel:
                        break
                for msgs in grouped.values():
                    bundle = from_messages(msgs, int(msgs[0].chat_id or 0))
                    await self._process_bundle(client, bundle)
                    handled += 1
                for msg in singles:
                    bundle = from_messages([msg], int(msg.chat_id or 0))
                    await self._process_bundle(client, bundle)
                    handled += 1
            self._emit("INFO", f"定时抓取完成，处理 {handled} 个帖子")
        finally:
            if own_temp_client and client.is_connected():
                await client.disconnect()
        return {"handled": handled}

    def _register_handlers(self, client: TelegramClient) -> None:
        chats = self.settings.source_channels

        @client.on(events.NewMessage(chats=chats))
        async def on_new_message(event: events.NewMessage.Event) -> None:
            msg = event.message
            if msg.grouped_id:
                return
            bundle = from_messages([msg], int(event.chat_id or 0))
            await self._process_bundle(client, bundle)

        @client.on(events.Album(chats=chats))
        async def on_album(event: events.Album.Event) -> None:
            bundle = from_messages(list(event.messages), int(event.chat_id or 0))
            await self._process_bundle(client, bundle)

    async def _verify(self, client: TelegramClient) -> None:
        for ch in self.settings.source_channels:
            entity = await client.get_entity(ch)
            self._emit("INFO", f"源频道 OK: {ch} ({getattr(entity, 'title', entity.id)})")
        target = await client.get_entity(self.settings.target_channel)
        self._emit(
            "INFO",
            f"目标频道 OK: {self.settings.target_channel} ({getattr(target, 'title', target.id)})",
        )

    async def start(self) -> None:
        if self._running:
            return
        self.reload_settings()
        errs = self.settings.validate_for_capture()
        if errs:
            raise ValueError("; ".join(errs))

        self._client = create_client(self.settings)
        self._register_handlers(self._client)
        await self._client.connect()
        if not await self._client.is_user_authorized():
            await self._client.disconnect()
            self._client = None
            raise ValueError("当前会话未登录，请先在网页发送验证码并完成登录")
        me = await self._client.get_me()
        self._emit("INFO", f"已登录 {me.first_name} (@{me.username or '-'})")
        await self._verify(self._client)
        self._emit("INFO", f"监听中: {', '.join(self.settings.source_channels)}")
        self._running = True

        if self.settings.fetch_on_start:
            await self.fetch_recent_once(limit_per_channel=self.settings.daily_fetch_limit)

        async def _run() -> None:
            try:
                await self._client.run_until_disconnected()
            finally:
                self._running = False

        self._task = asyncio.create_task(_run())

    async def stop(self) -> None:
        if self._client and self._client.is_connected():
            await self._client.disconnect()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._running = False
        self._emit("INFO", "已停止监听")
