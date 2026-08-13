"""
@Author: star_482
@Date: 2026/8/11
@File: relationships
@Description: 用户/群关系与推送权限管理。封装 database.repositories.relationships，
挂钩 QQ 关系事件（FriendAdd/Del、C2CMsgReceive/Reject、GroupAddRobot/DelRobot）自动落库。
供 live_subscription/announcement 等主动推送模块查询。
退群时的订阅数据清理不在此处——由 live_subscription 自己监听 GroupDelRobotEvent 处理，
避免 manage 反向依赖 live_subscription。
"""
from typing import Optional

from nonebot import on_message, on_notice
from nonebot.adapters.qq import Bot
from nonebot.adapters.qq.event import (
    C2CMessageCreateEvent,
    C2CMsgReceiveEvent,
    C2CMsgRejectEvent,
    FriendAddEvent,
    FriendDelEvent,
    GroupAddRobotEvent,
    GroupAtMessageCreateEvent,
    GroupDelRobotEvent,
)
from nonebot.log import logger
from nonebot.rule import Rule

from ..config import config
from ..database.repositories import FriendsRepo, GroupsRepo
from ..markdown import get_welcome_markdown
from .qq_api import get_group_info


class RelationshipService:
    """关系/推送权限业务 API。模块级单例 relations。"""

    def __init__(self) -> None:
        self.groups = GroupsRepo()
        self.friends = FriendsRepo()

    # ── 群 ──
    def mark_group_added(self, gid: str, op_member: Optional[str] = None) -> None:
        self.groups.upsert_added(gid, op_member)

    def mark_group_removed(self, gid: str) -> None:
        self.groups.mark_removed(gid)

    def mark_group_push_ok(self, gid: str) -> None:
        self.groups.set_push_state(gid, "ok")

    def mark_group_push_fail(self, gid: str, err: Optional[str] = None) -> None:
        self.groups.set_push_state(gid, "fail", err)

    def unmark_group_push(self, gid: str) -> None:
        self.groups.clear_push(gid)

    def is_group_push_ok(self, gid: str) -> Optional[bool]:
        """返回推送状态：True=可用, False=不可用, None=未知。"""
        state = self.groups.get_push_state(gid)
        if state == "ok":
            return True
        if state == "fail":
            return False
        return None

    def get_push_ok_groups(self) -> list[str]:
        return self.groups.list_push_ok()

    def is_group_active(self, gid: str) -> bool:
        return self.groups.is_active(gid)

    # ── 好友 ──
    def mark_friend_added(self, openid: str) -> None:
        self.friends.upsert_added(openid)

    def mark_friend_removed(self, openid: str) -> None:
        self.friends.mark_removed(openid)

    def mark_friend_push_ok(self, openid: str) -> None:
        self.friends.set_push_state(openid, "ok")

    def mark_friend_push_fail(self, openid: str) -> None:
        self.friends.set_push_state(openid, "fail")

    def is_friend_push_ok(self, openid: str) -> Optional[bool]:
        state = self.friends.get_push_state(openid)
        if state == "ok":
            return True
        if state == "fail":
            return False
        return None

    def get_push_ok_friends(self) -> list[str]:
        return self.friends.list_push_ok()

    def is_friend_active(self, openid: str) -> bool:
        return self.friends.is_active(openid)


relations = RelationshipService()


# ── 事件挂钩 ──

def _is_friend_add(event) -> bool:
    return isinstance(event, FriendAddEvent)


def _is_friend_del(event) -> bool:
    return isinstance(event, FriendDelEvent)


def _is_c2c_receive(event) -> bool:
    return isinstance(event, C2CMsgReceiveEvent)


def _is_c2c_reject(event) -> bool:
    return isinstance(event, C2CMsgRejectEvent)


def _is_group_add_robot(event) -> bool:
    return isinstance(event, GroupAddRobotEvent)


def _is_group_del_robot(event) -> bool:
    return isinstance(event, GroupDelRobotEvent)


