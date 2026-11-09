"""Polls breadsheet for active Fancy bakes, and keeps running from the last good copy through outages.

- Polls `GET /api/v2/bakes?status=active&alert=fancy` with `If-None-Match`, at the interval breadsheet suggests
  (`X-Breadsheet-Poll-Interval`: a minute while a Fancy bake is active, 2 minutes otherwise), honoring
  `Retry-After` on 429 and 503.
- A failed poll keeps the last good bakes. DataUpdateCoordinator keeps `data` on failure but sets
  `last_update_success` False, which entities would read as unavailable; ours use `bake_available()` instead,
  so a bake stays up until its projected end (spec: HA integration, Outage behavior).
- The last good bakes and their ETag are saved with HA's Store. After a restart during an outage, the
  integration carries on from them instead of waiting for breadsheet; and a bake that ended meanwhile still
  fires `breadsheet_bake_ended` on the next successful poll.
- `breadsheet_bake_ended` fires only when a successful poll no longer lists a bake: completed, canceled,
  expired, or switched to Simple. A failed poll never fires it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import BreadsheetApiError, BreadsheetAuthError, BreadsheetClient, BreadsheetConnectionError
from .const import DOMAIN, EVENT_BAKE_ENDED
from .models import Bake, parse_bakes

if TYPE_CHECKING:
    from . import BreadsheetConfigEntry

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
ACTIVE_INTERVAL = 60  # used until breadsheet suggests one, or if it doesn't
IDLE_INTERVAL = 120
MIN_INTERVAL = 60  # never poll faster than this, or slower than MAX, whatever the server says
MAX_INTERVAL = 1800


@dataclass(frozen=True, slots=True)
class BreadsheetState:
    """The active Fancy bakes, as of the last successful poll (`last_synced`)."""

    bakes: dict[str, Bake] = field(default_factory=dict)
    last_synced: datetime | None = None


class BreadsheetCoordinator(DataUpdateCoordinator[BreadsheetState]):
    """Polls one breadsheet account's active Fancy bakes."""

    config_entry: BreadsheetConfigEntry

    def __init__(self, hass: HomeAssistant, entry: BreadsheetConfigEntry, client: BreadsheetClient) -> None:
        super().__init__(hass, _LOGGER, config_entry=entry, name=DOMAIN,
                         update_interval=timedelta(seconds=IDLE_INTERVAL))
        self.client = client
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, storage_key(entry.entry_id))
        self._etag: str | None = None
        # What was saved before this run, until the first successful poll replaces it
        self._saved: BreadsheetState | None = None
        # Whether the last poll reached breadsheet. Not last_update_success, which is also True while running
        # from saved bakes after a restart during an outage.
        self._synced = False

    def bake_available(self, bake: Bake) -> bool:
        """Entities stay available through failed polls until the bake's projected end."""
        return (self._synced and self.last_update_success) or not bake.is_stale(dt_util.utcnow())

    async def _async_setup(self) -> None:
        """Load what the last run saved."""
        saved = await self._store.async_load()
        if not saved:
            return
        try:
            self._saved = BreadsheetState(bakes=parse_bakes(saved["bakes"]),
                                          last_synced=dt_util.parse_datetime(saved.get("last_synced") or ""))
            self._etag = saved.get("etag")
        except (KeyError, TypeError, ValueError) as err:
            _LOGGER.warning("Ignoring unreadable saved bakes: %s", err)

    async def _async_update_data(self) -> BreadsheetState:
        previous = self.data or self._saved
        try:
            result = await self.client.active_fancy_bakes(self._etag if previous else None)
        except BreadsheetAuthError as err:
            raise ConfigEntryAuthFailed(translation_domain=DOMAIN, translation_key="token_revoked") from err
        except (BreadsheetConnectionError, BreadsheetApiError) as err:
            if self.data is None and self._saved is not None:
                # HA started while breadsheet was unreachable: carry on from what was saved, and try again soon
                _LOGGER.warning("Couldn't reach breadsheet; using the bakes saved at %s", self._saved.last_synced)
                self.update_interval = timedelta(seconds=MIN_INTERVAL)
                self._synced = False
                return self._saved
            raise UpdateFailed(translation_domain=DOMAIN, translation_key="update_failed",
                               translation_placeholders={"error": str(err)},
                               retry_after=getattr(err, "retry_after", None)) from err

        if result.bakes is None:  # 304: nothing changed
            bakes = previous.bakes if previous else {}
        else:
            try:
                bakes = parse_bakes(result.bakes)
            except ValueError as err:
                raise UpdateFailed(translation_domain=DOMAIN, translation_key="update_failed",
                                   translation_placeholders={"error": str(err)}) from err

        state = BreadsheetState(bakes=bakes, last_synced=dt_util.utcnow())
        self._etag = result.etag
        self._set_interval(result.poll_interval, bakes)
        if result.bakes is not None:
            if previous is not None:
                self._fire_ended(previous.bakes, bakes)
            await self._store.async_save({"etag": self._etag, "last_synced": state.last_synced.isoformat(),
                                          "bakes": result.bakes})
        self._saved = None
        self._synced = True
        return state

    def _set_interval(self, suggested: int | None, bakes: dict[str, Bake]) -> None:
        seconds = suggested if suggested is not None else (ACTIVE_INTERVAL if bakes else IDLE_INTERVAL)
        self.update_interval = timedelta(seconds=min(max(seconds, MIN_INTERVAL), MAX_INTERVAL))

    def _fire_ended(self, before: dict[str, Bake], after: dict[str, Bake]) -> None:
        """A bake left the active Fancy list. The list doesn't say why, so there's no `outcome` to send."""
        for bake_id in before.keys() - after.keys():
            _LOGGER.debug("Bake %s ended", bake_id)
            self.hass.bus.async_fire(EVENT_BAKE_ENDED, {"bake_id": bake_id})


def storage_key(entry_id: str) -> str:
    return f"{DOMAIN}.{entry_id}"
