"""
@Author: star_482
@Date: 2026/5/26
@File: storage
@Description: COS 对象存储模块。提供 COSBucket（静态懒加载 + 配方寻址）+ get_bucket() 单例。
"""
import asyncio
import hashlib
import io
import json
import mimetypes
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from nonebot.log import logger

from . import manifest, cos_client

# 业务前缀：调用方应通过 KEY_PREFIX[...] 取，避免硬编码
KEY_PREFIX = {
    # static 段：按原文件名 key
    "fortune_base": "static/fortune/base",
    "whateat_eat": "static/whateat/eat",
    "whateat_drink": "static/whateat/drink",
    "wife": "static/wife",
    "activity": "static/activity",
    "ui": "static/ui",
    # addressed 段：按 recipe 哈希 key
    "addressed_fortune": "addressed/fortune",
    "addressed_diana_interaction": "addressed/diana/interaction",
    "addressed_diana_event":      "addressed/diana/event",
    "addressed_diana_costume":    "addressed/diana/costume",
    "addressed_diana_status":     "addressed/diana/status",
    "addressed_test": "addressed/test",
}


def _sha256_short_bytes(data: bytes, n: int = 12) -> str:
    return hashlib.sha256(data).hexdigest()[:n]


def _sha256_short_file(path: Path, n: int = 12) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()[:n]


def _recipe_hash(recipe: Any, n: int = 12) -> str:
    canon = json.dumps(recipe, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:n]


def _image_size(data: bytes) -> tuple[int, int]:
    """从 bytes 读宽高。失败返回 (0, 0)，不阻塞主流程。"""
    try:
        from PIL import Image as PILImage
        with PILImage.open(io.BytesIO(data)) as img:
            return img.size
    except Exception as e:
        logger.warning(f"读图片宽高失败：{e}")
        return 0, 0


def _image_size_file(path: Path) -> tuple[int, int]:
    try:
        from PIL import Image as PILImage
        with PILImage.open(path) as img:
            return img.size
    except Exception as e:
        logger.warning(f"读图片宽高失败 {path}：{e}")
        return 0, 0


def _guess_content_type(path: Path) -> str:
    ct, _ = mimetypes.guess_type(str(path))
    return ct or "application/octet-stream"


