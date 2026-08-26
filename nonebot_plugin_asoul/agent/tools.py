"""
@Author: star_482
@Date: 2026/7/31
@File: tools
@Description: Agent 工具注册与实现。每个工具复用插件现有功能模块，返回
ToolResult(text=给 LLM 的文本摘要, attachments=随回复发送的富媒体)。
"""
import random
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Literal

from nonebot.adapters.qq import Bot
from nonebot.log import logger

from ..config import config
from ..utils import open_json
from .media import sticker_library, image_library
from ..features import (
    fortune_manager,
    build_fortune_md,
    build_whateat_msg,
    get_random_wife_md_message,
    get_relative_content,
)
from ..features.group_admin import (
    welcome_repo,
    recall_repo,
    _parse_duration,
    _to_rfc3339,
)
from ..live_subscription import bili_api
from ..live_subscription.manager import manager as sub_manager
from ..manage.relationships import relations
from ..manage.qq_api import set_group_member_mute
from ..markdown import get_about_xiaoran_markdown
# 复用 diana/commands.py 的 session 缓存与结果 builder（同包，私有函数可 import）
from ..diana.commands import (
    get_session,
    list_items,
    _build_interaction_msg,
    _build_status_msg,
    _changes_text,
)
from ..diana.exceptions import DianaError


# ── 上下文与结果 ──

@dataclass
class ToolContext:
    user_id: str
    group_id: str | None  # 私聊为 None
    bot: Bot | None = None  # 群管等需调平台 API 的工具用
    member_role: str = ""  # 发送者群身份：owner/admin/""（私聊或普通成员）
    mention_user_ids: list[str] = field(default_factory=list)  # 原消息 @ 的非 bot 成员 openid
    scene_type: Literal["dm", "group"] = "dm"
    user_name: str = ""
    message_id: str = ""
    trigger_type: Literal["dm", "at", "reply"] = "dm"


@dataclass
class ToolResult:
    text: str  # 喂给 LLM 的文本摘要
    attachments: list = field(default_factory=list)  # list[MessageSegment]


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict  # JSON Schema
    handler: Callable[[dict, ToolContext], Awaitable[ToolResult]]


_REGISTRY: dict[str, Tool] = {}


def register_tool(name: str, description: str, parameters: dict):
    """注册一个工具。handler 签名: async def fn(args: dict, ctx: ToolContext) -> ToolResult."""
    def deco(fn: Callable[[dict, ToolContext], Awaitable[ToolResult]]):
        _REGISTRY[name] = Tool(name=name, description=description, parameters=parameters, handler=fn)
        return fn
    return deco


async def dispatch(name: str, args: dict, ctx: ToolContext) -> ToolResult:
    """按名调度工具，捕获异常避免打断 LLM 循环。"""
    tool = _REGISTRY.get(name)
    if tool is None:
        return ToolResult(text=f"未知工具：{name}")
    try:
        return await tool.handler(args, ctx)
    except Exception as e:
        logger.exception(f"agent tool {name} 执行异常")
        return ToolResult(text=f"工具 {name} 执行出错：{e}")


def get_tool_schemas() -> list[dict]:
    """生成 OpenAI tools 字段。"""
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
            },
        }
        for t in _REGISTRY.values()
    ]


# ── 工具实现 ──

@register_tool(
    "get_fortune",
    "帮用户抽今日运势（每天每用户一次）。返回运势图片和运势标题。",
    {"type": "object", "properties": {}, "required": []},
)
async def _get_fortune(args: dict, ctx: ToolContext) -> ToolResult:
    gid = ctx.group_id or "dm"
    uid = ctx.user_id
    if fortune_manager.check_data(gid, uid):
        result = await fortune_manager.do_draw(gid, uid)
        fortune_manager.save_data()
        already = False
    else:
        info = fortune_manager.get_cached_info(gid, uid)
        if info and info.get("url"):
            result = {"url": info["url"], "title": "", "w": info["w"], "h": info["h"]}
            already = True
        else:
            return ToolResult(text="用户今天已经抽过签了，但没有可展示的缓存结果。")
    att = [build_fortune_md(result, uid)]
    note = "用户今天已经抽过签了，再次展示了之前的运势图。" if already else f"已为用户抽到今日运势：{result.get('title', '今日运势')}。"
    return ToolResult(text=note, attachments=att)


