"""
@Author: star_482
@Date: 2026/7/31
@File: client
@Description: OpenAI 兼容 LLM 客户端 + 工具调用循环 + 分条回复解析。
用 httpx 直连，不引入 openai SDK（与 storage 用 boto3 直连的风格一致）。
"""
import json
import re

import httpx
from nonebot.log import logger
from nonebot.adapters.qq import MessageSegment

from ..config import config
from .tools import get_tool_schemas, dispatch, ToolContext

_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=60.0)
    return _client


async def _chat_completion(messages: list[dict], tools: list[dict]) -> dict:
    """调用 /chat/completions，返回响应 JSON。"""
    client = _get_client()
    url = config.agent_base_url.rstrip("/") + "/chat/completions"
    payload: dict = {
        "model": config.agent_model,
        "messages": messages,
        "temperature": 0.8,
    }
    if tools:
        payload["tools"] = tools
    resp = await client.post(
        url,
        json=payload,
        headers={"Authorization": f"Bearer {config.agent_api_key}"},
    )
    resp.raise_for_status()
    return resp.json()


def _parse_replies(content: str) -> list[str]:
    """从 LLM 输出解析分条回复，上限 4 条。

    解析顺序：
    1. 直接 json.loads（含 ```json 包裹容错）；
    2. 从原文提取首个 {...} 再解析（容错 LLM 在 JSON 前后加的多余文字）；
    3. 降级：按换行拆分（每非空行一条），适配 LLM 未输出 JSON 而是多行文本的情况。
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
        resp = await _chat_completion(working, tools)
        u = resp.get("usage") or {}
        usage["prompt_tokens"] += u.get("prompt_tokens", 0) or 0
        usage["completion_tokens"] += u.get("completion_tokens", 0) or 0
        usage["total_tokens"] += u.get("total_tokens", 0) or 0
        choice = resp["choices"][0]["message"]
        content = choice.get("content") or ""
        tool_calls = choice.get("tool_calls")

        assistant_msg: dict = {"role": "assistant", "content": content}
        if tool_calls:
            assistant_msg["tool_calls"] = tool_calls
        working.append(assistant_msg)
        new_messages.append(assistant_msg)

        if not tool_calls:
            # 最终回复：历史保留 LLM 原始 content（JSON），让后续轮次跟风输出 JSON
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
            tool_msg = {
                "role": "tool",
                "tool_call_id": tc.get("id", ""),
                "content": result.text,
            }
            working.append(tool_msg)
            new_messages.append(tool_msg)

    logger.warning(f"agent 超过最大步数 {config.agent_max_turns}，强制结束")
    fallback = ["嘉然想了太久，脑子转不过来了，晚点再聊~"]
    fallback_msg = {"role": "assistant", "content": fallback[0]}
    working.append(fallback_msg)
    new_messages.append(fallback_msg)
    return fallback, attachments, new_messages, usage
