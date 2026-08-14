"""
@Author: star_482
@Date: 2026/5/4
@File: markdown
@Description: QQ markdown 消息统一构造层。
- 构造器：command_button / link_button / text_chain / cmd / build_keyboard（对适配器模型的薄封装）。
- 具体定义：BTN_* 按钮常量、TC_* 指令文字链常量、KB_* 常用键盘组合 —— 其他模块直接 import
  这些具名实例，任意组合进自己的 md 消息，统一管理按钮 id / 文案 / 指令。
- 带参按钮工厂：live_go_button / welcome_review_button / live_sub_button / live_sub_all_button。
"""
import urllib.parse
from typing import Literal

from nonebot.adapters.qq import Message, MessageSegment
from nonebot.adapters.qq.models import (
    Action,
    Button,
    InlineKeyboard,
    InlineKeyboardRow,
    MessageKeyboard,
    Permission,
    RenderData,
)

# ── 常用外链常量（统一管理，避免各模块手写 URL）──
URL_USAGE_DOC = "https://docs.qq.com/doc/DRkFEbEhoa1Jzc05r"     # 使用说明
URL_SUBMIT = "https://docs.qq.com/form/page/DRkhCT0JLaFFJQmdJ"  # 点我投稿
URL_GROUP = "https://qm.qq.com/q/bTIMDcbTkA"                    # 交流群
URL_PIXEL_BOARD = "https://pixel-asoul.club/"                   # A手像素画板
URL_LIVE_DATA = "https://live.pixel-asoul.club"                 # 直播数据站


# ══════════════════ 构造器（适配器模型的薄封装） ══════════════════

def cmd(*parts) -> str:
    """拼接指令与参数为一条完整指令字符串，None / 空串参数自动跳过。

    供带参数的指令嵌入使用，如：

    >>> cmd("/审核欢迎语", "同意", "123")
    '/审核欢迎语 同意 123'
    """
    return " ".join(str(p) for p in parts if p not in (None, ""))


def command_button(
    button_id: str,
    label: str,
    command: str,
    *,
    enter: bool = False,
    reply: bool = False,
) -> Button:
    """指令注入按钮：点击将 command 插入输入框，由用户自行发送。

    command 支持带参数，可用 cmd() 拼接（如 cmd("/订阅开播", "嘉然")）。
    enter=True 时点击直接发送（如 whateat 的"换一个"），默认仅插入输入框。
    """
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
            reply=reply,
            enter=enter,
            unsupport_tips=f"请手动发送：{command}",
        ),
    )


def link_button(button_id: str, label: str, url: str) -> Button:
    """外链按钮：点击跳转 url。"""
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


def build_keyboard(rows: list[list[Button]]) -> MessageKeyboard:
    """由二维按钮列表构造 MessageKeyboard，一行一个 InlineKeyboardRow。"""
    return MessageKeyboard(
        content=InlineKeyboard(
            rows=[InlineKeyboardRow(buttons=row) for row in rows]
        )
    )


def text_chain(text: str, show: str = "") -> str:
    """QQ markdown 文字链（指令操作-参数指令）：点击后将 text 插入输入框，展示 show。

    群聊仅支持此 input 形式（点击插入输入框，用户自行发送），不占按钮额度，
    适合在指令中心铺大量指令。text 可带参数（可用 cmd() 拼接），show 默认同 text。
    text/show 需 urlencode（官方文档要求）。
    """
    show = show or text
    return (
        f'<qqbot-cmd-input text="{urllib.parse.quote(text, safe="/")}" '
        f'show="{urllib.parse.quote(show, safe="/")}" reference="false" />'
    )


# ══════════════════ 具体按钮定义（其他模块直接 import 组合） ══════════════════

