"""
@Author: star_482
@Date: 2026/7/31
@File: client
@Description: OpenAI 兼容 LLM 客户端 + 工具调用循环 + 分条回复解析。
统一走 openai SDK（AsyncOpenAI，base_url 可指向任意 OpenAI 兼容供应商，换供应商只改 .env）。
所有 LLM 调用（主对话 / 历史摘要）都经 _complete 单一入口，温度按调用方覆盖。
"""
import json
import re

from openai import AsyncOpenAI
from nonebot.log import logger
from nonebot.adapters.qq import MessageSegment

from ..config import config
from .tools import get_tool_schemas, dispatch, ToolContext

_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(
            base_url=config.agent_base_url,
            api_key=config.agent_api_key,
            timeout=60.0,
            max_retries=2,
        )
    return _client


async def _complete(
    messages: list[dict],
    *,
    tools: list[dict] | None = None,
    temperature: float = 0.8,
) -> tuple[dict, dict, str]:
    """统一 chat.completions 调用入口。返回 (choice.message 原始 dict, usage dict, finish_reason)。
    message 序列化为 dict 返回（含 reasoning_content 等非标准字段），调用方按 dict 处理。
    不设 max_tokens 上限（由模型/供应商默认值兜底）。"""
    client = _get_client()
    # 注意：openai 3.x 对显式 None 不省略（请求体会出现 "tools": null，部分供应商 400），
    # 可选参数按需拼 kwargs
    kwargs: dict = {
        "model": config.agent_model,
        "messages": messages,  # type: ignore[arg-type]
        "temperature": temperature,
    }
    if tools:
        kwargs["tools"] = tools
    if config.agent_json_mode:
        # 强制 JSON 输出。供应商不支持时会 400，关配置即可。
        # 注意：部分 vLLM 系后端 guided decoding 会与 tool_calls 冲突，开了反而不发工具调用
        kwargs["response_format"] = {"type": "json_object"}
    if not config.agent_thinking:
        # DeepSeek V4 思考模式默认开（effort=high），聊天场景可关掉换速度；参数走 extra_body
        kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
    resp = await client.chat.completions.create(**kwargs)
    msg = resp.choices[0].message
    # SDK 消息模型转 dict：model_extra 捞非标准字段（DeepSeek reasoning_content 等）
    raw = {"role": "assistant", "content": msg.content or ""}
    if msg.tool_calls:
        raw["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.function.name, "arguments": tc.function.arguments},
            }
            for tc in msg.tool_calls
        ]
    for key in ("reasoning_content",):
        extra = msg.model_extra.get(key) if msg.model_extra else None
        if extra is not None:
            raw[key] = extra
    usage = {
        "prompt_tokens": resp.usage.prompt_tokens if resp.usage else 0,
        "completion_tokens": resp.usage.completion_tokens if resp.usage else 0,
        "total_tokens": resp.usage.total_tokens if resp.usage else 0,
    }
    finish = resp.choices[0].finish_reason or ""
    # DeepSeek 缓存命中详情（非标准字段，在 usage 顶层；openai SDK 归入 model_extra）
    extra = resp.usage.model_extra if resp.usage else None
    if extra and ("prompt_cache_hit_tokens" in extra or "prompt_cache_miss_tokens" in extra):
        logger.debug(
            f"cache: hit={extra.get('prompt_cache_hit_tokens')} miss={extra.get('prompt_cache_miss_tokens')}"
        )
    return raw, usage, finish


# 从损坏的 JSON 里直接抽 replies 数组内的字符串项（含转义序列）
_REPLY_ITEM = re.compile(r'"((?:[^"\\]|\\.)*)"')


def _extract_replies_regex(text: str) -> list[str]:
    """第三层兜底：外层 JSON 损坏（缺右括号/被 max_tokens 截断）时，
    定位 "replies":[ 用正则逐个抽字符串项，非贪婪到首个 ] 为止（排除 replies 数组
    之后模型可能附带输出的其他 JSON 块的内容）。被截断的最后一项（无闭合引号）自然丢弃。"""
    m = re.search(r'"replies"\s*:\s*\[(.*?)\]', text, re.S)
    if not m:
        return []
    replies: list[str] = []
    for item in _REPLY_ITEM.findall(m.group(1)):
        try:
            s = json.loads('"' + item + '"')  # 解开 \n \" 等转义
        except json.JSONDecodeError:
            s = item
        s = s.strip()
        if s:
            replies.append(s)
    return replies


