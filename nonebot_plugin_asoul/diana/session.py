"""DianaSession — 每用户宠物会话编排器.

替代旧 DianaPet + api.py，提供：
- 统一互动管道 interact()
- 装饰器驱动的后置钩子
- 耗时/忙碌管理
- talk / status / tick / 服装方法
"""

import asyncio
import logging
import random
import time
from pathlib import Path
from typing import Optional

from jinja2 import TemplateError

from .core import PetState
from .items import ItemRegistry, Item
from .events import EventRegistry
from .dialogues import DialogueRegistry
from .costumes import CostumeService
from .renderer import ImageRenderer
from .utils import (
    load_pet, save_pet, today_str,
    configure as _configure_paths,
)
from .exceptions import (
    DianaError, ActionNotFoundError, CostumeNotFoundError,
)
from ..storage import get_bucket, KEY_PREFIX, manifest, _recipe_hash

logger = logging.getLogger(__name__)

# ── 渲染异常白名单 ──
try:
    from playwright.async_api import Error as PlaywrightError
    _RENDER_EXCEPTIONS: tuple[type[BaseException], ...] = (
        PlaywrightError, TemplateError, OSError, asyncio.TimeoutError, ImportError,
    )
except ImportError:
    _RENDER_EXCEPTIONS = (TemplateError, OSError, asyncio.TimeoutError, ImportError)

# ── 渲染并发控制 ──
MAX_CONCURRENT_RENDERS = 2

# ── 共享服务（模块级单例）──
_initialized = False
_item_registry: Optional[ItemRegistry] = None
_event_registry: Optional[EventRegistry] = None
_dialogue_registry: Optional[DialogueRegistry] = None
_costume_registry: Optional[CostumeService] = None
_renderer: Optional[ImageRenderer] = None
_render_semaphore: Optional[asyncio.Semaphore] = None
_user_locks: dict[str, asyncio.Lock] = {}


def _init_shared_services(
    data_dir: Path,
    assets_dir: Path,
    saves_dir: Path,
) -> None:
    """初始化所有共享服务（仅首次调用生效）."""
    global _initialized, _item_registry, _event_registry
    global _dialogue_registry, _costume_registry, _renderer, _render_semaphore

    if _initialized:
        return

    _configure_paths(data_dir=data_dir, assets_dir=assets_dir, saves_dir=saves_dir)

    _dialogue_registry = DialogueRegistry(data_dir)
    _item_registry = ItemRegistry(data_dir, _dialogue_registry)
    _event_registry = EventRegistry(data_dir, _dialogue_registry)
    _costume_registry = CostumeService(data_dir)
    _renderer = ImageRenderer(
        template_dir=data_dir / "templates",
        data_dir=data_dir,
        assets_dir=assets_dir,
    )
    _render_semaphore = asyncio.Semaphore(MAX_CONCURRENT_RENDERS)
    _initialized = True


def _ensure_services(
    data_dir: Optional[Path] = None,
    assets_dir: Optional[Path] = None,
    saves_dir: Optional[Path] = None,
) -> None:
    """确保共享服务已初始化；未初始化时用默认路径触发."""
    if _initialized:
        return
    package_dir = Path(__file__).parent.resolve()
    if data_dir is None:
        data_dir = package_dir / "data"
    if assets_dir is None:
        assets_dir = package_dir / "assets"
    if saves_dir is None:
        from ..config import config
        saves_dir = Path(config.data_path) / config.diana_saves_dir
    _init_shared_services(data_dir, assets_dir, saves_dir)


async def shutdown() -> None:
    """关闭共享资源（进程退出时调用）."""
    global _initialized, _renderer, _render_semaphore
    if _renderer:
        await _renderer.close()
        _renderer = None
    _render_semaphore = None
    _initialized = False


def _get_user_lock(user_id: str) -> asyncio.Lock:
    """获取或创建每用户互斥锁."""
    return _user_locks.setdefault(user_id, asyncio.Lock())


# ===================================================================
# DianaSession
# ===================================================================

