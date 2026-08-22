"""
@Author: star_482
@Date: 2026/7/31
@File: commands
@Description: Agent 兜底 matcher--命令未命中时落到 LLM 拟人聊天。
priority 远高于命令(15)，命令命中并 finish 后不会触发；只有未命中任何命令
的消息才落到这里。群聊即"@bot 闲聊"，私聊同样触发。
"""
import asyncio
import datetime
import json
import time
from pathlib import Path

from nonebot import get_driver
from nonebot.adapters import Event
from nonebot.adapters.qq import Bot, MessageSegment
from nonebot.internal.matcher import Matcher
from nonebot.log import logger
from nonebot.plugin.on import on_message
from nonebot.rule import to_me

from ..config import config
from .prompt import build_system_prompt
from .client import run_agent
from .history import get_history_for_request, append_turn, maybe_compress
from .stats import record_usage
from .tools import ToolContext
from .vision import vision_ready, describe_images, MAX_IMAGES

async def _not_bot(event: Event) -> bool:
    """过滤 bot 发送的消息（避免回复其他 bot / 自我循环）。"""
    author = getattr(event, "author", None)
    return not getattr(author, "bot", False)


# 命令前缀（NoneBot command_start，默认 "/"）-- 命令由命令 matcher 处理，不进 agent
_command_starts: tuple[str, ...] = tuple(get_driver().config.command_start)


async def _not_command(event: Event) -> bool:
    """排除命令消息（以 command_start 开头），命令在 rule 阶段就不匹配 agent matcher。"""
    text = event.get_message().extract_plain_text().lstrip()
    return not text.startswith(_command_starts)


# 兜底 matcher：priority=command_priority+50，排在所有命令之后
# rule=to_me() & _not_bot & _not_command：只响应私聊/群@、非 bot、非命令的消息
agent_matcher = on_message(
    priority=config.command_priority + 50,
    rule=to_me() & _not_bot & _not_command,
)

# 每用户调用 CD（防刷）
_cd_last: dict[str, float] = {}

# 每用户整轮对话锁：串行化 run_agent 全过程（读历史->生成->发送->写回），
# 避免同一用户上一轮未完成时下一轮重叠导致上下文陈旧 / 时序错乱。
# check-then-create 之间无 await，asyncio 单线程下原子，无需额外 guard。
_user_locks: dict[str, asyncio.Lock] = {}


def _get_user_lock(user_id: str) -> asyncio.Lock:
    if user_id not in _user_locks:
        _user_locks[user_id] = asyncio.Lock()
    return _user_locks[user_id]


# 回复条数上限（固定；不随机采样）。条数引导交给条数指令：以 1-2 条为主，最多 3 条
_MAX_REPLIES = 3


def _archive_dialogue(user_id: str, group_id, user_text: str, replies: list[str], turn_msgs: list[dict]) -> None:
    """把对话追加到 JSONL 存档文件（仅存档，不读回，不影响现有历史逻辑）。"""
    tools = []
    for m in turn_msgs:
        if m.get("role") == "assistant" and m.get("tool_calls"):
            for tc in m["tool_calls"]:
                tools.append((tc.get("function") or {}).get("name", ""))
    record = {
        "ts": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "user_id": user_id,
        "group_id": str(group_id) if group_id else "dm",
        "user_text": user_text,
        "replies": replies,
        "tools": tools,
    }
    path = Path(config.data_path) / "agent" / "dialogue_archive.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        logger.exception("写入 agent 对话存档失败")


def _now_text() -> str:
    """当前时间描述（东八区，聊天场景以国内时间为准）。"""
    tz = datetime.timezone(datetime.timedelta(hours=8))
    now = datetime.datetime.now(tz)
    return now.strftime("%Y年%m月%d日 %H:%M") + f" 星期{'一二三四五六日'[now.weekday()]}"


def _extract_text_with_mentions(event: Event) -> str:
    """提取用户消息文本，@成员 渲染成 @用户名（extract_plain_text 会丢弃 MentionUser 段，
    导致模型看不见"禁言@某人"里的被操作对象）。@bot 自身跳过（to_me 已表达）。"""
    parts: list[str] = []
    for seg in event.get_message():
        if seg.type == "text":
            parts.append(str(seg.data.get("text", "")))
        elif seg.type == "mention_user" and not seg.data.get("is_bot"):
            name = seg.data.get("username") or seg.data.get("user_id", "")
            parts.append(f"@{name} ")
    return "".join(parts).strip()


def _extract_mention_user_ids(event: Event) -> list[str]:
    """从消息的 mention_user segments 取非 bot 成员的 openid（群管工具的禁言目标）。"""
    ids: list[str] = []
    for seg in event.get_message():
        if seg.type == "mention_user" and not seg.data.get("is_bot"):
            uid = seg.data.get("user_id")
            if uid and uid not in ids:
                ids.append(uid)
    return ids


def _extract_image_urls(event: Event) -> list[str]:
    """提取用户消息里的图片 URL（QQ attachments 经 adapter 转成 image 段，data 带 url）。"""
    urls: list[str] = []
    for seg in event.get_message():
        if seg.type == "image":
            url = str(seg.data.get("url") or "")
            if url:
                urls.append(url)
    return urls


