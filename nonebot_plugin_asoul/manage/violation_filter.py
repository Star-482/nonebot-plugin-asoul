"""
@Author: star_482
@Date: 2026/8/8
@File: violation_filter
@Description: 违禁词拦截 -- 入站消息含违禁词计数 + 黑名单。违禁累计达阈值自动拉黑；
SUPERUSER 可手动添加/移除黑名单。词库为静态文件 data/asoul/violation_words.json。
"""
import json
import os
from datetime import datetime

from nonebot.adapters import Event
from nonebot.adapters.qq.event import MessageEvent
from nonebot.exception import IgnoredException
from nonebot.log import logger
from nonebot.matcher import Matcher
from nonebot.message import run_preprocessor
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from nonebot.plugin.on import on_command
from nonebot.typing import T_State

from ..config import config
from ..markdown import get_blacklist_md
from ..utils import save_json

# ── 词库（静态文件，导入时加载）──
_WORDS_FILE = "violation_words.json"


def _load_words() -> list[str]:
    """读取违禁词库；文件不存在则空列表（不崩插件）。"""
    path = os.path.join(config.data_path, _WORDS_FILE)
    if not os.path.exists(path):
        logger.warning(f"违禁词库 {path} 不存在，违禁词拦截按空词库运行")
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return [str(w) for w in data if w]
    except (json.JSONDecodeError, OSError) as e:
        logger.error(f"读取违禁词库失败: {e}")
    return []


_words: list[str] = _load_words()


# ── 计数 + 黑名单 ──
_STATS_FILE = "violation_stats.json"
_counts: dict[str, int] = {}
_blacklist: list[str] = []
_records: list[dict] = []


def _load_stats() -> None:
    global _counts, _blacklist, _records
    path = os.path.join(config.data_path, _STATS_FILE)
    if not os.path.exists(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.error(f"读取违禁统计失败: {e}")
        return
    if isinstance(data, dict):
        counts = data.get("counts") or {}
        _counts = {str(k): int(v) for k, v in counts.items()} if isinstance(counts, dict) else {}
        bl = data.get("blacklist") or []
        _blacklist = [str(u) for u in bl] if isinstance(bl, list) else []
        recs = data.get("records") or []
        _records = recs if isinstance(recs, list) else []


def _save_stats() -> None:
    save_json(_STATS_FILE, {"counts": _counts, "blacklist": _blacklist, "records": _records})


def _append_record(user_id: str, word: str, text: str, count: int, blacklisted: bool) -> None:
    """追加一条违禁记录（时间/用户/命中词/原始命令/第几次/是否拉黑），随 stats 一起落盘。"""
    _records.append({
        "time": datetime.now().astimezone().isoformat(timespec="seconds"),
        "user_id": user_id,
        "word": word,
        "raw_command": text[:100],
        "count": count,
        "blacklisted": blacklisted,
    })
    _save_stats()


_load_stats()


def _is_asoul_module(module_name: str) -> bool:
    return "nonebot_plugin_asoul" in module_name.split(".")


@run_preprocessor
async def violation_preprocessor(event: Event, matcher: Matcher, state: T_State):
    if not config.violation_enabled:
        return
    module_name = matcher.module_name or ""
    if not _is_asoul_module(module_name):
        return
    if not isinstance(event, MessageEvent):
        return  # 非消息事件（notice/meta）不检查违禁词
    user_id = event.get_user_id()
    # 黑名单：只禁 agent 聊天，其他功能正常使用
    if user_id in _blacklist:
        if module_name.endswith(".agent.commands"):
            await matcher.send(get_blacklist_md("你已被关进小黑屋，无法与然然聊天。"))
            raise IgnoredException("violation_filter")
        return  # 黑名单用户用其他命令放行（跳过违禁词检查）
    # 违禁词检查（非黑名单用户）
    text = event.get_message().extract_plain_text()
    for word in _words:
        if word and word in text:
            count = _counts.get(user_id, 0) + 1
            _counts[user_id] = count
            blacklisted = count >= config.violation_threshold
            if blacklisted and user_id not in _blacklist:
                _blacklist.append(user_id)
            _append_record(user_id, word, text, count, blacklisted)
            if blacklisted:
                await matcher.send(get_blacklist_md(f"⚠️检测到违禁词，累计 {count} 次，你已被关进小黑屋。"))
                raise IgnoredException("violation_filter")
            await matcher.send(
                f"⚠️检测到违禁词，这是第 {count} 次，累计 {config.violation_threshold} 次将被关进小黑屋。"
            )
            raise IgnoredException("violation_filter")


# ── SUPERUSER 命令 ──

blacklist_add = on_command("拉黑", permission=SUPERUSER, priority=config.command_priority)
blacklist_remove = on_command("解除拉黑", permission=SUPERUSER, priority=config.command_priority)


@blacklist_add.handle()
async def _(arg=CommandArg()):
    target = arg.extract_plain_text().strip()
    if not target:
        await blacklist_add.finish("用法：/拉黑 <user_id>")
    if target in _blacklist:
        await blacklist_add.finish(f"{target} 已在黑名单中")
    _blacklist.append(target)
    _save_stats()
    await blacklist_add.finish(f"已拉黑 {target}")


@blacklist_remove.handle()
async def _(arg=CommandArg()):
    target = arg.extract_plain_text().strip()
    if not target:
        await blacklist_remove.finish("用法：/解除拉黑 <user_id>")
    if target not in _blacklist:
        await blacklist_remove.finish(f"{target} 不在黑名单中")
    _blacklist.remove(target)
    _save_stats()
    await blacklist_remove.finish(f"已解除拉黑 {target}")