_friend_add = on_notice(rule=Rule(_is_friend_add), priority=100)
_friend_del = on_notice(rule=Rule(_is_friend_del), priority=100)
_c2c_receive = on_notice(rule=Rule(_is_c2c_receive), priority=100)
_c2c_reject = on_notice(rule=Rule(_is_c2c_reject), priority=100)
_group_add_robot = on_notice(rule=Rule(_is_group_add_robot), priority=100)
_group_del_robot = on_notice(rule=Rule(_is_group_del_robot), priority=100)


@_friend_add.handle()
async def _on_friend_add(event: FriendAddEvent):
    relations.mark_friend_added(event.openid)
    logger.info(f"好友添加: {event.openid}")
    if config.welcome_enabled:
        try:
            await _friend_add.send(get_welcome_markdown("friend"))
            logger.info(f"[welcome] 已向新好友发送指令中心: {event.openid}")
        except Exception as e:
            logger.warning(f"[welcome] 好友欢迎消息发送失败 openid={event.openid}: {e!r}")


@_friend_del.handle()
async def _on_friend_del(event: FriendDelEvent):
    relations.mark_friend_removed(event.openid)
    logger.info(f"好友删除: {event.openid}")


@_c2c_receive.handle()
async def _on_c2c_receive(event: C2CMsgReceiveEvent):
    relations.mark_friend_push_ok(event.openid)
    logger.info(f"好友推送开通: {event.openid}")


@_c2c_reject.handle()
async def _on_c2c_reject(event: C2CMsgRejectEvent):
    relations.mark_friend_push_fail(event.openid)
    logger.info(f"好友推送关闭: {event.openid}")


@_group_add_robot.handle()
async def _on_group_add_robot(event: GroupAddRobotEvent, bot: Bot):
    relations.mark_group_added(event.group_openid, event.op_member_openid)
    logger.info(f"bot 被加入群: {event.group_openid} (op={event.op_member_openid})")
    # 拉取群信息（白名单接口，失败不阻塞落库与欢迎消息）
    # 不拉 bot_state：刚进群群主尚未开主动推送，allow_proactive_msg 无意义；
    # push_state 由订阅轮询（admin.py 调 qq_api.get_group_bot_state）或首次主动推送自愈
    info = await get_group_info(bot, event.group_openid)
    if info:
        relations.groups.update_info(
            event.group_openid, info["name"], info["intro"], info["member_count"]
        )
    if config.welcome_enabled:
        try:
            await _group_add_robot.send(get_welcome_markdown("group"))
            logger.info(f"[welcome] 已向新群发送指令中心: {event.group_openid}")
        except Exception as e:
            logger.warning(f"[welcome] 群欢迎消息发送失败 gid={event.group_openid}: {e!r}")


@_group_del_robot.handle()
async def _on_group_del_robot(event: GroupDelRobotEvent):
    gid = event.group_openid
    relations.mark_group_removed(gid)
    relations.unmark_group_push(gid)
    logger.info(f"bot 被移出群: {gid}")


# ── 消息事件兜底：错过 FriendAdd/GroupAddRobot 时，发消息即补录关系 ──

def _is_c2c_msg(event) -> bool:
    return isinstance(event, C2CMessageCreateEvent)


def _is_group_at_msg(event) -> bool:
    return isinstance(event, GroupAtMessageCreateEvent)


_c2c_msg = on_message(rule=Rule(_is_c2c_msg), priority=100, block=False)
_group_at_msg = on_message(rule=Rule(_is_group_at_msg), priority=100, block=False)


@_c2c_msg.handle()
async def _on_c2c_msg(event: C2CMessageCreateEvent):
    relations.friends.ensure_added(event.get_user_id())


@_group_at_msg.handle()
async def _on_group_at_msg(event: GroupAtMessageCreateEvent):
    relations.groups.ensure_added(event.group_openid)
