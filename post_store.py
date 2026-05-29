"""SQLite 持久化存储，支持去重校验与批量发布。"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import DATA_DIR

DB_PATH = DATA_DIR / "posts.db"
_lock = threading.Lock()


@dataclass
class StoredPost:
    id: str
    source_key: str
    chat_id: int
    message_ids: list[int]
    image_paths: list[str] = field(default_factory=list)
    media_items: list[dict[str, str]] = field(default_factory=list)  # [{type,path}]
    text_original: str = ""
    text_cleaned: str = ""
    text_formatted: str = ""
    text_final: str = ""
    media_count: int = 0
    status: str = "captured"  # captured | rewritten | pending | sent | failed | blocked
    error: str = ""
    blocked_reason: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    published_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _lock, _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS posts (
                id TEXT PRIMARY KEY,
                source_key TEXT NOT NULL,
                chat_id INTEGER NOT NULL,
                message_ids TEXT NOT NULL,
                image_paths TEXT NOT NULL,
                media_items TEXT NOT NULL DEFAULT '[]',
                text_original TEXT NOT NULL,
                text_cleaned TEXT NOT NULL DEFAULT '',
                text_formatted TEXT NOT NULL DEFAULT '',
                text_final TEXT NOT NULL DEFAULT '',
                media_count INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'captured',
                error TEXT NOT NULL DEFAULT '',
                blocked_reason TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                published_at TEXT NOT NULL DEFAULT ''
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_posts_status_created ON posts(status, created_at DESC)"
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_posts_source_key ON posts(source_key)"
        )
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(posts)").fetchall()]
        if "media_items" not in cols:
            conn.execute("ALTER TABLE posts ADD COLUMN media_items TEXT NOT NULL DEFAULT '[]'")
        conn.commit()


def _row_to_post(row: sqlite3.Row) -> StoredPost:
    image_paths = json.loads(row["image_paths"] or "[]")
    media_items_raw = row["media_items"] if "media_items" in row.keys() else "[]"
    media_items = json.loads(media_items_raw or "[]")
    if not media_items and image_paths:
        media_items = [{"type": "image", "path": p} for p in image_paths]
    return StoredPost(
        id=row["id"],
        source_key=row["source_key"],
        chat_id=row["chat_id"],
        message_ids=json.loads(row["message_ids"] or "[]"),
        image_paths=image_paths,
        media_items=media_items,
        text_original=row["text_original"] or "",
        text_cleaned=row["text_cleaned"] or "",
        text_formatted=row["text_formatted"] or "",
        text_final=row["text_final"] or "",
        media_count=int(row["media_count"] or 0),
        status=row["status"] or "captured",
        error=row["error"] or "",
        blocked_reason=row["blocked_reason"] or "",
        created_at=row["created_at"] or "",
        updated_at=row["updated_at"] or "",
        published_at=row["published_at"] or "",
    )


def exists(post_id: str) -> bool:
    with _lock, _connect() as conn:
        row = conn.execute("SELECT 1 FROM posts WHERE id = ? LIMIT 1", (post_id,)).fetchone()
        return row is not None


def add_or_ignore(post: StoredPost) -> bool:
    now = datetime.now(timezone.utc).isoformat()
    post.updated_at = now
    if not post.created_at:
        post.created_at = now
    with _lock, _connect() as conn:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO posts(
                id, source_key, chat_id, message_ids, image_paths,
                media_items,
                text_original, text_cleaned, text_formatted, text_final,
                media_count, status, error, blocked_reason, created_at, updated_at, published_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                post.id,
                post.source_key,
                post.chat_id,
                json.dumps(post.message_ids, ensure_ascii=False),
                json.dumps(post.image_paths, ensure_ascii=False),
                json.dumps(post.media_items, ensure_ascii=False),
                post.text_original,
                post.text_cleaned,
                post.text_formatted,
                post.text_final,
                post.media_count,
                post.status,
                post.error,
                post.blocked_reason,
                post.created_at,
                post.updated_at,
                post.published_at,
            ),
        )
        conn.commit()
        return cur.rowcount > 0


def update(post_id: str, **kwargs: Any) -> None:
    if not kwargs:
        return
    serializable = dict(kwargs)
    for key in ("message_ids", "image_paths", "media_items"):
        if key in serializable:
            serializable[key] = json.dumps(serializable[key], ensure_ascii=False)
    serializable["updated_at"] = datetime.now(timezone.utc).isoformat()
    fields = ", ".join([f"{k} = ?" for k in serializable.keys()])
    params = list(serializable.values()) + [post_id]
    with _lock, _connect() as conn:
        conn.execute(f"UPDATE posts SET {fields} WHERE id = ?", params)
        conn.commit()


def get(post_id: str) -> StoredPost | None:
    with _lock, _connect() as conn:
        row = conn.execute("SELECT * FROM posts WHERE id = ? LIMIT 1", (post_id,)).fetchone()
    return _row_to_post(row) if row else None


def list_posts(limit: int = 50, status: str | None = None) -> list[dict[str, Any]]:
    sql = "SELECT * FROM posts"
    params: list[Any] = []
    if status:
        sql += " WHERE status = ?"
        params.append(status)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    with _lock, _connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_post(r).to_dict() for r in rows]


def delete_posts(post_ids: list[str]) -> int:
    if not post_ids:
        return 0
    q = ",".join("?" for _ in post_ids)
    with _lock, _connect() as conn:
        cur = conn.execute(f"DELETE FROM posts WHERE id IN ({q})", post_ids)
        conn.commit()
        return cur.rowcount


def delete_posts_and_media(post_ids: list[str], media_dir: Path) -> dict[str, int]:
    if not post_ids:
        return {"removed": 0, "media_deleted": 0}
    q = ",".join("?" for _ in post_ids)
    with _lock, _connect() as conn:
        rows = conn.execute(
            f"SELECT image_paths, media_items FROM posts WHERE id IN ({q})",
            post_ids,
        ).fetchall()
        media_deleted = 0
        for row in rows:
            items = json.loads(row["media_items"] or "[]")
            paths = json.loads(row["image_paths"] or "[]")
            if not items and paths:
                items = [{"type": "image", "path": p} for p in paths]
            for item in items:
                p = item.get("path", "")
                name = Path(str(p).replace("/media/", "")).name
                fp = media_dir / name
                if fp.exists():
                    fp.unlink(missing_ok=True)
                    media_deleted += 1
        cur = conn.execute(f"DELETE FROM posts WHERE id IN ({q})", post_ids)
        conn.commit()
        return {"removed": cur.rowcount, "media_deleted": media_deleted}


def list_pending_ids(limit: int = 100) -> list[str]:
    with _lock, _connect() as conn:
        rows = conn.execute(
            "SELECT id FROM posts WHERE status = 'pending' ORDER BY created_at ASC LIMIT ?",
            (limit,),
        ).fetchall()
    return [r["id"] for r in rows]


def validate_and_cleanup(media_dir: Path) -> dict[str, int]:
    """
    校验并修复：
    - 去重（按 source_key 仅保留最新）
    - 清理不存在媒体路径
    - 删除磁盘中的孤儿媒体文件
    """
    dedup_removed = 0
    media_fixed = 0
    orphan_deleted = 0

    media_dir.mkdir(parents=True, exist_ok=True)
    with _lock, _connect() as conn:
        rows = conn.execute(
            """
            SELECT rowid, source_key FROM posts
            ORDER BY source_key, created_at DESC
            """
        ).fetchall()
        seen: set[str] = set()
        remove_rowids: list[int] = []
        for row in rows:
            key = row["source_key"]
            if key in seen:
                remove_rowids.append(row["rowid"])
            else:
                seen.add(key)
        if remove_rowids:
            q = ",".join("?" for _ in remove_rowids)
            conn.execute(f"DELETE FROM posts WHERE rowid IN ({q})", remove_rowids)
            dedup_removed = len(remove_rowids)

        rows = conn.execute("SELECT id, image_paths, media_items FROM posts").fetchall()
        referenced_files: set[str] = set()
        for row in rows:
            paths = json.loads(row["image_paths"] or "[]")
            items = json.loads(row["media_items"] or "[]")
            if not items and paths:
                items = [{"type": "image", "path": p} for p in paths]
            item_kept: list[dict[str, str]] = []
            kept: list[str] = []
            for item in items:
                p = item.get("path", "")
                name = Path(str(p).replace("/media/", "")).name
                abs_path = media_dir / name
                if abs_path.exists():
                    new_path = f"/media/{name}"
                    if item.get("type") == "image":
                        kept.append(new_path)
                    item_kept.append({"type": item.get("type", "file"), "path": new_path})
                    referenced_files.add(name)
            if kept != paths or item_kept != items:
                conn.execute(
                    "UPDATE posts SET image_paths = ?, media_items = ?, updated_at = ? WHERE id = ?",
                    (
                        json.dumps(kept, ensure_ascii=False),
                        json.dumps(item_kept, ensure_ascii=False),
                        datetime.now(timezone.utc).isoformat(),
                        row["id"],
                    ),
                )
                media_fixed += 1

        for file in media_dir.iterdir():
            if file.is_file() and file.name not in referenced_files:
                file.unlink(missing_ok=True)
                orphan_deleted += 1

        conn.commit()

    return {
        "dedup_removed": dedup_removed,
        "media_fixed": media_fixed,
        "orphan_deleted": orphan_deleted,
    }


init_db()