async def _what_eat_drink(menu_type: Literal['drink', 'eat'], action_verb: str) -> ToolResult:
    """吃什么/喝什么共用逻辑。复用原版 md 模板（含键盘），与命令路径一致。"""
    msg = await build_whateat_msg(menu_type, action_verb)
    return ToolResult(text=f"已为用户挑了今天{action_verb}的，图片已发送。", attachments=[msg])


@register_tool(
    "what_eat",
    "帮用户决定今天吃什么，返回一张美食图片。",
    {"type": "object", "properties": {}, "required": []},
)
async def _what_eat(args: dict, ctx: ToolContext) -> ToolResult:
    return await _what_eat_drink("eat", "吃")


@register_tool(
    "what_drink",
    "帮用户决定今天喝什么，返回一张饮品图片。",
    {"type": "object", "properties": {}, "required": []},
)
async def _what_drink(args: dict, ctx: ToolContext) -> ToolResult:
    return await _what_eat_drink("drink", "喝")


@register_tool(
    "draw_wife",
    "帮用户抽一个'老婆'（随机动漫角色图片），返回图片和角色名。",
    {"type": "object", "properties": {}, "required": []},
)
async def _draw_wife(args: dict, ctx: ToolContext) -> ToolResult:
    msg = await get_random_wife_md_message()
    return ToolResult(text="已为用户抽了老婆，图片已发送。", attachments=[msg])


@register_tool(
    "get_quotation",
    "获取一篇'发病小作文'（嘉然风格的粉丝创作短文）。",
    {"type": "object", "properties": {}, "required": []},
)
async def _get_quotation(args: dict, ctx: ToolContext) -> ToolResult:
    data: dict = open_json("quotation.json")
    if not data:
        return ToolResult(text="暂时没有小作文内容。")
    entry = random.choice(list(data.values()))
    title = entry.get("title", "")
    content = entry.get("content", "")
    return ToolResult(text=f"发病小作文《{title}》：\n{content}")


@register_tool(
    "get_activity",
    "查询本周直播日程（今天和明天的安排）。",
    {"type": "object", "properties": {}, "required": []},
)
async def _get_activity(args: dict, ctx: ToolContext) -> ToolResult:
    content = get_relative_content()
    parts = []
    if content.get("today"):
        parts.append("今天：" + "、".join(content["today"]))
    if content.get("tomorrow"):
        parts.append("明天：" + "、".join(content["tomorrow"]))
    text = "\n".join(parts) if parts else "最近没有日程安排。"
    return ToolResult(text=text)


@register_tool(
    "diana_status",
    "查看嘉然（虚拟宠物）的当前状态：饱腹/心情/体力/亲密度/等级/金币等。",
    {"type": "object", "properties": {}, "required": []},
)
async def _diana_status(args: dict, ctx: ToolContext) -> ToolResult:
    session = await get_session(ctx.user_id)
    result = await session.status()
    att = [_build_status_msg(result)]
    return ToolResult(text=result.get("text", ""), attachments=att)


@register_tool(
    "diana_interact",
    "和嘉然（虚拟宠物）互动：投喂/玩耍/打工/互动/日常。需要 action_id（具体动作名，"
    "如 鸡胸肉、连连看、日常直播、摸摸头、休息）。可用 list_diana_items 查某分类的可选项。",
    {
        "type": "object",
        "properties": {
            "action_id": {"type": "string", "description": "动作名，例如 鸡胸肉/连连看/日常直播/摸摸头/休息"}
        },
        "required": ["action_id"],
    },
)
async def _diana_interact(args: dict, ctx: ToolContext) -> ToolResult:
    action_id = (args.get("action_id") or "").strip()
    if not action_id:
        return ToolResult(text="需要提供 action_id。")
    session = await get_session(ctx.user_id)
    try:
        result = await session.interact(action_id)
    except DianaError as e:
        return ToolResult(text=f"互动失败：{e}")
    att = [_build_interaction_msg(result)]
    dialogue = result.get("dialogue", "")
    changes = _changes_text(result.get("changes", {})) if result.get("changes") else ""
    summary = f"执行了「{action_id}」。"
    if dialogue:
        summary += f" 然然：{dialogue}"
    if changes:
        summary += f"（{changes}）"
    return ToolResult(text=summary, attachments=att)


