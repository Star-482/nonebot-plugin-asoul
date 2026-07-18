"""
@Author: star_482
@Date: 2026/5/18
@File: commands
@Description: NoneBot 命令注册层——将 DianaSession 暴露为 QQ Bot 指令.
"""

import asyncio
import json
import logging
import os
import time as _time
from collections import OrderedDict
from datetime import date, timedelta
from pathlib import Path
from threading import Lock as ThreadLock

from nonebot import get_driver
from nonebot.adapters import Event
from nonebot.internal.matcher import Matcher
from nonebot.params import CommandArg
from nonebot.plugin.on import on_command
from nonebot.adapters.qq import Message, MessageSegment
from nonebot.adapters.qq.models import (
    Action,
    Button,
    InlineKeyboard,
    InlineKeyboardRow,
    MessageKeyboard,
    Permission,
    RenderData,
)

from ..config import config

logger = logging.getLogger(__name__)

from .session import DianaSession, shutdown, list_items
from .exceptions import DianaError
from ..markdown import _text_chain

# ── stat 变化的中文标签和图标 ──
_CHANGE_LABELS = [
    ("hunger", "饱腹", "🍽️"), ("mood", "心情", "😊"),
    ("energy", "体力", "⚡"), ("closeness", "亲密度", "💕"),
    ("coins", "金币", "💰"),
]

# ── 用户缓存 ──

USER_CACHE: OrderedDict[str, DianaSession] = OrderedDict()
CACHE_MAX_SIZE = 50
USER_CACHE_LOCK = asyncio.Lock()


async def get_session(user_id: str) -> DianaSession:
    """获取或创建 DianaSession，LRU 淘汰."""
    if user_id in USER_CACHE:
        USER_CACHE.move_to_end(user_id)
        return USER_CACHE[user_id]
    async with USER_CACHE_LOCK:
        if user_id in USER_CACHE:
            USER_CACHE.move_to_end(user_id)
            return USER_CACHE[user_id]
        if len(USER_CACHE) >= CACHE_MAX_SIZE:
            oldest_key, oldest_session = USER_CACHE.popitem(last=False)
            try:
                await oldest_session.close()
            except Exception:
                logger.exception("DianaSession close() failed during eviction for user=%s", oldest_key)
        USER_CACHE[user_id] = DianaSession(user_id=user_id)
    return USER_CACHE[user_id]


# ── MD 消息构造 ──

def _md_image(url: str, width: int, height: int, alt: str = "") -> str:
    """QQ Markdown 图片字面量."""
    if not url or width <= 0 or height <= 0:
        return ""
    return f"![{alt} #{width}px #{height}px]({url})"


def _changes_text(changes: dict) -> str:
    """stat 变化 → 单行：🍽️ 饱腹 +25  ·  😊 心情 +15."""
    parts = []
    for key, label, icon in _CHANGE_LABELS:
        val = changes.get(key, 0)
        if val == 0:
            continue
        sign = "+" if val > 0 else ""
        parts.append(f"{icon} {label} {sign}{val}")
    return "  ·  ".join(parts)


def _build_text_msg(text: str) -> MessageSegment:
    return MessageSegment.markdown(str(text))


def _build_interaction_msg(result: dict) -> MessageSegment:
    """构建互动结果 MD. 无 COS URL 时降级为纯文本."""
    img_url = result.get("image_url")
    lines = []

    # 交互卡片图
    if img_url:
        img_w = result.get("image_width", 0)
        img_h = result.get("image_height", 0)
        if img_line := _md_image(img_url, img_w, img_h):
            lines.append(img_line)
            lines.append("")

    # 对话
    if dialogue := result.get("dialogue"):
        lines.append(f"> {dialogue}")

    # stat 变化
    if changes := result.get("changes"):
        if cl := _changes_text(changes):
            lines.append("")
            lines.append(cl)

    # 事件
    if et := result.get("events_triggered"):
        lines.append("")
        for i, t in enumerate(et):
            urls = result.get("event_urls", [])
            if i < len(urls) and urls[i]:
                if ei := _md_image(urls[i], 600, 380):
                    lines.append(ei)
                    lines.append("")
            lines.append(t)

    # 金币掉落
    if cb := result.get("coin_bonus"):
        lines.append("")
        lines.append(cb)

    # 防御：result 无任何可渲染字段（如 error_result）时降级为文本，避免空 markdown 被QQ拒绝
    if not lines:
        lines.append(str(result.get("text", "……")))
    return MessageSegment.markdown("\n".join(lines).strip())


