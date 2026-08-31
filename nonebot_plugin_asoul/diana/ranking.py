"""Diana 金币排行榜的数据同步、群贡献记录与 Markdown 文本构造。"""
import logging
from typing import Any

from ..database.repositories import DianaRankingRepo
from .utils import list_pets, load_pet

logger = logging.getLogger(__name__)

_repo = DianaRankingRepo()
_score_index_bootstrapped = False


def ensure_score_index() -> None:
    """首次查询时把既有宠物 JSON 的金币余额导入 SQLite 索引。"""
    global _score_index_bootstrapped
    if _score_index_bootstrapped:
        return
    balances: list[tuple[str, int]] = []
    for user_id in list_pets():
        try:
            pet = load_pet(user_id)
        except (OSError, ValueError):
            logger.exception("读取 Diana 存档失败，跳过金币榜导入 user=%s", user_id)
            continue
        if pet is not None:
            balances.append((pet.user_id, pet.coins))
    _repo.upsert_user_balances(balances)
    _score_index_bootstrapped = True


def record_group_participant(event: Any) -> str | None:
    """更新参与者昵称并记录群成员；返回群 ID，私聊返回 None。"""
    try:
        user_id = event.get_user_id()
        display_name = _event_display_name(event)
        # QQ 私聊事件没有 author 信息；此时绝不能以 openid 兜底名覆盖群聊昵称。
        _repo.upsert_user_profile(user_id, display_name)
        group_id = str(getattr(event, "group_openid", "") or "")
        if group_id:
            _repo.touch_group_member(group_id, user_id, display_name)
        return group_id or None
    except Exception:
        # 榜单数据是附属能力，写入失败不能中断正常的签到或互动。
        logger.exception("记录 Diana 榜单参与者失败")
        return None


def record_checkin_coin_gain(event: Any, coins: int) -> None:
    """记录群签到获得的正向金币；私聊签到不计入群贡献。"""
    group_id = record_group_participant(event)
    if not group_id or coins <= 0:
        return
    try:
        user_id = event.get_user_id()
        _repo.record_group_coin_gain(
            group_id, user_id, _event_display_name(event), coins, "checkin"
        )
    except Exception:
        logger.exception("记录 Diana 群签到金币失败")


def record_interaction_coin_gain(event: Any, result: dict) -> None:
    """记录群内互动的固定金币奖励与随机事件金币掉落。"""
    group_id = record_group_participant(event)
    if not group_id:
        return
    try:
        user_id = event.get_user_id()
        display_name = _event_display_name(event)
        action = str(result.get("action") or "interaction")
        changes = result.get("changes") or {}
        fixed_coins = _positive_int(changes.get("coins"))
        if fixed_coins:
            _repo.record_group_coin_gain(
                group_id, user_id, display_name, fixed_coins, f"interaction:{action}"
            )
        bonus_coins = _positive_int(result.get("coin_bonus_amount"))
        if bonus_coins:
            _repo.record_group_coin_gain(
                group_id, user_id, display_name, bonus_coins, "event_bonus"
            )
        event_coins = _positive_int(result.get("event_coin_gain"))
        if event_coins:
            _repo.record_group_coin_gain(
                group_id, user_id, display_name, event_coins, "event"
            )
    except Exception:
        logger.exception("记录 Diana 群互动金币失败")


def get_global_coin_board(
    event: Any, user_id: str, coins: int
) -> tuple[list[dict], tuple[int, int] | None]:
    ensure_score_index()
    _repo.upsert_user_balance(user_id, coins)
    record_group_participant(event)
    return _repo.top_users(), _repo.user_rank(user_id)


def get_group_coin_board(
    event: Any,
    user_id: str,
    coins: int,
) -> tuple[list[dict], list[dict], tuple[int, int] | None]:
    ensure_score_index()
    _repo.upsert_user_balance(user_id, coins)
    group_id = record_group_participant(event)
    if not group_id:
        return [], [], None
    return (
        _repo.top_group_members(group_id),
        _repo.top_groups(),
        _repo.group_rank(group_id),
    )


def format_global_coin_board(rows: list[dict], mine: tuple[int, int] | None) -> str:
    lines = ["## 💰 全站金币榜", ""]
    lines.extend(_format_rows(rows, name_key="display_name", empty="暂时还没有金币记录。"))
    lines.append("")
    if mine is None:
        lines.append("> 我的排名：暂无记录")
    else:
        lines.append(f"> 我的排名：第 **{mine[0]}** 名 · **{mine[1]}** 嘉心糖币")
    return "\n".join(lines)


def format_group_coin_board(
    members: list[dict],
    groups: list[dict],
    current_group: tuple[int, int] | None,
) -> str:
    lines = ["## 💰 本群成员金币榜", ""]
    lines.append("> 仅统计已参与嘉然玩法的本群成员")
    lines.append("")
    lines.extend(
        _format_rows(
            members,
            name_key="display_name",
            empty="本群暂时还没有参与记录。",
            quoted=True,
            emphasized=False,
        )
    )
    lines.extend(["", "## 🌐 全部群金币榜", "", "> 按最近 30 天群内获得的金币统计", ""])
    lines.extend(
        _format_rows(
            groups,
            name_key="group_name",
            empty="暂时还没有群贡献记录。",
            fallback_prefix="未命名群",
            quoted=True,
            emphasized=False,
        )
    )
    lines.append("")
    if current_group is None:
        lines.append("当前群暂无贡献记录，完成群内签到或互动后即可入榜。")
    else:
        lines.append(
            f"📍 当前群：第 **{current_group[0]}** 名 · "
            f"近 30 天 **{current_group[1]}** 群贡献金币"
        )
    return "\n".join(lines)


def _format_rows(
    rows: list[dict],
    name_key: str,
    empty: str,
    fallback_prefix: str = "用户",
    quoted: bool = False,
    emphasized: bool = True,
) -> list[str]:
    if not rows:
        return [f"> {empty}"]
    lines = []
    prefix = "> " if quoted else ""
    for rank, row in enumerate(rows, start=1):
        name = _safe_name(
            str(row.get(name_key) or ""),
            str(row.get("user_id") or row.get("group_openid") or ""),
            fallback_prefix,
        )
        coins = int(row["coins"])
        if emphasized:
            lines.append(f"{prefix}{rank}. **{name}** · **{coins}** 嘉心糖币")
        else:
            lines.append(f"{prefix}{rank}. {name} · {coins} 嘉心糖币")
    return lines


def _event_display_name(event: Any) -> str | None:
    """仅返回 QQ 事件实际携带的昵称，缺失时不产生兜底昵称。"""
    author = getattr(event, "author", None)
    value = getattr(author, "username", None) or getattr(author, "nickname", None) or ""
    name = " ".join(str(value).split()).strip()
    if not name:
        return None
    return _safe_name(name, "")


def _safe_name(value: str, fallback_id: str, fallback_prefix: str = "用户") -> str:
    name = " ".join(value.split()).strip()
    if not name:
        suffix = fallback_id[-6:] if fallback_id else "未知"
        name = f"{fallback_prefix} {suffix}"
    return name.replace("*", "＊").replace("_", "＿").replace("[", "［").replace("]", "］")[:40]


def _positive_int(value: object) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0
