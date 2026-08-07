"""
@Author: star_482
@Date: 2026/8/6
@File: broadcaster
@Description: WS 客户端广播注册表。每客户端一个有界 asyncio.Queue；publish 满则丢最旧。
"""
import asyncio
from typing import Any


class Broadcaster:
    def __init__(self, max_queue: int = 256):
        self._queues: set[asyncio.Queue] = set()
        self._max = max_queue

    def register(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=self._max)
        self._queues.add(q)
        return q

    def unregister(self, q: asyncio.Queue) -> None:
        self._queues.discard(q)

    @property
    def client_count(self) -> int:
        return len(self._queues)

    def publish(self, msg: dict) -> None:
        """向所有 WS 客户端投递消息；队列满时丢最旧以让位最新。"""
        for q in list(self._queues):
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    q.put_nowait(msg)
                except asyncio.QueueFull:
                    pass


broadcaster = Broadcaster()
