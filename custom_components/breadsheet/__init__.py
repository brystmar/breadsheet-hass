"""The Breadsheet integration: live bake schedules from breadsheet.com."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_TOKEN, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.loader import async_get_integration

from .api import BreadsheetClient
from .const import CONF_API_URL, CONF_LEAD_MINUTES, DEFAULT_LEAD_MINUTES, DOMAIN
from .coordinator import STORAGE_VERSION, BreadsheetCoordinator, storage_key
from .step_events import StepEvents

PLATFORMS: list[Platform] = [Platform.CALENDAR, Platform.SENSOR]


@dataclass(slots=True)
class BreadsheetData:
    """What a loaded config entry holds while it runs."""

    client: BreadsheetClient
    coordinator: BreadsheetCoordinator


type BreadsheetConfigEntry = ConfigEntry[BreadsheetData]


async def async_create_client(hass: HomeAssistant, api_url: str, token: str | None = None) -> BreadsheetClient:
    """A client on HA's shared aiohttp session, identifying this integration's version."""
    integration = await async_get_integration(hass, DOMAIN)
    return BreadsheetClient(async_get_clientsession(hass), api_url, token,
                            user_agent=f"breadsheet-hass/{integration.version}")


async def async_setup_entry(hass: HomeAssistant, entry: BreadsheetConfigEntry) -> bool:
    """Set up Breadsheet from a config entry."""
    client = await async_create_client(hass, entry.data[CONF_API_URL], entry.data[CONF_TOKEN])
    coordinator = BreadsheetCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = BreadsheetData(client=client, coordinator=coordinator)

    # Listeners also keep the coordinator polling (it only polls while something listens), so
    # breadsheet_bake_ended and the step events work even with every entity disabled.
    entry.async_on_unload(coordinator.async_add_listener(_remove_ended_bake_devices(hass, entry)))
    _remove_ended_bake_devices(hass, entry)()
    lead = timedelta(minutes=entry.options.get(CONF_LEAD_MINUTES, DEFAULT_LEAD_MINUTES))
    entry.async_on_unload(StepEvents(hass, coordinator, lead).async_start())

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


def _remove_ended_bake_devices(hass: HomeAssistant, entry: BreadsheetConfigEntry):
    """A listener that removes the devices (and with them, the entities) of bakes no longer active."""

    @callback
    def remove() -> None:
        state = entry.runtime_data.coordinator.data
        if state is None:
            return
        registry = dr.async_get(hass)
        for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
            bake_ids = {identifier for domain, identifier in device.identifiers if domain == DOMAIN}
            if device.model == "Bake" and not bake_ids & state.bakes.keys():
                registry.async_update_device(device.id, remove_config_entry_id=entry.entry_id)
    return remove


async def async_unload_entry(hass: HomeAssistant, entry: BreadsheetConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: BreadsheetConfigEntry) -> None:
    """Delete the saved bakes when the integration is removed."""
    await Store(hass, STORAGE_VERSION, storage_key(entry.entry_id)).async_remove()
