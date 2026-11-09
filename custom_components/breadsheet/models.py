"""Bakes as the integration sees them, parsed from breadsheet's `bake` objects (docs/api-v2.md).

Only the fields the integration uses. Text fields come from other people's recipes via the community library:
treat them as untrusted, never as templates or markup. Durations are seconds; unknown hands-on time arrives
already filled in by the server (`hands_on.estimated`), never as zero.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.util import dt as dt_util


def _time(value: Any, field: str, *, optional: bool = False) -> datetime | None:
    if value is None and optional:
        return None
    parsed = dt_util.parse_datetime(value) if isinstance(value, str) else None
    if parsed is None or parsed.tzinfo is None:
        raise ValueError(f"{field} isn't a time with a UTC offset")
    return dt_util.as_utc(parsed)


def _text(value: Any, field: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field} isn't text")
    return value


def _int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} isn't a whole number")
    return value


@dataclass(frozen=True, slots=True)
class BakeStep:
    """One step of a bake's schedule."""

    position: int
    text: str
    note: str | None
    wait_chosen: int
    skipped: bool
    scheduled_at: datetime | None  # None for skipped steps
    completed_at: datetime | None
    hands_on_seconds: int
    hands_on_estimated: bool

    @property
    def done(self) -> bool:
        return self.skipped or self.completed_at is not None

    @classmethod
    def from_api(cls, data: Mapping[str, Any]) -> BakeStep:
        hands_on = data.get("hands_on")
        if not isinstance(hands_on, Mapping):
            raise ValueError("hands_on is missing")
        return cls(
            position=_int(data.get("position"), "position"),
            text=_text(data.get("text"), "text") or "",
            note=_text(data.get("note"), "note", optional=True),
            wait_chosen=_int(data.get("wait_chosen"), "wait_chosen"),
            skipped=bool(data.get("skipped")),
            scheduled_at=_time(data.get("scheduled_at"), "scheduled_at", optional=True),
            completed_at=_time(data.get("completed_at"), "completed_at", optional=True),
            hands_on_seconds=_int(hands_on.get("seconds"), "hands_on.seconds"),
            hands_on_estimated=bool(hands_on.get("estimated")),
        )


@dataclass(frozen=True, slots=True)
class Bake:
    """An active Fancy bake and its schedule."""

    id: str
    name: str
    path: str
    time_zone: str
    status: str
    alert_mode: str
    projected_end: datetime
    steps: tuple[BakeStep, ...]

    def is_stale(self, now: datetime) -> bool:
        """Past its projected end: breadsheet would have expired it, so old data about it can't be trusted."""
        return now > self.projected_end

    @classmethod
    def from_api(cls, data: Mapping[str, Any]) -> Bake:
        steps = data.get("steps")
        if not isinstance(steps, list) or not steps:
            raise ValueError("steps are missing")
        return cls(
            id=_text(data.get("id"), "id") or "",
            name=_text(data.get("name"), "name") or "",
            path=_text(data.get("path"), "path") or "",
            time_zone=_text(data.get("time_zone"), "time_zone") or "",
            status=_text(data.get("status"), "status") or "",
            alert_mode=_text(data.get("alert_mode"), "alert_mode") or "",
            projected_end=_time(data.get("projected_end"), "projected_end"),  # type: ignore[arg-type]
            steps=tuple(BakeStep.from_api(step) for step in steps if isinstance(step, Mapping)),
        )


def parse_bakes(raw: list[dict[str, Any]]) -> dict[str, Bake]:
    """Bakes by id, in breadsheet's order. Raises ValueError if any bake is malformed."""
    bakes: dict[str, Bake] = {}
    for item in raw:
        bake = Bake.from_api(item)
        if not bake.id:
            raise ValueError("a bake has no id")
        bakes[bake.id] = bake
    return bakes