class DianaSession:
    """每用户宠物会话——编排动作执行、事件检测、渲染、持久化.

    设计要点：
    - 所有互动走统一的 interact() 管道，无 category 特判
    - 耗时（duration）以忙碌锁实现
    - 后置钩子通过 @DianaSession.on_post_action 注册，按序执行
    - 换装 / talk / status / tick 不经过互动管道
    """

    _post_action_hooks: list[callable] = []

    @classmethod
    def on_post_action(cls, hook: callable):
        """装饰器：注册后置钩子.

        钩子签名: (session: DianaSession, item: Item, result: dict) -> None
        按注册顺序执行。
        """
        cls._post_action_hooks.append(hook)
        return hook

    def __init__(self, user_id: str):
        _ensure_services()
        self.user_id = user_id
        self.pet = load_pet(user_id) or PetState.create(user_id)
        self._lock = _get_user_lock(user_id)
        self._recent_dialogues: list[str] = []
        self._busy_until: float = 0.0    # 忙碌结束时间戳（秒）
        self._busy_action: str = ""      # 当前忙碌事项（item id）

    # ── 统一互动入口 ──

    async def interact(self, action_id: str) -> dict:
        """所有互动（吃/玩/工作/社交/日常）的统一入口.

        管道: 忙碌检查 → 前置条件 → 应用效果 → 服装解锁 → 后置钩子 → 保存.

        Raises
        ------
        ActionNotFoundError — 动作 ID 不存在.
        PetBusyError — 宠物正在忙碌.
        InsufficientStatError — 前置条件不满足.
        """
        async with self._lock:
            item = _item_registry.get(action_id)
            self._check_not_busy()
            item.validate(self.pet)

            # ── 应用效果 ──
            old_level = self.pet.level
            result = item.apply(self.pet, self._recent_dialogues)
            self._track_dialogue(result["dialogue"])

            # 等级提升 → 服装解锁
            if result["leveled_up"]:
                _costume_registry.auto_unlock_by_level(self.pet, old_level)

            result.setdefault("success", True)
            result.setdefault("text", f"{item.emoji} {result['dialogue']}")
            result.setdefault("action", action_id)
            result.setdefault("category", item.category)

            # ── 后置钩子管道 ──
            for hook in self._post_action_hooks:
                await hook(self, item, result)

            self._set_busy(item)
            self._save()
            return result

    # ── 忙碌/耗时 ──

    def _check_not_busy(self) -> None:
        from .exceptions import PetBusyError
        now = time.time()
        if self._busy_until > now:
            raise PetBusyError(int(self._busy_until - now))

    def _set_busy(self, item: Item) -> None:
        if item.duration > 0:
            self._busy_until = time.time() + item.duration
            self._busy_action = item.id

    def is_busy(self) -> bool:
        """返回当前是否在忙碌状态."""
        return self._busy_until > time.time()

    @property
    def busy_remaining(self) -> int:
        """剩余忙碌秒数（0 = 空闲）."""
        return max(0, int(self._busy_until - time.time()))

    @property
    def busy_action(self) -> str:
        """当前忙碌事项（item id），空闲时为空."""
        return self._busy_action if self.is_busy() else ""

    # ── 非互动方法（不受忙碌限制）──

    async def talk(self, message: str) -> dict:
        """聊天——关键词检测 + 闲谈 fallback."""
        async with self._lock:
            self.pet.tick()
            self.pet.check_daily_decay(today_str())

            events = _event_registry.check_keywords(self.pet, message)
            img = None
            img_url = None
            if events:
                evt = events[0]
                recipe = {
                    "v": 1, "kind": "event",
                    "event_id": evt.id, "name": evt.name,
                    "text": evt.text, "effects": evt.effects,
                }
                async def producer():
                    async with _render_semaphore:
                        return await _renderer.render_event_card({
                            "type": evt.type, "name": evt.name,
                            "id": evt.id, "text": evt.text,
                            "effects": evt.effects,
                        })
                img_url, _, _, img = await self._upload_card(
                    recipe, producer, KEY_PREFIX["addressed_diana_event"],
                )

            text = "\n".join(e.text for e in events) if events else ""
            if not events and message.strip():
                text = _dialogue_registry.idle.pick(self._recent_dialogues)
                self._track_dialogue(text)

            self._save()
            return {
                "text": text or "...",
                "meme_triggered": len(events) > 0,
                "events": [
                    {"id": e.id, "type": e.type, "name": e.name, "text": e.text}
                    for e in events
                ],
                "image": img,
                "image_url": img_url,
                "stats": self._stats_dict(),
            }

    async def status(self) -> dict:
        """获取当前状态卡片."""
        async with self._lock:
            self.pet.tick()
            self.pet.check_daily_decay(today_str())

            pet = self.pet
            recipe = {
                "v": 1, "kind": "status",
                "hunger": pet.hunger, "mood": pet.mood,
                "energy": pet.energy, "closeness": pet.closeness,
                "level": pet.level, "coins": pet.coins,
                "title": pet.title, "outfit": pet.outfit,
                "streak_days": pet.streak_days,
                "busy_until": int(self._busy_until),
                "busy_action": self.busy_action,
            }
            async def producer():
                async with _render_semaphore:
                    return await _renderer.render_status_card(
                        self.pet,
                        busy_remaining=self.busy_remaining,
                        busy_action=self.busy_action,
                    )

            img_url, img_w, img_h, img = await self._upload_card(
                recipe, producer, KEY_PREFIX["addressed_diana_status"],
            )

            stats_text = self._format_status_text()
            alerts = self._get_low_stat_alerts()
            self._save()
            return {
                "text": stats_text + ("\n\n⚠ " + alerts if alerts else ""),
                "image": img,
                "image_url": img_url,
                "image_width": img_w,
                "image_height": img_h,
                "stats": self._stats_dict(),
                "alerts": alerts,
            }

    async def tick(self) -> dict:
        """触发事件检测并渲染事件卡片（无锁——调用方负责持锁 + 保存）.

        注意：不调 check_daily_decay()——streak 更新交由用户主动动作触发.
        """
        events = _event_registry.tick(self.pet)
        event_texts: list[str] = []
        images: list[bytes | None] = []
        event_urls: list[str | None] = []
        for evt in events:
            recipe = {
                "v": 1, "kind": "event",
                "event_id": evt.id, "name": evt.name,
                "text": evt.text, "effects": evt.effects,
            }
            async def producer(_evt=evt):
                async with _render_semaphore:
                    return await _renderer.render_event_card({
                        "type": _evt.type, "name": _evt.name,
                        "id": _evt.id, "text": _evt.text,
                        "effects": _evt.effects,
                    })
            e_url, _, _, e_img = await self._upload_card(
                recipe, producer, KEY_PREFIX["addressed_diana_event"],
            )
            images.append(e_img)
            event_urls.append(e_url)
            event_texts.append(evt.text)

        # 事件后 30% 概率掉落金币
        coin_bonus = None
        if random.random() < 0.3:
            bonus = random.randint(10, 20)
            self.pet.coins += bonus
            coin_bonus = f"🎁 运气不错！获得了 {bonus} 嘉心糖币！"


        return {
            "coin_bonus": coin_bonus,
            "events": [{"id": e.id, "type": e.type, "name": e.name, "text": e.text}
                       for e in events],
            "event_texts": event_texts,
            "images": images,
            "event_urls": event_urls,
            "stats": self._stats_dict(),
        }

    # ── 服装方法（不经互动管道）──

    def list_costumes(self) -> list[dict]:
        """获取所有服装及其解锁状态."""
        return _costume_registry.list_costumes(self.pet)

    async def change_outfit(self, costume_id: str) -> dict:
        """手动切换到指定服装."""
        async with self._lock:
            try:
                result = _costume_registry.change(self.pet, costume_id)
            except CostumeNotFoundError as exc:
                return {"success": False, "text": str(exc)}
            if result["success"]:
                self._save()
            return result

    async def random_outfit(self) -> dict:
        """随机换装."""
        async with self._lock:
            result = _costume_registry.random_change(self.pet)
            if result["success"]:
                self._save()
            return result

    async def buy_costume(self, costume_id: str) -> dict:
        """购买/解锁服装."""
        async with self._lock:
            try:
                result = _costume_registry.unlock(self.pet, costume_id)
            except (CostumeNotFoundError, DianaError) as exc:
                return {"success": False, "text": str(exc)}
            if result["success"]:
                self._save()
            return result

    async def costume_list_card(self) -> dict:
        """渲染服装选择列表卡片，返回 {image, image_url, image_width, image_height}."""
        costumes = _costume_registry.list_costumes(self.pet)
        recipe = {
            "v": 1, "kind": "costume_list",
            "all_costumes": sorted(c["id"] for c in costumes),
            "owned": sorted(self.pet.owned_outfits),
            "equipped": self.pet.outfit,
        }
        async def producer():
            async with _render_semaphore:
                return await _renderer.render_costume_list(costumes)

        url, w, h, data = await self._upload_card(
            recipe, producer, KEY_PREFIX["addressed_diana_costume"],
        )
        return {"image": data, "image_url": url, "image_width": w, "image_height": h}

    def list_items(self, category: Optional[str] = None) -> list[dict]:
        """列出可用互动动作."""
        if category:
            items = _item_registry.list_by_category(category)
        else:
            items = _item_registry.list_all()
        return [
            {
                "id": it.id, "category": it.category,
                "description": it.description, "emoji": it.emoji,
                "requires": it.requires, "duration": it.duration,
            }
            for it in items
        ]

    def match_costume(self, name: str) -> dict | None:
        """模糊匹配服装名."""
        return _costume_registry.match_by_name(name, self.pet)

    async def close(self) -> None:
        """保存状态并关闭当前实例."""
        async with self._lock:
            self._save()

    # ── 图床上传（内部）──

    async def _upload_card(self, recipe: dict, producer, prefix: str) -> tuple[str | None, int, int, bytes | None]:
        """渲染并上传卡片到 COS，返回 (url, width, height, bytes).

        bytes 为 producer 的渲染结果（缓存命中时为 None）.
        url 为 None 表示上传失败，调用方应降级使用 bytes.
        """
        rendered: list = []  # mutable to capture from closure
        async def _wrapped():
            data = await producer()
            rendered.append(data)
            return data

        try:
            bucket = get_bucket()
            url = await bucket.get_or_render(recipe, _wrapped, prefix=prefix)
            data = rendered[0] if rendered else None
            if url is not None:
                h = _recipe_hash(recipe)
                entry = manifest.get_addressed(h)
                if entry:
                    return url, entry.get("width", 0), entry.get("height", 0), data
                return url, 0, 0, data
            return None, 0, 0, data
        except Exception:
            logger.exception("Diana COS upload failed for recipe=%s", recipe.get("kind"))
            return None, 0, 0, rendered[0] if rendered else None

    # ── 内部 ──

    def _save(self) -> None:
        try:
            save_pet(self.pet)
        except OSError:
            logger.exception("Diana save_pet failed for user=%s", self.user_id)

    def _stats_dict(self) -> dict:
        return {
            "hunger": self.pet.hunger, "mood": self.pet.mood,
            "energy": self.pet.energy, "closeness": self.pet.closeness,
            "level": self.pet.level, "exp": self.pet.exp,
            "coins": self.pet.coins, "title": self.pet.title,
            "streak_days": self.pet.streak_days,
            "outfit": self.pet.outfit,
            "owned_outfits": self.pet.owned_outfits,
        }

    def _track_dialogue(self, text: str) -> None:
        if text:
            self._recent_dialogues.append(text)
            if len(self._recent_dialogues) > 50:
                self._recent_dialogues = self._recent_dialogues[-50:]

    def _format_status_text(self) -> str:
        pet = self.pet
        mood_emoji = "😊" if pet.mood > 60 else "😐" if pet.mood > 30 else "😢"
        hunger_emoji = "🍽️" if pet.hunger > 60 else "🍴" if pet.hunger > 30 else "🍗"
        energy_emoji = "⚡" if pet.energy > 60 else "🔋" if pet.energy > 30 else "🪫"
        busy_info = ""
        if self.is_busy():
            r = self.busy_remaining
            if r >= 3600:
                busy_info = f"\n⏳ 忙碌中：{r // 3600}小时{(r % 3600) // 60}分钟"
            elif r >= 60:
                busy_info = f"\n⏳ 忙碌中：{r // 60}分钟"
            else:
                busy_info = f"\n⏳ 忙碌中：{r}秒"
        return "\n".join([
            f"{hunger_emoji} 饱腹度：{pet.hunger}/100",
            f"{mood_emoji} 心情：{pet.mood}/100",
            f"{energy_emoji} 体力：{pet.energy}/100",
            f"💕 亲密度：{pet.closeness}/100",
            f"⭐ 等级：{pet.level} | 称号：{pet.title}",
            f"💰 嘉心糖币：{pet.coins}",
            f"🔥 连续互动：{pet.streak_days}天",
        ] + (busy_info and [busy_info] or []))

    def _get_low_stat_alerts(self) -> str | None:
        alerts = []
        if self.pet.hunger <= 20:
            ds = _dialogue_registry.get_stat_low("hunger")
            alerts.append(ds.pick(self._recent_dialogues) if ds else "饱腹度很低了……")
        if self.pet.mood <= 20:
            ds = _dialogue_registry.get_stat_low("mood")
            alerts.append(ds.pick(self._recent_dialogues) if ds else "心情不太好……")
        if self.pet.energy <= 15:
            ds = _dialogue_registry.get_stat_low("energy")
            alerts.append(ds.pick(self._recent_dialogues) if ds else "体力不多了……")
        if self.pet.closeness <= 20:
            ds = _dialogue_registry.get_stat_low("closeness")
            alerts.append(ds.pick(self._recent_dialogues) if ds else "很久没来找然然了……")
        return "\n".join(alerts) if alerts else None


