"""Diana 金币排行榜的 SQLite 查询与群贡献流水。"""
from datetime import datetime, timedelta
from typing import Iterable

from ..connection import _db_lock, get_db


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class DianaRankingRepo:
    """宠物金币余额索引、群参与者和近 30 天群贡献金币。"""

    def upsert_user_balance(self, user_id: str, coins: int) -> None:
        with _db_lock:
            get_db().execute(
                """INSERT INTO diana_user_scores (user_id, coins, updated_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(user_id) DO UPDATE SET
                     coins=excluded.coins, updated_at=excluded.updated_at""",
                (user_id, coins, _now()),
            )
            get_db().commit()

    def upsert_user_balances(self, balances: Iterable[tuple[str, int]]) -> None:
        rows = [(user_id, coins, _now()) for user_id, coins in balances]
        if not rows:
            return
        with _db_lock:
            get_db().executemany(
                """INSERT INTO diana_user_scores (user_id, coins, updated_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(user_id) DO UPDATE SET
                     coins=excluded.coins, updated_at=excluded.updated_at""",
                rows,
            )
            get_db().commit()

    def upsert_user_profile(self, user_id: str, display_name: str | None) -> None:
        """更新榜单展示用昵称，不影响宠物存档。"""
        if not display_name:
            return
        with _db_lock:
            db = get_db()
            self._upsert_profile(db, user_id, display_name, _now())
            db.commit()

    def touch_group_member(
        self, group_id: str, user_id: str, display_name: str | None
    ) -> None:
        now = _now()
        with _db_lock:
            db = get_db()
            self._upsert_profile(db, user_id, display_name, now)
            db.execute(
                """INSERT INTO diana_group_members
                     (group_openid, user_id, display_name, last_seen_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(group_openid, user_id) DO UPDATE SET
                     display_name=CASE
                       WHEN excluded.display_name <> '' THEN excluded.display_name
                       ELSE diana_group_members.display_name
                     END,
                     last_seen_at=excluded.last_seen_at""",
                (group_id, user_id, display_name or "", now),
            )
            db.commit()

    def record_group_coin_gain(
        self,
        group_id: str,
        user_id: str,
        display_name: str | None,
        coins: int,
        source: str,
    ) -> None:
        if coins <= 0:
            return
        now = _now()
        with _db_lock:
            db = get_db()
            self._upsert_profile(db, user_id, display_name, now)
            db.execute(
                """INSERT INTO diana_group_members
                     (group_openid, user_id, display_name, last_seen_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(group_openid, user_id) DO UPDATE SET
                     display_name=CASE
                       WHEN excluded.display_name <> '' THEN excluded.display_name
                       ELSE diana_group_members.display_name
                     END,
                     last_seen_at=excluded.last_seen_at""",
                (group_id, user_id, display_name or "", now),
            )
            db.execute(
                """INSERT INTO diana_group_coin_ledger
                     (group_openid, user_id, coins, source, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (group_id, user_id, coins, source, now),
            )
            db.commit()

    def top_users(self, limit: int = 10) -> list[dict]:
        with _db_lock:
            rows = get_db().execute(
                """SELECT scores.user_id, scores.coins,
                          COALESCE(profiles.display_name, '') AS display_name
                   FROM diana_user_scores AS scores
                   LEFT JOIN diana_user_profiles AS profiles USING (user_id)
                   ORDER BY scores.coins DESC, scores.user_id ASC LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def user_rank(self, user_id: str) -> tuple[int, int] | None:
        with _db_lock:
            row = get_db().execute(
                "SELECT coins FROM diana_user_scores WHERE user_id=?", (user_id,)
            ).fetchone()
            if row is None:
                return None
            coins = row["coins"]
            rank_row = get_db().execute(
                """SELECT 1 + COUNT(*) AS rank FROM diana_user_scores
                   WHERE coins > ? OR (coins = ? AND user_id < ?)""",
                (coins, coins, user_id),
            ).fetchone()
        return (rank_row["rank"], coins)

    def top_group_members(self, group_id: str, limit: int = 10) -> list[dict]:
        with _db_lock:
            rows = get_db().execute(
                """SELECT members.user_id, members.display_name, scores.coins
                   FROM diana_group_members AS members
                   JOIN diana_user_scores AS scores USING (user_id)
                   WHERE members.group_openid=?
                   ORDER BY scores.coins DESC, members.user_id ASC LIMIT ?""",
                (group_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def top_groups(self, limit: int = 10, days: int = 30) -> list[dict]:
        cutoff = (datetime.now().astimezone() - timedelta(days=days)).isoformat(timespec="seconds")
        with _db_lock:
            rows = get_db().execute(
                """SELECT ledger.group_openid, SUM(ledger.coins) AS coins,
                          COALESCE(groups.name, '') AS group_name
                   FROM diana_group_coin_ledger AS ledger
                   LEFT JOIN groups ON groups.group_openid=ledger.group_openid
                   WHERE ledger.created_at >= ?
                   GROUP BY ledger.group_openid
                   ORDER BY coins DESC, ledger.group_openid ASC LIMIT ?""",
                (cutoff, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def group_rank(self, group_id: str, days: int = 30) -> tuple[int, int] | None:
        cutoff = (datetime.now().astimezone() - timedelta(days=days)).isoformat(timespec="seconds")
        scores_sql = """SELECT group_openid, SUM(coins) AS coins
                        FROM diana_group_coin_ledger
                        WHERE created_at >= ? GROUP BY group_openid"""
        with _db_lock:
            db = get_db()
            row = db.execute(
                f"SELECT coins FROM ({scores_sql}) WHERE group_openid=?",
                (cutoff, group_id),
            ).fetchone()
            if row is None:
                return None
            coins = row["coins"]
            rank_row = db.execute(
                f"""SELECT 1 + COUNT(*) AS rank FROM ({scores_sql})
                    WHERE coins > ? OR (coins = ? AND group_openid < ?)""",
                (cutoff, coins, coins, group_id),
            ).fetchone()
        return (rank_row["rank"], coins)

    @staticmethod
    def _upsert_profile(
        db, user_id: str, display_name: str | None, now: str
    ) -> None:
        if not display_name:
            return
        db.execute(
            """INSERT INTO diana_user_profiles (user_id, display_name, updated_at)
               VALUES (?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET
                 display_name=excluded.display_name, updated_at=excluded.updated_at""",
            (user_id, display_name, now),
        )
