"""
@Author: star_482
@Date: 2026/8/7
@File: manage
@Description: bot 管理子包--群事件/统计/公告/违禁词拦截。导入即触发 matcher 注册。
"""
from . import relationships, admin_stats, announcement, violation_filter  # noqa: F401