# ===================================================================
# 后置钩子（模块 import 时注册）
# ===================================================================

@DianaSession.on_post_action
async def _hook_tick_and_decay(session: DianaSession, item: Item, result: dict) -> None:
    """钩子 1: 时间衰减 + 每日检查 + 互动计数."""
    session.pet.tick()
    session.pet.check_daily_decay(today_str())
    session.pet.interaction_count += 1


@DianaSession.on_post_action
async def _hook_track_category(session: DianaSession, item: Item, result: dict) -> None:
    """钩子 2: 按 category 追踪计数（成就用）."""
    _event_registry.track_interaction(session.pet, item.category)


@DianaSession.on_post_action
async def _hook_trigger_events(session: DianaSession, item: Item, result: dict) -> None:
    """钩子 3: 触发事件检测 + 渲染事件卡片."""
    tick_result = await session.tick()
    if tick_result["event_texts"]:
        result["events_triggered"] = tick_result["event_texts"]
        result["event_images"] = tick_result["images"]
        result["event_urls"] = tick_result["event_urls"]
    if tick_result["coin_bonus"]:
        result["coin_bonus"] = tick_result["coin_bonus"]


@DianaSession.on_post_action
async def _hook_render_card(session: DianaSession, item: Item, result: dict) -> None:
    """钩子 4: 渲染互动结果卡片 + COS 上传."""
    recipe = {
        "v": 1, "kind": "interaction",
        "action_id": item.id, "emoji": item.emoji,
        "description": item.description,
        "dialogue": result.get("dialogue", ""),
        "changes": result.get("changes", {}),
    }

    async def producer():
        async with _render_semaphore:
            return await _renderer.render_interaction_card(
                session.pet, item.id, item.emoji, item.description,
                result.get("dialogue", ""), result.get("changes", {}),
            )

    url, w, h, data = await session._upload_card(
        recipe, producer, KEY_PREFIX["addressed_diana_interaction"],
    )
    if url:
        result["image_url"] = url
        result["image_width"] = w
        result["image_height"] = h
    result["image"] = data  # COS bytes or local fallback