# ── 通用功能按钮 ──
BTN_TEST_MARKDOWN = command_button("test_markdown", "再测一次", "/测试markdown")
BTN_QUOTATION = command_button("quotation", "发病一下", "/发病小作文")
BTN_FORTUNE_DRAW = command_button("fortune_draw", "我也要抽签", "/今日运势")
BTN_WIFE_AGAIN = command_button("wife_again", "再抽老婆", "/抽老婆")
BTN_QUOTATION_AGAIN = command_button("quotation_again", "再来一篇", "/发病小作文")
BTN_EAT_AGAIN = command_button("whateat_eat_again", "换一个", "/今天吃什么", enter=True)
BTN_DRINK_AGAIN = command_button("whateat_drink_again", "换一个", "/今天喝什么", enter=True)

# ── Diana 玩法导航按钮 ──
BTN_DIANA_STATUS = command_button("diana_nav_status", "看状态", "/然然状态")
BTN_DIANA_COSTUME = command_button("diana_nav_costume", "换装", "/换装")
BTN_DIANA_HELP = command_button("diana_nav_help", "更多玩法", "/然然帮助")
BTN_DIANA_FEED = command_button("diana_nav_feed", "投喂", "/然然帮助 投喂")
BTN_DIANA_PLAY = command_button("diana_nav_play", "玩耍", "/然然帮助 玩耍")
BTN_DIANA_WORK = command_button("diana_nav_work", "打工", "/然然帮助 打工")
BTN_DIANA_INTERACT = command_button("diana_nav_interact", "互动", "/然然帮助 互动")
BTN_DIANA_DAILY = command_button("diana_nav_daily", "日常", "/然然帮助 日常")

# ── 外链按钮 ──
BTN_USAGE_DOC = link_button("introduce", "使用说明", URL_USAGE_DOC)
BTN_SUBMIT = link_button("submit", "点我投稿", URL_SUBMIT)
BTN_GROUP = link_button("group", "交流群", URL_GROUP)
BTN_LIVE_DATA = link_button("live_goto", "查看数据", URL_LIVE_DATA)


# ══════════════════ 指令文字链定义（不占按钮额度的指令嵌入） ══════════════════

TC_FORTUNE = text_chain("/今日运势", "今日运势")
TC_WIFE = text_chain("/抽老婆", "抽老婆")
TC_EAT = text_chain("/今天吃什么", "吃什么")
TC_DRINK = text_chain("/今天喝什么", "喝什么")
TC_QUOTATION = text_chain("/发病小作文", "发病小作文")
TC_CHECKIN = text_chain("/签到", "签到")
TC_STATUS = text_chain("/然然状态", "状态")
TC_COSTUME = text_chain("/换装", "换装")
TC_HELP = text_chain("/然然帮助", "更多玩法")
TC_FEED = text_chain("/投喂 鸡胸肉", "投喂")
TC_PLAY = text_chain("/玩 连连看", "玩耍")
TC_INTERACT = text_chain("/互动 摸摸头", "互动")
TC_SUBSCRIBE = text_chain("/订阅开播", "订阅开播")
TC_SCHEDULE = text_chain("/本周日程", "本周日程")
TC_DISABLE_WELCOME = text_chain("/关闭欢迎语", "关闭入群欢迎")
TC_SET_WELCOME = text_chain("/设置欢迎语 ", "设置欢迎语")


# ══════════════════ 常用键盘组合 ══════════════════

KB_COMMAND_CENTER = build_keyboard([
    [BTN_USAGE_DOC, BTN_SUBMIT, BTN_GROUP],
])

KB_DIANA_NAV = build_keyboard([
    [BTN_DIANA_STATUS, BTN_DIANA_COSTUME, BTN_DIANA_HELP],
    [BTN_DIANA_FEED, BTN_DIANA_PLAY, BTN_DIANA_WORK],
    [BTN_DIANA_INTERACT, BTN_DIANA_DAILY],
])


# ══════════════════ 带参按钮工厂（参数运行时才确定） ══════════════════

def live_go_button(url: str) -> Button:
    """去直播间跳转按钮，url 每次直播动态变化。"""
    return link_button("live_goto", "去直播间", url)


