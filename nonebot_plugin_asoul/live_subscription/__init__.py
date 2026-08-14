"""
@Author: star_482
@Date: 2026/6/4
@File: __init__
@Description: B站开播订阅包。注册定时轮询 + 管理命令。
"""
from nonebot import get_driver, require
from nonebot.log import logger
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from ..config import config
from .api import BiliLiveAPI
from .checker import LiveChecker
from .manager import manager
from .notifier import Notifier

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler

_api = BiliLiveAPI()
_notifier = Notifier()
_checker = LiveChecker(_api, _notifier)


async def _poll():
    uids = manager.get_uids()
    if not uids:
        return
    try:
        await _checker.check(uids)
    except Exception as e:
        logger.warning(f"开播轮询异常: {e}")


scheduler.add_job(
    _poll,
    trigger=IntervalTrigger(seconds=config.live_poll_interval),
    id="live_subscription_poll",
    coalesce=True,
    max_instances=1,
    replace_existing=True,
)
logger.info(f"开播轮询已注册，间隔 {config.live_poll_interval}s")

driver = get_driver()


@driver.on_shutdown
async def _shutdown():
    await _api.close()
    logger.info("开播轮询已关闭")


# 注册管理命令
from . import admin as _admin  # noqa: E402,F401

# ── 直播数据：粉丝统计（10 分钟缓存轮询 + 每日 6:00 基准）──
from . import follower as _follower  # noqa: E402,F401


async def _follower_poll():
    try:
        await _follower.refresh_cache()
    except Exception as e:
        logger.warning(f"粉丝数缓存轮询异常: {e}")


async def _follower_daily_base():
    try:
        await _follower.collect_daily_base()
    except Exception as e:
        logger.warning(f"粉丝数每日基准异常: {e}")


scheduler.add_job(
    _follower_poll,
    trigger=IntervalTrigger(seconds=config.follower_poll_interval),
    id="live_follower_poll",
    coalesce=True,
    max_instances=1,
    replace_existing=True,
)
logger.info(f"粉丝数缓存轮询已注册，间隔 {config.follower_poll_interval}s")

scheduler.add_job(
    _follower_daily_base,
    trigger=CronTrigger(hour=config.follower_base_hour, minute=0, timezone="Asia/Shanghai"),
    id="live_follower_daily_base",
    coalesce=True,
    max_instances=1,
    replace_existing=True,
)
logger.info(f"粉丝数每日基准已注册，时刻 {config.follower_base_hour}:00 (Asia/Shanghai)")
