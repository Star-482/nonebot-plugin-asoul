"""抽老婆投票的 SQLite 持久化与排行榜查询。"""
from datetime import datetime
from typing import Iterable, Literal
from zoneinfo import ZoneInfo

from ..connection import _db_lock, get_db


RankPeriod = Literal["total", "month"]
_SHANGHAI = ZoneInfo("Asia/Shanghai")


def _now_in_shanghai(now: datetime | None = None) -> datetime:
    if now is None:
        return datetime.now(_SHANGHAI)
    if now.tzinfo is None:
        return now.replace(tzinfo=_SHANGHAI)
    return now.astimezone(_SHANGHAI)


class WifeVoteRepo:
    """全局图片投票、每日限额与月榜/总榜查询。"""

    def cast_vote(
        self,
        voter_id: str,
        image_name: str,
        daily_limit: int,
        *,
        now: datetime | None = None,
    ) -> dict:
        """投出一票，返回 success / duplicate / limit 及当日使用情况。"""
        if daily_limit < 1:
            raise ValueError("daily_limit 必须大于 0")
        local_now = _now_in_shanghai(now)
        vote_day = local_now.date().isoformat()
        vote_month = local_now.strftime("%Y-%m")
        created_at = local_now.isoformat(timespec="seconds")

        with _db_lock:
            db = get_db()
            used = self._daily_vote_count(db, voter_id, vote_day)
            duplicate = db.execute(
                """SELECT 1 FROM wife_votes
                   WHERE voter_id=? AND vote_day=? AND image_name=?""",
                (voter_id, vote_day, image_name),
            ).fetchone()
            if duplicate is not None:
                return {"status": "duplicate", "used": used, "limit": daily_limit}
            if used >= daily_limit:
                return {"status": "limit", "used": used, "limit": daily_limit}
            db.execute(
                """INSERT INTO wife_votes
                   (image_name, voter_id, vote_day, vote_month, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (image_name, voter_id, vote_day, vote_month, created_at),
            )
            db.commit()
        return {"status": "success", "used": used + 1, "limit": daily_limit}

    def top_images(
        self,
        period: RankPeriod,
        valid_image_names: Iterable[str],
        *,
        limit: int = 10,
        now: datetime | None = None,
    ) -> list[dict]:
        """返回现存图库图片的票数 Top N。"""
        names = _normalized_names(valid_image_names)
        if not names:
            return []
        where, args = self._ranking_where(period, names, now)
        with _db_lock:
            rows = get_db().execute(
                f"""SELECT image_name, COUNT(*) AS votes FROM wife_votes
                    WHERE {where}
                    GROUP BY image_name
                    ORDER BY votes DESC, image_name ASC LIMIT ?""",
                (*args, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def image_rank(
        self,
        image_name: str,
        period: RankPeriod,
        valid_image_names: Iterable[str],
        *,
        now: datetime | None = None,
    ) -> tuple[int, int] | None:
        """返回现存图片的 (排名, 票数)，未获票或已不在图库时返回 None。"""
        names = _normalized_names(valid_image_names)
        if image_name not in names:
            return None
        where, args = self._ranking_where(period, names, now)
        scores_sql = f"""SELECT image_name, COUNT(*) AS votes FROM wife_votes
                         WHERE {where}
                         GROUP BY image_name"""
        with _db_lock:
            db = get_db()
            row = db.execute(
                f"SELECT votes FROM ({scores_sql}) WHERE image_name=?",
                (*args, image_name),
            ).fetchone()
            if row is None:
                return None
            votes = row["votes"]
            rank_row = db.execute(
                f"""SELECT 1 + COUNT(*) AS rank FROM ({scores_sql})
                    WHERE votes > ? OR (votes = ? AND image_name < ?)""",
                (*args, votes, votes, image_name),
            ).fetchone()
        return rank_row["rank"], votes

    @staticmethod
    def _daily_vote_count(db, voter_id: str, vote_day: str) -> int:
        row = db.execute(
            "SELECT COUNT(*) AS count FROM wife_votes WHERE voter_id=? AND vote_day=?",
            (voter_id, vote_day),
        ).fetchone()
        return row["count"] if row else 0

    @staticmethod
    def _ranking_where(
        period: RankPeriod, names: list[str], now: datetime | None
    ) -> tuple[str, tuple[str, ...]]:
        placeholders = ", ".join("?" for _ in names)
        clauses = [f"image_name IN ({placeholders})"]
        args: list[str] = list(names)
        if period == "month":
            clauses.append("vote_month=?")
            args.append(_now_in_shanghai(now).strftime("%Y-%m"))
        elif period != "total":
            raise ValueError(f"未知排行榜周期: {period}")
        return " AND ".join(clauses), tuple(args)


def _normalized_names(names: Iterable[str]) -> list[str]:
    """去重排序，让排行和并列名次不受调用方容器顺序影响。"""
    return sorted({str(name) for name in names if name})
