"""事件系统：Event 类层次、EventRegistry、ConditionChecker.

事件分为 4 类：
- RandomEvent — 概率触发
- KeywordEvent — 关键词匹配
- DateEvent — 特殊日期（MM-DD）
- AchievementEvent — 成就条件

ConditionChecker 通过装饰器注册条件检查函数，替代 if/elif 链.
"""

import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import yaml

from .core import PetState, AchievementFlag
from .dialogues import DialogueSet, DialogueRegistry
from .utils import today_str


# ── ConditionChecker：装饰器驱动的成就条件注册 ──

class ConditionChecker:
    """成就条件检查器注册表.

    通过 @ConditionChecker.register("name") 装饰器注册新的条件类型::

        @ConditionChecker.register("streak_days")
        def _check(pet, value): return pet.streak_days >= value
    """

    _checkers: dict[str, Callable] = {}

    @classmethod
    def register(cls, name: str):
        """装饰器：注册一个条件检查函数.

        Parameters
        ----------
        name : str
            条件名，对应 events.yaml 中 achievement.condition 的键.
        """
        def decorator(fn: Callable) -> Callable:
            cls._checkers[name] = fn
            return fn
        return decorator

    @classmethod
    def check_all(cls, pet: PetState, conditions: dict) -> bool:
        """检查 pet 是否满足 conditions 中所有条件."""
        for key, value in conditions.items():
            checker = cls._checkers.get(key)
            if checker is None:
                continue  # 未知条件→跳过（可扩展）
            if not checker(pet, value):
                return False
        return True


# ── 内置成就条件注册 ──

@ConditionChecker.register("streak_days")
def _check_streak_days(pet: PetState, value: int) -> bool:
    return pet.streak_days >= value


@ConditionChecker.register("closeness")
def _check_closeness(pet: PetState, value: int) -> bool:
    return pet.closeness >= value


@ConditionChecker.register("level")
def _check_level(pet: PetState, value: int) -> bool:
    return pet.level >= value


@ConditionChecker.register("interaction_feed_count")
def _check_feed_count(pet: PetState, value: int) -> bool:
    return pet.achievement_flags.get(AchievementFlag.INTERACTION_FEED_COUNT, 0) >= value


@ConditionChecker.register("interaction_play_count")
def _check_play_count(pet: PetState, value: int) -> bool:
    return pet.achievement_flags.get(AchievementFlag.INTERACTION_PLAY_COUNT, 0) >= value


@ConditionChecker.register("meme_triggers")
def _check_meme_triggers(pet: PetState, value: int) -> bool:
    count = sum(
        1 for k in pet.achievement_flags
        if k.startswith("meme_") and pet.achievement_flags[k]
    )
    return count >= value


# ── Event 类层次 ──

@dataclass
class Event:
    """事件基类.

    Parameters
    ----------
    id : str
        事件 ID.
    type : str
        random / meme / special_date / achievement.
    name : str
        显示名称.
    text : str
        事件描述文本.
    effects : dict
        stat 变化.
    dialogue : DialogueSet or None
    """

    id: str
    type: str
    name: str
    text: str
    effects: dict = field(default_factory=dict)
    dialogue: 'DialogueSet | None' = None

    def judge(self, **ctx) -> bool:
        """判断事件是否触发."""
        raise NotImplementedError

    def apply(self, pet: PetState) -> None:
        """将事件效果应用到宠物."""
        if self.effects:
            pet.modify(**self.effects)


@dataclass
class RandomEvent(Event):
    """随机事件：每次 tick 按概率触发."""

    probability: int = 5  # 0-100

    def judge(self, **ctx) -> bool:
        return random.randint(1, 100) <= self.probability


@dataclass
class KeywordEvent(Event):
    """关键词事件：用户消息包含关键词时触发."""

    keywords: list[str] = field(default_factory=list)

    def judge(self, text: str = "", **ctx) -> bool:
        return any(kw in text for kw in self.keywords)


@dataclass
class DateEvent(Event):
    """特殊日期事件：当天日期匹配 MM-DD 时触发（同一天去重）."""

    date: str = ""  # "MM-DD"

    def judge(self, today: str = "", triggered_dates: frozenset | None = None, **ctx) -> bool:
        triggered_dates = triggered_dates or frozenset()
        today_mmdd = today[5:] if len(today) >= 10 else today
        return today_mmdd == self.date and self.date not in triggered_dates


@dataclass
class AchievementEvent(Event):
    """成就事件：条件满足时触发."""

    conditions: dict = field(default_factory=dict)

    def judge(self, pet: PetState | None = None, **ctx) -> bool:
        if pet is None:
            return False
        return ConditionChecker.check_all(pet, self.conditions)


