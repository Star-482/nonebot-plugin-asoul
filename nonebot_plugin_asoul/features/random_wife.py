"""
@Author: star_482
@Date: 2026/5/13
@File: random_wife
@Description:
"""
import asyncio
import json
import logging
import os
import random
from pathlib import Path
from typing import Literal

from nonebot import on_notice
from nonebot.adapters import Event
from nonebot.adapters.qq import Bot, Message, MessageSegment
from nonebot.adapters.qq.event import InteractionCreateEvent
from nonebot.params import CommandArg
from nonebot.plugin.on import on_command
from nonebot.rule import Rule

from ..config import config
from ..database.repositories import WifeVoteRepo
from ..markdown import (
    BTN_CHECKIN,
    BTN_FORTUNE,
    BTN_MENU,
    BTN_WIFE_AGAIN,
    BTN_WIFE_RANK_MONTH,
    BTN_WIFE_RANK_TOTAL,
    URL_SUBMIT,
    WIFE_VOTE_CALLBACK_PREFIX,
    build_keyboard,
    icon,
    wife_vote_button,
)
from ..storage import get_bucket, KEY_PREFIX, manifest

logger = logging.getLogger(__name__)

_vote_repo = WifeVoteRepo()
RankPeriod = Literal["total", "month"]
_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
WIFE_VOTE_DAILY_LIMIT = 3
WIFE_RANK_THUMBNAIL_HEIGHT = 64

# 老婆分类元数据缓存：{filename: {"category": str, "submitter": str, "date": str}}
_wife_meta_cache: dict[str, dict] | None = None


