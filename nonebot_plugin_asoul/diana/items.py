"""Item 数据模型与注册表.

Item 是 YAML 中一个互动动作的不可变运行时表示.
ItemRegistry 从 items.yaml 加载全部 item 并关联对话.
"""

import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

from .core import PetState
from .dialogues import DialogueSet,DialogueRegistry
from .exceptions import ActionNotFoundError, InsufficientStatError

# ── 经验倍率 ──
# 经验 = 亲密度增量 × 此值。由 character.yaml 配置，ItemRegistry 加载时设置。
EXP_MULTIPLIER: int = 2

# ── stat 键名集合 ──
_STAT_KEYS = frozenset({"hunger", "mood", "energy", "closeness", "coins"})


@dataclass(frozen=True)
class Item:
    """一个互动动作的不可变表示.

    Parameters
    ----------
    id : str
        YAML key，如 "鸡胸肉".
    category : str
        food / play / work / social / daily.
    emoji : str
    description : str
    requires : dict
        前置条件 {stat: min_value}，如 {"coins": 12, "energy": 40}.
    duration : int
        耗时（秒），宠物进入忙碌状态.
    effects : dict
        固定 stat 变化 {"hunger": 25, "mood": 15, ...}.
    random_effects : dict
        随机 stat 变化 {"mood": [-5, 0, 5, 10], ...}，apply() 时随机选取.
    dialogue : DialogueSet or None
        关联的对话池.
    """

    id: str
    category: str
    emoji: str
    description: str
    requires: dict = field(default_factory=dict)
    duration: int = 0
    effects: dict = field(default_factory=dict)
    random_effects: dict = field(default_factory=dict)
    dialogue: 'DialogueSet | None' = None

    def validate(self, pet: PetState) -> None:
        """检查前置条件；不满足则抛 InsufficientStatError."""
        for stat, required in self.requires.items():
            current = getattr(pet, stat, 0)
            if current < required:
                raise InsufficientStatError(stat, current, required)

    def resolve_effects(self) -> dict:
        """固定效果 + 随机效果 → 本次执行的确定值."""
        resolved = dict(self.effects)
        for key, choices in self.random_effects.items():
            if choices:
                resolved[key] = random.choice(choices)
        return resolved

    def apply(self, pet: PetState, recent_dialogues: list[str]) -> dict:
        """将效果应用到宠物状态，返回 {changes, dialogue, leveled_up}.

        Parameters
        ----------
        pet : PetState
            目标宠物.
        recent_dialogues : list[str]
            对话历史（用于反重复）.

        Returns
        -------
        dict with keys:
            changes — 本次确定的 stat 变化.
            dialogue — 选中的对话文本.
            leveled_up — 是否升级.
        """
        old_level = pet.level
        resolved = self.resolve_effects()
        exp_gain = resolved.get("closeness", 0) * EXP_MULTIPLIER
        pet.modify(**resolved, exp=exp_gain)
        dialogue_text = self.dialogue.pick(recent_dialogues) if self.dialogue else ""
        return {
            "changes": resolved,
            "dialogue": dialogue_text,
            "leveled_up": pet.level > old_level,
        }


class ItemRegistry:
    """从 items.yaml 加载所有互动动作，构建 id → Item 映射.

    用法::

        registry = ItemRegistry(data_dir, dialogue_registry)
        item = registry.get("鸡胸肉")  # Item or ActionNotFoundError
    """

    def __init__(self, data_dir: Path, dialogue_registry: 'DialogueRegistry | None' = None):
        self._items: dict[str, Item] = {}
        self._by_category: dict[str, list[Item]] = {}
        self._load(data_dir, dialogue_registry)

    # ── 公共接口 ──

    def get(self, action_id: str) -> Item:
        """按 ID 获取 Item；不存在抛 ActionNotFoundError."""
        item = self._items.get(action_id)
        if item is None:
            raise ActionNotFoundError(action_id)
        return item

    def list_by_category(self, category: str) -> list[Item]:
        """列出某类目的所有 Item."""
        return list(self._by_category.get(category, []))

    def list_all(self) -> list[Item]:
        """列出所有 Item."""
        return list(self._items.values())

    def __contains__(self, action_id: str) -> bool:
        return action_id in self._items

    def __len__(self) -> int:
        return len(self._items)

    # ── 内部 ──

    def _load(self, data_dir: Path, dialogue_registry) -> None:
        global EXP_MULTIPLIER

        # 从 character.yaml 读取经验倍率
        char_path = data_dir / "character.yaml"
        if char_path.exists():
            with open(char_path, "r", encoding="utf-8") as f:
                char_data = yaml.safe_load(f) or {}
            EXP_MULTIPLIER = char_data.get("exp_closeness_multiplier", 2)

        path = data_dir / "items.yaml"
        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

        for item_id, data in raw.items():
            category = data.get("category", "daily")
            emoji = data.get("emoji", "")
            description = data.get("description", "")
            requires = data.get("requires", {})
            duration = data.get("duration", 0)

            # 解析 effects 与 random_effects
            if "effects" in data:
                effects = dict(data["effects"])
                random_effects = data.get("random_effects", {})
            else:
                # 兼容旧格式：顶层 stat 键 + <stat>_random 键
                effects, random_effects = self._parse_flat_effects(data)

            # 关联对话
            dialogue = None
            if dialogue_registry:
                dialogue = dialogue_registry.get_for_item(category, item_id)

            item = Item(
                id=item_id,
                category=category,
                emoji=emoji,
                description=description,
                requires=requires,
                duration=duration,
                effects=effects,
                random_effects=random_effects,
                dialogue=dialogue,
            )
            self._items[item_id] = item
            self._by_category.setdefault(category, []).append(item)

    @staticmethod
    def _parse_flat_effects(data: dict) -> tuple[dict, dict]:
        """兼容旧 YAML 格式：顶层 stat 键 → effects, <stat>_random → random_effects."""
        effects = {}
        random_effects = {}
        for key, value in data.items():
            if key in _STAT_KEYS:
                effects[key] = value
            elif key.endswith("_random"):
                stat = key[:-7]  # 去掉 "_random" 后缀
                if isinstance(value, list):
                    random_effects[stat] = value
        return effects, random_effects