def _build_status_msg(result: dict) -> MessageSegment:
    """构建状态卡片 MD."""
    lines = []
    if img_url := result.get("image_url"):
        img_w = result.get("image_width", 0)
        img_h = result.get("image_height", 0)
        if il := _md_image(img_url, img_w, img_h):
            lines.append(il)
    if alerts := result.get("alerts"):
        if lines:
            lines.append("")
        lines.append(f"⚠ {alerts}")
    if not lines:
        lines.append(str(result.get("text", "...")))
    return MessageSegment.markdown("\n".join(lines))


def _build_talk_msg(result: dict) -> MessageSegment:
    """构建聊天 MD."""
    lines = []
    if img_url := result.get("image_url"):
        if il := _md_image(img_url, 600, 380):
            lines.append(il)
            lines.append("")
    lines.append(str(result.get("text", "...")))
    return MessageSegment.markdown("\n".join(lines))


def _build_costume_msg(result: dict) -> MessageSegment:
    """构建衣柜卡片 MD."""
    img_url = result.get("image_url")
    img_w = result.get("image_width", 0)
    img_h = result.get("image_height", 0)
    if il := _md_image(img_url, img_w, img_h):
        return MessageSegment.markdown(il)
    if img := result.get("image"):
        return MessageSegment.file_image(img)
    return MessageSegment.markdown("（衣柜卡片渲染失败，请稍后再试）")


async def send_result(result: dict, matcher: Matcher, kind: str = "text") -> None:
    """发送 result 为 QQ MD 消息.

    kind 由调用方显式指定：
    - "interaction"  互动结果（投喂/玩耍/工作/社交/日常）
    - "status"       状态卡片
    - "talk"         聊天
    - "costume"      衣柜
    - "text"         纯文本（错误/换装/帮助等）
    """
    build = {
        "interaction": _build_interaction_msg,
        "status": _build_status_msg,
        "talk": _build_talk_msg,
        "costume": _build_costume_msg,
        "text": lambda r: _build_text_msg(r.get("text", str(r))),
    }
    builder = build.get(kind, build["text"])
    await matcher.send(builder(result))


def _extract_arg(args: Message) -> str:
    return args.extract_plain_text().strip()


def _error_result(exc: DianaError) -> dict:
    return {"success": False, "text": str(exc), "stats": {}}


# ── Shutdown ──

driver = get_driver()


@driver.on_shutdown
async def _shutdown() -> None:
    try:
        for session in USER_CACHE.values():
            try:
                await session.close()
            except Exception:
                logger.exception(
                    "DianaSession close() failed during shutdown for user=%s",
                    getattr(getattr(session, "pet", None), "user_id", "?"),
                )
    finally:
        await shutdown()


# ── 命令注册 ──

diana_status = on_command("然然状态", aliases={"状态", "我的然然", "然然信息"}, priority=config.command_priority)
diana_wardrobe = on_command("然然衣柜", aliases={"服装", "衣柜", "换装列表"}, priority=config.command_priority)
diana_feed = on_command("投喂", aliases={"喂", "吃", "喂食"}, priority=config.command_priority)
diana_play = on_command("玩耍", aliases={"玩"}, priority=config.command_priority)
diana_work = on_command("打工", aliases={"直播", "工作"}, priority=config.command_priority)
diana_costume = on_command("换装", aliases={"换上", "穿"}, priority=config.command_priority)
diana_unlock = on_command("解锁", aliases={"购买"}, priority=config.command_priority)
diana_talk = on_command("然然", aliases={"然然聊天"}, priority=config.command_priority)
diana_interact = on_command("互动", aliases={"撒娇", "和然然互动"}, priority=config.command_priority)
diana_daily = on_command("日常", aliases={"日常活动"}, priority=config.command_priority)
diana_help = on_command("然然帮助", aliases={"宠物帮助", "然然指令"}, priority=config.command_priority)
diana_checkin = on_command("签到", aliases={"嘉心糖签到", "每日签到"}, priority=config.command_priority)


# ── 签到奖励分层 ──
# 按全用户排名决定奖励币数

def _compute_checkin_reward(global_rank: int) -> int:
    """按全用户排名返回奖励嘉心糖币数."""
    if global_rank == 1:
        return 50
    elif global_rank <= 3:
        return 30
    elif global_rank <= 10:
        return 20
    else:
        return 10


# ── 签到排名文件 ──

_CHECKIN_WRITE_LOCK = ThreadLock()


def _get_checkin_dir() -> Path:
    """返回签到排名文件目录."""
    d = Path(config.data_path) / "diana" / "checkin"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _today_checkin_path() -> Path:
    return _get_checkin_dir() / f"{date.today().isoformat()}.json"


