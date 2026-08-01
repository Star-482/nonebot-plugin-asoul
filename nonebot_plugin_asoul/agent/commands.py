"""
@Author: star_482
@Date: 2026/7/31
@File: commands
@Description: Agent 兜底 matcher--命令未命中时落到 LLM 拟人聊天。
priority 远高于命令(15)，命令命中并 finish 后不会触发；只有未命中任何命令
的消息才落到这里。群聊即"@bot 闲聊"，私聊同样触发。
"""
import asyncio
import datetime
import json
import random
import time
from collections import OrderedDict
from pathlib import Path

from nonebot.adapters import Event
from nonebot.adapters.qq import MessageSegment
from nonebot.internal.matcher import Matcher
from nonebot.log import logger
from nonebot.plugin.on import on_message
from nonebot.rule import to_me

from ..config import config
from .prompt import build_system_prompt
from .client import run_agent
from .stats import record_usage
from .tools import ToolContext

async def _not_bot(event: Event) -> bool:
    """过滤 bot 发送的消息（避免回复其他 bot / 自我循环）。"""
    author = getattr(event, "author", None)
    return not getattr(author, "bot", False)


# 兜底 matcher：priority=command_priority+50，排在所有命令之后
# rule=to_me() & _not_bot：只响应私聊/群@，且不响应 bot 发送的消息
agent_matcher = on_message(priority=config.command_priority + 50, rule=to_me() & _not_bot)

# 每用户对话历史 LRU（进程内，bot 重启清空）
_HISTORY: OrderedDict[str, list[dict]] = OrderedDict()
_HISTORY_LOCK = asyncio.Lock()
_HISTORY_MAX_USERS = 200

# 每用户调用 CD（防刷）
_cd_last: dict[str, float] = {}


def _get_history(user_id: str) -> list[dict]:
    """取（或新建）用户历史，LRU move_to_end。调用方需持 _HISTORY_LOCK。"""
    if user_id in _HISTORY:
        _HISTORY.move_to_end(user_id)
        return _HISTORY[user_id]
    hist: list[dict] = []
    _HISTORY[user_id] = hist
    while len(_HISTORY) > _HISTORY_MAX_USERS:
        _HISTORY.popitem(last=False)
    return hist


async def _append_history(user_id: str, new_msgs: list[dict]) -> None:
    """把本轮新增消息（user + assistant/tool 序列）追加到历史，并安全裁剪。

    裁剪只在 user 消息边界进行，避免拆散 assistant(tool_calls) 与其 tool 结果，
    否则下一轮请求会因 tool_calls 缺少对应 tool 结果而报错。
    """
    async with _HISTORY_LOCK:
        hist = _get_history(user_id)
        hist.extend(new_msgs)
        limit = config.agent_history_limit
        while len(hist) > limit:
            # 找下一个 user 边界（跳过最旧一轮的开头 user），整轮删除
            next_user = None
            for i in range(1, len(hist)):
                if hist[i].get("role") == "user":
                    next_user = i
                    break
            if next_user is None:
                break  # 只剩一轮，不再裁剪
            del hist[:next_user]


def _sample_reply_count() -> int:
    """按 40/30/20/10 概率采样回复条数（1/2/3/4 条）。"""
    r = random.random()
    if r < 0.4:
        return 1
    if r < 0.7:
        return 2
    if r < 0.9:
        return 3
    return 4


def _archive_dialogue(user_id: str, group_id, user_text: str, replies: list[str], turn_msgs: list[dict]) -> None:
    """把对话追加到 JSONL 存档文件（仅存档，不读回，不影响现有历史逻辑）。"""
    tools = []
    for m in turn_msgs:
        if m.get("role") == "assistant" and m.get("tool_calls"):
            for tc in m["tool_calls"]:
                tools.append((tc.get("function") or {}).get("name", ""))
    record = {
        "ts": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "user_id": user_id,
        "group_id": str(group_id) if group_id else "dm",
        "user_text": user_text,
        "replies": replies,
        "tools": tools,
    }
    path = Path(config.data_path) / "agent" / "dialogue_archive.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        logger.exception("写入 agent 对话存档失败")


@agent_matcher.handle()
async def _(event: Event, matcher: Matcher):
    if not config.agent_enabled:
        return  # 未启用，静默（行为同现状）

    text = event.get_message().extract_plain_text().strip()
    if not text or text.startswith("/"):
        return  # 空消息或未匹配的命令，不喂 LLM

    user_id = event.get_user_id()
    # per-user CD
    now = time.time()
    if now - _cd_last.get(user_id, 0.0) < config.agent_user_cd:
        return
    _cd_last[user_id] = now

    group_id = getattr(event, "group_openid", None)
    ctx = ToolContext(user_id=user_id, group_id=str(group_id) if group_id else None)

    # 组装 messages：system + 历史 + 当前 user + 本次条数指令
    system_prompt = build_system_prompt()
    async with _HISTORY_LOCK:
        history_copy = list(_get_history(user_id))
    n = _sample_reply_count()
    messages = (
        [{"role": "system", "content": system_prompt}]
        + history_copy
        + [
            {"role": "user", "content": text},
            {"role": "system", "content": f"【本次输出要求】本次 replies 数组必须恰好包含 {n} 条消息，长度严格等于 {n}，一条不多一条不少。"},
        ]
    )

    try:
        replies, attachments, turn_msgs, usage = await run_agent(messages, ctx)
    except Exception:
        logger.exception("agent run_agent 失败")
        await matcher.send(MessageSegment.text("然然现在有点晕，稍后再试~"))
        return

    # 容错：LLM 可能不严格输出 n 条，取前 n 条（不足则全发）
    replies = replies[:n] if replies else []
    if not replies:
        replies = ["……"]

    # 逐条发送文本，附件单独各发一条（避免 text+markdown 混合时文本段被吞）
    for line in replies:
        await matcher.send(MessageSegment.text(line))
    for att in attachments:
        await matcher.send(att)

    # 存历史：user 消息 + 本轮新增（含工具调用过程），工具结果可跨轮记忆
    await _append_history(user_id, [{"role": "user", "content": text}] + turn_msgs)
    # 统计 token 用量 + 对话存档（仅存档，不读回）
    record_usage(calls=1, **usage)
    _archive_dialogue(user_id, group_id, text, replies, turn_msgs)
