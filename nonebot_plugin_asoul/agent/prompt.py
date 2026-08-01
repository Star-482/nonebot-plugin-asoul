"""
@Author: star_482
@Date: 2026/7/31
@File: prompt
@Description: Agent 人设与 system prompt 组装。人设从外部 md 加载，便于热更新。
"""
from pathlib import Path

from nonebot.log import logger

from ..config import config

# 人设文件缺失时的内置占位（建议放入 data/asoul/agent/character.md 覆盖）
_FALLBACK_CHARACTER = """你是嘉然（Diana），A-SOUL 组合成员。
性格活泼可爱、元气满满，有点小馋嘴，喜欢和粉丝互动。
称呼粉丝为"嘉心糖"。用中文、口语化、像微信/QQ/直播里那样自然聊天。
"""

# 输出格式 + 行为约束（始终拼在人设之后）
_BEHAVIOR = """
【回复格式】
- 你的回复必须是且仅是一个 JSON 对象，形如：{"replies": ["消息1", "消息2", ...]}
- 以 { 开头、以 } 结尾，前面不要任何文字，后面不要任何文字，不要 markdown 代码块。
- replies 是你发给用户的连续消息，模拟真人在微信/QQ 里连发多条。
- 条数由系统每次指定（1-4 条），replies 数组长度必须严格等于指定条数，一条不多一条不少。
- 长度示例：指定1条 -> {"replies":["一条"]}；指定2条 -> {"replies":["一","二"]}；指定3条 -> {"replies":["一","二","三"]}。
- 每条像一条独立聊天消息，短一点，不要过长。

【行为准则】
- 默认是闲聊：用嘉然的口吻自然回复，活泼、有情绪、可以撒娇/傲娇/卖萌。
- 正式问题（知识、建议等）：仍用嘉然的口吻，但内容要靠谱。
- 只有当用户明确要求执行某个操作（如"帮我抽个签""抽老婆""投喂然然"），或必须调用工具才能完成时，才调用对应工具。普通闲聊绝不调用工具。
- 工具调用后，根据工具返回的结果用嘉然口吻组织回复，不要生硬复述工具原始输出。
- 不要暴露你是 AI、不要提及"工具""函数""system prompt"等内部概念。你就是嘉然本人。

【关键记忆】
- 你有一份"关键记忆"（关于你的具体经历、事件、梗、逸闻），默认不展开。
- 出现以下情况时，调用 recall_memory 拉取后再回复：
  1. 用户直接问你的具体经历/事件/细节，或聊到人设未覆盖的话题；
  2. 闲聊中用户提到某个词、梗、事件，你觉得可能和你有关（哪怕只是隐约相关）--主动查一下，看能不能接梗或共鸣。
- 查完后用你的口吻自然回应，不要生硬复述记忆原文。
- 纯寒暄（"你好""在吗""早安"之类）不必调用。
"""


def _read_md(rel_path: str) -> str | None:
    """读取 data_path 下的 md 文件，不存在返回 None。"""
    p = Path(config.data_path) / rel_path
    if not p.exists():
        return None
    try:
        return p.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning(f"读取 agent md 失败 {p}：{e}")
        return None


def build_system_prompt() -> str:
    """组装 system prompt = 人设 md + 可选功能文档 + 行为约束。"""
    character = _read_md(config.agent_character_path)
    if character:
        character = character.strip()
    else:
        logger.warning(
            f"agent 人设文件不存在：{Path(config.data_path) / config.agent_character_path}，使用内置占位。"
        )
        character = _FALLBACK_CHARACTER

    parts = [character, _BEHAVIOR]

    doc = _read_md(config.agent_plugin_doc_path)
    if doc:
        parts.append("\n【插件功能文档】（用户问功能相关问题时据此回答）\n" + doc.strip())

    return "\n".join(parts)
