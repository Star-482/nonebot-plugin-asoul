"""
@Author: star_482
@Date: 2026/8/11
@File: database
@Description: 公共数据库服务子包。共享 SQLite 连接 + 集中 schema + repository 模式，
供 message_review / manage.relationships / live_subscription 等所有模块复用。
连接初始化在插件入口 __init__.py 首引 init_db() 触发，先于一切业务子包。
旧数据迁移与老库表结构升级见 scripts/migrate_db.py 与 scripts/upgrade_schema.py，
不在运行时代码内。
"""
from .connection import get_db, init_db
from .repositories import MessageStore, get_store, init_store

__all__ = ["get_db", "init_db", "MessageStore", "get_store", "init_store"]
