"""Step events: breadsheet_step_upcoming, breadsheet_step_due, and breadsheet_step_late.

The integration times these itself. HA's calendar triggers can't: they read the next 15 minutes of events
once and never re-read them, so a step that moves after an early check-off, or a bake that starts inside the
window, would be missed or fire at the old time.

Every time the coordinator's data changes (a poll, a webhook nudge, a local re-projection), all timers are
reset from the new schedule. Each event fires at most once per step and scheduled time; if the schedule moves
a step, its events fire again at the new time. A step that's done by the time its event comes due doesn't fire.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
import logging
from typing import Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.util import dt as dt_util

from .const import EVENT_STEP_DUE, EVENT_STEP_LATE, EVENT_STEP_UPCOMING
from .coordinator import BreadsheetCoordinator
from .models import Bake, BakeStep
from .steps import progress, step_after

_LOGGER = logging.getLogger(__name__)

LATE_AFTER = timedelta(minutes=5)
# An event that came due this recently, e.g. during a restart or between two resets, still fires; older ones don't
CATCH_UP = timedelta(seconds=60)

type EventKey = tuple[str, int, str, str]  # bake id, step position, event type, scheduled time


def step_event_data(bake: Bake, step: BakeStep) -> dict[str, Any]:
    """What every step event carries: enough for a notification and a Live Activity without reading entities."""
    after = step_after(bake, step)
    done, total = progress(bake)
    return {
        "bake_id": bake.id,
        "bake_name": bake.name,
        "bake_path": bake.path,
        "position": step.position,
        "text": step.text,
        "note": step.note,
        "scheduled_at": step.scheduled_at.isoformat() if step.scheduled_at else None,
        "hands_on_seconds": step.hands_on_seconds,
        "next_position": after.position if after else None,
        "next_text": after.text if after else None,
        "next_scheduled_at": after.scheduled_at.isoformat() if after and after.scheduled_at else None,
        "steps_done": done,
        "steps_total": total,
    }


class StepEvents:
    """Times the step events for one config entry's bakes."""

    def __init__(self, hass: HomeAssistant, coordinator: BreadsheetCoordinator, lead: timedelta) -> None:
        self._hass = hass
        self._coordinator = coordinator
        self._lead = lead
        self._timers: list[CALLBACK_TYPE] = []
        self._fired: set[EventKey] = set()

    @callback
    def async_start(self) -> Callable[[], None]:
        """Time events now and after every data change; returns the function that stops it."""
        remove_listener = self._coordinator.async_add_listener(self.async_reschedule)
        self.async_reschedule()

        def stop() -> None:
            remove_listener()
            self._cancel()
        return stop

    def _cancel(self) -> None:
        for cancel in self._timers:
            cancel()
        self._timers.clear()

    @callback
    def async_reschedule(self) -> None:
        self._cancel()
        state = self._coordinator.data
        if state is None:
            return
        now = dt_util.utcnow()
        current: set[EventKey] = set()
        # One timer per moment, firing its events in bake and step order: separate timers for the same moment
        # can run in any order, and same-minute steps must arrive in step order
        due: dict[datetime, list[EventKey]] = {}
        for bake in state.bakes.values():
            for step in bake.steps:
                if step.done or step.scheduled_at is None:
                    continue
                for event_type, at in self._times(step.scheduled_at):
                    key = (bake.id, step.position, event_type, step.scheduled_at.isoformat())
                    current.add(key)
                    if key in self._fired:
                        continue
                    if at <= now:
                        if now - at <= CATCH_UP:
                            self._fire(key)
                        else:
                            self._fired.add(key)  # long past: never fire it
                        continue
                    due.setdefault(at, []).append(key)
        for at, keys in due.items():
            self._timers.append(async_track_point_in_utc_time(self._hass, self._make_handler(keys), at))
        self._fired &= current  # forget steps that are done, moved, or gone

    def _times(self, scheduled_at: datetime) -> list[tuple[str, datetime]]:
        times = [(EVENT_STEP_DUE, scheduled_at), (EVENT_STEP_LATE, scheduled_at + LATE_AFTER)]
        if self._lead > timedelta(0):
            times.insert(0, (EVENT_STEP_UPCOMING, scheduled_at - self._lead))
        return times

    def _make_handler(self, keys: list[EventKey]) -> Callable[[datetime], None]:
        @callback
        def handle(_now: datetime) -> None:
            for key in keys:
                self._fire(key)
        return handle

    @callback
    def _fire(self, key: EventKey) -> None:
        bake_id, position, event_type, _ = key
        self._fired.add(key)
        state = self._coordinator.data
        bake = state.bakes.get(bake_id) if state else None
        step = next((s for s in bake.steps if s.position == position), None) if bake else None
        if bake is None or step is None or step.done:
            return
        _LOGGER.debug("%s for bake %s step %s", event_type, bake_id, position)
        self._hass.bus.async_fire(event_type, step_event_data(bake, step))