@register_tool(
    "list_diana_items",
    "列出嘉然（虚拟宠物）某分类下可用的动作。category 可选：food(投喂)/play(玩耍)/work(打工)/social(互动)/daily(日常)。"
    "不传则列出全部。",
    {
        "type": "object",
        "properties": {
            "category": {"type": "string", "description": "food/play/work/social/daily，可选"}
        },
        "required": [],
    },
)
async def _list_diana_items(args: dict, ctx: ToolContext) -> ToolResult:
    category = args.get("category") or None
    items = list_items(category)
    if not items:
        return ToolResult(text="该分类暂无可用动作。")
    lines = [f"{it.get('emoji', '')}{it.get('id', '')}".strip() for it in items]
    return ToolResult(text="可用动作：" + "、".join(lines))


@register_tool(
    "get_about",
    "获取小然机器人的功能介绍 / 帮助菜单（含各指令用法）。当用户问'帮助''功能介绍''介绍''菜单'"
    "'有什么功能''怎么用''你能做什么'等时调用。",
    {"type": "object", "properties": {}, "required": []},
)
async def _get_about(args: dict, ctx: ToolContext) -> ToolResult:
    msg = get_about_xiaoran_markdown()
    return ToolResult(text="已为用户展示小然的功能介绍/帮助菜单。", attachments=[msg])


# ── 直播订阅工具 ──

@register_tool(
    "query_live_status",
    "查询 B站主播的直播状态（谁在直播、直播间标题和链接）。names 可选，传主播名列表"
    "（如 嘉然、贝拉）；不传则查询预定义的全部主播，返回当前正在直播的。"
    "用户问'谁在直播''XX直播了吗''开播了吗'时调用。",
    {
        "type": "object",
        "properties": {
            "names": {
                "type": "array",
                "items": {"type": "string"},
                "description": "主播名列表，可选；不传查全部",
            }
        },
        "required": [],
    },
)
async def _query_live_status(args: dict, ctx: ToolContext) -> ToolResult:
    names = args.get("names") or []
    upstreams = sub_manager.get_upstreams()
    if names:
        by_name = {u["name"]: u for u in upstreams}
        targets = []
        for n in names:
            u = by_name.get(str(n).strip()) or sub_manager.search_upstream(str(n))
            if u:
                targets.append(u)
        if not targets:
            return ToolResult(text=f"没有找到这些主播：{'、'.join(map(str, names))}")
    else:
        targets = upstreams

    infos = await bili_api.fetch_live_status([u["uid"] for u in targets])
    lines = []
    for u in targets:
        info = infos.get(u["uid"])
        # 只统计真开播（live_status=1）；轮播（=2）不算直播
        if info and info.is_live:
            lines.append(f"{info.uname} 正在直播：《{info.title}》 {info.url}")
    if not lines:
        checked = "、".join(u["name"] for u in targets)
        return ToolResult(text=f"当前没有人在直播（已查：{checked}）。")
    return ToolResult(text="\n".join(lines))


@register_tool(
    "get_live_subscriptions",
    "查询本群订阅了哪些主播的开播通知。仅群聊可用。",
    {"type": "object", "properties": {}, "required": []},
)
async def _get_live_subscriptions(args: dict, ctx: ToolContext) -> ToolResult:
    if not ctx.group_id:
        return ToolResult(text="开播订阅只在群聊里才有意义，这里是私聊哦。")
    subs = await sub_manager.list_for_group(ctx.group_id)
    if not subs:
        return ToolResult(text="本群还没有订阅任何主播的开播通知，群主可以用 /订阅开播 来订阅。")
    lines = [f"{s['name']}（UID:{s['uid']}）" for s in subs]
    return ToolResult(text="本群的开播订阅有：" + "、".join(lines))


