"""持久化与工具函数."""

import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

from .core import PetState

logger = logging.getLogger(__name__)

# ── diana 包目录（包内的 data/ 与 assets/ 与代码一起发布，saves 在外部）──
_PACKAGE_DIR = Path(__file__).parent.resolve()

# ── 内置默认路径 ──
# data 与 assets 跟着包走（不依赖外部 data_path），是包自带的"只读"内容。
_DEFAULT_DATA_DIR = _PACKAGE_DIR / "data"
_DEFAULT_ASSETS_DIR = _PACKAGE_DIR / "assets"
# saves 默认为 None，由 get_saves_dir() 从 config 懒加载解析。
_DEFAULT_SAVES_DIR = None

# ── 运行时配置（由 configure() 修改）──
_config = {
    "data_dir": _DEFAULT_DATA_DIR,
    "assets_dir": _DEFAULT_ASSETS_DIR,
    "saves_dir": _DEFAULT_SAVES_DIR,
}


def configure(
    data_dir: Optional[Path] = None,
    assets_dir: Optional[Path] = None,
    saves_dir: Optional[Path] = None,
):
    """统一配置所有路径（在 DianaPet 初始化时自动调用）."""
    if data_dir is not None:
        _config["data_dir"] = Path(data_dir)
    if assets_dir is not None:
        _config["assets_dir"] = Path(assets_dir)
    if saves_dir is not None:
        _config["saves_dir"] = Path(saves_dir)


def get_data_dir() -> Path:
    return _config["data_dir"]


def get_assets_dir() -> Path:
    return _config["assets_dir"]


def get_saves_dir() -> Path:
    d = _config["saves_dir"]
    if d is None:
        # 懒加载：从 NoneBot 插件配置解析 saves 路径
        from ..config import config
        d = Path(config.data_path) / config.diana_saves_dir
        _config["saves_dir"] = d
    d.mkdir(parents=True, exist_ok=True)
    return d


def today_str() -> str:
    """返回今天的日期字符串 YYYY-MM-DD."""
    return time.strftime("%Y-%m-%d", time.localtime())


def now_ts() -> float:
    return time.time()


def save_pet(pet: PetState, save_dir: Optional[Path] = None) -> Path:
    """保存宠物状态到 JSON 文件（原子写入）.

    先写到 .tmp 再 os.replace，避免进程崩溃时留下截断的 JSON。
    Windows 上 os.replace 偶尔会因杀软 / 索引服务短暂持有目标文件抛
    PermissionError，所以包了指数退避重试。
    """
    if save_dir is None:
        save_dir = get_saves_dir()
    save_dir.mkdir(parents=True, exist_ok=True)
    filepath = save_dir / f"{pet.user_id}.json"
    tmp_path = filepath.with_suffix(".json.tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(pet.to_dict(), f, ensure_ascii=False, indent=2)

    delay = 0.02
    for attempt in range(6):
        try:
            os.replace(tmp_path, filepath)
            return filepath
        except PermissionError:
            last_err = PermissionError  # noqa
            time.sleep(delay)
            delay *= 2

    # 最终尝试，让它抛出
    try:
        os.replace(tmp_path, filepath)
    except PermissionError:
        logger.warning("Failed to atomic-replace save file for user=%s after retries", pet.user_id)
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


def load_pet(user_id: str, save_dir: Optional[Path] = None) -> Optional[PetState]:
    """从 JSON 文件加载宠物状态.

    不认识的键（如旧版 version）会被 PetState.from_dict 忽略；
    新增字段对旧存档使用默认值。
    """
    if save_dir is None:
        save_dir = get_saves_dir()
    filepath = save_dir / f"{user_id}.json"
    if not filepath.exists():
        return None
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)
    return PetState.from_dict(data)


def delete_pet(user_id: str, save_dir: Optional[Path] = None) -> bool:
    """删除宠物存档."""
    if save_dir is None:
        save_dir = get_saves_dir()
    filepath = save_dir / f"{user_id}.json"
    if filepath.exists():
        os.remove(filepath)
        return True
    return False


def list_pets(save_dir: Optional[Path] = None) -> list[str]:
    """列出所有已保存的宠物 user_id."""
    if save_dir is None:
        save_dir = get_saves_dir()
    if not save_dir.exists():
        return []
    return [f.stem for f in save_dir.glob("*.json")]
