"""
@Author: star_482
@Date: 2026/7/8
@File: browser
@Description: 插件级共享 Playwright 浏览器实例管理
"""

import logging

logger = logging.getLogger(__name__)

# ── 模块级单例 ──
_pw = None
_browser = None


async def get_browser():
    """懒初始化并返回共享的 Playwright Chromium 浏览器实例.

    整个插件的所有模块共享同一个浏览器进程，
    避免重复启动开销。
    """
    global _pw, _browser
    if _browser is None:
        from playwright.async_api import async_playwright

        _pw = await async_playwright().start()
        _browser = await _pw.chromium.launch(headless=True)
        logger.info("Playwright 浏览器已启动（共享实例）")
    return _browser


async def close_browser():
    """关闭共享浏览器及 Playwright 进程.

    通常在 bot 进程退出时调用一次。
    """
    global _pw, _browser
    if _browser:
        await _browser.close()
        _browser = None
        logger.info("Playwright 浏览器已关闭")
    if _pw:
        await _pw.stop()
        _pw = None
        logger.info("Playwright 已停止")
