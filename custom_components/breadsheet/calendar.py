"""A calendar of every active Fancy bake's steps.

For viewing, and for automations that can wait: HA's calendar triggers read events 15 minutes at a time and
don't re-read them when a step moves. Step alerts use the step events instead (step_events.py).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import BreadsheetConfigEntry
from .coordinator import BreadsheetCoordinator
from .entity import BreadsheetEntity, account_device

PARALLEL_UPDATES = 0
MIN_EVENT_LENGTH = timedelta(minutes=1)


async def async_setup_entry(hass: HomeAssistant, entry: BreadsheetConfigEntry,
                            async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    async_add_entities([BakesCalendar(entry.runtime_data.coordinator)])


class BakesCalendar(BreadsheetEntity, CalendarEntity):
    """One event per step that isn't skipped, lasting its hands-on time (10 minutes when unknown)."""

    _attr_translation_key = "bakes"

    def __init__(self, coordinator: BreadsheetCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.config_entry.unique_id}_bakes"
        self._attr_device_info = account_device(coordinator)

    @property
    def available(self) -> bool:
        """Up while any bake is, or while there's nothing stale to show."""
        state = self.coordinator.data
        if state is None:
            return False
        bakes = list(state.bakes.values())
        return not bakes or self.coordinator.last_update_success or any(
            self.coordinator.bake_available(bake) for bake in bakes)

    def _events(self) -> list[CalendarEvent]:
        state = self.coordinator.data
        events = []
        for bake in (state.bakes.values() if state else []):
            for step in bake.steps:
                if step.skipped or step.scheduled_at is None:
                    continue
                events.append(CalendarEvent(
                    start=step.scheduled_at,
                    end=step.scheduled_at + max(timedelta(seconds=step.hands_on_seconds), MIN_EVENT_LENGTH),
                    summary=f"{bake.name} · {step.text}", description=step.note,
                    uid=f"{bake.id}-{step.position}"))
        return sorted(events, key=lambda event: (event.start_datetime_local, event.uid or ""))

    @property
    def event(self) -> CalendarEvent | None:
        """The step happening now, or the next one."""
        now = dt_util.utcnow()
        return next((event for event in self._events() if event.end_datetime_local > now), None)

    async def async_get_events(self, hass: HomeAssistant, start_date: datetime,
                               end_date: datetime) -> list[CalendarEvent]:
        return [event for event in self._events()
                if event.start_datetime_local < end_date and event.end_datetime_local > start_date]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self._attributes()