def _load_wife_meta() -> dict[str, dict]:
    """懒加载老婆分类元数据，拍平为 {filename: {category, submitter, date}}。"""
    meta_file = Path(config.data_path) / "wife_meta.json"
    result: dict[str, dict] = {}
    if not meta_file.exists():
        return result
    try:
        with open(meta_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return result
    if isinstance(data, dict):
        for category, entries in data.items():
            if isinstance(entries, dict):
                for fname, info in entries.items():
                    result[fname] = {
                        "category": category,
                        "submitter": info.get("submitter", "") if isinstance(info, dict) else "",
                        "date": info.get("date", "") if isinstance(info, dict) else "",
                    }
    return result


def _get_wife_info(filename: str) -> dict | None:
    """获取某张老婆图片的分类/投稿信息，未收录返回 None（触发懒加载）。"""
    global _wife_meta_cache
    if _wife_meta_cache is None:
        _wife_meta_cache = _load_wife_meta()
    return _wife_meta_cache.get(filename)


def _wife_path() -> Path:
    return Path(config.data_path) / config.wife_img_dir


def _wife_image_names() -> list[str]:
    """返回当前图库中的文件名，目录与非文件均不参与抽取、投票和排行。"""
    wife_path = _wife_path()
    try:
        return sorted(
            path.name
            for path in wife_path.iterdir()
            if path.is_file() and path.suffix.lower() in _IMAGE_SUFFIXES
        )
    except OSError:
        return []


def _wife_name(image_name: str) -> str:
    return os.path.splitext(image_name)[0]


def _safe_md(value: str) -> str:
    return value.replace("*", "＊").replace("_", "＿").replace("[", "［").replace("]", "］")


def _title_icon(name: str, fallback: str) -> str:
    """优先显示已同步的然然图标，图标缺失时保留 emoji 语义。"""
    return icon(name, 28) or fallback


def _rank_snapshot(image_name: str, valid_images: list[str]) -> tuple[tuple[int, int] | None, tuple[int, int] | None]:
    """查询图片在总榜和当月榜中的位置；榜单故障不影响正常抽取。"""
    try:
        return (
            _vote_repo.image_rank(image_name, "total", valid_images),
            _vote_repo.image_rank(image_name, "month", valid_images),
        )
    except Exception:
        logger.exception("查询抽老婆图片排名失败 image=%s", image_name)
        return None, None


def _rank_text(total_rank: tuple[int, int] | None, month_rank: tuple[int, int] | None) -> str:
    def render(label: str, rank: tuple[int, int] | None) -> str:
        if rank is None:
            return f"{label}：未上榜"
        return f"{label}：第 **{rank[0]}** 名 · **{rank[1]}** 票"

    return f"> 📊 {render('总榜', total_rank)} · {render('月榜', month_rank)}"


def _wife_keyboard(image_name: str):
    return build_keyboard([
        [wife_vote_button(image_name), BTN_FORTUNE, BTN_CHECKIN],
        [BTN_MENU, BTN_WIFE_AGAIN],
    ])


def _wife_content(
    image_name: str,
    category: str,
    submission_note: str,
    rank_text: str,
    md_image: str = "",
) -> str:
    body = (
        f"## {_title_icon('心动-开心.png', '💗')} 今日抽老婆\n"
        f"你今日抽取的老婆是 **{_safe_md(_wife_name(image_name))}**\n\n"
        f"分类：{_safe_md(category)}{submission_note}\n\n"
        f"{rank_text}\n\n"
    )
    if md_image:
        body += f"{md_image}\n\n"
    return body + f"添加喜欢的角色[【点击投稿】]({URL_SUBMIT})"


async def get_random_wife_md_message() -> Message | MessageSegment:
    """返回 QQ Markdown 消息（图片走 COS 公网 URL）+ 内联键盘。

    降级路径：
    - 目录不存在 / 为空 -> 文本提示
    - COS 上传失败 -> 回退到本地 Image(path=...) 发送
    """
    wife_path = _wife_path()
    if not wife_path.exists():
        return MessageSegment.text("老婆图库还没准备好，请先放入图片后再试~")

    image_names = _wife_image_names()
    if not image_names:
        return MessageSegment.text("老婆图库还是空的，请先放入图片后再试~")

    img_name = random.choice(image_names)
    img = wife_path / img_name
    wife_info = _get_wife_info(img_name)
    category = wife_info.get("category", "未分类") if wife_info else "未分类"

    # 构建投稿标识
    submission_note = ""
    if wife_info and wife_info.get("submitter"):
        submission_note = f"\n🏷️用户投稿 | 投稿人：{_safe_md(str(wife_info['submitter']))}"

    rank_text = _rank_text(*_rank_snapshot(img_name, image_names))
    keyboard = _wife_keyboard(img_name)

    bucket = get_bucket()
    url = await bucket.get_or_upload_file(img, prefix=KEY_PREFIX["wife"])

    if url is None:
        # COS 上传失败 -> 降级到本地图片
        content = _wife_content(img_name, category, submission_note, rank_text)
        return (
            MessageSegment.file_image(img)
            + MessageSegment.markdown(content)
            + MessageSegment.keyboard(keyboard)
        )

    # 成功：从 manifest 取宽高
    key = f"{KEY_PREFIX['wife']}/{img_name}"
    entry = manifest.get_static(key)
    width = entry.get("width", 0) if entry else 0
    height = entry.get("height", 0) if entry else 0

    md_img = bucket.build_md_image(url, width, height, _wife_name(img_name))

    content = _wife_content(img_name, category, submission_note, rank_text, md_img)

    return MessageSegment.markdown(content) + MessageSegment.keyboard(keyboard)


random_wife_matcher = on_command("抽老婆", priority=config.command_priority)


@random_wife_matcher.handle()
async def _():
    message = await get_random_wife_md_message()
    await random_wife_matcher.finish(message)


wife_rank_matcher = on_command(
    "老婆榜", aliases={"抽老婆榜"}, priority=config.command_priority
)


def _is_wife_vote_callback(event: Event) -> bool:
    """匹配抽老婆卡片上的投票回调，避免接管其他业务按钮。"""
    if not isinstance(event, InteractionCreateEvent):
        return False
    resolved = event.data.resolved
    return (
        (resolved.button_id or "").startswith("wife_vote_")
        and (resolved.button_data or "").startswith(WIFE_VOTE_CALLBACK_PREFIX)
    )


wife_vote_callback_matcher = on_notice(
    rule=Rule(_is_wife_vote_callback), priority=1
)


def _vote_result_content(
    status: str,
    image_name: str,
    result: dict,
    total_rank: tuple[int, int] | None,
    month_rank: tuple[int, int] | None,
) -> str:
    display_name = _safe_md(_wife_name(image_name))
    used = result["used"]
    limit = result["limit"]
    if status == "success":
        title = f"# {_title_icon('比心-爱你.png', '❤️')} 投票成功"
        detail = f"已投给 **{display_name}**！"
    elif status == "duplicate":
        title = f"# {_title_icon('得意-高兴-炫耀.png', '✨')} 已经投过啦"
        detail = f"你今天已经投过 **{display_name}**，可以把票留给其他老婆哦。"
    else:
        title = f"# {_title_icon('垂头丧气-失落.png', '🥲')} 今日票数已用完"
        detail = "明天再来支持喜欢的老婆吧～"
    return "\n\n".join([
        title,
        detail,
        f"> 今日已投：**{used}/{limit}** 票",
        _rank_text(total_rank, month_rank),
    ])


def _wife_vote_keyboard():
    return build_keyboard([
        [BTN_WIFE_RANK_TOTAL, BTN_WIFE_RANK_MONTH],
        [BTN_WIFE_AGAIN],
    ])


def _cast_wife_vote(voter_id: str, image_name: str) -> str:
    """校验目标、投票并构造结果；供指令与回调按钮共用。"""
    if not image_name or Path(image_name).name != image_name:
        return (
            f"# {_title_icon('绷不住了-难绷-无语.png', '⚠️')} 投票目标无效\n\n"
            "请从抽老婆卡片下方点击“投票”按钮。"
        )

    image_names = _wife_image_names()
    if image_name not in image_names:
        return (
            f"# {_title_icon('垂头丧气-失落.png', '🥲')} 图片已失效\n\n"
            "这张老婆已经不在图库中了，换一张投票吧。"
        )

    try:
        result = _vote_repo.cast_vote(
            voter_id, image_name, WIFE_VOTE_DAILY_LIMIT
        )
        total_rank, month_rank = _rank_snapshot(image_name, image_names)
    except Exception:
        logger.exception("抽老婆投票失败 image=%s voter=%s", image_name, voter_id)
        return (
            f"# {_title_icon('绷不住了-难绷-无语.png', '⚠️')} 投票失败\n\n"
            "服务器开小差了，请稍后再试。"
        )
    return _vote_result_content(
        result["status"], image_name, result, total_rank, month_rank
    )


def _rank_period(arg: str) -> RankPeriod | None:
    normalized = arg.strip()
    if normalized in ("", "总榜", "总"):
        return "total"
    if normalized in ("月榜", "月"):
        return "month"
    return None


async def _build_wife_rank_visuals(rows: list[dict]) -> str:
    """构造榜单 Top 3 的 64px 高缩略图，单张失败不影响文字榜单。"""
    bucket = get_bucket()
    wife_path = _wife_path()

    async def build_one(rank: int, row: dict) -> str:
        image_name = str(row["image_name"])
        image_path = wife_path / image_name
        try:
            url = await bucket.get_or_upload_file(
                image_path, prefix=KEY_PREFIX["wife"]
            )
        except Exception:
            logger.exception("上传老婆榜缩略图失败 image=%s", image_name)
            return ""
        if not url:
            return ""
        entry = manifest.get_static(f"{KEY_PREFIX['wife']}/{image_name}") or {}
        source_width = int(entry.get("width") or 0)
        source_height = int(entry.get("height") or 0)
        width = (
            max(1, round(source_width * WIFE_RANK_THUMBNAIL_HEIGHT / source_height))
            if source_width > 0 and source_height > 0
            else WIFE_RANK_THUMBNAIL_HEIGHT
        )
        image = bucket.build_md_image(
            url, width, WIFE_RANK_THUMBNAIL_HEIGHT, _wife_name(image_name)
        )
        medals = ("🥇", "🥈", "🥉")
        return (
            f"{medals[rank - 1]} {image} **{_safe_md(_wife_name(image_name))}**"
            f" · **{int(row['votes'])}** 票"
        )

    tasks = [
        build_one(rank, row) for rank, row in enumerate(rows[:3], start=1)
    ]
    visuals = await asyncio.gather(*tasks)
    return "\n\n".join(visual for visual in visuals if visual)


def _format_wife_board(
    period: RankPeriod, rows: list[dict], top_visuals: str = ""
) -> str:
    title = (
        f"## {_title_icon('嘉速心动.png', '👑')} 老婆总榜"
        if period == "total"
        else f"## {_title_icon('好想法.png', '🗓️')} 老婆月榜"
    )
    lines = [title, ""]
    if not rows:
        lines.append("暂时还没有投票，抽到喜欢的老婆就投给她吧～")
        return "\n".join(lines)
    if top_visuals:
        lines.extend([top_visuals, ""])
        text_rows = rows[3:]
        text_start = 4
    else:
        text_rows = rows
        text_start = 1
    for rank, row in enumerate(text_rows, start=text_start):
        image_name = str(row["image_name"])
        info = _get_wife_info(image_name) or {}
        category = _safe_md(str(info.get("category") or "未分类"))
        lines.append(
            f"{rank}. **{_safe_md(_wife_name(image_name))}** · {category} · **{int(row['votes'])}** 票"
        )
    return "\n".join(lines)


@wife_vote_callback_matcher.handle()
async def _(bot: Bot, event: InteractionCreateEvent):
    """确认 QQ 回调后，直接完成投票，不要求用户再发送指令。"""
    try:
        await bot.put_interaction(interaction_id=event.id, code=0)
    except Exception:
        logger.exception("确认老婆投票回调失败 id=%s", event.id)
        await wife_vote_callback_matcher.send("❌ 投票回调确认失败，请稍后重试。")
        return

    callback_data = event.data.resolved.button_data or ""
    image_name = callback_data.removeprefix(WIFE_VOTE_CALLBACK_PREFIX)
    content = _cast_wife_vote(event.get_user_id(), image_name)
    await wife_vote_callback_matcher.send(
        MessageSegment.markdown(content)
        + MessageSegment.keyboard(_wife_vote_keyboard())
    )


@wife_rank_matcher.handle()
async def _(args: Message = CommandArg()):
    period = _rank_period(args.extract_plain_text())
    if period is None:
        await wife_rank_matcher.finish("用法：/老婆榜 [总榜|月榜]")
        return
    rows = _vote_repo.top_images(period, _wife_image_names())
    top_visuals = await _build_wife_rank_visuals(rows)
    keyboard = build_keyboard([
        [BTN_WIFE_RANK_TOTAL, BTN_WIFE_RANK_MONTH],
        [BTN_WIFE_AGAIN],
    ])
    await wife_rank_matcher.finish(
        MessageSegment.markdown(_format_wife_board(period, rows, top_visuals))
        + MessageSegment.keyboard(keyboard)
    )
