"""
@Author: star_482
@Date: 2026/8/6
@File: store
@Description: 兼容入口。存储实现已迁至 nonebot_plugin_asoul.database.repositories.messages，
本模块仅 re-export 以保持 message_review 内部 import 不变。
"""
from ..database.repositories.messages import (  # noqa: F401
    MessageStore,
    get_store,
    init_store,
    store,
)

__all__ = ["MessageStore", "get_store", "init_store", "store"]
