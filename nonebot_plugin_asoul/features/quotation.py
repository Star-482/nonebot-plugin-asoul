"""
@Author: star_482
@Date: 2026/8/7
@File: quotation
@Description: 发病小作文 + 我的id 命令
"""
import random

from nonebot.adapters import Event
from nonebot.adapters.qq import MessageSegment
from nonebot.adapters.qq.models import (
    Action,
    Button,
    InlineKeyboard,
    InlineKeyboardRow,
    MessageKeyboard,
    Permission,
    RenderData,
)
from nonebot.plugin.on import on_command

from ..config import config
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
    md = f"## {title}\n\n{quoted}\n\n\n{submission_note}你也想发病？[点我投稿](https://docs.qq.com/form/page/DRkhCT0JLaFFJQmdJ) 分享你的小作文吧~"
    keyboard = MessageKeyboard(
        content=InlineKeyboard(
            rows=[
                InlineKeyboardRow(
                    buttons=[
                        Button(
                            id="quotation_again",
                            render_data=RenderData(label="再来一篇", visited_label="再来一篇", style=1),
                            action=Action(
                                type=2,
                                permission=Permission(type=2),
                                data="/发病小作文",
                                reply=False,
                                enter=False,
                                unsupport_tips="请手动发送：/发病小作文",
                            ),
                        ),
                    ]
                )
            ]
        )
    )
    await quotation.finish(MessageSegment.markdown(md) + MessageSegment.keyboard(keyboard))