async def _build_image_note(image_urls: list[str]) -> str:
    """构造并入 user 消息的图片描述块（视觉能力）。返回空串表示无图。

    三种降级都保持"让 LLM 知道有图"：未启用视觉、识别失败、超出张数上限，
    分别以不同措辞告知，避免 LLM 对图的存在一无所知或假装看到了内容。
    """
    total = len(image_urls)
    if not total:
        return ""
    if not vision_ready():
        return f"（用户随消息发送了 {total} 张图片，但你看不到图片内容）"
    descs = await describe_images(image_urls)
    shown = min(total, MAX_IMAGES)
    lines = []
    for i in range(shown):
        d = descs[i] or "（内容暂时看不了）"
        lines.append(f"第{i + 1}张：{d}")
    header = f"（用户随消息发送了 {total} 张图片"
    if total > shown:
        header += f"，只看得到前 {shown} 张"
    return header + "，内容大致是：\n" + "\n".join(lines) + "）"


@agent_matcher.handle()
async def _(event: Event, bot: Bot, matcher: Matcher):
    if not config.agent_enabled:
        return  # 未启用，静默（行为同现状）

    text = _extract_text_with_mentions(event)
    image_urls = _extract_image_urls(event)
    if not text and not image_urls:
        return  # 空消息（无文字无图），不喂 LLM

    user_id = event.get_user_id()
    # per-user CD
    now = time.time()
    if now - _cd_last.get(user_id, 0.0) < config.agent_user_cd:
        return
    _cd_last[user_id] = now

    group_id = getattr(event, "group_openid", None)
    member_role = getattr(getattr(event, "author", None), "member_role", None) or ""
    ctx = ToolContext(
        user_id=user_id,
        group_id=str(group_id) if group_id else None,
        bot=bot,
        member_role=member_role,
        mention_user_ids=_extract_mention_user_ids(event),
    )

    # 每用户整轮锁：串行化 读历史->run_agent->发送->写回，避免重叠导致上下文陈旧与时序错乱
    async with _get_user_lock(user_id):
        # 视觉：图片转文字描述（输入预处理，先于历史读取）。描述并入 user 消息
        # 随历史原样回放，保证下一轮请求前缀与本轮一致（prompt cache 不断裂）
        image_note = await _build_image_note(image_urls) if image_urls else ""
        vision_calls = min(len(image_urls), MAX_IMAGES) if (image_note and vision_ready()) else 0
        # 组装 messages：system + [历史摘要?] + 历史 + 当前 user
        system_prompt = build_system_prompt()
        history_copy = await get_history_for_request(user_id)
        # 时间/条数/工具提醒并入 user 消息尾部注入：历史中不能出现 mid-history system 消息，
        # DeepSeek 服务端模板会重排夹在历史中间的 system 消息，导致 prompt cache 前缀逐轮断裂
        # （实测只有 system + 累积指令命中，全部 user/assistant 历史永久 miss）
        parts = [text or "（用户发送了图片，没有配文字）"]
        if image_note:
            parts.append(image_note)
        parts.append(
            f"（系统提示，不要复述或回应：现在是 {_now_text()}。"
            f"本次连发消息最多 {_MAX_REPLIES} 条，以 1-2 条为主：一句话能说完就发 1 条，"
            "确实想补充才分 2 条，只有内容很值得才用 3 条，禁止凑数或加'嗯嗯''对的'之类的填充消息。"
            "用户消息里要求执行操作（禁言/抽签/订阅等）时，必须先发起工具调用，"
            "等工具结果回来再组织回复；没执行过工具就不能声称已完成。）"
        )
        user_content = "\n\n".join(parts)
        messages = (
            [{"role": "system", "content": system_prompt}]
            + history_copy
            + [{"role": "user", "content": user_content}]
        )

        try:
            replies, attachments, turn_msgs, usage = await run_agent(messages, ctx)
        except Exception:
            logger.exception("agent run_agent 失败")
            await matcher.send(MessageSegment.text("然然现在有点晕，稍后再试~"))
            return  # 锁自动释放；跳过压缩/统计/存档

        # 容错：LLM 可能超条数，取前 3 条（不足则全发）
        replies = replies[:_MAX_REPLIES] if replies else []
        if not replies:
            replies = ["……"]

        # 逐条发送文本，附件单独各发一条（避免 text+markdown 混合时文本段被吞）
        for line in replies:
            await matcher.send(MessageSegment.text(line))
        for att in attachments:
            await matcher.send(att)

        # 存历史：user（含系统注入文本，原样回放保证下一轮请求前缀与本轮一致）+ 本轮新增
        # （含工具调用过程），工具结果可跨轮记忆
        await append_turn(user_id, [{"role": "user", "content": user_content}] + turn_msgs)
    # 锁释放：压缩/统计/存档放锁外，不阻塞下一轮（压缩自带前端不变校验，并发安全）
    # 达阈值则压缩：旧消息入 compressed 存档 + 滚动摘要，留尾部 keep 条
    await maybe_compress(user_id)
    # 统计 token 用量 + 视觉调用数 + 对话存档（仅存档，不读回）
    record_usage(calls=1, vision_calls=vision_calls, **usage)
    _archive_dialogue(user_id, group_id, text or "(图片)", replies, turn_msgs)
