"""
@Author: star_482
@Date: 2026/8/13
@File: command_stats
@Description: 命令使用统计 repository。command_stats 表 CRUD + 聚合查询，
替代原 usage_detail.jsonl + usage_summary.json 文件存储。
"""
from typing import Optional

from ..connection import get_db, _db_lock

# 可 GROUP BY 聚合的列白名单（防 SQL 注入）
_GROUP_COLUMNS = {"command", "user_id", "scene_id"}


class CommandStatsRepo:
    """命令使用统计。每次命令调用 insert 一行；统计查询走索引聚合。"""

    def insert(self, record: dict) -> None:
        with _db_lock:
            get_db().execute(
                """INSERT INTO command_stats (ts, command, user_id, scene_id, status)
                   VALUES (?, ?, ?, ?, ?)""",
                (record["ts"], record["command"], record["user_id"],
                 record.get("scene_id", ""), record.get("status", "success")),
            )
            get_db().commit()

    def count_total(self) -> int:
        with _db_lock:
            row = get_db().execute(
                "SELECT COUNT(*) AS c FROM command_stats"
            ).fetchone()
        return row["c"] if row else 0

    def count_by_date(self, date: str) -> int:
        """某天（YYYY-MM-DD）调用次数，走 substr 表达式索引。"""
        with _db_lock:
            row = get_db().execute(
                "SELECT COUNT(*) AS c FROM command_stats WHERE substr(ts, 1, 10)=?",
                (date,),
            ).fetchone()
        return row["c"] if row else 0

    def count_distinct_users(self, date: Optional[str] = None) -> int:
        """不同用户数；date 为 None 时统计全量。"""
        if date:
            sql = ("SELECT COUNT(DISTINCT user_id) AS c FROM command_stats "
                   "WHERE substr(ts, 1, 10)=?")
            args = (date,)
        else:
            sql = "SELECT COUNT(DISTINCT user_id) AS c FROM command_stats"
            args = ()
        with _db_lock:
            row = get_db().execute(sql, args).fetchone()
        return row["c"] if row else 0

    def count_by_command(self, date: str) -> list[tuple[str, int]]:
        """某天各命令调用次数，降序。"""
        with _db_lock:
            rows = get_db().execute(
                """SELECT command, COUNT(*) AS c FROM command_stats
                   WHERE substr(ts, 1, 10)=? GROUP BY command ORDER BY c DESC""",
                (date,),
            ).fetchall()
        return [(r["command"], r["c"]) for r in rows]

    def top(self, column: str, limit: int = 10) -> list[tuple[str, int]]:
        """按 column（command/user_id/scene_id）聚合排行，降序取前 limit。"""
        if column not in _GROUP_COLUMNS:
            raise ValueError(f"不支持的统计列: {column}")
        with _db_lock:
            rows = get_db().execute(
                f"SELECT {column} AS k, COUNT(*) AS c FROM command_stats "
                f"GROUP BY {column} ORDER BY c DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [(r["k"], r["c"]) for r in rows]

    def recent(self, limit: int = 10) -> list[dict]:
        """最近 limit 条明细（主键倒序，不扫全表）。"""
        with _db_lock:
            rows = get_db().execute(
                "SELECT ts, command, user_id, scene_id, status FROM command_stats "
                "ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
