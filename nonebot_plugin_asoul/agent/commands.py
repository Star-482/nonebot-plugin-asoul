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
from pathlib import Path

from nonebot import get_driver
from nonebot.adapters import Event
from nonebot.adapters.qq import MessageSegment
from nonebot.internal.matcher import Matcher
from nonebot.log import logger
from nonebot.plugin.on import on_message
from nonebot.rule import to_me

from ..config import config
from .prompt import build_system_prompt
from .client import run_agent
from .history import get_history_for_request, append_turn, maybe_compress
from .stats import record_usage
from .tools import ToolContext

async def _not_bot(event: Event) -> bool:
    """过滤 bot 发送的消息（避免回复其他 bot / 自我循环）。"""
    author = getattr(event, "author", None)
    return not getattr(author, "bot", False)


# 命令前缀（NoneBot command_start，默认 "/"）-- 命令由命令 matcher 处理，不进 agent
_command_starts: tuple[str, ...] = tuple(get_driver().config.command_start)


async def _not_command(event: Event) -> bool:
    """排除命令消息（以 command_start 开头），命令在 rule 阶段就不匹配 agent matcher。"""
    text = event.get_message().extract_plain_text().lstrip()
    return not text.startswith(_command_starts)


# 兜底 matcher：priority=command_priority+50，排在所有命令之后
# rule=to_me() & _not_bot & _not_command：只响应私聊/群@、非 bot、非命令的消息
agent_matcher = on_message(
    priority=config.command_priority + 50,
    rule=to_me() & _not_bot & _not_command,
)

# 每用户调用 CD（防刷）
_cd_last: dict[str, float] = {}

# 每用户整轮对话锁：串行化 run_agent 全过程（读历史->生成->发送->写回），
# 避免同一用户上一轮未完成时下一轮重叠导致上下文陈旧 / 时序错乱。
# check-then-create 之间无 await，asyncio 单线程下原子，无需额外 guard。
_user_locks: dict[str, asyncio.Lock] = {}


def _get_user_lock(user_id: str) -> asyncio.Lock:
    if user_id not in _user_locks:
        _user_locks[user_id] = asyncio.Lock()
    return _user_locks[user_id]


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
    if not text:
        return  # 空消息，不喂 LLM

    user_id = event.get_user_id()
    # per-user CD
    now = time.time()
    if now - _cd_last.get(user_id, 0.0) < config.agent_user_cd:
        return
    _cd_last[user_id] = now

    group_id = getattr(event, "group_openid", None)
    ctx = ToolContext(user_id=user_id, group_id=str(group_id) if group_id else None)

    # 每用户整轮锁：串行化 读历史->run_agent->发送->写回，避免重叠导致上下文陈旧与时序错乱
    async with _get_user_lock(user_id):
        # 组装 messages：system + [历史摘要?] + 历史 + 当前 user + 本次条数指令
        system_prompt = build_system_prompt()
        history_copy = await get_history_for_request(user_id)
        n = _sample_reply_count()
        count_instruction = {"role": "system", "content": f"【本次输出要求】本次 replies 数组必须恰好包含 {n} 条消息，长度严格等于 {n}，一条不多一条不少。"}
        messages = (
            [{"role": "system", "content": system_prompt}]
            + history_copy
            + [
                {"role": "user", "content": text},
                count_instruction,
            ]
        )

        try:
            replies, attachments, turn_msgs, usage = await run_agent(messages, ctx)
        except Exception:
            logger.exception("agent run_agent 失败")
            await matcher.send(MessageSegment.text("然然现在有点晕，稍后再试~"))
            return  # 锁自动释放；跳过压缩/统计/存档

        # 容错：LLM 可能不严格输出 n 条，取前 n 条（不足则全发）
        replies = replies[:n] if replies else []
        if not replies:
            replies = ["……"]

        # 逐条发送文本，附件单独各发一条（避免 text+markdown 混合时文本段被吞）
        for line in replies:
            await matcher.send(MessageSegment.text(line))
        for att in attachments:
            await matcher.send(att)

        # 存历史：user + 条数指令 + 本轮新增（含工具调用过程），工具结果可跨轮记忆
        # 条数指令也存入历史，保证下一轮请求前缀与本轮一致，提高 prompt cache 命中率
        await append_turn(user_id, [{"role": "user", "content": text}, count_instruction] + turn_msgs)
    # 锁释放：压缩/统计/存档放锁外，不阻塞下一轮（压缩自带前端不变校验，并发安全）
    # 达阈值则压缩：旧消息入 compressed 存档 + 滚动摘要，留尾部 keep 条
    await maybe_compress(user_id)
    # 统计 token 用量 + 对话存档（仅存档，不读回）
    record_usage(calls=1, **usage)
    _archive_dialogue(user_id, group_id, text, replies, turn_msgs)