def _load_today_checkin() -> dict:
    """读取今日签到文件，不存在返回空结构."""
    fp = _today_checkin_path()
    if not fp.exists():
        return {"date": date.today().isoformat(), "checkins": []}
    with open(fp, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_today_checkin(data: dict) -> None:
    """写入今日签到文件（原子写入）."""
    fp = _today_checkin_path()
    tmp = fp.with_suffix(".json.tmp")
    with _CHECKIN_WRITE_LOCK:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, fp)


def _get_group_id(event: Event) -> str:
    """从 Event 提取群 ID，私聊返回 'dm'."""
    if gid := getattr(event, "group_openid", ""):
        return str(gid)
    return "dm"


def _compute_group_rank(checkins: list, group_id: str) -> int:
    """计算群内排名：该 group_id 在 checkins 中出现次数 + 1."""
    return sum(1 for c in checkins if c.get("group_id") == group_id) + 1


def _build_checkin_success_msg(
    group_rank: int,
    global_rank: int,
    coins: int,
    streak: int,
    balance: int,
    is_dm: bool,
) -> str:
    """构建签到成功消息."""
    lines = ["✅ **签到成功！**", ""]
    if not is_dm:
        lines.append(f"🏅 本群第 **{group_rank}** 个签到")
    lines.append(f"🌍 全用户第 **{global_rank}** 个签到")
    lines.append(f"💰 获得 **{coins}** 嘉心糖币")
    lines.append(f"🔥 连续签到 **{streak}** 天")
    lines.append("")
    lines.append(f"当前余额：🪙 {balance} 嘉心糖币")
    return "\n".join(lines)


def _build_checkin_dup_msg(streak: int, balance: int) -> str:
    """构建重复签到消息."""
    return f"你今天已经签到过了哦~\n🔥 连续签到 {streak} 天 | 🪙 余额 {balance} 嘉心糖币"


def _mk_cmd_button(button_id: str, label: str, command: str) -> Button:
    """构造指令注入按钮（点击插入输入框，用户自行发送）。"""
    return Button(
        id=button_id,
        render_data=RenderData(label=label, visited_label=label, style=1),
        action=Action(
            type=2,
            permission=Permission(type=2),
            data=command,
            reply=False,
            enter=False,
            unsupport_tips=f"请手动发送：{command}",
        ),
    )


def _diana_nav_keyboard() -> MessageKeyboard:
    """签到后引流到 Diana 其他玩法的按钮面板。"""
    return MessageKeyboard(
        content=InlineKeyboard(
            rows=[
                InlineKeyboardRow(buttons=[
                    _mk_cmd_button("diana_nav_status", "看状态", "/然然状态"),
                    _mk_cmd_button("diana_nav_costume", "换装", "/换装"),
                    _mk_cmd_button("diana_nav_help", "更多玩法", "/然然帮助"),
                ]),
                InlineKeyboardRow(buttons=[
                    _mk_cmd_button("diana_nav_feed", "投喂", "/投喂 鸡胸肉"),
                    _mk_cmd_button("diana_nav_play", "玩耍", "/玩 连连看"),
                    _mk_cmd_button("diana_nav_interact", "互动", "/互动 摸摸头"),
                ]),
            ]
        )
    )


async def _send_checkin_reply(matcher: Matcher, text: str) -> None:
    """发送签到回复（markdown 文本 + Diana 玩法引流按钮）。"""
    await matcher.send(
        MessageSegment.markdown(text) + MessageSegment.keyboard(_diana_nav_keyboard())
    )


# ── 签到 handler ──

