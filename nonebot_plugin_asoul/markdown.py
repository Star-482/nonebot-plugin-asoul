"""
@Author: star_482
@Date: 2026/5/4
@File: markdown
@Description:
"""
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
    content = (
        "# 关于小然\n"
        "![嘉然 Diana #1053px #432px](https://img.cdn1.vip/i/6a04661d8253e_1778673181.png)\n\n"
        "## 嘉然 Diana\n"
        "她是 A-SOUL 玉米霸霸的队短。\n\n"
        "**她也是**\n"
        "> 不太擅长吃辣辣的东西的干饭人\n"
        "> 能宅舞 20 连，但是歌声让人安心的小偶像\n"
        "> 能提醒大家好好吃饭，面对多次节奏仍能抗压的圣嘉然\n\n"
        "**也许你没看过她的直播，但大概听过嘉然相关的梗和嘉心糖的二创**\n"
        "> 如果你想更了解嘉然，就去 B 站给她点个关注吧。\n"
        "> 关注嘉然，顿顿解馋！\n\n"
        "## 小然 Bot\n"
        "面向 A-SOUL 和嘉然粉丝的 QQ 群助手，提供开播提醒、每日运势、小作文等快捷功能。\n\n"
        "参与小然 Bot 的建设[点击投稿](https://docs.qq.com/form/page/DRkhCT0JLaFFJQmdJ) 分享你的想法~\n\n"
    )
    keyboard = MessageKeyboard(
        content=InlineKeyboard(
            rows=[
                InlineKeyboardRow(
                    buttons=[
                        _command_button("week_activity", "订阅开播", "/订阅开播"),
                        _command_button("daily_fortune", "今日运势", "/今日运势"),
                        _command_button("quotation", "发病一下", "/发病小作文"),
                    ]
                ),
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
