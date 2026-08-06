"""
@Author: star_482
@Date: 2026/8/3
@File: history
@Description: Agent 每用户历史持久化 + 摘要压缩。
状态文件 data/asoul/agent/history/{user_id}.json，三段式：
  summary    - 已压缩消息的滚动摘要（str|null，进 LLM 请求）
  compressed - 已压缩的原始消息存档（不进 LLM，无上限累积，摘要的来源）
  messages   - 当前上下文（进 LLM 请求）
内存 LRU 缓存磁盘文件；重启或 LRU 驱逐后未命中时从磁盘加载，历史可恢复。
压缩触发：len(messages) >= agent_history_limit 时，在 user 边界切 ~limit-keep 条
压缩进 compressed + 滚动摘要，留尾部 ~keep 条。LLM 摘要调用在锁外执行不阻塞其他用户。
"""
import asyncio
import json
import os
from collections import OrderedDict
from pathlib import Path

from nonebot.log import logger

from ..config import config
from .client import summarize_history

# 内存 LRU：user_id -> {"summary": str|None, "compressed": list[dict], "messages": list[dict]}
_HISTORY: OrderedDict[str, dict] = OrderedDict()
_LOCK = asyncio.Lock()
_MAX_USERS = 200

# 硬上限兜底倍数：压缩反复失败时退回 user 边界硬裁剪，防止 messages 无限增长
_HARD_CAP_MULT = 2


def _state_path(user_id: str) -> Path:
    return Path(config.data_path) / "agent" / "history" / f"{user_id}.json"


def _load_state(user_id: str) -> dict:
    """从磁盘加载用户状态；不存在或损坏返回空状态。"""
    p = _state_path(user_id)
    if not p.exists():
        return {"summary": None, "compressed": [], "messages": []}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return {
            "summary": data.get("summary"),
            "compressed": data.get("compressed") or [],
            "messages": data.get("messages") or [],
        }
    except Exception as e:
        logger.warning(f"读取 agent 历史失败 {p}：{e}，重置为空")
        return {"summary": None, "compressed": [], "messages": []}


def _save_state(user_id: str, state: dict) -> None:
    """原子写入用户状态（.tmp + os.replace）。"""
    p = _state_path(user_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)
        os.replace(tmp, p)
    except Exception:
        logger.exception(f"写入 agent 历史失败 {p}")
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass


def _get_state(user_id: str) -> dict:
    """取（或新建）用户状态，LRU move_to_end。调用方需持 _LOCK。"""
    if user_id in _HISTORY:
        _HISTORY.move_to_end(user_id)
        return _HISTORY[user_id]
    state = _load_state(user_id)
    _HISTORY[user_id] = state
    while len(_HISTORY) > _MAX_USERS:
        _HISTORY.popitem(last=False)
    return state


def _find_user_boundary(messages: list[dict], max_idx: int) -> int | None:
    """在 [1, max_idx] 范围内找最大的 user 消息索引（user 边界），保证
    messages[:idx] 是完整轮次、messages[idx:] 以 user 开头，不拆散
    assistant(tool_calls) 与其 tool 结果。找不到返回 None。
    """
    upper = min(max_idx, len(messages) - 1)
    for i in range(upper, 0, -1):
        if messages[i].get("role") == "user":
            return i
    return None


def _trim_to_boundary(messages: list[dict], limit: int) -> None:
    """硬裁剪：在 user 边界删除最旧的整轮，直到 len <= limit。就地修改。"""
    while len(messages) > limit:
        idx = _find_user_boundary(messages, len(messages) - 1)
        if idx is None:
            break
        del messages[:idx]


async def get_history_for_request(user_id: str) -> list[dict]:
    """组装进 LLM 请求的历史部分：[summary_sys?] + messages。
    调用方再前置 system_prompt、后置当前 user + 条数指令。
    """
    async with _LOCK:
        state = _get_state(user_id)
        out: list[dict] = []
        if state["summary"]:
            out.append({"role": "system", "content": f"【历史对话摘要】\n{state['summary']}"})
        out.extend(state["messages"])
        return out


async def append_turn(user_id: str, new_msgs: list[dict]) -> None:
    """追加本轮新增消息并落盘。new_msgs = [user, count_instruction] + turn_msgs。
    附带硬上限兜底裁剪（压缩失败时防止 messages 无限增长）。
    """
    async with _LOCK:
        state = _get_state(user_id)
        state["messages"].extend(new_msgs)
        _trim_to_boundary(state["messages"], config.agent_history_limit * _HARD_CAP_MULT)
        _save_state(user_id, state)


async def maybe_compress(user_id: str) -> None:
    """达阈值则压缩：把 messages 前段并入 compressed + 滚动摘要，留尾部 keep 条。
    LLM 摘要调用在锁外执行，不阻塞其他用户；应用时做前端不变校验防并发数据错乱。
    """
    limit = config.agent_history_limit
    keep = config.agent_summary_keep
    compress_target = max(1, limit - keep)  # 压缩 ~limit-keep 条

    # 快照（持锁）
    async with _LOCK:
        state = _get_state(user_id)
        msgs = state["messages"]
        if len(msgs) < limit:
            return
        split = _find_user_boundary(msgs, compress_target)
        if split is None:
            return  # 找不到 user 边界，等下一轮
        to_compress = list(msgs[:split])
        old_summary = state["summary"]

    # 锁外：LLM 摘要（可能耗时，不阻塞其他用户）
    try:
        new_summary = await summarize_history(old_summary, to_compress)
    except Exception:
        logger.exception(f"agent 历史摘要失败 user={user_id}，跳过本次压缩")
        return

    # 应用（持锁 + 前端不变校验）
    async with _LOCK:
        state = _get_state(user_id)
        msgs = state["messages"]
        if msgs[: len(to_compress)] == to_compress:
            # 前端未变（新轮次追加在尾部）-> 安全应用
            state["compressed"].extend(to_compress)
            state["summary"] = new_summary
            state["messages"] = msgs[len(to_compress) :]
            _save_state(user_id, state)
        else:
            # 前端已变（并发压缩或硬裁剪动过）-> 跳过，下一轮再来
            logger.warning(f"agent 压缩期间历史前端变化 user={user_id}，跳过本次应用")
