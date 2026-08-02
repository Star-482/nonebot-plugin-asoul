"""核心引擎：宠物状态、属性衰减、等级系统."""

import time
from dataclasses import dataclass, field
from typing import Optional

# 存档 schema。新增字段时：
# 1. 直接加到 PetState 的 dataclass 字段（带默认值）和 to_dict()
# 2. from_dict() 会自动丢弃不认识的键，新字段对旧存档使用默认值
# 模块尚未上线，不存在旧格式存档，因此不需要版本号和迁移链。
# 上线后如需不兼容的 schema 变更，再引入 SAVE_VERSION 和 _migrate_save。

# ── 成就标志常量 ──
# 集中管理 achievement_flags 字典的键名，避免裸字符串拼写错误。

class AchievementFlag:
    """achievement_flags 字典的键名常量."""
    INTERACTION_FEED_COUNT = "interaction_feed_count"
    INTERACTION_PLAY_COUNT = "interaction_play_count"
    MEME_TRIGGERS_COUNT = "meme_triggers_count"

# ── 衰减 / 恢复速率 ──
DECAY_HUNGER_PER_HOUR = 0.5        # 每小时饱腹 -0.5
DECAY_MOOD_PER_HOUR = 0.5          # 每小时心情 -0.5
ENERGY_RECOVERY_PER_HOUR = 2.5     # 每小时体力 +2.5（被动恢复）
DECAY_CLOSENESS_PER_DAY = 3.0      # 每天未互动亲密度 -3

# ── 升级经验 ──
EXP_PER_LEVEL = 100

# ── 称号表 ──
TITLES = [
    (1, "新来的嘉心糖"),
    (3, "小草莓"),
    (5, "小草莓守护者"),
    (7, "糖糖糖"),
    (10, "嘉门传教士"),
    (13, "然然的好朋友"),
    (16, "宅舞练习生"),
    (20, "圣嘉然骑士"),
    (25, "嘉心糖大将军"),
    (30, "最好的嘉心糖"),
]


