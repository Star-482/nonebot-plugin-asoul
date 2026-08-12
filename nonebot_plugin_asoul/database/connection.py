"""
@Author: star_482
@Date: 2026/8/11
@File: connection
@Description: 公共 SQLite 连接。单连接 + threading.RLock + WAL，供所有模块共享。
沿用 message_review/store.py 已验证的同步 IO + 锁模式（dev 量级，不引入 aiosqlite）。
"""
import os
import sqlite3
import threading
from typing import Optional

from nonebot.log import logger

from ..config import config
from . import schema as _schema

# 共享锁：保护所有 repository 对单连接的并发访问（读写串行化）。RLock 允许同线程重入，
# 防止 repository 方法间互相调用时死锁。
_db_lock = threading.RLock()
_conn: Optional[sqlite3.Connection] = None


def _db_path() -> str:
    return os.path.join(config.data_path, config.db_path)


def get_db() -> sqlite3.Connection:
    """返回共享连接。首次调用时建连接 + executescript(schema)。线程安全、幂等。"""
    global _conn
    if _conn is not None:
        return _conn
    with _db_lock:
        if _conn is None:
            path = _db_path()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            conn = sqlite3.connect(path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.executescript(_schema.SCHEMA)
            conn.commit()
            _conn = conn
            logger.info(f"公共数据库就绪: {path}")
    return _conn


def init_db() -> sqlite3.Connection:
    """显式初始化（供插件入口调用）。幂等，等价于 get_db()。"""
    return get_db()
