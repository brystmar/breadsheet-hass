"""Diagnostics for Breadsheet. The device token never appears here."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_TOKEN
from homeassistant.core import HomeAssistant

from . import BreadsheetConfigEntry
from .const import CONF_API_URL, DEFAULT_API_URL

TO_REDACT = {CONF_TOKEN, CONF_API_URL}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: BreadsheetConfigEntry) -> dict[str, Any]:
    """Diagnostics for a config entry."""
    return {
        "entry_data": async_redact_data(dict(entry.data), TO_REDACT),
        # A development API URL may name a private host, so say only whether it's the production one
        "api_url_is_default": entry.data.get(CONF_API_URL) == DEFAULT_API_URL,
    }
