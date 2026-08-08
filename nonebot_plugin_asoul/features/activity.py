"""
@Author: star_482
@Date: 2025/4/17
@File: activity
@Description:
"""
import json
from datetime import date, timedelta
from pathlib import Path

from nonebot.adapters import Event
from nonebot.adapters.qq import Message, MessageEvent, MessageSegment
from nonebot.log import logger
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from nonebot.plugin.on import on_command

from ..utils import download_img, open_json, save_json
from ..config import config


def save_img_activity(url: str):
    try:
        today_date = date.today()
        # img_name = today_date.isoformat() + ".png"
        download_img(url, config.data_path + "/activity", "new_activity.jpg")
        return True
    except Exception as e:
        return False


def save_json_activity(content: str):
    data: dict = open_json("activity/activity.json")
    try:
        new_data: dict = json.loads(content)
        for key, value in new_data.items():
            if key in data:
                data[key].extend(value)  # 合并列表
            else:
                data[key] = value  # 新增键值对
        save_json("activity/activity.json", data)
        return True
    except Exception as e:
        return False


def get_relative_content():
    """
    Returns today's and tomorrow's activities from the stored JSON data.
    """
    try:
        # Load the activity data
        data: dict = open_json("activity/activity.json")
        # Get today's and tomorrow's dates
        today = date.today().isoformat().replace("-", ".")
        tomorrow = (date.today() + timedelta(days=1)).isoformat().replace("-", ".")
        # Retrieve activities for today and tomorrow
        today_activities = data.get(today, [])
        tomorrow_activities = data.get(tomorrow, [])
        return {
            "today": today_activities,
            "tomorrow": tomorrow_activities
        }
    except Exception as e:
        return {
            "today": [],
            "tomorrow": []
        }


week_activity = on_command("本周日程", aliases={"日程"}, priority=config.command_priority)
add_activity = on_command("添加日程", priority=config.command_priority, permission=SUPERUSER)


@week_activity.handle()
async def _(event: Event):
    img_path = Path(config.data_path) / "activity" / "new_activity.jpg"
    content = get_relative_content()
    text = ""
    logger.info(content)
    if content["today"]:
        text = text + "今天的安排有：" + ",\n ".join(content["today"])
    if content["tomorrow"]:
        text = text + "\n明天的安排有：" + ",\n ".join(content["tomorrow"])
    message = MessageSegment.file_image(img_path) + MessageSegment.text(text)
    await week_activity.finish(message)


@add_activity.handle()
async def _(event: MessageEvent, arg: Message = CommandArg()):
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
