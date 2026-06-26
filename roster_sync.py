"""出勤名单同步：抓取 → 在岗校验 → 合并发布 → 不在岗删帖。"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from telethon import TelegramClient
from telethon.tl.types import User

from config import ROOT, Settings
from person_registry import (
    count_filled_fields,
    fields_from_text,
    merge_profile_fields,
    person_id_from_text,
    render_fields_template,
)
from roster_store import (
    PersonRecord,
    get_latest_active_person_ids,
    get_person,
    list_persons,
    list_posts_by_person,
    mark_person_posts,
    save_roster_snapshot,
    update_person,
    upsert_person,
)
from post_store import _connect, _lock, get, mark_posts_status, update as store_update
from roster_parse import RosterEntry, parse_roster_text

log = logging.getLogger("roster-sync")
MEDIA_DIR = ROOT / "data" / "media"


def _is_roster_bot(sender: Any, bot_names: list[str]) -> bool:
    if not sender:
        return False
    if getattr(sender, "bot", False):
        if not bot_names:
            return True
        names = {n.lower().lstrip("@") for n in bot_names}
        for attr in ("username", "first_name"):
            val = (getattr(sender, attr, "") or "").lower()
            if val in names or any(n in val for n in names):
                return True
        return False
    if isinstance(sender, User):
        title = (sender.first_name or "").lower()
        return any(n.lower() in title for n in bot_names)
    return False


def _roster_text_ok(text: str) -> bool:
    return bool(text and "【" in text and ("🟢" in text or "🔴" in text or "在岗" in text))


async def fetch_roster_text(
    client: TelegramClient,
    group: str,
    keyword: str,
    bot_names: list[str],
    wait_seconds: float = 8.0,
) -> tuple[str, int]:
    entity = await client.get_entity(group)

    async for msg in client.iter_messages(entity, limit=40):
        text = msg.message or ""
        if not _roster_text_ok(text):
            continue
        sender = await msg.get_sender()
        if _is_roster_bot(sender, bot_names):
            return text, int(msg.id or 0)

    sent = await client.send_message(entity, keyword)
    sent_id = int(sent.id or 0)
    deadline = asyncio.get_event_loop().time() + wait_seconds
    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(1.5)
        async for msg in client.iter_messages(entity, limit=15):
            if int(msg.id or 0) <= sent_id:
                continue
            text = msg.message or ""
            if not _roster_text_ok(text):
                continue
            sender = await msg.get_sender()
            if _is_roster_bot(sender, bot_names):
                return text, int(msg.id or 0)

    raise RuntimeError(f"群组 {group} 未收到出勤 Bot 回复，请确认已加入群且 Bot 名称配置正确")


def _score_post(post: dict[str, Any]) -> tuple[int, int, str]:
    text = post.get("text_original") or post.get("text_final") or ""
    fields = fields_from_text(text)
    media = int(post.get("media_count") or 0)
    filled = count_filled_fields(fields)
    created = post.get("created_at") or ""
    return (media, filled, created)


def pick_canonical_post(posts: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not posts:
        return None
    return max(posts, key=_score_post)


class RosterSync:
    def __init__(self, settings: Settings, media_dir: Any, emit=None) -> None:
        self.settings = settings
        self.media_dir = media_dir
        self._emit = emit or (lambda level, msg: getattr(log, level.lower(), log.info)(msg))

    def roster_groups(self) -> list[tuple[str, str, str]]:
        pairs: list[tuple[str, str, str]] = []
        if self.settings.roster_group_1:
            pairs.append(("group1", self.settings.roster_group_1, self.settings.roster_channel_1))
        if self.settings.roster_group_2:
            pairs.append(("group2", self.settings.roster_group_2, self.settings.roster_channel_2))
        return pairs

    async def fetch_all_rosters(self, client: TelegramClient) -> dict[str, list[RosterEntry]]:
        active: dict[str, RosterEntry] = {}
        bot_names = self.settings.roster_bot_names
        keyword = self.settings.roster_trigger_keyword or "出勤"

        for group_key, group_chat, _channel in self.roster_groups():
            try:
                text, msg_id = await fetch_roster_text(client, group_chat, keyword, bot_names)
                entries = parse_roster_text(text)
                if not entries:
                    self._emit("WARNING", f"{group_chat} 出勤名单解析为空")
                    continue
                save_roster_snapshot(
                    group_key,
                    group_chat,
                    msg_id,
                    text,
                    [{"region": e.region, "name": e.name, "status": e.status, "person_id": e.person_id} for e in entries],
                )
                for e in entries:
                    active[e.person_id] = e
                self._emit("INFO", f"出勤快照 {group_chat}: {len(entries)} 人")
                await asyncio.sleep(2)
            except Exception as e:
                self._emit("ERROR", f"抓取出勤失败 {group_chat}: {e}")

        return active

    async def _delete_target_messages(
        self, client: TelegramClient, message_ids: list[int]
    ) -> int:
        if not message_ids or not self.settings.target_channel:
            return 0
        try:
            await client.delete_messages(self.settings.target_channel, message_ids)
            return len(message_ids)
        except Exception as e:
            self._emit("ERROR", f"删除目标帖失败: {e}")
            return 0

    async def _collect_target_ids_for_person(self, person_id: str) -> list[int]:
        ids: set[int] = set()
        person = get_person(person_id)
        if person:
            ids.update(person.target_message_ids)
        for post in list_posts_by_person(person_id):
            for mid in post.get("target_message_ids") or []:
                ids.add(int(mid))
        return sorted(ids)

    async def remove_inactive_person(
        self, client: TelegramClient, person_id: str, person: PersonRecord | None = None
    ) -> int:
        person = person or get_person(person_id)
        target_ids = await self._collect_target_ids_for_person(person_id)
        deleted = await self._delete_target_messages(client, target_ids)
        update_person(person_id, roster_status="inactive", target_message_ids=[], canonical_post_id="")
        mark_person_posts(person_id, "inactive", "不在出勤名单")
        if person and person.canonical_post_id:
            store_update(person.canonical_post_id, status="inactive", error="不在出勤名单", target_message_ids=[])
        return deleted

    def _build_final_caption(self, fields: dict[str, str]) -> str:
        rendered = render_fields_template(self.settings.publish_template, fields)
        parts: list[str] = []
        if self.settings.forward_header:
            parts.append(self.settings.forward_header)
        if rendered:
            parts.append(rendered)
        return "\n\n".join(parts) if parts else ""

    async def publish_person(
        self,
        client: TelegramClient,
        person_id: str,
        active_entries: dict[str, RosterEntry] | None = None,
    ) -> bool:
        if not self.settings.target_channel:
            return False

        person = get_person(person_id)
        posts_raw = list_posts_by_person(person_id)
        if not posts_raw:
            return False

        merged = dict(person.merged_fields) if person else {}
        for post in posts_raw:
            merged = merge_profile_fields(merged, fields_from_text(post.get("text_original") or ""))

        canonical = pick_canonical_post(posts_raw)
        if not canonical:
            return False

        roster_status = "unknown"
        if active_entries and person_id in active_entries:
            roster_status = active_entries[person_id].status
        else:
            latest = get_latest_active_person_ids()
            if person_id in latest:
                roster_status = latest[person_id]
            elif person:
                roster_status = person.roster_status

        upsert_person(
            person_id,
            merged.get("name", ""),
            merged.get("region", ""),
            merged,
            roster_status=roster_status,
        )

        caption = self._build_final_caption(merged)
        if not caption:
            return False

        old_ids = await self._collect_target_ids_for_person(person_id)
        if old_ids:
            await self._delete_target_messages(client, old_ids)

        from pathlib import Path

        post = get(canonical["id"])
        if not post:
            return False

        media_items = post.media_items or [{"type": "image", "path": p} for p in post.image_paths]
        files = [str(MEDIA_DIR / Path(i.get("path", "")).name) for i in media_items]
        existing = [f for f in files if Path(f).exists()]

        target_ids: list[int] = []
        if existing:
            sent = await client.send_file(self.settings.target_channel, existing, caption=caption)
            if isinstance(sent, list):
                target_ids = [m.id for m in sent if m and getattr(m, "id", None)]
            elif sent and getattr(sent, "id", None):
                target_ids = [sent.id]
        elif caption:
            sent = await client.send_message(self.settings.target_channel, caption, link_preview=False)
            if sent and getattr(sent, "id", None):
                target_ids = [sent.id]

        dup_ids = [p["id"] for p in posts_raw if p["id"] != canonical["id"]]
        if dup_ids:
            mark_posts_status(dup_ids, "duplicate", "同一人合并发布")

        store_update(
            canonical["id"],
            status="sent",
            text_final=caption,
            text_formatted=render_fields_template(self.settings.publish_template, merged),
            target_message_ids=target_ids,
            person_id=person_id,
        )
        update_person(
            person_id,
            merged_fields=merged,
            canonical_post_id=canonical["id"],
            target_message_ids=target_ids,
            roster_status=roster_status,
            preview_text=caption,
            library_status="published",
        )
        self._emit("INFO", f"已发布/更新 {merged.get('name')} ({person_id}) -> {self.settings.target_channel}")
        return True

    async def refresh_person_library(
        self,
        person_id: str,
        active_entries: dict[str, RosterEntry] | None = None,
    ) -> bool:
        """合并资料写入人员库（不发布）。"""
        posts_raw = list_posts_by_person(person_id)
        if not posts_raw:
            return False

        person = get_person(person_id)
        merged = dict(person.merged_fields) if person else {}
        for post in posts_raw:
            merged = merge_profile_fields(merged, fields_from_text(post.get("text_original") or ""))

        canonical = pick_canonical_post(posts_raw)
        if not canonical:
            return False

        roster_status = "unknown"
        if active_entries and person_id in active_entries:
            roster_status = active_entries[person_id].status
        else:
            latest = get_latest_active_person_ids()
            if person_id in latest:
                roster_status = latest[person_id]

        caption = self._build_final_caption(merged)
        if not caption:
            return False

        dup_ids = [p["id"] for p in posts_raw if p["id"] != canonical["id"]]
        if dup_ids:
            mark_posts_status(dup_ids, "duplicate", "同一人已合并入人员库")

        store_update(
            canonical["id"],
            status="library",
            text_final=caption,
            text_formatted=render_fields_template(self.settings.publish_template, merged),
            person_id=person_id,
        )
        upsert_person(
            person_id,
            merged.get("name", ""),
            merged.get("region", ""),
            merged,
            roster_status=roster_status,
            preview_text=caption,
            library_status="ready",
        )
        update_person(person_id, canonical_post_id=canonical["id"])
        return True

    async def reconcile_all(self, client: TelegramClient) -> dict[str, int]:
        stats = {
            "roster_count": 0,
            "library_updated": 0,
            "removed": 0,
            "published": 0,
            "skipped_inactive": 0,
        }

        active_entries = await self.fetch_all_rosters(client)
        stats["roster_count"] = len(active_entries)

        if not active_entries:
            self._emit("WARNING", "未获取到任何出勤名单，跳过在岗校验")
            return stats

        active_ids = set(active_entries.keys())
        for pid, entry in active_entries.items():
            upsert_person(pid, entry.name, entry.region, roster_status=entry.status)

        all_person_ids: set[str] = set(active_ids)
        with _lock, _connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT person_id FROM posts WHERE person_id != ''"
            ).fetchall()
        for row in rows:
            all_person_ids.add(row["person_id"])
        for person in list_persons():
            all_person_ids.add(person.person_id)

        for person_id in all_person_ids:
            if not person_id:
                continue
            if person_id not in active_ids:
                person = get_person(person_id)
                update_person(person_id, roster_status="inactive", library_status="inactive")
                mark_person_posts(person_id, "inactive", "不在出勤名单")
                stats["skipped_inactive"] += 1
                if (
                    self.settings.delete_inactive_from_target
                    and person
                    and person.target_message_ids
                ):
                    removed = await self.remove_inactive_person(client, person_id, person)
                    stats["removed"] += 1 if removed else 0
                continue

            if await self.refresh_person_library(person_id, active_entries):
                stats["library_updated"] += 1

            if self.settings.auto_publish:
                if await self.publish_person(client, person_id, active_entries):
                    stats["published"] += 1

        return stats

    async def ingest_post_to_library(self, post_id: str, text: str) -> str:
        """抓取完成后写入/更新人员库。"""
        person_id = person_id_from_text(text)
        if not person_id:
            return ""
        fields = fields_from_text(text)
        store_update(post_id, person_id=person_id)
        await self.refresh_person_library(person_id)
        self._emit(
            "INFO",
            f"已入人员库: {fields.get('name')} ({fields.get('region')}) [{person_id}]",
        )
        return person_id

    async def register_post_text(self, text: str) -> str:
        pid = person_id_from_text(text)
        if not pid:
            return ""
        fields = fields_from_text(text)
        upsert_person(pid, fields.get("name", ""), fields.get("region", ""), fields)
        return pid

    async def on_post_ready(self, client: TelegramClient, post_id: str, text: str) -> None:
        await self.ingest_post_to_library(post_id, text)
