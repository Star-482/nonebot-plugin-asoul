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

    # ── 直播数据：粉丝统计（5 人粉丝数，每日基准 + 内存缓存）──
    follower_poll_interval: int = 600    # 每 10 分钟定时刷新内存缓存（不写库）
    follower_cache_ttl: int = 600        # 命令查询时缓存的新鲜度（秒），超时才现场调 API
    follower_base_hour: int = 6          # 每日基准时刻（东八区），当日 6:00 写库

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
    # 强制 JSON 输出：开 = chat.completions 附加 response_format={"type":"json_object"}。
    # 仅当供应商支持时开启（OpenAI 及多数兼容平台支持；Agnes 文档未提及，可开启实测，400 则关掉）
    agent_json_mode: bool = False
    # 思考模式开关（DeepSeek V4 默认开启且 effort=high）。关 = 请求附 thinking.type=disabled，
    # 聊天更快更省；仅对支持该参数的模型生效（DeepSeek 系），其他供应商忽略不开关时无影响
    agent_thinking: bool = True
    # 单次对话工具调用循环最大步数（含工具场景；闲聊通常 1 步）
    agent_max_turns: int = 5
    # 当前上下文消息条数上限，达到后触发摘要压缩（旧消息入 compressed 存档 + 滚动摘要）
    agent_history_limit: int = 30
    # 压缩后保留的最近消息条数（其余 ~limit-keep 条压缩为摘要）
    agent_summary_keep: int = 5
    # 每用户调用冷却（秒），防刷
    agent_user_cd: float = 3.0
    # ── Agent 群聊 ──
    # 群聊总开关；关闭后 agent 只响应 C2C 私聊
    agent_group_enabled: bool = True
    # 是否读取未 @bot 的全量群消息作为短期背景。需要 QQ 平台实际下发 GroupMessageCreateEvent
    agent_group_context_enabled: bool = True
    # 每群短期背景的条数与 TTL；只保存在内存中，不落盘
    agent_group_context_limit: int = 20
    agent_group_context_ttl: float = 600.0
    # 每轮最多识别群背景中最新的几张图片；复用主 Agent 现有视觉模型与描述提示词
    agent_group_context_vision_limit: int = 1
    # 群级调用冷却与排队上限，避免多人同时 @ 造成模型调用洪峰
    agent_group_cd: float = 0.0
    agent_group_queue_limit: int = 3
    # 跨场景最多同时进行的主 Agent 调用数
    agent_max_concurrency: int = 30
    # 人设/功能文档 md，相对 data_path；不存在则用内置占位/跳过
    agent_character_path: str = "agent/character.md"
    agent_plugin_doc_path: str = "agent/plugin_doc.md"
    # 关键记忆 md，相对 data_path；全量注入 system prompt（lookup_memories 工具可精确检索同一份）
    agent_memories_path: str = "agent/memories.md"
    # ── Agent 视觉（用户发图 -> 视觉模型转文字描述并入对话，OpenAI 兼容多模态 API）──
    # 总开关：默认关；需另配视觉模型（DeepSeek 主模型无 vision 能力）
    agent_vision_enabled: bool = False
    agent_vision_model: str = ""       # 如 qwen-vl-plus / glm-4v-flash / gpt-4o-mini
    agent_vision_base_url: str = ""    # 空 = 复用 agent_base_url（视觉与主模型同供应商时方便）
    agent_vision_api_key: str = ""     # 空 = 复用 agent_api_key

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
    # 黑名单用户可主动发起一次 AI 复核；默认开启，开启后会把违禁历史发送到 agent 配置的 API
    violation_ai_review_enabled: bool = True
    # 空 = 复用 agent_model；可单独指定更适合内容审核的模型
    violation_ai_review_model: str = ""
    # 单次复核最多发送的最近违禁记录数，避免异常历史占满模型上下文
    violation_ai_review_max_records: int = 20

    # ── 欢迎消息（加好友/加群事件触发，被动回复小然指令中心）──
    # 总开关：默认开
    welcome_enabled: bool = True

    # ── 新成员入群欢迎（GroupMemberAddEvent 触发；群主/管理员可开关+自定义，自定义经 SUPERUSER 复核，不通过回退默认）──
    # 总开关：默认开
    member_welcome_enabled: bool = True
    # 默认欢迎语（群未自定义或审核被拒回退时使用）
    member_welcome_default_text: str = "欢迎加入本群～我是嘉然 Diana"


config = get_plugin_config(Config)
