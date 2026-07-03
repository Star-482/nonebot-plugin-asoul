# nonebot-plugin-asoul

面向 A-SOUL 及嘉然（Diana）粉丝的 NoneBot2 插件，提供发病小作文、每日运势、B 站开播订阅等功能，适配 QQ 官方机器人，基于腾讯云 COS 图床（S3 兼容协议）实现 Markdown 消息。

---

## 功能一览

### 常用指令

| 指令 | 别名 | 说明 |
| --- | --- | --- |
| `/发病小作文` | `/发病` | 随机发送一篇嘉然小作文（Markdown 卡片） |
| `/今日运势` | `/抽签` | 每人每日一次，生成专属运势卡（Markdown 卡片） |
| `/本周日程` | `/日程` | 查看今天与明天的安排，附日程图 |
| `/关于小然` | `/小然`、`/关于然然` | 嘉然 & Bot 介绍（Markdown + 内联按钮） |
| `/抽老婆` | — | 随机抽取一张图片（Markdown 卡片） |
| `/今天吃什么` | `今/明/早/晚吃什么` 等正则匹配 | 随机推荐美食（Markdown 卡片） |
| `/今天喝什么` | 同上喝法 | 随机推荐饮品（Markdown 卡片） |
| `/订阅开播` | `/开播通知` | 订阅指定成员的 B 站开播通知（按钮式引导） |
| `/取消订阅` | `/退订直播` | 取消当前群的开播订阅 |
| `/订阅列表` | — | 查看当前群已订阅的成员 |
| `/我的id` | — | 查看自己在 QQ 官方机器人下的 openid |

### 嘉然宠物养成

基于属性衰减 + 等级 + 服装解锁的轻量养成系统。指令按功能分组：

| 类别 | 指令 | 别名 | 说明 |
| --- | --- | --- | --- |
| 状态 | `/然然状态` | `状态`、`我的然然`、`然然信息` | 查看宠物状态卡片 |
| 衣柜 | `/然然衣柜` | `服装`、`衣柜`、`换装列表` | 查看已拥有服装 |
| 喂食 | `/喂食 <食物>` | `吃`、`喂`、`投喂` | 喂食以恢复饱腹度 |
| 玩耍 | `/玩耍 <游戏>` | `玩` | 玩耍提升心情 |
| 打工 | `/打工 <工作>` | `直播`、`工作` | 打工赚取嘉心糖币 |
| 换装 | `/换装 [服装名]` | `换上`、`穿` | 换装，不填则随机 |
| 解锁 | `/解锁 [服装名]` | `购买` | 查看或购买服装 |
| 互动 | `/互动 <动作>` | `撒娇`、`和然然互动` | 社交互动提升亲密度 |
| 日常 | `/日常 <活动>` | `日常活动` | 日常活动 |
| 聊天 | `/然然 <话题>` | `然然聊天` | 和然然聊天 |
| 签到 | `/签到` | `嘉心糖签到`、`每日签到` | 每日签到，按全用户排名分层奖励 |
| 帮助 | `/然然帮助` | `宠物帮助`、`然然指令` | 查看所有指令 |

签到奖励按全用户排名分层：🥇 第 1 名 50 币 / 🥈 第 2-3 名 30 币 / 🥉 第 4-10 名 20 币 / 第 11 名起 10 币。连续签到天数独立于互动 streak，每日排名数据按日分文件保存在 `diana/checkin/` 下。

底层设计与模块结构见 `nonebot_plugin_asoul/diana/API_REFERENCE.md`。

### 管理员（SUPERUSER）

- `/添加日程` — 发图片即更新本周日程图；发 JSON 文本即合并到 `activity.json`
- `/统计总览`、`/统计排行`、`/统计明细` — 查看插件命令使用情况
- `/订阅全览` — 跨群查看所有开播订阅
- `/图床自检` — 验证 COS 凭据 / endpoint / 公网 URL 三件套
- `/图床同步 [前缀]` — 把本地静态图懒加载到 COS，无参数则同步默认前缀（吃喝、抽老婆）
- `/图床查询 <key>` — HEAD 检查并返回公网 URL
- `/图床清单` — 输出已上传对象的分前缀汇总

