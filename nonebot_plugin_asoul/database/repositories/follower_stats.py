"""
@Author: star_482
@Date: 2026/8/14
@File: follower_stats
@Description: 粉丝数每日基准 repository。follower_daily_base 表 CRUD。
每日 6:00 采集一次写入（upsert，一天一行），涨粉计算按 "<= 目标时刻的最近基准" 取数。
"""
from typing import Optional

from ..connection import get_db, _db_lock


class FollowerStatsRepo:
    """粉丝数每日基准。写库仅每日 6:00 一次，命令查询只读。"""

    def upsert_base(self, uid: int, day: str, follower: int, fetched_at: str) -> None:
        """写入某基准日的粉丝数；同日重复采集（重跑）则覆盖。"""
        with _db_lock:
            get_db().execute(
                """INSERT INTO follower_daily_base (uid, day, follower, fetched_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(uid, day) DO UPDATE SET
                     follower=excluded.follower, fetched_at=excluded.fetched_at""",
                (uid, day, follower, fetched_at),
            )
            get_db().commit()

    def base_at_or_before(self, uid: int, ts: str) -> Optional[tuple[str, int]]:
        """返回 fetched_at <= ts 的最近一条基准 (day, follower)；无则 None。"""
        with _db_lock:
            row = get_db().execute(
                """SELECT day, follower FROM follower_daily_base
                   WHERE uid=? AND fetched_at<=?
                   ORDER BY fetched_at DESC LIMIT 1""",
                (uid, ts),
            ).fetchone()
        return (row["day"], row["follower"]) if row else None

    def latest_base(self, uid: int) -> Optional[tuple[str, int]]:
        """最新一条基准 (day, follower)；无则 None。"""
        with _db_lock:
            row = get_db().execute(
                """SELECT day, follower FROM follower_daily_base
                   WHERE uid=?
                   ORDER BY fetched_at DESC LIMIT 1""",
                (uid,),
            ).fetchone()
        return (row["day"], row["follower"]) if row else None

    def count(self) -> int:
        with _db_lock:
            row = get_db().execute(
                "SELECT COUNT(*) AS c FROM follower_daily_base"
            ).fetchone()
        return row["c"] if row else 0
