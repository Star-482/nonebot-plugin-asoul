"""服装系统：解锁、切换、随机换装."""
import random
from pathlib import Path
from typing import Optional

import yaml

from .core import PetState
from .exceptions import CostumeNotFoundError, CostumeLockedError


class CostumeService:
    """管理服装解锁和切换."""

    def __init__(self, data_dir: Optional[Path] = None):
        if data_dir is None:
            data_dir = Path(__file__).parent / "data"
        self.data_dir = Path(data_dir)
        self.costumes = self._load_yaml("costumes.yaml")

    def _load_yaml(self, filename: str) -> dict:
        path = self.data_dir / filename
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def list_costumes(self, pet: PetState) -> list[dict]:
        """返回所有服装及其解锁状态."""
        result = []
        for cid, cdata in self.costumes.items():
            owned = cid in pet.owned_outfits
            equipped = cid == pet.outfit
            result.append({
                "id": cid,
                "name": cdata["name"],
                "emoji": cdata.get("emoji", ""),
                "description": cdata.get("description", ""),
                "image": cdata.get("image", ""),
                "owned": owned,
                "equipped": equipped,
                "unlock": cdata.get("unlock", {}),
            })
        return result

    def match_by_name(self, name: str, pet: PetState = None) -> dict | None:
        """模糊匹配服装名。name 是用户输入，可部分匹配（双向子串）。"""
        all_costumes = self.list_costumes(pet) if pet else [
            {"id": cid, "name": cdata["name"]} for cid, cdata in self.costumes.items()
        ]
        for costume in all_costumes:
            if costume["name"] in name or name in costume["name"]:
                return costume
        return None

    def get_owned(self, pet: PetState) -> list[str]:
        """返回已解锁的服装 ID 列表."""
        return pet.owned_outfits

    def check_unlock(self, pet: PetState, costume_id: str) -> tuple[bool, str]:
        """检查服装是否满足解锁条件，返回 (可解锁, 原因)."""
        if costume_id not in self.costumes:
            raise CostumeNotFoundError(costume_id)

        if costume_id in pet.owned_outfits:
            return False, "已经拥有这件服装了~"

        unlock = self.costumes[costume_id].get("unlock", {})
        utype = unlock.get("type", "default")
        uvalue = unlock.get("value")

        if utype == "default":
            return False, "初始服装不需要解锁"
        elif utype == "level":
            if pet.level >= uvalue:
                return True, ""
            return False, f"需要达到 Lv.{uvalue} 才能解锁"
        elif utype == "coins":
            if pet.coins >= uvalue:
                return True, ""
            return False, f"需要 {uvalue} 嘉心糖币"
        elif utype == "achievement":
            if pet.achievement_flags.get(uvalue, False):
                return True, ""
            return False, "成就条件未达成"

        return False, "未知的解锁类型"

    def unlock(self, pet: PetState, costume_id: str) -> dict:
        """尝试解锁服装."""
        if costume_id not in self.costumes:
            raise CostumeNotFoundError(costume_id)

        if costume_id in pet.owned_outfits:
            raise CostumeLockedError("已经拥有这件服装了~")

        can_unlock, reason = self.check_unlock(pet, costume_id)
        if not can_unlock:
            return {"success": False, "text": reason}

        unlock = self.costumes[costume_id].get("unlock", {})
        utype = unlock.get("type")
        uvalue = unlock.get("value", 0)

        if utype == "coins":
            pet.coins -= uvalue

        pet.owned_outfits.append(costume_id)
        name = self.costumes[costume_id]["name"]
        emoji = self.costumes[costume_id].get("emoji", "")
        return {"success": True, "text": f"{emoji} 解锁了新服装「{name}」！"}

    def change(self, pet: PetState, costume_id: str) -> dict:
        """切换到指定服装."""
        if costume_id not in self.costumes:
            raise CostumeNotFoundError(costume_id)

        if costume_id not in pet.owned_outfits:
            raise CostumeLockedError()

        pet.outfit = costume_id
        name = self.costumes[costume_id]["name"]
        emoji = self.costumes[costume_id].get("emoji", "")
        return {"success": True, "text": f"{emoji} 换上了「{name}」！"}

    def random_change(self, pet: PetState) -> dict:
        """从已解锁服装中随机换一套（排除当前装备的）."""
        owned = [c for c in pet.owned_outfits if c != pet.outfit]
        if not owned:
            return {"success": False, "text": "只有这一件衣服呢，先解锁更多服装吧~"}

        costume_id = random.choice(owned)
        return self.change(pet, costume_id)

    def auto_unlock_by_level(self, pet: PetState, old_level: int):
        """等级变化时自动检查并解锁等级服装."""
        for cid, cdata in self.costumes.items():
            unlock = cdata.get("unlock", {})
            if unlock.get("type") == "level":
                required = unlock.get("value", 999)
                if old_level < required <= pet.level and cid not in pet.owned_outfits:
                    pet.owned_outfits.append(cid)
