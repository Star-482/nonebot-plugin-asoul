"""
@Author: star_482
@Date: 2026/6/4
@File: api
@Description: B站直播 API 封装。POST 批量查询 up主 直播状态。
"""
from dataclasses import dataclass
from typing import Optional

import httpx
from nonebot.log import logger

from ..config import config


@dataclass
class LiveInfo:
    uid: int
    uname: str
    room_id: int
    short_id: int
    live_status: int  # 0=未开播, 1=直播中, 2=轮播中
    title: str
    cover: str
    area_name: str
    parent_area_name: str
    live_time: str

    @property
    def is_live(self) -> bool:
        return self.live_status == 1

    @property
    def url(self) -> str:
        return f"https://live.bilibili.com/{self.short_id or self.room_id}"


class BiliLiveAPI:
    URL = "https://api.live.bilibili.com/room/v1/Room/get_status_info_by_uids"

    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        "Referer": "https://live.bilibili.com/",
        "Content-Type": "application/json",
    }

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            headers=self.HEADERS, timeout=config.live_poll_http_timeout
        )

    async def fetch_live_status(self, uids: list[int]) -> dict[int, LiveInfo]:
        if not uids:
            return {}

        try:
            resp = await self._client.post(self.URL, json={"uids": uids})
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, ValueError):
            logger.warning("直播状态 API 请求失败")
            return {}

        if data.get("code") != 0:
            logger.warning(
                f"直播状态 API 返回错误: code={data.get('code')} message={data.get('message')}"
            )
            return {}

        result: dict[int, LiveInfo] = {}
        for uid_str, raw in (data.get("data") or {}).items():
            try:
                uid = int(uid_str)
                result[uid] = LiveInfo(
                    uid=uid,
                    uname=raw.get("uname", ""),
                    room_id=raw.get("room_id", 0),
                    short_id=raw.get("short_id", 0),
                    live_status=raw.get("live_status", 0),
                    title=raw.get("title", ""),
                    cover=raw.get("cover_from_user") or raw.get("keyframe", ""),
                    area_name=raw.get("area_v2_name", ""),
                    parent_area_name=raw.get("area_v2_parent_name", ""),
                    live_time=raw.get("live_time", ""),
                )
            except (KeyError, TypeError, ValueError):
                continue

        return result

    # ── 粉丝数（公开接口 GET /x/relation/stat）──

    FOLLOWER_URL = "https://api.bilibili.com/x/relation/stat"

    async def get_follower(self, uid: int) -> Optional[int]:
        """查询单个 up 主粉丝数；失败/非 0 code 返回 None（白名单风格，不抛）。"""
        try:
            resp = await self._client.get(
                self.FOLLOWER_URL,
                params={"vmid": uid},
                headers={"Referer": "https://www.bilibili.com/"},
            )
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, ValueError):
            logger.warning(f"粉丝数 API 请求失败 uid={uid}")
            return None
        if data.get("code") != 0:
            logger.warning(
                f"粉丝数 API 返回错误 uid={uid}: code={data.get('code')} message={data.get('message')}"
            )
            return None
        follower = (data.get("data") or {}).get("follower")
        return int(follower) if isinstance(follower, (int, float)) else None

    async def close(self) -> None:
        await self._client.aclose()