def _parse_replies(content: str) -> list[str]:
    """从 LLM 输出解析分条回复，上限 4 条。

    解析顺序：
    1. 直接 json.loads（含 ```json 包裹容错）；
    2. 从原文提取首个 {...} 再解析（容错 LLM 在 JSON 前后加的多余文字）；
    3. 正则抽取 replies 数组字符串项（容错外层 JSON 损坏/截断，如缺右花括号）；
    4. 降级：按换行拆分（每非空行一条），适配 LLM 未输出 JSON 而是多行文本的情况。
    """
    if not content:
        return []
    text = content.strip()
    # 容错：去掉可能的 ```json 包裹
    if text.startswith("```"):
        text = text.strip("`")
        if text[:4].lower() == "json":
            text = text[4:]
        text = text.strip()

    obj = None
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        # 容错：LLM 在 JSON 前后加了多余文字，提取首个 {...} 再试
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            try:
                obj = json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                obj = None
    if isinstance(obj, dict) and isinstance(obj.get("replies"), list):
        replies = [str(r).strip() for r in obj["replies"] if str(r).strip()]
        if replies:
            logger.debug(f"agent replies from JSON: {len(replies)} 条")
            return replies[:4]

    # 容错：外层 JSON 损坏（缺右括号/截断），正则直抽 replies 数组字符串项，
    # 避免"按行降级"把原始 JSON 文本当成回复发给用户
    repaired = _extract_replies_regex(text)
    if repaired:
        logger.debug(f"agent replies repaired by regex: {len(repaired)} 条; raw={content!r}")
        return repaired[:4]

    # 降级：按换行拆，每非空行一条（适配 LLM 直接输出多行文本而非 JSON）
    lines = [ln.strip() for ln in content.strip().splitlines() if ln.strip()]
    logger.debug(f"agent replies fallback by line: {len(lines)} 条; raw={content!r}")
    return lines[:4]


async def run_agent(messages: list[dict], ctx: ToolContext) -> tuple[list[str], list[MessageSegment], list[dict], dict]:
    """运行 agent 循环。

    messages: 含 system + 历史 + 当前 user 的完整消息列表（调用方组装）。
    返回 (回复文本列表, 附件列表, 本轮新增消息列表, token 用量)。新增消息含工具调用过程，
    供调用方写入对话历史，使工具结果可跨轮记忆。
    """
    tools = get_tool_schemas()
    attachments: list[MessageSegment] = []
    working = list(messages)  # 本地副本，追加 tool 消息，不污染调用方
    new_messages: list[dict] = []  # 本轮新增（assistant/tool），供调用方存历史
    usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    for _step in range(config.agent_max_turns):
        choice, u, finish = await _complete(working, tools=tools)
        for k in usage:
            usage[k] += u.get(k, 0)
        logger.debug(f"agent step {_step + 1} usage: {u} (累计 {usage['total_tokens']} tokens)")
        content = choice.get("content") or ""
        tool_calls = choice.get("tool_calls")

        # choice dict 含 role/content/tool_calls/reasoning_content。working（同轮工具循环的
        # 后续请求）原样回传全部字段。入库分两种（DeepSeek 思考模式文档规则）：
        # - 带 tool_calls 的 assistant：reasoning_content 在后续所有轮次必须回传，原样保留；
        # - 不带 tool_calls（每轮最终回复）：reasoning_content 无需拼接、传了会被忽略，剥掉
        assistant_msg: dict = dict(choice)
        working.append(assistant_msg)
        if assistant_msg.get("tool_calls"):
            new_messages.append(assistant_msg)
        else:
            new_messages.append({k: v for k, v in assistant_msg.items() if k != "reasoning_content"})

        if not tool_calls:
            # 最终回复：历史保留 LLM 原始 content（JSON），让后续轮次跟风输出 JSON
            if finish == "length":
                # 输出被截断（供应商默认上限）时 _parse_replies 走正则修复层；记日志便于观察
                logger.warning("agent 回复被供应商输出上限截断（finish_reason=length）")
            return _parse_replies(content), attachments, new_messages, usage

        # 执行所有工具调用
        for tc in tool_calls:
            fn = tc.get("function", {}) or {}
            name = fn.get("name", "")
            try:
                call_args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                call_args = {}
            logger.debug(f"agent tool call: {name} args={call_args}")
            result = await dispatch(name, call_args, ctx)
            attachments.extend(result.attachments)
            tool_text = result.text if len(result.text) <= 2000 else result.text[:2000] + "…（已截断）"
            tool_msg = {
                "role": "tool",
                "tool_call_id": tc.get("id", ""),
                "content": tool_text,
            }
            working.append(tool_msg)
            new_messages.append(tool_msg)

    logger.warning(f"agent 超过最大步数 {config.agent_max_turns}，强制结束")
    fallback = ["嘉然想了太久，脑子转不过来了，晚点再聊~"]
    fallback_msg = {"role": "assistant", "content": fallback[0]}
    working.append(fallback_msg)
    new_messages.append(fallback_msg)
    return fallback, attachments, new_messages, usage


