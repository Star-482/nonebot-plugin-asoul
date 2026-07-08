"""
@Author: star_482
@Date: 2026/6/4
@File: notifier
@Description: QQ 开播/下播通知器。被 LiveChecker 回调，查订阅群 → 发 Markdown 消息。
"""
import asyncio
import io

from nonebot import get_bot
from nonebot.adapters.qq import MessageSegment
from nonebot.adapters.qq.models import (
    Action,
    Button,
    InlineKeyboard,
    InlineKeyboardRow,
    MessageKeyboard,
    Permission,
    RenderData,
)
from nonebot.log import logger

from .api import LiveInfo
from .manager import manager
from .session import poll_session_id, screenshot_session_page
from ..storage import get_bucket, KEY_PREFIX


def _mk_link_button(url: str, label: str = "去直播间") -> Button:
    return Button(
        id="live_goto",
        render_data=RenderData(label=label, visited_label=label, style=1),
        action=Action(
            type=0,
            permission=Permission(type=2),
            data=url,
            unsupport_tips=f"请手动打开：{url}",
        ),
    )


class Notifier:
    """QQ 群 Markdown 通知。"""

    async def on_live_start(self, info: LiveInfo) -> None:
        groups = manager.get_subscribed_groups(info.uid)
        if not groups:
            return

        md = (
            f"## {info.uname} 开播啦！\n\n"
            f"**{info.title}**\n\n"
            f"![#1920px #1080px]({info.cover})"
        )
        keyboard = MessageKeyboard(
            content=InlineKeyboard(
                rows=[InlineKeyboardRow(buttons=[_mk_link_button(info.url)])]
            )
        )
        message = MessageSegment.markdown(md) + MessageSegment.keyboard(keyboard)

        bot = get_bot()
        for gid in groups:
            try:
                await bot.send_to_group(group_openid=gid, message=message)
            except Exception as e:
                logger.warning(
                    f"发送开播通知失败 gid={gid} uid={info.uid}: {e}"
                )

    async def on_live_stop(self, info: LiveInfo, _old_info: LiveInfo | None = None) -> None:
        if _old_info is None:
            return
        groups = manager.get_subscribed_groups(_old_info.uid)
        if not groups:
            return

        # 轮询 + 截图 + 上传可能长达 10 分钟，fire-and-forget 不阻塞轮询
        asyncio.ensure_future(
            self._do_live_stop_notify(info, _old_info, groups)
        )

    async def _do_live_stop_notify(
        self, info: LiveInfo, old_info: LiveInfo, groups: list[str],
    ) -> None:
        """下播通知管线：轮询聚合 → 截图 → 上传 → 发消息."""
        uname = old_info.uname
        uid = old_info.uid
        room_id = old_info.room_id

        # 1. 轮询聚合 API
        session_id = await poll_session_id(room_id)
        if session_id is None:
            logger.warning(
                f"[live-stop] {uname}(uid={uid}) 无法获取聚合会话ID，取消通知"
            )
            return

        # 2. 截图 session 页面
        screenshot_bytes = await screenshot_session_page(session_id)
        if screenshot_bytes is None:
            logger.warning(
                f"[live-stop] {uname}(uid={uid}) 截图失败，取消通知"
            )
            return

        # 3. 读图片尺寸
        try:
            from PIL import Image as PILImage
            with PILImage.open(io.BytesIO(screenshot_bytes)) as img:
                width, height = img.size
        except Exception as e:
            logger.warning(f"[live-stop] 读取截图尺寸失败: {e}")
            width, height = 0, 0

        # 4. 上传 COS
        bucket = get_bucket()
        key = f"{KEY_PREFIX['live_session']}/{session_id}.png"
        url = await bucket.upload_bytes(screenshot_bytes, key, content_type="image/png")
        if url is None:
            logger.warning(
                f"[live-stop] {uname}(uid={uid}) COS 上传失败，取消通知"
            )
            return

        # 5. 构建 Markdown + 发送
        md_img = bucket.build_md_image(
            url, width, height, alt=f"{uname} 直播数据"
        )
        md = (
            f"## {uname} 下播了\n\n"
            f"**{old_info.title}** 直播已结束\n\n"
            f"{md_img}\n\n"
            f"[查看完整数据](https://live.pixel-asoul.club/session/{session_id})"
        )

        keyboard = MessageKeyboard(
            content=InlineKeyboard(
                rows=[InlineKeyboardRow(buttons=[_mk_link_button("https://live.pixel-asoul.club", "查看数据")])]
            )
        )
        message = MessageSegment.markdown(md) + MessageSegment.keyboard(keyboard)

        bot = get_bot()
        for gid in groups:
            try:
                await bot.send_to_group(group_openid=gid, message=message)
            except Exception as e:
                logger.warning(
                    f"[live-stop] 发送下播通知失败 gid={gid} uid={uid}: {e}"
                )
