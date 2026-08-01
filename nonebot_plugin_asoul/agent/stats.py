"""
@Author: star_482
@Date: 2026/8/1
@File: stats
@Description: Agent 模块统计 - 按天记录对话次数与 token 消耗，提供接口给 admin_stats 展示。
统计写入 data/asoul/agent/stats.json，按天聚合。
"""
import datetime
import json
import os
from pathlib import Path
from threading import Lock

from nonebot.log import logger

from ..config import config

_lock = Lock()


def _stats_path() -> Path:
    return Path(config.data_path) / "agent" / "stats.json"


def _load() -> dict:
    p = _stats_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"读取 agent stats 失败：{e}")
        return {}


def _save(data: dict) -> None:
    p = _stats_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)


def record_usage(
    *,
    calls: int = 0,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: int = 0,
) -> None:
    """记录一次对话的调用次数与 token 消耗（按天聚合，线程安全）。"""
    today = datetime.date.today().isoformat()
    with _lock:
        data = _load()
        day = data.setdefault(
            today,
            {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        )
        day["calls"] += calls
        day["prompt_tokens"] += prompt_tokens
        day["completion_tokens"] += completion_tokens
        day["total_tokens"] += total_tokens
        _save(data)


def get_summary() -> dict:
    """返回全部按天统计 {date: {calls, prompt_tokens, completion_tokens, total_tokens}}，供 admin_stats 展示。"""
    return _load()
