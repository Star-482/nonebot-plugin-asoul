"""
@Author: star_482
@Date: 2026/8/11
@File: relationships
@Description: 用户/群关系 + 推送权限 repository。groups/friends 表 CRUD。
removed_at 非空表示关系已解除（保留历史，不物理删除）。
push_state 取值 'ok' | 'fail' | NULL(未知)。
"""
from datetime import datetime
from typing import Optional

from ..connection import get_db, _db_lock


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class GroupsRepo:
    """群关系 + 推送权限。"""

    def upsert_added(self, gid: str, op_member: Optional[str] = None) -> None:
        """记录 bot 被加入群。已存在则重置 removed_at（重新入群），保留 push_state。"""
        with _db_lock:
            get_db().execute(
                """INSERT INTO groups
                   (group_openid, op_member_openid, added_at, removed_at, push_state,
                    push_updated_at, push_last_error)
                   VALUES (?, ?, ?, NULL, NULL, NULL, NULL)
                   ON CONFLICT(group_openid) DO UPDATE SET
                     op_member_openid=excluded.op_member_openid,
                     added_at=excluded.added_at,
                     removed_at=NULL,
                     push_last_error=NULL""",
                (gid, op_member, _now()),
            )
            get_db().commit()

    def ensure_added(self, gid: str) -> None:
        """确保群关系存在（消息事件兜底）。已存在则无操作，不动已有字段。"""
        with _db_lock:
            get_db().execute(
                "INSERT OR IGNORE INTO groups (group_openid, added_at) VALUES (?, ?)",
                (gid, _now()),
            )
            get_db().commit()

    def mark_removed(self, gid: str) -> None:
        """标记退群。push_state 由 clear_push 单独清。"""
        with _db_lock:
            get_db().execute(
                "UPDATE groups SET removed_at=? WHERE group_openid=?",
                (_now(), gid),
            )
            get_db().commit()

    def set_push_state(self, gid: str, state: str, err: Optional[str] = None) -> None:
        """设置推送状态。upsert：群尚未记录时（如先收到推送开关事件）也能写入。"""
        with _db_lock:
            get_db().execute(
                """INSERT INTO groups (group_openid, added_at, push_state, push_updated_at, push_last_error)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(group_openid) DO UPDATE SET
                     push_state=excluded.push_state,
                     push_updated_at=excluded.push_updated_at,
                     push_last_error=excluded.push_last_error""",
                (gid, _now(), state, _now(), err if state == "fail" else None),
            )
            get_db().commit()

    def clear_push(self, gid: str) -> None:
        with _db_lock:
            get_db().execute(
                """UPDATE groups SET push_state=NULL, push_updated_at=NULL, push_last_error=NULL
                   WHERE group_openid=?""",
                (gid,),
            )
            get_db().commit()

    def get_push_state(self, gid: str) -> Optional[str]:
        with _db_lock:
            row = get_db().execute(
                "SELECT push_state FROM groups WHERE group_openid=?", (gid,)
            ).fetchone()
        return row["push_state"] if row else None

    def list_push_ok(self) -> list[str]:
        """返回所有 push_state='ok' 且未退群的群 openid。"""
        with _db_lock:
            rows = get_db().execute(
                """SELECT group_openid FROM groups
                   WHERE push_state='ok' AND removed_at IS NULL
                   ORDER BY group_openid"""
            ).fetchall()
        return [r["group_openid"] for r in rows]

    def get_member_counts(self, gids: list[str]) -> dict[str, Optional[int]]:
        """批量查群人数。返回 {group_openid: member_count or None}，未记录的 gid 不在结果中。"""
        if not gids:
            return {}
        placeholders = ",".join("?" * len(gids))
        with _db_lock:
            rows = get_db().execute(
                f"SELECT group_openid, member_count FROM groups WHERE group_openid IN ({placeholders})",
                gids,
            ).fetchall()
        return {r["group_openid"]: r["member_count"] for r in rows}

    def get(self, gid: str) -> Optional[dict]:
        with _db_lock:
            row = get_db().execute(
                "SELECT * FROM groups WHERE group_openid=?", (gid,)
            ).fetchone()
        return dict(row) if row else None

    def is_active(self, gid: str) -> bool:
        with _db_lock:
            row = get_db().execute(
                "SELECT 1 FROM groups WHERE group_openid=? AND removed_at IS NULL", (gid,)
            ).fetchone()
        return row is not None

    def update_info(self, gid: str, name: Optional[str], intro: Optional[str],
                    member_count: Optional[int]) -> None:
        """更新群基本信息（来自 /v2/groups/{gid}/info: group_name/group_finger_memo/group_member_num）。"""
        with _db_lock:
            get_db().execute(
                """INSERT INTO groups (group_openid, added_at, name, intro, member_count)
                   VALUES (?, '', ?, ?, ?)
                   ON CONFLICT(group_openid) DO UPDATE SET
                     name=excluded.name, intro=excluded.intro, member_count=excluded.member_count""",
                (gid, name, intro, member_count),
            )
            get_db().commit()

    def update_bot_state(self, gid: str, recv_msg_setting: Optional[str],
                         member_role: Optional[str]) -> None:
        """更新群内 bot 状态（来自 /v2/groups/{gid}/bot_state 的 recv_msg_setting/member_role）。"""
        with _db_lock:
            get_db().execute(
                """INSERT INTO groups (group_openid, added_at, recv_msg_setting, member_role)
                   VALUES (?, '', ?, ?)
                   ON CONFLICT(group_openid) DO UPDATE SET
                     recv_msg_setting=excluded.recv_msg_setting,
                     member_role=excluded.member_role""",
                (gid, recv_msg_setting, member_role),
            )
            get_db().commit()