---

## 安装

本仓库是一个 NoneBot2 插件包，不包含运行时（无 `pyproject.toml`/`requirements.txt`），需要放入宿主 NoneBot 项目中使用。

### 1. 依赖

宿主项目需安装：

```bash
pip install nonebot2 nonebot-adapter-qq nonebot-plugin-alconna
pip install httpx pillow pyyaml jinja2 playwright boto3
playwright install chromium
```

> `playwright + chromium` 仅在使用嘉然宠物相关图片渲染时需要；`boto3` 仅在使用 COS 图床功能时需要。

### 2. 安装插件

将本仓库 `nonebot_plugin_asoul/` 目录放入宿主项目 `plugins/`，或在 `bot.py` 中加载：

```python
import nonebot
nonebot.load_plugin("nonebot_plugin_asoul")
```

### 3. 放置数据目录

将本仓库的 `data/` 目录复制到宿主项目的工作目录下。默认数据根为 `./data/asoul/`，主要内容：

```
data/
├── asoul/
│   ├── quotation.json            # 发病小作文（首次启动会自动下载）
│   ├── activity/                 # 周日程 (new_activity.jpg + activity.json)
│   ├── live_subscription/        # 开播订阅持久化（自动生成）
│   │   ├── upstreams.json        # 预定义 up主 UID → 名称
│   │   └── subscriptions.json    # 群订阅关系
│   ├── diana/
│   │   ├── saves/                # 嘉然宠物用户存档（自动生成，每用户一个 JSON）
│   │   └── checkin/              # 签到排名数据（自动生成，按日期分文件）
│   ├── resource/
│   │   ├── font/                 # Mamelon.otf / sakura.ttf
│   │   ├── fortune/              # copywriting.json（首次启动前需就位）
│   │   ├── img/asoul/            # 抽签底图
│   │   └── out/                  # 抽签结果输出目录（自动创建）
│   ├── wife_img/                 # 抽老婆图片池
│   └── stats/                    # 命令统计（自动写入）
└── whateat_pic/
    ├── eat_pic/                  # 吃什么图片池
    └── drink_pic/                # 喝什么图片池
```

> ⚠ `whateat`（今天吃/喝什么）的图片目录写死为 `./data/whateat_pic`，相对启动时的工作目录，不受 `data_path` 配置影响。

---

## 配置项

在宿主项目 `.env` 中配置（或直接使用默认值）：

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| `data_path` | `./data/asoul` | 数据根目录 |
| `wife_img_dir` | `wife_img` | 抽老婆图片子目录 |
| `command_priority` | `15` | 所有指令的统一优先级 |
| `home_url` | 上游仓库地址 | 关于页链接 |
| `whateat_cd` | `10` | 吃 / 喝什么的全局冷却（秒） |
| `whateat_max` | `0` | 每用户每日上限，`0` 表示不限 |
| `live_poll_interval` | `60` | B 站开播轮询间隔（秒） |
| `live_poll_http_timeout` | `10.0` | B 站开播轮询 HTTP 超时（秒） |
| `diana_saves_dir` | `diana/saves` | 嘉然宠物用户存档子目录（相对于 data_path） |
| `cos_id` / `cos_key` | — | 腾讯云 COS 的 SecretId / SecretKey（S3 兼容凭据） |
| `cos_url` | — | COS endpoint，形如 `https://cos.<region>.myqcloud.com` |
| `cos_bucket_name` | `diana-image` | COS 桶名 |
| `cos_public_url` | — | 公网访问域（CDN 或 COS 静态网站），用于拼出图片直链 |
| `cos_region` | `ap-guangzhou` | COS 桶所在区域（SigV4 签名必需） |

> `diana_saves_dir` 控制嘉然宠物用户存档位置，默认 `diana/saves`（相对于 `data_path`）；签到排名数据固定写在 `diana/checkin/` 下。嘉然宠物的 data（YAML/模板）与 assets（服装立绘）已打包在 `nonebot_plugin_asoul/diana/` 包内，不再需要额外的外部配置。

