"""
@Author: star_482
@Date: 2026/8/13
@File: group_admin
@Description: 群管功能--入群欢迎（群主/管理员开关 + 自定义，自定义经 SUPERUSER 复核，不通过回退默认）+ 禁言/解禁。
入群欢迎配置与审核流水持久化到 database（group_welcome / welcome_reviews 表）。
禁言 API 封装在 manage/qq_api（adapter 未提供，借 bot._request 自调 /v2/groups/{gid}/restrict_chat_setting）。
群管身份用 event.author.member_role（admin/owner）判断；禁言需 bot 是群管理员，失败据 API message 提示。
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from nonebot import get_driver, on_command, on_notice
from nonebot.adapters import Event
from nonebot.adapters.qq import Bot, Message
from nonebot.adapters.qq.event import (
    GroupMessageCreateEvent,
    GroupMemberAddEvent,
)
from nonebot.log import logger
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from nonebot.rule import Rule

from ..config import config
from ..database.repositories import GroupWelcomeRepo, WelcomeReviewRepo
from ..manage.qq_api import set_group_member_mute
from ..markdown import get_member_welcome_md, get_welcome_review_md

_TZ = timezone(timedelta(hours=8))

welcome_repo = GroupWelcomeRepo()
review_repo = WelcomeReviewRepo()


# ── 辅助 ──

def _is_group_member_add(event) -> bool:
    return isinstance(event, GroupMemberAddEvent)


def _is_group_msg(event) -> bool:
    return isinstance(event, GroupMessageCreateEvent)


def _is_group_admin(event: GroupMessageCreateEvent) -> bool:
    """发送者是群主/管理员或 SUPERUSER。"""
    if event.get_user_id() in get_driver().config.superusers:
        return True
    role = getattr(getattr(event, "author", None), "member_role", None)
    return role in ("admin", "owner")


def _extract_target(event: GroupMessageCreateEvent) -> Optional[str]:
    """从消息的 mention_user segments 取首个非 bot 的成员 openid。"""
    for seg in event.get_message():
        if seg.type == "mention_user" and not seg.data.get("is_bot"):
            uid = seg.data.get("user_id")
            if uid:
                return uid
    return None


def _parse_duration(text: str) -> Optional[timedelta]:
    """解析时长：30m/2h/1d/7d 或纯数字(分钟)。空文本默认 15 分钟。>30 天或无效返回 None。"""
    text = text.strip().lower()
    if not text:
        return timedelta(minutes=15)
    units = {"m": 60, "h": 3600, "d": 86400}
    if text[-1] in units:
        try:
            n = int(text[:-1])
        except ValueError:
            return None
        seconds = n * units[text[-1]]
    else:
        try:
            n = int(text)
        except ValueError:
            return None
        seconds = n * 60
    if seconds <= 0 or seconds > 30 * 86400:
        return None
    return timedelta(seconds=seconds)


def _to_rfc3339(delta: timedelta) -> str:
    return (datetime.now(_TZ) + delta).isoformat()


# ── 入群欢迎（被动回复，自动带 event_id）──

_member_add = on_notice(rule=Rule(_is_group_member_add), priority=100)


@_member_add.handle()
async def _on_member_add(event: GroupMemberAddEvent):
    if not config.member_welcome_enabled:
        return
    text = welcome_repo.get_effective_text(
        event.group_openid, config.member_welcome_default_text
    )
    if not text:
        return
    await _member_add.send(get_member_welcome_md(text, event.member_openid))


# ── 群主/管理员：欢迎语开关 / 自定义 / 查看 ──

welcome_on = on_command("开启欢迎语", rule=Rule(_is_group_msg), priority=config.command_priority)
welcome_off = on_command("关闭欢迎语", rule=Rule(_is_group_msg), priority=config.command_priority)
set_welcome = on_command("设置欢迎语", rule=Rule(_is_group_msg), priority=config.command_priority)
view_welcome = on_command("查看欢迎语", rule=Rule(_is_group_msg), priority=config.command_priority)


@welcome_on.handle()
async def _welcome_on(event: GroupMessageCreateEvent):
    if not _is_group_admin(event):
        await welcome_on.finish("仅群主或管理员可操作。")
    welcome_repo.set_enabled(event.group_openid, True, event.get_user_id())
    await welcome_on.finish("已开启入群欢迎。")


@welcome_off.handle()
async def _welcome_off(event: GroupMessageCreateEvent):
    if not _is_group_admin(event):
        await welcome_off.finish("仅群主或管理员可操作。")
    welcome_repo.set_enabled(event.group_openid, False, event.get_user_id())
    await welcome_off.finish("已关闭入群欢迎。")


@set_welcome.handle()
async def _set_welcome(event: GroupMessageCreateEvent, bot: Bot, arg: Message = CommandArg()):
    if not _is_group_admin(event):
        await set_welcome.finish("仅群主或管理员可操作。")
    text = arg.extract_plain_text().strip()
    if not text:
        await set_welcome.finish("用法：/设置欢迎语 <欢迎语内容>")
    role = getattr(getattr(event, "author", None), "member_role", "") or ""
    # 立即生效
    welcome_repo.set_text(event.group_openid, text, event.get_user_id())
    # 创建审核记录并通知 SUPERUSER 复核
    review = review_repo.create(event.group_openid, event.get_user_id(), role, text)
    md = get_welcome_review_md(review)
    for openid in get_driver().config.superusers:
        try:
            await bot.send_to_c2c(openid, md)
        except Exception as e:
            logger.warning(f"[群管] 审核消息发送失败 superuser={openid}: {e!r}")
    await set_welcome.finish("欢迎语已设置并生效。")


@view_welcome.handle()
async def _view_welcome(event: GroupMessageCreateEvent):
    if not _is_group_admin(event):
        await view_welcome.finish("仅群主或管理员可操作。")
    cfg = welcome_repo.get(event.group_openid)
    if cfg and not cfg.get("enabled"):
        await view_welcome.finish("本群入群欢迎已关闭。/开启欢迎语 开启。")
    text = (cfg.get("text") if cfg else None) or config.member_welcome_default_text
    kind = "自定义" if (cfg and cfg.get("text")) else "默认"
    await view_welcome.finish(f"当前欢迎语（{kind}）：\n{text}")


# ── SUPERUSER：审核自定义欢迎语（C2C 按钮注入或手动）──

review_welcome = on_command("审核欢迎语", permission=SUPERUSER, priority=config.command_priority)


@review_welcome.handle()
async def _review_welcome(event: Event, arg: Message = CommandArg()):
    parts = arg.extract_plain_text().split()
    if len(parts) < 2 or parts[0] not in ("同意", "拒绝"):
        await review_welcome.finish("用法：/审核欢迎语 <同意|拒绝> <id>")
    action = parts[0]
    try:
        rid = int(parts[1])
    except ValueError:
        await review_welcome.finish("id 必须是数字。")
    review = review_repo.get(rid)
    if not review:
        await review_welcome.finish(f"审核记录 {rid} 不存在。")
        return
    if review["status"] != "pending":
        await review_welcome.finish(f"该记录已处理（{review['status']}）。")
    reviewer = event.get_user_id()
    if action == "同意":
        review_repo.approve(rid, reviewer)
        await review_welcome.finish(f"已同意审核 {rid}，欢迎语保持自定义。")
    review_repo.reject(rid, reviewer)
    welcome_repo.reset_text(review["group_openid"], reviewer)
    await review_welcome.finish(f"已拒绝审核 {rid}，欢迎语已恢复默认。")


# ── 群主/管理员/SUPERUSER：禁言 / 解禁 ──

mute = on_command("禁言", rule=Rule(_is_group_msg), priority=config.command_priority)
unmute = on_command("解禁", rule=Rule(_is_group_msg), priority=config.command_priority)


@mute.handle()
async def _mute(event: GroupMessageCreateEvent, bot: Bot, arg: Message = CommandArg()):
    if not _is_group_admin(event):
        await mute.finish("仅群主或管理员可操作。")
    target = _extract_target(event)
    if not target:
        await mute.finish("请 @ 要禁言的成员。")
    delta = _parse_duration(arg.extract_plain_text())
    if delta is None:
        await mute.finish("时长格式无效或超过 30 天。用法：/禁言 @成员 <30m|2h|1d|7d>（不填默认 15 分钟）")
        return
    expire = _to_rfc3339(delta)
    ok, msg = await set_group_member_mute(
        bot, event.group_openid,
        [{"op": "add", "member_openid": target, "mute_expire_at": expire}],
    )
    if ok:
        return  # 静默：禁言成功不回复，避免刷屏
    await mute.finish(f"禁言失败：{msg}")


@unmute.handle()
async def _unmute(event: GroupMessageCreateEvent, bot: Bot):
    if not _is_group_admin(event):
        await unmute.finish("仅群主或管理员可操作。")
    target = _extract_target(event)
    if not target:
        await unmute.finish("请 @ 要解禁的成员。")
    ok, msg = await set_group_member_mute(
        bot, event.group_openid,
        [{"op": "del", "member_openid": target, "mute_expire_at": ""}],
    )
    if ok:
        return  # 静默：解禁成功不回复，避免刷屏
    await unmute.finish(f"解禁失败：{msg}")
