"""枝江日程自动同步、缓存、渲染与命令入口。"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from jinja2 import Environment, FileSystemLoader, select_autoescape
from nonebot import get_driver, require
from nonebot.adapters.qq import Message, MessageSegment
from nonebot.log import logger
from nonebot.plugin.on import on_command

from ..browser import get_browser
from ..config import config
from ..storage import KEY_PREFIX, get_bucket, manifest


SCHEDULE_ICS_URL = "https://asoul.love/calendar.ics"
SCHEDULE_SCHEMA_VERSION = 1
UTC = timezone.utc
SHANGHAI = ZoneInfo("Asia/Shanghai")

_SUMMARY_RE = re.compile(
    r"^【(?P<event_type>[^】]+)】(?P<title>.*?)(?::\s*(?P<detail>.*))?$"
)
_DURATION_RE = re.compile(
    r"^P(?:(?P<weeks>\d+)W)?(?:(?P<days>\d+)D)?"
    r"(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?)?$"
)
_LIVE_URL_RE = re.compile(r"https?://live\.bilibili\.com/\d+")
_OPUS_URL_RE = re.compile(r"https?://www\.bilibili\.com/opus/\d+")
_TYPE_COLORS = {
    "日常": "#8267d8",
    "突击": "#ef7c9c",
    "节目": "#5272b8",
    "2D": "#43a8a5",
}
_WEEKDAY_NAMES = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
_MEMBER_THEMES = {
    "嘉然": {"color": "#E799B0", "soft": "#FCECF2", "ink": "#743F50"},
    "贝拉": {"color": "#DB7D74", "soft": "#FBEAE7", "ink": "#733B37"},
    "乃琳": {"color": "#576690", "soft": "#E9EDF7", "ink": "#303B60"},
    "心宜": {"color": "#C93773", "soft": "#F9E6EE", "ink": "#70203F"},
    "思诺": {"color": "#7252C0", "soft": "#EEE9FA", "ink": "#3E2D72"},
}
_MEMBER_ORDER = ("嘉然", "贝拉", "乃琳", "心宜", "思诺")


class CalendarParseError(ValueError):
    """ICS 内容不完整或不符合本模块可处理的日程格式。"""


@dataclass(frozen=True)
class ScheduleEvent:
    """已标准化、可缓存和渲染的一条枝江日程。"""

    id: str
    uid: str
    starts_at: datetime
    ends_at: datetime | None
    duration_minutes: int | None
    event_type: str | None
    title: str
    detail: str | None
    performers: tuple[str, ...]
    description: str | None
    live_url: str | None
    opus_url: str | None
    status: str | None
    transparency: str | None
    created_at: datetime | None

    def to_dict(self) -> dict[str, Any]:
        """序列化为稳定、显式含时区的 JSON 结构。"""
        return {
            "id": self.id,
            "uid": self.uid,
            "starts_at": self.starts_at.astimezone(UTC).isoformat(),
            "starts_at_shanghai": self.starts_at.astimezone(SHANGHAI).isoformat(),
            "ends_at": self.ends_at.astimezone(UTC).isoformat() if self.ends_at else None,
            "ends_at_shanghai": self.ends_at.astimezone(SHANGHAI).isoformat() if self.ends_at else None,
            "duration_minutes": self.duration_minutes,
            "type": self.event_type,
            "title": self.title,
            "detail": self.detail,
            "performers": list(self.performers),
            "description": self.description,
            "live_url": self.live_url,
            "opus_url": self.opus_url,
            "status": self.status,
            "transparency": self.transparency,
            "created_at": self.created_at.astimezone(UTC).isoformat() if self.created_at else None,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ScheduleEvent":
        starts_at = _parse_cached_datetime(raw["starts_at"])
        ends_at = _parse_cached_datetime(raw["ends_at"]) if raw.get("ends_at") else None
        created_at = _parse_cached_datetime(raw["created_at"]) if raw.get("created_at") else None
        performers = raw.get("performers") or []
        if not isinstance(performers, list):
            raise ValueError("performers must be a list")
        return cls(
            id=str(raw["id"]),
            uid=str(raw["uid"]),
            starts_at=starts_at,
            ends_at=ends_at,
            duration_minutes=_optional_int(raw.get("duration_minutes")),
            event_type=_optional_str(raw.get("type")),
            title=str(raw.get("title") or "未命名活动"),
            detail=_optional_str(raw.get("detail")),
            performers=tuple(str(item) for item in performers),
            description=_optional_str(raw.get("description")),
            live_url=_optional_str(raw.get("live_url")),
            opus_url=_optional_str(raw.get("opus_url")),
            status=_optional_str(raw.get("status")),
            transparency=_optional_str(raw.get("transparency")),
            created_at=created_at,
        )


def _optional_str(value: Any) -> str | None:
    return str(value) if value is not None else None


def _optional_int(value: Any) -> int | None:
    return int(value) if value is not None else None


def _parse_cached_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("cached datetime is missing timezone")
    return parsed


def _unfold_lines(text: str) -> list[str]:
    """按 RFC 5545 展开以空格或制表符开头的折行。"""
    unfolded: list[str] = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if line.startswith((" ", "\t")) and unfolded:
            unfolded[-1] += line[1:]
        else:
            unfolded.append(line)
    return unfolded


def _unescape_ical(value: str) -> str:
    decoded: list[str] = []
    index = 0
    replacements = {"n": "\n", "N": "\n", ",": ",", ";": ";", "\\": "\\"}
    while index < len(value):
        if value[index] == "\\" and index + 1 < len(value):
            decoded.append(replacements.get(value[index + 1], value[index + 1]))
            index += 2
        else:
            decoded.append(value[index])
            index += 1
    return "".join(decoded)


def _split_property(line: str) -> tuple[str, dict[str, str], str] | None:
    if ":" not in line:
        return None
    left, raw_value = line.split(":", 1)
    name, *raw_parameters = left.split(";")
    parameters: dict[str, str] = {}
    for parameter in raw_parameters:
        if "=" in parameter:
            key, parameter_value = parameter.split("=", 1)
            parameters[key.upper()] = parameter_value.strip('"')
    return name.upper(), parameters, _unescape_ical(raw_value)


def _parse_ical_datetime(value: str, parameters: dict[str, str]) -> datetime:
    if parameters.get("VALUE", "").upper() == "DATE" or re.fullmatch(r"\d{8}", value):
        return datetime.strptime(value, "%Y%m%d").replace(tzinfo=SHANGHAI)

    utc_formats = ("%Y%m%dT%H%M%SZ", "%Y%m%dT%H%MZ")
    local_formats = ("%Y%m%dT%H%M%S", "%Y%m%dT%H%M")
    if value.endswith("Z"):
        for format_string in utc_formats:
            try:
                return datetime.strptime(value, format_string).replace(tzinfo=UTC)
            except ValueError:
                continue
        raise CalendarParseError(f"Invalid UTC date-time: {value!r}")

    for format_string in local_formats:
        try:
            parsed = datetime.strptime(value, format_string)
            timezone_name = parameters.get("TZID")
            if timezone_name:
                try:
                    return parsed.replace(tzinfo=ZoneInfo(timezone_name))
                except ZoneInfoNotFoundError as exc:
                    raise CalendarParseError(f"Unknown TZID: {timezone_name!r}") from exc
            return parsed.replace(tzinfo=SHANGHAI)
        except ValueError:
            continue
    raise CalendarParseError(f"Invalid date-time: {value!r}")


def _parse_duration(value: str) -> timedelta | None:
    match = _DURATION_RE.fullmatch(value)
    if not match:
        return None
    parts = {key: int(number or 0) for key, number in match.groupdict().items()}
    return timedelta(**parts)


def _first(properties: dict[str, list[tuple[dict[str, str], str]]], name: str) -> tuple[dict[str, str], str] | None:
    values = properties.get(name)
    return values[0] if values else None


def _value(properties: dict[str, list[tuple[dict[str, str], str]]], name: str) -> str | None:
    item = _first(properties, name)
    return item[1] if item else None


def _parse_summary(summary: str) -> tuple[str | None, str, str | None]:
    match = _SUMMARY_RE.match(summary)
    if not match:
        return None, summary or "未命名活动", None
    return (
        match.group("event_type"),
        match.group("title").strip() or "未命名活动",
        (match.group("detail") or "").strip() or None,
    )


def _extract_description_metadata(description: str) -> tuple[str | None, tuple[str, ...]]:
    first_line = description.splitlines()[0] if description else ""
    event_type, separator, performer_text = first_line.partition("|")
    if not separator:
        return None, ()
    return event_type.strip() or None, tuple(performer_text.split())


def _event_from_properties(properties: dict[str, list[tuple[dict[str, str], str]]]) -> ScheduleEvent:
    uid = _value(properties, "UID")
    start_item = _first(properties, "DTSTART")
    if not uid or not start_item:
        raise CalendarParseError("VEVENT must include UID and DTSTART")

    starts_at = _parse_ical_datetime(start_item[1], start_item[0])
    end_item = _first(properties, "DTEND")
    ends_at = _parse_ical_datetime(end_item[1], end_item[0]) if end_item else None
    duration = _parse_duration(_value(properties, "DURATION") or "")
    if ends_at is None and duration is not None:
        ends_at = starts_at + duration

    summary_type, title, detail = _parse_summary(_value(properties, "SUMMARY") or "")
    description = _value(properties, "DESCRIPTION") or ""
    description_type, performers = _extract_description_metadata(description)
    created_item = _first(properties, "DTSTAMP")
    created_at = _parse_ical_datetime(created_item[1], created_item[0]) if created_item else None
    live_match = _LIVE_URL_RE.search(description)
    opus_match = _OPUS_URL_RE.search(description)

    return ScheduleEvent(
        id=uid.split("@", 1)[0],
        uid=uid,
        starts_at=starts_at,
        ends_at=ends_at,
        duration_minutes=int(duration.total_seconds() // 60) if duration else None,
        event_type=summary_type or description_type,
        title=title,
        detail=detail,
        performers=performers,
        description=description or None,
        live_url=_value(properties, "URL") or (live_match.group(0) if live_match else None),
        opus_url=opus_match.group(0) if opus_match else None,
        status=_value(properties, "STATUS"),
        transparency=_value(properties, "TRANSP"),
        created_at=created_at,
    )


def parse_ics_events(text: str) -> list[ScheduleEvent]:
    """解析完整 VCALENDAR；异常由调用方保留上一版缓存。"""
    current_event: dict[str, list[tuple[dict[str, str], str]]] | None = None
    events: list[ScheduleEvent] = []
    has_calendar = False
    ended_calendar = False

    for line in _unfold_lines(text):
        if line == "BEGIN:VCALENDAR":
            has_calendar = True
            continue
        if line == "END:VCALENDAR":
            if current_event is not None:
                raise CalendarParseError("VCALENDAR ended before VEVENT")
            ended_calendar = True
            continue
        if line == "BEGIN:VEVENT":
            if current_event is not None:
                raise CalendarParseError("Nested VEVENT is invalid")
            current_event = {}
            continue
        if line == "END:VEVENT":
            if current_event is None:
                raise CalendarParseError("END:VEVENT without BEGIN:VEVENT")
            events.append(_event_from_properties(current_event))
            current_event = None
            continue
        if current_event is None:
            continue

        parsed = _split_property(line)
        if parsed is None:
            continue
        name, parameters, value = parsed
        current_event.setdefault(name, []).append((parameters, value))

    if not has_calendar or not ended_calendar:
        raise CalendarParseError("Missing VCALENDAR envelope")
    if current_event is not None:
        raise CalendarParseError("Unclosed VEVENT")
    return events


class ScheduleImageRenderer:
    """将当前自然周日程渲染为一张无外部素材依赖的 PNG。"""

    def __init__(self) -> None:
        template_dir = Path(__file__).parent / "templates"
        self._environment = Environment(
            loader=FileSystemLoader(str(template_dir)),
            autoescape=select_autoescape(["html", "xml"]),
        )

    async def render(self, events: list[ScheduleEvent], now: datetime) -> bytes:
        week_start = (now - timedelta(days=now.weekday())).date()
        schedule_assets = self._schedule_assets()
        calendar_days: list[dict[str, Any]] = []
        for offset, weekday_name in enumerate(_WEEKDAY_NAMES):
            current_day = week_start + timedelta(days=offset)
            day_events = [
                event for event in events
                if event.starts_at.astimezone(SHANGHAI).date() == current_day
            ]
            calendar_days.append(
                {
                    "date": current_day.strftime("%m/%d"),
                    "weekday": weekday_name,
                    "is_today": current_day == now.date(),
                    "events": [
                        self._event_for_template(event, schedule_assets["chibi_uris"])
                        for event in day_events
                    ],
                }
            )

        template = self._environment.get_template("schedule_card.html")
        html = template.render(
            week_range=f"{week_start:%Y.%m.%d} - {(week_start + timedelta(days=6)):%m.%d}",
            calendar_days=calendar_days,
            has_events=any(day["events"] for day in calendar_days),
            header_poster_uri=schedule_assets["header_poster_uri"],
            generated_at=now.strftime("%m.%d %H:%M"),
        )
        browser = await get_browser()
        page = await browser.new_page(viewport={"width": 1800, "height": 1120}, device_scale_factor=1)
        try:
            await page.set_content(html, wait_until="networkidle")
            return await page.locator("main.schedule").screenshot(type="png")
        finally:
            await page.close()

    @staticmethod
    def _event_for_template(
        event: ScheduleEvent,
        chibi_uris: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        chibi_uris = chibi_uris or {}
        member_colors = [
            _MEMBER_THEMES[performer]["color"]
            for performer in event.performers
            if performer in _MEMBER_THEMES
        ]
        if not member_colors:
            member_colors = [_TYPE_COLORS.get(event.event_type or "", "#766B91")]
        primary_member = next(
            (performer for performer in event.performers if performer in _MEMBER_THEMES),
            None,
        )
        primary_theme = _MEMBER_THEMES.get(primary_member or "")
        return {
            "time": event.starts_at.astimezone(SHANGHAI).strftime("%H:%M"),
            "type": event.event_type or "日程",
            "title": event.title,
            "detail": event.detail or "",
            "performers": "、".join(event.performers),
            "accent": member_colors[0],
            "ink": primary_theme["ink"] if primary_theme else "#403852",
            "member_dots": member_colors,
            "chibi_uri": (
                chibi_uris.get(event.performers[0], "")
                if len(event.performers) == 1
                else ""
            ),
        }

    @staticmethod
    def _image_data_uri(image_path: Path) -> str:
        try:
            encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
        except OSError:
            logger.debug("日程图片素材不可用: %s", image_path)
            return ""
        return f"data:image/png;base64,{encoded}"

    @classmethod
    def _schedule_assets(cls) -> dict[str, Any]:
        asset_dir = Path(config.data_path) / "activity" / "schedule_assets"
        return {
            "header_poster_uri": cls._image_data_uri(asset_dir / "header-poster.png"),
            "chibi_uris": {
                member: cls._image_data_uri(asset_dir / f"{member}-chibi.png")
                for member in _MEMBER_ORDER
            },
        }


class ScheduleService:
    """同步 ICS、维护本地缓存，并生成与缓存匹配的周历图片。"""

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            headers={
                "Accept": "text/calendar, text/plain;q=0.9, */*;q=0.1",
                "User-Agent": "nonebot-plugin-asoul schedule-sync",
            },
            timeout=config.schedule_http_timeout,
            follow_redirects=True,
        )
        self._renderer = ScheduleImageRenderer()
        self._sync_lock = asyncio.Lock()

    @property
    def activity_dir(self) -> Path:
        return Path(config.data_path) / "activity"

    @property
    def cache_path(self) -> Path:
        return self.activity_dir / "schedule.json"

    @property
    def image_path(self) -> Path:
        return self.activity_dir / "schedule.png"

    async def sync(self, now: datetime | None = None) -> bool:
        """同步官方增量日程，并保留本周已过去的本地历史。"""
        now = (now or datetime.now(SHANGHAI)).astimezone(SHANGHAI)
        async with self._sync_lock:
            try:
                response = await self._client.get(SCHEDULE_ICS_URL)
                response.raise_for_status()
                parsed_events = parse_ics_events(response.text)
                source_events = self._select_events(parsed_events, now)
                events = self._merge_current_week_history(
                    source_events, self._read_cached_events(), now
                )
                image_bytes = await self._renderer.render(events, now)
                self._write_cache(events, now)
                self._atomic_write_bytes(self.image_path, image_bytes)
                if await self.get_image_markdown() is None:
                    logger.warning("周历图片上传 COS 失败，将在发送命令时重试")
            except (httpx.HTTPError, CalendarParseError, OSError, ValueError) as exc:
                logger.warning("日程同步失败，继续使用最近一次有效缓存: %s", exc)
                return False
            except Exception:
                logger.exception("日程同步失败，继续使用最近一次有效缓存")
                return False

        logger.info("日程同步完成: %d 条本周及未来有效活动", len(events))
        return True

    @staticmethod
    def _select_events(events: list[ScheduleEvent], now: datetime) -> list[ScheduleEvent]:
        """过滤源数据，只接受当前自然周及之后的未取消活动。"""
        week_start = (now - timedelta(days=now.weekday())).date()
        unique: dict[str, ScheduleEvent] = {}
        for event in events:
            if (event.status or "").upper() == "CANCELLED":
                continue
            if event.starts_at.astimezone(SHANGHAI).date() < week_start:
                continue
            unique[event.uid] = event
        return sorted(
            unique.values(),
            key=lambda event: (event.starts_at.astimezone(SHANGHAI), event.uid),
        )

    @staticmethod
    def _merge_current_week_history(
        source_events: list[ScheduleEvent], cached_events: list[ScheduleEvent], now: datetime
    ) -> list[ScheduleEvent]:
        """以源数据更新今天及以后，并补回本周今天之前的缓存活动。

        枝江 ICS 只提供当天起的日程。因而周中同步时，周一至昨天的活动不在
        远端响应中，不能据此删除；今天及以后则完全以远端响应为准。
        """
        week_start = (now - timedelta(days=now.weekday())).date()
        today = now.date()
        merged = {
            event.uid: event
            for event in cached_events
            if (
                (event.status or "").upper() != "CANCELLED"
                and week_start <= event.starts_at.astimezone(SHANGHAI).date() < today
            )
        }
        merged.update({event.uid: event for event in source_events})
        return sorted(
            merged.values(),
            key=lambda event: (event.starts_at.astimezone(SHANGHAI), event.uid),
        )

    def _write_cache(self, events: list[ScheduleEvent], synced_at: datetime) -> None:
        payload = {
            "schema_version": SCHEDULE_SCHEMA_VERSION,
            "source_url": SCHEDULE_ICS_URL,
            "synced_at": synced_at.isoformat(),
            "events": [event.to_dict() for event in events],
        }
        self._atomic_write_json(self.cache_path, payload)

    @staticmethod
    def _atomic_write_bytes(path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = path.with_suffix(path.suffix + ".tmp")
        temporary_path.write_bytes(content)
        os.replace(temporary_path, path)

    @staticmethod
    def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
        rendered = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        ScheduleService._atomic_write_bytes(path, rendered.encode("utf-8"))

    def _read_cached_events(self) -> list[ScheduleEvent]:
        """读取完整缓存，供同步合并当前周历史与命令摘要复用。"""
        if not self.cache_path.exists():
            return []
        try:
            with self.cache_path.open("r", encoding="utf-8") as file:
                payload = json.load(file)
            if payload.get("schema_version") != SCHEDULE_SCHEMA_VERSION:
                raise ValueError("unsupported schedule cache schema")
            raw_events = payload.get("events")
            if not isinstance(raw_events, list):
                raise ValueError("schedule cache events must be a list")
            events = [ScheduleEvent.from_dict(raw) for raw in raw_events if isinstance(raw, dict)]
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            logger.warning("读取日程缓存失败: %s", exc)
            return []
        return events

    def load_events(self, now: datetime | None = None) -> list[ScheduleEvent]:
        """读取尚未开始的缓存活动；缓存损坏时不抛到命令处理层。"""
        now = (now or datetime.now(SHANGHAI)).astimezone(SHANGHAI)
        return [
            event for event in self._read_cached_events()
            if event.starts_at.astimezone(SHANGHAI) > now
        ]

    async def get_image_markdown(self) -> str | None:
        """确保周历图已上传 COS，并返回可嵌入 QQ Markdown 的图片字面量。"""
        if not self.image_path.exists():
            return None

        bucket = get_bucket()
        try:
            url = await bucket.get_or_upload_file(
                self.image_path,
                prefix=KEY_PREFIX["activity"],
                force_upload_on_manifest_miss=True,
            )
        except Exception:
            logger.exception("上传周历图片到 COS 时发生异常")
            return None
        if not url:
            return None

        key = f"{KEY_PREFIX['activity']}/{self.image_path.name}"
        entry = manifest.get_static(key) or {}
        width = int(entry.get("width") or 1800)
        height = int(entry.get("height") or 1120)
        return bucket.build_md_image(url, width, height, "本周日程")

    async def close(self) -> None:
        await self._client.aclose()


schedule_service = ScheduleService()


def get_relative_content(now: datetime | None = None) -> dict[str, list[str]]:
    """供命令与其他内部模块使用的今天/明天日程摘要。"""
    now = (now or datetime.now(SHANGHAI)).astimezone(SHANGHAI)
    events = schedule_service.load_events(now)
    today = now.date()
    tomorrow = today + timedelta(days=1)
    return {
        "today": [_format_event(event) for event in events if event.starts_at.astimezone(SHANGHAI).date() == today],
        "tomorrow": [_format_event(event) for event in events if event.starts_at.astimezone(SHANGHAI).date() == tomorrow],
    }


def _format_event(event: ScheduleEvent) -> str:
    local_time = event.starts_at.astimezone(SHANGHAI).strftime("%H:%M")
    type_prefix = f"【{event.event_type}】" if event.event_type else ""
    detail = f"（{event.detail}）" if event.detail else ""
    performers = f" · {'、'.join(event.performers)}" if event.performers else ""
    return f"{local_time} {type_prefix}{event.title}{detail}{performers}"


def _build_command_markdown(today_events: list[str], image_markdown: str = "") -> str:
    """构造日程命令 Markdown：图片、今天安排和数据来源致谢。"""
    today = "\n".join(today_events) if today_events else "今天暂时没有后续日程。"
    sections = [section for section in (image_markdown, f"### 今日安排\n{today}") if section]
    sections.append(
        "> 感谢[【枝江日程表】](https://asoul.love/)提供的数据支持，"
        "非常好用的工具，推荐给大家。"
    )
    return "\n\n".join(sections)


week_activity = on_command("本周日程", aliases={"日程"}, priority=config.command_priority)


@week_activity.handle()
async def _week_activity_handler():
    content = get_relative_content()
    image_markdown = await schedule_service.get_image_markdown()
    markdown = _build_command_markdown(content["today"], image_markdown or "")
    if image_markdown:
        message: Message | MessageSegment = MessageSegment.markdown(markdown)
    elif schedule_service.image_path.exists():
        logger.warning("周历 COS 图片不可用，降级为本地图片发送")
        message = MessageSegment.file_image(schedule_service.image_path) + MessageSegment.markdown(markdown)
    else:
        message = MessageSegment.markdown(markdown + "\n\n> 周历图片正在生成，请稍后再试。")
    await week_activity.finish(message)


async def _sync_schedule() -> None:
    await schedule_service.sync()


require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler
from apscheduler.triggers.interval import IntervalTrigger


scheduler.add_job(
    _sync_schedule,
    trigger=IntervalTrigger(seconds=config.schedule_sync_interval),
    id="asoul_schedule_sync",
    coalesce=True,
    max_instances=1,
    replace_existing=True,
)
logger.info("日程同步已注册，间隔 %ss", config.schedule_sync_interval)

driver = get_driver()


@driver.on_startup
async def _schedule_startup() -> None:
    await schedule_service.sync()


@driver.on_shutdown
async def _schedule_shutdown() -> None:
    await schedule_service.close()
