"""
@Author: star_482
@Date: 2026/8/12
@File: qq_api
@Description: QQ 群/机器人相关接口的临时封装。adapter 目前未提供群 info/bot_state 的
Python 方法，这里借 bot 自带鉴权（bot._request 自动注入 Authorization + 401 刷新 token）
直接调 /v2/groups/{gid}/info 与 /v2/groups/{gid}/bot_state。供加群事件补全群信息/推送状态。
临时实现：后续 adapter 若内置这些接口，本文件可移除。接口仅白名单机器人可用（非白名单 11253）。
"""
from typing import Optional

from nonebot.adapters.qq import Bot
from nonebot.adapters.qq.exception import ActionFailed
from nonebot.drivers import Request


async def _get(bot: Bot, group_openid: str, path: str) -> Optional[dict]:
    """GET /v2/groups/{gid}/{path}，返回原始 dict；失败（含 11253 非白名单）返回 None。"""
    try:
        request = Request(
            "GET",
            bot.adapter.get_api_base().joinpath("v2", "groups", group_openid, path),
        )
        data = await bot._request(request)
    except Exception as e:
        from nonebot.log import logger
        logger.warning(f"[qq_api] GET .../{path} 失败 gid={group_openid}: {e!r}")
        return None
    return data if isinstance(data, dict) else None


async def get_group_info(bot: Bot, group_openid: str) -> Optional[dict]:
    """群信息：{name, intro, member_count}。白名单接口，失败返回 None。"""
    data = await _get(bot, group_openid, "info")
    if not data:
        return None
    return {
        "name": data.get("group_name"),
        "intro": data.get("group_finger_memo"),
        "member_count": data.get("group_member_num"),
    }


async def get_group_bot_state(bot: Bot, group_openid: str) -> Optional[dict]:
    """群内 bot 状态：{allow_proactive_msg, recv_msg_setting, member_role}。白名单接口。"""
    data = await _get(bot, group_openid, "bot_state")
    if not data:
        return None
    return {
        "allow_proactive_msg": data.get("allow_proactive_msg"),
        "recv_msg_setting": data.get("recv_msg_setting"),
        "member_role": data.get("member_role"),
    }


async def set_group_member_mute(
    bot: Bot, group_openid: str, operations: list[dict]
) -> tuple[bool, str]:
    """设置群成员禁言/解禁。需 bot 是群管理员。

    operations: [{"op":"add"|"update"|"del", "member_openid":str, "mute_expire_at":str}]
      - add/update: mute_expire_at 为 RFC3339 到期时间（最长 30 天）
      - del: mute_expire_at 传空串立即解除
    成功 -> (True, "")；失败 -> (False, 可读信息)，信息取自 API message（涵盖 bot 非管理员/目标不可禁言等）。
    """
    from nonebot.log import logger
    try:
        request = Request(
            "POST",
            bot.adapter.get_api_base().joinpath(
                "v2", "groups", group_openid, "restrict_chat_setting"
            ),
            json={"members": operations},
        )
        await bot._request(request)
    except ActionFailed as e:
        logger.warning(f"[qq_api] 设置禁言失败 gid={group_openid}: {e!r}")
        return (False, e.message or f"禁言失败（HTTP {e.status_code}）")
    except Exception as e:
        logger.warning(f"[qq_api] 设置禁言网络错误 gid={group_openid}: {e!r}")
        return (False, "网络错误，请稍后重试")
    return (True, "")