@register_tool(
    "subscribe_live",
    "为本群订阅某位主播的开播通知（直播开播时会在群里提醒）。仅群主可用，群聊专用。"
    "name 传主播名（如 嘉然）。",
    {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "主播名，例如 嘉然"}
        },
        "required": ["name"],
    },
)
async def _subscribe_live(args: dict, ctx: ToolContext) -> ToolResult:
    if not ctx.group_id:
        return ToolResult(text="开播订阅只在群聊里可用。")
    if ctx.member_role != "owner":
        return ToolResult(text="只有群主可以订阅开播通知哦。")
    name = str(args.get("name", "")).strip()
    upstream = sub_manager.search_upstream(name) if name else None
    if upstream is None:
        return ToolResult(text=f"没有找到主播「{name}」，可选：{'、'.join(sub_manager.get_upstream_names())}")
    gid = ctx.group_id
    if await sub_manager.is_subscribed(gid, upstream["uid"]):
        return ToolResult(text=f"本群已经订阅过 {upstream['name']} 啦。")
    await sub_manager.subscribe(gid, upstream["uid"])
    if relations.is_group_push_ok(gid) is True:
        return ToolResult(text=f"已订阅 {upstream['name']} 的开播通知，开播时会自动提醒~")
    return ToolResult(
        text=(
            f"已订阅 {upstream['name']}，但本群还没开启主动推送，暂时收不到开播通知。"
            "请告诉群主：点 bot 头像 -> 右上角设置 -> 允许机器人主动发言，开启后订阅才会生效"
            "（也可以直接用 /订阅开播 指令完成同样的操作）。"
        )
    )


@register_tool(
    "unsubscribe_live",
    "取消本群对某位主播的开播订阅。仅群主可用，群聊专用。name 传主播名。",
    {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "主播名，例如 嘉然"}
        },
        "required": ["name"],
    },
)
async def _unsubscribe_live(args: dict, ctx: ToolContext) -> ToolResult:
    if not ctx.group_id:
        return ToolResult(text="开播订阅只在群聊里可用。")
    if ctx.member_role != "owner":
        return ToolResult(text="只有群主可以取消开播订阅哦。")
    name = str(args.get("name", "")).strip()
    upstream = sub_manager.search_upstream(name) if name else None
    if upstream is None:
        return ToolResult(text=f"没有找到主播「{name}」。")
    gid = ctx.group_id
    if not await sub_manager.is_subscribed(gid, upstream["uid"]):
        return ToolResult(text=f"本群没有订阅过 {upstream['name']} 哦。")
    await sub_manager.unsubscribe(gid, upstream["uid"])
    return ToolResult(text=f"已取消 {upstream['name']} 的开播订阅。")


# ── 群管工具 ──
# 前置校验模板（bot/gid 绑定局部变量以便类型收窄）：
#   bot, gid = ctx.bot, ctx.group_id
#   if not bot or not gid: return "这个操作只能在群聊里用哦。"
#   if ctx.member_role not in ("owner", "admin"): return "只有群主或管理员才能操作这个。"


@register_tool(
    "mute_member",
    "禁言群成员（需要用户在消息里 @ 了要禁言的人，且用户是群主/管理员，bot 是群管理员）。"
    "duration 可选，格式如 30m/2h/1d，不传默认 15 分钟，最长 30 天。",
    {
        "type": "object",
        "properties": {
            "duration": {"type": "string", "description": "时长，如 30m/2h/1d；不传默认 15 分钟"}
        },
        "required": [],
    },
)
async def _mute_member(args: dict, ctx: ToolContext) -> ToolResult:
    bot, gid = ctx.bot, ctx.group_id
    if not bot or not gid:
        return ToolResult(text="这个操作只能在群聊里用哦。")
    if ctx.member_role not in ("owner", "admin"):
        return ToolResult(text="只有群主或管理员才能操作这个。")
    if not ctx.mention_user_ids:
        return ToolResult(text="用户没有 @ 要禁言的人，让用户 @ 目标成员后再试。")
    delta = _parse_duration(str(args.get("duration", "")))
    if delta is None:
        return ToolResult(text="时长格式无效（可用 30m/2h/1d，最长 30 天）。")
    target = ctx.mention_user_ids[0]
    expire = _to_rfc3339(delta)
    ok, msg = await set_group_member_mute(
        bot, gid,
        [{"op": "add", "member_openid": target, "mute_expire_at": expire}],
    )
    if not ok:
        return ToolResult(text=f"禁言失败：{msg}")
    return ToolResult(text=f"禁言成功，时长 {delta}（到期 {expire}）。简短确认即可，不用复述细节。")