@dataclass
class PetState:
    """嘉然宠物的完整状态.

    IMPORTANT: 外部修改应走 modify() 以确保 clamp + 升级检查。
    直接赋值（如 pet.mood = 50）绕过校验，仅用于恢复 / 序列化场景。
    """

    user_id: str
    hunger: int = 80          # 饱腹度 0-100
    mood: int = 70            # 心情 0-100
    energy: int = 90          # 体力 0-100
    closeness: int = 50       # 亲密度 0-100
    level: int = 1
    exp: int = 0
    coins: int = 100
    outfit: str = "default"
    owned_outfits: list = field(default_factory=lambda: ["default"])
    title: str = "新来的嘉心糖"
    last_tick: float = field(default_factory=time.time)
    created_at: float = field(default_factory=time.time)
    interaction_count: int = 0
    streak_days: int = 0
    last_interaction_date: str = ""       # YYYY-MM-DD
    unlocked_titles: list = field(default_factory=list)
    achievement_flags: dict = field(default_factory=dict)
    triggered_dates: list = field(default_factory=list)   # 已触发的特殊日期 ["MM-DD", ...]
    last_checkin_date: str = ""          # 上次签到日期 YYYY-MM-DD
    checkin_streak: int = 0              # 连续签到天数

    # ── 属性操作 ──

    def clamp(self):
        """将属性固定在 0-100 范围内."""
        self.hunger = max(0, min(100, self.hunger))
        self.mood = max(0, min(100, self.mood))
        self.energy = max(0, min(100, self.energy))
        self.closeness = max(0, min(100, self.closeness))
        return self

    def modify(self, hunger=0, mood=0, energy=0, closeness=0, coins=0, exp=0):
        """批量修改属性（传 0 表示不修改）."""
        self.hunger += hunger
        self.mood += mood
        self.energy += energy
        self.closeness += closeness
        self.coins += coins
        self.exp += exp
        self.clamp()
        self._check_level_up()
        return self

    def _check_level_up(self):
        """检查是否升级."""
        while self.exp >= EXP_PER_LEVEL:
            self.exp -= EXP_PER_LEVEL
            self.level += 1
            self._update_title()

    def _update_title(self):
        """根据等级更新称号."""
        for lv, title in reversed(TITLES):
            if self.level >= lv:
                self.title = title
                if title not in self.unlocked_titles:
                    self.unlocked_titles.append(title)
                break

    # ── 时间衰减 ──

    def tick(self, now: Optional[float] = None) -> dict:
        """应用时间衰减，返回触发的事件列表（饥饿/疲惫提醒等）."""
        if now is None:
            now = time.time()
        elapsed_hours = (now - self.last_tick) / 3600
        self.last_tick = now

        if elapsed_hours <= 0:
            return {}

        # 衰减 / 恢复计算
        self.hunger -= int(DECAY_HUNGER_PER_HOUR * elapsed_hours)
        self.mood -= int(DECAY_MOOD_PER_HOUR * elapsed_hours)
        self.energy += int(ENERGY_RECOVERY_PER_HOUR * elapsed_hours)

        # 饱腹归零时额外扣心情
        if self.hunger <= 0:
            self.mood -= 10

        self.clamp()

        # 阈值提醒
        alerts = {}
        if self.hunger <= 20:
            alerts["hunger_low"] = True
        if self.mood <= 20:
            alerts["mood_low"] = True
        if self.energy <= 15:
            alerts["energy_low"] = True

        return alerts

    def check_daily_decay(self, today_str: str):
        """每日检查：更新连续互动天数 & 亲密度衰减."""
        if self.last_interaction_date and self.last_interaction_date != today_str:
            yesterday = _days_ago(today_str, 1)
            if self.last_interaction_date == yesterday:
                self.streak_days += 1
            else:
                self.streak_days = 0
                self.closeness -= int(DECAY_CLOSENESS_PER_DAY)

        self.last_interaction_date = today_str
        self.clamp()

    # ── 序列化 ──

    def to_dict(self) -> dict:
        return {
            "user_id": self.user_id,
            "hunger": self.hunger,
            "mood": self.mood,
            "energy": self.energy,
            "closeness": self.closeness,
            "level": self.level,
            "exp": self.exp,
            "coins": self.coins,
            "outfit": self.outfit,
            "owned_outfits": self.owned_outfits,
            "title": self.title,
            "last_tick": self.last_tick,
            "created_at": self.created_at,
            "interaction_count": self.interaction_count,
            "streak_days": self.streak_days,
            "last_interaction_date": self.last_interaction_date,
            "unlocked_titles": self.unlocked_titles,
            "achievement_flags": self.achievement_flags,
            "triggered_dates": self.triggered_dates,
            "last_checkin_date": self.last_checkin_date,
            "checkin_streak": self.checkin_streak,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "PetState":
        data = {k: v for k, v in d.items() if k != "user_id"}
        # 不认识的键（如旧版存档中的 version）会被 dataclass 忽略，
        # 因为 PetState 没有对应字段。新增字段对旧存档使用默认值。
        return cls(**data, user_id=d["user_id"])

    @classmethod
    def create(cls, user_id: str) -> "PetState":
        """创建一个新宠物."""
        return cls(user_id=user_id)


def _days_ago(today_str: str, n: int) -> str:
    """返回 N 天前的日期字符串（简易版，不依赖 datetime）."""
    parts = today_str.split("-")
    if len(parts) != 3:
        return today_str
    y, m, d = int(parts[0]), int(parts[1]), int(parts[2])
    d -= n
    while d <= 0:
        m -= 1
        if m <= 0:
            m = 12
            y -= 1
        days_in_month = [0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
        if m == 2 and ((y % 4 == 0 and y % 100 != 0) or y % 400 == 0):
            days_in_month[2] = 29
        d += days_in_month[m]
    return f"{y:04d}-{m:02d}-{d:02d}"