"""
@Author: star_482
@Date: 2026/6/4
@File: manager
@Description: 订阅数据管理：预定义 up主列表 + 群订阅 CRUD + 持久化。
"""
import asyncio
import os
from typing import Optional

from nonebot.log import logger

from ..utils import open_json, save_json
from ..config import config

# open_json / save_json 内部会拼接 config.data_path，这里用相对路径
_UPSTREAMS_FILE = "live_subscription/upstreams.json"
_SUBSCRIPTIONS_FILE = "live_subscription/subscriptions.json"
_PUSH_FILE = "live_subscription/push_verified.json"
# 文件存在性检查用绝对路径
_UPSTREAMS_ABS = os.path.join(config.data_path, _UPSTREAMS_FILE)
_SUBSCRIPTIONS_ABS = os.path.join(config.data_path, _SUBSCRIPTIONS_FILE)
_PUSH_ABS = os.path.join(config.data_path, _PUSH_FILE)

_DEFAULT_UPSTREAMS = {
    "upstreams": [
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
}


class SubscriptionManager:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._upstreams: list[dict] = []
        self._subscriptions: dict[str, list[str]] = {}
        self._push_ok: set[str] = set()   # 已验证推送的群
        self._push_fail: set[str] = set()  # 推送失败的群
        self._load_upstreams()
        self._load_subscriptions()
        self._load_push()

    # ── 预定义列表 ──

    def _load_upstreams(self) -> None:
        if not os.path.exists(_UPSTREAMS_ABS):
            save_json(_UPSTREAMS_FILE, _DEFAULT_UPSTREAMS)
            logger.info(f"已创建默认 up主 列表: {_UPSTREAMS_FILE}")
        data = open_json(_UPSTREAMS_FILE)
        self._upstreams = data.get("upstreams", [])

    def get_upstreams(self) -> list[dict]:
        return list(self._upstreams)

    def get_uids(self) -> list[int]:
        return [u["uid"] for u in self._upstreams]

    def search_upstream(self, keyword: str) -> Optional[dict]:
        keyword_lower = keyword.strip().lower()
        matches = [
            u for u in self._upstreams
            if keyword_lower in u["name"].lower()
        ]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            return None
        # 多个匹配：优先完全匹配
        for u in matches:
            if u["name"].lower() == keyword_lower:
                return u
        return None  # 模糊匹配有多个结果，让调用方处理

    def get_upstream_names(self) -> list[str]:
        return [u["name"] for u in self._upstreams]

    # ── 群订阅 CRUD ──

    def _load_subscriptions(self) -> None:
        """同步加载订阅数据，仅在 __init__ 时调用。"""
        if not os.path.exists(_SUBSCRIPTIONS_ABS):
            save_json(_SUBSCRIPTIONS_FILE, {})
        self._subscriptions = open_json(_SUBSCRIPTIONS_FILE)

    async def _save_subscriptions(self) -> None:
        async with self._lock:
            save_json(_SUBSCRIPTIONS_FILE, self._subscriptions)

    async def subscribe(self, gid: str, uid: int) -> bool:
        uid_str = str(uid)
        async with self._lock:
            if gid not in self._subscriptions:
                self._subscriptions[gid] = []
            if uid_str in self._subscriptions[gid]:
                return False
            self._subscriptions[gid].append(uid_str)
            save_json(_SUBSCRIPTIONS_FILE, self._subscriptions)
            return True

    async def unsubscribe(self, gid: str, uid: int) -> bool:
        uid_str = str(uid)
        async with self._lock:
            group_subs = self._subscriptions.get(gid, [])
            if uid_str not in group_subs:
                return False
            group_subs.remove(uid_str)
            if not group_subs:
                del self._subscriptions[gid]
            save_json(_SUBSCRIPTIONS_FILE, self._subscriptions)
            return True

    async def is_subscribed(self, gid: str, uid: int) -> bool:
        return str(uid) in self._subscriptions.get(gid, [])

    async def remove_group(self, gid: str) -> bool:
        """移除该群的所有订阅记录。返回是否确实有数据被清除。"""
        async with self._lock:
            if gid not in self._subscriptions:
                return False
            del self._subscriptions[gid]
            save_json(_SUBSCRIPTIONS_FILE, self._subscriptions)
            return True

    async def list_for_group(self, gid: str) -> list[dict]:
        uid_strs = self._subscriptions.get(gid, [])
        uid_to_name = {str(u["uid"]): u["name"] for u in self._upstreams}
        return [
            {"uid": int(uid_str), "name": uid_to_name.get(uid_str, f"UID:{uid_str}")}
            for uid_str in uid_strs
        ]

    async def list_all(self) -> dict[str, list[dict]]:
        uid_to_name = {str(u["uid"]): u["name"] for u in self._upstreams}
        result = {}
        async with self._lock:
            for gid, uid_strs in self._subscriptions.items():
                result[gid] = [
                    {"uid": int(uid_str), "name": uid_to_name.get(uid_str, f"UID:{uid_str}")}
                    for uid_str in uid_strs
                ]
        return result

    def get_subscribed_groups(self, uid: int) -> list[str]:
        uid_str = str(uid)
        return [gid for gid, subs in self._subscriptions.items() if uid_str in subs]

    # ── 推送验证状态 ──

    def _load_push(self) -> None:
        """加载推送验证状态文件."""
        if not os.path.exists(_PUSH_ABS):
            save_json(_PUSH_FILE, {"ok": [], "fail": []})
        data = open_json(_PUSH_FILE)
        self._push_ok = set(data.get("ok", []))
        self._push_fail = set(data.get("fail", []))

    def _save_push(self) -> None:
        """持久化推送验证状态."""
        save_json(_PUSH_FILE, {
            "ok": sorted(self._push_ok),
            "fail": sorted(self._push_fail),
        })

    def mark_push_ok(self, gid: str) -> None:
        """标记该群推送可用."""
        self._push_fail.discard(gid)
        self._push_ok.add(gid)
        self._save_push()

    def mark_push_fail(self, gid: str) -> None:
        """标记该群推送不可用."""
        self._push_ok.discard(gid)
        self._push_fail.add(gid)
        self._save_push()

    def unmark_push(self, gid: str) -> None:
        """移除该群的推送状态记录（退群时清理）."""
        self._push_ok.discard(gid)
        self._push_fail.discard(gid)
        self._save_push()

    def is_push_ok(self, gid: str) -> bool | None:
        """返回推送状态：True=可用, False=不可用, None=未知."""
        if gid in self._push_ok:
            return True
        if gid in self._push_fail:
            return False
        return None

    def get_push_ok_groups(self) -> list[str]:
        """返回所有已验证可主动推送的群 openid。"""
        return sorted(self._push_ok)


manager = SubscriptionManager()
