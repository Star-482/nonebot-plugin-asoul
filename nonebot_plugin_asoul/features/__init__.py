"""
@Author: star_482
@Date: 2026/8/7
@File: features
@Description: 用户功能子包--抽老婆/运势/吃什么/日程/发病。导入即触发命令注册。
"""
from . import random_wife, whateat, activity, quotation, group_admin  # noqa: F401
from .fortune_manager import fortune_manager, build_fortune_md, FortuneManager  # noqa: F401
from .whateat import build_whateat_msg  # noqa: F401
from .random_wife import get_random_wife_md_message  # noqa: F401
from .activity import save_img_activity, save_json_activity, get_relative_content  # noqa: F401