def welcome_review_button(action: Literal["approve", "reject"], rid) -> Button:
    """欢迎语审核 同意/拒绝 按钮，rid 作为参数注入审核指令。"""
    label = {"approve": "同意", "reject": "拒绝"}[action]
    return command_button(
        f"welcome_{action}_{rid}", label, cmd("/审核欢迎语", label, rid)
    )


def live_sub_button(name: str, prefix: str = "/订阅开播") -> Button:
    """订阅/取消订阅单个成员按钮，成员名作为指令参数注入。"""
    return command_button(f"live_sub_{name}", name, cmd(prefix, name))


def live_sub_all_button(names: list[str], prefix: str = "/订阅开播") -> Button:
    """"全部订阅/取消"按钮：把成员名单作为指令参数注入。"""
    label = "全部取消" if "取消" in prefix else "全部订阅"
    return command_button("live_sub_all", label, cmd(prefix, " ".join(names)))


# ══════════════════ 完整 md 消息构造函数 ══════════════════

def get_test_markdown():
    content = (
        "# Markdown 测试\n"
        "这是一条来自 asoul 插件的 markdown 测试消息。\n\n"
        "- 支持列表\n"
        "- 支持 **加粗** 文本\n"
        "- 支持 `行内代码`\n\n"
        "> 如果你能看到格式化内容，说明 markdown 发送正常。"
    )
    keyboard = build_keyboard([[BTN_TEST_MARKDOWN, BTN_QUOTATION]])
    return MessageSegment.markdown(content) + MessageSegment.keyboard(keyboard)


def _xiaoran_command_center_content() -> str:
    """指令中心正文：嘉然立绘 + 标题 + 全部指令文字链。供 about_xiaoran / welcome 复用。"""
    return (
        "![嘉然 Diana #1053px #432px](https://img.cdn1.vip/i/6a04661d8253e_1778673181.png)\n\n"
        "# 📋 小然指令中心\n"
        "嘉然 Diana 的 QQ 群小助手，点一下就能玩～\n\n"
        "## 🍀 每日一抽\n"
        f"{TC_FORTUNE} · {TC_WIFE}\n\n"
        "## 🍱 干饭发病\n"
        f"{TC_EAT} · {TC_DRINK} · {TC_QUOTATION}\n\n"
        "## 🎮 养然然\n"
        f"{TC_CHECKIN} · {TC_STATUS} · {TC_COSTUME} · {TC_HELP}\n"
        f"互动示例：{TC_FEED} · {TC_PLAY} · {TC_INTERACT}\n\n"
        "## 📺 日程订阅\n"
        f"{TC_SUBSCRIBE} · {TC_SCHEDULE}\n\n"
        "## 🎨 二创和数据站\n"
        f"[A手像素画板]({URL_PIXEL_BOARD}) · [直播数据站]({URL_LIVE_DATA})\n\n"
        "更多说明见下方链接～\n"
    )


def get_about_xiaoran_markdown() -> Message:
    """指令中心：用 markdown 文字链列出全部指令（点击插入输入框），按钮额度留给外链。"""
    return (
        MessageSegment.markdown(_xiaoran_command_center_content())
        + MessageSegment.keyboard(KB_COMMAND_CENTER)
    )


def get_welcome_markdown(scene: str) -> Message:
    """加好友/加群欢迎消息：简短欢迎语 + 指令中心正文 + 外链键盘。

    scene: "friend" 或 "group"，决定欢迎语措辞。走被动回复（matcher.send 自动带 event_id）。
    """
    if scene == "friend":
        greeting = "嘉然 Diana 收到你啦～以下是能玩的指令 👇\n\n"
    else:
        greeting = "嘉然 Diana 进群啦～以下是能玩的指令 👇\n\n"
    content = greeting + _xiaoran_command_center_content()
    return (
        MessageSegment.markdown(content)
        + MessageSegment.keyboard(KB_COMMAND_CENTER)
    )


