"""人员库与出勤快照存储。"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any

from config import DATA_DIR
from person_registry import merge_profile_fields
from post_store import DB_PATH, _connect, _lock

PERSON_STATUSES = ("online", "resting", "inactive", "unknown")


class PersonRecord:
    def __init__(
        self,
        person_id: str,
        name: str = "",
        region: str = "",
        merged_fields: dict[str, str] | None = None,
        roster_status: str = "unknown",
        canonical_post_id: str = "",
        target_message_ids: list[int] | None = None,
        preview_text: str = "",
        library_status: str = "draft",
        updated_at: str = "",
    ) -> None:
        self.person_id = person_id
        self.name = name
        self.region = region
        self.merged_fields = merged_fields or {}
        self.roster_status = roster_status
        self.canonical_post_id = canonical_post_id
        self.target_message_ids = target_message_ids or []
        self.preview_text = preview_text
        self.library_status = library_status
        self.updated_at = updated_at


def init_roster_db() -> None:
    with _lock, _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS persons (
                person_id TEXT PRIMARY KEY,
                name TEXT NOT NULL DEFAULT '',
                region TEXT NOT NULL DEFAULT '',
                merged_fields TEXT NOT NULL DEFAULT '{}',
                roster_status TEXT NOT NULL DEFAULT 'unknown',
                canonical_post_id TEXT NOT NULL DEFAULT '',
                target_message_ids TEXT NOT NULL DEFAULT '[]',
                preview_text TEXT NOT NULL DEFAULT '',
                library_status TEXT NOT NULL DEFAULT 'draft',
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS roster_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_key TEXT NOT NULL,
                group_chat TEXT NOT NULL,
                message_id INTEGER NOT NULL DEFAULT 0,
                raw_text TEXT NOT NULL,
                entries TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_roster_group_created ON roster_snapshots(group_key, created_at DESC)"
        )
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(posts)").fetchall()]
        if "person_id" not in cols:
            conn.execute("ALTER TABLE posts ADD COLUMN person_id TEXT NOT NULL DEFAULT ''")
        if "source_channel" not in cols:
            conn.execute("ALTER TABLE posts ADD COLUMN source_channel TEXT NOT NULL DEFAULT ''")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_posts_person_id ON posts(person_id, status)"
        )
        pcols = [r["name"] for r in conn.execute("PRAGMA table_info(persons)").fetchall()]
        if "preview_text" not in pcols:
            conn.execute("ALTER TABLE persons ADD COLUMN preview_text TEXT NOT NULL DEFAULT ''")
        if "library_status" not in pcols:
            conn.execute("ALTER TABLE persons ADD COLUMN library_status TEXT NOT NULL DEFAULT 'draft'")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_persons_library ON persons(library_status, roster_status)"
        )
        conn.commit()


def _row_to_person(row: sqlite3.Row) -> PersonRecord:
    return PersonRecord(
        person_id=row["person_id"],
        name=row["name"] or "",
        region=row["region"] or "",
        merged_fields=json.loads(row["merged_fields"] or "{}"),
        roster_status=row["roster_status"] or "unknown",
        canonical_post_id=row["canonical_post_id"] or "",
        target_message_ids=json.loads(row["target_message_ids"] or "[]"),
        preview_text=row["preview_text"] if "preview_text" in row.keys() else "",
        library_status=row["library_status"] if "library_status" in row.keys() else "draft",
        updated_at=row["updated_at"] or "",
    )


def has_roster_snapshot() -> bool:
    with _lock, _connect() as conn:
        row = conn.execute("SELECT 1 FROM roster_snapshots LIMIT 1").fetchone()
    return row is not None


def save_roster_snapshot(
    group_key: str,
    group_chat: str,
    message_id: int,
    raw_text: str,
    entries: list[dict[str, Any]],
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with _lock, _connect() as conn:
        conn.execute(
            """
            INSERT INTO roster_snapshots(group_key, group_chat, message_id, raw_text, entries, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                group_key,
                group_chat,
                message_id,
                raw_text,
                json.dumps(entries, ensure_ascii=False),
                now,
            ),
        )
        conn.commit()


def get_person(person_id: str) -> PersonRecord | None:
    if not person_id:
        return None
    with _lock, _connect() as conn:
        row = conn.execute("SELECT * FROM persons WHERE person_id = ?", (person_id,)).fetchone()
    return _row_to_person(row) if row else None


