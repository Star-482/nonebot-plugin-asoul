"""
@Author: star_482
@Date: 2025/3/28 
@File: config 
@Description:
"""
from nonebot import get_plugin_config
from pydantic import BaseModel


class Config(BaseModel):
    data_path: str = "./data/asoul"
    # 公共 SQLite 数据库路径，相对 data_path。供 database 子包与所有模块共享（单库多表）。
    db_path: str = "asoul.db"
    wife_img_dir: str = "wife_img"
    # diana 的 data（YAML + 模板）与 assets（服装立绘）已挪到 nonebot_plugin_asoul/diana/ 包内，
    # 不再可配置；只留 saves 走 data_path，saves 是用户运行时数据。
    # 注：早期版本曾有 diana_data_dir / diana_assets_dir 两项配置，2026-06 路径重构时移除
    # （PR #23）。若 .env 中仍配置这两项，Pydantic 会静默忽略——无效果也无报错。
    diana_saves_dir: str = "diana/saves"
    command_priority: int = 15
    home_url: str = "https://github.com/whalefall123456/nonebot-plugin-asoul"
    whateat_cd: int = 10
    whateat_max: int = 0

    # B站开播订阅轮询
    live_poll_interval: int = 60
    live_poll_http_timeout: float = 10.0

    # 对象存储（腾讯云 COS，S3 兼容协议；也可填其他 S3 兼容存储）
    cos_id: str
    cos_key: str
    cos_url: str
    cos_bucket_name: str = "diana-image"
    cos_public_url: str
    # region：COS 必须填实际区域（如 ap-guangzhou），否则 SigV4 签名失败
    cos_region: str = "ap-guangzhou"

    # ── Agent（LLM 拟人聊天 + 工具调用，OpenAI 兼容 API）──
    # 总开关：默认关，需配置 base_url/api_key/model 后在 .env 开启
    agent_enabled: bool = False
    agent_base_url: str = "https://api.openai.com/v1"
    agent_api_key: str = ""
    agent_model: str = "gpt-4o-mini"
    # 单次对话工具调用循环最大步数（含工具场景；闲聊通常 1 步）
    agent_max_turns: int = 5
    # 当前上下文消息条数上限，达到后触发摘要压缩（旧消息入 compressed 存档 + 滚动摘要）
    agent_history_limit: int = 30
    # 压缩后保留的最近消息条数（其余 ~limit-keep 条压缩为摘要）
    agent_summary_keep: int = 5
    # 每用户调用冷却（秒），防刷
    agent_user_cd: float = 3.0
    # 人设/功能文档 md，相对 data_path；不存在则用内置占位/跳过
    agent_character_path: str = "agent/character.md"
    agent_plugin_doc_path: str = "agent/plugin_doc.md"
    # 关键记忆 md，相对 data_path；默认注入 system prompt
    agent_memories_path: str = "agent/memories.md"

    # ── 消息审核（捕获所有入/出站消息，SQLite 存储 + REST/WS 推送给外部仿 QQ 客户端）──
    # 总开关：默认关，开发审核工具，按需在 .env 开启
    review_enabled: bool = False
    # FastAPI 挂载路径（REST + WS 均在其下）
    review_mount: str = "/asoul-review"
    # 鉴权 token；非空则 REST/WS 需带 ?token=（bot 部署在公网时强烈建议设置）
    review_token: str = ""
    # 消息保留天数；0 = 永久保留
    review_retention_days: int = 0
    # WS 客户端连上时回补的最近消息条数
    review_ws_recent_on_connect: int = 20

    # ── 违禁词拦截（入站消息含违禁词计数 + 黑名单，SUPERUSER 管理）──
    # 总开关：默认开
    violation_enabled: bool = True
    # 用户违禁累计达此次数后自动拉黑
    violation_threshold: int = 3


config = get_plugin_config(Config)