@diana_checkin.handle()
async def _(event: Event, matcher: Matcher):
    user_id = event.get_user_id()
    group_id = _get_group_id(event)
    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()

    session = await get_session(user_id)
    pet = session.pet

    # 检查是否已签到
    if pet.last_checkin_date == today:
        await _send_checkin_reply(matcher, _build_checkin_dup_msg(pet.checkin_streak, pet.coins))
        return

    # 计算连续签到天数
    if pet.last_checkin_date == "":
        pet.checkin_streak = 1
    elif pet.last_checkin_date == yesterday:
        pet.checkin_streak += 1
    else:
        pet.checkin_streak = 1

    # 读取今日签到文件，计算排名
    checkin_data = _load_today_checkin()
    checkins = checkin_data["checkins"]

    # 去重检查（防御：PetState 说没签到但文件里已有记录）
    existing_ids = {c["user_id"] for c in checkins}
    if user_id in existing_ids:
        pet.last_checkin_date = today
        session._save()
        await _send_checkin_reply(matcher, _build_checkin_dup_msg(pet.checkin_streak, pet.coins))
        return

    global_rank = len(checkins) + 1
    group_rank = _compute_group_rank(checkins, group_id)

    # 计算奖励
    coin_reward = _compute_checkin_reward(global_rank)

    # 更新 PetState
    pet.coins += coin_reward
    pet.last_checkin_date = today

    # 追加签到记录
    checkins.append({
        "user_id": user_id,
        "group_id": group_id,
        "timestamp": _time.time(),
        "coins": coin_reward,
    })
    _save_today_checkin(checkin_data)
    session._save()

    is_dm = group_id == "dm"
    msg = _build_checkin_success_msg(
        group_rank=group_rank,
        global_rank=global_rank,
        coins=coin_reward,
        streak=pet.checkin_streak,
        balance=pet.coins,
        is_dm=is_dm,
    )
    await _send_checkin_reply(matcher, msg)


# ── 互动 handler（5 个统一路径：interact）──

@diana_feed.handle()
async def _(event: Event, matcher: Matcher, args: Message = CommandArg()):
    action_id = _extract_arg(args)
    if not action_id:
        await diana_feed.finish("要投喂什么呢？比如：/投喂 鸡胸肉、/投喂 小草莓、/投喂 薯片")
    session = await get_session(event.get_user_id())
    try:
        result = await session.interact(action_id)
    except DianaError as exc:
        await send_result(_error_result(exc), matcher, "text")
        return
    await send_result(result, matcher, "interaction")


@diana_play.handle()
async def _(event: Event, matcher: Matcher, args: Message = CommandArg()):
    action_id = _extract_arg(args)
    if not action_id:
        await diana_play.finish("玩什么呢？比如：/玩 连连看、/玩 宅舞一支、/玩 你画我猜")
    session = await get_session(event.get_user_id())
    try:
        result = await session.interact(action_id)
    except DianaError as exc:
        await send_result(_error_result(exc), matcher, "text")
        return
    await send_result(result, matcher, "interaction")


@diana_work.handle()
async def _(event: Event, matcher: Matcher, args: Message = CommandArg()):
    action_id = _extract_arg(args)
    if not action_id:
        await diana_work.finish("做什么工作呢？比如：/打工 日常直播、/打工 生日会直播、/打工 团播")
    session = await get_session(event.get_user_id())
    try:
        result = await session.interact(action_id)
    except DianaError as exc:
        await send_result(_error_result(exc), matcher, "text")
        return
    await send_result(result, matcher, "interaction")


@diana_interact.handle()
async def _(event: Event, matcher: Matcher, args: Message = CommandArg()):
    action_id = _extract_arg(args)
    if not action_id:
        await diana_interact.finish("要和然然做什么互动呢？比如：/互动 摸摸头、/互动 Mua、/互动 喊一米八")
    session = await get_session(event.get_user_id())
    try:
        result = await session.interact(action_id)
    except DianaError as exc:
        await send_result(_error_result(exc), matcher, "text")
        return
    await send_result(result, matcher, "interaction")


@diana_daily.handle()
async def _(event: Event, matcher: Matcher, args: Message = CommandArg()):
    action_id = _extract_arg(args)
    if not action_id:
        await diana_daily.finish("和然然一起做什么呢？比如：/日常 休息、/日常 逛街、/日常 刷B站")
    session = await get_session(event.get_user_id())
    try:
        result = await session.interact(action_id)
    except DianaError as exc:
        await send_result(_error_result(exc), matcher, "text")
        return
    await send_result(result, matcher, "interaction")


# ── 换装 handler（不走互动管道）──

@diana_costume.handle()
async def _(event: Event, matcher: Matcher, args: Message = CommandArg()):
    name = _extract_arg(args)
    session = await get_session(event.get_user_id())
    if not name:
        result = await session.random_outfit()
    elif matched := session.match_costume(name):
        result = await session.change_outfit(matched["id"])
    else:
        result = {"success": False, "text": f"没有找到'{name}'这件服装呢……"}
    await send_result(result, matcher, "text")