# ── EventRegistry ──

class EventRegistry:
    """从 events.yaml 加载所有事件，提供 tick() 和 check_keywords() 接口."""

    def __init__(self, data_dir: Path, dialogue_registry: 'DialogueRegistry | None' = None):
        self.random_events: list[RandomEvent] = []
        self.keyword_events: dict[str, KeywordEvent] = {}   # keyword → event
        self.date_events: dict[str, DateEvent] = {}         # "MM-DD" → event
        self.achievement_events: list[AchievementEvent] = []
        self._load(data_dir, dialogue_registry)

    # ── 公共接口 ──

    def tick(self, pet: PetState) -> list[Event]:
        """每次 tick 检查事件，最多触发一个。优先级：成就 > 特殊日期 > 随机."""
        today = today_str()

        # 1. 成就（优先级最高）
        for evt in self.achievement_events:
            ach_key = f"ach_{evt.id}"
            if ach_key not in pet.achievement_flags and evt.judge(pet=pet):
                evt.apply(pet)
                pet.achievement_flags[ach_key] = True
                return [evt]

        # 2. 特殊日期
        today_mmdd = today[5:]
        if today_mmdd in self.date_events:
            evt = self.date_events[today_mmdd]
            triggered_set = frozenset(pet.triggered_dates)
            if evt.judge(today=today, triggered_dates=triggered_set):
                evt.apply(pet)
                pet.triggered_dates.append(today_mmdd)
                return [evt]

        # 3. 随机事件：打乱列表，首个通过概率判定的触发
        shuffled = list(self.random_events)
        random.shuffle(shuffled)
        for evt in shuffled:
            if evt.judge():
                evt.apply(pet)
                return [evt]

        return []

    def check_keywords(self, pet: PetState, text: str) -> list[Event]:
        """检测用户消息中的关键词，返回触发的事件（最多 1 个）."""
        triggered: list[Event] = []
        for keyword, evt in self.keyword_events.items():
            if evt.judge(text=text):
                evt.apply(pet)
                triggered.append(evt)
                evt_key = f"meme_{evt.id}"
                # 计数
                pet.achievement_flags[AchievementFlag.MEME_TRIGGERS_COUNT] = \
                    pet.achievement_flags.get(AchievementFlag.MEME_TRIGGERS_COUNT, 0) + 1
                # 标记首次触发
                pet.achievement_flags[evt_key] = True
                break  # 一轮只触发一个 meme 事件
        return triggered

    def track_interaction(self, pet: PetState, category: str) -> None:
        """追踪互动类型计数（成就用）."""
        if category == "food":
            key = AchievementFlag.INTERACTION_FEED_COUNT
        elif category == "play":
            key = AchievementFlag.INTERACTION_PLAY_COUNT
        else:
            return
        pet.achievement_flags[key] = pet.achievement_flags.get(key, 0) + 1

    # ── 内部 ──

    def _load(self, data_dir: Path, dialogue_registry) -> None:
        path = data_dir / "events.yaml"
        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

        # 随机事件
        for name, data in raw.get("random", {}).items():
            evt = RandomEvent(
                id=name, type="random", name=name,
                text=data.get("text", ""),
                effects=data.get("effects", {}),
                probability=data.get("probability", 5),
                dialogue=dialogue_registry.get_for_event(name) if dialogue_registry else None,
            )
            self.random_events.append(evt)

        # 关键词事件
        for name, data in raw.get("meme", {}).items():
            data = dict(data)
            keywords = data.pop("keywords", [])
            evt = KeywordEvent(
                id=name, type="meme", name=name,
                text=data.get("text", ""),
                effects=data.get("effects", {}),
                keywords=keywords,
                dialogue=dialogue_registry.get_for_event(name) if dialogue_registry else None,
            )
            for kw in keywords:
                self.keyword_events[kw] = evt

        # 特殊日期
        for date_str, data in raw.get("special_dates", {}).items():
            evt = DateEvent(
                id=date_str, type="special_date", name=data.get("name", ""),
                text=data.get("text", ""),
                effects=data.get("effects", {}),
                date=date_str,
                dialogue=dialogue_registry.get_for_event(date_str) if dialogue_registry else None,
            )
            self.date_events[date_str] = evt

        # 成就
        for name, data in raw.get("achievements", {}).items():
            evt = AchievementEvent(
                id=name, type="achievement", name=data.get("name", name),
                text=data.get("text", ""),
                effects=data.get("effects", {}),
                conditions=data.get("condition", {}),
                dialogue=dialogue_registry.get_for_event(name) if dialogue_registry else None,
            )
            self.achievement_events.append(evt)
