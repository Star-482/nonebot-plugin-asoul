"""
@Author: star_482
@Date: 2026/8/20
@File: vision
@Description: Agent 视觉能力：把用户发送的图片转成文字描述，并入 user 消息喂主模型。
独立视觉模型（OpenAI 兼容多模态 API，base_url/api_key 缺省复用主 agent 配置）。
优先直传图片 URL 给视觉模型（省一次下载）；供应商拉取 URL 失败时降级为本地
下载转 base64 data URL 重试。描述文本由调用方拼进 user_content 入历史，
历史回放/prompt cache 前缀不受影响。
"""
import base64

import httpx
from openai import AsyncOpenAI
from nonebot.log import logger

from ..config import config

# 单张图描述的视觉 prompt：150-250 字，兼顾信息量与 token 成本
# （太简会丢关键细节，太详则拖慢回复且描述块挤占对话上下文）
_VISION_PROMPT = (
    "用中文描述这张图片（150-250字）：先说清主体是什么、在做什么、表情或状态如何，"
    "再点出场景和背景里值得注意的细节，最后概括整体情绪或这张图想表达的意思"
    "（梗图/表情包要说破它的梗）。"
    "图里出现的文字（含截图、聊天记录、水印、对话气泡）必须逐字转录出来--文字往往才是关键信息。"
    "只描述确实看到的，看不清就说看不清，不要编造。"
)

# 下载降级路径的保护参数
_DOWNLOAD_TIMEOUT = 10.0
_MAX_IMAGE_BYTES = 10 * 1024 * 1024
# 单条消息最多处理张数，超出忽略（描述块会告知用户发了更多图）
MAX_IMAGES = 3

_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(
            base_url=config.agent_vision_base_url or config.agent_base_url,
            api_key=config.agent_vision_api_key or config.agent_api_key,
            timeout=60.0,
            max_retries=1,
        )
    return _client


def vision_ready() -> bool:
    """视觉功能是否可用（开关开且模型已配置）。"""
    return bool(config.agent_vision_enabled and config.agent_vision_model)


async def _describe_one_url(url: str) -> str | None:
    """直传 URL 给视觉模型描述一张图。失败返回 None（触发调用方降级）。"""
    resp = await _get_client().chat.completions.create(
        model=config.agent_vision_model,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": _VISION_PROMPT},
                    {"type": "image_url", "image_url": {"url": url}},
                ],
            }
        ],
        temperature=0.3,
    )
    text = (resp.choices[0].message.content or "").strip()
    return text or None


async def _download_as_data_url(url: str) -> str | None:
    """下载图片转 base64 data URL（URL 直传失败时的降级路径）。"""
    try:
        async with httpx.AsyncClient(timeout=_DOWNLOAD_TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.content
    except Exception as e:
        logger.warning(f"agent 视觉下载图片失败 {url[:80]}：{e}")
        return None
    if len(data) > _MAX_IMAGE_BYTES:
        logger.warning(f"agent 视觉图片过大（{len(data)} bytes），跳过")
        return None
    b64 = base64.b64encode(data).decode("ascii")
    # content_type 从响应头取，兜底 jpeg（QQ 图片几乎都是 jpg/png/gif）
    ct = resp.headers.get("content-type", "").split(";")[0].strip() or "image/jpeg"
    if not ct.startswith("image/"):
        ct = "image/jpeg"
    return f"data:{ct};base64,{b64}"


async def _describe_one(url: str) -> str | None:
    """描述一张图：直传 URL -> 失败降级下载转 base64 重试。全失败返回 None。"""
    # 1) 直传 URL（首选：零下载成本）
    try:
        desc = await _describe_one_url(url)
        if desc:
            return desc
    except Exception as e:
        logger.warning(f"agent 视觉 URL 直传失败，降级 base64：{e}")

    # 2) 降级：本地下载转 data URL
    data_url = await _download_as_data_url(url)
    if not data_url:
        return None
    try:
        return await _describe_one_url(data_url)
    except Exception as e:
        logger.warning(f"agent 视觉 base64 重试仍失败 {url[:80]}：{e}")
        return None


async def describe_images(urls: list[str]) -> list[str | None]:
    """批量描述图片，返回与入参等长的列表（失败位为 None，不阻断）。
    超出 MAX_IMAGES 的部分直接忽略（返回 None 占位，让调用方能告知用户发了更多图）。"""
    out: list[str | None] = [None] * len(urls)
    for i, url in enumerate(urls[:MAX_IMAGES]):
        out[i] = await _describe_one(url)
    return out
