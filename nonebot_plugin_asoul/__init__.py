"""
@Author: star_482
@Date: 2025/3/28 
@File: __init__.py 
@Description:
"""
import random
import os

from nonebot.log import logger
from nonebot.adapters import Event
from nonebot.adapters.qq import Message, MessageEvent, MessageSegment, GroupAtMessageCreateEvent
from nonebot.adapters.qq.models import (
    Action,
    Button,
    InlineKeyboard,
    InlineKeyboardRow,
    MessageKeyboard,
    Permission,
    RenderData,
)
from nonebot.params import CommandArg, RawCommand
from nonebot.plugin import PluginMetadata
from nonebot.plugin.on import on_command
from nonebot import get_driver, require
from nonebot.permission import SUPERUSER

require("nonebot_plugin_alconna")
from nonebot_plugin_alconna import on_alconna
from nonebot_plugin_alconna.uniseg import Image, Text, UniMessage

from .config import config, Config
from . import start_up as _
from . import admin_stats as _admin_stats
from .diana import commands as _diana_commands
from . import whateat as _whateat
from . import storage as _storage
from .utils import open_json, download_img
from . import live_subscription as _live_subscription
from .fortune_manager import fortune_manager
from .activity import save_img_activity, save_json_activity, get_relative_content
from .markdown import get_about_xiaoran_markdown, get_test_markdown
from .random_wife import get_random_wife_md_message
from .storage import get_bucket

__plugin_meta__ = PluginMetadata(
    name="asoul插件",
    description="提供与asoul相关服务",
    usage="待定",
    type="application",
    config=Config,
    extra={},
)

my_openid = on_command("我的id", priority=config.command_priority)
quotation = on_command("发病小作文", aliases={"发病"}, priority=config.command_priority)
# add_quotation = on_command("添加发病小作文", aliases={"添加发病"}, permission=SUPERUSER,
#                            priority=config.command_priority)
daily_fortune = on_command("今日运势", aliases={"抽签"}, priority=config.command_priority)

week_activity = on_command("本周日程", aliases={"日程"}, priority=config.command_priority)
add_activity = on_command("添加日程", priority=config.command_priority, permission=SUPERUSER)

test_markdown = on_command("测试markdown", aliases={"测试md"}, priority=config.command_priority)
about_xiaoran = on_command("关于小然", aliases={"小然", "关于然然"}, priority=config.command_priority)
random_wife_matcher = on_command("抽老婆", priority=config.command_priority)


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
    quoted = "\n".join(f"> {line}" if line else ">" for line in content.split("\n"))
    md = f"## {title}\n\n{quoted}\n\n\n你也想发病？[点我投稿](https://docs.qq.com/form/page/DRkhCT0JLaFFJQmdJ) 分享你的小作文吧~"
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


@daily_fortune.handle()
async def _(event: GroupAtMessageCreateEvent):
    gid = event.group_openid
    uid = event.get_user_id()
    # admin 每次都重新生成，方便调试
    is_admin = event.get_user_id() in get_driver().config.superusers
    if is_admin or fortune_manager.check_data(gid, uid):
        result = await fortune_manager.do_draw(gid, uid)
        fortune_manager.save_data()
        if "url" in result:
            bucket = get_bucket()
            md_img = bucket.build_md_image(result["url"], result["w"], result["h"], result["title"])
            md = f"<@{uid}>\n### ✨今日运势✨\n\n{md_img}"
            keyboard = MessageKeyboard(
                content=InlineKeyboard(
                    rows=[InlineKeyboardRow(buttons=[
                        Button(
                            id="fortune_draw",
                            render_data=RenderData(label="我也要抽签", visited_label="我也要抽签", style=1),
                            action=Action(type=2, permission=Permission(type=2), data="/今日运势",
                                          reply=False, enter=False, unsupport_tips="请手动发送：/今日运势"),
                        ),
                    ])]
                )
            )
            await daily_fortune.finish(MessageSegment.markdown(md) + MessageSegment.keyboard(keyboard))
        else:
            message = UniMessage(Image(path=result["img_path"]))
            message.append(Text("✨今日运势✨\n"))
            await message.send()
    else:
        info = fortune_manager.get_cached_info(gid, uid)
        if info and info.get("url"):
            bucket = get_bucket()
            md_img = bucket.build_md_image(info["url"], info["w"], info["h"])
            md = f"<@{uid}>\n### 你今天抽过签了，再给你看一次哦🤗\n\n{md_img}"
            await daily_fortune.finish(MessageSegment.markdown(md))
        else:
            message = UniMessage(Text("你今天抽过签了，再给你看一次哦🤗\n"))
            img_path = os.path.join(config.data_path, f"resource/out/{gid}_{uid}.png")
            message.append(Image(path=img_path))
            await message.send()


@week_activity.handle()
async def _(event: GroupAtMessageCreateEvent):
    img_path = config.data_path + "/activity/new_activity.jpg"
    content = get_relative_content()
    text = ""
    logger.info(content)
    if content["today"]:
        text = text + "今天的安排有：" + ",\n ".join(content["today"])
    if content["tomorrow"]:
        text = text + "\n明天的安排有：" + ",\n ".join(content["tomorrow"])
    message = UniMessage(Text(text))
    message.append(Image(path=img_path))
    await message.send()


@add_activity.handle()
async def _(event: GroupAtMessageCreateEvent, arg: Message = CommandArg()):
    msg = event.get_message()
    image_segment = next((seg for seg in msg if seg.type == "image"), None)
    if image_segment:
        # 如果有图片，直接记录
        image_url = image_segment.data["url"]
        if save_img_activity(image_url):
            await add_activity.finish("日程已记录")
    elif msg[0].data["text"]:
        if save_json_activity(arg[0].data["text"]):
            await add_activity.finish("日程已记录")
    # logger.info(msg[0].data["text"])
    # logger.info(arg[0])
    await add_activity.finish("日程添加失败，请检查")


@test_markdown.handle()
async def _():
    message = get_test_markdown()
    await test_markdown.finish(message)


@about_xiaoran.handle()
async def _():
    message = get_about_xiaoran_markdown()
    await about_xiaoran.finish(message)


@random_wife_matcher.handle()
async def _():
    message = await get_random_wife_md_message()
    await random_wife_matcher.finish(message)
