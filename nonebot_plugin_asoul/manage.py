"""
@Author: star_482
@Date: 2026/7/10
@File: manage
@Description: 插件管理——处理群移除 bot 等事件，自动清理相关数据.
"""
from nonebot import on_notice
from nonebot.adapters.qq.event import GroupDelRobotEvent
from nonebot.log import logger
from nonebot.rule import Rule

from .live_subscription.manager import manager


def _is_group_del_robot(event) -> bool:
    return isinstance(event, GroupDelRobotEvent)


_group_del_robot = on_notice(rule=Rule(_is_group_del_robot), priority=100)


@_group_del_robot.handle()
async def _(event: GroupDelRobotEvent):
    """
    处理退群之后的清理流程
    1、移除该群的订阅
    """
    gid = event.group_openid
    logger.info(f"检测到群移除 bot，group_openid={gid}，清理开播订阅数据")

    removed = await manager.remove_group(gid)
    if removed:
        logger.info(f"已清理群 {gid} 的开播订阅数据")
