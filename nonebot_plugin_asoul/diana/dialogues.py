"""对话系统：DialogueSet 类 + DialogueRegistry 注册表."""

import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


@dataclass(frozen=True)
class DialogueSet:
    """不可变对话池。一个池可被多个 Item / Event 实例共享。

    Parameters
    ----------
    key : str
        标识符，如 "feed_鸡胸肉".
    lines : tuple[str, ...]
        对话文本列表.
    """

    key: str
    lines: tuple[str, ...] = field(default_factory=tuple)

    def pick(self, avoid: list[str] | None = None) -> str:
        """随机选一条，避免与 `avoid` 中最近条目重复.

        最多避免最近 5 条；若可选项为空则退化为完全随机.
        """
        avoid = avoid or []
        available = [line for line in self.lines if line not in avoid[-5:]]
        if not available:
            available = list(self.lines)
        if not available:
            return ""  # 空池
        return random.choice(available)

    def __bool__(self) -> bool:
        return len(self.lines) > 0

    def __len__(self) -> int:
        return len(self.lines)


class DialogueRegistry:
    """从 dialogues.yaml 加载所有对话，提供按 Item / Event 查找的接口."""

    def __init__(self, data_dir: Path):
        self._data: dict[str, DialogueSet] = {}
        self._load(data_dir)

    # ── Item 对话 ──

    def get_for_item(self, category: str, item_id: str) -> DialogueSet:
        """查找 Item 关联的对话池.

        尝试顺序: {category}_{item_id} → {category}_{item_id_prefix} → fallback.
        """
        # 精确匹配
        key = f"{category}_{item_id}"
        ds = self._data.get(key)
        if ds:
            return ds

        # 前缀匹配（如 "play_宅舞20连" 匹配 "play_宅舞"）
        if "_" in item_id:
            prefix = item_id.split("_")[0] if "_" in item_id else item_id
            key2 = f"{category}_{prefix}"
            ds = self._data.get(key2)
            if ds:
                return ds

        # fallback: 任意包含 item_id 的键
        for k, v in self._data.items():
            if item_id in k:
                return v

        return self._fallback_for(category)

    # ── Event 对话 ──

    def get_for_event(self, event_name: str) -> Optional[DialogueSet]:
        """查找 Event 关联的对话池（可选）. 目前在 YAML 中事件通常无独立对话."""
        return self._data.get(event_name)

    # ── 特殊对话 ──

    @property
    def greeting(self) -> DialogueSet:
        return self._data.get("greeting", DialogueSet("greeting", ("嗨！嘉心糖！🍓",)))

    @property
    def idle(self) -> DialogueSet:
        return self._data.get("idle", DialogueSet("idle", ("今天天气真好呢~",)))

    def get_stat_low(self, stat: str) -> DialogueSet:
        """获取低属性警告对话池."""
        return self._data.get(f"stat_low_{stat}", DialogueSet(f"stat_low_{stat}", ()))

    # ── 内部 ──

    def _load(self, data_dir: Path) -> None:
        path = data_dir / "dialogues.yaml"
        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

        for key, lines in raw.items():
            if isinstance(lines, list) and lines:
                self._data[key] = DialogueSet(key=key, lines=tuple(lines))

    def _fallback_for(self, category: str) -> DialogueSet:
        """分类 fallback 对话."""
        fallbacks = {
            "food": ("嗯~ 好吃！", "谢谢投喂！", "吃饱了好开心~", "好耶！"),
            "play": ("好好玩！", "再来一次！", "开心开心~"),
            "work": ("努力工作！", "辛苦但值得！", "为了梦想加油！"),
            "social": ("诶嘿~", "谢谢你~", "好开心~"),
            "daily": ("嗯嗯~", "好的~", "知道啦~"),
        }
        lines = fallbacks.get(category, ("好耶！",))
        return DialogueSet(key=f"_fallback_{category}", lines=tuple(lines))
