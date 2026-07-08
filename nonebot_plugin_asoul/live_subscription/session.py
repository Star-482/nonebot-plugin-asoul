"""
@Author: star_482
@Date: 2026/7/8
@File: session
@Description: pixel-asoul.club 直播聚合会话查询 + 页面截图
"""
import asyncio
import logging

import httpx

from ..config import config
from ..browser import get_browser

logger = logging.getLogger(__name__)

SESSION_API = "https://live.pixel-asoul.club/api/sessions/latest-aggregated"
SESSION_PAGE = "https://live.pixel-asoul.club/session"


async def poll_session_id(
    room_id: int,
    *,
    max_retries: int = 10,
    interval: float = 60.0,
) -> int | None:
    """轮询聚合 API 获取 session_id，最多重试 max_retries 次.

    GET ?room_id=xxx，data=0 表示尚未聚合完成，等 interval 秒后重试。
    返回 session_id（int）或 None（超时/异常）。
    """
    async with httpx.AsyncClient(timeout=config.live_poll_http_timeout) as client:
        for attempt in range(1, max_retries + 1):
            try:
                resp = await client.get(
                    SESSION_API, params={"room_id": room_id}
                )
                resp.raise_for_status()
                body = resp.json()
            except (httpx.HTTPError, ValueError) as e:
                logger.warning(
                    f"[live-stop] 聚合查询失败 (第{attempt}/{max_retries}次): {e}"
                )
                if attempt < max_retries:
                    await asyncio.sleep(interval)
                continue

            if body.get("code") != 0:
                logger.warning(
                    f"[live-stop] 聚合 API 返回错误: code={body.get('code')} "
                    f"message={body.get('message')}"
                )
                return None

            session_id = body.get("data", 0)
            if session_id != 0:
                logger.info(
                    f"[live-stop] 获取到 session_id={session_id} (第{attempt}次)"
                )
                return session_id

            logger.debug(
                f"[live-stop] 会话尚未聚合 room_id={room_id} "
                f"(第{attempt}/{max_retries}次)"
            )
            if attempt < max_retries:
                await asyncio.sleep(interval)

    logger.warning(
        f"[live-stop] 超时：room_id={room_id} 在 {max_retries} 次重试后仍未聚合"
    )
    return None


async def screenshot_session_page(session_id: int) -> bytes | None:
    """使用共享浏览器访问 session 页面，截取 <main.page-shell> 元素.

    等待 5 秒让 JS 渲染完成，然后截图。
    返回 PNG bytes 或 None。
    """
    try:
        browser = await get_browser()
        page = await browser.new_page()
        try:
            await page.goto(
                f"{SESSION_PAGE}/{session_id}",
                wait_until="domcontentloaded",
            )
            await asyncio.sleep(10)
            el = page.locator("main.page-shell")
            await el.wait_for(state="visible", timeout=10_000)
            return await el.screenshot(type="png")
        finally:
            await page.close()
    except Exception as e:
        logger.warning(f"[live-stop] 截图 session={session_id} 失败: {e}")
        return None