class FriendsRepo:
    """好友关系 + 推送权限。结构同 GroupsRepo，主键 openid。"""

    def upsert_added(self, openid: str) -> None:
        with _db_lock:
            get_db().execute(
                """INSERT INTO friends (openid, added_at, removed_at, push_state, push_updated_at)
                   VALUES (?, ?, NULL, NULL, NULL)
                   ON CONFLICT(openid) DO UPDATE SET
                     added_at=excluded.added_at,
                     removed_at=NULL""",
                (openid, _now()),
            )
            get_db().commit()

    def ensure_added(self, openid: str) -> None:
        """确保好友关系存在（消息事件兜底）。已存在则无操作，不动已有字段。"""
        with _db_lock:
            get_db().execute(
                "INSERT OR IGNORE INTO friends (openid, added_at) VALUES (?, ?)",
                (openid, _now()),
            )
            get_db().commit()

    def mark_removed(self, openid: str) -> None:
        with _db_lock:
            get_db().execute(
                "UPDATE friends SET removed_at=? WHERE openid=?", (_now(), openid)
            )
            get_db().commit()

    def set_push_state(self, openid: str, state: str) -> None:
        with _db_lock:
            get_db().execute(
                """INSERT INTO friends (openid, added_at, push_state, push_updated_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(openid) DO UPDATE SET
                     push_state=excluded.push_state,
                     push_updated_at=excluded.push_updated_at""",
                (openid, _now(), state, _now()),
            )
            get_db().commit()

    def clear_push(self, openid: str) -> None:
        with _db_lock:
            get_db().execute(
                "UPDATE friends SET push_state=NULL, push_updated_at=NULL WHERE openid=?",
                (openid,),
            )
            get_db().commit()

    def get_push_state(self, openid: str) -> Optional[str]:
        with _db_lock:
            row = get_db().execute(
                "SELECT push_state FROM friends WHERE openid=?", (openid,)
            ).fetchone()
        return row["push_state"] if row else None

    def list_push_ok(self) -> list[str]:
        with _db_lock:
            rows = get_db().execute(
                """SELECT openid FROM friends
                   WHERE push_state='ok' AND removed_at IS NULL
                   ORDER BY openid"""
            ).fetchall()
        return [r["openid"] for r in rows]

    def get(self, openid: str) -> Optional[dict]:
        with _db_lock:
            row = get_db().execute(
                "SELECT * FROM friends WHERE openid=?", (openid,)
            ).fetchone()
        return dict(row) if row else None

    def is_active(self, openid: str) -> bool:
        with _db_lock:
            row = get_db().execute(
                "SELECT 1 FROM friends WHERE openid=? AND removed_at IS NULL", (openid,)
            ).fetchone()
        return row is not None
