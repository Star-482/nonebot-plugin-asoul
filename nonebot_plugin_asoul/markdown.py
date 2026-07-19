"""
@Author: star_482
@Date: 2026/5/4
@File: markdown
@Description:
"""
import urllib.parse

from nonebot.adapters.qq import MessageSegment
from nonebot.adapters.qq.models import (
    Action,
    Button,
    InlineKeyboard,
    InlineKeyboardRow,
    MessageKeyboard,
    Permission,
    RenderData,
)


def _command_button(button_id: str, label: str, command: str) -> Button:
    return Button(
        id=button_id,
        render_data=RenderData(
            label=label,
            visited_label=label,
            style=1,
        ),
        action=Action(
            type=2,
            permission=Permission(type=2),
            data=command,
            reply=False,
            enter=False,
            unsupport_tips=f"请手动发送：{command}",
        ),
    )


def _link_button(button_id: str, label: str, url: str) -> Button:
    return Button(
        id=button_id,
        render_data=RenderData(
            label=label,
            visited_label=label,
            style=1,
        ),
        action=Action(
            type=0,
            permission=Permission(type=2),
            data=url,
            unsupport_tips=f"请手动打开：{url}",
        ),
    )


def _text_chain(text: str, show: str = "") -> str:
    """QQ markdown 文字链（指令操作-参数指令）：点击后将 text 插入输入框，展示 show。

    群聊仅支持此 input 形式（点击插入输入框，用户自行发送），不占按钮额度，
    适合在指令中心铺大量指令。text/show 需 urlencode（官方文档要求）。
    """
    show = show or text
    return (
        f'<qqbot-cmd-input text="{urllib.parse.quote(text, safe="/")}" '
        f'show="{urllib.parse.quote(show, safe="/")}" reference="false" />'
    )


def get_test_markdown():
    content = (
        "# Markdown 测试\n"
        "这是一条来自 asoul 插件的 markdown 测试消息。\n\n"
        "- 支持列表\n"
        "- 支持 **加粗** 文本\n"
        "- 支持 `行内代码`\n\n"
        "> 如果你能看到格式化内容，说明 markdown 发送正常。"
    )
    keyboard = MessageKeyboard(
        content=InlineKeyboard(
            rows=[
                InlineKeyboardRow(
                    buttons=[
                        _command_button("test_markdown", "再测一次", "/测试markdown"),
                        _command_button("quotation", "发病一下", "/发病小作文"),
                    ]
                )
            ]
        )
    )
    return MessageSegment.markdown(content) + MessageSegment.keyboard(keyboard)


def get_about_xiaoran_markdown():
    """指令中心：用 markdown 文字链列出全部指令（点击插入输入框），按钮额度留给外链。"""
    content = (
        "![嘉然 Diana #1053px #432px](https://img.cdn1.vip/i/6a04661d8253e_1778673181.png)\n\n"
        "# 📋 小然指令中心\n"
        "嘉然 Diana 的 QQ 群小助手，点一下就能玩～\n\n"
        "## 🍀 每日一抽\n"
        f"{_text_chain('/今日运势', '今日运势')} · {_text_chain('/抽老婆', '抽老婆')}\n\n"
        "## 🍱 干饭发病\n"
        f"{_text_chain('/今天吃什么', '吃什么')} · {_text_chain('/今天喝什么', '喝什么')} · "
        f"{_text_chain('/发病小作文', '发病小作文')}\n\n"
        "## 🎮 养然然\n"
        f"{_text_chain('/签到', '签到')} · {_text_chain('/然然状态', '状态')} · "
        f"{_text_chain('/换装', '换装')} · {_text_chain('/然然帮助', '更多玩法')}\n"
        f"互动示例：{_text_chain('/投喂 鸡胸肉', '投喂')} · {_text_chain('/玩 连连看', '玩耍')} · "
        f"{_text_chain('/互动 摸摸头', '互动')}\n\n"
        "## 📺 日程订阅\n"
        f"{_text_chain('/订阅开播', '订阅开播')} · {_text_chain('/本周日程', '本周日程')}\n\n"
        "更多说明见下方链接～\n"
    )
    keyboard = MessageKeyboard(
        content=InlineKeyboard(
            rows=[
                InlineKeyboardRow(
                    buttons=[
                        _link_button("introduce", "使用说明", "https://docs.qq.com/doc/DRkFEbEhoa1Jzc05r"),
                        _link_button("submit", "点我投稿", "https://docs.qq.com/form/page/DRkhCT0JLaFFJQmdJ"),
                        _link_button("group", "交流群", "https://qm.qq.com/q/bTIMDcbTkA"),
                    ]
                ),
            ]
        )
    )
    return MessageSegment.markdown(content) + MessageSegment.keyboard(keyboard)
