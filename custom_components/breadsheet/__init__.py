"""The Breadsheet integration: live bake schedules from breadsheet.com."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_TOKEN, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.loader import async_get_integration

from homeassistant.helpers.storage import Store

from .api import BreadsheetClient
from .const import CONF_API_URL, DOMAIN
from .coordinator import STORAGE_VERSION, BreadsheetCoordinator, storage_key

# Entities arrive with the polling coordinator and the calendar and sensors (breadsheet-hass#2, #3)
PLATFORMS: list[Platform] = []


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
    # DataUpdateCoordinator only polls while something listens. breadsheet_bake_ended needs polling even
    # when no entity is listening, so the integration keeps a listener of its own.
    entry.async_on_unload(coordinator.async_add_listener(lambda: None))
    entry.runtime_data = BreadsheetData(client=client, coordinator=coordinator)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: BreadsheetConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: BreadsheetConfigEntry) -> None:
    """Delete the saved bakes when the integration is removed."""
    await Store(hass, STORAGE_VERSION, storage_key(entry.entry_id)).async_remove()
