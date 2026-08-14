"""
@Author: star_482
@Date: 2026/8/11
@File: repositories
@Description: database repository 入口。按领域组织，各 repository 共用 connection.get_db()。
"""
from .messages import MessageStore, get_store, init_store, store
from .relationships import GroupsRepo, FriendsRepo
from .subscriptions import SubscriptionsRepo, UpstreamsRepo
from .group_admin import GroupWelcomeRepo, WelcomeReviewRepo
from .command_stats import CommandStatsRepo

__all__ = [
    "MessageStore",
    "get_store",
    "init_store",
    "store",
    "GroupsRepo",
    "FriendsRepo",
    "SubscriptionsRepo",
    "UpstreamsRepo",
    "GroupWelcomeRepo",
    "WelcomeReviewRepo",
    "CommandStatsRepo",
]
