"""
@Author: star_482
@Date: 2026/6/4
@File: admin
@Description: 开播订阅管理命令：订阅开播 / 取消订阅 / 订阅列表 / 订阅全览（SUPERUSER）。
"""
import asyncio

from nonebot.adapters import Event
from nonebot.adapters.qq import Message, MessageSegment
from nonebot.adapters.qq.event import GroupMessageCreateEvent
from nonebot.adapters.qq.models import (
    Action,
    Button,
    InlineKeyboard,
    InlineKeyboardRow,
    MessageKeyboard,
    Permission,
    RenderData,
)
from nonebot.params import CommandArg
from nonebot.plugin.on import on_command
from nonebot.permission import SUPERUSER

from ..config import config
from .manager import manager
from ..manage.relationships import relations

subscribe_matcher = on_command(
    "订阅开播", aliases={"开播通知"}, priority=config.command_priority
)
unsubscribe_matcher = on_command(
    "取消订阅", aliases={"退订直播"}, priority=config.command_priority
)
list_matcher = on_command("订阅列表", priority=config.command_priority)
overview_matcher = on_command(
    "订阅全览", priority=config.command_priority, permission=SUPERUSER
)

_INSTRUCTIONS = (
    "请确保您是群主/管理员或经过允许，点击下方按钮发送即可。\n"
    "❗同时需要群主点击 bot 头像，打开右上角的设置，"
    "允许机器人在群聊内主动发言。❗没有该选项请尝试更新 QQ。\n"
    "其他UP主请查看下方说明⬇️。\n"
    "[查看操作说明](https://docs.qq.com/doc/DRkFEbEhoa1Jzc05r)"
)

# "全部"对应的五位成员
_ALL_NAMES = ["嘉然", "贝拉", "乃琳", "心宜", "思诺"]
_ALL_NAMES_STR = " ".join(_ALL_NAMES)

# 预定义按钮布局：第一排 嘉然/贝拉/乃琳，第二排 心宜/思诺
_BUTTON_MEMBERS = [
    ["嘉然", "贝拉", "乃琳"],
    ["心宜", "思诺"],
]


def _mk_cmd_button(button_id: str, label: str, command: str) -> Button:
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


def _build_md_keyboard(prefix: str) -> tuple[str, MessageKeyboard]:
    """构建无参时的 Markdown 按钮面板。prefix 为 "/订阅开播" 或 "/取消订阅"。"""
    is_unsub = "取消" in prefix
    md = f"## {'取消' if is_unsub else ''}开播通知\n\n{_INSTRUCTIONS}"
    rows = []
    for members in _BUTTON_MEMBERS:
        buttons = []
        for name in members:
            cmd = f"{prefix} {name}"
            buttons.append(_mk_cmd_button(f"live_sub_{name}", name, cmd))
        rows.append(InlineKeyboardRow(buttons=buttons))

    # "全部"按钮：插入五位成员名作为参数
    all_label = "全部取消" if is_unsub else "全部订阅"
    all_cmd = f"{prefix} {_ALL_NAMES_STR}"
    rows.append(
        InlineKeyboardRow(
            buttons=[_mk_cmd_button("live_sub_all", all_label, all_cmd)]
        )
    )

    keyboard = MessageKeyboard(content=InlineKeyboard(rows=rows))
    return md, keyboard


def _parse_keywords(text: str) -> list[str]:
    """解析用户输入的 up主名称，空格分隔。'全部' 替换为五位默认成员。"""
    text = text.strip()
    if not text:
        return []
    parts = text.split()
    if "全部" in parts:
        return _ALL_NAMES
    return parts


def _build_push_warning_md(results: str = "") -> MessageSegment:
    """未开启主动推送时的提醒：订阅结果 + md 文本 + 使用说明链接。"""
    content = (f"{results}\n\n" if results else "") + (
        "## ⚠️ 还差一步\n\n"
        "本群尚未开启主动推送，暂时收不到开播通知。\n\n"
        "请群主点击 bot 头像 → 右上角设置 → 允许机器人主动发言\n\n"
        "开启后我会自动检测，3 分钟内订阅生效~\n\n"
        "[📖 查看操作说明](https://docs.qq.com/doc/DRkFEbEhoa1Jzc05r)"
    )
    return MessageSegment.markdown(content)


# ── 订阅开播 ──

