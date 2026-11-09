"""Check-offs that survive breadsheet outages (spec: Resilience; HA integration, Outage behavior).

`breadsheet.complete_step` records the moment it's called, re-projects the schedule locally at once (so sensors
and step events follow right away), and sends the check-off. If breadsheet can't be reached, or answers 429 or
5xx, the check-off waits in a queue, saved with HA's Store, and is retried with backoff. Breadsheet takes the
first write and the client's time, so a late retry lands exactly as an on-time one would have.

Until a check-off is delivered, every poll's bakes get it re-applied (`apply()`), so a poll can't make a step
look undone again.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .api import BreadsheetApiError, BreadsheetAuthError, BreadsheetClient, BreadsheetConnectionError
from .const import DOMAIN
from .models import Bake
from .steps import with_check_off

if TYPE_CHECKING:
    from . import BreadsheetConfigEntry
    from .coordinator import BreadsheetCoordinator

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
BACKOFF = (15, 30, 60, 120, 300)  # seconds before each retry; the last repeats
# Breadsheet refuses check-offs more than an hour or so after a bake's projected end (it expires), so a check-off
# this old can't land anyway
GIVE_UP_AFTER = timedelta(days=2)
RETRYABLE_STATUSES = {429}


@dataclass(slots=True)
class CheckOff:
    bake_id: str
    position: int
    completed_at: datetime
    attempts: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {"bake_id": self.bake_id, "position": self.position,
                "completed_at": self.completed_at.isoformat(), "attempts": self.attempts}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CheckOff:
        completed_at = dt_util.parse_datetime(data["completed_at"])
        if completed_at is None:
            raise ValueError("completed_at isn't a time")
        return cls(str(data["bake_id"]), int(data["position"]), completed_at, int(data.get("attempts", 0)))


def checkoffs_storage_key(entry_id: str) -> str:
    return f"{DOMAIN}.{entry_id}.checkoffs"


class CheckOffs:
    """The check-offs one config entry hasn't delivered yet, and their retries."""

    def __init__(self, hass: HomeAssistant, entry: BreadsheetConfigEntry, client: BreadsheetClient) -> None:
        self._hass = hass
        self._entry = entry
        self._client = client
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, checkoffs_storage_key(entry.entry_id))
        self._pending: list[CheckOff] = []
        self._coordinator: BreadsheetCoordinator | None = None
        self._unsub_retry: CALLBACK_TYPE | None = None
        self._sending = False

    @property
    def pending(self) -> list[CheckOff]:
        return list(self._pending)

    async def async_load(self) -> None:
        saved = await self._store.async_load()
        for item in (saved or {}).get("checkoffs", []):
            try:
                self._pending.append(CheckOff.from_dict(item))
            except (KeyError, TypeError, ValueError) as err:
                _LOGGER.warning("Dropping an unreadable saved check-off: %s", err)

    @callback
    def async_start(self, coordinator: BreadsheetCoordinator) -> CALLBACK_TYPE:
        """Start delivering anything left from the last run; returns the function that stops retries."""
        self._coordinator = coordinator
        if self._pending:
            self._send_soon(0)
        return self._cancel_retry

    def apply(self, bakes: dict[str, Bake]) -> dict[str, Bake]:
        """The bakes with every undelivered check-off applied."""
        for item in self._pending:
            if (bake := bakes.get(item.bake_id)) is not None:
                bakes = {**bakes, item.bake_id: with_check_off(bake, item.position, item.completed_at)}
        return bakes

    async def async_check_off(self, bake_id: str, position: int, completed_at: datetime) -> None:
        """Record a check-off, show it at once, and send it in the background."""
        if any(item.bake_id == bake_id and item.position == position for item in self._pending):
            return  # already on its way; the first time counts
        self._pending.append(CheckOff(bake_id, position, completed_at))
        await self._save()
        if self._coordinator is not None:
            self._coordinator.async_apply_local_changes()
        self._send_soon(0)

    @callback
    def _send_soon(self, delay: float) -> None:
        self._cancel_retry()
        if delay <= 0:
            self._entry.async_create_background_task(self._hass, self._send(), "breadsheet check-offs")
            return

        @callback
        def retry(_now: datetime) -> None:
            self._unsub_retry = None
            self._entry.async_create_background_task(self._hass, self._send(), "breadsheet check-off retry")
        self._unsub_retry = async_call_later(self._hass, delay, retry)

    @callback
    def _cancel_retry(self) -> None:
        if self._unsub_retry is not None:
            self._unsub_retry()
            self._unsub_retry = None

    async def _send(self) -> None:
        """Deliver queued check-offs in order; on a temporary failure, keep the rest and try again later."""
        if self._sending:
            return
        self._sending = True
        delivered = False
        try:
            now = dt_util.utcnow()
            for item in list(self._pending):
                if now - item.completed_at > GIVE_UP_AFTER:
                    _LOGGER.warning("Giving up on check-off of bake %s step %s from %s",
                                    item.bake_id, item.position, item.completed_at)
                    self._pending.remove(item)
                    continue
                try:
                    await self._client.complete_step(item.bake_id, item.position, item.completed_at)
                except BreadsheetAuthError:
                    # Keep the queue: once the user pairs again, the reloaded entry sends it
                    self._entry.async_start_reauth(self._hass)
                    return
                except (BreadsheetConnectionError, BreadsheetApiError) as err:
                    if isinstance(err, BreadsheetApiError) and err.status not in RETRYABLE_STATUSES:
                        _LOGGER.warning("Breadsheet refused the check-off of bake %s step %s: %s",
                                        item.bake_id, item.position, err.code)
                        self._pending.remove(item)
                        delivered = True  # refresh, so HA shows what breadsheet actually has
                        continue
                    item.attempts += 1
                    delay = max(BACKOFF[min(item.attempts, len(BACKOFF)) - 1], getattr(err, "retry_after", 0) or 0)
                    _LOGGER.info("Couldn't send a check-off (%s); retrying in %s seconds", err, delay)
                    self._send_soon(delay)
                    return
                self._pending.remove(item)
                delivered = True
        finally:
            self._sending = False
            await self._save()
            if delivered and self._coordinator is not None:
                await self._coordinator.async_request_refresh()

    async def _save(self) -> None:
        await self._store.async_save({"checkoffs": [item.to_dict() for item in self._pending]})


async def async_remove_checkoffs(hass: HomeAssistant, entry_id: str) -> None:
    await Store(hass, STORAGE_VERSION, checkoffs_storage_key(entry_id)).async_remove()
