"""活跃群资料与 Bot 群状态的定时同步。"""
import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.triggers.cron import CronTrigger
from nonebot import get_bot, require
from nonebot.adapters.qq import Bot
from nonebot.log import logger

from ..config import config
from .qq_api import get_group_bot_state, get_group_info
from .relationships import relations

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler


_TIMEZONE = ZoneInfo("Asia/Shanghai")
_BOT_RETRY_INTERVAL_SECONDS = 2
# 群资料、Bot 群状态两个接口均为 30 QPM；每轮各调用一次，间隔至少 2 秒。
_REQUEST_INTERVAL_SECONDS = 2


def _yesterday_epoch_range() -> tuple[float, float]:
    """返回上海时区昨天 00:00 到今天 00:00 的 Unix 时间戳范围。"""
    today_start = datetime.now(_TIMEZONE).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    yesterday_start = today_start - timedelta(days=1)
    return yesterday_start.timestamp(), today_start.timestamp()


async def _get_connected_bot(group_id: str) -> Bot | None:
    """获取已连接的 Bot；首次失败后每 2 秒重试两次。"""
    for attempt in range(3):
        try:
            bot = get_bot()
        except (KeyError, ValueError):
            if attempt < 2:
                await asyncio.sleep(_BOT_RETRY_INTERVAL_SECONDS)
            continue
        if isinstance(bot, Bot):
            return bot
        logger.warning(f"[群信息维护] 当前默认 Bot 不是 QQ Adapter，跳过群 {group_id}")
        return None
    logger.warning(f"[群信息维护] Bot 重连超时，跳过群 {group_id}")
    return None


async def refresh_active_group_info() -> None:
    """刷新昨天有消息记录的活跃群资料与 Bot 群状态。"""
    if not config.review_enabled:
        logger.info("[群信息维护] 消息审核未开启，无法确定昨日活跃群，跳过同步")
        return

    start_epoch, end_epoch = _yesterday_epoch_range()
    group_ids = relations.groups.list_active_with_messages(start_epoch, end_epoch)
    if not group_ids:
        logger.info("[群信息维护] 昨日没有符合条件的活跃群")
        return

    info_updated = 0
    state_updated = 0
    push_disabled = 0
    for index, group_id in enumerate(group_ids):
        bot = await _get_connected_bot(group_id)
        if bot is None:
            continue

        info = await get_group_info(bot, group_id)
        if info is not None:
            relations.groups.update_info(
                group_id,
                info["name"],
                info["intro"],
                info["member_count"],
            )
            info_updated += 1

        bot_state = await get_group_bot_state(bot, group_id)
        if bot_state is not None:
            relations.groups.update_bot_state(
                group_id,
                bot_state["recv_msg_setting"],
                bot_state["member_role"],
            )
            state_updated += 1
            if bot_state["allow_proactive_msg"] is True:
                relations.mark_group_push_ok(group_id)
            elif bot_state["allow_proactive_msg"] is False:
                relations.mark_group_push_fail(group_id, "群未开启机器人主动消息")
                push_disabled += 1

        if index + 1 < len(group_ids):
            await asyncio.sleep(_REQUEST_INTERVAL_SECONDS)

    logger.info(
        f"[群信息维护] 同步完成：候选 {len(group_ids)} 群，"
        f"群资料更新 {info_updated}，Bot 状态更新 {state_updated}，"
        f"主动消息未开启 {push_disabled}"
    )


scheduler.add_job(
    refresh_active_group_info,
    trigger=CronTrigger(hour=3, minute=0, timezone=_TIMEZONE),
    id="group_info_maintenance",
    coalesce=True,
    max_instances=1,
    replace_existing=True,
)
logger.info("群信息维护任务已注册：每天 03:00 (Asia/Shanghai)")
