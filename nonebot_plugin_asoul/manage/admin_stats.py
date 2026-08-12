"""
@Author: star_482
@Date: 2026/5/13
@File: admin_stats
@Description:
"""
import json
import os
from datetime import datetime
from typing import Optional

from nonebot.adapters import Event
from nonebot.adapters.qq.event import MessageEvent
from nonebot.consts import CMD_ARG_KEY, CMD_KEY, PREFIX_KEY, RAW_CMD_KEY
from nonebot.matcher import Matcher
from nonebot.message import run_postprocessor, run_preprocessor
from nonebot.permission import SUPERUSER
from nonebot.plugin.on import on_command
from nonebot.typing import T_State

from ..config import config

STATS_STATE_KEY = "_asoul_command_stats_record"


def _stats_dir() -> str:
    return os.path.join(config.data_path, "stats")


def _detail_path() -> str:
    return os.path.join(_stats_dir(), "usage_detail.jsonl")


def _summary_path() -> str:
    return os.path.join(_stats_dir(), "usage_summary.json")


def _empty_summary() -> dict:
    return {
        "total": 0,
        "by_command": {},
        "by_user": {},
        "by_scene": {},
        "by_date": {},
        "last_updated": "",
    }


def _load_summary() -> dict:
    path = _summary_path()
    if not os.path.exists(path):
        return _empty_summary()
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_summary(summary: dict):
    os.makedirs(_stats_dir(), exist_ok=True)
    with open(_summary_path(), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=4)