async def summarize_history(old_summary: str | None, messages: list[dict]) -> str:
    """把一段历史对话 + 旧摘要压缩成新的摘要文本（用于历史压缩）。

    只取本轮被压缩消息 + 上一版摘要，成本有界。
    被压缩的原始消息由调用方留存到 compressed 存档，本函数产出滚动摘要注入后续请求。
    返回空串时回退到旧摘要。
    """
    convo: list[str] = []
    for m in messages:
        role = m.get("role")
        if role == "user":
            convo.append(f"用户：{m.get('content', '')}")
        elif role == "assistant":
            c = m.get("content") or ""
            tcs = m.get("tool_calls")
            if tcs:
                names = ",".join((tc.get("function", {}) or {}).get("name", "") for tc in tcs)
                c = f"[调用工具:{names}] " + c
            convo.append(f"嘉然：{c}")
        elif role == "tool":
            # 工具结果可能很长，截断
            convo.append(f"[工具结果]：{(m.get('content') or '')[:200]}")
        # role == system（条数指令）跳过，对摘要无意义
    convo_text = "\n".join(convo)[-8000:]  # 截断保护，保留最近内容

    sys_msg = (
        "你是对话摘要助手。把嘉然与一位或多位用户的对话压缩成一段简洁中文摘要，控制在 1000 字以内，"
        "通常 400-800 字即可。"
        "必须保留：各发言人的区分、关键事实、偏好/称呼、未结束的话题、约定与明显的情绪；"
        "多人群聊中绝不能把不同成员的事实混为一人。"
        "必须丢弃：一次性娱乐工具调用的过程细节（抽签/吃什么/抽老婆/小作文/帮助菜单等，"
        "除非用户对此表现出明确的偏好或情绪）；寒暄客套；已经过时的信息"
        "（话题已结束、旧问题已有新答案时，只留结论不留过程）。"
        "涉及状态变更的操作只记结论（如'群主订阅了嘉然开播'，不记调用过程）。"
        "写成连贯的一段话，不要列表、不要标题、不要前后缀。"
    )
    if old_summary:
        user_msg = (
            "把【上一版摘要】和【本轮新增对话】合并重写成一份新摘要（1000 字以内）。"
            "不是拼接：过时或不再重要的旧信息要果断删掉，为新信息腾出空间。\n\n"
            f"【上一版摘要】\n{old_summary}\n\n"
            f"【本轮新增对话】\n{convo_text}"
        )
    else:
        user_msg = f"【对话内容】\n{convo_text}"

    raw, _usage, _finish = await _complete(
        [
            {"role": "system", "content": sys_msg},
            {"role": "user", "content": user_msg},
        ],
        tools=None,
        temperature=0.3,
    )
    summary = (raw.get("content") or "").strip()
    # 兜底：LLM 超长时硬截断（1000 字目标 + 容差），保证注入 system prompt 的体积有界
    if len(summary) > 1200:
        summary = summary[:1200].rstrip() + "…"
    return summary or (old_summary or "")
