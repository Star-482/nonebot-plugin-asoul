"""
@Author: star_482
@Date: 2025/4/11
@File: whateat
@Description:
"""
import json
import os
import time
import secrets
from datetime import date
from pathlib import Path
from typing import Literal

from nonebot import get_driver
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
from ..storage import get_bucket, KEY_PREFIX, manifest

NICKNAME = list(get_driver().config.nickname)
BOT_NAME = NICKNAME[0] if NICKNAME else "然然"

# 全局 CD（秒）
_cd_last: float = 0.0
# 每日用户使用次数 {date_str: {user_id: count}}
_daily_count: dict[str, dict[str, int]] = {}
# 达到上限时的随机回复
MAX_MSG = [
    "你今天吃的够多了！不许再吃了(´-ωก`)",
    "吃吃吃，就知道吃，你都吃饱了！明天再来(▼皿▼#)",
    "(*｀へ´*)你猜我会不会再给你发好吃的图片",
    f"没得吃的了，{BOT_NAME}的食物都被你这坏蛋吃光了！",
    "你在等我给你发好吃的？做梦哦！你都吃那么多了，不许再吃了！ヽ(≧Д≦)ノ",
]

_res_path = "data/whateat_pic"
# 用户投稿元数据缓存：{menu_type: {filename: {"submitter": str, "date": str}}}
_submission_cache: dict[str, dict[str, dict]] | None = None


