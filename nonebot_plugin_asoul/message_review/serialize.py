"""
@Author: star_482
@Date: 2026/8/6
@File: serialize
@Description: 统一消息 content 序列化。入站从 Message 段、出站从 QQ API 参数还原为同构 segments；
并提供 build_text_preview 把任意段降级为纯文字预览，供只关心文字的客户端直接读取 plain_text。
"""
from datetime import date, datetime
from typing import Any

from nonebot.adapters.qq import Message


def _dump(obj: Any) -> Any:
    """递归把 pydantic model / dict / list 转成 JSON 安全结构。
    兼容 pydantic v2 (model_dump) 与 v1 (.dict())；datetime 等转字符串。
    对 model_dump/.dict() 的结果再递归，兜底未转干净的嵌套模型。"""
    if obj is None:
        return None
    if hasattr(obj, "model_dump"):  # pydantic v2
        return _dump(obj.model_dump(mode="json", exclude_none=True))
    if hasattr(obj, "dict") and callable(getattr(obj, "dict")) and not isinstance(
        obj, (dict, list, tuple, str, bytes, int, float, bool)
    ):  # pydantic v1
        try:
            return _dump(obj.dict())
        except Exception:
            pass
    if isinstance(obj, dict):
        return {k: _dump(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_dump(v) for v in obj]
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    return obj


def json_default(obj: Any) -> Any:
    """json.dumps 的 default 兜底：_dump 漏掉的模型/对象在此转换，避免序列化抛错搞挂捕获。"""
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json", exclude_none=True)
    if hasattr(obj, "dict") and callable(getattr(obj, "dict")) and not isinstance(
        obj, (dict, list, tuple, str, bytes, int, float, bool)
    ):
        try:
            return obj.dict()
        except Exception:
            pass
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    return str(obj)


def _seg(seg) -> dict:
    """入站单段 -> 统一形状 {type, data, [便捷字段]}。data 即 seg.data 原形状。"""
    t = seg.type
    d = _dump(seg.data) or {}
    out: dict = {"type": t, "data": d}
    if t == "text":
        out["text"] = d.get("text", "")
    elif t == "image":
        out["url"] = d.get("url")
        out["file"] = d.get("file")
    elif t in ("mention", "at"):
        out["user_id"] = d.get("user_id")
    elif t == "reply":
        out["id"] = d.get("id")
    return out


def _kb_labels(kb: Any) -> list[str]:
    """从 keyboard 模型抽全部按钮 label。"""
    if not isinstance(kb, dict):
        return []
    content = kb.get("content") or {}
    labels: list[str] = []
    for row in content.get("rows") or []:
        if not isinstance(row, dict):
            continue
        for btn in row.get("buttons") or []:
            if not isinstance(btn, dict):
                continue
            rd = btn.get("render_data") or {}
            if label := rd.get("label"):
                labels.append(label)
    return labels


def _embed_text(emb: Any) -> str:
    """从 embed 模型抽 title/description/fields 文字。"""
    if not isinstance(emb, dict):
        return ""
    parts: list[str] = []
    if t := emb.get("title"):
        parts.append(t)
    if d := emb.get("description"):
        parts.append(d)
    for f in emb.get("fields") or []:
        if isinstance(f, dict):
            if n := f.get("name"):
                parts.append(n)
            if v := f.get("value"):
                parts.append(v)
    return "\n".join(parts)


def build_text_preview(segments: list[dict]) -> str:
    """把任意 segments 降级为纯文字预览。

    客户端 v1 可只读 plain_text（由本函数生成），完全不解析 content 段：
      text -> 原文；markdown -> 源码；keyboard -> [按钮:label1|label2]；
      image/file_image -> [图片]；media -> [媒体]；embed -> title/description；ark -> [模板消息]；
      mention -> @user_id；reply -> [回复]；未知 -> 跳过。
    """
    parts: list[str] = []
    for seg in segments:
        t = seg.get("type")
        d = seg.get("data") or {}
        if not isinstance(d, dict):
            d = {}
        if t == "text":
            s = seg.get("text") or d.get("text") or ""
        elif t == "markdown":
            md = d.get("markdown") or {}
            s = (md.get("content") if isinstance(md, dict) else "") or seg.get("content") or "[markdown]"
        elif t == "keyboard":
            labels = _kb_labels(d.get("keyboard"))
            s = f"[按钮:{'|'.join(labels)}]" if labels else "[按钮]"
        elif t in ("image", "file_image"):
            s = "[图片]"
        elif t == "media":
            s = "[媒体]"
        elif t == "embed":
            s = _embed_text(d.get("embed")) or "[卡片]"
        elif t == "ark":
            s = "[模板消息]"
        elif t in ("mention", "at"):
            uid = d.get("user_id")
            s = f"@{uid}" if uid else "@某人"
        elif t == "reply":
            s = "[回复]"
        else:
            s = ""
        if s:
            parts.append(s)
    return "\n".join(parts)


def serialize_incoming(message: Message) -> tuple[str, list[dict]]:
    """入站：Message -> (plain_text, segments)。plain_text 由 build_text_preview 生成。"""
    segments = [_seg(s) for s in message]
    return build_text_preview(segments), segments


def serialize_outgoing(data: dict) -> tuple[str, list[dict], int | None]:
    """出站：QQ call_api 参数 -> (plain_text, segments, msg_type)。

    段形状与入站对齐：data 统一为 {<type>: <模型>}（与 seg.data 一致），
    使 build_text_preview / 客户端渲染逻辑出入站共用一套。
    """
    msg_type = data.get("msg_type")
    segments: list[dict] = []

    if content := data.get("content"):
        segments.append({"type": "text", "data": {"text": content}, "text": content})
    if md := data.get("markdown"):
        m = _dump(md) or {}
        item = {"type": "markdown", "data": {"markdown": m}}
        if "content" in m:
            item["content"] = m["content"]  # 便捷字段：markdown 源码
        segments.append(item)
    if kb := data.get("keyboard"):
        segments.append({"type": "keyboard", "data": {"keyboard": _dump(kb)}})
    if emb := data.get("embed"):
        segments.append({"type": "embed", "data": {"embed": _dump(emb)}})
    if ark := data.get("ark"):
        segments.append({"type": "ark", "data": {"ark": _dump(ark)}})
    if media := data.get("media"):
        segments.append({"type": "media", "data": {"media": _dump(media)}, "msg_type": msg_type})

    return build_text_preview(segments), segments, msg_type
