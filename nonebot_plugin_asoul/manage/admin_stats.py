"""
@Author: star_482
@Date: 2026/5/13
@File: admin_stats
@Description: 命令使用统计。run_preprocessor/postprocessor 拦截 asoul 模块命令，
写入 SQLite command_stats 表（替代原 usage_detail.jsonl/usage_summary.json）；
SUPERUSER 命令 统计总览/统计排行/统计明细 走 SQL 聚合查询。
"""
from datetime import datetime
from typing import Optional

from nonebot.adapters import Event
from nonebot.adapters.qq.event import MessageEvent
from nonebot.consts import CMD_KEY, PREFIX_KEY
from nonebot.matcher import Matcher
from nonebot.message import run_postprocessor, run_preprocessor
from nonebot.permission import SUPERUSER
from nonebot.plugin.on import on_command
from nonebot.typing import T_State

from ..config import config
from ..database.repositories import CommandStatsRepo

STATS_STATE_KEY = "_asoul_command_stats_record"

_stats_repo = CommandStatsRepo()


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
    if not command:
        msg_text = event.get_message().extract_plain_text().strip()
        if not msg_text:
            return None
        parts = msg_text.split()
        command = [parts[0].lstrip("/")] if parts else [msg_text.lstrip("/")]

    _scene_type, scene_id = _scene_info(event)
    return {
        "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
        "command": " ".join(command),
        "user_id": event.get_user_id(),
        "scene_id": scene_id,
        "status": "success",
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
    _stats_repo.insert(record)


stats_overview = on_command("统计总览", priority=config.command_priority, permission=SUPERUSER)
stats_rank = on_command("统计排行", priority=config.command_priority, permission=SUPERUSER)
stats_detail = on_command("统计明细", priority=config.command_priority, permission=SUPERUSER)


def _format_top(title: str, items: list[tuple[str, int]]) -> str:
    if not items:
        return f"{title}\n暂无数据"
    lines = [title]
    lines.extend(f"{index}. {key}: {value}" for index, (key, value) in enumerate(items, 1))
    return "\n".join(lines)


@stats_overview.handle()
async def _():
    today = datetime.now().astimezone().strftime("%Y-%m-%d")
    total = _stats_repo.count_total()
    today_count = _stats_repo.count_by_date(today)
    today_users = _stats_repo.count_distinct_users(today)
    user_count = _stats_repo.count_distinct_users()
    today_by_command = _stats_repo.count_by_command(today)
    if today_by_command:
        command_lines = "\n".join(
            f"  {cmd}: {count}" for cmd, count in today_by_command
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
        f"总调用次数：{total}\n"
        f"今日调用次数：{today_count}\n"
        f"今日使用人数：{today_users}\n"
        f"用户数量：{user_count}\n"
        f"今日各命令使用次数：\n{command_lines}\n"
        f"{agent_lines}"
    )
    await stats_overview.finish(text)


@stats_rank.handle()
async def _():
    text = "\n\n".join(
        [
            _format_top("命令排行 Top 10", _stats_repo.top("command", 10)),
            _format_top("用户排行 Top 10", _stats_repo.top("user_id", 10)),
            _format_top("场景排行 Top 10", _stats_repo.top("scene_id", 10)),
        ]
    )
    await stats_rank.finish(text)


@stats_detail.handle()
async def _():
    records = _stats_repo.recent(10)
    if not records:
        await stats_detail.finish("暂无统计明细")
    lines = ["最近 10 条命令使用记录"]
    for record in records:
        lines.append(
            f"{record['ts']} | {record['command']} | "
            f"{record['user_id']} | {record['scene_id']} | {record['status']}"
        )
    await stats_detail.finish("\n".join(lines))