def _load_submissions() -> dict[str, dict[str, dict]]:
    """懒加载用户投稿元数据，返回 {menu_type: {filename: {submitter, date}}}。"""
    meta_file = Path(_res_path) / "user_submitted.json"
    result: dict[str, dict[str, dict]] = {"eat_pic": {}, "drink_pic": {}}
    if not meta_file.exists():
        return result
    try:
        with open(meta_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return result
    if isinstance(data, dict):
        for key in ("eat_pic", "drink_pic"):
            entries = data.get(key)
            if isinstance(entries, dict):
                result[key] = {
                    name: {"submitter": info.get("submitter", ""), "date": info.get("date", "")}
                    for name, info in entries.items()
                    if isinstance(info, dict)
                }
    return result


def _get_submission_info(menu_type: str, filename: str) -> dict | None:
    """获取某张图片的投稿信息，非投稿返回 None（触发懒加载）。"""
    global _submission_cache
    if _submission_cache is None:
        _submission_cache = _load_submissions()
    return _submission_cache.get(f"{menu_type}_pic", {}).get(filename)


def _get_today() -> str:
    return date.today().isoformat()


def _check_ismax(event: Event) -> bool:
    """检查用户是否达到每日次数上限，未达上限则计数+1。"""
    max_count = config.whateat_max
    if max_count == 0:
        return False
    today = _get_today()
    user_id = event.get_user_id()
    if today not in _daily_count:
        _daily_count.clear()
        _daily_count[today] = {}
    day_data = _daily_count[today]
    if user_id not in day_data:
        day_data[user_id] = 0
    if day_data[user_id] < max_count:
        day_data[user_id] += 1
        return False
    return True


def _check_cd() -> tuple[bool, float]:
    """检查全局 CD，返回 (是否在CD中, 剩余秒数)。"""
    global _cd_last
    cd = config.whateat_cd
    now = time.time()
    elapsed = now - _cd_last
    if elapsed < cd:
        return True, cd - elapsed
    _cd_last = now
    return False, 0.0


def _random_pic(menu_type: Literal["drink", "eat"]) -> tuple[Path, str, dict | None]:
    """从本地随机选取一张图片，返回 (路径, 名称, 投稿信息/None)。"""
    pic_dir = Path(_res_path) / f"{menu_type}_pic"
    pic_list = os.listdir(pic_dir)
    pic_name = secrets.choice(pic_list)
    pic_path = pic_dir / pic_name
    sub_info = _get_submission_info(menu_type, pic_name)
    return pic_path, Path(pic_name).stem, sub_info


async def build_whateat_msg(menu_type: Literal["drink", "eat"], action_verb: str) -> MessageSegment:
    """构造吃什么/喝什么的完整 md 消息（含键盘），不绑 matcher。

    供命令 handler 和 agent 工具复用，保证两路径返回一致的模板。
    """
    bucket = get_bucket()
    prefix = KEY_PREFIX["whateat_eat"] if menu_type == "eat" else KEY_PREFIX["whateat_drink"]
    command = "/今天吃什么" if menu_type == "eat" else "/今天喝什么"
    food_word = "美食" if menu_type == "eat" else "饮品"

    pic_path, pic_name, sub_info = _random_pic(menu_type)
    url = await bucket.get_or_upload_file(pic_path, prefix=prefix)

    if url is not None:
        key = f"{prefix}/{pic_path.name}"
        entry = manifest.get_static(key)
        w = entry.get("width", 0) if entry else 0
        h = entry.get("height", 0) if entry else 0
        md_img = bucket.build_md_image(url, w, h, pic_name)
        submission_note = ""
        if sub_info:
            submitter = sub_info.get("submitter", "")
            if submitter:
                submission_note = f" 🏷️用户投稿 | 投稿人：{submitter}\n\n"
            else:
                submission_note = " 🏷️用户投稿\n\n"
        else:
            submission_note = "\n"
        md = f"### 🎉{BOT_NAME}建议你{action_verb}🎉\n\n**{pic_name}**\n\n{submission_note}{md_img}\n\n\n没有心仪的{food_word}？[点击投稿](https://docs.qq.com/form/page/DRkhCT0JLaFFJQmdJ)"
        keyboard = MessageKeyboard(
            content=InlineKeyboard(
                rows=[InlineKeyboardRow(buttons=[
                    Button(
                        id=f"whateat_{menu_type}_again",
                        render_data=RenderData(label="换一个", visited_label="换一个", style=1),
                        action=Action(type=2, permission=Permission(type=2), data=command,
                                      reply=False, enter=True, unsupport_tips=f"请手动发送：{command}"),
                    ),
                ])]
            )
        )
        return MessageSegment.markdown(md) + MessageSegment.keyboard(keyboard)
    else:
        submission_note = ""
        if sub_info:
            submitter = sub_info.get("submitter", "")
            if submitter:
                submission_note = f" 🏷️用户投稿 | 投稿人：{submitter}"
            else:
                submission_note = " 🏷️用户投稿"
        return MessageSegment.file_image(pic_path) + MessageSegment.text(f"🎉{BOT_NAME}建议你{action_verb}🎉\n{pic_name}{submission_note}")


async def _send_whateat(menu_type: Literal["drink", "eat"], action_verb: str, matcher):
    """eat 和 drink 的共用发送逻辑。"""
    await matcher.finish(await build_whateat_msg(menu_type, action_verb))


eat_pic_matcher = on_command("今天吃什么", priority=config.command_priority)
drink_pic_matcher = on_command("今天喝什么", priority=config.command_priority)

@eat_pic_matcher.handle()
async def handle_eat(event: Event):
    if _check_ismax(event):
        await eat_pic_matcher.finish(MessageSegment.text(secrets.choice(MAX_MSG)))
    in_cd, remain = _check_cd()
    if in_cd:
        await eat_pic_matcher.finish(MessageSegment.text(f"cd冷却中, 还有{remain:.2f}秒"))
    await _send_whateat("eat", "吃", eat_pic_matcher)


@drink_pic_matcher.handle()
async def handle_drink(event: Event):
    if _check_ismax(event):
        await drink_pic_matcher.finish(MessageSegment.text(secrets.choice(MAX_MSG)))
    in_cd, remain = _check_cd()
    if in_cd:
        await drink_pic_matcher.finish(MessageSegment.text(f"cd冷却中, 还有{remain:.2f}秒"))
    await _send_whateat("drink", "喝", drink_pic_matcher)
