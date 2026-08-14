"""
@Author: star_482
@Date: 2026/8/14
@File: follower
@Description: 直播数据--粉丝统计。5 人（嘉然/贝拉/乃琳/心宜/思诺）粉丝数：
- 每 10 分钟定时轮询调 API 刷内存缓存（不写库）；
- 每日 6:00（东八区）采集一次写 follower_daily_base 基准入库（一天 5 行）；
- /粉丝数据 命令读内存缓存 + 基准表，计算 当前/今日 涨粉（7天/30天后续开放），不实时调 API。
"""
import asyncio
import time
from datetime import datetime, timedelta, timezone

from nonebot import get_driver
from nonebot.adapters.qq import MessageSegment
from nonebot.log import logger
from nonebot.plugin.on import on_command

from ..config import config
from ..database.repositories import FollowerStatsRepo
from ..markdown import get_follower_stats_md
from .admin import _ALL_NAMES
from .api import BiliLiveAPI
from .manager import manager

_TZ = timezone(timedelta(hours=8))

_repo = FollowerStatsRepo()
_api = BiliLiveAPI()

driver = get_driver()


@driver.on_shutdown
async def _close_api() -> None:
    await _api.close()


# ── 内存缓存：{uid: (fetch_ts, follower)}；重启后由轮询/命令重建 ──
_cache: dict[int, tuple[float, int]] = {}

# 单成员拉取失败重试：间隔 2s，最多重试 3 次（共最多 4 次尝试）
_RETRY_DELAY = 2.0
_MAX_RETRIES = 3


def _target_uids() -> list[int]:
    """5 人 uid：从 upstreams 表按 admin._ALL_NAMES 过滤（名字↔uid 以表为准）。"""
    by_name = {u["name"]: u["uid"] for u in manager.get_upstreams()}
    return [by_name[n] for n in _ALL_NAMES if n in by_name]


def _uid_by_name() -> dict[str, int]:
    """5 人 name → uid 映射（命令按名字取 uid）。"""
    by_name = {u["name"]: u["uid"] for u in manager.get_upstreams()}
    return {name: by_name[name] for name in _ALL_NAMES if name in by_name}


async def _fetch_one(uid: int) -> int | None:
    """单成员粉丝数；失败间隔 2s 重试，最多重试 3 次。"""
    for attempt in range(_MAX_RETRIES + 1):
        follower = await _api.get_follower(uid)
        if follower is not None:
            return follower
        if attempt < _MAX_RETRIES:
            logger.warning(
                f"粉丝数获取失败 uid={uid}，{_RETRY_DELAY}s 后重试 ({attempt + 1}/{_MAX_RETRIES})"
            )
            await asyncio.sleep(_RETRY_DELAY)
    logger.warning(f"粉丝数获取失败 uid={uid}（已重试 {_MAX_RETRIES} 次，跳过）")
    return None


async def _fetch_all() -> dict[int, int]:
    """并发查询 5 人粉丝数（各带重试），返回 {uid: follower}；重试仍失败的成员跳过。"""
    uids = _target_uids()
    results: dict[int, int] = {}

    async def _one(uid: int) -> None:
        follower = await _fetch_one(uid)
        if follower is not None:
            results[uid] = follower

    await asyncio.gather(*(_one(u) for u in uids))
    return results


async def refresh_cache() -> None:
    """调 API 拉 5 人粉丝数并更新内存缓存；失败保留旧缓存（降级）。
    顺带做每日基准补采：当日（6:00 日界）基准缺失则用本次值补写（幂等）。"""
    try:
        fetched = await _fetch_all()
    except Exception:
        logger.exception("粉丝数缓存刷新异常")
        return
    if not fetched:
        return
    now = time.time()
    for uid, follower in fetched.items():
        _cache[uid] = (now, follower)
    try:
        await _ensure_daily_base(fetched)
    except Exception:
        logger.exception("粉丝数基准补采异常")