@subscribe_matcher.handle()
async def _(event: Event, arg: Message = CommandArg()):
    if not isinstance(event, GroupMessageCreateEvent):
        await subscribe_matcher.finish("开播订阅仅在群内可用~")
        return
    if event.author.member_role != "owner":
        await subscribe_matcher.finish("只有群主可以使用该指令~")
        return
    gid = event.group_openid
    keywords = _parse_keywords(arg.extract_plain_text())

    if not keywords:
        md, keyboard = _build_md_keyboard("/订阅开播")
        await subscribe_matcher.finish(
            MessageSegment.markdown(md) + MessageSegment.keyboard(keyboard)
        )

    results: list[str] = []
    subscribed: list[tuple[int, str]] = []  # 本次新订阅的 (uid, name)，超时取消用
    for kw in keywords:
        upstream = manager.search_upstream(kw)
        if upstream is None:
            results.append(f"✗「{kw}」未找到匹配的 up主")
            continue
        if await manager.is_subscribed(gid, upstream["uid"]):
            results.append(f"○ {upstream['name']} 已订阅，跳过")
            continue
        await manager.subscribe(gid, upstream["uid"])
        results.append(f"✓ 已订阅 {upstream['name']}")
        subscribed.append((upstream["uid"], upstream["name"]))

    # 已开启主动推送：直接就绪
    if relations.is_group_push_ok(gid) is True:
        await subscribe_matcher.finish("\n".join(results) + "\n✅ 开播通知已就绪~")

    # 无新订阅且未开推送：提示但不等待
    if not subscribed:
        await subscribe_matcher.finish(
            "\n".join(results)
            + "\n⚠️ 本群尚未开启主动推送，已有订阅暂无法收到通知"
        )

    # 有新订阅但未开推送：提醒群主开启，轮询等待 3 分钟
    await subscribe_matcher.send(_build_push_warning_md("\n".join(results)))
    for _ in range(6):  # 每 30s 检查一次，共 3 分钟
        await asyncio.sleep(30)
        if relations.is_group_push_ok(gid) is True:
            await subscribe_matcher.finish("✅ 已检测到主动推送开启，订阅生效~")
    # 超时：取消本次订阅
    for uid, _name in subscribed:
        await manager.unsubscribe(gid, uid)
    await subscribe_matcher.finish(
        "⏰ 3 分钟内未开启主动推送，本次订阅已取消，请群主开启后重新订阅~"
    )

# ── 取消订阅 ──

@unsubscribe_matcher.handle()
async def _(event: Event, arg: Message = CommandArg()):
    if not isinstance(event, GroupMessageCreateEvent):
        await unsubscribe_matcher.finish("开播订阅仅在群内可用~")
        return
    if event.author.member_role != "owner":
        await unsubscribe_matcher.finish("只有群主可以使用该指令~")
        return
    gid = event.group_openid
    keywords = _parse_keywords(arg.extract_plain_text())

    if not keywords:
        md, keyboard = _build_md_keyboard("/取消订阅")
        await unsubscribe_matcher.finish(
            MessageSegment.markdown(md) + MessageSegment.keyboard(keyboard)
        )

    results: list[str] = []
    for kw in keywords:
        upstream = manager.search_upstream(kw)
        if upstream is None:
            results.append(f"✗「{kw}」未找到匹配的 up主")
            continue
        if not await manager.is_subscribed(gid, upstream["uid"]):
            results.append(f"○ {upstream['name']} 未订阅，跳过")
            continue
        await manager.unsubscribe(gid, upstream["uid"])
        results.append(f"✓ 已取消 {upstream['name']}")

    await unsubscribe_matcher.finish("\n".join(results))


# ── 订阅列表 ──

@list_matcher.handle()
async def _(event: Event):
    gid = getattr(event, "group_openid", None)
    if not gid:
        await list_matcher.finish("开播订阅仅在群内可用~")
    subs = await manager.list_for_group(gid)
    if not subs:
        await list_matcher.finish(
            "本群还没有订阅任何 up主 的开播通知。\n发送 /订阅开播 查看可选列表"
        )

    lines = ["本群的开播订阅："]
    for i, s in enumerate(subs, 1):
        lines.append(f"{i}. {s['name']}（UID:{s['uid']}）")
    await list_matcher.finish("\n".join(lines))


# ── 订阅全览（SUPERUSER）──

@overview_matcher.handle()
async def _():
    data = await manager.list_all()
    if not data:
        await overview_matcher.finish("没有任何群订阅了开播通知")
    await overview_matcher.finish(f"共 {len(data)} 个群订阅了开播通知")
