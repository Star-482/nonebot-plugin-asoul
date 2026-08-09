"""
@Author: star_482
@Date: 2026/6/4
@File: notifier
@Description: QQ 开播/下播通知器。被 LiveChecker 回调，查订阅群 → 发 Markdown 消息。
"""
import asyncio
import io
import time
import traceback

from nonebot import get_bot
from nonebot.adapters.qq import ActionFailed, MessageSegment
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


# 主动消息频控错误码（HTTP 400, 业务码 40034100）；429 同样按频控处理
_RATE_LIMIT_CODES = {40034100}

# 群不可用的真实失效错误码：无主动权限/群解散/bot 非成员，标记 push_fail 不再推送
_FATAL_CODES = {40034105, 11255, 40034101, 40054003}


def _on_notify_done(task: asyncio.Task, label: str = "live-notify") -> None:
    """记录 fire-and-forget 通知任务中的未捕获异常."""
    if task.cancelled():
        logger.info(f"[{label}] 通知任务被取消")
        return
    exc = task.exception()
    if exc is not None:
        logger.warning(
            f"[{label}] 通知任务未捕获异常:\n"
            f"{''.join(traceback.format_exception(exc))}"
        )


class Notifier:
    """QQ 群 Markdown 通知。"""

    async def _try_send(self, gid: str, message, uid: int, label: str) -> None:
        """发送一条通知并更新推送验证状态.

        bot 临时不可用（QQ ws 每 30 分钟重连，期间 get_bot() 抛 KeyError）时
        短暂重试以避免漏发；仍不可用则跳过本群：不标记 push_fail，也不向调用方抛出 —— 避免中断后续群的通知循环.
        """
        push_state = manager.is_push_ok(gid)
        if push_state is False:
            return  # 已知不可用，跳过

        # ws 重连约 3s，最多等待 ~3s 重试以命中重连完成；仍不可用则跳过本群，
        # 不标记 push_fail，不中断后续群的通知循环
        bot = None
        for attempt in range(3):
            try:
                bot = get_bot()
                break
            except KeyError:
                if attempt < 2:
                    await asyncio.sleep(1.5)
        if bot is None:
            logger.warning(
                f"[{label}] bot 未连接（重连超时），跳过 gid={gid} uid={uid}"
            )
            return

        # 撞频控时每秒重试至成功或 60s 超时：开播提醒优先即时性，窗口一释放即发
        deadline = time.monotonic() + 60.0
        attempt = 0
        while True:
            try:
                await bot.send_to_group(group_openid=gid, message=message)
                if push_state is None:
                    manager.mark_push_ok(gid)
                    logger.info(f"[{label}] 首次发送成功，标记推送可用 gid={gid}")
                if attempt > 0:
                    logger.info(f"[{label}] gid={gid} 频控重试第 {attempt} 次成功")
                return
            except ActionFailed as e:
                if (
                    e.status_code == 429
                    or e.code in _RATE_LIMIT_CODES
                    or (e.message is not None and "频控" in e.message)
                ):
                    # 频控：每秒重试至成功或 60s 超时，不标记 push_fail
                    if time.monotonic() >= deadline:
                        logger.warning(
                            f"[{label}] gid={gid} uid={uid} 撞频控超 60s，放弃（不标记 push_fail）"
                        )
                        return
                    attempt += 1
                    if attempt == 1:
                        logger.warning(
                            f"[{label}] gid={gid} uid={uid} 撞频控 code={e.code}，每秒重试中…"
                        )
                    await asyncio.sleep(1.0)
                    continue
                if e.code in _FATAL_CODES:
                    # 群不可用（无主动权限/群解散/bot 非成员）：标记 push_fail 不再推送
                    logger.warning(
                        f"[{label}] 群不可用 gid={gid} uid={uid} code={e.code}: {e}"
                    )
                    if push_state in (True, None):
                        manager.mark_push_fail(gid)
                        logger.info(f"[{label}] 标记推送不可用 gid={gid}")
                    return
                # 其他未知失败：不标记 push_fail，跳过本轮（保守，下轮直播再试）
                logger.warning(
                    f"[{label}] 发送失败 gid={gid} uid={uid} "
                    f"code={e.code} status={e.status_code}: {e}（不标记 push_fail）"
                )
                return
            except Exception as e:
                # 网络等未知异常：不标记 push_fail，跳过本轮
                logger.warning(
                    f"[{label}] 发送异常 gid={gid} uid={uid}: {e!r}（不标记 push_fail）"
                )
                return

    async def on_live_start(self, info: LiveInfo) -> None:
        groups = manager.get_subscribed_groups(info.uid)
        if not groups:
            return
        # 并发发送 + 撞频控重试可能耗时，fire-and-forget 不阻塞 _poll，
        # 避免触发 max_instances=1 跳过下一轮
        task = asyncio.create_task(self._do_live_start_notify(info, groups))
        task.add_done_callback(lambda t: _on_notify_done(t, "live-start"))

    async def _do_live_start_notify(self, info: LiveInfo, groups: list[str]) -> None:
        """开播通知管线：构建 Markdown + 逐群发送."""
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

        await asyncio.gather(
            *(self._try_send(gid, message, info.uid, "live-start") for gid in groups)
        )

    async def on_live_stop(self, info: LiveInfo, _old_info: LiveInfo | None = None) -> None:
        if _old_info is None:
            return
        groups = manager.get_subscribed_groups(_old_info.uid)

        # 轮询 + 截图 + 上传可能长达 10 分钟，fire-and-forget 不阻塞轮询
        # 无论有无订阅群都执行管线（截图数据上传后可供后续查看），groups 为空时只跳过发消息
        task = asyncio.create_task(
            self._do_live_stop_notify(info, _old_info, groups)
        )
        task.add_done_callback(lambda t: _on_notify_done(t, "live-stop"))

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

        await asyncio.gather(
            *(self._try_send(gid, message, uid, "live-stop") for gid in groups)
        )
