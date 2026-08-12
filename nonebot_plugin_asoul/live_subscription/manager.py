"""
@Author: star_482
@Date: 2026/6/4
@File: manager
@Description: 订阅数据管理：预定义 up主列表 + 群订阅 CRUD。
持久化迁至 database（subscriptions/upstreams 表）。推送权限状态由 manage.relationships 负责。
退群时自行监听 GroupDelRobotEvent 清理订阅（不依赖 manage，避免循环依赖）。
"""
from typing import Optional

from nonebot import on_notice
from nonebot.adapters.qq.event import GroupDelRobotEvent
from nonebot.log import logger
from nonebot.rule import Rule

from ..database.repositories import SubscriptionsRepo, UpstreamsRepo

_DEFAULT_UPSTREAMS = [
    {"uid": 672328094, "name": "嘉然"},
    {"uid": 672353429, "name": "贝拉"},
    {"uid": 672342685, "name": "乃琳"},
    {"uid": 1795147802, "name": "柚恩"},
    {"uid": 1669777785, "name": "露早"},
    {"uid": 3537115310721781, "name": "思诺"},
    {"uid": 3537115310721181, "name": "心宜"},
    {"uid": 3493139945884106, "name": "雪糕"},
    {"uid": 401315430, "name": "星瞳"},
    {"uid": 1878154667, "name": "沐霂"},
    {"uid": 1660392980, "name": "恬豆"},
    {"uid": 1217754423, "name": "又一"},
    {"uid": 1900141897, "name": "梨安"},
    {"uid": 7706705, "name": "阿梓"},
]


def _is_group_del_robot(event) -> bool:
    return isinstance(event, GroupDelRobotEvent)


class SubscriptionManager:
    def __init__(self) -> None:
        self._upstreams_repo = UpstreamsRepo()
        self._subs_repo = SubscriptionsRepo()
        # upstreams 表为空时灌默认（首次启动或迁移未覆盖）
        if self._upstreams_repo.count() == 0:
            self._upstreams_repo.load_defaults(_DEFAULT_UPSTREAMS)
            logger.info(f"已灌入默认 up主 列表: {len(_DEFAULT_UPSTREAMS)} 条")

    # ── 预定义列表 ──

    def get_upstreams(self) -> list[dict]:
        return self._upstreams_repo.list()

    def get_uids(self) -> list[int]:
        return [u["uid"] for u in self.get_upstreams()]

    def search_upstream(self, keyword: str) -> Optional[dict]:
        return self._upstreams_repo.search(keyword)

    def get_upstream_names(self) -> list[str]:
        return self._upstreams_repo.names()

    # ── 群订阅 CRUD ──

    async def subscribe(self, gid: str, uid: int) -> bool:
        return await self._subs_repo.subscribe(gid, uid)

    async def unsubscribe(self, gid: str, uid: int) -> bool:
        return await self._subs_repo.unsubscribe(gid, uid)

    async def is_subscribed(self, gid: str, uid: int) -> bool:
        return await self._subs_repo.is_subscribed(gid, uid)

    async def remove_group(self, gid: str) -> bool:
        """移除该群的所有订阅记录。返回是否确实有数据被清除。"""
        return await self._subs_repo.remove_group(gid)

    async def list_for_group(self, gid: str) -> list[dict]:
        return await self._subs_repo.list_for_group(gid)

    async def list_all(self) -> dict[str, list[dict]]:
        return await self._subs_repo.list_all()

    def get_subscribed_groups(self, uid: int) -> list[str]:
        return self._subs_repo.get_subscribed_groups(uid)


manager = SubscriptionManager()


# 退群时自行清理订阅（不依赖 manage，避免循环依赖）
_group_del_robot = on_notice(rule=Rule(_is_group_del_robot), priority=100)


@_group_del_robot.handle()
async def _on_group_del_robot(event: GroupDelRobotEvent):
    gid = event.group_openid
    removed = await manager.remove_group(gid)
    if removed:
        logger.info(f"检测到群移除 bot，已清理群 {gid} 的开播订阅数据")
