"""
@Author: star_482
@Date: 2026/8/13
@File: group_admin
@Description: 群管 repository--入群欢迎配置（group_welcome）+ 自定义欢迎语审核流水（welcome_reviews）+ 关键词撤回配置（group_recall_keywords）。
仿 relationships.py：共用 connection.get_db() + _db_lock，_now() 同款 ISO 时间戳。
"""
import json
from datetime import datetime
from typing import Optional

from nonebot.log import logger

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


class GroupRecallRepo:
    """每群关键词撤回配置（keywords 存 JSON 数组）。"""

    def __init__(self) -> None:
        # 老库兼容：get_db() 的 SCHEMA 只在首次建库时执行，
        # 这里幂等补建本表，让已部署的库不用手动迁移。
        self._ensure_table()

    def _ensure_table(self) -> None:
        with _db_lock:
            get_db().execute(
                """CREATE TABLE IF NOT EXISTS group_recall_keywords (
                       group_openid TEXT PRIMARY KEY,
                       keywords TEXT NOT NULL,
                       updated_at TEXT NOT NULL,
                       updated_by TEXT
                   )"""
            )
            get_db().commit()

    def get(self, gid: str) -> Optional[dict]:
        with _db_lock:
            row = get_db().execute(
                "SELECT group_openid, keywords, updated_at, updated_by "
                "FROM group_recall_keywords WHERE group_openid=?",
                (gid,),
            ).fetchone()
        return dict(row) if row else None

    def get_keywords(self, gid: str) -> list[str]:
        """返回该群当前生效的撤回关键词列表；无记录/解析失败返回空列表。"""
        row = self.get(gid)
        if not row or not row.get("keywords"):
            return []
        try:
            data = json.loads(row["keywords"])
        except (TypeError, json.JSONDecodeError):
            logger.warning(f"[群管] 撤回关键词 JSON 解析失败 gid={gid}")
            return []
        if not isinstance(data, list):
            return []
        return [str(w).strip() for w in data if str(w).strip()]

    def remove_keywords(self, gid: str, words: list[str], op: str) -> dict:
        """从当前关键词中移除指定词（大小写不敏感匹配），返回最新记录 dict。"""
        removes = {str(w).strip().lower() for w in words if str(w).strip()}
        kept = [w for w in self.get_keywords(gid) if w.lower() not in removes]
        return self.set_keywords(gid, kept, op)

    def clear_keywords(self, gid: str, op: str) -> None:
        """清空该群撤回关键词（删除整行记录）。"""
        with _db_lock:
            get_db().execute(
                "DELETE FROM group_recall_keywords WHERE group_openid=?",
                (gid,),
            )
            get_db().commit()

    def set_keywords(self, gid: str, keywords: list[str], op: str) -> dict:
        """覆盖写入关键词（自动去重、去空），返回最新记录 dict。"""
        cleaned: list[str] = []
        seen: set[str] = set()
        for raw in keywords:
            word = str(raw).strip()
            if not word:
                continue
            # 大小写不敏感去重，保留首次出现时的写法
            key = word.lower()
            if key in seen:
                continue
            seen.add(key)
            cleaned.append(word)

        with _db_lock:
            get_db().execute(
                """INSERT INTO group_recall_keywords (group_openid, keywords, updated_at, updated_by)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(group_openid) DO UPDATE SET
                     keywords=excluded.keywords,
                     updated_at=excluded.updated_at,
                     updated_by=excluded.updated_by""",
                (gid, json.dumps(cleaned, ensure_ascii=False), _now(), op),
            )
            get_db().commit()
            row = get_db().execute(
                "SELECT group_openid, keywords, updated_at, updated_by "
                "FROM group_recall_keywords WHERE group_openid=?",
                (gid,),
            ).fetchone()
        return dict(row)


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
