"""
@Author: star_482
@Date: 2026/8/13
@File: group_admin
@Description: 群管功能--入群欢迎（群主/管理员开关 + 自定义，自定义经 SUPERUSER 复核，不通过回退默认）+ 禁言/解禁 + 关键词撤回。
入群欢迎配置与审核流水持久化到 database（group_welcome / welcome_reviews 表）。
禁言 API 封装在 manage/qq_api（adapter 未提供，借 bot._request 自调 /v2/groups/{gid}/restrict_chat_setting）。
关键词撤回：群主/管理员 /设置撤回关键词（整表覆盖）、/查看撤回关键词、/删除撤回关键词（移除指定词）、
/清空撤回关键词，event_preprocessor 检测群消息命中后调 Bot.delete_group_message 撤回；
bot 需为群管理员身份才能撤回成功（API 失败时记录日志，不阻塞事件）。
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
from nonebot.adapters.qq.exception import ActionFailed
from nonebot.log import logger
from nonebot.message import event_preprocessor
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from nonebot.rule import Rule

from ..config import config
from ..database.repositories import GroupRecallRepo, GroupWelcomeRepo, WelcomeReviewRepo
from ..manage.qq_api import set_group_member_mute
from ..markdown import (
    get_group_admin_help_md,
    get_member_welcome_md,
    get_welcome_review_md,
)

_TZ = timezone(timedelta(hours=8))

welcome_repo = GroupWelcomeRepo()
review_repo = WelcomeReviewRepo()
recall_repo = GroupRecallRepo()


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


def _find_recall_keyword(keywords: list[str], text: str) -> Optional[str]:
    """返回文本命中的第一个撤回关键词；未命中返回 None。大小写不敏感。"""
    if not text:
        return None
    lowered = text.lower()
    for word in keywords:
        if word and word.lower() in lowered:
            return word
    return None


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


# ── 关键词撤回（群消息命中即撤回，不阻塞事件）──

@event_preprocessor
async def _recall_keyword_preprocessor(event: Event, bot: Bot):
    """检测群消息中的撤回关键词并调 QQ 接口撤回。

    只处理普通群成员消息：bot 自己、群主/管理员、SUPERUSER 均跳过。
    撤回失败（如 bot 非群管理员）仅记录日志，不影响消息后续处理。
    注意：只能检测 QQ 推送过来的群消息；被动接收模式下平台只会推送 @bot 的消息。
    """
    if not isinstance(event, GroupMessageCreateEvent):
        return

    author = getattr(event, "author", None)
    if getattr(author, "bot", False):
        return
    if _is_group_admin(event):
        return

    keywords = recall_repo.get_keywords(event.group_openid)
    if not keywords:
        return

    text = event.get_message().extract_plain_text()
    hit = _find_recall_keyword(keywords, text)
    if not hit:
        return

    message_id = str(getattr(event, "id", "") or "")
    if not message_id:
        logger.warning(f"[群管] 消息缺少 id，无法撤回 gid={event.group_openid}")
        return

    try:
        # QQ 官方 API：DELETE /v2/groups/{group_openid}/messages/{message_id}
        await bot.delete_group_message(
            group_openid=event.group_openid, message_id=message_id
        )
    except ActionFailed as e:
        # 常见失败原因：bot 非群管理员 / 消息不存在 / 无权限撤回该成员
        logger.warning(
            f"[群管] 撤回失败 gid={event.group_openid} mid={message_id} "
            f"keyword={hit!r}: code={e.code} message={e.message or '未知错误'}"
        )
        return
    except Exception:
        logger.exception(
            f"[群管] 撤回异常 gid={event.group_openid} mid={message_id} keyword={hit!r}"
        )
        return

    logger.info(
        f"[群管] 已撤回群消息 gid={event.group_openid} mid={message_id} "
        f"user={event.get_user_id()} keyword={hit!r}"
    )


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


# ── 群主/管理员：撤回关键词 设置 / 查看 / 删除 / 清空 ──

set_recall_keywords = on_command(
    "设置撤回关键词", rule=Rule(_is_group_msg), priority=config.command_priority
)
view_recall_keywords = on_command(
    "查看撤回关键词", rule=Rule(_is_group_msg), priority=config.command_priority
)
del_recall_keywords = on_command(
    "删除撤回关键词", rule=Rule(_is_group_msg), priority=config.command_priority
)
clear_recall_keywords = on_command(
    "清空撤回关键词", rule=Rule(_is_group_msg), priority=config.command_priority
)


@set_recall_keywords.handle()
async def _set_recall_keywords(
    event: GroupMessageCreateEvent, arg: Message = CommandArg()
):
    if not _is_group_admin(event):
        await set_recall_keywords.finish("仅群主或管理员可操作。")
    raw = arg.extract_plain_text().strip()
    if not raw:
        await set_recall_keywords.finish(
            "用法：/设置撤回关键词 关键词1 关键词2 ..."
        )
    recall_repo.set_keywords(event.group_openid, raw.split(), event.get_user_id())
    keywords = recall_repo.get_keywords(event.group_openid)
    await set_recall_keywords.finish(
        f"已设置 {len(keywords)} 个撤回关键词：{'、'.join(keywords)}\n"
        "群成员发送包含以上关键词的消息时，然然会自动撤回。\n"
        "（该功能需要 Bot 为群管理员身份）"
    )


@view_recall_keywords.handle()
async def _view_recall_keywords(event: GroupMessageCreateEvent):
    if not _is_group_admin(event):
        await view_recall_keywords.finish("仅群主或管理员可操作。")
    keywords = recall_repo.get_keywords(event.group_openid)
    if not keywords:
        await view_recall_keywords.finish("本群尚未设置撤回关键词。")
    await view_recall_keywords.finish(
        f"当前共 {len(keywords)} 个撤回关键词：\n{'、'.join(keywords)}"
    )


@del_recall_keywords.handle()
async def _del_recall_keywords(
    event: GroupMessageCreateEvent, arg: Message = CommandArg()
):
    if not _is_group_admin(event):
        await del_recall_keywords.finish("仅群主或管理员可操作。")
    raw = arg.extract_plain_text().strip()
    if not raw:
        await del_recall_keywords.finish(
            "用法：/删除撤回关键词 关键词1 关键词2 ...（可用 /清空撤回关键词 一次性移除全部）"
        )
    words = raw.split()
    current = recall_repo.get_keywords(event.group_openid)
    if not current:
        await del_recall_keywords.finish("本群尚未设置撤回关键词。")
        return
    lowers = {w.lower() for w in words}
    removed = [w for w in current if w.lower() in lowers]
    if not removed:
        await del_recall_keywords.finish(
            f"以下关键词不在列表中：{'、'.join(words)}\n"
            f"当前关键词：{'、'.join(current)}"
        )
        return
    recall_repo.remove_keywords(event.group_openid, words, event.get_user_id())
    kept = recall_repo.get_keywords(event.group_openid)
    suffix = f"当前剩余 {len(kept)} 个：{'、'.join(kept)}" if kept else "当前已无撤回关键词。"
    await del_recall_keywords.finish(
        f"已删除 {len(removed)} 个关键词：{'、'.join(removed)}\n{suffix}"
    )


@clear_recall_keywords.handle()
async def _clear_recall_keywords(event: GroupMessageCreateEvent):
    if not _is_group_admin(event):
        await clear_recall_keywords.finish("仅群主或管理员可操作。")
    if not recall_repo.get_keywords(event.group_openid):
        await clear_recall_keywords.finish("本群尚未设置撤回关键词。")
    recall_repo.clear_keywords(event.group_openid, event.get_user_id())
    await clear_recall_keywords.finish("已清空全部撤回关键词。")


# ── 群管帮助（指令中心群管模块的详情页）──

group_admin_help = on_command("群管帮助", priority=config.command_priority)


@group_admin_help.handle()
async def _group_admin_help():
    await group_admin_help.finish(get_group_admin_help_md())


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
