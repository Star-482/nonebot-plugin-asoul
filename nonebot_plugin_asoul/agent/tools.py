"""
@Author: star_482
@Date: 2026/7/31
@File: tools
@Description: Agent 工具注册与实现。每个工具复用插件现有功能模块，返回
ToolResult(text=给 LLM 的文本摘要, attachments=随回复发送的富媒体)。
"""
import random
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from nonebot.log import logger

from ..utils import open_json
from ..fortune_manager import fortune_manager, build_fortune_md
from ..whateat import build_whateat_msg
from ..random_wife import get_random_wife_md_message
from ..activity import get_relative_content
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


async def _what_eat_drink(menu_type: str, action_verb: str) -> ToolResult:
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
    "查询本周日程（今天和明天的安排）。",
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
