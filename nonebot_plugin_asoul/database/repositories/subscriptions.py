"""
@Author: star_482
@Date: 2026/8/11
@File: subscriptions
@Description: 群订阅 + 预定义 up主列表 repository。subscriptions/upstreams 表 CRUD。
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from ..connection import _db_lock, get_db


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class UpstreamsRepo:
    """预定义 up主列表。"""

    def count(self) -> int:
        with _db_lock:
            row = get_db().execute("SELECT COUNT(*) AS c FROM upstreams").fetchone()
        return row["c"] if row else 0

    def load_defaults(self, upstreams: list[dict]) -> None:
        """灌入默认列表。已存在的 uid 跳过（INSERT OR IGNORE）。"""
        with _db_lock:
            db = get_db()
            for u in upstreams:
                try:
                    db.execute(
                        "INSERT OR IGNORE INTO upstreams (uid, name) VALUES (?, ?)",
                        (int(u["uid"]), str(u["name"])),
                    )
                except (KeyError, TypeError, ValueError):
                    continue
            db.commit()

    def list(self) -> list[dict]:
        with _db_lock:
            rows = get_db().execute(
                "SELECT uid, name FROM upstreams ORDER BY uid"
            ).fetchall()
        return [{"uid": r["uid"], "name": r["name"]} for r in rows]

    def names(self) -> list[str]:
        with _db_lock:
            rows = get_db().execute(
                "SELECT name FROM upstreams ORDER BY uid"
            ).fetchall()
        return [r["name"] for r in rows]

    def search(self, keyword: str) -> Optional[dict]:
        """关键字模糊匹配 name。唯一匹配返回；多匹配时优先完全匹配；无匹配返回 None。"""
        keyword_lower = keyword.strip().lower()
        with _db_lock:
            matches = get_db().execute(
                "SELECT uid, name FROM upstreams WHERE LOWER(name) LIKE ?",
                (f"%{keyword_lower}%",),
            ).fetchall()
        if len(matches) == 1:
            return {"uid": matches[0]["uid"], "name": matches[0]["name"]}
        if not matches:
            return None
        for u in matches:
            if u["name"].lower() == keyword_lower:
                return {"uid": u["uid"], "name": u["name"]}
        return None


class SubscriptionsRepo:
    """群订阅（群 <-> up主 多对多）。"""

    async def subscribe(self, gid: str, uid: int) -> bool:
        """返回是否新增（已订阅返回 False）。"""
        with _db_lock:
            cur = get_db().execute(
                "INSERT OR IGNORE INTO subscriptions (group_openid, uid, created_at) VALUES (?, ?, ?)",
                (gid, uid, _now()),
            )
            get_db().commit()
        return cur.rowcount > 0

    async def unsubscribe(self, gid: str, uid: int) -> bool:
        """返回是否删除（未订阅返回 False）。"""
        with _db_lock:
            cur = get_db().execute(
                "DELETE FROM subscriptions WHERE group_openid=? AND uid=?",
                (gid, uid),
            )
            get_db().commit()
        return cur.rowcount > 0

    async def is_subscribed(self, gid: str, uid: int) -> bool:
        with _db_lock:
            row = get_db().execute(
                "SELECT 1 FROM subscriptions WHERE group_openid=? AND uid=?",
                (gid, uid),
            ).fetchone()
        return row is not None

    async def remove_group(self, gid: str) -> bool:
        """移除该群所有订阅。返回是否确有数据被清除。"""
        with _db_lock:
            cur = get_db().execute(
                "DELETE FROM subscriptions WHERE group_openid=?", (gid,)
            )
            get_db().commit()
        return cur.rowcount > 0

    async def list_for_group(self, gid: str) -> list[dict]:
        with _db_lock:
            rows = get_db().execute(
                """SELECT s.uid, COALESCE(u.name, 'UID:' || s.uid) AS name
                   FROM subscriptions s
                   LEFT JOIN upstreams u ON s.uid = u.uid
                   WHERE s.group_openid=?
                   ORDER BY s.uid""",
                (gid,),
            ).fetchall()
        return [{"uid": r["uid"], "name": r["name"]} for r in rows]

    async def list_all(self) -> dict[str, list[dict]]:
        result: dict[str, list[dict]] = {}
        with _db_lock:
            rows = get_db().execute(
                """SELECT s.group_openid, s.uid, COALESCE(u.name, 'UID:' || s.uid) AS name
                   FROM subscriptions s
                   LEFT JOIN upstreams u ON s.uid = u.uid
                   ORDER BY s.group_openid, s.uid"""
            ).fetchall()
        for r in rows:
            result.setdefault(r["group_openid"], []).append(
                {"uid": r["uid"], "name": r["name"]}
            )
        return result

    def get_subscribed_groups(self, uid: int) -> list[str]:
        with _db_lock:
            rows = get_db().execute(
                "SELECT group_openid FROM subscriptions WHERE uid=? ORDER BY group_openid",
                (uid,),
            ).fetchall()
        return [r["group_openid"] for r in rows]
