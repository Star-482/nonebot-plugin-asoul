"""
@Author: star_482
@Date: 2026/8/1
@File: announcement
@Description: 更新公告推送 -- SUPERUSER 用 /设置公告 存 markdown，/推送更新 主动推送到
所有开了主动推送的群（复用 manage.relationships 的 push_ok 群列表）。
"""
import asyncio
from pathlib import Path

from nonebot import get_bot, on_command
from nonebot.adapters.qq import Message, MessageSegment
from nonebot.internal.matcher import Matcher
from nonebot.log import logger
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER

from ..config import config
from .relationships import relations

_ANNOUNCEMENT_FILE = "announcement.md"


def _announcement_path() -> Path:
    return Path(config.data_path) / _ANNOUNCEMENT_FILE


set_announcement = on_command("设置公告", priority=config.command_priority, permission=SUPERUSER)
preview_announcement = on_command("预览公告", priority=config.command_priority, permission=SUPERUSER)
push_update = on_command("推送更新", priority=config.command_priority, permission=SUPERUSER)


@set_announcement.handle()
async def _(arg: Message = CommandArg()):
    content = arg.extract_plain_text().strip()
    if not content:
        await set_announcement.finish("公告内容为空。用法：/设置公告 <markdown 内容>")
    path = _announcement_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    await set_announcement.finish(
        f"公告已设置（{len(content)} 字）。发送 /预览公告 查看效果，/推送更新 推送到所有群。"
    )


@preview_announcement.handle()
async def _():
    path = _announcement_path()
    if not path.exists():
        await preview_announcement.finish("还没有设置公告。先用 /设置公告 <markdown 内容> 设置。")
    md = path.read_text(encoding="utf-8").strip()
    if not md:
        await preview_announcement.finish("公告内容为空。")
    await preview_announcement.finish(MessageSegment.markdown(md))


@push_update.handle()
async def _(matcher: Matcher):
    path = _announcement_path()
    if not path.exists():
        await push_update.finish("还没有设置公告。先用 /设置公告 <markdown 内容> 设置。")
    md = path.read_text(encoding="utf-8").strip()
    if not md:
        await push_update.finish("公告内容为空。")
    groups = relations.get_push_ok_groups()
    if not groups:
        await push_update.finish("没有已验证可主动推送的群。需群主在群内开启“允许机器人主动发言”后再试。")

    message = MessageSegment.markdown(md)
    bot = get_bot()
    success = fail = 0
    for gid in groups:
        try:
            await bot.send_to_group(group_openid=gid, message=message)
            success += 1
        except Exception as e:
            logger.warning(f"[公告] 推送失败 gid={gid}: {e}")
            relations.mark_group_push_fail(gid)  # 失效则移出 push_ok，下次不再推
            fail += 1
        await asyncio.sleep(1)  # 防限流
    await matcher.send(
        f"推送完成：成功 {success} 群，失败 {fail} 群（共 {len(groups)} 群）。"
    )
