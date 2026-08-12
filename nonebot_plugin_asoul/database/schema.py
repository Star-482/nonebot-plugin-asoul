"""
@Author: star_482
@Date: 2026/8/11
@File: schema
@Description: 集中所有表 DDL。get_db() 首次调用时 executescript 建表。
新增表/列在此追加（仅对新建库生效；老库升级见 scripts/upgrade_schema.py）。
"""

SCHEMA = """
-- 消息审核
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    epoch REAL NOT NULL,
    direction TEXT NOT NULL,
    scene_type TEXT NOT NULL,
    scene_id TEXT NOT NULL,
    user_id TEXT,
    user_name TEXT,
    matcher_module TEXT,
    command TEXT,
    msg_type INTEGER,
    plain_text TEXT,
    content_json TEXT NOT NULL,
    status TEXT
);
CREATE INDEX IF NOT EXISTS idx_msg_scene_epoch ON messages(scene_type, scene_id, epoch DESC);
CREATE INDEX IF NOT EXISTS idx_msg_epoch ON messages(epoch DESC);

-- 群关系 + 推送权限 + 群信息
CREATE TABLE IF NOT EXISTS groups (
    group_openid TEXT PRIMARY KEY,
    op_member_openid TEXT,
    added_at TEXT NOT NULL,
    removed_at TEXT,
    push_state TEXT,
    push_updated_at TEXT,
    push_last_error TEXT,
    name TEXT,
    intro TEXT,
    member_count INTEGER,
    recv_msg_setting TEXT,
    member_role TEXT
);
CREATE INDEX IF NOT EXISTS idx_groups_push_ok ON groups(group_openid) WHERE push_state='ok';

-- 好友关系 + 推送权限
CREATE TABLE IF NOT EXISTS friends (
    openid TEXT PRIMARY KEY,
    added_at TEXT NOT NULL,
    removed_at TEXT,
    push_state TEXT,
    push_updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_friends_push_ok ON friends(openid) WHERE push_state='ok';

-- 群订阅（群 <-> up主 多对多）
CREATE TABLE IF NOT EXISTS subscriptions (
    group_openid TEXT NOT NULL,
    uid INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (group_openid, uid)
);
CREATE INDEX IF NOT EXISTS idx_subs_uid ON subscriptions(uid);

-- 预定义 up 主列表
CREATE TABLE IF NOT EXISTS upstreams (
    uid INTEGER PRIMARY KEY,
    name TEXT NOT NULL
);
"""
