"""
@Author: star_482
@Date: 2025/3/28
@File: __init__.py
@Description:
"""
from nonebot.plugin import PluginMetadata
from nonebot.plugin.on import on_command

from .config import config, Config
from . import start_up as _
from . import storage as _storage
from . import live_subscription as _live_subscription
from . import features as _features
from . import manage as _manage
from . import agent as _agent
from . import message_review as _message_review
from .diana import commands as _diana_commands
from .markdown import get_about_xiaoran_markdown, get_test_markdown

__plugin_meta__ = PluginMetadata(
    name="asoul插件",
    description="提供与asoul相关服务",
    usage="待定",
    type="application",
    config=Config,
    extra={},
)

test_markdown = on_command("测试markdown", aliases={"测试md"}, priority=config.command_priority)
about_xiaoran = on_command("关于小然", aliases={"小然", "关于然然", "菜单", "帮助", "指令"}, priority=config.command_priority)


@test_markdown.handle()
async def _():
    message = get_test_markdown()
    await test_markdown.finish(message)


@about_xiaoran.handle()
async def _():
    message = get_about_xiaoran_markdown()
    await about_xiaoran.finish(message)
