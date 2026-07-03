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


config = get_plugin_config(Config)