def upsert_person(
    person_id: str,
    name: str,
    region: str,
    fields: dict[str, str] | None = None,
    roster_status: str | None = None,
    preview_text: str | None = None,
    library_status: str | None = None,
) -> PersonRecord:
    now = datetime.now(timezone.utc).isoformat()
    existing = get_person(person_id)
    merged = merge_profile_fields(
        existing.merged_fields if existing else {},
        fields or {},
    )
    if name:
        merged["name"] = name
    if region:
        merged["region"] = region
    status = roster_status or (existing.roster_status if existing else "unknown")
    canonical = existing.canonical_post_id if existing else ""
    target_ids = existing.target_message_ids if existing else []
    preview = preview_text if preview_text is not None else (existing.preview_text if existing else "")
    lib_status = library_status or (existing.library_status if existing else "draft")

    with _lock, _connect() as conn:
        conn.execute(
            """
            INSERT INTO persons(
                person_id, name, region, merged_fields, roster_status,
                canonical_post_id, target_message_ids, preview_text, library_status, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(person_id) DO UPDATE SET
                name = excluded.name,
                region = excluded.region,
                merged_fields = excluded.merged_fields,
                roster_status = excluded.roster_status,
                canonical_post_id = excluded.canonical_post_id,
                target_message_ids = excluded.target_message_ids,
                preview_text = excluded.preview_text,
                library_status = excluded.library_status,
                updated_at = excluded.updated_at
            """,
            (
                person_id,
                merged.get("name") or name,
                merged.get("region") or region,
                json.dumps(merged, ensure_ascii=False),
                status,
                canonical,
                json.dumps(target_ids, ensure_ascii=False),
                preview,
                lib_status,
                now,
            ),
        )
        conn.commit()
    return get_person(person_id) or PersonRecord(person_id=person_id, name=name, region=region)


def update_person(person_id: str, **kwargs: Any) -> None:
    if not person_id or not kwargs:
        return
    data = dict(kwargs)
    if "merged_fields" in data:
        data["merged_fields"] = json.dumps(data["merged_fields"], ensure_ascii=False)
    if "target_message_ids" in data:
        data["target_message_ids"] = json.dumps(data["target_message_ids"], ensure_ascii=False)
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    fields = ", ".join(f"{k} = ?" for k in data)
    with _lock, _connect() as conn:
        conn.execute(f"UPDATE persons SET {fields} WHERE person_id = ?", [*data.values(), person_id])
        conn.commit()


def list_persons(status: str | None = None) -> list[PersonRecord]:
    sql = "SELECT * FROM persons"
    params: list[Any] = []
    if status:
        sql += " WHERE roster_status = ?"
        params.append(status)
    sql += " ORDER BY updated_at DESC"
    with _lock, _connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_person(r) for r in rows]


def list_library_persons(
    include_inactive: bool = False,
    limit: int = 200,
    offset: int = 0,
) -> list[PersonRecord]:
    sql = """
        SELECT * FROM persons
        WHERE preview_text != ''
          AND library_status IN ('draft', 'ready', 'published')
    """
    if not include_inactive:
        sql += " AND roster_status IN ('online', 'resting', 'unknown')"
    sql += " ORDER BY name ASC LIMIT ? OFFSET ?"
    with _lock, _connect() as conn:
        rows = conn.execute(sql, (limit, offset)).fetchall()
    return [_row_to_person(r) for r in rows]


def count_library_persons(include_inactive: bool = False) -> int:
    sql = """
        SELECT COUNT(*) AS c FROM persons
        WHERE preview_text != ''
          AND library_status IN ('draft', 'ready', 'published')
    """
    if not include_inactive:
        sql += " AND roster_status IN ('online', 'resting', 'unknown')"
    with _lock, _connect() as conn:
        row = conn.execute(sql).fetchone()
    return int(row["c"] or 0) if row else 0


def list_posts_by_person(person_id: str) -> list[dict[str, Any]]:
    from post_store import _row_to_post

    with _lock, _connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM posts
            WHERE person_id = ?
              AND status NOT IN ('blocked', 'duplicate', 'source_deleted', 'inactive')
            ORDER BY created_at DESC
            """,
            (person_id,),
        ).fetchall()
    return [_row_to_post(r).to_dict() for r in rows]


def mark_person_posts(person_id: str, status: str, error: str = "") -> int:
    now = datetime.now(timezone.utc).isoformat()
    with _lock, _connect() as conn:
        cur = conn.execute(
            """
            UPDATE posts SET status = ?, error = ?, updated_at = ?
            WHERE person_id = ? AND status NOT IN ('blocked', 'source_deleted')
            """,
            (status, error, now, person_id),
        )
        conn.commit()
        return cur.rowcount


def get_latest_active_person_ids() -> dict[str, str]:
    """各群最新出勤快照的并集 person_id -> status。"""
    active: dict[str, str] = {}
    with _lock, _connect() as conn:
        rows = conn.execute(
            "SELECT group_key, MAX(id) AS max_id FROM roster_snapshots GROUP BY group_key"
        ).fetchall()
        for row in rows:
            snap = conn.execute(
                "SELECT entries FROM roster_snapshots WHERE id = ?",
                (row["max_id"],),
            ).fetchone()
            if not snap:
                continue
            for item in json.loads(snap["entries"] or "[]"):
                pid = item.get("person_id") or ""
                if pid:
                    active[pid] = item.get("status") or "online"
    return active


init_roster_db()
