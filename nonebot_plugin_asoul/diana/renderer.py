"""HTML → PNG 渲染器（基于 Jinja2 + Playwright）."""

import base64
import logging
import random
from pathlib import Path
from typing import Optional

from jinja2 import Environment, FileSystemLoader

from .core import PetState

logger = logging.getLogger(__name__)


class ImageRenderer:
    """将宠物数据渲染为卡片 PNG 图片."""

    def __init__(
        self,
        template_dir: Optional[Path] = None,
        data_dir: Optional[Path] = None,
        assets_dir: Optional[Path] = None,
    ):
        # 默认指向 diana 包内的 data/ 与 assets/ 目录（与代码一起发布）。
        if template_dir is None:
            template_dir = Path(__file__).parent / "data" / "templates"
        self.template_dir = Path(template_dir)
        self.data_dir = Path(data_dir) if data_dir else Path(__file__).parent / "data"
        self.assets_dir = Path(assets_dir) if assets_dir else Path(__file__).parent / "assets"
        self.env = Environment(loader=FileSystemLoader(str(self.template_dir)))
        self._browser = None
        # 服装名缓存：启动时一次性读 costumes.yaml，避免每次状态卡片渲染都重读。
        self._costumes_cache: dict[str, str] = {}
        self._load_costumes_yaml()

    async def _get_browser(self):
        """懒初始化 Playwright 浏览器."""
        if self._browser is None:
            from playwright.async_api import async_playwright
            self._pw = await async_playwright().start()
            self._browser = await self._pw.chromium.launch(headless=True)
        return self._browser

    async def _html_to_png(self, html: str, width: int = 600, height: int = 400) -> bytes:
        """将 HTML 字符串渲染为 PNG 字节."""
        browser = await self._get_browser()
        page = await browser.new_page(viewport={"width": width, "height": height})
        await page.set_content(html, wait_until="networkidle")
        screenshot = await page.screenshot(full_page=False, type="png")
        await page.close()
        return screenshot

    def _get_costume_image_data(self, costume_id: str) -> str:
        """读取服装图片并返回 base64 data URI，文件不存在则返回空字符串."""
        image_path = self.assets_dir / "images" / "costumes" / f"{costume_id}.png"
        if image_path.exists():
            with open(image_path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("utf-8")
            return f"data:image/png;base64,{b64}"
        return ""

    def _get_costume_name(self, costume_id: str) -> str:
        """根据 costume_id 返回中文名称（读启动期缓存，不重读 YAML）."""
        return self._costumes_cache.get(costume_id, costume_id)

    def _load_costumes_yaml(self) -> None:
        """启动期一次性加载 costumes.yaml 到 _costumes_cache.

        文件缺失或解析失败时打 warning 并保持空缓存（不影响功能，只是显示
        服装名会回退到 costume_id）。运行时不会重新加载，编辑 costumes.yaml
        后需要重启 bot。
        """
        try:
            import yaml
            path = self.data_dir / "costumes.yaml"
            if not path.exists():
                logger.warning("costumes.yaml not found at %s; costume names will fall back to id", path)
                return
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            self._costumes_cache = {
                cid: info.get("name", cid)
                for cid, info in data.items()
                if isinstance(info, dict)
            }
        except Exception:
            logger.exception("Failed to load costumes.yaml; costume names will fall back to id")

    # ── 卡片渲染 ──

    async def render_status_card(
        self, pet: PetState,
        busy_remaining: int = 0,
        busy_action: str = "",
    ) -> bytes:
        """渲染宠物状态面板.

        Parameters
        ----------
        busy_remaining : int
            忙碌剩余秒数，0 表示空闲.
        busy_action : str
            忙碌事项名称（item id），空字符串表示空闲.
        """
        quotes = [
            "关注嘉然，顿顿解馋！🍓",
            "要成为全世界最开心的糖！",
            "大家一定要好好吃饭啊！",
            "我是你们最甜甜甜的小草莓~",
            "今天也是元气满满的一天呢！",
        ]
        # 格式化忙碌剩余时间
        busy_text = ""
        if busy_remaining > 0:
            if busy_remaining >= 3600:
                h = busy_remaining // 3600
                m = (busy_remaining % 3600) // 60
                busy_text = f"{h}小时{m}分钟" if m else f"{h}小时"
            elif busy_remaining >= 60:
                m = busy_remaining // 60
                s = busy_remaining % 60
                busy_text = f"{m}分钟{s}秒" if s else f"{m}分钟"
            else:
                busy_text = f"{busy_remaining}秒"

        costume_img = self._get_costume_image_data(pet.outfit)
        costume_name = self._get_costume_name(pet.outfit)
        template = self.env.get_template("status_card.html")
        html = template.render(
            name="嘉然 Diana",
            title=pet.title,
            level=pet.level,
            streak_days=pet.streak_days,
            coins=pet.coins,
            outfit=costume_name,
            costume_image=costume_img,
            quote=random.choice(quotes),
            busy_remaining=busy_remaining,
            busy_action=busy_action,
            busy_text=busy_text,
            stats=[
                {"icon": "🍽️", "label": "饱腹度", "value": pet.hunger},
                {"icon": "😊", "label": "心情", "value": pet.mood},
                {"icon": "⚡", "label": "体力", "value": pet.energy},
                {"icon": "💕", "label": "亲密度", "value": pet.closeness},
            ],
        )
        return await self._html_to_png(html, 680, 440)

    async def render_interaction_card(
        self, pet: PetState, action_name: str, emoji: str, description: str,
        dialogue: str, changes: dict,
    ) -> bytes:
        """渲染交互结果卡片."""
        template = self.env.get_template("interaction_card.html")

        change_items = []
        for key, label, icon in [
            ("hunger", "饱腹", "🍽️"), ("mood", "心情", "😊"),
            ("energy", "体力", "⚡"), ("closeness", "亲密度", "💕"),
            ("coins", "金币", "💰"),
        ]:
            val = changes.get(key, 0)
            if val == 0:
                continue
            sign = "+" if val > 0 else ""
            change_items.append({
                "icon": icon,
                "label": label,
                "value": f"{sign}{val}",
                "type": "positive" if val > 0 else "negative",
            })

        html = template.render(
            emoji=emoji,
            action_name=action_name,
            description=description,
            dialogue=dialogue,
            changes=change_items,
        )
        return await self._html_to_png(html, 600, 340)

    async def render_event_card(self, event: dict) -> bytes:
        """渲染事件卡片."""
        template = self.env.get_template("event_card.html")

        badge_map = {
            "random": "🎲 随机事件",
            "meme": "💬 梗事件",
            "special_date": "📅 特殊日期",
            "achievement": "🏆 成就达成",
        }

        effects_list = []
        for key, label in [("hunger", "饱腹"), ("mood", "心情"), ("energy", "体力"),
                            ("closeness", "亲密度"), ("coins", "金币"), ("exp", "经验")]:
            val = event.get("effects", {}).get(key, 0)
            if val != 0:
                sign = "+" if val > 0 else ""
                effects_list.append(f"{label} {sign}{val}")

        icons = {"random": "✨", "meme": "💬", "special_date": "🎉", "achievement": "🏆"}

        html = template.render(
            badge=badge_map.get(event.get("type", "random"), ""),
            icon=icons.get(event.get("type", "random"), "✨"),
            title=event.get("name", event.get("id", "事件")),
            text=event.get("text", ""),
            effects=effects_list if effects_list else None,
        )
        return await self._html_to_png(html, 600, 380)

    async def render_costume_list(self, costumes: list[dict]) -> bytes:
        """渲染服装选择列表卡片."""
        enriched = []
        for c in costumes:
            img_data = self._get_costume_image_data(c["id"])
            enriched.append({**c, "image_data": img_data})
        template = self.env.get_template("costume_card.html")
        html = template.render(costumes=enriched)
        return await self._html_to_png(html, 600, 520)

    async def close(self):
        """关闭浏览器."""
        if self._browser:
            await self._browser.close()
            self._browser = None
        if hasattr(self, '_pw') and self._pw:
            await self._pw.stop()