@diana_unlock.handle()
async def _(event: Event, matcher: Matcher, args: Message = CommandArg()):
    name = _extract_arg(args)
    session = await get_session(event.get_user_id())
    if not name:
        locked = [c for c in session.list_costumes() if not c["owned"]]
        if not locked:
            await diana_unlock.finish("你已经解锁了全部服装！")
        lines = ["可解锁的服装："]
        for costume in locked:
            unlock = costume["unlock"]
            if unlock.get("type") == "level":
                condition = f"需要 Lv.{unlock.get('value')}"
            elif unlock.get("type") == "coins":
                condition = f"需要 {unlock.get('value')} 嘉心糖币"
            elif unlock.get("type") == "achievement":
                condition = "成就解锁"
            else:
                condition = "特殊条件"
            lines.append(f"{costume['emoji']} {costume['name']} - {condition}")
        await diana_unlock.finish("\n".join(lines))
    elif matched := session.match_costume(name):
        result = await session.buy_costume(matched["id"])
    else:
        result = {"success": False, "text": f"没有找到'{name}'这件服装呢……"}
    await send_result(result, matcher, "text")


# ── 其他 handler ──

@diana_status.handle()
async def _(event: Event, matcher: Matcher):
    session = await get_session(event.get_user_id())
    result = await session.status()
    await send_result(result, matcher, "status")


@diana_wardrobe.handle()
async def _(event: Event, matcher: Matcher):
    session = await get_session(event.get_user_id())
    result = await session.costume_list_card()
    if not result.get("image_url") and not result.get("image"):
        result["text"] = "（衣柜卡片渲染失败，请稍后再试）"
    await send_result(result, matcher, "costume")


@diana_talk.handle()
async def _(event: Event, matcher: Matcher, args: Message = CommandArg()):
    session = await get_session(event.get_user_id())
    result = await session.talk(_extract_arg(args))
    await send_result(result, matcher, "talk")


# ── 然然帮助（分页）──

# (category, 中文名, 指令前缀, 简介)
_HELP_CATEGORIES = [
    ("food", "投喂", "/投喂", "给然然喂好吃的，恢复饱腹度"),
    ("play", "玩耍", "/玩", "陪然然玩耍，消耗体力换心情"),
    ("work", "打工", "/打工", "然然去直播赚钱"),
    ("social", "互动", "/互动", "和然然亲密互动"),
    ("daily", "日常", "/日常", "然然的日常活动"),
]

_CATEGORY_ALIASES = {
    "投喂": "food", "食物": "food", "喂食": "food", "food": "food",
    "玩耍": "play", "玩": "play", "play": "play",
    "打工": "work", "工作": "work", "直播": "work", "work": "work",
    "互动": "social", "社交": "social", "social": "social",
    "日常": "daily", "daily": "daily",
}


def _build_diana_help_overview() -> str:
    """帮助总览：各分类入口 + 其他指令。"""
    lines = ["# 🍓 然然养成帮助", "点击分类查看全部可选项～", ""]
    for _cat, name, _prefix, desc in _HELP_CATEGORIES:
        lines.append(f"## {name}")
        lines.append(desc)
        lines.append(_text_chain(f"/然然帮助 {name}", f"查看{name}列表"))
        lines.append("")
    lines.append("## 其他指令")
    others = [
        ("/签到", "签到"),
        ("/然然状态", "状态"),
        ("/换装", "换装"),
        ("/然然衣柜", "衣柜"),
        ("/解锁", "解锁服装"),
        ("/然然", "聊天"),
    ]
    lines.append(" · ".join(_text_chain(t, s) for t, s in others))
    return "\n".join(lines)


def _build_diana_help_category(category: str) -> str:
    """某分类的详细 item 列表，每项用文字链点击即插入对应指令。"""
    meta = next((c for c in _HELP_CATEGORIES if c[0] == category), None)
    if meta is None:
        return "未找到该分类"
    _cat, name, prefix, desc = meta
    items = list_items(category)
    if not items:
        return f"暂无{name}项目"
    lines = [f"# {name}列表", desc, ""]
    for i in range(0, len(items), 3):
        chunk = items[i:i + 3]
        links = [
            _text_chain(f"{prefix} {it['id']}", f"{it['emoji']} {it['id']}")
            for it in chunk
        ]
        lines.append(" · ".join(links))
    lines.append("")
    lines.append(f"返回{_text_chain('/然然帮助', '帮助总览')}")
    return "\n".join(lines)


@diana_help.handle()
async def _(args: Message = CommandArg()):
    arg = _extract_arg(args)
    category = _CATEGORY_ALIASES.get(arg)
    if category:
        text = _build_diana_help_category(category)
    elif arg:
        text = f"未找到分类「{arg}」。\n返回{_text_chain('/然然帮助', '帮助总览')}"
    else:
        text = _build_diana_help_overview()
    await diana_help.finish(MessageSegment.markdown(text))