---

## 适配器与消息

- **目标适配器**：`nonebot.adapters.qq`（QQ 官方机器人）
- 部分处理器直接使用 `GroupAtMessageCreateEvent`，仅在 QQ 群 @ 机器人 时可触发
- 插件优先使用 QQ 适配器原生 `MessageSegment`（如 `MessageSegment.markdown()`、`MessageSegment.keyboard()`），以充分使用 QQ 官方机器人的能力；仅在确实需要跨适配器或额外段类型时使用 `nonebot_plugin_alconna.uniseg.UniMessage`

---

## 项目结构

```
nonebot_plugin_asoul/
├── __init__.py              # 命令注册入口，通过 import 拉起各子模块
├── config.py                # Pydantic 插件配置
├── start_up.py              # on_startup 钩子：缺失的 quotation.json 自动下载
├── admin_stats.py           # 全局 pre/post processor 统计命令使用情况
├── activity.py              # 周日程读写
├── fortune_manager.py       # 抽签（每日每用户每群一次，COS 配方缓存）
├── random_wife.py           # 抽老婆（Markdown 卡片）
├── markdown.py              # QQ Markdown + 内联键盘
├── whateat.py               # 今天吃/喝什么（Markdown 卡片）
├── utils.py                 # JSON 读写、图片下载、抽签合成
├── live_subscription/       # B 站开播订阅包
│   ├── __init__.py          # 注册定时轮询 + 管理命令
│   ├── admin.py             # 订阅开播 / 取消订阅 / 列表 / 全览
│   ├── api.py               # B 站直播 API 封装（批量查询 up 主直播状态）
│   ├── checker.py           # 状态比对与开播检测（带两阶段确认）
│   ├── manager.py           # 订阅数据持久化（upstreams + 群订阅）
│   └── notifier.py          # QQ Markdown 通知发送（封面图 + 标题）
├── storage/                 # 腾讯云 COS 图床（boto3，S3 兼容）
│   ├── __init__.py          # COSBucket 公开 API：get_or_upload_file / get_or_render
│   ├── admin.py             # SUPERUSER 管理命令
│   ├── cos_client.py        # boto3 S3 兼容客户端单例
│   └── manifest.py          # 本地缓存索引（static / addressed 两段）
└── diana/                   # 嘉然宠物核心引擎
    ├── __init__.py          # 版本号 + 拉取 commands 注册
    ├── commands.py          # NoneBot 命令注册与 handler（签到逻辑在此）
    ├── session.py           # DianaSession 会话管理、互动管线、post-action hooks
    ├── core.py              # PetState 状态、属性衰减、等级系统
    ├── items.py             # Item / ItemRegistry，YAML 驱动的交互物品
    ├── events.py            # 事件系统（随机/关键词/日期/成就）+ EventManager
    ├── dialogues.py         # DialogueSet 对话池 + 防重复选择
    ├── costumes.py          # 服装系统（解锁/换装/衣柜卡片）
    ├── renderer.py          # Playwright + Jinja2 HTML→PNG 渲染
    ├── utils.py             # 持久化（save_pet/load_pet/list_pets）、路径管理
    ├── exceptions.py        # 异常定义（DianaError 等）
    └── data/                # 包内只读数据（YAML/HTML 模板）
```

---

## 致谢

本项目的部分功能基于以下开源项目修改而来：

- **抽签/运势**（`fortune_manager.py`）基于 [MinatoAquaCrews/nonebot_plugin_fortune](https://github.com/MinatoAquaCrews/nonebot_plugin_fortune)
- **今天吃/喝什么**（`whateat.py`）基于 [Cvandia/nonebot-plugin-whateat-pic](https://github.com/Cvandia/nonebot-plugin-whateat-pic)

感谢原作者的贡献。

## 上游

主仓库（含资源文件初始模板）：
<https://github.com/whalefall123456/nonebot-plugin-asoul>
