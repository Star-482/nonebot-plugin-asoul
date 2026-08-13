"""
@Author: star_482
@Date: 2026/8/13
@File: group_admin
@Description: 群管 repository--入群欢迎配置（group_welcome）+ 自定义欢迎语审核流水（welcome_reviews）。
仿 relationships.py：共用 connection.get_db() + _db_lock，_now() 同款 ISO 时间戳。
"""
from datetime import datetime
from typing import Optional

from ..connection import get_db, _db_lock


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class GroupWelcomeRepo:
    """每群入群欢迎配置（当前生效）。"""

    def get(self, gid: str) -> Optional[dict]:
        with _db_lock:
            row = get_db().execute(
                "SELECT enabled, text, updated_at, updated_by FROM group_welcome WHERE group_openid=?",
                (gid,),
            ).fetchone()
        return dict(row) if row else None

    def get_effective_text(self, gid: str, default: str) -> Optional[str]:
        """返回生效欢迎语：无记录（默认开启）或 enabled=1 时返回 text 或 default。
        显式关闭（enabled=0）返回 None。"""
        with _db_lock:
            row = get_db().execute(
                "SELECT enabled, text FROM group_welcome WHERE group_openid=?",
                (gid,),
            ).fetchone()
        if not row:
            return default
        if not row["enabled"]:
            return None
        return row["text"] or default

    def set_enabled(self, gid: str, enabled: bool, op: str) -> None:
        """开关入群欢迎。upsert（群记录不存在则建，text 保持 NULL）。"""
        with _db_lock:
            get_db().execute(
                """INSERT INTO group_welcome (group_openid, enabled, updated_at, updated_by)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(group_openid) DO UPDATE SET
                     enabled=excluded.enabled,
                     updated_at=excluded.updated_at,
                     updated_by=excluded.updated_by""",
                (gid, 1 if enabled else 0, _now(), op),
            )
            get_db().commit()

    def set_text(self, gid: str, text: str, op: str) -> None:
        """写入自定义欢迎语并立即生效（enabled=1）。upsert。"""
        with _db_lock:
            get_db().execute(
                """INSERT INTO group_welcome (group_openid, enabled, text, updated_at, updated_by)
                   VALUES (?, 1, ?, ?, ?)
                   ON CONFLICT(group_openid) DO UPDATE SET
                     enabled=1,
                     text=excluded.text,
                     updated_at=excluded.updated_at,
                     updated_by=excluded.updated_by""",
                (gid, text, _now(), op),
            )
            get_db().commit()

    def reset_text(self, gid: str, op: str) -> None:
        """清除自定义欢迎语，回退默认（text=NULL）。enabled 不变。审核拒绝时调用。"""
        with _db_lock:
            get_db().execute(
                "UPDATE group_welcome SET text=NULL, updated_at=?, updated_by=? WHERE group_openid=?",
                (_now(), op, gid),
            )
            get_db().commit()


class WelcomeReviewRepo:
    """自定义欢迎语审核流水。"""

    def create(self, gid: str, submitter: str, role: str, text: str) -> dict:
        """创建待审核记录，返回新记录 dict。"""
        with _db_lock:
            cur = get_db().execute(
                """INSERT INTO welcome_reviews
                   (group_openid, submitter_openid, submitter_role, pending_text, status, submitted_at)
                   VALUES (?, ?, ?, ?, 'pending', ?)""",
                (gid, submitter, role, text, _now()),
            )
            get_db().commit()
            row = get_db().execute(
                "SELECT * FROM welcome_reviews WHERE id=?", (cur.lastrowid,)
            ).fetchone()
        return dict(row)

    def get(self, rid: int) -> Optional[dict]:
        with _db_lock:
            row = get_db().execute(
                "SELECT * FROM welcome_reviews WHERE id=?",
                (rid,),
            ).fetchone()
        return dict(row) if row else None

    def approve(self, rid: int, reviewer: str) -> bool:
        """标记为 approved。返回是否确实更新（原 status=pending）。"""
        with _db_lock:
            cur = get_db().execute(
                "UPDATE welcome_reviews SET status='approved', reviewed_at=?, reviewer_openid=? "
                "WHERE id=? AND status='pending'",
                (_now(), reviewer, rid),
            )
            get_db().commit()
        return cur.rowcount > 0

    def reject(self, rid: int, reviewer: str) -> bool:
        """标记为 rejected。返回是否确实更新。"""
        with _db_lock:
            cur = get_db().execute(
                "UPDATE welcome_reviews SET status='rejected', reviewed_at=?, reviewer_openid=? "
                "WHERE id=? AND status='pending'",
                (_now(), reviewer, rid),
            )
            get_db().commit()
        return cur.rowcount > 0

    def list_pending(self) -> list[dict]:
        with _db_lock:
            rows = get_db().execute(
                "SELECT * FROM welcome_reviews WHERE status='pending' ORDER BY submitted_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]