@register_tool(
    "unmute_member",
    "解除群成员的禁言（需要用户在消息里 @ 了要解禁的人，且用户是群主/管理员，bot 是群管理员）。",
    {"type": "object", "properties": {}, "required": []},
)
async def _unmute_member(args: dict, ctx: ToolContext) -> ToolResult:
    bot, gid = ctx.bot, ctx.group_id
    if not bot or not gid:
        return ToolResult(text="这个操作只能在群聊里用哦。")
    if ctx.member_role not in ("owner", "admin"):
        return ToolResult(text="只有群主或管理员才能操作这个。")
    if not ctx.mention_user_ids:
        return ToolResult(text="用户没有 @ 要解禁的人，让用户 @ 目标成员后再试。")
    target = ctx.mention_user_ids[0]
    ok, msg = await set_group_member_mute(
        bot, gid,
        [{"op": "del", "member_openid": target, "mute_expire_at": ""}],
    )
    if not ok:
        return ToolResult(text=f"解禁失败：{msg}")
    return ToolResult(text="解禁成功。")


@register_tool(
    "query_group_config",
    "查询本群的入群欢迎配置（是否开启、当前欢迎语）和撤回关键词列表。仅群聊可用。",
    {"type": "object", "properties": {}, "required": []},
)
async def _query_group_config(args: dict, ctx: ToolContext) -> ToolResult:
    if not ctx.group_id:
        return ToolResult(text="这里是私聊，没有群配置可查。")
    gid = ctx.group_id
    lines = []
    cfg = welcome_repo.get(gid)
    enabled = bool(cfg and cfg.get("enabled", True))
    text = (cfg.get("text") if cfg else None) or "（默认欢迎语）"
    lines.append(f"入群欢迎：{'已开启' if enabled else '已关闭'}；欢迎语：{text}")
    keywords = recall_repo.get_keywords(gid)
    lines.append("撤回关键词：" + ("、".join(keywords) if keywords else "（未设置）"))
    return ToolResult(text="\n".join(lines))


# ── 记忆检索（memories.md 不再注入 system prompt，按需检索原文）──

# 条目缓存：(mtime, [(display, section, subsection), ...])，文件热更新后自动失效。
# display 为带上下文前缀的原文行：编年条目 "2021年 3月7日…"、词典条目 "【梗】关于"X"：…（多行合并）"、
# 成员/小节条目 "【向晚】…"。前缀即检索上下文（搜人名/年份可命中所属条目）；
# section/subsection 保留小节归属，供 section 浏览模式（问"队长是谁"这类身份事实优先整节浏览）。
_memory_cache: tuple[float, list[tuple[str, str, str]]] | None = None


def _norm(s: str) -> str:
    """归一化：小写 + 去空格（中英文混排、全半角空格）。"""
    return s.lower().replace(" ", "").replace("　", "")


# 年份小节标题：### 2020年 / ### 2023（"年"字可省，后者曾导致年份错标）
_YEAR_TITLE = re.compile(r"^(\d{4})年?$")
# 词典条目起始行：关于"XXX"：
_DICT_TITLE = re.compile(r"^关于\s*[“\"「『]?(.+?)[”\"」』]?\s*[：:]$")
# 编年条目前缀：2021年 …
_YEAR_ENTRY = re.compile(r"^(\d{4})年 ")


def _parse_memory_file(text: str) -> list[tuple[str, str, str]]:
    """解析 memories.md 为 (带上下文条目, 小节, 子小节) 列表。

    结构约定（## 小节 / ### 子小节）：
    - 个人经历：### YYYY[年] 子小节，每行一条"日期 事件"，前缀补全年份；
    - 枝江方言词典：`关于"X"：` 行起始，后续行合并为一条完整梗解释；
    - 其余小节（逸闻/嘉然玩的游戏/枝江人物介绍等）：每行一条，
      有子小节（如 ### 向晚）用【子小节】前缀，否则用【小节】前缀。
    """
    entries: list[tuple[str, str, str]] = []
    section = subsection = ""
    year = ""
    pending: list[str] | None = None  # 词典条目累积缓冲

    def _flush() -> None:
        nonlocal pending
        if pending:
            entries.append(("【梗】" + "\n".join(pending), section, subsection))
            pending = None

    for raw in text.splitlines():
        s = raw.strip()
        if not s:
            continue
        if s.startswith("#"):
            _flush()
            level = len(s) - len(s.lstrip("#"))
            title = s.lstrip("#").strip()
            m = _YEAR_TITLE.match(title)
            year = m.group(1) if m else ""  # 进入非年份标题即清空，防止年份泄漏到后续小节
            if level <= 2:
                section, subsection = title, ""
            else:
                subsection = title
            continue
        if _DICT_TITLE.match(s):
            _flush()
            pending = [s]
            continue
        if pending is not None:
            pending.append(s)
            continue
        if year:
            entries.append((f"{year}年 {s}", section, subsection))
        elif subsection:
            entries.append((f"【{subsection}】{s}", section, subsection))
        else:
            entries.append((f"【{section}】{s}", section, ""))
    _flush()
    return entries


