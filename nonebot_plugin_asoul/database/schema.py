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

-- 每群入群欢迎配置（当前生效）
CREATE TABLE IF NOT EXISTS group_welcome (
    group_openid TEXT PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 0,   -- 0=关 1=开
    text TEXT,                             -- 已审核通过的欢迎语
    updated_at TEXT NOT NULL,
    updated_by TEXT                        -- 最后操作者 openid（群管或审核 SUPERUSER）
);

-- 每群关键词撤回配置（群主/管理员通过 /设置撤回关键词 维护）
CREATE TABLE IF NOT EXISTS group_recall_keywords (
    group_openid TEXT PRIMARY KEY,
    keywords TEXT NOT NULL,                -- JSON 数组，命中的群消息会被撤回
    updated_at TEXT NOT NULL,
    updated_by TEXT                        -- 最后操作者 openid
);

-- 自定义欢迎语审核流水
CREATE TABLE IF NOT EXISTS welcome_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_openid TEXT NOT NULL,
    submitter_openid TEXT NOT NULL,
    submitter_role TEXT,                   -- admin/owner
    pending_text TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',-- pending/approved/rejected
    submitted_at TEXT NOT NULL,
    reviewed_at TEXT,
    reviewer_openid TEXT
);
CREATE INDEX IF NOT EXISTS idx_welcome_reviews_status ON welcome_reviews(status);

-- 命令使用统计（替代 usage_detail.jsonl + usage_summary.json）
CREATE TABLE IF NOT EXISTS command_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,                      -- ISO8601 时间，按天统计用 substr(ts,1,10)
    command TEXT NOT NULL,
    user_id TEXT NOT NULL,
    scene_id TEXT NOT NULL DEFAULT '',     -- 群 openid / friend_xxx / guild gid/cid
    status TEXT NOT NULL DEFAULT 'success' -- success / failed
);
CREATE INDEX IF NOT EXISTS idx_cmd_stats_day ON command_stats(substr(ts, 1, 10));
CREATE INDEX IF NOT EXISTS idx_cmd_stats_command ON command_stats(command);
CREATE INDEX IF NOT EXISTS idx_cmd_stats_user ON command_stats(user_id);

-- 粉丝数每日基准（直播数据功能：每日 6:00 采集一次，作为涨粉计算基准）
CREATE TABLE IF NOT EXISTS follower_daily_base (
    uid INTEGER NOT NULL,
    day TEXT NOT NULL,              -- 基准日（东八区，以 6:00 为日界），如 2026-08-15
    follower INTEGER NOT NULL,
    fetched_at TEXT NOT NULL,       -- 采集时刻 ISO8601
    PRIMARY KEY (uid, day)
);
CREATE INDEX IF NOT EXISTS idx_follower_base_time ON follower_daily_base(uid, fetched_at);
"""
