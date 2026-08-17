"""
@Author: star_482
@Date: 2026/8/7
@File: quotation
@Description: 发病小作文 + 我的id 命令
"""
import random

from nonebot.adapters import Event
from nonebot.adapters.qq import MessageSegment
from nonebot.plugin.on import on_command

from ..config import config
from ..markdown import BTN_PIXEL_BOARD, BTN_QUOTATION_AGAIN, URL_SUBMIT, build_keyboard
from ..utils import open_json

my_openid = on_command("我的id", priority=config.command_priority)
quotation = on_command("发病小作文", aliases={"发病"}, priority=config.command_priority)
# add_quotation = on_command("添加发病小作文", aliases={"添加发病"}, permission=SUPERUSER,
#                            priority=config.command_priority)


@my_openid.handle()
async def _(event: Event):
    uid = event.get_user_id()
    await quotation.finish(f"你的唯一id是{uid}")


@quotation.handle()
async def _():
    data: dict = open_json("quotation.json")
    entry = random.choice(list(data.values()))
    title = entry["title"]
    content = entry["content"]
    submitter = entry.get("submitter", "")
    quoted = "\n".join(f"> {line}" if line else ">" for line in content.split("\n"))
    submission_note = f"🏷️用户投稿 | 投稿人：{submitter}\n\n" if submitter else ""
    md = f"## {title}\n\n{quoted}\n\n\n{submission_note}你也想发病？[点我投稿]({URL_SUBMIT}) 分享你的小作文吧~"
    keyboard = build_keyboard([[BTN_QUOTATION_AGAIN, BTN_PIXEL_BOARD]])
    await quotation.finish(MessageSegment.markdown(md) + MessageSegment.keyboard(keyboard))
