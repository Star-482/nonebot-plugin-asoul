"""
@Author: star_482
@Date: 2026/5/26
@File: manifest
@Description: COS 上传索引（本地缓存）。两段式：static 按 key、addressed 按 recipe 哈希。
"""
import json
import os
import time
from datetime import datetime, timezone
from threading import Lock
from typing import Optional

from nonebot.log import logger

from ..config import config

MANIFEST_FILENAME = "cos_manifest.json"

_manifest: Optional[dict] = None
_lock = Lock()


def _manifest_path() -> str:
    return os.path.join(config.data_path, MANIFEST_FILENAME)


def _empty() -> dict:
    return {"static": {}, "addressed": {}}


def _load() -> dict:
    path = _manifest_path()
    if not os.path.exists(path):
        return _empty()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        data.setdefault("static", {})
        data.setdefault("addressed", {})
        return data
    except Exception as e:
        logger.warning(f"manifest 读取失败，使用空 manifest：{e}")
        return _empty()


def _ensure_loaded() -> dict:
    global _manifest
    if _manifest is None:
        with _lock:
            if _manifest is None:
                _manifest = _load()
    return _manifest


def _flush():
    """原子写入：先写到 .tmp 再 rename，避免崩溃时损坏 manifest.

    Windows 上 os.replace 偶尔会因杀软/同步盘/索引服务短暂持有目标文件抛 PermissionError，
    所以包了一层指数退避重试。最多 6 次约 1.5s 总时长，仍失败才放弃。
    """
    data = _ensure_loaded()
    path = _manifest_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    last_err: Optional[OSError] = None
    delay = 0.02
    for attempt in range(6):
        try:
            os.replace(tmp_path, path)
            return
        except PermissionError as e:
            last_err = e
            time.sleep(delay)
            delay *= 2

    logger.warning(f"manifest 写入重试 6 次仍失败：{last_err}")
    # 兜底：尝试直接删除 .tmp，避免下次启动残留
    try:
        os.remove(tmp_path)
    except OSError:
        pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _safe_flush():
    """flush 包装：任何异常只 warning，不上抛。
    内存中的 manifest 已经更新，落盘失败只意味着下次启动看不到这次的更新。
    """
    try:
        _flush()
    except Exception as e:
        logger.warning(f"manifest 落盘失败（内存仍生效）：{e}")


# ── static 段 ──

def get_static(key: str) -> Optional[dict]:
    return _ensure_loaded()["static"].get(key)


def put_static(key: str, *, url: str, width: int, height: int, sha256_short: str):
    with _lock:
        manifest = _ensure_loaded()
        manifest["static"][key] = {
            "url": url,
            "width": width,
            "height": height,
            "sha256_short": sha256_short,
            "uploaded_at": _now_iso(),
        }
        _safe_flush()


# ── addressed 段 ──

def get_addressed(recipe_hash: str) -> Optional[dict]:
    return _ensure_loaded()["addressed"].get(recipe_hash)


def put_addressed(recipe_hash: str, *, key: str, url: str, width: int, height: int):
    with _lock:
        manifest = _ensure_loaded()
        manifest["addressed"][recipe_hash] = {
            "key": key,
            "url": url,
            "width": width,
            "height": height,
            "uploaded_at": _now_iso(),
        }
        _safe_flush()


# ── 摘要（admin 命令用）──

def summary() -> dict:
    """按业务前缀汇总各段对象数量。bucket 取 key 去掉文件名后剩下的目录部分。

    例：
      static/whateat/eat/abc.jpg     → bucket "static/whateat/eat"
      addressed/fortune/abcdef.png   → bucket "fortune"
    """
    manifest = _ensure_loaded()
    out: dict[str, dict[str, int]] = {"static": {}, "addressed": {}}
    for key in manifest["static"]:
        bucket = key.rsplit("/", 1)[0] if "/" in key else key
        out["static"][bucket] = out["static"].get(bucket, 0) + 1
    for entry in manifest["addressed"].values():
        key = entry.get("key", "")
        parts = key.split("/")
        bucket = parts[1] if len(parts) >= 2 else "(unknown)"
        out["addressed"][bucket] = out["addressed"].get(bucket, 0) + 1
    return out
