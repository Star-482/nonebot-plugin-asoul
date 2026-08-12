"""
@Author: star_482
@Date: 2026/8/11
@File: messages
@Description: 消息审核存储 repository。从 message_review/store.py 迁入，改用公共连接。
"""
import json
import sqlite3
from datetime import datetime, timedelta
from typing import Optional

from ..connection import get_db, _db_lock


class MessageStore:
    """线程安全的 SQLite 消息存储。dev 审核工具量级，同步 IO + 共享锁。"""

    def insert(self, record: dict) -> int:
        with _db_lock:
            cur = get_db().execute(
                """INSERT INTO messages
                   (ts, epoch, direction, scene_type, scene_id, user_id, user_name,
                    matcher_module, command, msg_type, plain_text, content_json, status)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    record["ts"],
                    record["epoch"],
                    record["direction"],
                    record["scene_type"],
                    record["scene_id"],
                    record.get("user_id"),
                    record.get("user_name"),
                    record.get("matcher_module"),
                    record.get("command"),
                    record.get("msg_type"),
                    record.get("plain_text"),
                    record["content_json"],
                    record.get("status"),
                ),
            )
            get_db().commit()
            assert cur.lastrowid is not None  # INSERT 一定产生自增 id
            return cur.lastrowid

    def _row(self, row: sqlite3.Row) -> dict:
        d = dict(row)
        try:
            parsed = json.loads(d.pop("content_json"))
            d["content"] = parsed.get("segments", []) if isinstance(parsed, dict) else parsed
        except (ValueError, KeyError):
            d["content"] = []
        return d

    def list_conversations(self) -> list[dict]:
        sql = """
        SELECT m.scene_type, m.scene_id, m.id AS last_id, m.ts AS last_ts,
               m.plain_text AS last_text, m.direction AS last_direction, m.user_id AS last_user_id,
               cnt.count
        FROM messages m
        JOIN (SELECT scene_type, scene_id, MAX(id) AS max_id, COUNT(*) AS count
              FROM messages GROUP BY scene_type, scene_id) cnt
          ON cnt.scene_type = m.scene_type AND cnt.scene_id = m.scene_id AND m.id = cnt.max_id
        ORDER BY m.epoch DESC;
        """
        with _db_lock:
            rows = get_db().execute(sql).fetchall()
        return [dict(r) for r in rows]

    def page_before(
        self, scene_type: str, scene_id: str, before_id: Optional[int], limit: int = 50
    ) -> list[dict]:
        """向前翻页：返回 id < before_id（before_id 为 None 时不限）的最新 limit 条，id DESC。"""
        sql = """SELECT * FROM messages
                 WHERE scene_type=? AND scene_id=? AND (? IS NULL OR id < ?)
                 ORDER BY id DESC LIMIT ?"""
        with _db_lock:
            rows = get_db().execute(
                sql, (scene_type, scene_id, before_id, before_id, limit)
            ).fetchall()
        return [self._row(r) for r in rows]

    def recent(self, limit: int) -> list[dict]:
        """全局最近 limit 条，按时间升序返回（供 WS 连上回补）。"""
        sql = "SELECT * FROM messages ORDER BY id DESC LIMIT ?"
        with _db_lock:
            rows = get_db().execute(sql, (limit,)).fetchall()
        return [self._row(r) for r in reversed(rows)]

    def since(self, since_id: Optional[int], limit: int) -> list[dict]:
        """返回 id > since_id 的消息，升序，最多 limit 条。since_id 为 None 时等同 recent。"""
        if since_id is None:
            return self.recent(limit)
        sql = "SELECT * FROM messages WHERE id > ? ORDER BY id ASC LIMIT ?"
        with _db_lock:
            rows = get_db().execute(sql, (since_id, limit)).fetchall()
        return [self._row(r) for r in rows]

    def purge_old(self, days: int) -> int:
        if days <= 0:
            return 0
        cutoff = (datetime.now().astimezone() - timedelta(days=days)).timestamp()
        with _db_lock:
            cur = get_db().execute("DELETE FROM messages WHERE epoch < ?", (cutoff,))
            get_db().commit()
            return cur.rowcount


# 模块级单例，由 message_review/__init__ 在 review_enabled 时初始化
store: Optional[MessageStore] = None


def init_store() -> MessageStore:
    global store
    if store is None:
        get_db()  # 确保建库建表
        store = MessageStore()
    return store


def get_store() -> MessageStore:
    if store is None:
        return init_store()
    return store
