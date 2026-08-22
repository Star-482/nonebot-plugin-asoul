"""
@Author: star_482
@Date: 2026/5/26
@File: admin
@Description: COS 图床的 SUPERUSER 管理命令：自检 / 同步 / 查询 / 清单。
"""
import os
from pathlib import Path
from typing import Iterable

from nonebot.adapters.qq import Message, MessageSegment
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from nonebot.plugin.on import on_command

from ..config import config
from . import manifest

# 1×1 透明 PNG，用于自检
_PROBE_PNG = bytes.fromhex(
    "89504E470D0A1A0A0000000D49484452000000010000000108060000001F15C4"
    "890000000A49444154789C6300010000000500010D0A2DB40000000049454E44AE426082"
)
_PROBE_KEY = "static/_healthcheck/probe.png"


def _local_root_for_prefix(prefix: str) -> Path | None:
    """把桶内 prefix 反向映射回本地目录。"""
    data_root = Path(config.data_path)
    mapping: dict[str, Path] = {
        "static/whateat/eat": Path("data/whateat_pic/eat_pic"),
        "static/whateat/drink": Path("data/whateat_pic/drink_pic"),
        "static/wife": data_root / config.wife_img_dir,
        "static/fortune/base": data_root / "resource" / "img" / "asoul",
        "static/ui": data_root / "ui",
        "static/ui/icon": data_root / "ui" / "icons",
        "static/agent/sticker": data_root / "agent" / "stickers",
        "static/agent/image": data_root / "agent" / "images",
    }
    return mapping.get(prefix)


# 默认全量同步的 prefix 列表（覆盖率 = 实际会被业务发出的静态图）
_DEFAULT_SYNC_PREFIXES = [
    "static/whateat/eat",
    "static/whateat/drink",
    "static/wife",
    "static/ui/icon",
    "static/agent/sticker",
    "static/agent/image",
]

_SUPPORTED_IMG_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}


def _iter_images(root: Path) -> Iterable[Path]:
    if not root.exists():
        return []
    return [p for p in sorted(root.iterdir()) if p.is_file() and p.suffix.lower() in _SUPPORTED_IMG_EXT]


def _iter_images_with_prefix(root: Path, prefix: str) -> Iterable[tuple[Path, str]]:
    """展开 (图片, 实际 COS 前缀)：库根文件用 prefix，一级子目录文件用 prefix/子目录名
    （agent 图片库的分类目录布局）。"""
    if not root.exists():
        return []
    pairs: list[tuple[Path, str]] = [(p, prefix) for p in _iter_images(root)]
    for sub in sorted(root.iterdir()):
        if sub.is_dir():
            pairs.extend((p, f"{prefix}/{sub.name}") for p in _iter_images(sub))
    return pairs


# ── 命令注册 ──

healthcheck = on_command(
    "图床自检", priority=config.command_priority, permission=SUPERUSER
)
sync_cmd = on_command(
    "图床同步", priority=config.command_priority, permission=SUPERUSER
)
query_cmd = on_command(
    "图床查询", priority=config.command_priority, permission=SUPERUSER
)
list_cmd = on_command(
    "图床清单", priority=config.command_priority, permission=SUPERUSER
)


# ── 处理器 ──

@healthcheck.handle()
async def _():
    from . import get_bucket

    bucket = get_bucket()
    lines = ["COS 图床自检"]

    url = await bucket.upload_bytes(_PROBE_PNG, _PROBE_KEY, content_type="image/png")
    if not url:
        lines.append("✗ 上传失败，检查 COS 凭据 / endpoint")
        await healthcheck.finish(MessageSegment.text("\n".join(lines)))
    lines.append(f"✓ 上传 OK：{url}")

    exists = await bucket.head(_PROBE_KEY)
    lines.append("✓ HEAD OK" if exists else "✗ HEAD 失败")

    deleted = await bucket.delete(_PROBE_KEY)
    lines.append("✓ 删除 OK" if deleted else "✗ 删除失败")

    await healthcheck.finish(MessageSegment.text("\n".join(lines)))


@sync_cmd.handle()
async def _(arg: Message = CommandArg()):
    from . import get_bucket

    bucket = get_bucket()
    target = arg.extract_plain_text().strip()
    prefixes = [target] if target else list(_DEFAULT_SYNC_PREFIXES)

    lines = [f"图床同步：共 {len(prefixes)} 个前缀"]
    grand_ok = grand_skip = grand_fail = 0

    for prefix in prefixes:
        root = _local_root_for_prefix(prefix)
        if root is None:
            lines.append(f"  [{prefix}] 未注册本地映射，跳过")
            continue
        if not root.exists():
            lines.append(f"  [{prefix}] 目录不存在 {root}，跳过")
            continue

        ok = skip = fail = 0
        for img, file_prefix in _iter_images_with_prefix(root, prefix):
            cached_before = manifest.get_static(f"{file_prefix}/{img.name}")
            url = await bucket.get_or_upload_file(img, prefix=file_prefix)
            if url is None:
                fail += 1
            elif cached_before and cached_before.get("url") == url:
                skip += 1
            else:
                ok += 1

        lines.append(f"  [{prefix}] 上传 {ok} / 跳过 {skip} / 失败 {fail}")
        grand_ok += ok
        grand_skip += skip
        grand_fail += fail

    lines.append(f"合计：上传 {grand_ok} / 跳过 {grand_skip} / 失败 {grand_fail}")
    await sync_cmd.finish(MessageSegment.text("\n".join(lines)))


@query_cmd.handle()
async def _(arg: Message = CommandArg()):
    from . import get_bucket

    key = arg.extract_plain_text().strip()
    if not key:
        await query_cmd.finish(MessageSegment.text("用法：/图床查询 <key>"))

    bucket = get_bucket()
    exists = await bucket.head(key)
    url = bucket.public_url(key)
    flag = "✓ 存在" if exists else "✗ 不存在"
    await query_cmd.finish(MessageSegment.text(f"{flag}\n{url}"))


@list_cmd.handle()
async def _():
    summary = manifest.summary()
    lines = ["COS 图床清单"]
    lines.append("[static]")
    if summary["static"]:
        for bucket_name, count in sorted(summary["static"].items()):
            lines.append(f"  {bucket_name}: {count}")
    else:
        lines.append("  (空)")
    lines.append("[addressed]")
    if summary["addressed"]:
        for bucket_name, count in sorted(summary["addressed"].items()):
            lines.append(f"  {bucket_name}: {count}")
    else:
        lines.append("  (空)")
    await list_cmd.finish(MessageSegment.text("\n".join(lines)))
