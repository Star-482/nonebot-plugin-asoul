"""Diana 宠物系统自定义异常.

所有业务错误通过这些异常抛出，api.py 层捕获后转为 {success: False, text: ...} 字典，
保持对外 API 不变。调用方无需感知异常层次。
"""


class DianaError(Exception):
    """Diana 宠物系统异常基类."""


class ActionNotFoundError(DianaError):
    """动作/物品 ID 不存在."""

    def __init__(self, action_id: str):
        self.action_id = action_id
        super().__init__(f"不知道'{action_id}'是什么呢……")


class InsufficientCoinsError(DianaError):
    """嘉心糖币不足."""

    def __init__(self):
        super().__init__("嘉心糖币不够了……让然然去打工会不会好一点？")


class CostumeLockedError(DianaError):
    """服装未解锁."""

    def __init__(self, reason: str = ""):
        self.reason = reason
        super().__init__(reason or "还没有解锁这件服装哦~")


class CostumeNotFoundError(DianaError):
    """服装 ID/名称不存在."""

    def __init__(self, costume_id: str):
        self.costume_id = costume_id
        super().__init__(f"没有'{costume_id}'这件服装呢……")


class InsufficientStatError(DianaError):
    """前置条件不满足——某个 stat 不足."""

    STAT_LABELS = {
        "coins": "嘉心糖币", "energy": "体力", "hunger": "饱腹度",
        "mood": "心情", "closeness": "亲密度",
    }

    def __init__(self, stat: str, current: int, required: int):
        self.stat = stat
        self.current = current
        self.required = required
        label = self.STAT_LABELS.get(stat, stat)
        super().__init__(f"{label}不足（需要 {required}，当前 {current}）")


class PetBusyError(DianaError):
    """宠物忙碌中——上一个互动的耗时还未结束."""

    def __init__(self, remain_seconds: int):
        self.remain = remain_seconds
        if remain_seconds >= 3600:
            h = remain_seconds // 3600
            m = (remain_seconds % 3600) // 60
            remain_str = f"{h}小时{m}分钟" if m else f"{h}小时"
        elif remain_seconds >= 60:
            m = remain_seconds // 60
            s = remain_seconds % 60
            remain_str = f"{m}分钟{s}秒" if s else f"{m}分钟"
        else:
            remain_str = f"{remain_seconds}秒"
        super().__init__(f"然然还在忙呢……还要等{remain_str}哦~")