def _append_detail(record: dict):
    os.makedirs(_stats_dir(), exist_ok=True)
    with open(_detail_path(), "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _increase(data: dict, key: str):
    data[key] = data.get(key, 0) + 1


def save_usage_record(record: dict):
    summary = _load_summary()
    summary["total"] = summary.get("total", 0) + 1
    _increase(summary.setdefault("by_command", {}), record["command"])
    _increase(summary.setdefault("by_user", {}), record["user_id"])
    _increase(summary.setdefault("by_scene", {}), record["scene_id"])
    _increase(summary.setdefault("by_date", {}), record["date"])
    summary["last_updated"] = record["time"]
    _save_summary(summary)
    _append_detail(record)


def read_recent_details(limit: int = 10) -> list[dict]:
    path = _detail_path()
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    records = []
    for line in lines[-limit:]:
        if line.strip():
            records.append(json.loads(line))
    return records


def _read_details_for_date(date_str: str) -> list[dict]:
    """读取某一天的全部使用明细记录（按 date 字段过滤）。"""
    path = _detail_path()
    if not os.path.exists(path):
        return []
    records = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("date") == date_str:
                records.append(record)
    return records


def _top_items(data: dict, limit: int = 10) -> list[tuple[str, int]]:
    return sorted(data.items(), key=lambda item: item[1], reverse=True)[:limit]


def _format_top(title: str, data: dict) -> str:
    items = _top_items(data)
    if not items:
        return f"{title}\n暂无数据"
    lines = [title]
    lines.extend(f"{index}. {key}: {value}" for index, (key, value) in enumerate(items, 1))
    return "\n".join(lines)


def _scene_info(event: Event) -> tuple[str, str]:
    if group_openid := getattr(event, "group_openid", ""):
        return "group", group_openid
    if guild_id := getattr(event, "guild_id", ""):
        channel_id = getattr(event, "channel_id", "")
        return "guild", f"{guild_id}/{channel_id}" if channel_id else guild_id
    session_id = event.get_session_id()
    if session_id.startswith("friend_"):
        return "friend", session_id
    return "unknown", session_id


def _is_asoul_module(module_name: str) -> bool:
    return "nonebot_plugin_asoul" in module_name.split(".")


def _build_record(event: Event, matcher: Matcher, state: T_State) -> Optional[dict]:
    if not isinstance(event, MessageEvent):
        return None  # 非消息事件（notice/meta）不计入命令统计
    module_name = matcher.module_name or ""
    if not _is_asoul_module(module_name):
        return None
    # agent 兜底 matcher 与 relationships 关系兜底 matcher 不是命令，不计入命令统计
    if module_name.endswith(".agent.commands") or module_name.endswith(".manage.relationships"):
        return None

    prefix = state.get(PREFIX_KEY) or {}
    command = prefix.get(CMD_KEY)
    raw_command = prefix.get(RAW_CMD_KEY)
    command_arg = prefix.get(CMD_ARG_KEY)
    if not command or not raw_command:
        msg_text = event.get_message().extract_plain_text().strip()
        if not msg_text:
            return None
        raw_command = msg_text
        parts = msg_text.split()
        cmd_name = parts[0].lstrip("/") if parts else msg_text.lstrip("/")
        command = [cmd_name]
        command_arg = None

    now = datetime.now().astimezone()
    arg_text = command_arg.extract_plain_text() if command_arg else ""
    scene_type, scene_id = _scene_info(event)
    return {
        "time": now.isoformat(timespec="seconds"),
        "date": now.strftime("%Y-%m-%d"),
        "command": " ".join(command),
        "raw_command": raw_command,
        "arg_text": arg_text,
        "user_id": event.get_user_id(),
        "session_id": event.get_session_id(),
        "scene_type": scene_type,
        "scene_id": scene_id,
        "matcher_module": module_name,
        "status": "success",
        "exception": "",
    }


@run_preprocessor
async def command_stats_preprocessor(event: Event, matcher: Matcher, state: T_State):
    record = _build_record(event, matcher, state)
    if record:
        state[STATS_STATE_KEY] = record


@run_postprocessor
async def command_stats_postprocessor(matcher: Matcher, exception: Optional[Exception] = None):
    record = matcher.state.get(STATS_STATE_KEY)
    if not record:
        return
    if exception:
        record["status"] = "failed"
        record["exception"] = type(exception).__name__
    save_usage_record(record)


stats_overview = on_command("统计总览", priority=config.command_priority, permission=SUPERUSER)
stats_rank = on_command("统计排行", priority=config.command_priority, permission=SUPERUSER)
stats_detail = on_command("统计明细", priority=config.command_priority, permission=SUPERUSER)


@stats_overview.handle()
async def _():
    summary = _load_summary()
    today = datetime.now().astimezone().strftime("%Y-%m-%d")
    today_by_command: dict[str, int] = {}
    today_users: set[str] = set()
    for record in _read_details_for_date(today):
        command = record.get("command", "未知")
        today_by_command[command] = today_by_command.get(command, 0) + 1
        if user_id := record.get("user_id"):
            today_users.add(user_id)
    if today_by_command:
        command_lines = "\n".join(
            f"  {cmd}: {count}"
            for cmd, count in sorted(
                today_by_command.items(), key=lambda item: item[1], reverse=True
            )
        )
    else:
        command_lines = "  暂无数据"
    # agent 统计（对话次数 + token 消耗）
    try:
        from ..agent.stats import get_summary as _agent_summary
        agent_stats = _agent_summary()
        today_agent = agent_stats.get(today, {})
        agent_total_calls = sum(d.get("calls", 0) for d in agent_stats.values())
        agent_total_tokens = sum(d.get("total_tokens", 0) for d in agent_stats.values())
        agent_lines = (
            f"今日 agent 对话：{today_agent.get('calls', 0)} 次，"
            f"消耗 {today_agent.get('total_tokens', 0)} tokens\n"
            f"agent 累计对话：{agent_total_calls} 次，消耗 {agent_total_tokens} tokens"
        )
    except Exception:
        agent_lines = "agent 统计不可用"
    text = (
        "命令统计总览\n"
        f"总调用次数：{summary.get('total', 0)}\n"
        f"今日调用次数：{summary.get('by_date', {}).get(today, 0)}\n"
        f"今日使用人数：{len(today_users)}\n"
        f"用户数量：{len(summary.get('by_user', {}))}\n"
        f"今日各命令使用次数：\n{command_lines}\n"
        f"{agent_lines}\n"
        f"最近更新时间：{summary.get('last_updated') or '暂无'}"
    )
    await stats_overview.finish(text)


@stats_rank.handle()
async def _():
    summary = _load_summary()
    text = "\n\n".join(
        [
            _format_top("命令排行 Top 10", summary.get("by_command", {})),
            _format_top("用户排行 Top 10", summary.get("by_user", {})),
            _format_top("场景排行 Top 10", summary.get("by_scene", {})),
        ]
    )
    await stats_rank.finish(text)


@stats_detail.handle()
async def _():
    records = read_recent_details()
    if not records:
        await stats_detail.finish("暂无统计明细")
    lines = ["最近 10 条命令使用记录"]
    for record in records:
        lines.append(
            f"{record['time']} | {record['raw_command']} | "
            f"{record['user_id']} | {record['scene_type']}:{record['scene_id']} | "
            f"{record['status']}"
        )
    await stats_detail.finish("\n".join(lines))