async def _ensure_daily_base(fetched: dict[int, int]) -> None:
    """轮询补采：最新基准日 != 当前日界时，用本次缓存值补写当日基准（幂等 upsert）。

    6:00 cron 正常写基准后 latest day == 今日 → 跳过；
    cron 失败/错过/停机 → 下一个 10 分钟轮询自动补写，最坏延迟 10 分钟；
    凌晨 0~6 点轮询 day 为昨天，补写的是昨日基准（正是"今日涨粉"的基准）。
    """
    now = datetime.now(_TZ)
    day = _day_key(now)
    for uid, follower in fetched.items():
        latest = _repo.latest_base(uid)
        if latest is None or latest[0] != day:
            _repo.upsert_base(uid, day, follower, now.isoformat())
            logger.info(f"补采粉丝数基准 uid={uid} day={day} follower={follower}")


def _cache_is_fresh() -> bool:
    if not _cache:
        return False
    latest = max(ts for ts, _ in _cache.values())
    return time.time() - latest <= config.follower_cache_ttl


async def ensure_cache() -> None:
    """命令入口：缓存新鲜则跳过，否则现场刷新。"""
    if not _cache_is_fresh():
        await refresh_cache()


def _day_key(now: datetime) -> str:
    """东八区以 6:00 为日界：凌晨 0-6 点归前一天。

    naive datetime（无 tzinfo）按东八区解释，aware 则统一 astimezone 转换。
    """
    t = now if now.tzinfo is not None else now.replace(tzinfo=_TZ)
    t = t.astimezone(_TZ)
    if t.hour < config.follower_base_hour:
        t = t - timedelta(days=1)
    return t.strftime("%Y-%m-%d")


async def collect_daily_base() -> None:
    """每日 6:00 基准采集（首选路径）。与轮询补采共用 _ensure_daily_base 幂等写库。"""
    fetched = await _fetch_all()
    if not fetched:
        logger.warning("每日基准采集失败（无任何成员数据），跳过")
        return
    # 同步刷新内存缓存，保证 6:00 后命令查询命中
    ts = time.time()
    for uid, follower in fetched.items():
        _cache[uid] = (ts, follower)
    await _ensure_daily_base(fetched)
    logger.info(f"粉丝数每日基准采集完成 rows={len(fetched)}")


def _fmt_diff(current: int, base: tuple[str, int] | None) -> str:
    """涨粉差格式化；无基准显示 —。"""
    if base is None:
        return "—"
    return f"{current - base[1]:+,}"


def _fmt_num(n: int) -> str:
    return f"{n:,}"


# ── 命令 ──

follower_stats = on_command("粉丝数据", priority=config.command_priority)


@follower_stats.handle()
async def _():
    await ensure_cache()
    now = datetime.now(_TZ)
    uid_by_name = _uid_by_name()

    if not _cache:
        await follower_stats.finish(
            MessageSegment.markdown(
                "## 📊 直播数据 · 粉丝数\n\n> 暂无可展示数据，稍后再试。"
            )
        )
        return

    rows = []
    missing_base_days = set()
    for name in _ALL_NAMES:
        uid = uid_by_name.get(name)
        if uid is None:
            rows.append({"name": name, "current": "—", "today": "—"})
            continue
        entry = _cache.get(uid)
        if entry is None:
            rows.append({"name": name, "current": "—", "today": "—"})
            continue
        current = entry[1]
        base = _repo.base_at_or_before(uid, now.isoformat())
        # 7/30 天涨粉：暂无历史基准，后续逐步开放（基准积累后取消注释）
        # base7 = _repo.base_at_or_before(uid, (now - timedelta(days=7)).isoformat())
        # base30 = _repo.base_at_or_before(uid, (now - timedelta(days=30)).isoformat())
        if base is None:
            missing_base_days.add("today")
        rows.append({
            "name": name,
            "current": _fmt_num(current),
            "today": _fmt_diff(current, base),
            # "week": _fmt_diff(current, base7),
            # "month": _fmt_diff(current, base30),
        })

    updated_at = ""
    if _cache:
        latest = max(ts for ts, _ in _cache.values())
        updated_at = datetime.fromtimestamp(latest, _TZ).strftime("%m-%d %H:%M")
    note = ""
    if missing_base_days:
        note = "暂无每日基准，首次基准将于次日 6:00 生效，今日涨粉暂显示 —"

    content = get_follower_stats_md(
        rows, updated_at=updated_at, base_hour=config.follower_base_hour, note=note
    )
    await follower_stats.finish(MessageSegment.markdown(content))
