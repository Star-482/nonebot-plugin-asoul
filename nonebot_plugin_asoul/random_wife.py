"""
@Author: star_482
@Date: 2026/5/13
@File: random_wife
@Description:
"""
import os
import random
from pathlib import Path

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

from .config import config
from .storage import get_bucket, KEY_PREFIX, manifest


def _wife_path() -> Path:
    return Path(config.data_path) / config.wife_img_dir


async def get_random_wife_md_message():
    """返回 QQ Markdown 消息（图片走 COS 公网 URL）+ 内联键盘。

    降级路径：
    - 目录不存在 / 为空 → 文本提示
    - COS 上传失败 → 回退到本地 Image(path=...) 发送
    """
    wife_path = _wife_path()
    if not wife_path.exists():
        return MessageSegment.text("老婆图库还没准备好，请先放入图片后再试~")

    try:
        imgs = os.listdir(wife_path)
    except OSError:
        return MessageSegment.text("读取老婆图库失败了，稍后再试试吧~")

    if not imgs:
        return MessageSegment.text("老婆图库还是空的，请先放入图片后再试~")

    img_name = random.choice(imgs)
    img = wife_path / img_name
    name = os.path.splitext(img_name)[0]

    bucket = get_bucket()
    url = await bucket.get_or_upload_file(img, prefix=KEY_PREFIX["wife"])

    if url is None:
        # COS 上传失败 → 降级到本地图片
        return MessageSegment.file_image(img) + MessageSegment.text(f"你今日抽取的老婆是{name}")

    # 成功：从 manifest 取宽高
    key = f"{KEY_PREFIX['wife']}/{img_name}"
    entry = manifest.get_static(key)
    width = entry.get("width", 0) if entry else 0
    height = entry.get("height", 0) if entry else 0

    md_img = bucket.build_md_image(url, width, height, name)

    content = (
        "## 今日抽老婆\n"
        f"你今日抽取的老婆是 **{name}**\n\n"
        f"{md_img}\n\n"
        "添加喜欢的角色[【点击投稿】](https://docs.qq.com/form/page/DRkhCT0JLaFFJQmdJ)"
    )

    keyboard = MessageKeyboard(
        content=InlineKeyboard(
            rows=[
                InlineKeyboardRow(
                    buttons=[
                        Button(
                            id="wife_again",
                            render_data=RenderData(label="再抽老婆", visited_label="再抽老婆", style=1),
                            action=Action(
                                type=2,
                                permission=Permission(type=2),
                                data="/抽老婆",
                                reply=False,
                                enter=False,
                                unsupport_tips="请手动发送：/抽老婆",
                            ),
                        ),
                    ]
                )
            ]
        )
    )

    return MessageSegment.markdown(content) + MessageSegment.keyboard(keyboard)
