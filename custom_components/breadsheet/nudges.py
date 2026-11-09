"""Webhook nudges from breadsheet: "poll now" (spec: HA integration, Webhook; API v2, Webhook nudges).

Polling alone means a bake started on breadsheet.com takes up to two minutes to reach HA. So the integration
registers an HA webhook and gives breadsheet its address; breadsheet POSTs to it right after any change to a
Fancy bake, and HA polls at once.

- The address is a Home Assistant Cloud cloudhook when the user has Cloud, otherwise HA's external https URL,
  otherwise nothing: then polling covers it, as before.
- The webhook takes no authentication and its body is ignored. All a caller can do is make HA poll breadsheet a
  little early, through the coordinator's debouncer, so a burst of nudges costs at most one extra poll.
- The address is a secret (anyone with it can trigger those polls): never log it or put it in diagnostics.
"""

from __future__ import annotations

import asyncio
import logging
from types import ModuleType
from typing import TYPE_CHECKING, Any

from aiohttp import web

from homeassistant.components import webhook
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.network import NoURLAvailableError, get_url

from .api import BreadsheetApiError, BreadsheetClient, BreadsheetError
from .const import CONF_WEBHOOK_ID, DOMAIN

if TYPE_CHECKING:
    from . import BreadsheetConfigEntry
    from .coordinator import BreadsheetCoordinator

_LOGGER = logging.getLogger(__name__)

KIND_CLOUD = "cloud"
KIND_EXTERNAL = "external"
KIND_NONE = "none"


def cloud_component(hass: HomeAssistant) -> ModuleType | None:
    """Home Assistant Cloud's helpers, if Cloud is set up. Imported only then: it's optional (and heavy)."""
    if "cloud" not in hass.config.components:
        return None
    from homeassistant.components import cloud  # noqa: PLC0415
    return cloud


class WebhookNudges:
    """One config entry's webhook: its handler in HA, and its address registered with breadsheet."""

    def __init__(self, hass: HomeAssistant, entry: BreadsheetConfigEntry, client: BreadsheetClient,
                 coordinator: BreadsheetCoordinator) -> None:
        self._hass = hass
        self._entry = entry
        self._client = client
        self._coordinator = coordinator
        self._webhook_id: str = entry.data[CONF_WEBHOOK_ID]
        self._lock = asyncio.Lock()
        self._registered_url: str | None = None  # what breadsheet has, as far as this run knows
        self._cleared = False
        self._refused_url: str | None = None
        self._retry = False
        self.kind = KIND_NONE  # for diagnostics: which kind of address breadsheet has
        self._unsubs: list[CALLBACK_TYPE] = []

    async def async_start(self) -> CALLBACK_TYPE:
        """Listen for nudges and give breadsheet the address; returns the function that stops listening."""
        webhook.async_register(self._hass, DOMAIN, f"Breadsheet ({self._entry.title})", self._webhook_id,
                               self._handle_nudge, allowed_methods=("POST",))
        self._unsubs.append(lambda: webhook.async_unregister(self._hass, self._webhook_id))
        if (cloud := cloud_component(self._hass)) is not None:
            # Cloud often connects after this integration starts, and cloudhooks can be replaced
            self._unsubs.append(cloud.async_listen_connection_change(self._hass, self._cloud_changed))
            self._unsubs.append(cloud.async_listen_cloudhook_change(self._hass, self._webhook_id, self._cloud_changed))
        self._unsubs.append(self._coordinator.async_add_listener(self._retry_after_a_good_poll))
        await self.async_register()
        return self._stop

    @callback
    def _stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()

    async def _handle_nudge(self, hass: HomeAssistant, webhook_id: str, request: web.Request) -> web.Response:
        """Breadsheet says something changed. The body is ignored; HA pulls with its own token."""
        self._entry.async_create_background_task(hass, self._coordinator.async_request_refresh(), "breadsheet nudge")
        return web.Response(status=200)

    @callback
    def _cloud_changed(self, _state: Any = None) -> None:
        self._entry.async_create_background_task(self._hass, self.async_register(), "breadsheet webhook")

    @callback
    def _retry_after_a_good_poll(self) -> None:
        if self._retry and self._coordinator.last_update_success:
            self._retry = False
            self._entry.async_create_background_task(self._hass, self.async_register(), "breadsheet webhook retry")

    async def _address(self) -> tuple[str, str | None] | None:
        """(kind, URL) to give breadsheet, or None to leave things as they are for now (Cloud is still connecting)."""
        cloud = cloud_component(self._hass)
        if cloud is not None and cloud.async_active_subscription(self._hass):
            try:
                return KIND_CLOUD, await cloud.async_get_or_create_cloudhook(self._hass, self._webhook_id)
            except cloud.CloudNotAvailable:
                return None
        try:
            base = get_url(self._hass, allow_internal=False, allow_cloud=False, allow_ip=False,
                           require_ssl=True, require_standard_port=True)
        except NoURLAvailableError:
            return KIND_NONE, None
        return KIND_EXTERNAL, f"{base}{webhook.async_generate_path(self._webhook_id)}"

    async def async_register(self) -> None:
        """Make breadsheet's copy of the address match what HA can be reached at now."""
        async with self._lock:
            address = await self._address()
            if address is None:
                return
            kind, url = address
            try:
                if url is None:
                    if not self._cleared:
                        await self._client.clear_webhook()
                        self._cleared, self._registered_url, self.kind = True, None, KIND_NONE
                    return
                if url in (self._registered_url, self._refused_url):
                    return
                await self._client.set_webhook(url)
                self._registered_url, self._cleared, self.kind = url, False, kind
                _LOGGER.debug("Registered a %s webhook with breadsheet", kind)
            except BreadsheetApiError as err:
                if err.code == "webhook_url_invalid":
                    self._refused_url, self.kind = url, KIND_NONE
                    _LOGGER.warning("Breadsheet can't reach this Home Assistant's %s address, so bake changes "
                                    "arrive by polling (up to two minutes): %s", kind, err.message)
                elif err.code != "token_invalid":  # the coordinator handles a revoked token
                    _LOGGER.warning("Breadsheet refused the webhook: %s", err.code)
            except BreadsheetError as err:
                _LOGGER.debug("Couldn't register the webhook yet: %s", err)
                self._retry = True


async def async_remove_webhook(hass: HomeAssistant, client: BreadsheetClient, webhook_id: str | None) -> None:
    """When the integration is removed: stop breadsheet's nudges and delete the cloudhook. Best effort."""
    try:
        await client.clear_webhook()
    except BreadsheetError as err:
        _LOGGER.debug("Couldn't clear the webhook on breadsheet: %s", err)
    cloud = cloud_component(hass)
    if cloud is not None and webhook_id:
        try:
            await cloud.async_delete_cloudhook(hass, webhook_id)
        except Exception as err:  # noqa: BLE001 (Cloud raises its own errors; nothing here matters after removal)
            _LOGGER.debug("Couldn't delete the cloudhook: %s", err)
