"""
@Author: star_482
@Date: 2026/8/6
@File: store
@Description: 消息审核 SQLite 存储层。单连接 + threading.Lock，WAL 模式。
"""
import json
import os
import sqlite3
import threading
from datetime import datetime, timedelta
from typing import Optional

from nonebot.log import logger

from ..config import config


_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    epoch REAL NOT NULL,
    direction TEXT NOT NULL,
    scene_type TEXT NOT NULL,
    scene_id TEXT NOT NULL,
    user_id TEXT,
    user_name TEXT,
    matcher_module TEXT,
    command TEXT,
    msg_type INTEGER,
    plain_text TEXT,
    content_json TEXT NOT NULL,
    status TEXT
);
CREATE INDEX IF NOT EXISTS idx_msg_scene_epoch ON messages(scene_type, scene_id, epoch DESC);
CREATE INDEX IF NOT EXISTS idx_msg_epoch ON messages(epoch DESC);
"""


def _db_path() -> str:
    return os.path.join(config.data_path, config.review_db_path)


class MessageStore:
    """线程安全的 SQLite 消息存储。dev 审核工具量级，同步 IO + 锁即可。"""

    def __init__(self, path: Optional[str] = None):
        self._path = path or _db_path()
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA synchronous=NORMAL;")
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()
        logger.info(f"消息审核存储就绪: {self._path}")

    def insert(self, record: dict) -> int:
        with self._lock:
            cur = self._conn.execute(
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
            self._conn.commit()
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
        with self._lock:
            rows = self._conn.execute(sql).fetchall()
        return [dict(r) for r in rows]

    def page_before(
        self, scene_type: str, scene_id: str, before_id: Optional[int], limit: int = 50
    ) -> list[dict]:
        """向前翻页：返回 id < before_id（before_id 为 None 时不限）的最新 limit 条，id DESC。"""
        sql = """SELECT * FROM messages
                 WHERE scene_type=? AND scene_id=? AND (? IS NULL OR id < ?)
                 ORDER BY id DESC LIMIT ?"""
        with self._lock:
            rows = self._conn.execute(
                sql, (scene_type, scene_id, before_id, before_id, limit)
            ).fetchall()
        return [self._row(r) for r in rows]

    def recent(self, limit: int) -> list[dict]:
        """全局最近 limit 条，按时间升序返回（供 WS 连上回补）。"""
        sql = "SELECT * FROM messages ORDER BY id DESC LIMIT ?"
        with self._lock:
            rows = self._conn.execute(sql, (limit,)).fetchall()
        return [self._row(r) for r in reversed(rows)]

    def since(self, since_id: Optional[int], limit: int) -> list[dict]:
        """返回 id > since_id 的消息，升序，最多 limit 条。since_id 为 None 时等同 recent。"""
        if since_id is None:
            return self.recent(limit)
        sql = "SELECT * FROM messages WHERE id > ? ORDER BY id ASC LIMIT ?"
        with self._lock:
            rows = self._conn.execute(sql, (since_id, limit)).fetchall()
        return [self._row(r) for r in rows]

    def purge_old(self, days: int) -> int:
        if days <= 0:
            return 0
        cutoff = (datetime.now().astimezone() - timedelta(days=days)).timestamp()
        with self._lock:
            cur = self._conn.execute("DELETE FROM messages WHERE epoch < ?", (cutoff,))
            self._conn.commit()
            return cur.rowcount


# 模块级单例，由 __init__ 在 review_enabled 时初始化
store: Optional[MessageStore] = None


def init_store() -> MessageStore:
    global store
    if store is None:
        store = MessageStore()
    return store


def get_store() -> MessageStore:
    if store is None:
        return init_store()
    return store