def _load_memory_entries() -> list[tuple[str, str, str]]:
    """加载并缓存记忆条目。mtime 变化时重新解析。"""
    global _memory_cache
    p = Path(config.data_path) / config.agent_memories_path
    try:
        mtime = p.stat().st_mtime
    except OSError:
        return []
    if _memory_cache and _memory_cache[0] == mtime:
        return _memory_cache[1]
    try:
        entries = _parse_memory_file(p.read_text(encoding="utf-8"))
    except OSError:
        return []
    _memory_cache = (mtime, entries)
    return entries


def _section_names(entries: list[tuple[str, str, str]]) -> list[str]:
    """去重的小节/子小节名，供 LLM 浏览选择与未命中提示。"""
    names: list[str] = []
    for _d, sec, sub in entries:
        for name in (sec, f"{sec}·{sub}" if sub and sub != sec else ""):
            if name and name not in names:
                names.append(name)
    return names


def _pick_capped(scored: list[tuple[int, str]], *, max_entries: int, max_chars: int) -> list[str]:
    """按序取条目，条数与字符双重封顶。"""
    picked: list[str] = []
    total = 0
    for _s, e in scored:
        if len(picked) >= max_entries or total + len(e) > max_chars:
            break
        picked.append(e)
        total += len(e)
    return picked


@register_tool(
    "lookup_memories",
    "检索嘉然本人的记忆库，返回原文条目。覆盖：经历编年（2020年至今）、枝江方言词典（梗的出处和含义）、"
    "逸闻、嘉然玩过的游戏、枝江人物介绍（向晚/贝拉/珈乐/乃琳/心宜/思诺等成员的身份、定位、生日、粉丝名）。"
    "两种用法：① section 浏览--问成员身份/定位/关系这类事实（如'队长是谁''乃琳的粉丝名叫什么'）"
    "用 section='枝江人物介绍'，也可直接给成员名（如 section='贝拉'）或小节名（枝江方言词典/逸闻/嘉然玩的游戏）；"
    "② keywords 关键词--查具体事件/梗时传 3-5 个关键词（人名、事件名、梗、近义说法），year 可选限定年份。"
    "没查到就换关键词或 section 再查；仍查不到就说不确定，不要凭印象编。",
    {
        "type": "object",
        "properties": {
            "keywords": {
                "type": "array",
                "items": {"type": "string"},
                "description": "关键词列表，3-5 个，含近义说法；与 section 二选一",
            },
            "section": {
                "type": "string",
                "description": "小节名：枝江人物介绍/枝江方言词典/逸闻/嘉然玩的游戏/成员名/年份；与 keywords 二选一",
            },
            "year": {"type": "string", "description": "限定年份，如 2021，可选"},
        },
        "required": [],
    },
)
async def _lookup_memories(args: dict, ctx: ToolContext) -> ToolResult:
    entries = _load_memory_entries()
    if not entries:
        return ToolResult(text="记忆库暂时不可用。")

    # 用法①：小节浏览。问"队长是谁"这类身份事实优先用这个--整节原文可读，不依赖关键词猜中。
    sec = str(args.get("section") or "").strip()
    if sec:
        nsec = _norm(sec)
        # 双向包含：用户给"人物介绍"或"枝江人物介绍"、"贝拉"或"枝江人物介绍·贝拉"都能命中
        matched = [
            (0, d) for d, s, sub in entries
            if nsec in _norm(s) or _norm(s) in nsec
            or (sub and (nsec in _norm(sub) or _norm(sub) in nsec))
        ]
        if matched:
            picked = _pick_capped(matched, max_entries=60, max_chars=6000)
            return ToolResult(text=f"小节【{sec}】内容（按原文顺序，可能截断）：\n" + "\n".join(picked))
        return ToolResult(text="没有这个小节。可用小节：" + "、".join(_section_names(entries)))

    # 用法②：关键词打分检索
    kws = [str(k).strip() for k in (args.get("keywords") or []) if str(k).strip()]
    if not kws:
        return ToolResult(
            text="请提供 keywords 或 section。可用小节：" + "、".join(_section_names(entries))
        )
    year = str(args.get("year") or "").strip()
    if year:
        entries = [e for e in entries if e[0].startswith(f"{year}年 ")]
    nkws = [_norm(k) for k in kws]
    scored: list[tuple[int, str]] = []
    for d, _s, _sub in entries:
        nd = _norm(d)
        score = sum(1 for k in nkws if k in nd)
        if score:
            scored.append((score, d))
    if not scored:
        years = sorted({m.group(1) for e in entries if (m := _YEAR_ENTRY.match(e[0]))})
        return ToolResult(
            text="没查到相关记忆，试试别的关键词（人名、事件名、梗的原文写法），"
            "或改用 section 浏览小节（可用：" + "、".join(_section_names(entries)) + "）。"
            "记忆库覆盖年份：" + "、".join(years)
        )
    # 相关度优先，同分保持原文顺序；只返回原文，零改写
    scored.sort(key=lambda x: -x[0])
    picked = _pick_capped(scored, max_entries=40, max_chars=3500)
    return ToolResult(text="相关记忆（按相关度排序，可能不全）：\n" + "\n".join(picked))


