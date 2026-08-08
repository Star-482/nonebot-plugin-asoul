"""
@Author: star_482
@Date: 2026/7/10
@File: manage
@Description: 插件管理——处理群移除 bot、消息推送开通等事件，自动清理/标记相关数据.
"""
from nonebot import on_notice
from nonebot.adapters.qq.event import GroupDelRobotEvent, GroupMsgReceiveEvent, GroupMsgRejectEvent
from nonebot.log import logger
from nonebot.rule import Rule

from ..live_subscription.manager import manager


def _is_group_del_robot(event) -> bool:
    return isinstance(event, GroupDelRobotEvent)


def _is_group_msg_receive(event) -> bool:
    return isinstance(event, GroupMsgReceiveEvent)


def _is_group_msg_reject(event) -> bool:
    return isinstance(event, GroupMsgRejectEvent)


_group_del_robot = on_notice(rule=Rule(_is_group_del_robot), priority=100)
_group_msg_receive = on_notice(rule=Rule(_is_group_msg_receive), priority=100)
_group_msg_reject = on_notice(rule=Rule(_is_group_msg_reject), priority=100)


@_group_del_robot.handle()
async def _(event: GroupDelRobotEvent):
    """
    处理退群之后的清理流程：
    1. 移除该群的订阅
    2. 清理推送验证状态
    """
    gid = event.group_openid
    logger.info(f"检测到群移除 bot，group_openid={gid}")

    removed = await manager.remove_group(gid)
    if removed:
        logger.info(f"已清理群 {gid} 的开播订阅数据")
    manager.unmark_push(gid)


@_group_msg_receive.handle()
async def _(event: GroupMsgReceiveEvent):
    """收到消息推送开通事件，标记该群推送可用."""
    gid = event.group_openid
    if manager.is_push_ok(gid) is not True:
        manager.mark_push_ok(gid)
        logger.info(f"检测到群推送开通，标记推送可用 gid={gid}")


@_group_msg_reject.handle()
async def _(event: GroupMsgRejectEvent):
    """收到消息推送关闭事件，标记该群推送不可用."""
    gid = event.group_openid
    manager.mark_push_fail(gid)
    logger.info(f"检测到群推送关闭，标记推送不可用 gid={gid}")