class COSBucket:
    def __init__(self):
        # 进程内 inflight 锁：防止同一 hash 在并发请求下被多次上传
        self._inflight: dict[str, asyncio.Lock] = {}
        self._inflight_guard = asyncio.Lock()

    # ── URL / Markdown 工具 ──

    def public_url(self, key: str) -> str:
        return cos_client.public_url_for(key)

    def build_md_image(self, url: str, width: int, height: int, alt: str = "") -> str:
        """生成 QQ Markdown 图片字面量：![alt #Wpx #Hpx](url)。宽高必填。"""
        return f"![{alt} #{width}px #{height}px]({url})"

    # ── 低级 API ──

    async def head(self, key: str) -> bool:
        try:
            return await asyncio.to_thread(cos_client.head_object_sync, key)
        except Exception as e:
            logger.warning(f"COS head 失败 key={key}：{e}")
            return False

    async def delete(self, key: str) -> bool:
        try:
            await asyncio.to_thread(cos_client.delete_object_sync, key)
            return True
        except Exception as e:
            logger.warning(f"COS delete 失败 key={key}：{e}")
            return False

    async def upload_bytes(
        self, data: bytes, key: str, *, content_type: str = "image/png"
    ) -> Optional[str]:
        try:
            await asyncio.to_thread(cos_client.put_object_sync, key, data, content_type)
            return self.public_url(key)
        except Exception as e:
            logger.warning(f"COS upload_bytes 失败 key={key}：{e}")
            return None

    # ── 主路径 1：静态文件懒加载 ──

    async def get_or_upload_file(
        self, local_path: Path, *, prefix: str
    ) -> Optional[str]:
        local_path = Path(local_path)
        if not local_path.exists():
            logger.warning(f"本地文件不存在：{local_path}")
            return None

        key = f"{prefix}/{local_path.name}"
        local_sha = _sha256_short_file(local_path)

        cached = manifest.get_static(key)
        if cached and cached.get("sha256_short") == local_sha:
            return cached["url"]

        # sha 漂移（manifest 命中但哈希不匹配）→ 无条件重新上传
        if cached:
            return await self._do_upload_file(local_path, key, local_sha)

        # manifest miss → 尝试从 COS 恢复（HEAD 读元数据），避免重复上传
        meta = await asyncio.to_thread(cos_client.head_object_meta_sync, key)
        if meta is not None:
            width = int(meta.get("width", 420))
            height = int(meta.get("height", 420))
            url = self.public_url(key)
            manifest.put_static(
                key, url=url, width=width, height=height, sha256_short=local_sha
            )
            return url

        return await self._do_upload_file(local_path, key, local_sha)

    async def _do_upload_file(
        self, local_path: Path, key: str, local_sha: str
    ) -> Optional[str]:
        """无条件上传文件到 COS 并写 manifest。"""
        try:
            data = await asyncio.to_thread(local_path.read_bytes)
            content_type = _guess_content_type(local_path)
            width, height = _image_size_file(local_path)
            await asyncio.to_thread(
                cos_client.put_object_sync, key, data, content_type,
                metadata={"width": width, "height": height},
            )
        except Exception as e:
            logger.warning(f"COS upload_file 失败 path={local_path} key={key}：{e}")
            return None

        url = self.public_url(key)
        manifest.put_static(
            key, url=url, width=width, height=height, sha256_short=local_sha
        )
        return url

    # ── 主路径 2：配方寻址（render-on-miss）──

    async def get_or_render(
        self,
        recipe: Any,
        producer: Callable[[], Awaitable[bytes]],
        *,
        prefix: str,
        ext: str = "png",
    ) -> Optional[str]:
        h = _recipe_hash(recipe)

        # 命中：纯本地查表，0 网络、0 渲染
        cached = manifest.get_addressed(h)
        if cached:
            return cached["url"]

        # 同 hash 并发请求合流
        async with self._inflight_guard:
            lock = self._inflight.get(h)
            if lock is None:
                lock = asyncio.Lock()
                self._inflight[h] = lock

        async with lock:
            # 双检：等锁期间可能已被别的协程上传完
            cached = manifest.get_addressed(h)
            if cached:
                return cached["url"]

            # manifest miss → 尝试从 COS 恢复（HEAD 读元数据），跳过重复渲染
            key = f"{prefix}/{h}.{ext}"
            meta = await asyncio.to_thread(cos_client.head_object_meta_sync, key)
            if meta is not None:
                img_w = int(meta.get("width", 420))
                img_h = int(meta.get("height", 420))
                url = self.public_url(key)
                manifest.put_addressed(h, key=key, url=url, width=img_w, height=img_h)
                return url

            try:
                data = await producer()
            except Exception as e:
                logger.warning(f"render producer 失败 hash={h}：{e}")
                return None

            content_type = f"image/{ext}"
            width, height = _image_size(data)
            try:
                await asyncio.to_thread(
                    cos_client.put_object_sync, key, data, content_type,
                    metadata={"width": width, "height": height},
                )
            except Exception as e:
                logger.warning(f"COS get_or_render upload 失败 key={key}：{e}")
                return None

            url = self.public_url(key)
            manifest.put_addressed(h, key=key, url=url, width=width, height=height)

            # 释放 inflight 锁记录（保留锁本身让正在 await 的协程跑完）
            async with self._inflight_guard:
                self._inflight.pop(h, None)

            return url


_bucket: Optional[COSBucket] = None


def get_bucket() -> COSBucket:
    global _bucket
    if _bucket is None:
        _bucket = COSBucket()
    return _bucket


# import admin 触发其中的 on_command 注册（必须放在 get_bucket 定义之后，因为 admin.py 用 get_bucket）
from . import admin as _admin  # noqa: E402,F401


__all__ = ["COSBucket", "get_bucket", "KEY_PREFIX"]