# ── 表情包 / 图片库工具 ──

def _tags_hint(lib) -> str:
    """工具描述里附上当前可用标签（import 时生成；标签变动重启后更新，未命中时工具会现场提示）。"""
    tags = lib.tags()
    return "、".join(tags[:40]) if tags else "（库暂时为空）"


@register_tool(
    "send_sticker",
    "发送一个表情包来表达情绪，会作为单独的图片消息紧跟在你的文字回复后到达。"
    f"tag 传情绪/场景关键词，可用标签：{_tags_hint(sticker_library)}。"
    "传错了会提示全部可用标签。",
    {
        "type": "object",
        "properties": {
            "tag": {"type": "string", "description": "情绪/场景关键词，如 开心、无语、哭、吃"}
        },
        "required": ["tag"],
    },
)
async def _send_sticker(args: dict, ctx: ToolContext) -> ToolResult:
    tag = str(args.get("tag", "")).strip()
    item = sticker_library.pick(tag)
    if item is None:
        tags = sticker_library.tags()
        if not tags:
            return ToolResult(text="表情包库还是空的，发不了。")
        return ToolResult(text=f"没有匹配「{tag}」的表情包。可用标签：" + "、".join(tags))
    seg = await sticker_library.build_segment(item)
    if seg is None:
        return ToolResult(text="表情包发送失败了（图片读取/上传出错），换个说法继续聊就行。")
    return ToolResult(text=f"已发送表情包「{item.path.stem}」。", attachments=[seg])


@register_tool(
    "send_image",
    "从图片库发送一张主题图片（会作为单独的图片消息到达）。query 传主题/分类关键词，"
    f"可用关键词：{_tags_hint(image_library)}。传错了会提示全部可用关键词。",
    {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "主题/分类关键词，如 壁纸、美图、梗图"}
        },
        "required": ["query"],
    },
)
async def _send_image(args: dict, ctx: ToolContext) -> ToolResult:
    query = str(args.get("query", "")).strip()
    item = image_library.pick(query)
    if item is None:
        tags = image_library.tags()
        if not tags:
            return ToolResult(text="图片库还是空的，发不了。")
        return ToolResult(text=f"没有匹配「{query}」的图片。可用关键词：" + "、".join(tags))
    seg = await image_library.build_segment(item)
    if seg is None:
        return ToolResult(text="图片发送失败了（图片读取/上传出错），换个说法继续聊就行。")
    return ToolResult(text=f"已发送图片「{item.path.stem}」。", attachments=[seg])