def get_blacklist_md(text: str) -> Message:
    """构造拉黑提示的 md 消息（含交流群按钮，提示误判可联系开发者）。"""
    content = f"{text}\n\n> 如果是误判，可点击下方按钮加入交流群联系开发者。"
    keyboard = build_keyboard([[BTN_GROUP]])
    return MessageSegment.markdown(content) + MessageSegment.keyboard(keyboard)


def get_welcome_review_md(review: dict) -> Message:
    """构造自定义欢迎语审核消息：展示待审核内容 + 同意/拒绝按钮（注入审核命令）。

    review: {id, group_openid, submitter_openid, submitter_role, pending_text}
    按钮 data 为 `/审核欢迎语 同意|拒绝 <id>`（经 cmd() 拼接参数），点击触发 SUPERUSER 审核命令。
    """
    rid = review["id"]
    role = {"admin": "管理员", "owner": "群主"}.get(review.get("submitter_role") or "", "群管")
    content = (
        "# 📢 入群欢迎语审核\n\n"
        f"**群**：`{review['group_openid']}`\n\n"
        f"**提交者**：{role} `{review['submitter_openid']}`\n\n"
        "## 待审核欢迎语\n\n"
        f"> {review['pending_text']}\n\n"
        "请选择是否通过："
    )
    keyboard = build_keyboard([
        [
            welcome_review_button("approve", rid),
            welcome_review_button("reject", rid),
        ]
    ])
    return MessageSegment.markdown(content) + MessageSegment.keyboard(keyboard)


def get_member_welcome_md(text: str, member_openid: str) -> MessageSegment:
    """新成员入群欢迎消息（markdown）。

    text 为本群当前欢迎语（自定义或默认）；member_openid 为新成员 openid，用于 @。
    正文内嵌两条指令文字链（关闭入群欢迎 / 设置欢迎语），非按钮、不占按钮额度。
    """
    content = (
        f"🎉 欢迎新成员<@{member_openid}>\n\n{text}\n\n"
        f"> 群管操作：{TC_DISABLE_WELCOME} · {TC_SET_WELCOME}"
    )
    return MessageSegment.markdown(content)


def _trend_md(value: str) -> str:
    """涨跌值上色（QQ md 走 LaTeX 语法）：正数红、负数绿；"-"（无数据）原样。"""
    if value.startswith("+"):
        return rf'$\textcolor{{#FF0000}}{{\text{{{value}}}}}$'
    if value.startswith("-") and value != "-":
        return rf'$\textcolor{{#27AE60}}{{\text{{{value}}}}}$'
    return value


def get_follower_stats_md(
    rows: list[dict],
    *,
    updated_at: str = "",
    base_hour: int = 6,
    note: str = "",
) -> str:
    """直播数据 · 粉丝数统计 md 正文（供 /粉丝数据 命令使用）。

    rows: [{name, current, today}]，各值为已格式化字符串（"1,234" / "+56" / "—"）。
    updated_at: 数据更新时间文案；base_hour: 每日基准时刻，用于口径标注。
    7天/30天涨粉列暂未开放（历史基准积累后恢复）。
    """
    lines = [
        "## 📊 直播数据 · 粉丝数\n",
        "| 成员 | 当前粉丝 | 今日涨粉 |",
        "| --- | --- | --- |",
    ]
    for r in rows:
        lines.append(f"| {r['name']} | {r['current']} | {_trend_md(r['today'])} |")
        # 7天/30天涨粉：后续开放
        # lines.append(
        #     f"| {r['name']} | {r['current']} | {r['today']} | {r['week']} | {r['month']} |"
        # )
    lines.append("")
    lines.append(f"> 涨粉按每日 {base_hour}:00 基准结算" + (f" · 数据更新于 {updated_at}" if updated_at else ""))
    if note:
        lines.append(f"> {note}")
    return "\n".join(lines)
