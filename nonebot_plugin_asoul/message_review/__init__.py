"""
@Author: star_482
@Date: 2026/8/6
@File: message_review
@Description: 消息审核子包。捕获所有入/出站消息 -> SQLite 存储 -> REST 历史 + WS 实时推送，
供外部仿 QQ 客户端审核。review_enabled=False 时零注册、零开销。
"""
from nonebot.log import logger

from ..config import config


def _bootstrap() -> None:
    if not config.review_enabled:
        logger.debug("消息审核未启用（review_enabled=False）")
        return
    from .store import init_store
    from . import capture

    init_store()
    capture.register()
    # HTTP 接口依赖 fastapi + ASGI driver；缺失时降级为仅存储+捕获，不拖垮插件加载
    try:
        from . import server
        server.register_routes()
    except Exception as e:
        logger.warning(f"消息审核 HTTP 接口不可用（{e!r}）；存储与捕获仍可用")
    logger.info("消息审核已启用")


_bootstrap()
