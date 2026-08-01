"""
@Author: star_482
@Date: 2025/4/11
@File: fortune_manager
@Description:抽签相关的类和方法
"""
import json
import os
import random
from datetime import date
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

from .utils import open_json, drawing, pick_fortune_base, drawing_to_bytes
from .config import config
from .storage import get_bucket, KEY_PREFIX, manifest, _recipe_hash


class FortuneManager:
    def __init__(self):
        self.fortune_data_path = os.path.join(config.data_path, "resource/fortune_data.json")
        self.fortune_data = {}
        self.copywriter = open_json("resource/fortune/copywriting.json")
        self.load_data()

    def check_data(self, gid, uid) -> bool:
        """今天是否还没抽过签。"""
        if gid not in self.fortune_data:
            self.fortune_data[gid] = {}
        if uid not in self.fortune_data[gid]:
            self.fortune_data[gid][uid] = None
        entry = self.fortune_data[gid][uid]
        if isinstance(entry, dict):
            last_date = entry.get("date")
        else:
            last_date = entry
        return last_date != date.today().isoformat()

    def get_cached_info(self, gid, uid) -> dict | None:
        """返回已抽签用户的缓存信息 {url, w, h} 或 None。"""
        entry = self.fortune_data.get(gid, {}).get(uid)
        if isinstance(entry, dict) and entry.get("url"):
            return {"url": entry["url"], "w": entry.get("w", 420), "h": entry.get("h", 420)}
        return None

    async def do_draw(self, gid, uid) -> dict:
        """执行抽签，返回 {"url": ..., "title": ...} 或降级的 {"img_path": ..., "title": ...}。"""
        title, text = self.get_copywriting()
        base_name = pick_fortune_base()
        recipe = {"v": 1, "base": base_name, "title": title, "text": text}
        today_date = date.today().isoformat()

        async def producer():
            data, w, h = drawing_to_bytes(base_name, title, text)
            return data

        bucket = get_bucket()
        url = await bucket.get_or_render(recipe, producer, prefix=KEY_PREFIX["addressed_fortune"])

        if url is not None:
            entry = manifest.get_addressed(_recipe_hash(recipe))
            img_w = entry.get("width", 420) if entry else 420
            img_h = entry.get("height", 420) if entry else 420
            self.fortune_data[gid][uid] = {"date": today_date, "url": url, "w": img_w, "h": img_h}
            return {"url": url, "title": title, "w": img_w, "h": img_h}
        else:
            img_path = drawing(gid, uid, title, text)
            self.fortune_data[gid][uid] = {"date": today_date}
            return {"img_path": img_path, "title": title}

    def get_copywriting(self):
        """
        Read the copywriting.json, choice a luck with a random content
        """
        content = self.copywriter.get("copywriting")
        luck = random.choice(content)
        title: str = luck.get("good-luck")
        text: str = random.choice(luck.get("content"))
        return title, text

    def load_data(self):
        if not os.path.exists(self.fortune_data_path):
            os.makedirs(os.path.dirname(self.fortune_data_path), exist_ok=True)
            with open(self.fortune_data_path, "w", encoding="utf-8") as f:
                json.dump({}, f, ensure_ascii=False, indent=4)

        with open(self.fortune_data_path, "r", encoding="utf-8") as f:
            self.fortune_data = json.load(f)

    def save_data(self):
        with open(self.fortune_data_path, "w", encoding="utf-8") as f:
            json.dump(self.fortune_data, f, ensure_ascii=False, indent=4)


def build_fortune_md(result: dict, uid: str) -> MessageSegment:
    """构造今日运势的完整 md 消息（含键盘），不绑 matcher。供命令和 agent 工具复用。"""
    if "url" in result:
        bucket = get_bucket()
        md_img = bucket.build_md_image(
            result["url"], result.get("w", 420), result.get("h", 420), result.get("title", "")
        )
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
        return MessageSegment.markdown(md) + MessageSegment.keyboard(keyboard)
    else:
        img_path = Path(result["img_path"])
        return MessageSegment.file_image(img_path) + MessageSegment.text("✨今日运势✨\n")


fortune_manager = FortuneManager()
