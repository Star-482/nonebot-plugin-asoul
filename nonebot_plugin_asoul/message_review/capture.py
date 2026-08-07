"""
@Author: star_482
@Date: 2026/8/6
@File: capture
@Description: 消息捕获。入站用 event_preprocessor，出站用 QQBot.on_calling_api。
仅关注群消息 + C2C 私聊，不处理频道（guild）相关。
零侵入：仅在 review_enabled 时由 __init__ 调用 register() 挂载 hook。
"""
import json
from datetime import datetime
from typing import Any

from nonebot.adapters import Bot, Event
from nonebot.adapters.qq import C2CMessageCreateEvent, GroupMessageCreateEvent
from nonebot.adapters.qq import Bot as QQBot
from nonebot.message import event_preprocessor

from .broadcaster import broadcaster
from .serialize import json_default, serialize_incoming, serialize_outgoing
from .store import get_store

# 入站消息事件：
#   GroupMessageCreateEvent —— 群消息（全局消息模式，bot 收到全部消息）；
#       GroupAtMessageCreateEvent（被动 @bot）是其子类，一并命中。
#   C2CMessageCreateEvent —— C2C 私聊。
#   频道 GuildMessageEvent / DirectMessageCreateEvent 不属于 GroupMessageCreateEvent 分支，自动排除。
_INCOMING_TYPES = (GroupMessageCreateEvent, C2CMessageCreateEvent)

# 出站消息相关的 QQ API（其它 API 一律早 return，避免开销）。仅群 + C2C，排除频道。
SEND_APIS = {
    "post_group_messages",
    "post_c2c_messages",
}

# 出站 API -> (scene_type, 取 scene_id 的字段名)
_OUT_SCENE = {
    "post_group_messages": ("group", "group_openid"),
    "post_c2c_messages": ("friend", "openid"),
}


def _scene_info(event: Event) -> tuple[str, str]:
    if group_openid := getattr(event, "group_openid", ""):
        return "group", group_openid
    # C2C/私聊：scene_id 即对方 openid（event.get_user_id() 返回 author.user_openid）
    return "friend", event.get_user_id()


def _now() -> tuple[str, float]:
    dt = datetime.now().astimezone()
    return dt.isoformat(timespec="seconds"), dt.timestamp()


def _emit(record: dict, segments: list[dict], row_id: int) -> None:
    """写库后构造广播载荷并推送。"""
    payload = {
        "id": row_id,
        "ts": record["ts"],
        "epoch": record["epoch"],
        "direction": record["direction"],
        "scene_type": record["scene_type"],
        "scene_id": record["scene_id"],
        "user_id": record.get("user_id"),
        "user_name": record.get("user_name"),
        "matcher_module": record.get("matcher_module"),
        "command": record.get("command"),
        "msg_type": record.get("msg_type"),
        "plain_text": record.get("plain_text"),
        "content": segments,
        "status": record.get("status"),
    }
    broadcaster.publish({"type": "message", "data": payload})


async def handle_incoming(event: Event) -> None:
    if not isinstance(event, _INCOMING_TYPES):
        return
    try:
        message = event.get_message()
    except Exception:
        return
    plain, segments = serialize_incoming(message)
    ts, epoch = _now()
    scene_type, scene_id = _scene_info(event)
    author = getattr(event, "author", None)
    user_name = getattr(author, "username", None) if author else None
    record = {
        "ts": ts,
        "epoch": epoch,
        "direction": "in",
        "scene_type": scene_type,
        "scene_id": scene_id,
        "user_id": event.get_user_id(),
        "user_name": user_name,
        "matcher_module": None,
        "command": None,
        "msg_type": None,
        "plain_text": plain,
        "content_json": json.dumps({"segments": segments}, ensure_ascii=False, default=json_default),
        "status": "received",
    }
    row_id = get_store().insert(record)
    _emit(record, segments, row_id)


async def handle_outgoing(bot: Bot, api: str, data: dict[str, Any]) -> None:
    if api not in SEND_APIS:
        return
    scene_type, field = _OUT_SCENE[api]
    scene_id = data.get(field) or "unknown"
    plain, segments, msg_type = serialize_outgoing(data)
    ts, epoch = _now()
    record = {
        "ts": ts,
        "epoch": epoch,
        "direction": "out",
        "scene_type": scene_type,
        "scene_id": scene_id,
        "user_id": "bot",
        "user_name": "小然",
        "matcher_module": None,
        "command": None,
        "msg_type": msg_type,
        "plain_text": plain,
        "content_json": json.dumps({"segments": segments}, ensure_ascii=False, default=json_default),
        "status": "sent",
    }
    row_id = get_store().insert(record)
    _emit(record, segments, row_id)


def register() -> None:
    """由 __init__ 在 review_enabled 时调用，注册入/出站 hook。

    两个 hook 都在此注册，模块导入无副作用；review_enabled=False 时
    （message_review 未启用）不会有任何 hook 挂载、不写库、不挂路由。
    """
    event_preprocessor(handle_incoming)
    QQBot.on_calling_api(handle_outgoing)
