"""
@Author: star_482
@Date: 2026/8/6
@File: server
@Description: FastAPI 路由（REST 历史 + WS 实时），经 get_app() 挂载到 review_mount。
仅在 review_enabled 时由 __init__ 调用 register_routes()。fastapi 为本模块硬依赖
（HTTP 审核接口本就需要 HTTP 框架）；缺 fastapi 时 __init__ 会捕获并降级为仅存储。
"""
import json
from typing import Optional

from fastapi import HTTPException, WebSocket, WebSocketDisconnect
from nonebot import get_app
from nonebot.log import logger

from ..config import config
from .broadcaster import broadcaster
from .store import get_store

_mount = config.review_mount.rstrip("/")

# 重连 catchup 单次最大回补条数；缺口超过此数时 has_more=true，客户端转 REST 翻页补齐
CATCHUP_LIMIT = 1000


def _check_token(token: Optional[str]) -> bool:
    cfg = config.review_token
    if not cfg:
        return True
    return token == cfg


def _unauthorized() -> HTTPException:
    return HTTPException(status_code=401, detail="invalid token")


async def list_conversations(token: Optional[str] = None):
    if not _check_token(token):
        raise _unauthorized()
    return get_store().list_conversations()


async def list_messages(
    scene_type: Optional[str] = None,
    scene_id: Optional[str] = None,
    before_id: Optional[int] = None,
    since_id: Optional[int] = None,
    limit: int = 50,
    token: Optional[str] = None,
):
    """消息历史查询，两种模式：

    - 全局 since 分页：传 since_id，返回 id > since_id 的消息（升序），最多 limit（上限 CATCHUP_LIMIT）。
      用于 WS 重连 has_more=true 时客户端循环补全全局缺口。客户端靠"返回不足 limit 条"判断结束。
    - 按会话翻历史：传 scene_type + scene_id，返回该会话 id < before_id 的最新 limit 条（id DESC）。
    """
    if not _check_token(token):
        raise _unauthorized()
    if since_id is not None:
        return get_store().since(since_id, min(limit, CATCHUP_LIMIT))
    if not scene_type or not scene_id:
        raise HTTPException(status_code=400, detail="scene_type and scene_id required (or pass since_id for global paging)")
    return get_store().page_before(scene_type, scene_id, before_id, limit)


async def ws_endpoint(websocket: WebSocket):
    if not _check_token(websocket.query_params.get("token")):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    q = broadcaster.register()
    try:
        # 解析 since_id：客户端重连时带最后收到的消息 id，精确填缺口
        raw = websocket.query_params.get("since_id")
        try:
            since_id = int(raw) if raw is not None else None
        except (TypeError, ValueError):
            since_id = None

        if since_id is not None:
            batch = get_store().since(since_id, CATCHUP_LIMIT)
            has_more = len(batch) >= CATCHUP_LIMIT
            await websocket.send_text(
                json.dumps(
                    {"type": "catchup", "since_id": since_id, "has_more": has_more, "data": batch},
                    ensure_ascii=False,
                )
            )
            last_sent_id = batch[-1]["id"] if batch else since_id
        else:
            batch = get_store().recent(config.review_ws_recent_on_connect)
            await websocket.send_text(
                json.dumps({"type": "recent", "data": batch}, ensure_ascii=False)
            )
            last_sent_id = batch[-1]["id"] if batch else 0

        # 实时推送：register 与批量查询之间插入的消息会同时出现在批量结果和队列里，
        # 按 id 去重（id <= last_sent_id 说明已在批量结果中下发过）
        while True:
            msg = await q.get()
            msg_id = (msg.get("data") or {}).get("id")
            if msg_id is not None and msg_id <= last_sent_id:
                continue
            await websocket.send_text(json.dumps(msg, ensure_ascii=False))
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.debug(f"review ws 断开: {e!r}")
    finally:
        broadcaster.unregister(q)


def register_routes() -> None:
    app = get_app()  # 非 ASGI driver 时抛错，由调用方捕获
    app.add_api_route(f"{_mount}/api/conversations", list_conversations, methods=["GET"])
    app.add_api_route(f"{_mount}/api/messages", list_messages, methods=["GET"])
    app.add_api_websocket_route(f"{_mount}/ws", ws_endpoint)
    logger.info(
        f"消息审核接口已挂载: GET {_mount}/api/conversations | "
        f"GET {_mount}/api/messages | WS {_mount}/ws"
    )
