"""
@Author: star_482
@Date: 2026/8/20
@File: media
@Description: Agent 表情包/图片库。两个独立素材库：表情包（聊天情绪小图，扁平目录）、
图片库（主题图，一级子目录=分类）。手动放文件维护；索引 JSON 可选
（data/asoul/agent/{stickers,images}_index.json，key 为库内相对路径，
值为 {"tags": [...], "desc": "..."}），未登记的文件用文件名兜底生成标签。
发送走 COS：get_or_upload_file 懒上传（manifest 缓存，重复发送零网络），
失败降级本地 file_image。每次调用时重新扫描目录，新放文件无需重启即可生效。
"""
import json
import random
import re
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from nonebot.adapters.qq import MessageSegment
from nonebot.log import logger

from ..config import config
from ..storage import get_bucket, KEY_PREFIX

_IMG_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}

# 文件名兜底标签的分隔符：`然然_开心_笑.jpg` -> [然然, 开心, 笑, 然然_开心_笑(整名)]
_STEM_SPLIT = re.compile(r"[\s_\-·]+")


@dataclass(frozen=True)
class MediaItem:
    path: Path
    rel: str  # 库内相对路径（正斜杠），同时作索引 key
    name: str  # 文件名（含扩展名）
    category: str  # 一级子目录名；库根下的文件为 ""
    tags: list[str]


class MediaLibrary:
    """一个素材库：扫描、索引、标签匹配、COS 上传与发送段构造。"""

    def __init__(self, root: Path, index_path: Path, prefix: str):
        self._root = root
        self._index_path = index_path
        self._prefix = prefix  # KEY_PREFIX 里的 COS 前缀
        # 近期已发送文件名（避免短时间反复发同一张），进程内状态，重启即忘
        self._recent: deque[str] = deque(maxlen=8)

    # ── 扫描 ──

    def _load_index(self) -> dict:
        """读索引 JSON；不存在/损坏返回空 dict（索引是可选的）。"""
        if not self._index_path.exists():
            return {}
        try:
            data = json.loads(self._index_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError) as e:
            logger.warning(f"读取素材索引失败 {self._index_path}：{e}")
            return {}

    def _make_item(self, path: Path, category: str, index: dict) -> MediaItem:
        rel = path.relative_to(self._root).as_posix()
        entry = index.get(rel) or index.get(path.name) or {}
        tags = [str(t) for t in entry.get("tags", []) if str(t).strip()]
        if not tags:
            # 兜底：文件名按分隔符拆词 + 整个 stem 作为一个标签；分类名也算标签
            stem = path.stem
            tags = [p for p in _STEM_SPLIT.split(stem) if p] or [stem]
        if category:
            tags.append(category)
        return MediaItem(
            path=path,
            rel=rel,
            name=path.name,
            category=category,
            tags=tags,
        )

    def scan(self) -> list[MediaItem]:
        """扫描库目录（库根 + 一级子目录），返回全部图片项。目录不存在返回空列表。"""
        if not self._root.is_dir():
            return []
        index = self._load_index()
        items: list[MediaItem] = []

        def _add_dir(d: Path, category: str) -> None:
            if not d.is_dir():
                return
            for p in sorted(d.iterdir()):
                if p.is_file() and p.suffix.lower() in _IMG_EXT:
                    items.append(self._make_item(p, category, index))

        _add_dir(self._root, "")
        for sub in sorted(self._root.iterdir()):
            if sub.is_dir():
                _add_dir(sub, sub.name)
        return items

    # ── 标签与匹配 ──

    def tags(self) -> list[str]:
        """全部可用标签（按小写去重、保原拼写，排序），供工具描述展示。"""
        seen: dict[str, str] = {}
        for it in self.scan():
            for t in it.tags:
                seen.setdefault(t.lower(), t)
        return sorted(seen.values())

    @staticmethod
    def _score(item: MediaItem, q: str) -> int:
        """查询词与素材的匹配分：精确标签 3 > 标签互含 2 > 描述/文件名含 1 > 不匹配 0。"""
        best = 0
        for tag in item.tags:
            tl = tag.lower()
            if tl == q:
                return 3
            if q in tl or tl in q:
                best = max(best, 2)
        if q in item.path.stem.lower():
            best = max(best, 1)
        return best

    def pick(self, query: str) -> MediaItem | None:
        """按查询词挑一张素材：最高分组内随机，优先避开近期已发送。无匹配返回 None。"""
        q = (query or "").strip().lower()
        if not q:
            return None
        items = self.scan()
        if not items:
            return None
        scored = [(self._score(it, q), it) for it in items]
        top = max(s for s, _ in scored)
        if top <= 0:
            return None
        candidates = [it for s, it in scored if s == top]
        fresh = [it for it in candidates if it.name not in self._recent]
        chosen = random.choice(fresh or candidates)
        self._recent.append(chosen.name)
        return chosen

    # ── 发送 ──

    async def build_segment(self, item: MediaItem) -> MessageSegment | None:
        """构造发送段：优先 COS URL（懒上传 + manifest 缓存），失败降级本地文件。"""
        bucket = get_bucket()
        prefix = f"{self._prefix}/{item.category}" if item.category else self._prefix
        url = await bucket.get_or_upload_file(item.path, prefix=prefix)
        if url:
            return MessageSegment.image(url)
        logger.warning(f"素材 COS 上传失败，降级本地发送：{item.path}")
        try:
            return MessageSegment.file_image(item.path)
        except Exception as e:
            logger.error(f"素材本地发送也失败 {item.path}：{e}")
            return None


# ── 两个库的单例（agent 工具用）──

_agent_root = Path(config.data_path) / "agent"

sticker_library = MediaLibrary(
    root=_agent_root / "stickers",
    index_path=_agent_root / "stickers_index.json",
    prefix=KEY_PREFIX["agent_sticker"],
)

image_library = MediaLibrary(
    root=_agent_root / "images",
    index_path=_agent_root / "images_index.json",
    prefix=KEY_PREFIX["agent_image"],
)
