"""
@Author: star_482
@Date: 2026/6/4
@File: notifier
@Description: QQ 开播/下播通知器。被 LiveChecker 回调，查订阅群 → 发 Markdown 消息。
"""
import asyncio
import io
import traceback

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


def _on_notify_done(task: asyncio.Task) -> None:
    """记录 fire-and-forget 通知任务中的未捕获异常."""
    if task.cancelled():
        logger.info("[live-stop] 通知任务被取消")
        return
    exc = task.exception()
    if exc is not None:
        logger.warning(
            f"[live-stop] 通知任务未捕获异常:\n{traceback.format_exc()}"
        )


class Notifier:
    """QQ 群 Markdown 通知。"""

    async def _try_send(self, gid: str, message, uid: int, label: str) -> None:
        """发送一条通知并更新推送验证状态."""
        push_state = manager.is_push_ok(gid)
        if push_state is False:
            return  # 已知不可用，跳过

        bot = get_bot()
        try:
            await bot.send_to_group(group_openid=gid, message=message)
            if push_state is None:
                manager.mark_push_ok(gid)
                logger.info(f"[{label}] 首次发送成功，标记推送可用 gid={gid}")
        except Exception as e:
            logger.warning(
                f"[{label}] 发送通知失败 gid={gid} uid={uid}: {e}"
            )
            if push_state is True:
                manager.mark_push_fail(gid)
                logger.info(f"[{label}] 推送失效，标记不可用 gid={gid}")
            elif push_state is None:
                manager.mark_push_fail(gid)
                logger.info(f"[{label}] 首次发送失败，标记不可用 gid={gid}")

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

        for gid in groups:
            await self._try_send(gid, message, info.uid, "live-start")
            await asyncio.sleep(1)

    async def on_live_stop(self, info: LiveInfo, _old_info: LiveInfo | None = None) -> None:
        if _old_info is None:
            return
        groups = manager.get_subscribed_groups(_old_info.uid)

        # 轮询 + 截图 + 上传可能长达 10 分钟，fire-and-forget 不阻塞轮询
        # 无论有无订阅群都执行管线（截图数据上传后可供后续查看），groups 为空时只跳过发消息
        task = asyncio.create_task(
            self._do_live_stop_notify(info, _old_info, groups)
        )
        task.add_done_callback(_on_notify_done)

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
            f"[查看完整数据](https://live.pixel-asoul.club)"
        )

        keyboard = MessageKeyboard(
            content=InlineKeyboard(
                rows=[InlineKeyboardRow(buttons=[_mk_link_button("https://live.pixel-asoul.club", "查看数据")])]
            )
        )
        message = MessageSegment.markdown(md) + MessageSegment.keyboard(keyboard)

        for gid in groups:
            await self._try_send(gid, message, uid, "live-stop")
            await asyncio.sleep(1)
