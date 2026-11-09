"""Sensors for each active Fancy bake: current step, next step, next step time, and progress."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorEntityDescription
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BreadsheetConfigEntry
from .coordinator import BreadsheetCoordinator
from .entity import BreadsheetBakeEntity
from .models import Bake, BakeStep
from .steps import current_step, next_step, progress

PARALLEL_UPDATES = 0
MAX_STATE_LENGTH = 255  # HA's limit for a state; full text is in the attributes
NOT_STARTED = "Not started"


def _text(value: str) -> str:
    return value if len(value) <= MAX_STATE_LENGTH else value[:MAX_STATE_LENGTH - 1] + "…"


def _step_attributes(step: BakeStep | None) -> dict[str, Any]:
    if step is None:
        return {}
    return {"position": step.position, "text": step.text, "note": step.note,
            "scheduled_at": step.scheduled_at.isoformat() if step.scheduled_at else None,
            "hands_on_seconds": step.hands_on_seconds, "hands_on_estimated": step.hands_on_estimated}


def _current(bake: Bake) -> str:
    step = current_step(bake)
    return _text(step.text) if step else NOT_STARTED


def _current_attributes(bake: Bake) -> dict[str, Any]:
    step = current_step(bake)
    if step is None:
        return {}
    return {**_step_attributes(step), "completed_at": step.completed_at.isoformat() if step.completed_at else None}


def _next(bake: Bake) -> str | None:
    step = next_step(bake)
    return _text(step.text) if step else None


def _next_time(bake: Bake) -> datetime | None:
    step = next_step(bake)
    return step.scheduled_at if step else None


def _progress(bake: Bake) -> int:
    done, total = progress(bake)
    return round(100 * done / total) if total else 0


def _progress_attributes(bake: Bake) -> dict[str, Any]:
    done, total = progress(bake)
    return {"steps_done": done, "steps_total": total}


@dataclass(frozen=True, kw_only=True)
class BakeSensorDescription(SensorEntityDescription):
    value_fn: Callable[[Bake], Any]
    attributes_fn: Callable[[Bake], dict[str, Any]] = lambda bake: {}


SENSORS: tuple[BakeSensorDescription, ...] = (
    BakeSensorDescription(key="current_step", translation_key="current_step", value_fn=_current,
                          attributes_fn=_current_attributes),
    BakeSensorDescription(key="next_step", translation_key="next_step", value_fn=_next,
                          attributes_fn=lambda bake: _step_attributes(next_step(bake))),
    BakeSensorDescription(key="next_step_time", translation_key="next_step_time", value_fn=_next_time,
                          device_class=SensorDeviceClass.TIMESTAMP,
                          attributes_fn=lambda bake: {"position": s.position} if (s := next_step(bake)) else {}),
    BakeSensorDescription(key="progress", translation_key="progress", value_fn=_progress,
                          native_unit_of_measurement=PERCENTAGE, attributes_fn=_progress_attributes),
)


async def async_setup_entry(hass: HomeAssistant, entry: BreadsheetConfigEntry,
                            async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    """Add sensors for each bake as it appears. Ended bakes leave with their device (see __init__)."""
    coordinator = entry.runtime_data.coordinator
    known: set[str] = set()

    @callback
    def add_new_bakes() -> None:
        state = coordinator.data
        new = [bake for bake_id, bake in (state.bakes.items() if state else []) if bake_id not in known]
        known.intersection_update(state.bakes if state else ())
        if new:
            known.update(bake.id for bake in new)
            async_add_entities(BakeSensor(coordinator, bake, description) for bake in new for description in SENSORS)

    add_new_bakes()
    entry.async_on_unload(coordinator.async_add_listener(add_new_bakes))


class BakeSensor(BreadsheetBakeEntity, SensorEntity):
    """One fact about an active bake."""

    entity_description: BakeSensorDescription

    def __init__(self, coordinator: BreadsheetCoordinator, bake: Bake, description: BakeSensorDescription) -> None:
        super().__init__(coordinator, bake, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        bake = self.bake
        return self.entity_description.value_fn(bake) if bake else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        bake = self.bake
        return {**self._attributes(), **(self.entity_description.attributes_fn(bake) if bake else {})}
